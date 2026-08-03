import pytest

import db.database as database


@pytest.mark.asyncio
async def test_attachment_text_roundtrip(db_path: str) -> None:
    tid = await database.create_task("p", "task", db_path=db_path)
    await database.add_attachment(
        tid, "text", "spec.txt",
        mime="text/plain", content_text="hello spec",
        db_path=db_path,
    )
    rows = await database.get_attachments(tid, db_path=db_path)
    assert len(rows) == 1
    assert rows[0]["kind"] == "text"
    assert rows[0]["filename"] == "spec.txt"
    assert rows[0]["content_text"] == "hello spec"
    assert rows[0]["content_b64"] is None


@pytest.mark.asyncio
async def test_attachment_screenshot_b64(db_path: str) -> None:
    tid = await database.create_task("p", "task", db_path=db_path)
    b64 = "aGVsbG8="  # "hello"
    await database.add_attachment(
        tid, "screenshot", "shot.png",
        mime="image/png", content_b64=b64,
        db_path=db_path,
    )
    rows = await database.get_attachments(tid, db_path=db_path)
    assert len(rows) == 1
    assert rows[0]["kind"] == "screenshot"
    assert rows[0]["mime"] == "image/png"
    assert rows[0]["content_b64"] == b64
    assert rows[0]["content_text"] is None


@pytest.mark.asyncio
async def test_attachments_ordered(db_path: str) -> None:
    tid = await database.create_task("p", "task", db_path=db_path)
    await database.add_attachment(
        tid, "pasted", "a.txt", content_text="one", db_path=db_path
    )
    await database.add_attachment(
        tid, "text", "b.txt", content_text="two", db_path=db_path
    )
    rows = await database.get_attachments(tid, db_path=db_path)
    assert [r["filename"] for r in rows] == ["a.txt", "b.txt"]
