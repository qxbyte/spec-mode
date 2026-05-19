"""End-to-end integration tests covering the full specode v0.6 event chain."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


def _read_sess(fake_home: Path, sid: str) -> dict:
    return json.loads((fake_home / ".specode" / "sessions" / f"{sid}.json").read_text())


def _read_cfg(spec_dir: Path) -> dict:
    return json.loads((spec_dir / ".config.json").read_text())


def _ctx(stdout: str) -> str:
    s = stdout.strip()
    if not s:
        return ""
    return json.loads(s).get("hookSpecificOutput", {}).get("additionalContext", "")


def test_full_lifecycle_event_chain(run_script, fake_home, doc_root, make_session_id):
    """SessionStart → /spec → phase-transition → /end → SessionEnd.
    Validate state of sessions/<id>.json and spec.config.json at each step."""
    sid = make_session_id()

    # 1) SessionStart hook (new session)
    cp = run_script("spec_session.py", "on-session-start",
                    stdin=json.dumps({"session_id": sid}))
    assert cp.returncode == 0
    sess = _read_sess(fake_home, sid)
    assert sess["mode"] == "idle"

    # 2) /spec creates a new spec; mode should become active, lock held by sid.
    cp = run_script(
        "spec_init.py",
        "--name", "lifecycle",
        "--requirement-name", "Lifecycle Spec",
        "--source-text", "做一个完整生命周期的测试",
        "--session", sid,
    )
    assert cp.returncode == 0, cp.stderr
    payload = json.loads(cp.stdout)
    spec_dir = Path(payload["spec_dir"])
    sess = _read_sess(fake_home, sid)
    assert sess["mode"] == "active"
    assert sess["active_spec_slug"] == "lifecycle"
    assert sess["pending_selector"] == "workflow-choice"
    cfg = _read_cfg(spec_dir)
    assert cfg["phase"] == "intake"
    assert cfg["lock"]["holder"] == sid

    # 3) phase-transition intake → requirements
    cp = run_script("spec_session.py", "phase-transition",
                    "--spec", str(spec_dir), "--session", sid,
                    "--from", "intake", "--to", "requirements")
    assert cp.returncode == 0
    assert _read_cfg(spec_dir)["phase"] == "requirements"
    assert _read_sess(fake_home, sid)["phase"] == "requirements"
    assert _read_sess(fake_home, sid)["pending_selector"] == "doc-confirm-requirements"

    # 4) /end releases the lock and sets mode=ended
    cp = run_script("spec_session.py", "end", "--session", sid)
    assert cp.returncode == 0
    sess = _read_sess(fake_home, sid)
    assert sess["mode"] == "ended"
    assert sess["ended_at"]
    assert _read_cfg(spec_dir)["lock"] is None

    # 5) SessionEnd hook is idempotent; sess remains ended; lock stays released
    cp = run_script("spec_session.py", "on-session-end",
                    stdin=json.dumps({"session_id": sid}))
    assert cp.returncode == 0
    assert _read_sess(fake_home, sid)["mode"] == "ended"
    assert _read_cfg(spec_dir)["lock"] is None


def test_after_end_user_prompt_emits_nothing(
    run_script, fake_home, doc_root, make_session_id
):
    """After /end, on-user-prompt must not inject spec-mode reminders."""
    sid = make_session_id()
    # Create spec and end it
    cp = run_script(
        "spec_init.py",
        "--name", "ended",
        "--requirement-name", "Ended",
        "--source-text", "x",
        "--session", sid,
    )
    assert cp.returncode == 0
    run_script("spec_session.py", "end", "--session", sid)

    cp = run_script(
        "spec_session.py", "on-user-prompt",
        stdin=json.dumps({"session_id": sid, "prompt": "hey, anything?"})
    )
    assert cp.returncode == 0
    assert cp.stdout.strip() == ""


def test_multi_window_takeover(run_script, fake_home, doc_root, make_session_id):
    """Two sessions racing for the same spec lock — acquire/force/heartbeat semantics."""
    sid_a = make_session_id()
    sid_b = make_session_id()

    # Session A creates the spec → holds lock
    cp = run_script(
        "spec_init.py",
        "--name", "race",
        "--requirement-name", "Race",
        "--source-text", "race",
        "--session", sid_a,
    )
    assert cp.returncode == 0
    spec_dir = Path(json.loads(cp.stdout)["spec_dir"])
    assert _read_cfg(spec_dir)["lock"]["holder"] == sid_a

    # Session B fails to acquire (no --force)
    cp = run_script("spec_session.py", "acquire",
                    "--spec", str(spec_dir), "--session", sid_b)
    assert cp.returncode == 4
    assert _read_cfg(spec_dir)["lock"]["holder"] == sid_a

    # Session B forces takeover
    cp = run_script("spec_session.py", "acquire",
                    "--spec", str(spec_dir), "--session", sid_b, "--force")
    assert cp.returncode == 0
    assert _read_cfg(spec_dir)["lock"]["holder"] == sid_b

    # Session A's heartbeat is now rejected
    cp = run_script("spec_session.py", "heartbeat",
                    "--spec", str(spec_dir), "--session", sid_a)
    assert cp.returncode == 1
    payload = json.loads(cp.stdout)
    assert payload["ok"] is False
    assert payload["reason"] == "lock_lost"


def test_guard_off_bypasses_all_hooks(run_script, fake_home, make_session_id):
    """SPECODE_GUARD=off → all four hooks exit 0 with empty stdout."""
    sid = make_session_id()
    # Even if we set up an active session, GUARD=off should silence everything
    sess_dir = fake_home / ".specode" / "sessions"
    sess_dir.mkdir(parents=True, exist_ok=True)
    (sess_dir / f"{sid}.json").write_text(json.dumps({
        "session_id": sid,
        "mode": "active",
        "active_spec_slug": "x",
        "active_spec_dir": "/tmp/no-such",
        "phase": "intake",
        "pending_selector": "workflow-choice",
        "lock_state": "ok",
    }), encoding="utf-8")
    env = {"SPECODE_GUARD": "off"}
    for hook in ("on-session-start", "on-user-prompt", "on-stop", "on-session-end"):
        cp = run_script(
            "spec_session.py", hook,
            stdin=json.dumps({"session_id": sid, "prompt": "ping"}),
            extra_env=env,
        )
        assert cp.returncode == 0, f"hook {hook} did not exit 0"
        assert cp.stdout.strip() == "", f"hook {hook} emitted output: {cp.stdout!r}"


def test_continue_readonly_emits_readonly_mode(
    run_script, fake_home, doc_root, make_session_id
):
    """`continue --readonly` returns ok with mode=readonly and does not seize the lock.

    NOTE: as of v0.6 scripts, the --readonly code-path in cmd_continue does NOT
    persist the readonly mode to sessions/<id>.json (the `else: acquire lock`
    branch is the only one that writes a session payload). This test pins the
    current observable behaviour: stdout reports mode=readonly, lock holder is
    unchanged. See report for interface drift.
    """
    sid_a = make_session_id()
    sid_b = make_session_id()
    cp = run_script(
        "spec_init.py",
        "--name", "ro-flow",
        "--requirement-name", "RO Flow",
        "--source-text", "x",
        "--session", sid_a,
    )
    assert cp.returncode == 0
    spec_dir = Path(json.loads(cp.stdout)["spec_dir"])

    cp = run_script("spec_session.py", "continue",
                    "--spec", str(spec_dir), "--session", sid_b, "--readonly")
    assert cp.returncode == 0, cp.stderr
    payload = json.loads(cp.stdout)
    assert payload["ok"] is True
    assert payload["mode"] == "readonly"
    # Lock still held by A
    assert _read_cfg(spec_dir)["lock"]["holder"] == sid_a


def test_continue_no_force_no_readonly_writes_takeover_pending(
    run_script, fake_home, doc_root, make_session_id
):
    """When B continues without --force or --readonly on a locked spec,
    spec config's pending_selector flips to takeover-options and exit 4."""
    sid_a = make_session_id()
    sid_b = make_session_id()
    cp = run_script(
        "spec_init.py",
        "--name", "takeover-flow",
        "--requirement-name", "TF",
        "--source-text", "x",
        "--session", sid_a,
    )
    assert cp.returncode == 0
    spec_dir = Path(json.loads(cp.stdout)["spec_dir"])

    cp = run_script("spec_session.py", "continue",
                    "--spec", str(spec_dir), "--session", sid_b)
    assert cp.returncode == 4
    out = json.loads(cp.stdout)
    assert out["ok"] is False
    assert out["pending_selector"] == "takeover-options"
    # Spec config updated; session B's sessions file written
    cfg = _read_cfg(spec_dir)
    assert cfg["pending_selector"] == "takeover-options"
    sess_b = _read_sess(fake_home, sid_b)
    assert sess_b["mode"] == "readonly"
    assert sess_b["pending_selector"] == "takeover-options"
