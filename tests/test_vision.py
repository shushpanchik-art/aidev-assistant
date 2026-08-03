"""Тесты vision-ветки core.describe_screenshots (моки gemini)."""
from __future__ import annotations

import os

os.environ.setdefault("WEB_AUTH_TOKEN", "test-token")

from agent import core  # noqa: E402
from ai import gemini  # noqa: E402


def _fake_result(text: str) -> gemini.GenResult:
    return gemini.GenResult(
        text=text, input_tokens=5, output_tokens=7, model="flash", via="api",
    )


def test_describe_empty_no_ai_call(monkeypatch):
    """Нет картинок -> пустая строка, gemini не дёргается."""
    def _boom(*a, **k):
        raise AssertionError("generate_multimodal не должен вызываться")

    monkeypatch.setattr(core.gemini, "generate_multimodal", _boom)
    assert core.describe_screenshots([], "задача") == ""


def test_describe_returns_text(monkeypatch):
    """Скрин -> модель описывает -> возвращаем очищенный текст."""
    captured = {}

    def _fake(prompt, images, **kwargs):
        captured["prompt"] = prompt
        captured["images"] = images
        captured["system"] = kwargs.get("system_instruction")
        return _fake_result("  На экране KeyError: 'x' в main.py:10  \n")

    monkeypatch.setattr(core.gemini, "generate_multimodal", _fake)

    imgs = [(b"PNGDATA", "image/png")]
    out = core.describe_screenshots(imgs, "почини ошибку")

    assert out == "На экране KeyError: 'x' в main.py:10"
    assert captured["images"] == imgs
    assert "почини ошибку" in captured["prompt"]
    assert captured["system"] == core.prompts.SYSTEM_DEVELOPER
