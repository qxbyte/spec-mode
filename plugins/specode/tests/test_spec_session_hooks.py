"""Tests for spec_session.py hook sub-commands.

Hooks always exit 0. They communicate with the host via stdout JSON of the form:
    {"hookSpecificOutput": {"hookEventName": <e>, "additionalContext": <str>}}
Empty stdout (or empty JSON) means "no injection".
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import pytest


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _parse_hook(stdout: str) -> Optional[dict]:
    """Parse a hook's stdout. Returns None when stdout is empty."""
    s = stdout.strip()
    if not s:
        return None
    return json.loads(s)


def _ctx(payload: Optional[dict]) -> str:
    """Pull additionalContext text out of a hook payload (or '' when none)."""
    if payload is None:
        return ""
    return payload.get("hookSpecificOutput", {}).get("additionalContext", "")


def _write_session(fake_home: Path, sid: str, **overrides) -> Path:
    """Write a sessions/<sid>.json with sensible defaults that callers can patch."""
    sess_dir = fake_home / ".specode" / "sessions"
    sess_dir.mkdir(parents=True, exist_ok=True)
    base = {
        "session_id": sid,
        "started_at": "2026-01-01T00:00:00Z",
        "last_activity_at": "2026-01-01T00:00:00Z",
        "ended_at": None,
        "mode": "idle",
        "active_spec_slug": None,
        "active_spec_dir": None,
        "spec_id": None,
        "phase": None,
        "lock_state": "released",
        "task_swarm_run_id": None,
        "pending_selector": None,
    }
    base.update(overrides)
    p = sess_dir / f"{sid}.json"
    p.write_text(json.dumps(base), encoding="utf-8")
    return p


# --------------------------------------------------------------------------
# on-session-start
# --------------------------------------------------------------------------

def test_on_session_start_new_session_writes_idle(
    run_script, fake_home, make_session_id
):
    sid = make_session_id()
    cp = run_script("spec_session.py", "on-session-start",
                    stdin=json.dumps({"session_id": sid}))
    assert cp.returncode == 0, cp.stderr
    sess_path = fake_home / ".specode" / "sessions" / f"{sid}.json"
    assert sess_path.exists()
    sess = json.loads(sess_path.read_text())
    assert sess["mode"] == "idle"
    assert sess["session_id"] == sid


def test_on_session_start_additional_context_contains_session_id(
    run_script, fake_home, make_session_id
):
    sid = make_session_id()
    cp = run_script("spec_session.py", "on-session-start",
                    stdin=json.dumps({"session_id": sid}))
    payload = _parse_hook(cp.stdout)
    ctx = _ctx(payload)
    assert sid in ctx
    assert "Specode session" in ctx


def test_on_session_start_reactivates_ended_session(
    run_script, fake_home, make_session_id
):
    sid = make_session_id()
    _write_session(fake_home, sid, mode="ended", ended_at="2026-01-01T00:00:00Z")
    cp = run_script("spec_session.py", "on-session-start",
                    stdin=json.dumps({"session_id": sid}))
    assert cp.returncode == 0
    sess = json.loads(
        (fake_home / ".specode" / "sessions" / f"{sid}.json").read_text()
    )
    assert sess["mode"] == "idle"  # back to idle from ended
    assert sess["ended_at"] is None


# --------------------------------------------------------------------------
# on-user-prompt
# --------------------------------------------------------------------------

def test_on_user_prompt_ended_session_emits_nothing(
    run_script, fake_home, make_session_id
):
    sid = make_session_id()
    _write_session(fake_home, sid, mode="ended")
    cp = run_script(
        "spec_session.py", "on-user-prompt",
        stdin=json.dumps({"session_id": sid, "prompt": "hello"})
    )
    assert cp.returncode == 0
    # mode=ended branch returns early; no additionalContext emitted.
    assert _parse_hook(cp.stdout) is None


def test_on_user_prompt_idle_session_emits_nothing(
    run_script, fake_home, make_session_id
):
    sid = make_session_id()
    _write_session(fake_home, sid, mode="idle")
    cp = run_script(
        "spec_session.py", "on-user-prompt",
        stdin=json.dumps({"session_id": sid, "prompt": "hello"})
    )
    assert cp.returncode == 0
    # idle also returns early
    assert _parse_hook(cp.stdout) is None


def test_on_user_prompt_active_with_workflow_choice_emits_all_segments(
    run_script, fake_home, make_session_id, doc_root
):
    sid = make_session_id()
    # set up an active spec with a real spec config
    spec_dir = doc_root / "specs" / "active-spec"
    spec_dir.mkdir(parents=True)
    (spec_dir / ".config.json").write_text(json.dumps({
        "specId": "abc",
        "slug": "active-spec",
        "phase": "intake",
        "workflow": None,
        "pending_selector": "workflow-choice",
        "lock": {"holder": sid},
        "source_text": "示例源需求摘要内容",
    }), encoding="utf-8")
    _write_session(
        fake_home, sid,
        mode="active",
        active_spec_slug="active-spec",
        active_spec_dir=str(spec_dir),
        phase="intake",
        pending_selector="workflow-choice",
        lock_state="ok",
    )
    cp = run_script(
        "spec_session.py", "on-user-prompt",
        stdin=json.dumps({"session_id": sid, "prompt": "继续推进"})
    )
    payload = _parse_hook(cp.stdout)
    ctx = _ctx(payload)
    # 5 segments expected
    assert sid in ctx                                        # session_id 提醒
    assert "选择器节点：工作流选择" in ctx
    assert "AskUserQuestion" in ctx
    assert "multiSelect: false" in ctx
    assert "Requirements first" in ctx
    assert "Technical Design first" in ctx
    assert "Bugfix" in ctx
    assert "状态行" in ctx                                    # footer template
    assert "spec-mode" in ctx
    assert "文档优先提醒" in ctx
    assert "你仍处于 spec 模式" in ctx                         # continue reminder


def test_on_user_prompt_active_implementation_no_pending(
    run_script, fake_home, make_session_id, doc_root
):
    """phase=implementation with no pending_selector → selector segment absent
    but other segments present."""
    sid = make_session_id()
    spec_dir = doc_root / "specs" / "mid-impl"
    spec_dir.mkdir(parents=True)
    (spec_dir / ".config.json").write_text(json.dumps({
        "specId": "x",
        "slug": "mid-impl",
        "phase": "implementation",
        "workflow": "requirements-first",
        "pending_selector": None,
        "lock": {"holder": sid},
    }), encoding="utf-8")
    _write_session(
        fake_home, sid,
        mode="active",
        active_spec_slug="mid-impl",
        active_spec_dir=str(spec_dir),
        phase="implementation",
        pending_selector=None,
        lock_state="ok",
    )
    cp = run_script(
        "spec_session.py", "on-user-prompt",
        stdin=json.dumps({"session_id": sid, "prompt": "更多 coding"})
    )
    ctx = _ctx(_parse_hook(cp.stdout))
    assert "选择器节点：" not in ctx          # no selector segment
    assert "文档优先提醒" in ctx
    assert "状态行" in ctx
    assert "你仍处于 spec 模式" in ctx
    assert sid in ctx


def test_on_user_prompt_readonly_footer_has_readonly_marker(
    run_script, fake_home, make_session_id, doc_root
):
    sid = make_session_id()
    spec_dir = doc_root / "specs" / "ro-spec"
    spec_dir.mkdir(parents=True)
    (spec_dir / ".config.json").write_text(json.dumps({
        "specId": "ro",
        "slug": "ro-spec",
        "phase": "tasks",
        "pending_selector": None,
        "lock": {"holder": "someone-else"},
    }), encoding="utf-8")
    _write_session(
        fake_home, sid,
        mode="readonly",
        active_spec_slug="ro-spec",
        active_spec_dir=str(spec_dir),
        phase="tasks",
        pending_selector=None,
        lock_state="readonly",
    )
    cp = run_script(
        "spec_session.py", "on-user-prompt",
        stdin=json.dumps({"session_id": sid, "prompt": "只读一下"})
    )
    ctx = _ctx(_parse_hook(cp.stdout))
    assert "[只读]" in ctx
    assert "只读模式" in ctx


def test_on_user_prompt_help_fastpath_only_emits_help(
    run_script, fake_home, make_session_id
):
    sid = make_session_id()
    # Even active sessions only emit the help fast-path when prompt matches
    _write_session(fake_home, sid, mode="active",
                   active_spec_slug="any", phase="intake",
                   pending_selector="workflow-choice",
                   active_spec_dir="/dev/null")
    cp = run_script(
        "spec_session.py", "on-user-prompt",
        stdin=json.dumps({"session_id": sid, "prompt": "/specode:spec -h"})
    )
    ctx = _ctx(_parse_hook(cp.stdout))
    assert "fast-path" in ctx
    assert "specode v0.6" in ctx
    # Workflow-choice selector should NOT leak in
    assert "选择器节点：工作流选择" not in ctx


def test_on_user_prompt_vault_status_fastpath(
    run_script, fake_home, make_session_id
):
    sid = make_session_id()
    _write_session(fake_home, sid, mode="idle")
    cp = run_script(
        "spec_session.py", "on-user-prompt",
        stdin=json.dumps({"session_id": sid, "prompt": "/specode:spec --vault-status"})
    )
    ctx = _ctx(_parse_hook(cp.stdout))
    assert "vault-status fast-path" in ctx
    # The wrapped content contains JSON with either "source": "..." or "doc_root"
    assert "doc_root" in ctx or "source" in ctx


def test_on_user_prompt_guard_off_emits_nothing(
    run_script, fake_home, make_session_id
):
    sid = make_session_id()
    _write_session(fake_home, sid, mode="active",
                   active_spec_slug="any", phase="intake",
                   pending_selector="workflow-choice",
                   active_spec_dir="/dev/null")
    cp = run_script(
        "spec_session.py", "on-user-prompt",
        stdin=json.dumps({"session_id": sid, "prompt": "hi"}),
        extra_env={"SPECODE_GUARD": "off"},
    )
    assert cp.returncode == 0
    assert cp.stdout.strip() == ""


# --------------------------------------------------------------------------
# on-stop
# --------------------------------------------------------------------------

def test_on_stop_active_emits_code_doc_sync(
    run_script, fake_home, make_session_id, doc_root
):
    sid = make_session_id()
    _write_session(fake_home, sid, mode="active",
                   active_spec_slug="s", phase="implementation",
                   active_spec_dir=str(doc_root / "specs" / "s"))
    cp = run_script(
        "spec_session.py", "on-stop",
        stdin=json.dumps({"session_id": sid})
    )
    ctx = _ctx(_parse_hook(cp.stdout))
    assert "代码-文档同步提醒" in ctx
    assert "tasks.md" in ctx
    assert "implementation-log.md" in ctx
    assert "你仍处于 spec 模式" in ctx


def test_on_stop_ended_emits_nothing(
    run_script, fake_home, make_session_id
):
    sid = make_session_id()
    _write_session(fake_home, sid, mode="ended")
    cp = run_script(
        "spec_session.py", "on-stop",
        stdin=json.dumps({"session_id": sid})
    )
    assert _parse_hook(cp.stdout) is None


def test_on_stop_readonly_only_readonly_reminder(
    run_script, fake_home, make_session_id, doc_root
):
    sid = make_session_id()
    _write_session(fake_home, sid, mode="readonly",
                   active_spec_slug="s", phase="implementation",
                   active_spec_dir=str(doc_root / "specs" / "s"))
    cp = run_script(
        "spec_session.py", "on-stop",
        stdin=json.dumps({"session_id": sid})
    )
    ctx = _ctx(_parse_hook(cp.stdout))
    assert "只读模式" in ctx
    # No code-doc sync segment in readonly
    assert "代码-文档同步提醒" not in ctx


# --------------------------------------------------------------------------
# on-session-end
# --------------------------------------------------------------------------

def test_on_session_end_releases_held_lock(
    run_script, fake_home, make_session_id, doc_root
):
    sid = make_session_id()
    spec_dir = doc_root / "specs" / "end-spec"
    spec_dir.mkdir(parents=True)
    (spec_dir / ".config.json").write_text(json.dumps({
        "specId": "e",
        "slug": "end-spec",
        "phase": "tasks",
        "pending_selector": "doc-confirm-tasks",
        "lock": {"holder": sid, "acquired_at": "2026-01-01T00:00:00Z",
                 "last_heartbeat_at": "2026-01-01T00:00:00Z"},
    }), encoding="utf-8")
    _write_session(
        fake_home, sid,
        mode="active",
        active_spec_slug="end-spec",
        active_spec_dir=str(spec_dir),
        phase="tasks",
        lock_state="ok",
    )
    cp = run_script(
        "spec_session.py", "on-session-end",
        stdin=json.dumps({"session_id": sid})
    )
    assert cp.returncode == 0
    cfg = json.loads((spec_dir / ".config.json").read_text())
    assert cfg["lock"] is None
    sess = json.loads(
        (fake_home / ".specode" / "sessions" / f"{sid}.json").read_text()
    )
    assert sess["mode"] == "ended"
    assert sess["ended_at"]


def test_on_session_end_no_active_spec_is_ok(
    run_script, fake_home, make_session_id
):
    sid = make_session_id()
    _write_session(fake_home, sid, mode="idle")
    cp = run_script(
        "spec_session.py", "on-session-end",
        stdin=json.dumps({"session_id": sid})
    )
    assert cp.returncode == 0
    sess = json.loads(
        (fake_home / ".specode" / "sessions" / f"{sid}.json").read_text()
    )
    assert sess["mode"] == "ended"


def test_on_session_end_missing_session_is_ok(
    run_script, fake_home, make_session_id
):
    sid = make_session_id()
    # no session file exists
    cp = run_script(
        "spec_session.py", "on-session-end",
        stdin=json.dumps({"session_id": sid})
    )
    assert cp.returncode == 0
    # Hook returns early when session not found; no file written
    sess_path = fake_home / ".specode" / "sessions" / f"{sid}.json"
    assert not sess_path.exists()
