"""Integration tests for spec_guard.py: hook handlers + invariants end-to-end."""
from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import spec_sync


def make_edit_payload(target, project_root, session_id="test-sess"):
    return {
        "session_id": session_id,
        "cwd": str(project_root),
        "tool_name": "Edit",
        "tool_input": {"file_path": str(target)},
    }


def _new_turn(ws, hook_caller):
    hook_caller("user-prompt-submit", {"session_id": ws["session_id"], "cwd": str(ws["project_root"])}, capture_stdout=True)


# ---- INV-1 (Code-Doc Sync) -------------------------------------------------

def test_user_prompt_submit_injects_status_block(workspace, hook_caller):
    rc, stdout, _ = hook_caller(
        "user-prompt-submit",
        {"session_id": workspace["session_id"], "cwd": str(workspace["project_root"])},
        capture_stdout=True,
    )
    assert rc == 0
    payload = json.loads(stdout)
    block = payload["hookSpecificOutput"]["additionalContext"]
    assert "spec-mode active" in block
    assert "test-spec" in block
    assert "implementation" in block


def test_pretooluse_allows_tasks_files(workspace, hook_caller):
    _new_turn(workspace, hook_caller)
    target = workspace["project_root"] / "src/foo.py"
    rc, _, _ = hook_caller("pre-tool-use", make_edit_payload(target, workspace["project_root"]))
    assert rc == 0


def test_pretooluse_denies_inv1(workspace, hook_caller):
    _new_turn(workspace, hook_caller)
    target = workspace["project_root"] / "src/baz.py"
    rc, _, err = hook_caller(
        "pre-tool-use",
        make_edit_payload(target, workspace["project_root"]),
        capture_stderr=True,
    )
    assert rc == 2
    assert "INV-1" in err


def test_pretooluse_allows_after_doc_change(workspace, hook_caller):
    _new_turn(workspace, hook_caller)
    # Stage a doc change first.
    doc_target = workspace["spec_dir"] / "design.md"
    hook_caller("post-tool-use", make_edit_payload(doc_target, workspace["project_root"]))
    target = workspace["project_root"] / "src/baz.py"
    rc, _, err = hook_caller(
        "pre-tool-use",
        make_edit_payload(target, workspace["project_root"]),
        capture_stderr=True,
    )
    assert rc == 0, f"expected ok, got {rc}, err={err}"


def test_freeform_bypasses_inv1_but_not_inv6(workspace, hook_caller):
    (workspace["spec_dir"] / ".config.json").write_text(
        json.dumps({"specId": "test-id", "freeformMode": True})
    )
    _new_turn(workspace, hook_caller)
    target = workspace["project_root"] / "src/quux.py"
    rc, _, _ = hook_caller(
        "pre-tool-use", make_edit_payload(target, workspace["project_root"]), capture_stderr=True
    )
    assert rc == 0, "freeform should allow INV-1"

    # Switch to forbidden phase: INV-6 must still deny even with freeform.
    workspace["current_phase"] = "design"
    _new_turn(workspace, hook_caller)
    rc, _, err = hook_caller(
        "pre-tool-use", make_edit_payload(target, workspace["project_root"]), capture_stderr=True
    )
    assert rc == 2 and "INV-6" in err


# ---- INV-2 (turn conservation) --------------------------------------------

def test_stop_denies_inv2_when_code_only(workspace, hook_caller):
    _new_turn(workspace, hook_caller)
    # foo.py is in tasks_files so PreToolUse allows. Then we record it via PostToolUse.
    target = workspace["project_root"] / "src/foo.py"
    hook_caller("post-tool-use", make_edit_payload(target, workspace["project_root"]))
    rc, _, err = hook_caller(
        "stop", {"session_id": workspace["session_id"]}, capture_stderr=True
    )
    assert rc == 2 and "INV-2" in err


def test_stop_passes_with_code_plus_doc(workspace, hook_caller):
    _new_turn(workspace, hook_caller)
    hook_caller("post-tool-use", make_edit_payload(workspace["spec_dir"] / "design.md", workspace["project_root"]))
    hook_caller("post-tool-use", make_edit_payload(workspace["project_root"] / "src/foo.py", workspace["project_root"]))
    rc, _, _ = hook_caller("stop", {"session_id": workspace["session_id"]}, capture_stderr=True)
    assert rc == 0


def test_inv4_requirements_without_tasks(workspace, hook_caller):
    _new_turn(workspace, hook_caller)
    hook_caller("post-tool-use", make_edit_payload(workspace["spec_dir"] / "requirements.md", workspace["project_root"]))
    rc, _, err = hook_caller("stop", {"session_id": workspace["session_id"]}, capture_stderr=True)
    assert rc == 2 and "INV-4" in err


def test_inv4_requirements_with_tasks(workspace, hook_caller):
    _new_turn(workspace, hook_caller)
    hook_caller("post-tool-use", make_edit_payload(workspace["spec_dir"] / "requirements.md", workspace["project_root"]))
    hook_caller("post-tool-use", make_edit_payload(workspace["spec_dir"] / "tasks.md", workspace["project_root"]))
    rc, _, _ = hook_caller("stop", {"session_id": workspace["session_id"]}, capture_stderr=True)
    assert rc == 0


def test_inv4_bugfix_without_tasks(workspace, hook_caller):
    _new_turn(workspace, hook_caller)
    hook_caller("post-tool-use", make_edit_payload(workspace["spec_dir"] / "bugfix.md", workspace["project_root"]))
    rc, _, err = hook_caller("stop", {"session_id": workspace["session_id"]}, capture_stderr=True)
    assert rc == 2 and "INV-4" in err


# ---- INV-3 (verify-lock) --------------------------------------------------

def test_inv3_denies_when_evicted(workspace, hook_caller):
    (workspace["spec_dir"] / ".config.json").write_text(json.dumps({
        "specId": "test-id",
        "lock": {
            "sessionId": "other-session",
            "acquiredAt": "2026-05-15T00:00:00+00:00",
            "lastHeartbeatAt": "2026-05-15T00:00:00+00:00",
        },
        "evictedSessions": [{
            "sessionId": workspace["session_id"],
            "evictedAt": "2026-05-15T00:00:00+00:00",
            "evictedBy": "other-session",
            "reason": "force_acquire",
        }],
    }))
    _new_turn(workspace, hook_caller)
    rc, _, err = hook_caller(
        "pre-tool-use",
        make_edit_payload(workspace["spec_dir"] / "design.md", workspace["project_root"]),
        capture_stderr=True,
    )
    assert rc == 2 and "INV-3" in err


def test_inv3_allows_when_lock_owned(workspace, hook_caller):
    (workspace["spec_dir"] / ".config.json").write_text(json.dumps({
        "specId": "test-id",
        "lock": {
            "sessionId": workspace["session_id"],
            "acquiredAt": "2026-05-15T00:00:00+00:00",
            "lastHeartbeatAt": "2026-05-15T00:00:00+00:00",
        },
    }))
    _new_turn(workspace, hook_caller)
    rc, _, _ = hook_caller(
        "pre-tool-use",
        make_edit_payload(workspace["spec_dir"] / "design.md", workspace["project_root"]),
        capture_stderr=True,
    )
    assert rc == 0


# ---- outside / silent paths -----------------------------------------------

def test_outside_project_root_ignored(workspace, hook_caller):
    _new_turn(workspace, hook_caller)
    rc, _, _ = hook_caller(
        "pre-tool-use",
        make_edit_payload(Path("/tmp/elsewhere.txt"), workspace["project_root"]),
        capture_stderr=True,
    )
    assert rc == 0
