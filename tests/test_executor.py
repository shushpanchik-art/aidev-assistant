"""Тесты executor'а -- САМЫЙ ВАЖНЫЙ модуль безопасности (SPEC §13).

Проверяем БЕЗ реального запуска опасных команд: политика отклоняет
всё вне allow-list и всё вне песочницы ДО вызова subprocess.
"""
from __future__ import annotations

import os

os.environ.setdefault("WEB_AUTH_TOKEN", "test-token")

import pytest  # noqa: E402

from agent import executor  # noqa: E402


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Подменяем SANDBOX_DIR на tmp и создаём каталог задачи '1'."""
    root = tmp_path / "sandbox"
    root.mkdir()
    monkeypatch.setattr(executor.config, "SANDBOX_DIR", str(root))
    task = root / "1"
    task.mkdir()
    return root, task


# --- allow-list: опасные команды отклоняются ---

@pytest.mark.parametrize(
    "argv",
    [
        ["rm", "-rf", "/"],
        ["curl", "http://evil"],
        ["sudo", "systemctl", "restart", "smoki-bot"],
        ["systemctl", "stop", "smoki-bot"],
        ["bash", "-c", "echo hi"],
        ["git", "push"],
        ["chmod", "-R", "777", "."],
    ],
)
def test_blocks_commands_not_in_allowlist(sandbox, argv):
    with pytest.raises(executor.ExecutorError, match="allow-list"):
        executor.run(argv, task_id=1)


def test_empty_command_rejected(sandbox):
    with pytest.raises(executor.ExecutorError):
        executor.run([], task_id=1)


# --- песочница: cwd вне неё отклоняется ---

@pytest.mark.parametrize("bad_cwd", ["/opt/SMOKI/bot", "/etc", "/opt/aidev", "/"])
def test_blocks_cwd_outside_sandbox(sandbox, bad_cwd):
    with pytest.raises(executor.ExecutorError, match="вне песочницы"):
        executor.run(["ruff", "check", "."], task_id=1, cwd=bad_cwd)


def test_blocks_escape_via_parent(sandbox):
    _root, task = sandbox
    escape = str(task / ".." / ".." / "etc")
    with pytest.raises(executor.ExecutorError, match="вне песочницы"):
        executor.run(["ruff", "check", "."], task_id=1, cwd=escape)


# --- is_within_sandbox ---

def test_is_within_sandbox_true_for_task_dir(sandbox):
    _root, task = sandbox
    assert executor.is_within_sandbox(task) is True


def test_is_within_sandbox_false_for_outside(sandbox):
    assert executor.is_within_sandbox("/etc") is False


def test_is_within_sandbox_false_for_escape(sandbox):
    _root, task = sandbox
    assert executor.is_within_sandbox(str(task / ".." / "..")) is False


# --- memory-guard: pytest откладывается при нехватке RAM (§15) ---

def test_pytest_deferred_when_low_memory(sandbox, monkeypatch):
    monkeypatch.setattr(executor.config, "MEM_MIN_AVAIL_MB", 100000)
    monkeypatch.setattr(executor, "available_memory_mb", lambda: 50)
    with pytest.raises(executor.ExecutorError, match="Мало памяти"):
        executor.run(["pytest", "-q"], task_id=1)


def test_ruff_not_blocked_by_memory_guard(sandbox, monkeypatch):
    """ruff не в списке memory-heavy -- не должен отклоняться по памяти."""
    monkeypatch.setattr(executor.config, "MEM_MIN_AVAIL_MB", 100000)
    monkeypatch.setattr(executor, "available_memory_mb", lambda: 1)
    called = {}

    def fake_run(resolved, **kwargs):
        called["cmd"] = resolved

        class P:
            returncode = 0
            stdout = ""
            stderr = ""

        return P()

    monkeypatch.setattr(executor.subprocess, "run", fake_run)
    res = executor.run(["ruff", "--version"], task_id=1)
    assert res.ok
    assert called["cmd"][0].endswith("/ruff")


# --- happy path: разрешённая команда доходит до subprocess ---

def test_allowed_command_runs_in_task_dir(sandbox, monkeypatch):
    _root, task = sandbox
    captured = {}

    def fake_run(resolved, **kwargs):
        captured["resolved"] = resolved
        captured["cwd"] = kwargs.get("cwd")
        captured["shell"] = kwargs.get("shell")

        class P:
            returncode = 0
            stdout = "all good"
            stderr = ""

        return P()

    monkeypatch.setattr(executor.subprocess, "run", fake_run)
    res = executor.run(["pytest", "-q"], task_id=1)
    assert res.ok
    assert res.stdout == "all good"
    assert captured["cwd"] == str(task)
    assert captured["shell"] is False
    assert captured["resolved"][0].endswith("/pytest")


def test_run_result_ok_property():
    assert executor.RunResult(0, "", "").ok is True
    assert executor.RunResult(1, "", "err").ok is False
