"""Отправка крэш-алертов в Telegram через Bot API (SPEC Этап 2).

Стек: httpx (async, уже в зависимостях). Только исходящие сообщения —
приём команд не нужен. Токен/чат берутся из config (из .env).
Если канал не настроен — тихо пропускаем (return False), не роняя приложение.
"""
from __future__ import annotations

import logging

import httpx

import config

logger = logging.getLogger(__name__)

_API_TEMPLATE = "https://api.telegram.org/bot{token}/sendMessage"
_TIMEOUT_S = 10.0
_MAX_LEN = 4096  # лимит Telegram на длину текста сообщения


class AlertError(RuntimeError):
    """Ошибка доставки алерта (сетевой сбой / отказ Telegram API)."""


def _truncate(text: str, limit: int = _MAX_LEN) -> str:
    """Обрезать текст до лимита Telegram, добавив маркер усечения."""
    if len(text) <= limit:
        return text
    marker = "\n…(обрезано)"
    return text[: limit - len(marker)] + marker


def is_configured() -> bool:
    """Настроен ли Telegram-канал (заданы токен и chat_id)."""
    return bool(config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID)


async def send_alert(text: str, *, raise_on_error: bool = False) -> bool:
    """Отправить сообщение в Telegram.

    Возвращает True при успехе, False если канал не настроен или произошла
    ошибка (и raise_on_error=False). При raise_on_error=True бросает AlertError.
    """
    if not is_configured():
        logger.debug("telegram alert skipped: channel not configured")
        return False

    url = _API_TEMPLATE.format(token=config.TELEGRAM_BOT_TOKEN)
    payload = {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": _truncate(text),
        "disable_web_page_preview": True,
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("telegram alert failed: %s", exc)
        if raise_on_error:
            raise AlertError(str(exc)) from exc
        return False
    return True


def format_crash(context: str, error: BaseException) -> str:
    """Собрать текст крэш-алерта: контекст + тип и текст исключения."""
    return f"🔴 AIDEV crash\nГде: {context}\n{type(error).__name__}: {error}"
