"""Executor: единственная точка запуска команд в песочнице.

Принципы безопасности (SPEC §5.3, §13):
- allow-list разрешённых бинарей (НЕ blacklist);
- cwd строго внутри SANDBOX_DIR/<task_id>, побег через ../ невозможен;
- shell=False всегда, минимальное окружение;
- memory-guard: pytest откладывается при нехватке RAM (§15).
Всё, чего нет в allow-list или вне песочницы, -> ExecutorError.
"""
from __future__ import annotations

import os
import subprocess  # nosec B404 - запуск строго по allow-list, shell=False
from dataclasses import dataclass
from pathlib import Path

import config

# Бинари инструментов берём ТОЛЬКО из нашего venv (нельзя подменить через PATH).
_VENV_BIN = Path(__file__).resolve().parent.parent / "venv" / "bin"

# allow-list: имя команды -> абсолютный путь к бинарю.
ALLOWED_COMMANDS: dict[str, str] = {
    "python": str(_VENV_BIN / "python"),
    "ruff": str(_VENV_BIN / "ruff"),
    "mypy": str(_VENV_BIN / "mypy"),
    "bandit": str(_VENV_BIN / "bandit"),
    "pytest": str(_VENV_BIN / "pytest"),
}

# Команды, требующие много памяти (проверяются memory-guard'ом).
_MEMORY_HEAVY = {"pytest"}

# Таймаут по умолчанию на любую команду, сек.
DEFAULT_TIMEOUT = 300


class ExecutorError(Exception):
    """Команда отклонена политикой безопасности executor'а."""


@dataclass
class RunResult:
    """Результат выполнения команды."""

    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def sandbox_root() -> Path:
    """Абсолютный канонический путь корня песочницы."""
    return Path(config.SANDBOX_DIR).resolve()


def task_dir(task_id: int | str) -> Path:
    """Каталог конкретной задачи внутри песочницы."""
    return sandbox_root() / str(task_id)


def is_within_sandbox(path: str | os.PathLike[str]) -> bool:
    """True, если path (после разрешения симлинков/..) лежит внутри песочницы."""
    root = sandbox_root()
    try:
        target = Path(path).resolve()
    except (OSError, RuntimeError):
        return False
    return target == root or root in target.parents


def available_memory_mb() -> int:
    """Свободная память (MemAvailable) в МБ из /proc/meminfo; 0 при ошибке."""
    try:
        with open("/proc/meminfo", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    kb = int(line.split()[1])
                    return kb // 1024
    except (OSError, ValueError, IndexError):
        return 0
    return 0


def _minimal_env() -> dict[str, str]:
    """Минимальное окружение для subprocess (без утечки лишнего)."""
    keep = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env.setdefault("PATH", "/usr/bin:/bin")
    return env


def run(
    argv: list[str],
    *,
    task_id: int | str,
    cwd: str | os.PathLike[str] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> RunResult:
    """Запустить разрешённую команду внутри каталога задачи.

    argv[0] -- имя команды из ALLOWED_COMMANDS (не абсолютный путь).
    cwd по умолчанию -- каталог задачи; если задан, обязан быть внутри песочницы.
    Любое нарушение -> ExecutorError (запуск не производится).
    """
    if not argv:
        raise ExecutorError("Пустая команда")

    name = argv[0]
    if name not in ALLOWED_COMMANDS:
        raise ExecutorError(f"Команда не в allow-list: {name!r}")

    workdir = Path(cwd) if cwd is not None else task_dir(task_id)
    if not is_within_sandbox(workdir):
        raise ExecutorError(f"cwd вне песочницы: {workdir}")

    if name in _MEMORY_HEAVY:
        avail = available_memory_mb()
        if avail < config.MEM_MIN_AVAIL_MB:
            raise ExecutorError(
                f"Мало памяти для {name}: доступно {avail} МБ "
                f"< порога {config.MEM_MIN_AVAIL_MB} МБ. Отложено."
            )

    resolved = [ALLOWED_COMMANDS[name], *argv[1:]]
    try:
        proc = subprocess.run(  # nosec B603 - argv из allow-list, shell=False
            resolved,
            cwd=str(workdir),
            timeout=timeout,
            capture_output=True,
            text=True,
            shell=False,
            env=_minimal_env(),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ExecutorError(f"Таймаут {timeout}s для {name}") from exc
    except (OSError, ValueError) as exc:
        raise ExecutorError(f"Ошибка запуска {name}: {exc}") from exc

    return RunResult(proc.returncode, proc.stdout, proc.stderr)
