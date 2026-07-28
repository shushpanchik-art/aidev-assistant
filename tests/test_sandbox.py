"""Тесты sandbox-менеджера (SPEC §5, test_sandbox).

Главное: снимок БЕЗ секретов (.env/*.db) и правки НЕ касаются оригинала.
Используем локальный fake-git-репо во tmp вместо реального /opt/SMOKI/bot.
"""
from __future__ import annotations

import os
import subprocess

os.environ.setdefault("WEB_AUTH_TOKEN", "test-token")

import pytest  # noqa: E402

from agent import sandbox  # noqa: E402


def _run(args, cwd):
    subprocess.run(args, cwd=str(cwd), check=True,
                   capture_output=True, text=True)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Готовим: fake целевой репо + пустые mirrors/sandbox во tmp."""
    project = tmp_path / "proj"
    project.mkdir()
    _run(["git", "init", "-q"], project)
    _run(["git", "config", "user.email", "t@t"], project)
    _run(["git", "config", "user.name", "t"], project)
    (project / "app.py").write_text("x = 1\n")
    (project / ".env").write_text("SECRET=topsecret\n")
    (project / "data.db").write_text("BINARYDB")
    (project / "logs").mkdir()
    (project / "logs" / "run.log").write_text("log\n")
    _run(["git", "add", "-A"], project)
    _run(["git", "commit", "-q", "-m", "init"], project)

    mirrors = tmp_path / "mirrors"
    sbox = tmp_path / "sandbox"
    mirrors.mkdir()
    sbox.mkdir()
    monkeypatch.setattr(sandbox.config, "MIRRORS_DIR", str(mirrors))
    monkeypatch.setattr(sandbox.config, "SANDBOX_DIR", str(sbox))
    monkeypatch.setattr(sandbox.config, "ALLOWED_PROJECTS", [str(project)])
    return project, mirrors, sbox


# --- allow-list проектов ---

def test_mirror_rejects_project_not_in_allowlist(env):
    with pytest.raises(sandbox.SandboxError, match="ALLOWED_PROJECTS"):
        sandbox.create_mirror("/etc")


def test_mirror_rejects_smoki_bot_when_not_allowed(env):
    with pytest.raises(sandbox.SandboxError):
        sandbox.create_mirror("/opt/SMOKI/bot")


# --- зеркало ---

def test_create_mirror_is_bare_and_idempotent(env):
    mp = sandbox.create_mirror(str(env[0]))
    assert mp.exists()
    assert mp.name.endswith(".git")
    mp2 = sandbox.create_mirror(str(env[0]))  # второй раз -> update
    assert mp == mp2


# --- снимок без секретов (ГЛАВНОЕ) ---

def test_workspace_has_code_but_no_secrets(env):
    project = env[0]
    dest = sandbox.prepare_workspace(1, str(project))
    assert (dest / "app.py").is_file()
    assert not (dest / ".env").exists(), ".env должен быть вырезан"
    assert not (dest / "data.db").exists(), "*.db должен быть вырезан"
    assert not (dest / "logs").exists(), "logs/ должен быть вырезан"


def test_workspace_within_sandbox(env):
    dest = sandbox.prepare_workspace(2, str(env[0]))
    assert sandbox.sandbox_root() in dest.parents


# --- правки НЕ касаются оригинала ---

def test_edits_do_not_touch_original(env):
    project = env[0]
    sandbox.prepare_workspace(3, str(project))
    sandbox.write_file(3, "app.py", "x = 999  # edited\n")
    assert (project / "app.py").read_text() == "x = 1\n", "оригинал изменён!"
    assert "999" in sandbox.read_file(3, "app.py")


# --- защита путей ---

def test_write_outside_task_rejected(env):
    sandbox.prepare_workspace(4, str(env[0]))
    with pytest.raises(sandbox.SandboxError, match="вне"):
        sandbox.write_file(4, "../../escape.py", "boom")


def test_read_missing_file_raises(env):
    sandbox.prepare_workspace(5, str(env[0]))
    with pytest.raises(sandbox.SandboxError, match="не найден"):
        sandbox.read_file(5, "nope.py")


# --- diff ---

def test_collect_diff_shows_only_agent_changes(env):
    sandbox.prepare_workspace(6, str(env[0]))
    sandbox.write_file(6, "app.py", "x = 42\n")
    diff = sandbox.collect_diff(6)
    assert "app.py" in diff
    assert "+x = 42" in diff
    assert "topsecret" not in diff  # секрет не утёк


def test_collect_diff_empty_when_no_changes(env):
    sandbox.prepare_workspace(7, str(env[0]))
    assert sandbox.collect_diff(7).strip() == ""


# --- cleanup ---

def test_cleanup_removes_task_dir(env):
    dest = sandbox.prepare_workspace(8, str(env[0]))
    assert dest.exists()
    sandbox.cleanup(8)
    assert not dest.exists()


def test_cleanup_refuses_sandbox_root(env, monkeypatch):
    monkeypatch.setattr(sandbox, "task_dir", lambda _tid: sandbox.sandbox_root())
    with pytest.raises(sandbox.SandboxError, match="корень"):
        sandbox.cleanup(999)
