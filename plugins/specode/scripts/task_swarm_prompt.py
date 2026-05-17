"""Subagent prompt pre-renderer.

The orchestrator should NOT compose subagent prompts by hand — it only reads
the rendered `task.md` produced by this module and passes it verbatim to the
Task tool. This keeps four invariants:

  - @writes boundary is always declared, identically across rounds
  - inbox relay paths use a stable convention (workspace/inbox/...)
  - fix-round prompts always include the "only fix P0/fail items, no scope creep"
    guardrail
  - checkpoint validator prompts always include the upstream coder+reviewer
    outboxes by reference

The rendered file is also useful for debugging: every subagent's exact input is
on disk under `.task-swarm/runs/<RUN>/agents/stage-N-<role>[-rR]/task.md`.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


# ---------- workspace ----------

def workspace_name(stage: int, role: str, round_no: int) -> str:
    """Naming convention: stage-N-<role> for round 1, -rR suffix from round 2+."""
    base = f"stage-{stage}-{role}"
    return base if round_no <= 1 else f"{base}-r{round_no}"


def agent_workspace(run_dir: Path, stage: int, role: str, round_no: int) -> Path:
    return run_dir / "agents" / workspace_name(stage, role, round_no)


def prepare_workspace(run_dir: Path, stage: int, role: str, round_no: int) -> Path:
    """Create inbox + outbox directories and return workspace path."""
    ws = agent_workspace(run_dir, stage, role, round_no)
    (ws / "inbox").mkdir(parents=True, exist_ok=True)
    (ws / "outbox").mkdir(parents=True, exist_ok=True)
    return ws


def relay_inbox(run_dir: Path, ws: Path, sources: Iterable[tuple[int, str, int, str]]) -> list[str]:
    """Copy files from upstream outboxes into ws/inbox/.

    sources: iterable of (stage, role, round_no, label) tuples.
    label is the destination filename (e.g., "prev-result.md", "review.md").

    Returns list of relative inbox paths actually copied.
    """
    inbox = ws / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for stage, role, round_no, label in sources:
        src_ws = agent_workspace(run_dir, stage, role, round_no)
        src_dir = src_ws / "outbox"
        if not src_dir.exists():
            continue
        # if the label maps to a specific filename, copy that single file;
        # otherwise copy whole outbox tree.
        primary_map = {
            "coder": "result.md",
            "reviewer": "review.md",
            "validator": "validation.md",
        }
        primary = primary_map.get(role)
        if primary and (src_dir / primary).exists():
            dest = inbox / label
            shutil.copyfile(src_dir / primary, dest)
            copied.append(dest.name)
        else:
            for p in src_dir.iterdir():
                if p.is_file():
                    dest = inbox / f"{label}__{p.name}"
                    shutil.copyfile(p, dest)
                    copied.append(dest.name)
    return copied


# ---------- prompt rendering ----------

@dataclass
class StageContext:
    stage_num: int
    stage_title: str
    stage_kind: str             # "stage" | "checkpoint"
    leaves: list[dict]          # subset of parser Leaf as dict
    spec_dir: Path
    project_root: Path
    workspace: Path
    round_no: int = 1
    scope: str = ""             # "" | "p0-fix" | "validator-fail-fix" | "post-fix"


def _writes_clause(leaves: list[dict]) -> str:
    files: list[str] = []
    for l in leaves:
        if l.get("policy") == "skip":
            continue
        for f in l.get("files") or []:
            if f not in files:
                files.append(f)
    return ", ".join(files) if files else "(本阶段无 @writes 声明文件; 仅限 inbox/outbox + 已存在文件的最小改动)"


def _leaves_block(leaves: list[dict]) -> str:
    lines: list[str] = []
    for l in leaves:
        if l.get("policy") == "skip":
            continue
        lines.append(f"- {l.get('num')} {l.get('title')}")
        if l.get("files"):
            lines.append(f"  - 文件: {', '.join(l['files'])}")
        if l.get("requirement"):
            lines.append(f"  - 需求: {l['requirement']}")
        if l.get("verify"):
            lines.append(f"  - 验证: {l['verify']}")
    return "\n".join(lines) if lines else "(无叶子任务)"


def _inbox_listing(workspace: Path) -> str:
    inbox = workspace / "inbox"
    if not inbox.exists():
        return "(空)"
    names = sorted(p.name for p in inbox.iterdir() if p.is_file())
    return "\n".join(f"- {n}" for n in names) if names else "(空)"


def render_coder_prompt(ctx: StageContext) -> str:
    writes = _writes_clause(ctx.leaves)
    is_fix = ctx.round_no > 1 and ctx.scope in {"p0-fix", "validator-fail-fix"}
    header = (
        f"你正在 task-swarm 流程中作为 CODER 子 agent 执行阶段 {ctx.stage_num}"
        + (f"（修复轮 r{ctx.round_no}，scope={ctx.scope}）" if is_fix else "（初轮）")
        + "。"
    )

    body = [
        header,
        "",
        f"# 阶段 {ctx.stage_num}: {ctx.stage_title}",
        "",
        "## 本阶段子任务清单",
        _leaves_block(ctx.leaves),
        "",
        "## 边界",
        f"- 项目根: {ctx.project_root}",
        f"- @writes（你只能修改这些路径）: {writes}",
        f"- 工作区: {ctx.workspace}",
        f"- inbox（只读）: {ctx.workspace / 'inbox'}",
        f"- outbox（你的产出）: {ctx.workspace / 'outbox'}",
        f"- spec 文档（绝对只读，禁止修改）: {ctx.spec_dir}",
        "",
        "## inbox 内容",
        _inbox_listing(ctx.workspace),
        "",
    ]

    if is_fix:
        body += [
            "## 修复轮硬规则",
            "1. 只动 inbox/review.md 列出的 **P0** 项（或 inbox/validation.md 的修复指引）涉及的文件/位置",
            "2. P1/P2 不在本轮职责内，不要顺手优化",
            "3. 修完每条 P0 在 outbox/result.md 用 `- [x] <P0 摘要> — 已修复: ...` 标记",
            "4. 不要重写整个阶段，是定向补丁",
            "",
        ]

    body += [
        "## 输出协议（严格）",
        "在 outbox/result.md 中至少包含：",
        "",
        "```markdown",
        f"# 阶段 {ctx.stage_num}: {ctx.stage_title} 执行结果",
        "",
        "## 子任务状态",
        "- 1.1 写 ...: done — <文件>",
        "- 1.2 写 ...: failed — <原因>",
        "",
        "## 关键变更",
        "- ...",
        "",
        "## 给下游 reviewer 的提示",
        "- ...",
        "```",
        "",
        "**末行**必须是 `STATUS: ok` / `STATUS: failed: <原因>` / `STATUS: blocked: <原因>` 之一。",
        "",
        "禁止：",
        "- 给自己的产物打分（LGTM / 看起来对 / approved）",
        "- 评审任何代码（包括自己刚写的）",
        "- 判 pass/fail（那是 validator 的事）",
        "- 修改 @writes 之外的任何文件",
        "- 修改 spec 文档",
    ]
    return "\n".join(body)


def render_reviewer_prompt(ctx: StageContext) -> str:
    is_post_fix = ctx.scope == "post-fix"
    header = (
        f"你正在 task-swarm 流程中作为 REVIEWER 子 agent 评审阶段 {ctx.stage_num}"
        + (f"（复审第 r{ctx.round_no} 轮）" if ctx.round_no > 1 else "（初评）")
        + "。"
    )

    body = [
        header,
        "",
        f"# 阶段 {ctx.stage_num}: {ctx.stage_title}",
        "",
        "## 评审范围",
        _leaves_block(ctx.leaves),
        "",
        "## 边界",
        "- 你**没有** Edit/Write 工具——这是物理隔离，想改也改不了。",
        "- 仅用 Bash 写 outbox/review.md。",
        f"- inbox: {ctx.workspace / 'inbox'}",
        f"- outbox: {ctx.workspace / 'outbox'}",
        f"- 评审 spec 文档（只读）: {ctx.spec_dir}",
        "",
        "## inbox 内容",
        _inbox_listing(ctx.workspace),
        "",
    ]

    if is_post_fix:
        body += [
            "## 本轮特别说明（post-fix 复审）",
            "- 这是 validator fail 后 coder 修复的复审。",
            "- 只看本轮 coder 改动的文件，不重新评全阶段。",
            "- 主要确认：fail 指引列出的问题被解决、没引入新回归。",
            "",
        ]

    body += [
        "## 输出协议（严格，主编排器要解析）",
        "在 outbox/review.md 写入：",
        "",
        "```markdown",
        "## 结论",
        "needs-changes | approved-with-comments | approved",
        "",
        "## P0 — 阻塞，coder 必须修复（修完才能进 validator）",
        "- <文件:行> [req:x.y] — <问题> — <建议>",
        "- <文件:行> [security] — <安全/数据完整性问题> — <建议>",
        "- <文件:行> [contract] — <接口契约不一致> — <建议>",
        "（如无 P0 写 `(none)`，不要省略本节）",
        "",
        "## P1 — 建议修复，不阻塞",
        "- ...",
        "",
        "## P2 — 可选改进",
        "- ...",
        "",
        "## 给 validator 的提示",
        "- 重点跑：...",
        "```",
        "",
        "末行：`STATUS: ok`（无论 approved 或 needs-changes 都 ok）。",
        "",
        "## P0 证据标签规则（**重要**）",
        "P0 行**必须**带下列证据标签之一，否则会被主编排器**自动降级为 advisory**（只入档审计、不触发 coder 修复轮）：",
        "",
        "- `[req:x.y]` — 直接违反某条 `_需求：x.y_` 的 SHALL 语句",
        "- `[security]` — 安全 / 数据完整性问题（注入、越权、token 泄漏、并发不安全等）",
        "- `[contract]` — 接口契约不一致（A 子任务说返回 token，B 子任务期望 session_id）",
        "",
        "**没有证据标签 = 不是阻塞 P0**。如果你只是『觉得』代码可以更好但说不出具体的需求/安全/契约依据，请放进 P1 而不是 P0。",
        "这条规则的目的：reviewer 是 LLM 读代码，主观倾向容易让 coder 在多轮无意义修复中空转；",
        "强制举证把『印象』逼成『证据』，没证据的担忧仍然有价值——但走 P1 路径不阻塞推进。",
        "",
        "**死循环识别**：如果本轮 P0 与 inbox 中 prev-review.md 完全一致，",
        "在文件**顶部**加 `## 进入死循环风险` 节，主编排器会立刻终止本阶段。",
        "",
        "**零 P0 是允许的**——但必须扫完每个文件、每个子任务才能下结论。",
    ]
    return "\n".join(body)


def render_validator_prompt(ctx: StageContext) -> str:
    is_checkpoint = ctx.stage_kind == "checkpoint"
    header = (
        f"你正在 task-swarm 流程中作为 VALIDATOR 子 agent 执行阶段 {ctx.stage_num}"
        + ("（specode 检查点）" if is_checkpoint else "")
        + (f"（重验第 r{ctx.round_no} 轮）" if ctx.round_no > 1 else "")
        + "。"
    )

    body = [
        header,
        "",
        f"# 阶段 {ctx.stage_num}: {ctx.stage_title}",
        "",
        "## 边界",
        "- 你**没有** Edit/Write 工具——只能 Bash 跑命令 + Read 看文件。",
        "- 验收报告用 Bash 写到 outbox/validation.md。",
        "- 不许因为 reviewer approved 就 pass —— 必须**独立**用真实命令证明。",
        f"- inbox: {ctx.workspace / 'inbox'}",
        f"- outbox: {ctx.workspace / 'outbox'}",
        "",
        "## inbox 内容",
        _inbox_listing(ctx.workspace),
        "",
        "## 输出协议（严格，主编排器要解析）",
        "",
        "```markdown",
        "## 判定",
        "pass | fail",
        "",
        "## 复现命令",
        "```bash",
        "<任何人执行都能得到一样结果的命令序列>",
        "```",
        "",
        "## 按子任务的验证结果",
        "- [x] 1.1 ...: pass (pytest ...)",
        "- [ ] 1.3 ...: fail — 未达 _需求：1.3_",
        "",
        "## 给 coder 的修复指引（必填 if fail）",
        "- 文件: <path>",
        "- 位置: <function/line>",
        "- 问题: <具体>",
        "- 建议: <具体做法>",
        "- 涉及需求: _需求：x.y_",
        "```",
        "",
        "末行：`STATUS: ok`（无论 pass 还是 fail，写完报告就 ok）。",
        "",
        "**死循环识别**：若本轮失败原因与 inbox 中 prev-validation.md 完全相同，",
        "在文件**顶部**加 `## 进入死循环风险` 节，主编排器会立刻终止本阶段。",
    ]
    return "\n".join(body)


def render_prompt(role: str, ctx: StageContext) -> str:
    if role == "coder":
        return render_coder_prompt(ctx)
    if role == "reviewer":
        return render_reviewer_prompt(ctx)
    if role == "validator":
        return render_validator_prompt(ctx)
    raise ValueError(f"unknown role: {role}")


def write_task_file(ctx: StageContext, role: str) -> Path:
    """Render and write task.md into the workspace, return path."""
    text = render_prompt(role, ctx)
    path = ctx.workspace / "task.md"
    path.write_text(text, encoding="utf-8")
    return path
