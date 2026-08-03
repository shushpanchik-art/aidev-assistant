"""Тесты промптов агента: без AI-вызовов, только структура строк."""
from ai import prompts


def test_system_prompt_has_obligations() -> None:
    s = prompts.SYSTEM_DEVELOPER.lower()
    assert "ruff" in s
    assert "mypy" in s
    assert "bandit" in s
    assert "pytest" in s
    assert "песочниц" in s
    assert "секрет" in s


def test_safety_rules_forbid_dangerous() -> None:
    s = prompts.SAFETY_RULES.lower()
    assert "eval" in s
    assert "секрет" in s


def test_complexity_prompt_asks_json() -> None:
    p = prompts.complexity_prompt("добавь функцию X")
    assert "json" in p.lower()
    assert "needs_pro" in p
    assert "добавь функцию X" in p


def test_plan_prompt_contains_task() -> None:
    p = prompts.plan_prompt("рефактор db.py", files_summary="db.py: 100 строк")
    assert "рефактор db.py" in p
    assert "db.py: 100 строк" in p


def test_edit_prompt_embeds_code_and_safety() -> None:
    p = prompts.edit_prompt("почини баг", "db.py", "x = 1")
    assert "db.py" in p
    assert "x = 1" in p
    assert "```python" in p
    assert "секрет" in p.lower()


def test_new_file_prompt() -> None:
    p = prompts.new_file_prompt("создай хелпер", "utils.py")
    assert "utils.py" in p
    assert "```python" in p


def test_fix_prompt_includes_gate_output() -> None:
    p = prompts.fix_prompt("f.py", "code", "E501 line too long")
    assert "E501 line too long" in p
    assert "f.py" in p
    assert "code" in p


def test_explain_prompt_json_shape() -> None:
    p = prompts.explain_prompt("задача", "изменён 1 файл")
    assert "done" in p
    assert "risks" in p
    assert "files_changed" in p
    assert "изменён 1 файл" in p


def test_all_builders_return_nonempty_str() -> None:
    builders = [
        prompts.complexity_prompt("t"),
        prompts.plan_prompt("t"),
        prompts.edit_prompt("t", "f.py", "c"),
        prompts.new_file_prompt("t", "f.py"),
        prompts.fix_prompt("f.py", "c", "err"),
        prompts.explain_prompt("t", "d"),
    ]
    for p in builders:
        assert isinstance(p, str)
        assert len(p) > 20


def test_review_prompt_embeds_task_diff_gate() -> None:
    p = prompts.review_prompt("добавь X", "diff --git a/x", "ruff ✅ pytest ✅")
    assert "добавь X" in p
    assert "diff --git a/x" in p
    assert "ruff ✅ pytest ✅" in p
    assert "approved" in p
    assert "json" in p.lower()
    assert "секрет" in p.lower()


def test_parse_review_valid_json() -> None:
    r = prompts.parse_review(
        '{"approved": true, "risks": "низкие", "comments": "ок"}'
    )
    assert r["approved"] is True
    assert r["risks"] == "низкие"
    assert r["comments"] == "ок"


def test_parse_review_fenced_json() -> None:
    r = prompts.parse_review(
        'вот вердикт:\n```json\n{"approved": false, "risks": "SQL"}\n```'
    )
    assert r["approved"] is False
    assert r["risks"] == "SQL"


def test_parse_review_garbage_is_not_approved() -> None:
    r = prompts.parse_review("это не json вовсе")
    assert r["approved"] is False
    assert "разобрать" in str(r["comments"])


def test_parse_review_non_dict_is_not_approved() -> None:
    r = prompts.parse_review("[1, 2, 3]")
    assert r["approved"] is False
