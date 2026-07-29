# AIDEV Assistant — Техническое задание (SPEC v0.1)

> Приватный AI-помощник разработчика с веб-интерфейсом.
> Документ САМОДОСТАТОЧЕН: новый агент-разработчик ничего не знает о проекте,
> все данные подключения и контекст — здесь. Истина проекта = этот файл.

## 0. ПРАВИЛА БЕЗОПАСНОСТИ (обязательны)

Владелец — НЕ программист. Все шаги пошаговые, с проверкой. Файлы пишутся
через nano целиком или через Python-скрипт patch.py (без ручного
редактирования). После записи .py — `python3 -c "import ast; ast.parse(open('f.py').read())"`.

### Агенту КАТЕГОРИЧЕСКИ запрещено

- Читать/выводить `.env` любого проекта, приватные ключи, `~/.ssh/`.
- Писать в рабочие каталоги `/opt/SMOKI/bot`, `/opt/smoktolk/bot` напрямую.
- `systemctl restart|stop` рабочих сервисов `smoki-bot`, `smoktolk-bot`.
- `git push` в `main` любого репо; `git push --no-verify`.
- `rm -rf`, `DROP TABLE`, `chmod -R` вне `/opt/aidev/sandbox`.
- Отправлять код репозиториев на внешние хосты.

### Агенту разрешено (в песочнице)

- Читать код проектов в read-only снимке (БЕЗ `.env`, БЕЗ `*.db`).
- Писать/править файлы ТОЛЬКО в `/opt/aidev/sandbox/<task_id>/`.
- Запускать `ast.parse`, `ruff`, `mypy`, `bandit`, `pytest` в песочнице.
- Создавать ветку и (по уровню автономии) открывать PR через `gh`.
- `systemctl restart` ТОЛЬКО `aidev-sandbox-run.service`.

Реализация — allow-list команд (не blacklist). Прод меняется ТОЛЬКО через
PR → CI → ручной мерж владельцем.

## 1. НАЗНАЧЕНИЕ

Владелец через веб-UI (браузер, доступ по SSH-туннелю) ставит задачу
текстом → агент читает нужный код в изолированной песочнице, генерирует
правки/новые файлы, САМ прогоняет проверки (синтаксис, ruff, mypy, bandit,
pytest, smoke), показывает человеко-читаемое описание изменений и рисков →
владелец жмёт «Создать PR». Ничего не выкатывается в прод без подтверждения.

Многопроектный: обслуживает любой проект из allow-list (два бота + произвольный путь).

## 2. СЕРВЕР И ДОСТУП (ДАННЫЕ ПОДКЛЮЧЕНИЯ)

- ОС: Debian 12, Python 3.11.2 (системный).
- Сервер (SSH host alias): `telegram-bot`, внутренний IP `10.128.0.2`.
- Пользователь работы сейчас: `shushpanchik_art`.
- Новый служебный пользователь (Этап 0): `aidev` (по образцу `smoktolk`).
- Корень проекта: `/opt/aidev/`.
- RAM: ~969 МБ, свободно ~100 МБ (впритык!). Своп: 2 ГБ → увеличить до 4 ГБ (Этап 0).
- Своп-инструменты: `swapon` НЕ в PATH — использовать полный путь `/sbin/swapon`, `/sbin/mkswap`.
- Доступ к веб-UI: ТОЛЬКО через SSH-туннель с машины владельца (Mac):
  `ssh -N -L 8090:127.0.0.1:8090 shushpanchik_art@telegram-bot`
  (выполнять на МАКЕ, не на сервере). FastAPI слушает 127.0.0.1:8090, наружу НЕ публикуется.

## 3. AI (GEMINI) — ДАННЫЕ ПОДКЛЮЧЕНИЯ

Переиспользуем инфраструктуру проекта SMOKI (те же ключи/проект Google Cloud).

- Библиотека: `google-genai`.
- Основной канал: Vertex AI. `GOOGLE_GENAI_USE_VERTEXAI=true`,
  `GOOGLE_CLOUD_PROJECT=<из SMOKI .env>`, `GOOGLE_CLOUD_LOCATION=us-central1`,
  доступ через ADC/service account. Клиент: `genai.Client()` БЕЗ аргументов.
- Резерв: AI Studio по ключу `GEMINI_API_KEY_FALLBACK` (`genai.Client(api_key=...)`).
  `GEMINI_API_KEY` отсутствует — это норма.
- Референс-обёртка (копировать логику): `/opt/SMOKI/bot/ai/gemini.py`
  функции: `get_client()`, `get_fallback_client()`,
  `generate_text(prompt, *, temperature=0.9, ..., use_search)`, `generate_image(prompt)`.
  Автопереключение primary→fallback уже реализовано там — форкнуть в `/opt/aidev/ai/gemini.py`.
- Промпт агента (`/opt/aidev/ai/prompts.py`): НОВЫЙ системный промпт «senior Python-разработчик»,
  а не автор канала. Обязательства: код проходит ruff/mypy/bandit/pytest; без секретов;
  объяснять изменения/риски простым языком; работать в песочнице.

### Модели и стоимость (факт разведки 2025)

- Flash `gemini-2.5-flash`: $0.30 вход / $2.50 выход за 1M токенов. DEFAULT.
- Pro `gemini-2.5-pro`: $1.25 / $10 (до 200K), при >200K $2.50 / $15.
- Batch API даёт -50%, но 24ч задержка — НЕ подходит (сценарий интерактивный).
- Реальный бюджет: ~$1–5/мес на Flash, +$5–20 при активном Pro. $300 кредитов на 90 дней.

## 4. ГИБРИДНЫЙ РЕЖИМ FLASH/PRO С САМОЗАПРОСОМ

По умолчанию Flash. Перед задачей — дешёвый Flash-запрос «оценка сложности».
Если сложно ИЛИ Flash провалил N итераций gate → агент НЕ переключается молча,
а спрашивает в UI: «Задача сложная (причина…). Рекомендую Pro, оценка $0.X.
Переключить? [Да/Нет]». Политика: `AI_ESCALATION=never|ask|auto` (default ask).
Дневной лимит `AI_DAILY_BUDGET_USD` — при превышении Pro блокируется.
Каждый вызов пишется в таблицу `ai_usage`.

## 5. ПЕСОЧНИЦА (SANDBOX)

Три слоя изоляции:

1. Файловый: агент работает в `/opt/aidev/sandbox/<task_id>/`. Код проекта
   попадает туда через `git clone --local`/`git worktree` из ЗЕРКАЛА
   `/opt/aidev/mirrors/<project>.git` (fetch-only, не пушит обратно).
   `.env`, `*.db`, `logs/`, `media/` ИСКЛЮЧАЮТСЯ из снимка.
2. Права ОС: всё `aidev:aidev`, нет прав записи в прод-каталоги.
   sudo для `aidev` — только `systemctl * aidev-sandbox-run.service`.
3. Executor allow-list: единственная точка запуска команд
   (`/opt/aidev/agent/executor.py`). Не в списке / путь вне sandbox → отказ + лог.

### Gate (барьер перед «Создать PR»)

1. `ast.parse` каждого `.py`; 2. `ruff check`; 3. `mypy`; 4. `bandit -ll`;
5. `pytest` (затронутые → полный); 6. smoke-импорт модулей.
Красный gate → правка НЕ предлагается, агент чинит сам (до `AI_MAX_ITERATIONS`).

## 6. УРОВНИ АВТОНОМИИ

- L0 read-only — только советует.
- L1 sandbox-write (DEFAULT) — пишет в песочницу, gate, показывает риски. Выход по кнопке.
- L2 auto-PR — при зелёном gate сам создаёт ветку+PR; мерж — владелец.
- L3 auto-deploy-sandbox — катит в тестовый `aidev-sandbox-run` (не прод).

Прод-мерж и рестарт прод-сервисов НЕ автоматизируются никогда.

## 7. АРХИТЕКТУРА И ФАЙЛЫ

```text
/opt/aidev/
  web/app.py          FastAPI: UI + API, auth по WEB_AUTH_TOKEN
  agent/core.py       план → контекст → escalation → генерация → gate → фикс
  agent/executor.py   allow-list, subprocess в sandbox, ресурс-гвард (MEM_MIN_AVAIL_MB)
  ai/gemini.py        форк обёртки SMOKI
  ai/prompts.py       системный промпт разработчика
  db/schema.sql       tasks, task_steps, ai_usage, diffs, gate_runs, settings
  db/database.py      CRUD
  sandbox/            рабочие копии задач (в .gitignore)
  mirrors/            зеркала репо (fetch-only)
  docs/SPEC.md        этот файл (истина)
  tests/              test_executor, test_sandbox, test_agent_core, test_gate,
                      test_cost, test_web, test_schema, test_import, test_secrets
  scripts/            служебные (setup swap, create user)
  .env / .env.example
  requirements.txt
  venv/               /opt/aidev/venv/bin/python

Стек: FastAPI + uvicorn (веб, ~50 МБ), HTMX/чистый JS (без Node), SQLite+aiosqlite,
google-genai, ruff/mypy/bandit/pytest, python-dotenv, systemd.
```

## 8. ВЕБ-UI (без дифф-вьюера)

Владелец — не программист, код читает на GitHub в PR. UI даёт РЕШЕНИЕ на человеческом языке.

Экраны:

Новая задача: текст + выбор проекта (allow-list) + уровень L0–L3 + индикатор модели.
Живой лог работы агента (шаги с ✓/✗) + всплывающий вопрос про Pro.
Результат: три блока —
📝 Что сделано (простым языком);
⚠️ Риски (сколько файлов, обратимо ли, результат тестов);
✅ Проверки (ruff/mypy/bandit/pytest/smoke);
Кнопки [Создать PR] / [Доработать] / [Отклонить].
История задач (статус, проект, модель, стоимость, ссылка на PR).
Настройки (модель по умолчанию, escalation, бюджет, уровень).
## 9. КОНФИГУРАЦИЯ .env

WEB_HOST=127.0.0.1
WEB_PORT=8090
WEB_AUTH_TOKEN=<openssl rand -hex 24>
GOOGLE_GENAI_USE_VERTEXAI=true
GOOGLE_CLOUD_PROJECT=<из SMOKI>
GOOGLE_CLOUD_LOCATION=us-central1
GEMINI_API_KEY_FALLBACK=<из SMOKI>
AI_TEXT_MODEL_FLASH=gemini-2.5-flash
AI_TEXT_MODEL_PRO=gemini-2.5-pro
AI_DEFAULT_MODEL=flash
AI_ESCALATION=ask
AI_DAILY_BUDGET_USD=3.0
AI_MAX_ITERATIONS=4
SANDBOX_DIR=/opt/aidev/sandbox
MIRRORS_DIR=/opt/aidev/mirrors
AUTONOMY_LEVEL=1
MEM_MIN_AVAIL_MB=120
ALLOWED_PROJECTS=/opt/SMOKI/bot,/opt/smoktolk/bot
DB_PATH=/opt/aidev/aidev.db
LOG_LEVEL=INFO

.env НЕ в git (права 600, владелец aidev). .env.example — в git, без значений.
Тест test_env_example: .env.example содержит все ключи config.

## 10. ОБСЛУЖИВАЕМЫЕ ПРОЕКТЫ (КОНТЕКСТ ДЛЯ АГЕНТА)

smoktolk (лоялти-бот)

Путь: /opt/smoktolk/bot, venv /opt/smoktolk/bot/venv/bin/python.
systemd smoktolk-bot.service. aiogram 3.29.1, SQLite(aiosqlite), aiohttp webhook (WEBHOOK_PORT), handlers/*.py → ROUTERS.
Упр: systemctl restart|status smoktolk-bot; journalctl -u smoktolk-bot -n50.
Файлы: bot.py, config.py, catalog.py, db.py, keyboards.py, handlers/, services/, middlewares/, webhook.py. Истина: SPEC.md, CONTRIBUTING.md, ONBOARDING_AI.md.
SMOKI (контент-бот канала @SMOKTOLK)

Путь: /opt/SMOKI/bot, venv /opt/SMOKI/bot/venv/bin/python.
systemd smoki-bot (Restart=always), polling, WEBHOOK_PORT=8082 резерв, Py3.11.2.
Канал @SMOKTOLK, обсуждения DISCUSSION_GROUP_ID.
aiogram 3.29.1/3.30.0, aiosqlite smoki.db, apscheduler, dotenv.
AI google-genai Vertex (см. §3). Модели TEXT=gemini-2.5-flash, IMAGE=gemini-2.5-flash-image. Search grounding только для тела статьи: generate_text(use_search=True).
Структура: bot.py, config.py, scheduler.py, db/schema.sql+database.py (published_topics, articles, comments, ai_logs, settings, story_jobs), ai/gemini.py+prompts.py, services/content.py+publisher.py+comments.py+stories.py+ schedule_view.py+schedule_control.py+story_render.py, handlers/admin.py+group.py+ init→ROUTERS, scripts/ai_healthcheck.py+heartbeat_healthcheck.sh, userbot.py.
scheduler джобы: SPEC разделы U8/U8.2 + scheduler.py(add_job id=), schedule_control (PAUSABLE_JOBS, TIME_EDITABLE); внешний smoki-heartbeat.timer грепает свежесть, порог HEARTBEAT_MAX_AGE_HOURS=8, алерт админу при зависании.
Упр: systemctl restart|status smoki-bot; journalctl -u smoki-bot -n50. sudoers /etc/sudoers.d/smoki-bot restart/start/stop/status; sudo visudo -c.
GitHub: git@github.com:shushpanchik-art/smoki-bot.git. Истина: docs/SPEC.md.
## 11. CI (свой репо aidev-assistant, private)

Наследуем культуру ботов. Джобы (MVP-минимум → полный набор из 10):
syntax, ruff 0.15.12, bandit 1.9.4 -ll, pytest+cov (fail<45), mypy 1.14.1, gitleaks.
Затем добавить: pip-audit, codespell, yamllint 1.35.1, markdownlint.
main protected, pre-push hook gitleaks. Версии ruff/mypy в venv = как в CI.

## 12. GIT FLOW

main protected, прямых коммитов нет. Ветка feature|fix|chore|docs →
add/commit/push → gh pr create → CI зелёный → merge UI → main pull → branch -d.
Перед push: venv/bin/ruff check <файлы> && venv/bin/python -m mypy <файлы>.
MD: markdownlint, MD012 ≤1 пустая строка, без хвостов, финальный \n.
GitHub аккаунт: shushpanchik-art. Репо: aidev-assistant (создать).
gh для aidev — отдельный deploy key (не переиспользовать ключи ботов).

## 13. ТЕСТЫ (tests/)

AI-вызовы ТОЛЬКО моки. Ключевые:

test_executor — allow-list блокирует rm/curl/выход-из-sandbox/sudo-на-прод; разрешает ruff/pytest в песочнице (САМЫЙ ВАЖНЫЙ).
test_sandbox — снимок без .env/*.db; правки не касаются оригинала.
test_agent_core — план/escalation/фикс-цикл на моках.
test_gate — красный ruff/pytest → правка НЕ применяется.
test_cost — учёт токенов, дневной бюджет, блок Pro при превышении.
test_web — эндпоинты FastAPI, 401 без токена.
test_schema, test_import, test_secrets — как в SMOKI.
conftest.py: tmp_db + init_db; заглушки токенов для CI без .env.

## 14. SYSTEMD

aidev.service — веб-агент (uvicorn). User=aidev, Restart=always, MemoryMax=400M (защита ботов от OOM), 127.0.0.1:8090.
aidev-sandbox-run.service — тестовый запуск кода (L3), отдельный порт. Единственный сервис, который агенту разрешено рестартовать.
## 15. RAM / СВОП (блокирующее предусловие)

969 МБ RAM, ~100 МБ свободно — впритык. Этап 0: своп 2→4 ГБ
(fallocate/dd + /sbin/mkswap + /sbin/swapon). aidev.service с MemoryMax=400M.
pytest — последовательно (-x, -p no:cacheprovider). При available<MEM_MIN_AVAIL_MB
executor откладывает pytest и предупреждает в UI.
Будущее: апгрейд VM до 2 ГБ (~$7–10/мес) снимет ограничения.

## 16. ПЛАН ВНЕДРЕНИЯ

Этап 0 — подготовка сервера

0.1 Своп 2→4 ГБ (полные пути /sbin/*).
0.2 Пользователь aidev + каталоги /opt/aidev/{sandbox,mirrors}.
0.3 sudoers: aidev только systemctl * aidev-sandbox-run.service.
0.4 venv + requirements. ✅ (main 62beff3)
Этап 1 — MVP

1.1 Форк ai/gemini.py + системный промпт разработчика. ✅ (gemini.py 62beff3; ai/prompts.py: SYSTEM_DEVELOPER + 6 билдеров, tests/test_prompts.py 9 passed)
1.2 Executor (allow-list) + песочница (snapshot без секретов).
1.3 Agent Core (план→генерация→gate→фикс-цикл).
1.4 FastAPI: 3 экрана + auth-токен. ✅ (web/app.py: auth Bearer/cookie по WEB_AUTH_TOKEN, экраны новая-задача/история/задача/настройки, POST /api/task → prepare_workspace + solve_task в потоке → шаги/diff в БД; tests/test_web.py 8 passed — 401 без токена, healthz без auth, мок solve_task, отказ чужого проекта)
1.5 SQLite (tasks/ai_usage/gate_runs). ✅ (schema.sql + database.py CRUD + tests/test_schema.py, 6 passed)
1.6 Кнопка «Создать PR» через gh от aidev. ✅ (PR #14, a77e06f: web POST /api/task/{id}/pr + кнопка на странице задачи; sandbox.create_pull_request — ветка aidev/task-<id>, push, gh pr create; db branch/pr_url + set_task_pr + миграция; tests/test_web.py 75 passed)
1.7 systemd + тесты executor/sandbox/gate. ✅ (scripts/aidev.service — uvicorn web.app:app, User=aidev, MemoryMax=400M, порт 8090; scripts/aidev-sandbox-run.service — L3 test-run порт 8091 (заглушка до Этапа 2); scripts/README.md — установка; тесты executor/sandbox/gate 48 passed)

Этап 2 (за рамками MVP)

Telegram-бот как альтернативный интерфейс (отдельный бот у @BotFather).
L3 auto-deploy в тестовый инстанс.
Antigravity CLI как альтернативный движок (headless-совместим по факту разведки; тестовая установка + замер RAM + проверка что использует наши ключи).
Крэш-алерты в Telegram, дневные сводки стоимости.
## 17. ROADMAP / ОТКРЫТЫЕ ВОПРОСЫ
