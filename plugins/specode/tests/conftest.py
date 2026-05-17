"""Shared fixtures for specode tests.

Tests use pytest but the plugin runtime stays stdlib-only. Install dev deps
with `python3 -m pip install pytest` to run the suite.
"""
from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Iterator

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import spec_state  # noqa: E402
import spec_guard  # noqa: E402


@pytest.fixture
def workspace() -> Iterator[dict]:
    """Provide a self-contained temp dir with spec_dir + project_root.

    Yields a dict with paths and patches spec_state.find_active_spec to return
    a synthetic info struct pointing into the workspace.
    """
    root = Path(tempfile.mkdtemp(prefix="specode-test-"))
    spec_dir = root / "test-spec"
    project_root = root / "project"
    spec_dir.mkdir()
    project_root.mkdir()
    (project_root / "src").mkdir()
    (spec_dir / "tasks.md").write_text(
        "# Tasks\n\n- [ ] FILE: src/foo.py\n- [ ] FILE: src/bar.py\n"
    )
    (spec_dir / ".config.json").write_text(json.dumps({"specId": "test-id"}))

    state = {
        "root": root,
        "spec_dir": spec_dir,
        "project_root": project_root,
        "current_phase": "implementation",
        "session_id": "test-sess",
        "slug": "test-spec",
    }

    def fake_find_active(prefer_session_id=None):
        return {
            "spec_slug": state["slug"],
            "spec_dir": str(state["spec_dir"]),
            "current_phase": state["current_phase"],
            "session_id": state["session_id"],
            "spec_id": "test-id",
            "last_activity_at": "2026-05-15T00:00:00Z",
        }

    original = spec_state.find_active_spec
    spec_state.find_active_spec = fake_find_active

    yield state

    spec_state.find_active_spec = original
    shutil.rmtree(root, ignore_errors=True)


def call_hook(sub: str, payload: dict, capture_stderr=False, capture_stdout=False):
    """Invoke spec_guard.main with a fabricated stdin payload.

    Returns (exit_code, stdout, stderr).
    """
    sys.stdin = io.StringIO(json.dumps(payload))
    out = io.StringIO()
    err = io.StringIO()
    if capture_stdout:
        sys.stdout = out
    if capture_stderr:
        sys.stderr = err
    try:
        rc = spec_guard.main(["spec_guard", sub])
    finally:
        sys.stdout = sys.__stdout__
        sys.stderr = sys.__stderr__
    return rc, out.getvalue(), err.getvalue()


@pytest.fixture
def hook_caller():
    return call_hook


def make_edit_payload(target, project_root, session_id="test-sess"):
    return {
        "session_id": session_id,
        "cwd": str(project_root),
        "tool_name": "Edit",
        "tool_input": {"file_path": str(target)},
    }


@pytest.fixture
def edit_payload():
    return make_edit_payload
