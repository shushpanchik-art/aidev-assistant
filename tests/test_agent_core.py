"""Тесты Agent Core (SPEC §1.3) на моках gemini.

extract_code — разбор ответа модели.
solve_task — цикл план->правка->gate->фикс, БЕЗ реальных AI-вызовов.
Приёмочный smoke: подсовываем битый файл, мок "модели" его чинит,
проверяем что gate позеленел и баг исчез (имитация сценария владельца).
"""
from __future__ import annotations

import os

os.environ.setdefault("WEB_AUTH_TOKEN", "test-token")

import pytest  # noqa: E402
from pathlib import Path  # noqa: E402

from agent import core, executor  # noqa: E402
from ai import gemini  # noqa: E402


# --- extract_code -----------------------------------------------------

def test_extract_code_from_block():
    txt = "Готово:\n```python\nx = 1\n```\nвсё."
    assert core.extract_code(txt) == "x = 1\n"


def test_extract_code_no_language_tag():
    assert core.extract_code("```\ny = 2\n```") == "y = 2\n"


def test_extract_code_bare_text():
    assert core.extract_code("z = 3") == "z = 3\n"


# --- sandbox под tmp --------------------------------------------------

@pytest.fixture
def task(tmp_path, monkeypatch):
    """SANDBOX_DIR -> tmp; каталог задачи 1 готов."""
    root = tmp_path / "sandbox"
    root.mkdir()
    monkeypatch.setattr(executor.config, "SANDBOX_DIR", str(root))
    # sandbox.py и core вызывают executor.sandbox_root косвенно; gate тоже
    (root / "1").mkdir()
    return root / "1"


def _write(task_dir, rel, content):
    p = task_dir / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


_HAS_VENV = (
    Path(executor._VENV_BIN, "ruff").exists()
    and Path(executor._VENV_BIN, "python").exists()
)
_skip_no_venv = pytest.mark.skipif(
    not _HAS_VENV, reason="нет venv/bin: реальный gate недоступен (CI)"
)


# --- solve_task: gate зелёный с первого раза --------------------------

@_skip_no_venv
def test_solve_task_green_first_try(task, monkeypatch):
    _write(task, "mod.py", "x=1\n")  # ruff-нарушение (нет пробелов)
    _write(task, "test_ok.py", "def test_ok():\n    assert True\n")

    def fake_gen(prompt, **kw):
        if kw.get("model") == "pro":
            return gemini.GenResult(
                text='{"approved": true, "risks": "", "comments": "ok"}',
                input_tokens=0, output_tokens=0,
                model="gemini-2.5-pro", via="primary",
            )
        return gemini.GenResult(
            text="```python\nx = 1\n```",
            input_tokens=10, output_tokens=5, model="gemini-2.5-flash", via="primary",
        )

    monkeypatch.setattr(core.sandbox, "read_file", lambda tid, p: (task / p).read_text())
    monkeypatch.setattr(core.sandbox, "write_file",
                        lambda tid, p, c: (task / p).write_text(c) or (task / p))
    monkeypatch.setattr(core.sandbox, "collect_diff", lambda tid: "diff")
    monkeypatch.setattr(core.gemini, "generate_text", fake_gen)

    out = core.solve_task(1, "убери лишние пробелы", "mod.py", max_iterations=2)
    assert out.success
    assert out.iterations == 0
    assert (task / "mod.py").read_text() == "x = 1\n"
    assert out.input_tokens == 10 and out.output_tokens == 5


# --- ПРИЁМОЧНЫЙ SMOKE: битый код -> агент чинит ------------------------

@_skip_no_venv
def test_acceptance_broken_code_gets_fixed(task, monkeypatch):
    """Сценарий владельца: подсунут баг, 'агент' его исправляет за 1 итерацию.

    Первый ответ модели оставляет ruff-ошибку (unused import) -> gate красный.
    Второй ответ (фикс) её убирает -> gate зелёный. Проверяем итог.
    """
    _write(task, "buggy.py", "import os\nresult = 1 + 1\n")  # unused import (F401)
    _write(task, "test_ok.py", "def test_ok():\n    assert True\n")

    calls = {"n": 0}

    def fake_gen(prompt, **kw):
        if kw.get("model") == "pro":
            return gemini.GenResult(
                text='{"approved": true, "risks": "", "comments": "ok"}',
                input_tokens=0, output_tokens=0,
                model="gemini-2.5-pro", via="primary",
            )
        calls["n"] += 1
        if calls["n"] == 1:
            # "правка" оставляет баг нетронутым
            text = "```python\nimport os\nresult = 1 + 2\n```"
        else:
            # фикс по выводу gate: убираем unused import
            text = "```python\nresult = 1 + 2\n```"
        return gemini.GenResult(
            text=text, input_tokens=20, output_tokens=8,
            model="gemini-2.5-flash", via="primary",
        )

    monkeypatch.setattr(core.sandbox, "read_file", lambda tid, p: (task / p).read_text())
    monkeypatch.setattr(core.sandbox, "write_file",
                        lambda tid, p, c: (task / p).write_text(c) or (task / p))
    monkeypatch.setattr(core.sandbox, "collect_diff", lambda tid: "diff")
    monkeypatch.setattr(core.gemini, "generate_text", fake_gen)

    out = core.solve_task(
        1, "почини баг: убери неиспользуемый импорт", "buggy.py", max_iterations=3
    )

    assert out.success, f"gate не позеленел: {out.gate.summary()}"
    assert out.iterations == 1, "ожидали ровно одну фикс-итерацию"
    assert calls["n"] == 2, "модель должна быть вызвана дважды (правка + фикс)"
    final = (task / "buggy.py").read_text()
    assert "import os" not in final, "unused import не убран"
    assert "result = 1 + 2" in final
