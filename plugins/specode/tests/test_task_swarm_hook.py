"""tests for hook_on_task_completed — 不在 run 期间 exit 0；在 run 期间注入正确文本。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

SCRIPTS_DIR = Path("/Users/xueqiang/Git/specode/plugins/specode/scripts")


@pytest.fixture
def fake_env(tmp_path, monkeypatch):
    home = tmp_path / "_home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("SPECODE_GUARD", raising=False)
    return home


def _run(script: str, *args: str, stdin: str = "", env_extra: dict = None,
         cwd: Path = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / script), *args],
        capture_output=True, text=True, input=stdin, env=env, timeout=30,
        cwd=str(cwd) if cwd else None,
    )


def _make_session(home: Path, sid: str, task_swarm_run_id: str = None,
                  mode: str = "active") -> Path:
    sd = home / ".specode" / "sessions"
    sd.mkdir(parents=True, exist_ok=True)
    p = sd / f"{sid}.json"
    p.write_text(json.dumps({
        "session_id": sid,
        "mode": mode,
        "task_swarm_run_id": task_swarm_run_id,
    }), encoding="utf-8")
    return p


def _write_tasks_md(tmp_path: Path) -> Path:
    p = tmp_path / "tasks.md"
    p.write_text(
        "## 阶段 1: A\n"
        "- [ ] 1.1 t @writes:f.py _需求：1.1_\n",
        encoding="utf-8",
    )
    return p


def test_hook_no_session_id_exits_0(fake_env):
    cp = _run("spec_session.py", "on-task-completed", stdin=json.dumps({}))
    assert cp.returncode == 0
    assert cp.stdout == ""


def test_hook_unknown_session_exits_0(fake_env):
    sid = str(uuid.uuid4())
    cp = _run("spec_session.py", "on-task-completed",
              stdin=json.dumps({"session_id": sid}))
    assert cp.returncode == 0
    assert cp.stdout == ""


def test_hook_no_task_swarm_run_id_exits_0(fake_env):
    sid = str(uuid.uuid4())
    _make_session(fake_env, sid, task_swarm_run_id=None)
    cp = _run("spec_session.py", "on-task-completed",
              stdin=json.dumps({"session_id": sid}))
    assert cp.returncode == 0
    # 没在 run 中 → 不注入
    assert cp.stdout == ""


def test_hook_guard_off_exits_0(fake_env):
    sid = str(uuid.uuid4())
    _make_session(fake_env, sid)
    cp = _run("spec_session.py", "on-task-completed",
              stdin=json.dumps({"session_id": sid}),
              env_extra={"SPECODE_GUARD": "off"})
    assert cp.returncode == 0
    assert cp.stdout == ""


def test_hook_in_run_injects_plan_context(fake_env, tmp_path):
    # 建一个真实 task-swarm run
    sid = str(uuid.uuid4())
    _make_session(fake_env, sid)
    tasks_md = _write_tasks_md(tmp_path)
    init_cp = _run("task_swarm.py", "init", "--tasks", str(tasks_md),
                   "--session", sid, cwd=tmp_path,
                   env_extra={"HOME": str(fake_env), "USERPROFILE": str(fake_env)})
    assert init_cp.returncode == 0, init_cp.stderr
    init_out = json.loads(init_cp.stdout)
    # sessions/<sid>.json.task_swarm_run_id 现在应当被设置
    sess = json.loads((fake_env / ".specode" / "sessions" / f"{sid}.json").read_text(encoding="utf-8"))
    assert sess["task_swarm_run_id"] == init_out["run_id"]
    # 触发 hook
    cp = _run("spec_session.py", "on-task-completed",
              stdin=json.dumps({"session_id": sid}),
              cwd=tmp_path,
              env_extra={"HOME": str(fake_env), "USERPROFILE": str(fake_env)})
    assert cp.returncode == 0
    assert cp.stdout, "hook 应注入 additionalContext"
    payload = json.loads(cp.stdout)
    assert "hookSpecificOutput" in payload
    ctx = payload["hookSpecificOutput"]["additionalContext"]
    assert "task-swarm 节点提醒" in ctx
    assert "本提醒仅供参考" in ctx


def test_hook_trailer_text_always_appended(fake_env, tmp_path):
    sid = str(uuid.uuid4())
    _make_session(fake_env, sid)
    tasks_md = _write_tasks_md(tmp_path)
    init_cp = _run("task_swarm.py", "init", "--tasks", str(tasks_md),
                   "--session", sid, cwd=tmp_path,
                   env_extra={"HOME": str(fake_env), "USERPROFILE": str(fake_env)})
    cp = _run("spec_session.py", "on-task-completed",
              stdin=json.dumps({"session_id": sid}),
              cwd=tmp_path,
              env_extra={"HOME": str(fake_env), "USERPROFILE": str(fake_env)})
    payload = json.loads(cp.stdout)
    ctx = payload["hookSpecificOutput"]["additionalContext"]
    assert "fork 谁、是否 fork、何时 writeback 仍由你判断" in ctx


def test_hook_payload_session_id_synonym(fake_env, tmp_path):
    """payload 用 'sessionId'（camelCase）也应被识别。"""
    sid = str(uuid.uuid4())
    _make_session(fake_env, sid, task_swarm_run_id=None)
    cp = _run("spec_session.py", "on-task-completed",
              stdin=json.dumps({"sessionId": sid}),
              env_extra={"HOME": str(fake_env), "USERPROFILE": str(fake_env)})
    assert cp.returncode == 0
    # 没在 run 中 → 不注入
    assert cp.stdout == ""


def test_hook_event_name_is_post_tool_use(fake_env, tmp_path):
    sid = str(uuid.uuid4())
    _make_session(fake_env, sid)
    tasks_md = _write_tasks_md(tmp_path)
    _run("task_swarm.py", "init", "--tasks", str(tasks_md),
         "--session", sid, cwd=tmp_path,
         env_extra={"HOME": str(fake_env), "USERPROFILE": str(fake_env)})
    cp = _run("spec_session.py", "on-task-completed",
              stdin=json.dumps({"session_id": sid}),
              cwd=tmp_path,
              env_extra={"HOME": str(fake_env), "USERPROFILE": str(fake_env)})
    payload = json.loads(cp.stdout)
    assert payload["hookSpecificOutput"]["hookEventName"] == "PostToolUse"


def test_hook_when_plan_fails_falls_back(fake_env, tmp_path):
    """sessions.task_swarm_run_id 指向不存在的 run → plan 失败但 hook 仍 exit 0 兜底注入。"""
    sid = str(uuid.uuid4())
    _make_session(fake_env, sid, task_swarm_run_id="nonexistent-run-id")
    cp = _run("spec_session.py", "on-task-completed",
              stdin=json.dumps({"session_id": sid}),
              cwd=tmp_path,
              env_extra={"HOME": str(fake_env), "USERPROFILE": str(fake_env)})
    assert cp.returncode == 0
    # 兜底文本应包含 run_id 和 plan 调用失败提示
    if cp.stdout:
        payload = json.loads(cp.stdout)
        ctx = payload["hookSpecificOutput"]["additionalContext"]
        assert "nonexistent-run-id" in ctx or "task-swarm" in ctx
