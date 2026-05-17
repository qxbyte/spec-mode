"""Unit tests for task_swarm_prompt rendering + workspace prep."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import task_swarm_prompt as PR  # noqa: E402


def _ctx(tmp: Path, *, round_no=1, scope="", kind="stage") -> PR.StageContext:
    ws = PR.prepare_workspace(tmp, 1, "coder", round_no)
    return PR.StageContext(
        stage_num=1,
        stage_title="实现登录",
        stage_kind=kind,
        leaves=[
            {"num": "1.1", "title": "写 user model", "files": ["src/models/user.py"], "requirement": "1.1", "verify": "pytest", "policy": "default"},
            {"num": "1.2", "title": "写 service", "files": ["src/auth/service.py"], "requirement": "1.2", "verify": "", "policy": "default"},
        ],
        spec_dir=tmp / "spec",
        project_root=tmp / "proj",
        workspace=ws,
        round_no=round_no,
        scope=scope,
    )


def test_workspace_name_first_round():
    assert PR.workspace_name(1, "coder", 1) == "stage-1-coder"


def test_workspace_name_fix_round():
    assert PR.workspace_name(3, "reviewer", 2) == "stage-3-reviewer-r2"


def test_prepare_workspace_creates_inbox_outbox():
    with tempfile.TemporaryDirectory() as td:
        run = Path(td)
        ws = PR.prepare_workspace(run, 1, "coder", 1)
        assert (ws / "inbox").is_dir()
        assert (ws / "outbox").is_dir()


def test_render_coder_initial_includes_writes_and_inbox():
    with tempfile.TemporaryDirectory() as td:
        ctx = _ctx(Path(td))
        text = PR.render_coder_prompt(ctx)
        assert "CODER" in text
        assert "src/models/user.py" in text
        assert "src/auth/service.py" in text
        # No fix-round guardrail on initial run
        assert "修复轮硬规则" not in text
        # Output protocol always present
        assert "STATUS: ok" in text


def test_render_coder_fix_round_includes_guardrail():
    with tempfile.TemporaryDirectory() as td:
        # R3: reviewer no longer drives fix rounds; only validator-fail-fix.
        ctx = _ctx(Path(td), round_no=2, scope="validator-fail-fix")
        text = PR.render_coder_prompt(ctx)
        assert "修复轮硬规则" in text
        assert "validation.md" in text
        assert "不要顺手优化" in text


def test_render_reviewer_no_edit_tools_warning():
    with tempfile.TemporaryDirectory() as td:
        ctx = _ctx(Path(td))
        text = PR.render_reviewer_prompt(ctx)
        assert "没有" in text and "Edit/Write" in text
        assert "## P0" in text


def test_render_reviewer_is_advisory():
    """R3: reviewer prompt must declare advisory mode + non-blocking role."""
    with tempfile.TemporaryDirectory() as td:
        ctx = _ctx(Path(td))
        text = PR.render_reviewer_prompt(ctx)
        assert "advisory" in text
        assert "不参与" in text or "不阻塞" in text
        assert "tasks.md" in text  # explains where the report ends up


def test_render_validator_checkpoint_marker():
    with tempfile.TemporaryDirectory() as td:
        ctx = _ctx(Path(td), kind="checkpoint")
        text = PR.render_validator_prompt(ctx)
        assert "检查点" in text
        assert "## 判定" in text
        assert "修复指引" in text


def test_relay_inbox_copies_outbox():
    with tempfile.TemporaryDirectory() as td:
        run = Path(td)
        coder_ws = PR.prepare_workspace(run, 1, "coder", 1)
        (coder_ws / "outbox" / "result.md").write_text("# result\n\nSTATUS: ok\n")

        reviewer_ws = PR.prepare_workspace(run, 1, "reviewer", 1)
        copied = PR.relay_inbox(run, reviewer_ws, [(1, "coder", 1, "result.md")])
        assert "result.md" in copied
        assert (reviewer_ws / "inbox" / "result.md").exists()


def test_write_task_file_persists_prompt():
    with tempfile.TemporaryDirectory() as td:
        ctx = _ctx(Path(td))
        p = PR.write_task_file(ctx, "coder")
        assert p.name == "task.md"
        assert "CODER" in p.read_text()
