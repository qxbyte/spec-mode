"""Snapshot-style tests for the 11 SELECTOR_PROMPTS templates.

We don't import SELECTOR_PROMPTS directly — we drive each key end-to-end
through `on-user-prompt`, parse the additionalContext, and assert that the
template's most distinctive substrings are present.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


def _write_session(fake_home: Path, sid: str, **overrides) -> Path:
    sess_dir = fake_home / ".specode" / "sessions"
    sess_dir.mkdir(parents=True, exist_ok=True)
    base = {
        "session_id": sid,
        "started_at": "2026-01-01T00:00:00Z",
        "last_activity_at": "2026-01-01T00:00:00Z",
        "ended_at": None,
        "mode": "active",
        "active_spec_slug": "snap-spec",
        "active_spec_dir": None,
        "spec_id": "snap",
        "phase": "intake",
        "lock_state": "ok",
        "task_swarm_run_id": None,
        "pending_selector": None,
    }
    base.update(overrides)
    p = sess_dir / f"{sid}.json"
    p.write_text(json.dumps(base), encoding="utf-8")
    return p


def _write_spec(doc_root: Path, slug: str, **overrides) -> Path:
    spec_dir = doc_root / "specs" / slug
    spec_dir.mkdir(parents=True, exist_ok=True)
    base = {
        "specId": "snap-id",
        "slug": slug,
        "phase": "intake",
        "workflow": None,
        "pending_selector": None,
        "lock": {"holder": "self"},
        "source_text": "示例需求文本摘要内容内容内容",
    }
    base.update(overrides)
    (spec_dir / ".config.json").write_text(
        json.dumps(base), encoding="utf-8"
    )
    return spec_dir


def _fetch_ctx(run_script, fake_home, sid: str, prompt: str = "go") -> str:
    cp = run_script(
        "spec_session.py", "on-user-prompt",
        stdin=json.dumps({"session_id": sid, "prompt": prompt})
    )
    assert cp.returncode == 0, cp.stderr
    s = cp.stdout.strip()
    assert s, "expected non-empty hook output"
    payload = json.loads(s)
    return payload["hookSpecificOutput"]["additionalContext"]


# --------------------------------------------------------------------------
# Type-A single-column selectors
# --------------------------------------------------------------------------

@pytest.fixture
def selector_setup(fake_home, doc_root, make_session_id):
    """Factory: configures a session+spec with the given pending_selector and phase."""
    def _setup(pending: str, phase: str = "intake", slug: str = "snap-spec",
               extra_cfg: dict = None) -> str:
        sid = make_session_id()
        cfg_extra = {"pending_selector": pending, "phase": phase}
        if extra_cfg:
            cfg_extra.update(extra_cfg)
        spec_dir = _write_spec(doc_root, slug, **cfg_extra)
        _write_session(
            fake_home, sid,
            mode="active",
            active_spec_slug=slug,
            active_spec_dir=str(spec_dir),
            phase=phase,
            pending_selector=pending,
        )
        return sid
    return _setup


def test_workflow_choice_snapshot(run_script, fake_home, selector_setup):
    sid = selector_setup("workflow-choice", phase="intake")
    ctx = _fetch_ctx(run_script, fake_home, sid)
    assert "选择器节点：工作流选择" in ctx
    assert "Requirements first" in ctx
    assert "Technical Design first" in ctx
    assert "Bugfix" in ctx
    # 改为 AskUserQuestion 工具协议 + YAML 缩进格式
    assert "AskUserQuestion" in ctx
    assert "multiSelect: false" in ctx
    assert "label:" in ctx
    assert "options:" in ctx
    # 三段式结构（目的/前置动作/约束）
    assert "**目的**" in ctx or "目的" in ctx
    assert "**约束**" in ctx or "约束" in ctx
    # 显式断言"禁止保留位"措辞存在
    assert "Type something" in ctx  # 在禁区说明里出现
    assert "Other" in ctx


def test_clarification_wizard_snapshot(run_script, fake_home, selector_setup):
    sid = selector_setup("clarification-wizard", phase="intake")
    ctx = _fetch_ctx(run_script, fake_home, sid)
    assert "选择器节点：需求澄清问答" in ctx
    assert "wizard" in ctx
    assert "AskUserQuestion" in ctx
    assert "multiSelect: false" in ctx  # wizard 内每个 question 都是单选
    assert "wizard" in ctx
    assert "决策点" in ctx


def test_clarification_done_snapshot(run_script, fake_home, selector_setup):
    sid = selector_setup("clarification-done", phase="intake")
    ctx = _fetch_ctx(run_script, fake_home, sid)
    assert "需求澄清是否完成？" in ctx
    assert "进入下一阶段（推荐）" in ctx
    assert "继续澄清" in ctx


def test_doc_confirm_requirements_snapshot(run_script, fake_home, selector_setup):
    sid = selector_setup("doc-confirm-requirements", phase="requirements")
    ctx = _fetch_ctx(run_script, fake_home, sid)
    assert "requirements.md 文档确认" in ctx
    assert "确认（推荐）" in ctx
    assert "查看全文" in ctx
    assert "继续沟通" in ctx


def test_doc_confirm_bugfix_snapshot(run_script, fake_home, selector_setup):
    sid = selector_setup("doc-confirm-bugfix", phase="bugfix")
    ctx = _fetch_ctx(run_script, fake_home, sid)
    assert "bugfix.md 文档确认" in ctx
    assert "确认（推荐）" in ctx
    assert "查看全文" in ctx
    assert "继续沟通" in ctx


def test_doc_confirm_design_snapshot(run_script, fake_home, selector_setup):
    sid = selector_setup("doc-confirm-design", phase="design")
    ctx = _fetch_ctx(run_script, fake_home, sid)
    assert "design.md 文档确认" in ctx
    assert "确认（推荐）" in ctx
    assert "查看全文" in ctx
    assert "继续沟通" in ctx


def test_doc_confirm_tasks_snapshot(run_script, fake_home, selector_setup):
    sid = selector_setup("doc-confirm-tasks", phase="tasks")
    ctx = _fetch_ctx(run_script, fake_home, sid)
    assert "tasks.md 文档确认" in ctx
    assert "确认（推荐）" in ctx
    assert "查看全文" in ctx
    assert "继续沟通" in ctx


def test_tasks_execution_snapshot(run_script, fake_home, selector_setup):
    sid = selector_setup("tasks-execution", phase="tasks")
    ctx = _fetch_ctx(run_script, fake_home, sid)
    assert "任务执行选择" in ctx
    assert "开始 required" in ctx
    assert "用 task-swarm 多 agent 并发" in ctx
    assert "暂不 coding" in ctx


def test_takeover_options_snapshot(run_script, fake_home, selector_setup):
    sid = selector_setup("takeover-options", phase="design")
    ctx = _fetch_ctx(run_script, fake_home, sid)
    assert "强制接管" in ctx
    assert "只读查看" in ctx
    assert "取消" in ctx


def test_acceptance_gate_snapshot(run_script, fake_home, selector_setup):
    sid = selector_setup("acceptance-gate", phase="acceptance")
    ctx = _fetch_ctx(run_script, fake_home, sid)
    assert "验收结论" in ctx
    assert "验收通过，进入 iteration" in ctx
    assert "继续修改" in ctx


def test_iteration_scope_snapshot(run_script, fake_home, selector_setup):
    sid = selector_setup("iteration-scope", phase="iteration")
    ctx = _fetch_ctx(run_script, fake_home, sid)
    assert "iteration 调整范围" in ctx
    assert "改 requirements" in ctx
    assert "改 design" in ctx
    assert "改 tasks" in ctx
    assert "重跑测试" in ctx
    # 类型 C 关键：multiSelect=true
    assert "AskUserQuestion" in ctx
    assert "multiSelect: true" in ctx
