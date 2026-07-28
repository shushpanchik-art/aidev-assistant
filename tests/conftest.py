"""Общие фикстуры pytest: временная БД с применённой схемой."""
from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest_asyncio

# Заглушки для CI без .env (config читает окружение при импорте).
os.environ.setdefault("WEB_AUTH_TOKEN", "test-token")

from db import database  # noqa: E402


@pytest_asyncio.fixture
async def db_path(tmp_path) -> AsyncIterator[str]:
    """Путь к чистой tmp-БД с применённой schema.sql."""
    path = str(tmp_path / "test_aidev.db")
    await database.init_db(path)
    yield path
