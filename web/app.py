"""FastAPI веб-интерфейс агента (SPEC §8, §1.4).

Аутентификация: заголовок ``Authorization: Bearer <WEB_AUTH_TOKEN>`` ИЛИ
cookie ``auth`` (для браузера через SSH-туннель). Все API/страницы, кроме
``/login`` и ``/healthz``, требуют токен.

Экраны (человеко-читаемые, без дифф-вьюера — код смотрится в PR на GitHub):
* ``/``            — новая задача (проект из allow-list, уровень L0–L3).
* ``/history``     — список задач (статус, проект, модель, стоимость).
* ``/task/{id}``   — результат: шаги, риски, проверки.
* ``/settings``    — модель по умолчанию, escalation, бюджет.

Запуск задачи (``POST /api/task``) выполняется в отдельном потоке, т.к.
``core.solve_task`` синхронный и CPU/IO-bound.
"""
from __future__ import annotations

import asyncio
import html
import logging

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

import config
from agent import core, sandbox
from db import database

logger = logging.getLogger("aidev.web")

app = FastAPI(title="AIDEV Assistant", docs_url=None, redoc_url=None)


# --------------------------------------------------------------------------- #
# Аутентификация                                                              #
# --------------------------------------------------------------------------- #
def _extract_token(request: Request) -> str:
    """Токен из заголовка Bearer или cookie ``auth``."""
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.cookies.get("auth", "")


def require_auth(request: Request) -> None:
    """Зависимость: 401, если токен не совпал с WEB_AUTH_TOKEN."""
    expected = config.WEB_AUTH_TOKEN
    if not expected or _extract_token(request) != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


# --------------------------------------------------------------------------- #
# Мини-шаблоны (без Jinja — простые f-строки, всё экранируется)               #
# --------------------------------------------------------------------------- #
def _page(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang='ru'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(title)}</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:820px;"
        "margin:2rem auto;padding:0 1rem;background:#0d1117;color:#e6edf3}"
        "a{color:#58a6ff}input,select,textarea,button{font:inherit;"
        "padding:.5rem;border-radius:6px;border:1px solid #30363d;"
        "background:#161b22;color:#e6edf3;width:100%;box-sizing:border-box;"
        "margin:.3rem 0}button{cursor:pointer;background:#238636;border:0}"
        "nav a{margin-right:1rem}table{width:100%;border-collapse:collapse}"
        "td,th{border-bottom:1px solid #30363d;padding:.4rem;text-align:left}"
        ".ok{color:#3fb950}.bad{color:#f85149}.muted{color:#8b949e}</style>"
        "</head><body><nav><a href='/'>Новая задача</a>"
        "<a href='/history'>История</a><a href='/settings'>Настройки</a></nav>"
        f"<h1>{html.escape(title)}</h1>{body}</body></html>"
    )


def _project_options() -> str:
    return "".join(
        f"<option value='{html.escape(p)}'>{html.escape(p)}</option>"
        for p in config.ALLOWED_PROJECTS
    )


# --------------------------------------------------------------------------- #
# Служебные (без auth)                                                        #
# --------------------------------------------------------------------------- #
@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/login", response_class=HTMLResponse)
async def login_form() -> str:
    body = (
        "<form method='post' action='/login'>"
        "<label>Токен доступа</label>"
        "<input type='password' name='token' autofocus>"
        "<button type='submit'>Войти</button></form>"
    )
    return _page("Вход", body)


@app.post("/login")
async def login_submit(token: str = Form(...)) -> Response:
    if not config.WEB_AUTH_TOKEN or token != config.WEB_AUTH_TOKEN:
        return HTMLResponse(_page("Вход", "<p class='bad'>Неверный токен.</p>"
                                  "<a href='/login'>Назад</a>"), status_code=401)
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie("auth", token, httponly=True, samesite="strict", max_age=86400)
    return resp


# --------------------------------------------------------------------------- #
# Экраны (auth)                                                               #
# --------------------------------------------------------------------------- #
@app.get("/", response_class=HTMLResponse, dependencies=[Depends(require_auth)])
async def index() -> str:
    body = (
        f"<form method='post' action='/api/task'>"
        "<label>Задача (простым языком)</label>"
        "<textarea name='task' rows='4' required "
        "placeholder='Например: добавь проверку возраста в handlers/start.py'>"
        "</textarea>"
        "<label>Проект</label>"
        f"<select name='project'>{_project_options()}</select>"
        "<label>Файл в проекте (rel_path)</label>"
        "<input name='rel_path' required placeholder='handlers/start.py'>"
        "<label>Уровень автономии</label>"
        "<select name='level'>"
        "<option value='0'>L0 — только советует</option>"
        f"<option value='1' {'selected' if config.AUTONOMY_LEVEL == 1 else ''}>"
        "L1 — правит в песочнице (default)</option>"
        "<option value='2'>L2 — авто-PR при зелёном gate</option>"
        "</select>"
        f"<p class='muted'>Модель по умолчанию: {html.escape(config.AI_DEFAULT_MODEL)}</p>"
        "<button type='submit'>Поставить задачу</button></form>"
    )
    return _page("Новая задача", body)


@app.get("/history", response_class=HTMLResponse,
         dependencies=[Depends(require_auth)])
async def history() -> str:
    tasks = await database.list_tasks(limit=50)
    if not tasks:
        rows = "<tr><td colspan='5' class='muted'>Пока пусто.</td></tr>"
    else:
        rows = "".join(
            f"<tr><td><a href='/task/{t['id']}'>#{t['id']}</a></td>"
            f"<td>{html.escape(str(t.get('project', '')))}</td>"
            f"<td>{html.escape(str(t.get('status', '')))}</td>"
            f"<td>{html.escape(str(t.get('model_used', '')))}</td>"
            f"<td>{html.escape(str(t.get('created_at', '')))}</td></tr>"
            for t in tasks
        )
    body = ("<table><tr><th>#</th><th>Проект</th><th>Статус</th>"
            f"<th>Модель</th><th>Создана</th></tr>{rows}</table>")
    return _page("История задач", body)


@app.get("/task/{task_id}", response_class=HTMLResponse,
         dependencies=[Depends(require_auth)])
async def task_view(task_id: int) -> Response:
    task = await database.get_task(task_id)
    if task is None:
        return HTMLResponse(_page("Задача", "<p class='bad'>Не найдена.</p>"),
                            status_code=404)
    steps = await database.get_steps(task_id)
    steps_html = "".join(
        f"<li>{html.escape(str(st.get('output_summary') or st.get('kind', '')))}</li>"
        for st in steps
    ) or "<li class='muted'>Шагов нет.</li>"
    status = html.escape(str(task.get("status", "")))
    cls = "ok" if status in {"done", "success"} else "muted"
    body = (
        f"<p>Статус: <span class='{cls}'>{status}</span></p>"
        f"<p>Проект: {html.escape(str(task.get('project', '')))}</p>"
        f"<h2>📝 Что сделано</h2><ul>{steps_html}</ul>"
    )
    return HTMLResponse(_page(f"Задача #{task_id}", body))


@app.get("/settings", response_class=HTMLResponse,
         dependencies=[Depends(require_auth)])
async def settings_view() -> str:
    cost = await database.today_cost_usd()
    body = (
        "<table>"
        f"<tr><th>Модель по умолчанию</th><td>{html.escape(config.AI_DEFAULT_MODEL)}</td></tr>"
        f"<tr><th>Escalation</th><td>{html.escape(config.AI_ESCALATION)}</td></tr>"
        f"<tr><th>Дневной бюджет</th><td>${config.AI_DAILY_BUDGET_USD:.2f}</td></tr>"
        f"<tr><th>Потрачено сегодня</th><td>${cost:.4f}</td></tr>"
        f"<tr><th>Макс. итераций gate</th><td>{config.AI_MAX_ITERATIONS}</td></tr>"
        f"<tr><th>Уровень автономии</th><td>L{config.AUTONOMY_LEVEL}</td></tr>"
        "</table>"
    )
    return _page("Настройки", body)


# --------------------------------------------------------------------------- #
# API запуска задачи (auth)                                                   #
# --------------------------------------------------------------------------- #
@app.post("/api/task", dependencies=[Depends(require_auth)])
async def api_task(
    task: str = Form(...),
    project: str = Form(...),
    rel_path: str = Form(...),
    level: int = Form(1),
) -> Response:
    if project not in config.ALLOWED_PROJECTS:
        raise HTTPException(status_code=400, detail="project not allowed")

    task_id = await database.create_task(
        project=project, prompt=task, autonomy_level=level
    )
    await database.update_task_status(task_id, "running")

    try:
        sandbox.prepare_workspace(task_id, project)
        outcome: core.TaskOutcome = await asyncio.to_thread(
            core.solve_task,
            task_id,
            task,
            rel_path,
            model=config.AI_DEFAULT_MODEL,
            max_iterations=config.AI_MAX_ITERATIONS,
        )
    except Exception as exc:  # noqa: BLE001 — любую ошибку показываем в UI
        logger.exception("task %s failed", task_id)
        await database.add_step(
            task_id, 0, "error", output_summary=str(exc)
        )
        await database.update_task_status(task_id, "error", finished=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    for i, text in enumerate(outcome.steps, start=1):
        await database.add_step(task_id, i, "step", output_summary=text)
    if outcome.diff:
        await database.add_diff(task_id, rel_path, outcome.diff)
    if outcome.error:
        await database.add_step(
            task_id, len(outcome.steps) + 1, "error",
            output_summary=outcome.error,
        )
    await database.update_task_status(
        task_id,
        "done" if outcome.success else "gate_red",
        model_used=outcome.model,
        finished=True,
    )
    return RedirectResponse(f"/task/{task_id}", status_code=303)


def _run() -> None:  # pragma: no cover — точка входа для uvicorn/systemd
    import uvicorn

    logging.basicConfig(level=config.LOG_LEVEL)
    uvicorn.run(app, host=config.WEB_HOST, port=config.WEB_PORT)


if __name__ == "__main__":  # pragma: no cover
    _run()
