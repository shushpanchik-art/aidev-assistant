"""Read-only обзор проекта в песочнице для автономного агента.

Даёт модели структуру проекта и содержимое выбранных файлов, чтобы она
САМА решила, какие файлы читать и править. Все пути идут через
sandbox._resolve_in_task — побег из песочницы невозможен by design.

RAM 969 МБ: жёсткие лимиты на число файлов и размер чтения.
"""
from __future__ import annotations


from agent import sandbox

# Что показываем модели (исходники + доки/конфиги, без бинарей и мусора).
_TREE_EXTS = {".py", ".md", ".txt", ".toml", ".cfg", ".ini", ".yaml", ".yml", ".sql", ".json"}
_SKIP_DIRS = {".git", "venv", ".venv", "__pycache__", "node_modules", ".mypy_cache", ".ruff_cache", ".pytest_cache"}

_MAX_TREE_FILES = 400        # предел файлов в дереве (защита контекста/RAM)
_MAX_FILE_BYTES = 60_000     # предел чтения одного файла
_MAX_READ_FILES = 20         # предел числа файлов за один read_many


def project_tree(task_id: int) -> str:
    """Плоский список относительных путей файлов проекта (для промпта).

    Пропускает venv/.git/pycache и бинарные расширения. Обрезается по
    _MAX_TREE_FILES, чтобы не раздувать контекст модели и не жрать RAM.
    """
    base = sandbox.task_dir(task_id)
    if not base.is_dir():
        return "(пусто: рабочая копия не готова)"
    rows: list[str] = []
    for p in sorted(base.rglob("*")):
        rel = p.relative_to(base)
        if any(part in _SKIP_DIRS for part in rel.parts):
            continue
        if not p.is_file():
            continue
        if p.suffix.lower() not in _TREE_EXTS:
            continue
        try:
            size = p.stat().st_size
        except OSError:
            size = 0
        rows.append(f"{rel}  ({size} B)")
        if len(rows) >= _MAX_TREE_FILES:
            rows.append(f"... (обрезано на {_MAX_TREE_FILES} файлах)")
            break
    if not rows:
        return "(нет исходных файлов для показа)"
    return "\n".join(rows)


def read_many(task_id: int, rel_paths: list[str]) -> dict[str, str]:
    """Прочитать несколько файлов проекта (read-only, с лимитами).

    Возвращает {rel_path: содержимое}. Ошибки чтения/побега кладутся
    как "(ошибка: ...)" — агент увидит проблему, но процесс не падает.
    Число файлов режется _MAX_READ_FILES, размер — _MAX_FILE_BYTES.
    """
    out: dict[str, str] = {}
    for rel in rel_paths[:_MAX_READ_FILES]:
        rel = rel.strip()
        if not rel:
            continue
        try:
            target = sandbox._resolve_in_task(task_id, rel)
            if not target.is_file():
                out[rel] = "(ошибка: файл не найден)"
                continue
            data = target.read_bytes()[:_MAX_FILE_BYTES]
            text = data.decode("utf-8", errors="replace")
            if target.stat().st_size > _MAX_FILE_BYTES:
                text += "\n... (файл обрезан по лимиту)"
            out[rel] = text
        except sandbox.SandboxError as exc:
            out[rel] = f"(ошибка доступа: {exc})"
        except OSError as exc:
            out[rel] = f"(ошибка чтения: {exc})"
    return out


def files_block(files: dict[str, str]) -> str:
    """Собрать словарь файлов в текстовый блок для промпта модели."""
    parts: list[str] = []
    for rel, content in files.items():
        parts.append(f"=== FILE: {rel} ===\n{content}")
    return "\n\n".join(parts)
