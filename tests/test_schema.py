"""Проверка слоя БД на tmp-базе."""
from __future__ import annotations

import pytest

from db import database

pytestmark = pytest.mark.asyncio


async def test_init_creates_tables(db_path: str) -> None:
    import aiosqlite

    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        names = {r[0] for r in await cur.fetchall()}
    expected = {
        "tasks", "task_steps", "ai_usage", "diffs", "gate_runs",
        "settings", "attachments",
    }
    assert expected <= names


async def test_task_lifecycle(db_path: str) -> None:
    tid = await database.create_task("/opt/SMOKI/bot", "fix bug", db_path=db_path)
    assert tid > 0
    task = await database.get_task(tid, db_path=db_path)
    assert task is not None
    assert task["status"] == "pending"
    assert task["project"] == "/opt/SMOKI/bot"

    await database.update_task_status(
        tid, "done", model_used="flash", finished=True, db_path=db_path
    )
    task2 = await database.get_task(tid, db_path=db_path)
    assert task2["status"] == "done"
    assert task2["model_used"] == "flash"
    assert task2["finished_at"] is not None

    tasks = await database.list_tasks(db_path=db_path)
    assert any(t["id"] == tid for t in tasks)


async def test_steps_and_diffs(db_path: str) -> None:
    tid = await database.create_task("p", "x", db_path=db_path)
    await database.add_step(tid, 1, "plan", output_summary="ok", db_path=db_path)
    steps = await database.get_steps(tid, db_path=db_path)
    assert len(steps) == 1 and steps[0]["kind"] == "plan"

    await database.add_diff(tid, "a.py", "@@ diff @@", db_path=db_path)
    diffs = await database.get_diffs(tid, db_path=db_path)
    assert len(diffs) == 1 and diffs[0]["applied"] == 0


async def test_ai_usage_today_cost(db_path: str) -> None:
    tid = await database.create_task("p", "x", db_path=db_path)
    await database.log_ai_usage("flash", 1000, 500, 0.0015, task_id=tid, db_path=db_path)
    await database.log_ai_usage("pro", 2000, 800, 0.0105, db_path=db_path)
    cost = await database.today_cost_usd(db_path=db_path)
    assert cost == pytest.approx(0.012, abs=1e-6)


async def test_gate_runs(db_path: str) -> None:
    tid = await database.create_task("p", "x", db_path=db_path)
    await database.log_gate_run(tid, "ruff", 0, output_tail="clean", db_path=db_path)
    await database.log_gate_run(tid, "pytest", 1, output_tail="fail", db_path=db_path)
    runs = await database.get_gate_runs(tid, db_path=db_path)
    assert len(runs) == 2
    assert {r["tool"] for r in runs} == {"ruff", "pytest"}


async def test_settings_upsert(db_path: str) -> None:
    await database.set_setting("model", "flash", db_path=db_path)
    assert await database.get_setting("model", db_path=db_path) == "flash"
    await database.set_setting("model", "pro", db_path=db_path)
    assert await database.get_setting("model", db_path=db_path) == "pro"
    assert await database.get_setting("nope", "def", db_path=db_path) == "def"

async def test_attachments(db_path: str) -> None:
    tid = await database.create_task("p", "x", db_path=db_path)
    aid = await database.add_attachment(
        tid, "text", "spec.txt", mime="text/plain",
        content_text="hello", db_path=db_path,
    )
    assert aid > 0
    await database.add_attachment(
        tid, "image", "scr.png", mime="image/png",
        content_b64="AAA=", db_path=db_path,
    )
    atts = await database.get_attachments(tid, db_path=db_path)
    assert len(atts) == 2
    assert atts[0]["kind"] == "text"
    assert atts[0]["content_text"] == "hello"
    assert atts[1]["kind"] == "image"
    assert atts[1]["content_b64"] == "AAA="
