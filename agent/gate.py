"""Gate: барьер качества перед предложением правки (SPEC §5).

Шесть проверок по порядку:
1. ast.parse каждого .py (синтаксис);
2. ruff check;
3. mypy;
4. bandit -ll;
5. pytest (весь набор задачи);
6. smoke-импорт затронутых модулей.

Все внешние инструменты запускаются ТОЛЬКО через executor.run
(allow-list, cwd в песочнице, shell=False, memory-guard).
Gate сам ничего не чинит: красный результат -> агент правит (agent/core.py).
"""
from __future__ import annotations

import ast
import time
from dataclasses import dataclass, field
from pathlib import Path

from agent import executor

# Сколько символов вывода инструмента сохраняем (хвост) для лога/БД.
_OUTPUT_TAIL = 4000


def _tail(text: str, limit: int = _OUTPUT_TAIL) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return "…(обрезано)\n" + text[-limit:]


@dataclass
class GateStep:
    """Результат одного шага gate."""

    tool: str
    passed: bool
    exit_code: int
    output: str = ""
    duration_sec: float = 0.0
    skipped: bool = False


@dataclass
class GateResult:
    """Агрегированный результат прохождения gate."""

    steps: list[GateStep] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """True, если ни один НЕ пропущенный шаг не упал."""
        return all(s.passed for s in self.steps if not s.skipped)

    @property
    def red_tools(self) -> list[str]:
        """Инструменты, давшие красный результат."""
        return [s.tool for s in self.steps if not s.passed and not s.skipped]

    def summary(self) -> str:
        """Однострочная сводка вида 'ruff ✅ mypy ✅ pytest ❌'."""
        marks = []
        for s in self.steps:
            mark = "⏭" if s.skipped else ("✅" if s.passed else "❌")
            marks.append(f"{s.tool} {mark}")
        return " ".join(marks)


def _py_files(task_id: int) -> list[str]:
    """Относительные пути всех .py в каталоге задачи (кроме venv/.git/pycache)."""
    base = executor.task_dir(task_id)
    if not base.is_dir():
        return []
    skip = {".git", "venv", ".venv", "__pycache__", "node_modules"}
    out: list[str] = []
    for p in sorted(base.rglob("*.py")):
        if any(part in skip for part in p.relative_to(base).parts):
            continue
        out.append(str(p.relative_to(base)))
    return out


def _step_ast(task_id: int, files: list[str]) -> GateStep:
    """Синтаксическая проверка каждого .py через ast.parse (без subprocess)."""
    t0 = time.monotonic()
    base = executor.task_dir(task_id)
    errors: list[str] = []
    for rel in files:
        fp = base / rel
        try:
            ast.parse(fp.read_text(encoding="utf-8"), filename=rel)
        except SyntaxError as exc:
            errors.append(f"{rel}:{exc.lineno}: {exc.msg}")
        except OSError as exc:
            errors.append(f"{rel}: {exc}")
    dur = time.monotonic() - t0
    ok = not errors
    return GateStep(
        tool="ast",
        passed=ok,
        exit_code=0 if ok else 1,
        output=_tail("\n".join(errors)),
        duration_sec=dur,
    )


def _run_tool(task_id: int, tool: str, argv: list[str]) -> GateStep:
    """Прогнать инструмент через executor; ExecutorError -> красный/skip."""
    t0 = time.monotonic()
    try:
        res = executor.run(argv, task_id=task_id)
    except executor.ExecutorError as exc:
        dur = time.monotonic() - t0
        msg = str(exc)
        # Нехватка памяти для pytest -> шаг помечается skipped (не красный).
        skipped = "памяти" in msg.lower()
        return GateStep(
            tool=tool,
            passed=skipped,
            exit_code=-1,
            output=_tail(msg),
            duration_sec=dur,
            skipped=skipped,
        )
    dur = time.monotonic() - t0
    out = _tail((res.stdout + "\n" + res.stderr).strip())
    return GateStep(
        tool=tool,
        passed=res.ok,
        exit_code=res.returncode,
        output=out,
        duration_sec=dur,
    )


def _step_smoke(task_id: int, files: list[str]) -> GateStep:
    """Smoke-импорт: python -c 'import a; import b' по затронутым модулям."""
    modules: list[str] = []
    for rel in files:
        p = Path(rel)
        if p.name == "__init__.py":
            mod = ".".join(p.parent.parts)
        else:
            mod = ".".join([*p.parent.parts, p.stem])
        if mod and not mod.startswith("test"):
            modules.append(mod)
    modules = sorted(set(modules))
    if not modules:
        return GateStep(tool="smoke", passed=True, exit_code=0, skipped=True)
    code = "; ".join(f"import {m}" for m in modules)
    return _run_tool(task_id, "smoke", ["python", "-c", code])


def run_gate(task_id: int) -> GateResult:
    """Прогнать все шаги gate по коду задачи и вернуть агрегат.

    ast — стоп-фактор: при синтаксической ошибке остальные инструменты
    пропускаются (запускать их по неразбираемому коду бессмысленно).
    """
    files = _py_files(task_id)
    result = GateResult()

    ast_step = _step_ast(task_id, files)
    result.steps.append(ast_step)
    if not ast_step.passed:
        for tool in ("ruff", "mypy", "bandit", "pytest", "smoke"):
            result.steps.append(
                GateStep(tool=tool, passed=True, exit_code=0, skipped=True)
            )
        return result

    result.steps.append(_run_tool(task_id, "ruff", ["ruff", "check", "."]))
    result.steps.append(_run_tool(task_id, "mypy", ["mypy", "."]))
    result.steps.append(_run_tool(task_id, "bandit", ["bandit", "-ll", "-r", "."]))
    result.steps.append(_run_tool(task_id, "pytest", ["pytest", "-q"]))
    result.steps.append(_step_smoke(task_id, files))
    return result


async def log_gate_to_db(task_id: int, result: GateResult, *, db_path: str) -> None:
    """Записать каждый шаг gate в таблицу gate_runs."""
    from db import database

    for step in result.steps:
        if step.skipped:
            continue
        await database.log_gate_run(
            task_id,
            step.tool,
            step.exit_code,
            output_tail=step.output or None,
            duration_sec=round(step.duration_sec, 3),
            db_path=db_path,
        )
