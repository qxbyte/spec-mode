"""Unit tests for task_swarm_guard (INV-7/8/9/10)."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import task_swarm_guard as G  # noqa: E402
import task_swarm_prompt as PR  # noqa: E402


# ---------- INV-7 ----------

def test_inv7_accepts_prefixed_types():
    for r in ("coder", "reviewer", "validator", "planner"):
        decision, _ = G.check_inv7_subagent_type(f"specode:task-swarm-{r}")
        assert decision == "ok"


def test_inv7_rejects_general_purpose():
    decision, msg = G.check_inv7_subagent_type("general-purpose")
    assert decision == "deny"
    assert "INV-7" in msg
    assert "general-purpose" in msg


def test_inv7_rejects_missing_prefix():
    decision, msg = G.check_inv7_subagent_type("task-swarm-coder")
    assert decision == "deny"
    assert "specode:" in msg


def test_inv7_rejects_empty():
    decision, _ = G.check_inv7_subagent_type("")
    assert decision == "deny"


# ---------- INV-8 ----------

def _make_subagent_ws(tmp: Path, writes: list[str]) -> tuple[Path, Path, Path]:
    """Build a fake subagent workspace with task.md declaring writes.

    Returns (workspace, project_root, spec_dir).
    """
    proj = tmp / "project"
    proj.mkdir()
    spec = tmp / "spec-dir"
    spec.mkdir()
    run_dir = proj / ".task-swarm" / "runs" / "run-1"
    ws = run_dir / "agents" / "stage-1-coder"
    (ws / "inbox").mkdir(parents=True)
    (ws / "outbox").mkdir(parents=True)
    writes_line = ", ".join(writes) if writes else "(本阶段无 @writes 声明文件)"
    task_md = (
        "## 边界\n"
        f"- 项目根: {proj}\n"
        f"- @writes（你只能修改这些路径）: {writes_line}\n"
    )
    (ws / "task.md").write_text(task_md, encoding="utf-8")
    return ws, proj, spec


def test_inv8_allows_writes_listed_file():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        ws, proj, spec = _make_subagent_ws(tmp, ["src/a.py", "src/b.py"])
        target = proj / "src/a.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x")
        decision, _ = G.check_inv8_writes_boundary(target, ws, proj, spec)
        assert decision == "ok"


def test_inv8_rejects_unlisted_file():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        ws, proj, spec = _make_subagent_ws(tmp, ["src/a.py"])
        target = proj / "src/other.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x")
        decision, msg = G.check_inv8_writes_boundary(target, ws, proj, spec)
        assert decision == "deny"
        assert "INV-8" in msg


def test_inv8_rejects_spec_dir_writes():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        ws, proj, spec = _make_subagent_ws(tmp, ["src/a.py"])
        target = spec / "requirements.md"
        target.write_text("x")
        decision, msg = G.check_inv8_writes_boundary(target, ws, proj, spec)
        assert decision == "deny"
        assert "spec 文档" in msg


def test_inv8_allows_outbox_writes():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        ws, proj, spec = _make_subagent_ws(tmp, ["src/a.py"])
        target = ws / "outbox" / "result.md"
        decision, _ = G.check_inv8_writes_boundary(target, ws, proj, spec)
        assert decision == "ok"


# ---------- R4: directory-form @writes ----------

def test_inv8_dir_with_trailing_slash_allows_child():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        ws, proj, spec = _make_subagent_ws(tmp, ["src/api/"])
        target = proj / "src/api/__init__.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("")
        decision, _ = G.check_inv8_writes_boundary(target, ws, proj, spec)
        assert decision == "ok"


def test_inv8_dir_glob_allows_nested():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        ws, proj, spec = _make_subagent_ws(tmp, ["src/api/**"])
        target = proj / "src/api/v1/users.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("")
        decision, _ = G.check_inv8_writes_boundary(target, ws, proj, spec)
        assert decision == "ok"


def test_inv8_dir_does_not_match_sibling():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        ws, proj, spec = _make_subagent_ws(tmp, ["src/api/"])
        target = proj / "src/admin/users.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("")
        decision, msg = G.check_inv8_writes_boundary(target, ws, proj, spec)
        assert decision == "deny"
        assert "INV-8" in msg


# ---------- INV-9 ----------

OLD_TASKS = """\
- [ ] 1. 实现登录
  - [ ] 1.1 写 model
    - 文件：src/m.py
    - _需求：1.1_

- [ ] 2. 检查点
"""


def test_inv9_allows_checkbox_swap():
    new = OLD_TASKS.replace("- [ ] 1.1", "- [x] 1.1")
    decision, _ = G.check_inv9_tasks_md_diff(OLD_TASKS, new)
    assert decision == "ok"


def test_inv9_allows_annotation_insert():
    new = OLD_TASKS.replace(
        "- [ ] 2. 检查点\n",
        "- [ ] 2. 检查点\n  > ✔ task-swarm 收敛\n",
    )
    decision, _ = G.check_inv9_tasks_md_diff(OLD_TASKS, new)
    assert decision == "ok"


def test_inv9_rejects_traceability_change():
    new = OLD_TASKS.replace("_需求：1.1_", "_需求：1.2_")
    decision, msg = G.check_inv9_tasks_md_diff(OLD_TASKS, new)
    assert decision == "deny"
    assert "INV-9" in msg


def test_inv9_rejects_file_metadata_change():
    new = OLD_TASKS.replace("文件：src/m.py", "文件：src/n.py")
    decision, _ = G.check_inv9_tasks_md_diff(OLD_TASKS, new)
    assert decision == "deny"


def test_inv9_rejects_title_change():
    new = OLD_TASKS.replace("写 model", "写 model V2")
    decision, _ = G.check_inv9_tasks_md_diff(OLD_TASKS, new)
    assert decision == "deny"


def test_inv9_rejects_arbitrary_line_insert():
    new = OLD_TASKS.replace(
        "- [ ] 1.1 写 model\n",
        "- [ ] 1.1 写 model\n    - 新增的设计说明\n",
    )
    decision, _ = G.check_inv9_tasks_md_diff(OLD_TASKS, new)
    assert decision == "deny"


# ---------- INV-10 ----------

def test_inv10_passes_valid_outbox():
    with tempfile.TemporaryDirectory() as td:
        outbox = Path(td)
        (outbox / "result.md").write_text(
            "## 子任务状态\n- 1.1 写 a: done — src/a.py\n\n## 关键变更\n- x\n\nSTATUS: ok\n"
        )
        decision, _ = G.check_inv10_outbox_schema("coder", outbox)
        assert decision == "ok"


def test_inv10_rejects_missing_subtask_section():
    with tempfile.TemporaryDirectory() as td:
        outbox = Path(td)
        (outbox / "result.md").write_text("## 关键变更\n- x\n\nSTATUS: ok\n")
        decision, msg = G.check_inv10_outbox_schema("coder", outbox)
        assert decision == "deny"
        assert "INV-10" in msg


def test_inv10_rejects_missing_p0_section():
    with tempfile.TemporaryDirectory() as td:
        outbox = Path(td)
        (outbox / "review.md").write_text("## 结论\napproved\n\nSTATUS: ok\n")
        decision, msg = G.check_inv10_outbox_schema("reviewer", outbox)
        assert decision == "deny"


def test_inv10_rejects_validator_fail_without_guidance():
    with tempfile.TemporaryDirectory() as td:
        outbox = Path(td)
        (outbox / "validation.md").write_text(
            "## 判定\nfail\n\n## 复现命令\n```bash\nx\n```\n\nSTATUS: ok\n"
        )
        decision, msg = G.check_inv10_outbox_schema("validator", outbox)
        assert decision == "deny"


# ---------- active-run discovery ----------

def test_active_run_detection():
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td)
        assert G.is_task_swarm_active(proj) is False
        (proj / ".task-swarm").mkdir()
        (proj / ".task-swarm" / "active-run").write_text("run-x")
        (proj / ".task-swarm" / "runs" / "run-x").mkdir(parents=True)
        assert G.is_task_swarm_active(proj) is True
