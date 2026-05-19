#!/usr/bin/env python3
"""task_swarm_prompt.py — 预渲染 coder / reviewer / validator subagent 的 task.md。

按 references/task-swarm.md §4 规范输出，每个 prompt 必须含：
    - specId / spec_dir 上下文
    - @writes / @reads 边界
    - inbox 文件清单（指向 .task-swarm/runs/<id>/agents/<key>/inbox/）
    - outbox 路径
    - STATUS 输出协议

每个 prompt 渲染后写到：
    .task-swarm/runs/<run_id>/agents/<agent-key>/task.md

stdlib-only。
"""
from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path
from typing import Any, Optional


# -------------------------------------------------------------------------
# 原子写
# -------------------------------------------------------------------------

def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            with contextlib.suppress(OSError):
                os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


# -------------------------------------------------------------------------
# 通用上下文段
# -------------------------------------------------------------------------

def _context_block(spec_id: str, spec_dir: str, run_id: str, group: int, round_: int) -> str:
    return (
        f"## 上下文\n"
        f"- specId: {spec_id}\n"
        f"- spec_dir: {spec_dir}\n"
        f"- run_id: {run_id}\n"
        f"- group: {group}\n"
        f"- round: {round_}\n"
    )


def _agent_root(run_dir: Path, agent_key: str) -> Path:
    return run_dir / "agents" / agent_key


def _ensure_agent_dirs(run_dir: Path, agent_key: str) -> tuple[Path, Path, Path]:
    """创建 agent_root / inbox / outbox 三个目录，返回它们。"""
    root = _agent_root(run_dir, agent_key)
    inbox = root / "inbox"
    outbox = root / "outbox"
    inbox.mkdir(parents=True, exist_ok=True)
    outbox.mkdir(parents=True, exist_ok=True)
    return root, inbox, outbox


def _stage_writes(stage: Any) -> list[str]:
    """获取 stage 的 writes 列表（兼容 property / 字段两种形式）。"""
    w = getattr(stage, "writes", None)
    if w is None:
        return []
    if callable(w):
        try:
            w = w()
        except TypeError:
            return []
    return list(w)


def _stage_reads(stage: Any) -> list[str]:
    r = getattr(stage, "reads", None)
    if r is None:
        return []
    if callable(r):
        try:
            r = r()
        except TypeError:
            return []
    return list(r)


# -------------------------------------------------------------------------
# coder prompt
# -------------------------------------------------------------------------

def render_coder_prompt(
    stage: Any,  # StageEntry-like：number/title/items/writes/reads/requirements
    run_dir: Path,
    run_id: str,
    spec_id: str,
    spec_dir: str,
    group: int,
    round_: int = 1,
    mode: str = "initial",  # initial / p0-fix / v-fix
    fix_targets: Optional[list[dict]] = None,
    file_idx: Optional[int] = None,
) -> str:
    """渲染 coder 的 task.md。返回 prompt 文本。同步写到 agent 目录的 task.md。"""
    if mode == "initial":
        agent_key = f"coder-g{group}-s{stage.number}-r{round_}"
    elif mode == "p0-fix":
        agent_key = f"coder-p0fix-g{group}-r{round_}-f{file_idx or 0}"
    elif mode == "v-fix":
        agent_key = f"coder-vfix-g{group}-r{round_}-f{file_idx or 0}"
    else:
        raise ValueError(f"未知 mode: {mode}")

    root, inbox, outbox = _ensure_agent_dirs(run_dir, agent_key)

    lines: list[str] = []
    title = stage.title if hasattr(stage, "title") else "修复任务"
    if mode == "initial":
        lines.append(f"# {agent_key}：阶段 {stage.number} {title}")
    else:
        lines.append(f"# {agent_key}：{mode} 修复任务")
    lines.append("")
    lines.append(_context_block(spec_id, spec_dir, run_id, group, round_))
    lines.append("")

    if mode == "initial":
        lines.append("## 任务清单（按顺序逐条完成）")
        items = getattr(stage, "items", []) or []
        for it in items:
            tags = []
            it_writes = it.get("writes") if isinstance(it, dict) else getattr(it, "writes", [])
            it_reads = it.get("reads") if isinstance(it, dict) else getattr(it, "reads", [])
            it_reqs = it.get("requirements") if isinstance(it, dict) else getattr(it, "requirements", [])
            num = it.get("number") if isinstance(it, dict) else getattr(it, "number", "")
            it_title = it.get("title") if isinstance(it, dict) else getattr(it, "title", "")
            if it_writes:
                tags.append(f"@writes:{','.join(it_writes)}")
            if it_reads:
                tags.append(f"@reads:{','.join(it_reads)}")
            if it_reqs:
                tags.append("_需求:" + ",".join(it_reqs) + "_")
            suffix = (" " + " ".join(tags)) if tags else ""
            lines.append(f"- {num} {it_title}{suffix}")
        lines.append("")
        st_writes = _stage_writes(stage)
        st_reads = _stage_reads(stage)
        lines.append("## 文件边界（严格遵守）")
        lines.append(f"- @writes（仅允许写入）：{', '.join(st_writes) if st_writes else '(无声明)'}")
        lines.append(f"- @reads（允许读取）：{', '.join(st_reads) if st_reads else '(无声明)'}")
    elif mode == "p0-fix":
        lines.append("## P0 修复任务")
        lines.append("reviewer 提出的 P0（带证据标签）必须修复。详情见 inbox/p0-items.md。")
        lines.append("修复后请在 outbox/result.md 的「子任务状态」节按 done/failed 标记。")
        if fix_targets:
            lines.append("")
            lines.append("## 涉及文件")
            for ft in fix_targets:
                lines.append(f"- {ft.get('file_hint') or ft.get('file_path') or 'unknown'}")
    elif mode == "v-fix":
        lines.append("## v-fix 修复任务（按 validator 修复指引）")
        lines.append("仅修复 validator 在 validation.md 「给 coder 的修复指引」中列出的项。")
        lines.append("不要扩大范围；不要重写非失败相关的代码。")
        if fix_targets:
            lines.append("")
            lines.append("## 涉及文件 / 修复指引")
            for ft in fix_targets:
                lines.append(f"- 文件：{ft.get('file_path', '?')}")
                if ft.get("location"):
                    lines.append(f"  位置：{ft['location']}")
                if ft.get("problem"):
                    lines.append(f"  问题：{ft['problem']}")
                if ft.get("suggestion"):
                    lines.append(f"  建议：{ft['suggestion']}")
                if ft.get("requirements"):
                    lines.append(f"  _需求:{','.join(ft['requirements'])}_")

    lines.append("")
    lines.append("## inbox（上游产物，**只读**）")
    lines.append(f"- 路径：`{inbox}`")
    lines.append("- 内容（由主编排器在 fork 前放入）：上一轮 result.md / review.md / validation.md（按需）")
    lines.append("")
    lines.append("## outbox（你的产物**必须**写到这里）")
    lines.append(f"- result.md：`{outbox / 'result.md'}`")
    lines.append("")
    lines.append("## 输出协议（必读）")
    lines.append("1. result.md 必须含三节：`## 上下文` / `## 子任务状态` / `## 关键变更`")
    lines.append("2. 子任务状态行格式：`- <编号> <标题>: <done|failed|skipped> — <备注/文件>`")
    lines.append("3. 末行必须是：`STATUS: ok` 或 `STATUS: failed: <原因>` 或 `STATUS: blocked: <原因>`")
    lines.append("4. 严禁评价自己产物（不写 LGTM / 看起来不错）；reviewer 自会评审")
    lines.append("5. 严禁修改 @writes 之外的文件")

    text = "\n".join(lines) + "\n"
    _atomic_write_text(root / "task.md", text)
    return text


# -------------------------------------------------------------------------
# reviewer prompt
# -------------------------------------------------------------------------

def render_reviewer_prompt(
    group_stages: list[Any],
    coder_outboxes: list[Path],
    run_dir: Path,
    run_id: str,
    spec_id: str,
    spec_dir: str,
    group: int,
    round_: int = 1,
) -> str:
    agent_key = f"reviewer-g{group}-r{round_}"
    root, inbox, outbox = _ensure_agent_dirs(run_dir, agent_key)
    lines: list[str] = []
    lines.append(f"# {agent_key}：本 group {len(group_stages)} 个 stage 联合评审")
    lines.append("")
    lines.append(_context_block(spec_id, spec_dir, run_id, group, round_))
    lines.append("")
    lines.append("## 评审范围")
    for s in group_stages:
        st_writes = _stage_writes(s)
        lines.append(f"- 阶段 {s.number}: {s.title}（@writes: {', '.join(st_writes)}）")
    lines.append("")
    lines.append("## inbox（上游 coder 全部产物）")
    for p in coder_outboxes:
        lines.append(f"- `{p}`")
    lines.append("")
    lines.append("## outbox")
    lines.append(f"- review.md：`{outbox / 'review.md'}`")
    lines.append("")
    lines.append("## review.md schema（必须严格遵守）")
    lines.append("````markdown")
    lines.append("# " + agent_key)
    lines.append("")
    lines.append("## 结论")
    lines.append("needs-changes | approved-with-comments | approved")
    lines.append("")
    lines.append("## P0（每条必须带证据标签：[req:x.y] / [security] / [contract]）")
    lines.append("- <file:line> [req:x.y] — <一句话说明>")
    lines.append("（如无 P0：本节写 `(none)`）")
    lines.append("")
    lines.append("## P1")
    lines.append("- <file:line> — <说明>")
    lines.append("")
    lines.append("## P2")
    lines.append("- <一句话风格类建议>")
    lines.append("")
    lines.append("## 给使用者的提示")
    lines.append("- 一句话总结")
    lines.append("")
    lines.append("STATUS: ok")
    lines.append("````")
    lines.append("")
    lines.append("## 关键约束")
    lines.append("- P0 必须带证据标签；否则自动降级为 advisory（仅写入 tasks.md 注释，不进 fix loop）")
    lines.append("- reviewer 不参与修复循环；本轮 advisory 提完即结束（v0.7 reviewer round 恒为 1）")
    lines.append("- 末行恒为 `STATUS: ok`（advisory 模式；pass/fail 在 validator）")

    text = "\n".join(lines) + "\n"
    _atomic_write_text(root / "task.md", text)
    return text


# -------------------------------------------------------------------------
# validator prompt
# -------------------------------------------------------------------------

def render_validator_prompt(
    group_stages: list[Any],
    run_dir: Path,
    run_id: str,
    spec_id: str,
    spec_dir: str,
    group: int,
    round_: int = 1,
    prev_validation: Optional[Path] = None,
) -> str:
    agent_key = f"validator-g{group}-r{round_}"
    root, inbox, outbox = _ensure_agent_dirs(run_dir, agent_key)
    lines: list[str] = []
    lines.append(f"# {agent_key}：本 group {len(group_stages)} 个 stage 联合验证")
    lines.append("")
    lines.append(_context_block(spec_id, spec_dir, run_id, group, round_))
    lines.append("")
    lines.append("## 验证范围")
    for s in group_stages:
        lines.append(f"- 阶段 {s.number}: {s.title}")
        items = getattr(s, "items", []) or []
        for it in items:
            num = it.get("number") if isinstance(it, dict) else getattr(it, "number", "")
            it_title = it.get("title") if isinstance(it, dict) else getattr(it, "title", "")
            it_reqs = it.get("requirements") if isinstance(it, dict) else getattr(it, "requirements", [])
            req = ("（_需求:" + ",".join(it_reqs) + "_）") if it_reqs else ""
            lines.append(f"  - {num} {it_title}{req}")
    lines.append("")
    if prev_validation is not None:
        lines.append("## 上一轮 validation（本轮在其上验证 v-fix 是否成功）")
        lines.append(f"- `{prev_validation}`")
        lines.append("")
    lines.append("## outbox")
    lines.append(f"- validation.md：`{outbox / 'validation.md'}`")
    lines.append("")
    lines.append("## validation.md schema（必须严格遵守）")
    lines.append("````markdown")
    lines.append("# " + agent_key)
    lines.append("")
    lines.append("## 判定")
    lines.append("pass | fail")
    lines.append("")
    lines.append("## 复现命令")
    lines.append("```bash")
    lines.append("cd <project root>")
    lines.append("pytest tests/... -v")
    lines.append("```")
    lines.append("")
    lines.append("## 按子任务的验证结果")
    lines.append("- [x] 1.1 <标题>: pass")
    lines.append("- [ ] 1.3 <标题>: fail — <一句话现场>")
    lines.append("")
    lines.append("## 失败现场（fail 时必填）")
    lines.append("```")
    lines.append("FAILED tests/... ::test_xxx")
    lines.append("AssertionError: ...")
    lines.append("```")
    lines.append("")
    lines.append("## 给 coder 的修复指引（fail 时必填，不带 P0/P1 标签）")
    lines.append("### 修复 1 — <短标题>")
    lines.append("- 文件: <abs/rel>")
    lines.append("- 位置: <函数名 / 行号>")
    lines.append("- 问题: <说明>")
    lines.append("- 建议: <修复方向>")
    lines.append("- _需求:x.y_")
    lines.append("")
    lines.append("STATUS: ok")
    lines.append("````")
    lines.append("")
    lines.append("## 关键约束")
    lines.append("- pass / fail 是客观信号；fail 必须给出可复现命令 + 失败现场 + 按文件分组的修复指引")
    lines.append("- 不要评论代码风格（那是 reviewer 的活）；只关心可验证项")
    lines.append("- 末行恒为 `STATUS: ok`")

    text = "\n".join(lines) + "\n"
    _atomic_write_text(root / "task.md", text)
    return text


# -------------------------------------------------------------------------
# 模块自测
# -------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    print("import this module; see render_coder_prompt / render_reviewer_prompt / render_validator_prompt")
