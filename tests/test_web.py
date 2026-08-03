"""Тесты FastAPI-слоя (SPEC §13 test_web): auth 401/200, запуск задачи на моке.

AI и sandbox замоканы — реальные вызовы Gemini/git не выполняются.
БД — tmp через monkeypatch DB_PATH во всех CRUD-функциях.
"""
from __future__ import annotations

import httpx
import pytest

import config
from agent import core, sandbox
from db import database
from web import app as webapp


@pytest.fixture
def client(db_path, monkeypatch):
    """AsyncClient поверх ASGI + tmp-БД во всех DB-функциях + токен."""
    monkeypatch.setattr(config, "WEB_AUTH_TOKEN", "test-token")
    monkeypatch.setattr(database, "DB_PATH", db_path)

    # Подставляем tmp db_path во все CRUD-обёртки (у них db_path=DB_PATH kwarg).
    for name in (
        "create_task", "get_task", "update_task_status", "list_tasks",
        "add_step", "get_steps", "add_diff", "today_cost_usd",
        "set_task_pr", "add_attachment", "get_attachments",
    ):
        orig = getattr(database, name)

        def make(fn):
            async def wrapper(*a, **kw):
                kw.setdefault("db_path", db_path)
                return await fn(*a, **kw)
            return wrapper

        monkeypatch.setattr(webapp.database, name, make(orig))

    transport = httpx.ASGITransport(app=webapp.app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_healthz_no_auth(client):
    async with client as c:
        r = await c.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_index_requires_auth(client):
    async with client as c:
        r = await c.get("/")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_index_with_bearer(client):
    async with client as c:
        r = await c.get("/", headers={"Authorization": "Bearer test-token"})
    assert r.status_code == 200
    assert "Новая задача" in r.text


@pytest.mark.asyncio
async def test_index_with_cookie(client):
    async with client as c:
        c.cookies.set("auth", "test-token")
        r = await c.get("/")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_login_wrong_token(client):
    async with client as c:
        r = await c.post("/login", data={"token": "nope"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_history_and_settings(client):
    async with client as c:
        h = await c.get("/history", headers={"Authorization": "Bearer test-token"})
        s = await c.get("/settings", headers={"Authorization": "Bearer test-token"})
    assert h.status_code == 200 and "История" in h.text
    assert s.status_code == 200 and "Настройки" in s.text


@pytest.mark.asyncio
async def test_create_task_runs_mocked(client, db_path, monkeypatch):
    project = config.ALLOWED_PROJECTS[0]

    def fake_prepare(task_id, project_path):
        return None

    def fake_solve(task_id, task, rel_path, *, model="flash", max_iterations=4):
        return core.TaskOutcome(
            success=True,
            iterations=0,
            gate=None,  # type: ignore[arg-type]
            diff="--- a\n+++ b\n",
            model="gemini-2.5-flash",
            steps=["Прочитан файл", "Внесена правка", "Gate зелёный"],
        )

    monkeypatch.setattr(sandbox, "prepare_workspace", fake_prepare)
    monkeypatch.setattr(webapp.sandbox, "prepare_workspace", fake_prepare)
    monkeypatch.setattr(core, "solve_task", fake_solve)
    monkeypatch.setattr(webapp.core, "solve_task", fake_solve)

    async with client as c:
        r = await c.post(
            "/api/task",
            headers={"Authorization": "Bearer test-token"},
            data={
                "task": "добавь проверку",
                "project": project,
                "rel_path": "handlers/start.py",
                "level": "1",
            },
            follow_redirects=False,
        )
    assert r.status_code == 303
    assert r.headers["location"].startswith("/task/")

    tasks = await database.list_tasks(db_path=db_path)
    assert len(tasks) == 1
    assert tasks[0]["status"] == "done"
    assert tasks[0]["project"] == project
    steps = await database.get_steps(tasks[0]["id"], db_path=db_path)
    assert any("Gate зелёный" in (s["output_summary"] or "") for s in steps)


@pytest.mark.asyncio
async def test_create_task_rejects_foreign_project(client):
    async with client as c:
        r = await c.post(
            "/api/task",
            headers={"Authorization": "Bearer test-token"},
            data={
                "task": "x",
                "project": "/etc/passwd",
                "rel_path": "a.py",
                "level": "1",
            },
        )
    assert r.status_code == 400



@pytest.mark.asyncio
async def test_pr_button_visible_when_done(client, db_path):
    project = config.ALLOWED_PROJECTS[0]
    tid = await database.create_task(project, "p", autonomy_level=1, db_path=db_path)
    await database.update_task_status(tid, "done", db_path=db_path)
    async with client as c:
        r = await c.get(
            f"/task/{tid}", headers={"Authorization": "Bearer test-token"}
        )
    assert r.status_code == 200
    assert f"/api/task/{tid}/pr" in r.text
    assert "Создать Pull Request" in r.text


@pytest.mark.asyncio
async def test_pr_button_hidden_when_pending(client, db_path):
    project = config.ALLOWED_PROJECTS[0]
    tid = await database.create_task(project, "p", autonomy_level=1, db_path=db_path)
    async with client as c:
        r = await c.get(
            f"/task/{tid}", headers={"Authorization": "Bearer test-token"}
        )
    assert r.status_code == 200
    assert f"/api/task/{tid}/pr" not in r.text


@pytest.mark.asyncio
async def test_pr_create_success_mocked(client, db_path, monkeypatch):
    project = config.ALLOWED_PROJECTS[0]
    tid = await database.create_task(project, "p", autonomy_level=1, db_path=db_path)
    await database.update_task_status(tid, "done", db_path=db_path)

    def fake_pr(task_id, project_path, title, body, *, base="main"):
        return "https://github.com/o/r/pull/7"

    monkeypatch.setattr(sandbox, "create_pull_request", fake_pr)
    monkeypatch.setattr(webapp.sandbox, "create_pull_request", fake_pr)

    async with client as c:
        r = await c.post(
            f"/api/task/{tid}/pr",
            headers={"Authorization": "Bearer test-token"},
            follow_redirects=False,
        )
    assert r.status_code == 303
    assert r.headers["location"] == f"/task/{tid}"

    task = await database.get_task(tid, db_path=db_path)
    assert task is not None
    assert task["pr_url"] == "https://github.com/o/r/pull/7"
    assert task["branch"] == f"aidev/task-{tid}"


@pytest.mark.asyncio
async def test_pr_create_rejects_pending(client, db_path):
    project = config.ALLOWED_PROJECTS[0]
    tid = await database.create_task(project, "p", autonomy_level=1, db_path=db_path)
    async with client as c:
        r = await c.post(
            f"/api/task/{tid}/pr",
            headers={"Authorization": "Bearer test-token"},
        )
    assert r.status_code == 409
