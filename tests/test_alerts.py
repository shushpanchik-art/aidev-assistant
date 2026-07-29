"""Тесты alerts.telegram (SPEC Этап 2): конфигурация, обрезка, отправка.

httpx замокан через MockTransport — реальных запросов к Telegram нет.
"""
from __future__ import annotations

import httpx
import pytest

import config
from alerts import telegram


def test_is_configured_false_by_default(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "")
    assert telegram.is_configured() is False


def test_is_configured_true(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "123")
    assert telegram.is_configured() is True


def test_truncate_short_unchanged():
    assert telegram._truncate("hi") == "hi"


def test_truncate_long():
    out = telegram._truncate("x" * 5000)
    assert len(out) <= telegram._MAX_LEN
    assert out.endswith("…(обрезано)")


def test_mask_removes_token(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "SECRET123")
    raw = "url https://api.telegram.org/botSECRET123/sendMessage failed"
    out = telegram._mask(raw)
    assert "SECRET123" not in out
    assert "***TOKEN***" in out


def test_mask_empty_token_noop(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "")
    assert telegram._mask("plain text") == "plain text"


@pytest.mark.asyncio
async def test_send_alert_error_text_has_no_token(monkeypatch):
    """AlertError не должен содержать токен даже при HTTP-ошибке."""
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "SECRET123")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "123")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400)

    transport = httpx.MockTransport(handler)
    orig = httpx.AsyncClient

    def fake_client(*args, **kwargs):
        kwargs["transport"] = transport
        return orig(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", fake_client)
    with pytest.raises(telegram.AlertError) as excinfo:
        await telegram.send_alert("hi", raise_on_error=True)
    assert "SECRET123" not in str(excinfo.value)


def test_format_crash():
    msg = telegram.format_crash("web.run_task", ValueError("boom"))
    assert "web.run_task" in msg
    assert "ValueError: boom" in msg


@pytest.mark.asyncio
async def test_send_alert_skips_when_not_configured(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "")
    assert await telegram.send_alert("hi") is False


@pytest.mark.asyncio
async def test_send_alert_success(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "123")

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    orig = httpx.AsyncClient

    def fake_client(*args, **kwargs):
        kwargs["transport"] = transport
        return orig(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", fake_client)
    assert await telegram.send_alert("hi") is True
    assert "sendMessage" in captured["url"]


@pytest.mark.asyncio
async def test_send_alert_http_error_returns_false(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "123")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    transport = httpx.MockTransport(handler)
    orig = httpx.AsyncClient

    def fake_client(*args, **kwargs):
        kwargs["transport"] = transport
        return orig(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", fake_client)
    assert await telegram.send_alert("hi") is False


@pytest.mark.asyncio
async def test_send_alert_raise_on_error(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "123")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    transport = httpx.MockTransport(handler)
    orig = httpx.AsyncClient

    def fake_client(*args, **kwargs):
        kwargs["transport"] = transport
        return orig(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", fake_client)
    with pytest.raises(telegram.AlertError):
        await telegram.send_alert("hi", raise_on_error=True)
