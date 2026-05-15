"""Unit tests for spec_sync.py — tasks_files extraction and decision functions."""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import spec_sync


def test_extract_tasks_files_from_FILE_lines(tmp_path):
    spec = tmp_path / "spec"
    spec.mkdir()
    (spec / "tasks.md").write_text(
        "# Tasks\n"
        "- [ ] FILE: src/auth/middleware.py\n"
        "- [x] FILE: src/auth/session.py\n"
        "- [ ] FILE：tests/auth/test_middleware.py\n"  # full-width 冒号
        "- [ ] FILE: src/auth/glob/**/*.py\n"
    )
    out = spec_sync.extract_tasks_files(spec)
    assert "src/auth/middleware.py" in out
    assert "src/auth/session.py" in out
    assert "tests/auth/test_middleware.py" in out
    assert "src/auth/glob/**/*.py" in out


def test_extract_tasks_files_from_affected_section(tmp_path):
    spec = tmp_path / "spec"
    spec.mkdir()
    (spec / "design.md").write_text(
        "# Design\n\n## Affected Files\n\n- `src/foo.py`\n- src/bar.py\n\n## Next\n- ignored\n"
    )
    out = spec_sync.extract_tasks_files(spec)
    assert "src/foo.py" in out
    assert "src/bar.py" in out
    assert "ignored" not in out


def test_matches_tasks_files_literal_and_glob(tmp_path):
    proj = tmp_path / "proj"
    (proj / "src" / "auth").mkdir(parents=True)
    target_literal = proj / "src" / "foo.py"
    target_glob = proj / "src" / "auth" / "middleware.py"
    target_literal.touch()
    target_glob.touch()

    assert spec_sync.matches_tasks_files(target_literal, ["src/foo.py"], proj)
    assert spec_sync.matches_tasks_files(target_glob, ["src/auth/**/*.py"], proj)
    assert not spec_sync.matches_tasks_files(target_literal, ["src/bar.py"], proj)


def test_classify_path_spec_doc_vs_project_code_vs_outside(tmp_path):
    spec = tmp_path / "spec"
    proj = tmp_path / "proj"
    spec.mkdir()
    proj.mkdir()
    (spec / "design.md").touch()
    (proj / "src.py").touch()
    outside = Path("/tmp/literally-outside.txt")

    assert spec_sync.classify_path(spec / "design.md", spec, proj) == "spec-doc"
    assert spec_sync.classify_path(proj / "src.py", spec, proj) == "project-code"
    assert spec_sync.classify_path(outside, spec, proj) == "outside"


def test_check_phase_gate_forbids_pre_implementation():
    for phase in ("intake", "requirements", "design", "tasks", "bugfix"):
        decision, msg = spec_sync.check_phase_gate(phase)
        assert decision == "deny"
        assert "INV-6" in msg
        assert phase in msg

    for phase in ("implementation", "acceptance", "iteration", "ended"):
        decision, _ = spec_sync.check_phase_gate(phase)
        assert decision == "ok"


def test_check_stop_inv2_and_inv4():
    # Empty ledger → ok
    ledger = spec_sync._new_ledger(Path("/tmp/x"))
    assert spec_sync.check_stop(ledger) == []

    # Code-only → INV-2
    ledger = spec_sync._new_ledger(Path("/tmp/x"))
    spec_sync.append_change(ledger, "code", "/proj/src/foo.py", "Edit")
    violations = spec_sync.check_stop(ledger)
    assert any(v["id"] == "INV-2" for v in violations)

    # Code + doc → ok
    spec_sync.append_change(ledger, "doc", "/spec/design.md", "Edit")
    assert spec_sync.check_stop(ledger) == []

    # Requirements w/o tasks.md → INV-4
    ledger = spec_sync._new_ledger(Path("/tmp/x"))
    spec_sync.append_change(ledger, "doc", "/spec/requirements.md", "Edit")
    violations = spec_sync.check_stop(ledger)
    assert any(v["id"] == "INV-4" for v in violations)

    # Requirements + tasks.md → ok (测试要点 lives in tasks.md)
    spec_sync.append_change(ledger, "doc", "/spec/tasks.md", "Edit")
    assert spec_sync.check_stop(ledger) == []

    # Bugfix w/o tasks.md → INV-4
    ledger = spec_sync._new_ledger(Path("/tmp/x"))
    spec_sync.append_change(ledger, "doc", "/spec/bugfix.md", "Edit")
    violations = spec_sync.check_stop(ledger)
    assert any(v["id"] == "INV-4" for v in violations)


def test_ledger_turn_lifecycle(tmp_path):
    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    ledger = spec_sync.read_ledger(spec_dir)
    assert ledger["turn_id"] is None
    spec_sync.start_new_turn(ledger, tmp_path, ["src/a.py"])
    assert ledger["turn_id"] is not None
    assert ledger["tasks_files"] == ["src/a.py"]
    spec_sync.append_change(ledger, "code", "src/a.py", "Edit")
    assert len(ledger["turn_code_changes"]) == 1
    spec_sync.reset_turn(ledger)
    assert ledger["turn_code_changes"] == []
