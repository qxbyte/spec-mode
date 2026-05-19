"""Tests for spec_init.py — initial spec scaffolding + session/active-pointer writes."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


DOC_FILENAMES = (
    "requirements.md",
    "bugfix.md",
    "design.md",
    "tasks.md",
    "acceptance-checklist.md",
    "implementation-log.md",
)


def test_spec_init_creates_full_skeleton(run_script, doc_root, fake_home, make_session_id):
    sid = make_session_id()
    cp = run_script(
        "spec_init.py",
        "--name", "alpha-spec",
        "--requirement-name", "Alpha Spec",
        "--source-text", "我要做一个测试需求",
        "--session", sid,
    )
    assert cp.returncode == 0, cp.stderr
    payload = json.loads(cp.stdout)
    spec_dir = Path(payload["spec_dir"])
    assert spec_dir.exists()
    # All 6 markdown docs present
    for name in DOC_FILENAMES:
        assert (spec_dir / name).exists(), f"{name} missing"
    # .config.json present
    assert (spec_dir / ".config.json").exists()


def test_spec_init_config_json_initial_state(
    run_script, doc_root, fake_home, make_session_id
):
    sid = make_session_id()
    cp = run_script(
        "spec_init.py",
        "--name", "beta-spec",
        "--requirement-name", "Beta",
        "--source-text", "需求 B",
        "--session", sid,
    )
    assert cp.returncode == 0, cp.stderr
    payload = json.loads(cp.stdout)
    spec_dir = Path(payload["spec_dir"])
    cfg = json.loads((spec_dir / ".config.json").read_text())
    assert cfg["specId"] == payload["specId"]
    assert cfg["slug"] == "beta-spec"
    assert cfg["phase"] == "intake"
    assert cfg["pending_selector"] == "workflow-choice"
    assert cfg["workflow"] is None
    # lock initially held by the initialising session
    assert cfg["lock"]["holder"] == sid
    assert cfg["doc_root"] == str(doc_root)
    assert cfg["source_text"] == "需求 B"


def test_spec_init_writes_sessions_file(
    run_script, doc_root, fake_home, make_session_id
):
    sid = make_session_id()
    cp = run_script(
        "spec_init.py",
        "--name", "gamma",
        "--requirement-name", "Gamma",
        "--source-text", "需求 C",
        "--session", sid,
    )
    assert cp.returncode == 0, cp.stderr
    sess_path = fake_home / ".specode" / "sessions" / f"{sid}.json"
    assert sess_path.exists()
    sess = json.loads(sess_path.read_text())
    assert sess["session_id"] == sid
    assert sess["mode"] == "active"
    assert sess["active_spec_slug"] == "gamma"
    assert sess["phase"] == "intake"
    assert sess["pending_selector"] == "workflow-choice"
    assert sess["lock_state"] == "ok"


def test_spec_init_updates_active_pointer(
    run_script, doc_root, fake_home, make_session_id
):
    sid = make_session_id()
    cp = run_script(
        "spec_init.py",
        "--name", "delta",
        "--requirement-name", "Delta",
        "--source-text", "需求 D",
        "--session", sid,
    )
    assert cp.returncode == 0, cp.stderr
    ptr_path = doc_root / ".active-specode.json"
    assert ptr_path.exists()
    ptr = json.loads(ptr_path.read_text())
    assert ptr["active_spec_slug"] == "delta"
    assert ptr["session_id"] == sid
    assert ptr["specId"] == json.loads(cp.stdout)["specId"]


def test_spec_init_missing_root_exits_3(
    run_script, fake_home, monkeypatch, make_session_id
):
    """All three tiers miss → exit 3 + stderr hint."""
    monkeypatch.delenv("SPECODE_ROOT", raising=False)
    sid = make_session_id()
    cp = run_script(
        "spec_init.py",
        "--name", "no-root",
        "--requirement-name", "NoRoot",
        "--source-text", "x",
        "--session", sid,
    )
    assert cp.returncode == 3
    assert "doc_root" in cp.stderr or "vault" in cp.stderr


def test_spec_init_duplicate_slug_refuses(
    run_script, doc_root, fake_home, make_session_id
):
    """Re-running with the same slug must fail without clobbering existing data."""
    sid1 = make_session_id()
    cp1 = run_script(
        "spec_init.py",
        "--name", "dupe",
        "--requirement-name", "First",
        "--source-text", "first",
        "--session", sid1,
    )
    assert cp1.returncode == 0, cp1.stderr
    first_cfg = json.loads(
        (doc_root / "specs" / "dupe" / ".config.json").read_text()
    )

    sid2 = make_session_id()
    cp2 = run_script(
        "spec_init.py",
        "--name", "dupe",
        "--requirement-name", "Second",
        "--source-text", "second",
        "--session", sid2,
    )
    assert cp2.returncode == 3
    assert "已存在" in cp2.stderr
    # The original config is untouched
    again_cfg = json.loads(
        (doc_root / "specs" / "dupe" / ".config.json").read_text()
    )
    assert again_cfg["specId"] == first_cfg["specId"]


def test_spec_init_missing_session_arg_errors(run_script, doc_root, fake_home):
    """argparse should reject when --session is missing."""
    cp = run_script(
        "spec_init.py",
        "--name", "no-session",
        "--requirement-name", "NoSession",
        "--source-text", "x",
    )
    # argparse exits 2 for missing required args
    assert cp.returncode != 0
    assert "session" in cp.stderr.lower()


def test_spec_init_rejects_invalid_slug(run_script, doc_root, fake_home, make_session_id):
    """Slug must be lowercase alnum + hyphen; CamelCase rejected with exit 3."""
    sid = make_session_id()
    cp = run_script(
        "spec_init.py",
        "--name", "BadSlug",
        "--requirement-name", "Bad",
        "--source-text", "x",
        "--session", sid,
    )
    assert cp.returncode == 3
    assert "slug" in cp.stderr.lower() or "非法" in cp.stderr


def test_spec_init_root_override_wins(
    run_script, fake_home, tmp_path, make_session_id, monkeypatch
):
    """--root flag overrides env."""
    env_root = fake_home / "env-root"
    env_root.mkdir()
    monkeypatch.setenv("SPECODE_ROOT", str(env_root))
    cli_root = tmp_path / "cli-root"
    cli_root.mkdir()
    sid = make_session_id()
    cp = run_script(
        "spec_init.py",
        "--name", "override-spec",
        "--requirement-name", "OverrideSpec",
        "--source-text", "x",
        "--session", sid,
        "--root", str(cli_root),
    )
    assert cp.returncode == 0, cp.stderr
    payload = json.loads(cp.stdout)
    assert payload["doc_root_source"] == "override"
    assert Path(payload["spec_dir"]).is_relative_to(cli_root)
