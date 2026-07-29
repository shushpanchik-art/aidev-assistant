"""Sandbox manager (SPEC §5, п.1.3).

Отвечает за изоляцию:
1. Зеркало проекта  -> MIRRORS_DIR/<project>.git  (git --mirror, fetch-only).
2. Рабочая копия    -> SANDBOX_DIR/<task_id>/      (git clone --local).
3. Снимок БЕЗ секретов: .env / *.db / venv / logs удаляются из копии.
4. Правки НЕ касаются оригинала (работаем только в песочнице).
5. collect_diff -> unified diff по git.

Git-команды здесь запускаются НАПРЯМУЮ: это доверенный оркестратор AIDEV,
а не сгенерированный агентом код. Код-под-проверкой всё равно ходит только
через executor.py (allow-list). Все пути жёстко валидируются.
"""
from __future__ import annotations

import re
import shutil
import subprocess  # nosec B404 -- git оркестратора, args фиксированы, shell=False
from dataclasses import dataclass
from pathlib import Path

import config

# Что вычищаем из снимка (секреты и мусор) -- SPEC:224.
_STRIP_NAMES = {".env", "venv", ".venv", "logs", "__pycache__"}
_STRIP_GLOBS = ("*.db", "*.sqlite", "*.sqlite3", "*.log", ".env.*")

_PROJECT_RE = re.compile(r"[^A-Za-z0-9_.-]")


class SandboxError(RuntimeError):
    """Нарушение изоляции песочницы или ошибка git-операции."""


@dataclass(frozen=True)
class GitResult:
    returncode: int
    stdout: str
    stderr: str


def sandbox_root() -> Path:
    return Path(config.SANDBOX_DIR).resolve()


def mirrors_root() -> Path:
    return Path(config.MIRRORS_DIR).resolve()


def _project_slug(project_path: str) -> str:
    """Имя зеркала из пути проекта: /opt/SMOKI/bot -> opt_SMOKI_bot."""
    p = project_path.strip("/").replace("/", "_")
    return _PROJECT_RE.sub("_", p) or "project"


def _assert_allowed_project(project_path: str) -> Path:
    src = Path(project_path).resolve()
    allowed = [Path(a).resolve() for a in config.ALLOWED_PROJECTS]
    if src not in allowed:
        raise SandboxError(
            f"Проект '{project_path}' не в ALLOWED_PROJECTS: "
            f"{[str(a) for a in allowed]}"
        )
    if not src.is_dir():
        raise SandboxError(f"Проект не найден: {src}")
    return src


def task_dir(task_id: int) -> Path:
    return (sandbox_root() / str(int(task_id))).resolve()


def _assert_within_sandbox(path: Path) -> Path:
    """Путь обязан лежать внутри SANDBOX_DIR (защита от побега)."""
    resolved = Path(path).resolve()
    root = sandbox_root()
    if resolved != root and root not in resolved.parents:
        raise SandboxError(f"Путь '{path}' вне песочницы {root}")
    return resolved


def _git(args: list[str], *, cwd: Path) -> GitResult:
    """Запуск git напрямую (доверенный код), shell=False, фикс-аргументы."""
    proc = subprocess.run(  # nosec B603 B607 -- git, shell=False, args из кода
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    return GitResult(proc.returncode, proc.stdout, proc.stderr)


def _gh(args: list[str], *, cwd: Path) -> GitResult:
    """Запуск gh напрямую (доверенный оркестратор), shell=False."""
    proc = subprocess.run(  # nosec B603 B607 -- gh, shell=False, args из кода
        ["gh", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )
    return GitResult(proc.returncode, proc.stdout, proc.stderr)


def _github_repo(origin_url: str) -> str:
    """owner/repo из git@github.com:owner/repo.git или https-URL."""
    m = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$", origin_url.strip())
    if not m:
        raise SandboxError(f"Не github origin: {origin_url!r}")
    return m.group(1)


# --- зеркало --------------------------------------------------------------

def mirror_path(project_path: str) -> Path:
    return (mirrors_root() / f"{_project_slug(project_path)}.git").resolve()


def create_mirror(project_path: str) -> Path:
    """Создать/обновить bare-зеркало проекта (fetch-only). Идемпотентно."""
    src = _assert_allowed_project(project_path)
    mirrors_root().mkdir(parents=True, exist_ok=True)
    mp = mirror_path(project_path)
    if mp.exists():
        res = _git(["remote", "update", "--prune"], cwd=mp)
        if res.returncode != 0:
            raise SandboxError(f"git remote update: {res.stderr.strip()}")
        return mp
    res = _git(["clone", "--mirror", str(src), str(mp)], cwd=mirrors_root())
    if res.returncode != 0:
        raise SandboxError(f"git clone --mirror: {res.stderr.strip()}")
    return mp


# --- рабочая копия --------------------------------------------------------

def _strip_secrets(root: Path) -> None:
    """Убрать секреты/мусор из снимка (SPEC:224)."""
    for name in _STRIP_NAMES:
        target = root / name
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        elif target.exists():
            target.unlink(missing_ok=True)
    for pattern in _STRIP_GLOBS:
        for f in root.rglob(pattern):
            try:
                if f.is_file():
                    f.unlink()
            except OSError:
                pass


def prepare_workspace(task_id: int, project_path: str) -> Path:
    """Развернуть рабочую копию проекта в sandbox/<task_id>/ без секретов."""
    create_mirror(project_path)
    mp = mirror_path(project_path)
    dest = _assert_within_sandbox(task_dir(task_id))
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    sandbox_root().mkdir(parents=True, exist_ok=True)
    res = _git(["clone", "--local", str(mp), str(dest)], cwd=sandbox_root())
    if res.returncode != 0:
        raise SandboxError(f"git clone --local: {res.stderr.strip()}")
    _strip_secrets(dest)
    # фиксируем «чистое» состояние снимка, чтобы diff показывал только правки агента
    _git(["add", "-A"], cwd=dest)
    _git(["-c", "user.email=aidev@local", "-c", "user.name=aidev",
          "commit", "-m", "sandbox snapshot", "--allow-empty"], cwd=dest)
    return dest


# --- файловые операции (строго внутри песочницы) --------------------------

def _resolve_in_task(task_id: int, rel_path: str) -> Path:
    base = task_dir(task_id)
    target = (base / rel_path).resolve()
    _assert_within_sandbox(target)
    if base not in target.parents and target != base:
        raise SandboxError(f"Файл '{rel_path}' вне каталога задачи {base}")
    return target


def read_file(task_id: int, rel_path: str) -> str:
    target = _resolve_in_task(task_id, rel_path)
    if not target.is_file():
        raise SandboxError(f"Файл не найден: {rel_path}")
    return target.read_text(encoding="utf-8")


def write_file(task_id: int, rel_path: str, content: str) -> Path:
    target = _resolve_in_task(task_id, rel_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


# --- diff и уборка --------------------------------------------------------

def collect_diff(task_id: int) -> str:
    """Unified diff правок агента относительно снимка."""
    dest = _assert_within_sandbox(task_dir(task_id))
    if not dest.is_dir():
        raise SandboxError(f"Нет рабочей копии для задачи {task_id}")
    _git(["add", "-A"], cwd=dest)
    res = _git(["-c", "color.ui=never", "diff", "--cached"], cwd=dest)
    if res.returncode != 0:
        raise SandboxError(f"git diff: {res.stderr.strip()}")
    return res.stdout


def cleanup(task_id: int) -> None:
    """Удалить рабочую копию задачи (только внутри SANDBOX_DIR)."""
    dest = _assert_within_sandbox(task_dir(task_id))
    if dest == sandbox_root():
        raise SandboxError("Отказ: cleanup не может удалить корень песочницы")
    shutil.rmtree(dest, ignore_errors=True)


# --- ветка + PR (SPEC L2) --------------------------------------------------

_BRANCH_RE = re.compile(r"[^A-Za-z0-9._/-]")


def create_pull_request(
    task_id: int,
    project_path: str,
    title: str,
    body: str,
    *,
    base: str = "main",
) -> str:
    """Ветка aidev/task-<id>, коммит правок, push, gh pr create. Возврат URL."""
    _assert_allowed_project(project_path)
    dest = _assert_within_sandbox(task_dir(task_id))
    if not dest.is_dir():
        raise SandboxError(f"Нет рабочей копии для задачи {task_id}")

    origin = _git(["remote", "get-url", "origin"], cwd=Path(project_path))
    if origin.returncode != 0:
        raise SandboxError(f"origin проекта не найден: {origin.stderr.strip()}")
    origin_url = origin.stdout.strip()
    repo = _github_repo(origin_url)

    branch = _BRANCH_RE.sub("-", f"aidev/task-{task_id}")

    _git(["add", "-A"], cwd=dest)
    status = _git(["status", "--porcelain"], cwd=dest)
    if not status.stdout.strip():
        raise SandboxError("Нет изменений для PR (diff пуст).")

    _git(["checkout", "-B", branch], cwd=dest)
    commit = _git(
        ["-c", "user.email=aidev@local", "-c", "user.name=aidev",
         "commit", "-m", title],
        cwd=dest,
    )
    if commit.returncode != 0 and "nothing to commit" not in commit.stdout:
        raise SandboxError(
            f"git commit: {commit.stderr.strip() or commit.stdout.strip()}"
        )

    _git(["remote", "remove", "github"], cwd=dest)
    add_rem = _git(["remote", "add", "github", origin_url], cwd=dest)
    if add_rem.returncode != 0:
        raise SandboxError(f"git remote add github: {add_rem.stderr.strip()}")

    push = _git(["push", "-u", "github", branch, "--force"], cwd=dest)
    if push.returncode != 0:
        raise SandboxError(f"git push: {push.stderr.strip()}")

    pr = _gh(
        ["pr", "create", "--repo", repo, "--head", branch,
         "--base", base, "--title", title, "--body", body],
        cwd=dest,
    )
    if pr.returncode != 0:
        raise SandboxError(
            f"gh pr create: {pr.stderr.strip() or pr.stdout.strip()}"
        )

    lines = [ln for ln in pr.stdout.strip().splitlines() if ln.strip()]
    return lines[-1] if lines else f"https://github.com/{repo}/pulls"
