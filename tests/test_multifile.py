"""Тесты автономного мультифайлового цикла (КИРПИЧ 1, ROADMAP).

solve_task_auto: дерево -> выбор файлов -> чтение -> мультиправка ->
gate -> фикс-цикл. Все AI-вызовы замоканы, gate замокан — проверяем
ЛОГИКУ оркестрации, а не реальные ruff/pytest.
"""
from __future__ import annotations

import os

os.environ.setdefault("WEB_AUTH_TOKEN", "test-token")

import pytest  # noqa: E402

from agent import core, executor  # noqa: E402
from ai import gemini  # noqa: E402


def _gr(text: str) -> gemini.GenResult:
    """Быстрый GenResult для мока generate_text."""
    return gemini.GenResult(
        text=text,
        input_tokens=10,
        output_tokens=20,
        model="gemini-flash-latest",
        via="fallback",
    )


class _FakeGate:
    """Замена gate.GateResult: управляем passed/red_tools."""

    def __init__(self, passed: bool, red_tools=None):
        self.passed = passed
        self.red_tools = red_tools or []
        self.steps = []

    def summary(self) -> str:
        return "OK" if self.passed else "FAIL"


@pytest.fixture
def task(tmp_path, monkeypatch):
    """SANDBOX_DIR -> tmp; каталог задачи 1 с парой исходников."""
    root = tmp_path / "sandbox"
    root.mkdir()
    monkeypatch.setattr(executor.config, "SANDBOX_DIR", str(root))
    d = root / "1"
    d.mkdir()
    (d / "a.py").write_text("x = 1\n", encoding="utf-8")
    (d / "b.py").write_text("y = 2\n", encoding="utf-8")
    return d


# --- 1. Дерево видит файлы, побега нет --------------------------------


def test_project_tree_lists_files(task):
    from agent import explorer

    tree = explorer.project_tree(1)
    assert "a.py" in tree
    assert "b.py" in tree


def test_read_many_returns_content(task):
    from agent import explorer

    files = explorer.read_many(1, ["a.py", "b.py"])
    assert files["a.py"].strip() == "x = 1"
    assert files["b.py"].strip() == "y = 2"


def test_read_many_missing_file_is_soft(task):
    from agent import explorer

    files = explorer.read_many(1, ["нет.py"])
    assert "ошибка" in files["нет.py"]


# --- 2. Полный цикл: выбор -> правка N файлов -> зелёный gate ---------


def test_solve_auto_writes_multiple_files(task, monkeypatch):
    """Модель выбирает 2 файла и правит оба; gate зелёный сразу."""
    calls = []

    def fake_gen(prompt, **kw):
        calls.append(prompt)
        if len(calls) == 1:  # explore -> JSON со списком файлов
            return _gr('{"files": ["a.py", "b.py"]}')
        # edit -> два FILE-блока
        return _gr(
            "=== FILE: a.py ===\nx = 10\n\n"
            "=== FILE: b.py ===\ny = 20\n"
        )

    monkeypatch.setattr(core.gemini, "generate_text", fake_gen)
    monkeypatch.setattr(core.gate, "run_gate", lambda tid: _FakeGate(True))
    monkeypatch.setattr(core.sandbox, "collect_diff", lambda tid: "diff-stub")

    out = core.solve_task_auto(1, "поменяй значения", max_iterations=2)

    assert out.success is True
    assert out.iterations == 0  # зелёный с первого раза
    assert (task / "a.py").read_text().strip() == "x = 10"
    assert (task / "b.py").read_text().strip() == "y = 20"
    assert len(calls) == 2  # explore + edit, без фикс-итераций


# --- 3. Пустые блоки -> прекращаем, success=False --------------------


def test_solve_auto_no_blocks_aborts(task, monkeypatch):
    def fake_gen(prompt, **kw):
        if "files" not in prompt.lower():
            return _gr('{"files": ["a.py"]}')
        return _gr('{"files": ["a.py"]}')

    # explore вернёт файл, edit — мусор без FILE-блоков
    seq = iter([
        _gr('{"files": ["a.py"]}'),
        _gr("извините, не смог"),
    ])
    monkeypatch.setattr(core.gemini, "generate_text", lambda *a, **k: next(seq))
    monkeypatch.setattr(core.gate, "run_gate", lambda tid: _FakeGate(False))

    out = core.solve_task_auto(1, "задача", max_iterations=2)

    assert out.success is False
    assert out.iterations == 0
    assert any("не вернула" in s for s in out.steps)


# --- 4. Красный gate -> фикс-итерация чинит -> зелёный ---------------


def test_solve_auto_fix_iteration(task, monkeypatch):
    seq = iter([
        _gr('{"files": ["a.py"]}'),                       # explore
        _gr("=== FILE: a.py ===\nx = broken(\n"),       # первичная правка (битая)
        _gr("=== FILE: a.py ===\nx = 99\n"),            # фикс
    ])
    gates = iter([_FakeGate(False, ["ruff"]), _FakeGate(True)])
    monkeypatch.setattr(core.gemini, "generate_text", lambda *a, **k: next(seq))
    monkeypatch.setattr(core.gate, "run_gate", lambda tid: next(gates))
    monkeypatch.setattr(core.sandbox, "collect_diff", lambda tid: "d")

    out = core.solve_task_auto(1, "почини", max_iterations=3)

    assert out.success is True
    assert out.iterations == 1
    assert (task / "a.py").read_text().strip() == "x = 99"
