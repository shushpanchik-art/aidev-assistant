"""Тесты gate (SPEC §5): реальный прогон инструментов на tmp-песочнице.

Проверяем: чистый код -> зелёно; синтаксис-ошибка -> ast красный + остальные
skip; ruff-нарушение -> красный ruff; smoke-импорт битого модуля -> красный;
логирование в БД пишет по строке на каждый непропущенный шаг.
"""
from __future__ import annotations

import os

os.environ.setdefault("WEB_AUTH_TOKEN", "test-token")

import pytest  # noqa: E402

from agent import executor, gate  # noqa: E402


@pytest.fixture
def task(tmp_path, monkeypatch):
    """SANDBOX_DIR -> tmp; каталог задачи 1 готов к записи файлов."""
    root = tmp_path / "sandbox"
    root.mkdir()
    monkeypatch.setattr(executor.config, "SANDBOX_DIR", str(root))
    d = root / "1"
    d.mkdir()
    return d


def _write(task_dir, rel, content):
    p = task_dir / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


# --- ast --------------------------------------------------------------


def test_ast_green_on_valid(task):
    _write(task, "mod.py", "x = 1\n")
    step = gate._step_ast(1, ["mod.py"])
    assert step.passed and step.exit_code == 0


def test_ast_red_on_syntax_error(task):
    _write(task, "broken.py", "def f(:\n")
    step = gate._step_ast(1, ["broken.py"])
    assert not step.passed
    assert "broken.py" in step.output


def test_syntax_error_skips_rest(task):
    _write(task, "broken.py", "def f(:\n")
    res = gate.run_gate(1)
    assert not res.passed
    assert "ast" in res.red_tools
    # остальные инструменты пропущены, не красные
    tools = {s.tool: s for s in res.steps}
    for t in ("ruff", "mypy", "bandit", "pytest", "smoke"):
        assert tools[t].skipped


# --- ruff -------------------------------------------------------------


def test_ruff_red_on_violation(task):
    # неиспользуемый импорт -> ruff F401
    _write(task, "bad.py", "import os\nx = 1\n")
    res = gate.run_gate(1)
    ruff = next(s for s in res.steps if s.tool == "ruff")
    assert not ruff.passed
    assert "ruff" in res.red_tools


def test_clean_code_passes_lint(task):
    _write(task, "ok.py", '"""Docstring."""\n\n\ndef f() -> int:\n    return 1\n')
    res = gate.run_gate(1)
    ruff = next(s for s in res.steps if s.tool == "ruff")
    assert ruff.passed, ruff.output


# --- smoke ------------------------------------------------------------


def test_smoke_green_on_importable(task):
    _write(task, "good.py", "VALUE = 42\n")
    step = gate._step_smoke(1, ["good.py"])
    assert step.passed, step.output


def test_smoke_red_on_import_error(task):
    _write(task, "boom.py", "raise RuntimeError('boom at import')\n")
    step = gate._step_smoke(1, ["boom.py"])
    assert not step.passed
    assert "boom" in step.output.lower()


def test_smoke_skips_test_modules(task):
    _write(task, "test_x.py", "def test_x(): assert True\n")
    step = gate._step_smoke(1, ["test_x.py"])
    assert step.skipped


# --- сбор файлов и агрегаты ------------------------------------------


def test_py_files_ignores_junk(task):
    _write(task, "keep.py", "x = 1\n")
    _write(task, "__pycache__/skip.py", "y = 2\n")
    _write(task, ".git/hooks.py", "z = 3\n")
    files = gate._py_files(1)
    assert "keep.py" in files
    assert not any("pycache" in f or ".git" in f for f in files)


def test_summary_and_red_tools():
    res = gate.GateResult(
        steps=[
            gate.GateStep("ast", True, 0),
            gate.GateStep("ruff", False, 1),
            gate.GateStep("pytest", True, 0, skipped=True),
        ]
    )
    assert not res.passed
    assert res.red_tools == ["ruff"]
    assert "ruff ❌" in res.summary()
    assert "pytest ⏭" in res.summary()


# --- логирование в БД -------------------------------------------------


async def test_log_gate_to_db(tmp_path):
    from db import database

    db_path = str(tmp_path / "t.db")
    await database.init_db(db_path)
    tid = await database.create_task(
        "/opt/SMOKI/bot", "test prompt", autonomy_level=1, db_path=db_path
    )
    res = gate.GateResult(
        steps=[
            gate.GateStep("ast", True, 0, output="ok", duration_sec=0.01),
            gate.GateStep("ruff", False, 1, output="err"),
            gate.GateStep("pytest", True, 0, skipped=True),
        ]
    )
    await gate.log_gate_to_db(tid, res, db_path=db_path)
    rows = await database.get_gate_runs(tid, db_path=db_path)
    assert len(rows) == 2  # skipped не пишется
    assert {r["tool"] for r in rows} == {"ast", "ruff"}
