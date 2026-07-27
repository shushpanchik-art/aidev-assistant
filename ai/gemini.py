"""Обёртка над Google GenAI SDK: Vertex AI (primary) + AI Studio (fallback).

Форк из SMOKI/bot/ai/gemini.py. Отличия:
- generate_text принимает model= (flash|pro|полное имя) — выбор на лету;
- возвращает GenResult с текстом и токенами (для учёта бюджета);
- нет генерации картинок (в ассистенте не нужна).
"""
import logging
import time
from dataclasses import dataclass

import config  # noqa: F401 — выполняет load_dotenv() до создания клиента
from google import genai
from google.genai import errors, types

logger = logging.getLogger(__name__)

_client: genai.Client | None = None
_fallback: genai.Client | None = None

_MAX_ATTEMPTS = 3
_BASE_DELAY = 1.0


@dataclass
class GenResult:
    """Результат генерации: текст, использованные токены, имя модели, клиент."""

    text: str
    input_tokens: int
    output_tokens: int
    model: str
    via: str  # primary|fallback


def get_client() -> genai.Client:
    """Ленивая инициализация основного клиента (Vertex из окружения)."""
    global _client
    if _client is None:
        _client = genai.Client()
        logger.info(
            "GenAI client создан (Vertex=%s, project=%s, location=%s)",
            config.GOOGLE_GENAI_USE_VERTEXAI,
            config.GOOGLE_CLOUD_PROJECT,
            config.GOOGLE_CLOUD_LOCATION,
        )
    return _client


def get_fallback_client() -> genai.Client | None:
    """Резервный клиент AI Studio по ключу GEMINI_API_KEY_FALLBACK."""
    global _fallback
    if not config.GEMINI_API_KEY_FALLBACK:
        return None
    if _fallback is None:
        _fallback = genai.Client(api_key=config.GEMINI_API_KEY_FALLBACK)
    return _fallback


def _is_transient(err: Exception) -> bool:
    """5xx или 429 — стоит повторить; прочее — нет."""
    if isinstance(err, errors.ServerError):
        return True
    if isinstance(err, errors.ClientError):
        return getattr(err, "code", None) == 429
    return False


def _call_with_retry(fn, label):
    """Повторяет вызов при транзиентных ошибках с экспопаузой."""
    last: Exception | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            last = e
            if not _is_transient(e) or attempt == _MAX_ATTEMPTS:
                raise
            delay = _BASE_DELAY * (2 ** (attempt - 1))
            logger.warning(
                "%s: транзиентная ошибка (попытка %d/%d), пауза %.1fs: %s",
                label, attempt, _MAX_ATTEMPTS, delay, e,
            )
            time.sleep(delay)
    if last:
        raise last
    raise RuntimeError("unreachable")


def _clients() -> list[tuple[str, genai.Client]]:
    out: list[tuple[str, genai.Client]] = [("primary", get_client())]
    fb = get_fallback_client()
    if fb is not None:
        out.append(("fallback", fb))
    return out


def _resolve_model(model: str) -> str:
    """flash|pro -> полное имя; иначе считаем, что уже полное имя."""
    if model in ("flash", "pro"):
        return config.model_name(model)
    return model


def _usage(resp) -> tuple[int, int]:
    """Достаёт (input_tokens, output_tokens) из ответа, безопасно."""
    meta = getattr(resp, "usage_metadata", None)
    if meta is None:
        return 0, 0
    return (
        getattr(meta, "prompt_token_count", 0) or 0,
        getattr(meta, "candidates_token_count", 0) or 0,
    )


def generate_text(
    prompt: str,
    *,
    model: str = "flash",
    temperature: float = 0.4,
    max_output_tokens: int = 8192,
    use_search: bool = False,
    system_instruction: str | None = None,
) -> GenResult:
    """Генерация текста с автопереключением primary -> fallback.

    model: 'flash' | 'pro' | полное имя модели.
    use_search=True подключает Google Search grounding.
    Возвращает GenResult (текст + токены + метаданные).
    """
    model_full = _resolve_model(model)
    tools: types.ToolListUnion | None = (
        [types.Tool(google_search=types.GoogleSearch())] if use_search else None
    )
    last_err: Exception | None = None
    for name, client in _clients():
        try:
            resp = _call_with_retry(
                lambda c=client: c.models.generate_content(
                    model=model_full,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=temperature,
                        max_output_tokens=max_output_tokens,
                        tools=tools,
                        system_instruction=system_instruction,
                    ),
                ),
                f"generate_text[{name}]",
            )
            if name == "fallback":
                logger.warning("generate_text: использован резервный ключ")
            in_tok, out_tok = _usage(resp)
            return GenResult(
                text=(resp.text or "").strip(),
                input_tokens=in_tok,
                output_tokens=out_tok,
                model=model_full,
                via=name,
            )
        except Exception as e:  # noqa: BLE001
            last_err = e
            logger.warning("generate_text через %s не удалось: %s", name, e)
    if last_err:
        raise last_err
    return GenResult(text="", input_tokens=0, output_tokens=0, model=model_full, via="none")
