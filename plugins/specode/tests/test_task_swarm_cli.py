"""tests for task_swarm.py — CLI 子命令端到端。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path("/Users/xueqiang/Git/specode/plugins/specode/scripts")


@pytest.fixture
def run_swarm(tmp_path, monkeypatch):
    """运行 task_swarm.py CLI，cwd 设到 tmp_path 让 .task-swarm 目录可解析。"""
    monkeypatch.chdir(tmp_path)
    # 与 conftest.fake_home 一致：HOME 也指向 tmp 避免污染
    monkeypatch.setenv("HOME", str(tmp_path / "_home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "_home"))

    def _run(*args: str, stdin: str = "") -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env["HOME"] = str(tmp_path / "_home")
        env["USERPROFILE"] = str(tmp_path / "_home")
        cmd = [sys.executable, str(SCRIPTS_DIR / "task_swarm.py"), *args]
        return subprocess.run(cmd, capture_output=True, text=True,
                              input=stdin, env=env, timeout=30, cwd=str(tmp_path))
    return _run


def _write_tasks_md(tmp_path: Path, num_stages: int = 2) -> Path:
    p = tmp_path / "tasks.md"
    lines = []
    for i in range(1, num_stages + 1):
        lines.append(f"## 阶段 {i}: 阶段 {i}")
        lines.append(f"- [ ] {i}.1 任务 @writes:src/f{i}.py _需求：{i}.1_")
        if i > 1:
            lines.append(f"- [ ] {i}.2 任务2 @writes:src/g{i}.py @depends-on:{i-1} _需求：{i}.2_")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def _write_coder_result(run_dir: Path, agent_key: str, status: str = "ok") -> None:
    outbox = run_dir / "agents" / agent_key / "outbox"
    outbox.mkdir(parents=True, exist_ok=True)
    (outbox / "result.md").write_text(
        "# c\n## 上下文\n- x\n## 子任务状态\n- 1.1 t: done — f.py\n## 关键变更\n- a\n\n"
        f"STATUS: {status}\n",
        encoding="utf-8",
    )


def _write_reviewer(run_dir: Path, group: int, with_p0: bool = True) -> None:
    out = run_dir / "agents" / f"reviewer-g{group}-r1" / "outbox"
    out.mkdir(parents=True, exist_ok=True)
    p0_section = ("## P0\n- src/f1.py:5 [req:1.1] — issue\n\n"
                  if with_p0 else "## P0\n(none)\n\n")
    (out / "review.md").write_text(
        "# rev\n## 结论\napproved-with-comments\n\n"
        + p0_section
        + "## P1\n## P2\nSTATUS: ok\n",
        encoding="utf-8",
    )


def _write_validator(run_dir: Path, group: int, round_: int, verdict: str = "pass",
                     sig_marker: str = "default") -> None:
    out = run_dir / "agents" / f"validator-g{group}-r{round_}" / "outbox"
    out.mkdir(parents=True, exist_ok=True)
    if verdict == "pass":
        body = ("# v\n## 判定\npass\n## 复现命令\n```bash\npytest\n```\n"
                "## 按子任务的验证结果\n- [x] 1.1 t: pass\n\nSTATUS: ok\n")
    else:
        body = ("# v\n## 判定\nfail\n## 复现命令\n```bash\npytest\n```\n"
                "## 按子任务的验证结果\n- [ ] 1.1 t: fail\n"
                f"## 失败现场\n```\nFAILED tests/t.py::test_{sig_marker}\nAssertionError: x\n```\n"
                "## 给 coder 的修复指引\n### 修复 1\n- 文件: src/f1.py\n- 位置: x\n"
                "- 问题: y\n- 建议: z\n\nSTATUS: ok\n")
    (out / "validation.md").write_text(body, encoding="utf-8")


# -------------------------------------------------------------------------
# 测试
# -------------------------------------------------------------------------

def test_init_creates_state_and_groups(tmp_path, run_swarm):
    p = _write_tasks_md(tmp_path, num_stages=2)
    cp = run_swarm("init", "--tasks", str(p))
    assert cp.returncode == 0, cp.stderr
    out = json.loads(cp.stdout)
    assert "run_id" in out
    assert len(out["groups"]) >= 1
    run_dir = Path(out["run_dir"])
    assert (run_dir / "state.json").exists()


def test_init_with_nonexistent_tasks_exits_1(tmp_path, run_swarm):
    cp = run_swarm("init", "--tasks", str(tmp_path / "no.md"))
    assert cp.returncode == 1


def test_init_empty_tasks_md_exits_1(tmp_path, run_swarm):
    p = tmp_path / "tasks.md"
    p.write_text("# nothing\n", encoding="utf-8")
    cp = run_swarm("init", "--tasks", str(p))
    assert cp.returncode == 1


def test_plan_initial_returns_coding_fork(tmp_path, run_swarm):
    p = _write_tasks_md(tmp_path, num_stages=1)
    init = json.loads(run_swarm("init", "--tasks", str(p)).stdout)
    cp = run_swarm("plan", "--run", init["run_id"])
    assert cp.returncode == 0
    plan = json.loads(cp.stdout)
    assert plan["phase"] == "coding"
    assert plan["action"] == "coding-fork"
    assert plan["fork"]


def test_status_reports_phase(tmp_path, run_swarm):
    p = _write_tasks_md(tmp_path, num_stages=1)
    init = json.loads(run_swarm("init", "--tasks", str(p)).stdout)
    cp = run_swarm("status", "--run", init["run_id"])
    assert cp.returncode == 0
    st = json.loads(cp.stdout)
    assert st["run_id"] == init["run_id"]
    assert st["phase"] in ("init", "coding")


def test_advance_coding_then_review_fork(tmp_path, run_swarm):
    p = _write_tasks_md(tmp_path, num_stages=1)
    init = json.loads(run_swarm("init", "--tasks", str(p)).stdout)
    run_dir = Path(init["run_dir"])
    # plan to materialize prompts + transition to coding
    run_swarm("plan", "--run", init["run_id"])
    _write_coder_result(run_dir, "coder-g1-s1-r1")
    cp = run_swarm("advance", "--run", init["run_id"], "--phase", "coding", "--round", "1")
    assert cp.returncode == 0
    out = json.loads(cp.stdout)
    assert out["ok"]
    assert out["plan"]["action"] == "review-fork"


def test_full_cycle_no_p0_pass(tmp_path, run_swarm):
    """coding → review (no P0) → validation (pass) → writeback"""
    p = _write_tasks_md(tmp_path, num_stages=1)
    init = json.loads(run_swarm("init", "--tasks", str(p)).stdout)
    run_id = init["run_id"]
    run_dir = Path(init["run_dir"])
    run_swarm("plan", "--run", run_id)
    _write_coder_result(run_dir, "coder-g1-s1-r1")
    run_swarm("advance", "--run", run_id, "--phase", "coding", "--round", "1")
    _write_reviewer(run_dir, 1, with_p0=False)
    run_swarm("advance", "--run", run_id, "--phase", "review", "--round", "1")
    _write_validator(run_dir, 1, 1, verdict="pass")
    cp = run_swarm("advance", "--run", run_id, "--phase", "validation", "--round", "1")
    out = json.loads(cp.stdout)
    assert out["plan"]["action"] == "writeback"
    cp = run_swarm("writeback", "--run", run_id, "--group", "1")
    assert cp.returncode == 0, cp.stderr
    text = p.read_text(encoding="utf-8")
    assert "- [x] 1.1" in text


def test_full_cycle_with_p0_fix(tmp_path, run_swarm):
    p = _write_tasks_md(tmp_path, num_stages=1)
    init = json.loads(run_swarm("init", "--tasks", str(p)).stdout)
    run_id = init["run_id"]
    run_dir = Path(init["run_dir"])
    run_swarm("plan", "--run", run_id)
    _write_coder_result(run_dir, "coder-g1-s1-r1")
    run_swarm("advance", "--run", run_id, "--phase", "coding", "--round", "1")
    _write_reviewer(run_dir, 1, with_p0=True)
    run_swarm("advance", "--run", run_id, "--phase", "review", "--round", "1")
    # p0-fix coder
    _write_coder_result(run_dir, "coder-p0fix-g1-r1-f0")
    cp = run_swarm("advance", "--run", run_id, "--phase", "p0-fix", "--round", "1")
    out = json.loads(cp.stdout)
    assert out["plan"]["phase"] == "validation"


def test_validation_fail_triggers_v_fix(tmp_path, run_swarm):
    p = _write_tasks_md(tmp_path, num_stages=1)
    init = json.loads(run_swarm("init", "--tasks", str(p)).stdout)
    run_id = init["run_id"]
    run_dir = Path(init["run_dir"])
    run_swarm("plan", "--run", run_id)
    _write_coder_result(run_dir, "coder-g1-s1-r1")
    run_swarm("advance", "--run", run_id, "--phase", "coding", "--round", "1")
    _write_reviewer(run_dir, 1, with_p0=False)
    run_swarm("advance", "--run", run_id, "--phase", "review", "--round", "1")
    _write_validator(run_dir, 1, 1, verdict="fail", sig_marker="first")
    cp = run_swarm("advance", "--run", run_id, "--phase", "validation", "--round", "1")
    out = json.loads(cp.stdout)
    assert out["plan"]["phase"] == "v-fix"


def test_deadloop_after_3_identical_fails(tmp_path, run_swarm):
    p = _write_tasks_md(tmp_path, num_stages=1)
    init = json.loads(run_swarm("init", "--tasks", str(p)).stdout)
    run_id = init["run_id"]
    run_dir = Path(init["run_dir"])
    run_swarm("plan", "--run", run_id)
    _write_coder_result(run_dir, "coder-g1-s1-r1")
    run_swarm("advance", "--run", run_id, "--phase", "coding", "--round", "1")
    _write_reviewer(run_dir, 1, with_p0=False)
    run_swarm("advance", "--run", run_id, "--phase", "review", "--round", "1")
    # 3 同样的 fail
    for r in range(1, 4):
        _write_validator(run_dir, 1, r, verdict="fail", sig_marker="same")
        cp = run_swarm("advance", "--run", run_id, "--phase", "validation", "--round", str(r))
        out = json.loads(cp.stdout)
        if r < 3:
            # v-fix coder 返回
            v_round = r + 1
            files = ["src/f1.py"]
            for i, _f in enumerate(files):
                key = f"coder-vfix-g1-r{v_round}-f{i}"
                _write_coder_result(run_dir, key)
            run_swarm("advance", "--run", run_id, "--phase", "v-fix", "--round", str(v_round))
    assert "deadloop" in out.get("next", "") or out.get("deadloop") is True


def test_writeback_invalid_group_returns_1(tmp_path, run_swarm):
    p = _write_tasks_md(tmp_path, num_stages=1)
    init = json.loads(run_swarm("init", "--tasks", str(p)).stdout)
    cp = run_swarm("writeback", "--run", init["run_id"], "--group", "99")
    assert cp.returncode == 1


def test_heartbeat_updates_state(tmp_path, run_swarm):
    p = _write_tasks_md(tmp_path, num_stages=1)
    init = json.loads(run_swarm("init", "--tasks", str(p)).stdout)
    cp = run_swarm("heartbeat", "--run", init["run_id"])
    assert cp.returncode == 0
    out = json.loads(cp.stdout)
    assert out["run_id"] == init["run_id"]


def test_resolve_abort_sets_status(tmp_path, run_swarm):
    p = _write_tasks_md(tmp_path, num_stages=1)
    init = json.loads(run_swarm("init", "--tasks", str(p)).stdout)
    cp = run_swarm("resolve", "--run", init["run_id"], "--abort")
    assert cp.returncode == 0
    out = json.loads(cp.stdout)
    assert out["status"] == "aborted"


def test_resolve_clears_session_task_swarm_run_id(tmp_path, run_swarm, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "_home"))
    sessions_dir = tmp_path / "_home" / ".specode" / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    sid = "test-sess-123"
    sess_file = sessions_dir / f"{sid}.json"
    sess_file.write_text(json.dumps({"session_id": sid, "mode": "active",
                                     "task_swarm_run_id": None}), encoding="utf-8")
    p = _write_tasks_md(tmp_path, num_stages=1)
    init = json.loads(run_swarm("init", "--tasks", str(p), "--session", sid).stdout)
    # 验证 init 写了 task_swarm_run_id
    saved = json.loads(sess_file.read_text(encoding="utf-8"))
    assert saved["task_swarm_run_id"] == init["run_id"]
    # resolve 后应清空
    run_swarm("resolve", "--run", init["run_id"])
    saved2 = json.loads(sess_file.read_text(encoding="utf-8"))
    assert saved2["task_swarm_run_id"] is None
