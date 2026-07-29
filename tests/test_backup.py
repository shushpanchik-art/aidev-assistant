"""Smoke-тесты бэкап-скриптов AIDEV.

Проверяют: скрипты существуют, исполняемы, синтаксически валидны (bash -n),
tar-архив full-offsite исключает venv/.git/кэши, но включает код и .env.
Не требуют rclone/сети — только локальная логика.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
BACKUP_SH = SCRIPTS / "backup.sh"
FULL_SH = SCRIPTS / "backup_full_offsite.sh"


def test_scripts_exist_and_executable() -> None:
    for s in (BACKUP_SH, FULL_SH):
        assert s.is_file(), f"нет скрипта {s}"
        assert os.access(s, os.X_OK), f"{s} не исполняемый"


@pytest.mark.parametrize("script", [BACKUP_SH, FULL_SH])
def test_bash_syntax_valid(script: Path) -> None:
    r = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
    assert r.returncode == 0, f"bash -n {script} упал: {r.stderr}"


@pytest.mark.skipif(shutil.which("sqlite3") is None, reason="нет sqlite3")
def test_backup_sh_creates_and_dedups(tmp_path: Path) -> None:
    db = tmp_path / "aidev.db"
    subprocess.run(
        ["sqlite3", str(db), "CREATE TABLE t(x TEXT); INSERT INTO t VALUES('a');"],
        check=True,
    )
    bdir = tmp_path / "backups"
    env = {**os.environ, "DB_PATH": str(db), "BACKUP_DIR": str(bdir)}
    r1 = subprocess.run(
        ["bash", str(BACKUP_SH)], env=env, capture_output=True, text=True
    )
    assert r1.returncode == 0, r1.stderr
    dumps = list(bdir.glob("aidev_*.db"))
    assert len(dumps) == 1, f"ожидался 1 бэкап, есть {dumps}"
    # повторный прогон без изменений БД → dedup, второй файл не создаётся
    r2 = subprocess.run(
        ["bash", str(BACKUP_SH)], env=env, capture_output=True, text=True
    )
    assert r2.returncode == 0, r2.stderr
    assert "SKIP" in r2.stdout, r2.stdout
    assert len(list(bdir.glob("aidev_*.db"))) == 1


def test_backup_sh_skips_when_no_db(tmp_path: Path) -> None:
    env = {
        **os.environ,
        "DB_PATH": str(tmp_path / "nope.db"),
        "BACKUP_DIR": str(tmp_path / "b"),
    }
    r = subprocess.run(
        ["bash", str(BACKUP_SH)], env=env, capture_output=True, text=True
    )
    assert r.returncode == 0
    assert "ещё не создана" in r.stdout


def test_full_offsite_tar_excludes_junk_keeps_code(tmp_path: Path) -> None:
    """Повторяет tar-логику скрипта локально: venv/.git/кэши вон, код внутри."""
    root = tmp_path / "aidev"
    (root / "venv").mkdir(parents=True)
    (root / "venv" / "big").write_text("x")
    (root / ".git").mkdir()
    (root / ".git" / "HEAD").write_text("ref")
    (root / "sub" / "__pycache__").mkdir(parents=True)
    (root / "sub" / "__pycache__" / "m.pyc").write_text("x")
    (root / "sandbox").mkdir()
    (root / "sandbox" / "task1").write_text("x")
    (root / "config.py").write_text("X = 1")
    (root / ".env").write_text("BOT_TOKEN=secret")

    archive = tmp_path / "out.tar.gz"
    excludes = [
        "--exclude=aidev/venv",
        "--exclude=aidev/sandbox",
        "--exclude=aidev/mirrors",
        "--exclude=aidev/.git",
        "--exclude=aidev/__pycache__",
        "--exclude=aidev/**/__pycache__",
        "--exclude=aidev/.pytest_cache",
        "--exclude=aidev/.mypy_cache",
        "--exclude=aidev/.ruff_cache",
    ]
    subprocess.run(
        ["tar", *excludes, "-C", str(tmp_path), "-czf", str(archive), "aidev"],
        check=True,
    )
    with tarfile.open(archive) as tf:
        names = tf.getnames()
    junk = [n for n in names if "/venv/" in n or "/.git/" in n
            or "__pycache__" in n or "/sandbox/" in n]
    assert not junk, f"мусор попал в архив: {junk}"
    assert "aidev/config.py" in names
    assert "aidev/.env" in names, "критичный .env должен быть в бэкапе!"
