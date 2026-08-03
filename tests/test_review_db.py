import json

import pytest

from db import database


@pytest.mark.asyncio
async def test_set_task_review_roundtrip(db_path: str) -> None:
    tid = await database.create_task("p", "task", db_path=db_path)
    review = {
        "approved": True,
        "risks": "нет",
        "comments": "хорошо",
    }
    await database.set_task_review(tid, review, db_path=db_path)
    task = await database.get_task(tid, db_path=db_path)
    assert task is not None
    assert task["review_approved"] == 1
    stored = json.loads(task["review_json"])
    assert stored == review


@pytest.mark.asyncio
async def test_set_task_review_not_approved(db_path: str) -> None:
    tid = await database.create_task("p", "task", db_path=db_path)
    await database.set_task_review(tid, {"approved": False}, db_path=db_path)
    task = await database.get_task(tid, db_path=db_path)
    assert task is not None
    assert task["review_approved"] == 0
