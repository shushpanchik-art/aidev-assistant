"""Асинхронный слой доступа к SQLite (aiosqlite) для AIDEV Assistant.

Схема — db/schema.sql. Точка входа: init_db() создаёт таблицы.
Все функции принимают путь к БД (db_path) для тестируемости с tmp_db.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import aiosqlite

from config import DB_PATH

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


async def init_db(db_path: str = DB_PATH) -> None:
    """Создать таблицы из schema.sql (идемпотентно)."""
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(sql)
        await _migrate(db)
        await db.commit()


async def _migrate(db: aiosqlite.Connection) -> None:
    """Идемпотентно добавить недостающие колонки в существующие БД."""
    cur = await db.execute("PRAGMA table_info(tasks)")
    cols = {row[1] for row in await cur.fetchall()}
    for col in ("branch", "pr_url"):
        if col not in cols:
            await db.execute(f"ALTER TABLE tasks ADD COLUMN {col} TEXT")


def _row_to_dict(row: aiosqlite.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


# --- tasks ---

async def create_task(
    project: str,
    prompt: str,
    *,
    autonomy_level: int = 1,
    db_path: str = DB_PATH,
) -> int:
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "INSERT INTO tasks (project, prompt, autonomy_level) VALUES (?, ?, ?)",
            (project, prompt, autonomy_level),
        )
        await db.commit()
        return int(cur.lastrowid or 0)


async def get_task(task_id: int, *, db_path: str = DB_PATH) -> dict[str, Any] | None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        return _row_to_dict(await cur.fetchone())


async def update_task_status(
    task_id: int,
    status: str,
    *,
    model_used: str | None = None,
    finished: bool = False,
    db_path: str = DB_PATH,
) -> None:
    # fin — фиксированный литерал из кода (не пользовательский ввод),
    # SQL-инъекция невозможна; параметры передаются через ? плейсхолдеры.
    fin = "datetime('now')" if finished else "finished_at"
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            f"UPDATE tasks SET status = ?, "
            f"model_used = COALESCE(?, model_used), "
            f"finished_at = {fin} WHERE id = ?",  # nosec B608
            (status, model_used, task_id),
        )
        await db.commit()


async def set_task_pr(
    task_id: int,
    branch: str,
    pr_url: str,
    *,
    db_path: str = DB_PATH,
) -> None:
    """Сохранить ветку и ссылку на PR для задачи."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "UPDATE tasks SET branch = ?, pr_url = ? WHERE id = ?",
            (branch, pr_url, task_id),
        )
        await db.commit()


async def list_tasks(
    *, limit: int = 50, db_path: str = DB_PATH
) -> list[dict[str, Any]]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM tasks ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in await cur.fetchall()]


# --- task_steps ---

async def add_step(
    task_id: int,
    step_no: int,
    kind: str,
    *,
    input_summary: str | None = None,
    output_summary: str | None = None,
    gate_result: str | None = None,
    db_path: str = DB_PATH,
) -> int:
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "INSERT INTO task_steps "
            "(task_id, step_no, kind, input_summary, output_summary, gate_result) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (task_id, step_no, kind, input_summary, output_summary, gate_result),
        )
        await db.commit()
        return int(cur.lastrowid or 0)


async def get_steps(task_id: int, *, db_path: str = DB_PATH) -> list[dict[str, Any]]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM task_steps WHERE task_id = ? ORDER BY step_no", (task_id,)
        )
        return [dict(r) for r in await cur.fetchall()]


# --- ai_usage ---

async def log_ai_usage(
    model: str,
    input_tokens: int,
    output_tokens: int,
    est_cost_usd: float,
    *,
    task_id: int | None = None,
    db_path: str = DB_PATH,
) -> int:
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "INSERT INTO ai_usage "
            "(task_id, model, input_tokens, output_tokens, est_cost_usd) "
            "VALUES (?, ?, ?, ?, ?)",
            (task_id, model, input_tokens, output_tokens, est_cost_usd),
        )
        await db.commit()
        return int(cur.lastrowid or 0)


async def today_cost_usd(*, db_path: str = DB_PATH) -> float:
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "SELECT COALESCE(SUM(est_cost_usd), 0) FROM ai_usage "
            "WHERE date(created_at) = date('now')"
        )
        row = await cur.fetchone()
        return float(row[0]) if row else 0.0


# --- diffs ---

async def add_diff(
    task_id: int,
    file_path: str,
    diff_text: str,
    *,
    applied: bool = False,
    db_path: str = DB_PATH,
) -> int:
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "INSERT INTO diffs (task_id, file_path, diff_text, applied) "
            "VALUES (?, ?, ?, ?)",
            (task_id, file_path, diff_text, 1 if applied else 0),
        )
        await db.commit()
        return int(cur.lastrowid or 0)


async def get_diffs(task_id: int, *, db_path: str = DB_PATH) -> list[dict[str, Any]]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM diffs WHERE task_id = ? ORDER BY id", (task_id,)
        )
        return [dict(r) for r in await cur.fetchall()]


# --- gate_runs ---

async def log_gate_run(
    task_id: int,
    tool: str,
    exit_code: int,
    *,
    output_tail: str | None = None,
    duration_sec: float = 0.0,
    db_path: str = DB_PATH,
) -> int:
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "INSERT INTO gate_runs "
            "(task_id, tool, exit_code, output_tail, duration_sec) "
            "VALUES (?, ?, ?, ?, ?)",
            (task_id, tool, exit_code, output_tail, duration_sec),
        )
        await db.commit()
        return int(cur.lastrowid or 0)


async def get_gate_runs(task_id: int, *, db_path: str = DB_PATH) -> list[dict[str, Any]]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM gate_runs WHERE task_id = ? ORDER BY id", (task_id,)
        )
        return [dict(r) for r in await cur.fetchall()]


# --- settings ---

async def set_setting(key: str, value: str, *, db_path: str = DB_PATH) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await db.commit()


async def get_setting(
    key: str, default: str | None = None, *, db_path: str = DB_PATH
) -> str | None:
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = await cur.fetchone()
        return row[0] if row else default
