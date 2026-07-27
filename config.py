"""Конфигурация AIDEV Assistant. Читает .env, даёт типизированные константы."""
import os

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    val = os.getenv(name)
    try:
        return int(val) if val else default
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    val = os.getenv(name)
    try:
        return float(val) if val else default
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    val = os.getenv(name, "").strip().lower()
    if not val:
        return default
    return val in ("1", "true", "yes", "on")


def _list(name: str, default: str = "") -> list[str]:
    raw = os.getenv(name, default)
    return [p.strip() for p in raw.split(",") if p.strip()]


# --- Web ---
WEB_HOST = os.getenv("WEB_HOST", "127.0.0.1")
WEB_PORT = _int("WEB_PORT", 8090)
WEB_AUTH_TOKEN = os.getenv("WEB_AUTH_TOKEN", "")

# --- AI (Gemini) ---
GOOGLE_GENAI_USE_VERTEXAI = os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "false")
GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "")
GOOGLE_CLOUD_LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
GEMINI_API_KEY_FALLBACK = os.getenv("GEMINI_API_KEY_FALLBACK", "")

AI_TEXT_MODEL_FLASH = os.getenv("AI_TEXT_MODEL_FLASH", "gemini-2.5-flash")
AI_TEXT_MODEL_PRO = os.getenv("AI_TEXT_MODEL_PRO", "gemini-2.5-pro")
AI_DEFAULT_MODEL = os.getenv("AI_DEFAULT_MODEL", "flash")  # flash|pro
AI_ESCALATION = os.getenv("AI_ESCALATION", "ask")  # never|ask|auto
AI_DAILY_BUDGET_USD = _float("AI_DAILY_BUDGET_USD", 3.0)
AI_MAX_ITERATIONS = _int("AI_MAX_ITERATIONS", 4)

# Цены за 1M токенов (для оценки стоимости; из разведки)
PRICE_FLASH_IN = _float("PRICE_FLASH_IN", 0.30)
PRICE_FLASH_OUT = _float("PRICE_FLASH_OUT", 2.50)
PRICE_PRO_IN = _float("PRICE_PRO_IN", 1.25)
PRICE_PRO_OUT = _float("PRICE_PRO_OUT", 10.0)

# --- Sandbox ---
SANDBOX_DIR = os.getenv("SANDBOX_DIR", "/opt/aidev/sandbox")
MIRRORS_DIR = os.getenv("MIRRORS_DIR", "/opt/aidev/mirrors")
AUTONOMY_LEVEL = _int("AUTONOMY_LEVEL", 1)  # 0|1|2|3
MEM_MIN_AVAIL_MB = _int("MEM_MIN_AVAIL_MB", 120)

# --- Projects (allow-list путей) ---
ALLOWED_PROJECTS = _list("ALLOWED_PROJECTS", "/opt/SMOKI/bot,/opt/smoktolk/bot")

# --- DB / логи ---
DB_PATH = os.getenv("DB_PATH", "/opt/aidev/aidev.db")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")


def model_name(kind: str) -> str:
    """Вернуть имя модели по ключу 'flash'|'pro'."""
    return AI_TEXT_MODEL_PRO if kind == "pro" else AI_TEXT_MODEL_FLASH
