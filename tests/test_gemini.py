"""Тест generate_multimodal на моке клиента (без реальных AI-вызовов)."""
from __future__ import annotations

from ai import gemini


class _Meta:
    prompt_token_count = 100
    candidates_token_count = 40


class _Resp:
    text = "  это скриншот ошибки ImportError  "
    usage_metadata = _Meta()


class _Models:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def generate_content(self, *, model, contents, config):  # noqa: ANN001
        self.calls.append({"model": model, "contents": contents})
        return _Resp()


class _Client:
    def __init__(self) -> None:
        self.models = _Models()


def test_generate_multimodal_ok(monkeypatch):  # noqa: ANN001
    client = _Client()
    monkeypatch.setattr(gemini, "_clients", lambda: [("primary", client)])

    res = gemini.generate_multimodal(
        "что за ошибка на скрине?",
        images=[(b"\x89PNG_fake", "image/png")],
        model="flash",
    )

    assert res.text == "это скриншот ошибки ImportError"
    assert res.input_tokens == 100
    assert res.output_tokens == 40
    assert res.via == "primary"
    # проверяем, что собрали text-part + image-part
    contents = client.models.calls[0]["contents"]
    assert len(contents) == 2


def test_generate_multimodal_fallback(monkeypatch):  # noqa: ANN001
    class _BadClient:
        class models:  # noqa: N801
            @staticmethod
            def generate_content(**kwargs):  # noqa: ANN003
                raise RuntimeError("primary down")

    good = _Client()
    monkeypatch.setattr(
        gemini, "_clients",
        lambda: [("primary", _BadClient()), ("fallback", good)],
    )
    monkeypatch.setattr(gemini, "_call_with_retry", lambda fn, label: fn())

    res = gemini.generate_multimodal(
        "текст", images=[(b"img", "image/jpeg")]
    )
    assert res.via == "fallback"
    assert res.text == "это скриншот ошибки ImportError"
