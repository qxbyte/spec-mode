#!/usr/bin/env python3
"""spec_session.py — specode 会话 / 锁 / hook 注入统一入口（详见 SKILL.md + references/）。

业务子命令（被 SKILL.md 引导主会话调用；都接 --session）：
  acquire / release / heartbeat / verify-lock / phase-transition
  load / continue / end / status / read-session

hook 子命令（仅由 hooks/hooks.json 调用；全部 exit 0，仅注入提示）：
  on-session-start / on-user-prompt / on-stop / on-session-end
  on-task-completed（v0.7 stub；当前 exit 0）
  on-heartbeat-quiet（v0.8 stub；当前 exit 0）
  on-pre-tool-use（v0.8 stub；当前 exit 0）

强制写入语义：
  - 任何修改 sessions/<id>.json 或 <spec-dir>/.config.json 的命令必须 tempfile + os.replace + fsync。
  - 写失败 → 整命令视失败、回滚已变更的另一份文件、exit 1。

所有 hook 子命令永远 exit 0；任何异常一律 catch。

stdlib-only。
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path
from string import Template
from typing import Any, Optional

THIS_DIR = Path(__file__).resolve().parent

# spec_log 是兄弟脚本（同目录），通过 sys.path 注入以便 import；失败时降级为 no-op
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))
try:
    from spec_log import write_event as _log_event  # type: ignore
except Exception:
    def _log_event(event: str, payload: Optional[dict] = None,
                   session_id: Optional[str] = None) -> None:
        return None

# -------------------------------------------------------------------------
# 常量
# -------------------------------------------------------------------------

STALE_LOCK_SECONDS = 30 * 60  # 30 分钟无 heartbeat 视为 stale

VALID_PHASES = {
    "intake",
    "requirements",
    "bugfix",
    "design",
    "tasks",
    "implementation",
    "acceptance",
    "iteration",
}


# -------------------------------------------------------------------------
# 时间工具
# -------------------------------------------------------------------------

def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _parse_iso(s: Optional[str]) -> Optional[float]:
    if not s:
        return None
    try:
        # 朴素 ISO8601-UTC 解析
        if s.endswith("Z"):
            s2 = s[:-1] + "+00:00"
        else:
            s2 = s
        import datetime as _dt
        return _dt.datetime.fromisoformat(s2).timestamp()
    except Exception:
        return None


# -------------------------------------------------------------------------
# 原子写
# -------------------------------------------------------------------------

def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        os.replace(tmp, path)
        try:
            dir_fd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            except OSError:
                pass
            finally:
                os.close(dir_fd)
        except OSError:
            pass
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _atomic_write_json(path: Path, payload: Any) -> None:
    _atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))


# -------------------------------------------------------------------------
# 数据层
# -------------------------------------------------------------------------

def _sessions_dir() -> Path:
    return Path.home() / ".specode" / "sessions"


def session_file_path(session_id: str) -> Path:
    return _sessions_dir() / f"{session_id}.json"


def read_session(session_id: str) -> Optional[dict]:
    p = session_file_path(session_id)
    if not p.exists():
        return None
    try:
        with p.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            # 兼容老 sessions/<id>.json：字段名曾叫 claude_session_id，迁移到 session_id
            if "session_id" not in data and "claude_session_id" in data:
                data["session_id"] = data["claude_session_id"]
            return data
    except Exception:
        return None
    return None


def write_session_atomic(session_id: str, data: dict) -> None:
    _atomic_write_json(session_file_path(session_id), data)


def read_spec_config(spec_dir: Path) -> Optional[dict]:
    p = spec_dir / ".config.json"
    if not p.exists():
        return None
    try:
        with p.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except Exception:
        return None
    return None


def write_spec_config_atomic(spec_dir: Path, data: dict) -> None:
    _atomic_write_json(spec_dir / ".config.json", data)


# -------------------------------------------------------------------------
# Selector 提示词常量库（详见 references/selectors.md）
# -------------------------------------------------------------------------

SELECTOR_PROMPTS: dict[str, str] = {
    "workflow-choice": """## 选择器节点：工作流选择

**目的**：用户刚运行 /specode:spec <需求>，已进入 intake 阶段。
在写 requirements.md / bugfix.md / design.md 之前，先决定走哪条 spec 工作流。

**上下文**：active spec=<slug>，phase=<phase>。

**前置动作（chat 简报，≤2 行）**：写一句"接到需求《<source_text_head>...》，请选择工作流。"

**调用 `AskUserQuestion` 工具**，参数完全按下列结构（直接传入，不要翻译/重写选项）：

questions:
  - question: "工作流选择 —— 决定走哪条 spec 流程？"
    header: "工作流选择"
    multiSelect: false
    options:
      - label: "Requirements first"
        description: "行为优先的新特性：先把 SHALL 写清楚，再补技术设计。"
      - label: "Technical Design first"
        description: "架构约束已知的新特性：先把 design.md 框架定下来，再反推 requirements。"
      - label: "Bugfix"
        description: "缺陷修复 / 回归测试：用 bugfix.md（Current/Expected/Unchanged）替代 requirements.md。"

**约束**：
- 调用工具后立即 end turn 等待用户选择。
- 不要在 chat 输出 markdown 列表 / 不要让用户回复编号。
- 宿主工具自动提供 "Other" + ESC 取消，**禁止**自己加 "Type something" / "Chat about this" 保留位。
""",
    "clarification-wizard": """## 选择器节点：需求澄清问答（wizard）

**目的**：需求有歧义，必须在写 requirements.md / bugfix.md 之前**一次性**收齐
影响 scope / behavior / UX / data / validation / acceptance 的 2-4 个阻塞性澄清点。

**上下文**：active spec=<slug>，phase=intake。
源需求摘要：<source_text_head>

**前置动作（chat 简报，≤3 行）**：写一句"为避免 invent 业务规则，需要先确认 N 个关键点，请逐一回答。"

**调用 `AskUserQuestion` 工具一次**，`questions` 数组传 **2-4 个 question 对象**
（每个 question 都是独立的 chip-tab，每个 multiSelect=false）。子问题与选项**由你结合源需求摘要 + 用户最近输入 + assets/templates 章节结构自行生成**——不要凭空 invent 业务规则。

参数格式示例（替换为你针对当前需求生成的具体子问题）：

questions:
  - question: "<具体决策点 1 标题，必须是'是/否/选哪条'问题>"
    header: "<≤12 字 chip 标签>"
    multiSelect: false
    options:
      - label: "<选项 A>"
        description: "<一句话解释 + trade-off>"
      - label: "<选项 B>"
        description: "<一句话解释 + trade-off>"
  - question: "<具体决策点 2>"
    header: "<chip 标签>"
    multiSelect: false
    options:
      - label: "<选项 A>"
        description: "..."
      - label: "<选项 B>"
        description: "..."
  # 最多 4 个 question

**约束**：
- 每个子问题必须是"是/否/选哪条"具体问题；禁止开放式叙述（"你怎么想"）。
- 子问题之间**无依赖**——若有依赖应拆成两次 wizard。
- 决策点 ≥ 5 个 → 只保留最阻塞的 4 个，其余记入 requirements.md "待确认问题" 节。
- inputs 不足以构成阻塞决策点 → **不调本工具**，直接进 `clarification-done`。
- 工具自动提供 "Other"，**不要**手工加 "Type something" / "Chat about this" 保留位。
- 调用工具后立即 end turn。
""",
    "clarification-done": """## 选择器节点：需求澄清是否完成？

**目的**：上一轮 wizard 用户已回答；判断是否进入 requirements.md / bugfix.md 生成，
还是再发一轮 wizard 继续澄清。

**上下文**：active spec=<slug>，phase=intake。

**前置动作（chat 简报，≤2 行）**：写一句"已记录用户的 N 个澄清回答，请确认下一步。"

**调用 `AskUserQuestion` 工具**：

questions:
  - question: "需求澄清是否完成？"
    header: "澄清完成?"
    multiSelect: false
    options:
      - label: "进入下一阶段（推荐）"
        description: "用户回答已覆盖所有阻塞项，可开始写 requirements.md / bugfix.md。"
      - label: "继续澄清"
        description: "还有未解决的歧义，再发一轮 wizard。"

**约束**：
- 调用工具后立即 end turn。
- 不要复述选项 / 不要让用户回复编号。
""",
    "doc-confirm-requirements": """## 选择器节点：requirements.md 文档确认

**目的**：requirements.md 已生成 / 更新；让用户确认是否进入 design phase，
或者先看全文 / 继续修改。

**上下文**：active spec=<slug>，phase=<phase>。
刚生成的文档：<spec_dir>/requirements.md

**前置动作（chat 简报，≤8 行）**：列出 3-8 条**关键变更要点**（文件路径 + 章节增量 + 未决问题）。
绝对不要 reprint 文档全文。

**调用 `AskUserQuestion` 工具**：

questions:
  - question: "requirements.md 已生成。下一步？"
    header: "需求确认"
    multiSelect: false
    options:
      - label: "确认（推荐）"
        description: "文档内容符合预期，进入下一 phase。"
      - label: "查看全文"
        description: "在 chat 完整 echo 该文档（不进入下一 phase）。"
      - label: "继续沟通"
        description: "文档需要修改，告诉你具体怎么改。"

**约束**：
- 调用工具后立即 end turn。
- 简报必须在工具调用**之前**输出。
""",
    "doc-confirm-bugfix": """## 选择器节点：bugfix.md 文档确认

**目的**：bugfix.md 已生成 / 更新；让用户确认是否进入 design phase，
或者先看全文 / 继续修改。

**上下文**：active spec=<slug>，phase=<phase>。
刚生成的文档：<spec_dir>/bugfix.md

**前置动作（chat 简报，≤8 行）**：列出 3-8 条关键变更要点
（Current / Expected / Unchanged 段落增量 + 复现步骤 + 影响范围）。

**调用 `AskUserQuestion` 工具**：

questions:
  - question: "bugfix.md 已生成。下一步？"
    header: "缺陷确认"
    multiSelect: false
    options:
      - label: "确认（推荐）"
        description: "文档内容符合预期，进入下一 phase。"
      - label: "查看全文"
        description: "在 chat 完整 echo 该文档（不进入下一 phase）。"
      - label: "继续沟通"
        description: "文档需要修改，告诉你具体怎么改。"

**约束**：
- 调用工具后立即 end turn。
- 简报必须在工具调用**之前**输出。
""",
    "doc-confirm-design": """## 选择器节点：design.md 文档确认

**目的**：design.md 已生成 / 更新；让用户确认是否进入 tasks phase，
或者先看全文 / 继续修改。

**上下文**：active spec=<slug>，phase=<phase>。
刚生成的文档：<spec_dir>/design.md

**前置动作（chat 简报，≤8 行）**：列出 3-8 条关键变更要点
（架构图变化 + 接口签名 + 数据模型字段 + 风险 / 偏离）。

**调用 `AskUserQuestion` 工具**：

questions:
  - question: "design.md 已生成。下一步？"
    header: "设计确认"
    multiSelect: false
    options:
      - label: "确认（推荐）"
        description: "文档内容符合预期，进入下一 phase。"
      - label: "查看全文"
        description: "在 chat 完整 echo 该文档（不进入下一 phase）。"
      - label: "继续沟通"
        description: "文档需要修改，告诉你具体怎么改。"

**约束**：
- 调用工具后立即 end turn。
- 简报必须在工具调用**之前**输出。
""",
    "tasks-execution": """## 选择器节点：任务执行选择（合并 0.9.2 旧 doc-confirm-tasks）

**目的**：tasks.md 已生成；让用户在一个选择器里同时完成「确认 tasks.md」+「选择执行方式」+「回退（需要调整）」+「暂不 coding」。0.9.3 起废弃单独的 doc-confirm-tasks 选择器，「需要调整 tasks.md」作为本选择器的回退出口。

**上下文**：active spec=<slug>，phase=tasks。
required 任务数：<n_required>，optional 任务数：<n_optional>。

**前置动作（chat 简报，≤8 行）**：
- 列出**任务计数**（required N 个，optional M 个）
- 列出**主要阶段**与 traceability（`_需求：x.y_` 标签）
- 标注同文件冲突的 stage（影响 task-swarm group 切分）

**调用 `AskUserQuestion` 工具**：

questions:
  - question: "tasks.md 已生成。怎么执行？"
    header: "执行方式"
    multiSelect: false
    options:
      - label: "用 task-swarm 多 agent 并发（推荐）"
        description: "委派给 task-swarm 编排器；多 coder 并发 + reviewer + validator 自动 fix loop。required + optional 一并处理。"
      - label: "顺序执行（同时处理 optional）"
        description: "单 agent 逐个推进 required + optional 任务，[ ] → [~] → [x]。如需只跑 required，可在 Other 输入说明。"
      - label: "需要调整 tasks.md"
        description: "tasks 不符合预期，告诉你具体怎么改。"
      - label: "暂不 coding"
        description: "tasks.md 已落地但暂不开始实现；随时 /specode:end 关闭会话。"

**约束**：
- 4 个选项已占满工具上限；细化需求（如只跑 required / 跳过某 optional）走 "Other" 输入。
- 调用工具后立即 end turn。
- 简报必须在工具调用**之前**输出。
""",
    "takeover-options": """## 选择器节点：接管选项

**目的**：/specode:continue <slug> 命中 LockHeld；让用户选择强制接管 / 只读查看 / 取消。

**上下文**：active spec=<slug>，phase=<phase>。
锁持有者: <other_id_short>（前 8 位），最近 heartbeat: <last_heartbeat>。

**前置动作（chat 简报，≤2 行）**：写一句"spec '<slug>' 已被 <other_id_short> 在 <last_heartbeat> 持有，请选择处理方式。"

**调用 `AskUserQuestion` 工具**：

questions:
  - question: "该 spec 已被其他会话窗口持有，怎么处理？"
    header: "接管选项"
    multiSelect: false
    options:
      - label: "强制接管"
        description: "驱逐对方锁，本会话成为新锁主；对方下一次写操作会被 verify-lock 拒绝。"
      - label: "只读查看"
        description: "不持锁，加载文档进入只读模式；所有 Edit/Write 在 SKILL.md 层面被劝阻。"
      - label: "取消"
        description: "不接管，关闭本次 /specode:continue。"

**约束**：
- **不给"（推荐）"标记**——让用户根据对方是否仍活跃自己判断。
- 调用工具后立即 end turn。
""",
    "acceptance-gate": """## 选择器节点：验收门

**目的**：acceptance phase；tasks.md 全部 `[x]` 完成后，判断是否通过验收进入 iteration，或者回到 requirements / design / tasks 继续修改。

**上下文**：active spec=<slug>，phase=acceptance。
任务完成度：<n_done>/<n_total>。

**前置动作（chat 简报，≤3 行）**：
- 列出 tasks.md 完成度（done/total）。
- 调用 `spec_lint.py --spec <spec_dir>` 把 WARNING 列出来（traceability / log / EARS 三类，如有）。
- 若 tasks.md 末尾 `## 测试要点` 章节存在，简述本次需要测试人员关注的要点；测试要点是参考信息，不参与验收门判定。

**调用 `AskUserQuestion` 工具**：

questions:
  - question: "验收结论？"
    header: "验收门"
    multiSelect: false
    options:
      - label: "验收通过，进入 iteration（推荐）"
        description: "所有任务完成；如有后续调整走 iteration 子循环。"
      - label: "继续修改"
        description: "仍有未完成任务 / lint WARNING 需处理，回到 requirements / design / tasks 调整。"

**约束**：
- n_done == n_total 时推荐选 1；否则**移除"（推荐）"标记**。
- 调用工具后立即 end turn。
""",
    "iteration-scope": """## 选择器节点：iteration 调整范围（多选）

**目的**：用户从 acceptance-gate 选了"验收通过"或显式提出迭代调整；确定本轮 iteration 调整哪些文档/动作。

**上下文**：active spec=<slug>，phase=iteration。

**前置动作（chat 简报，≤2 行）**：写一句"进入 iteration 子循环，请选择本轮调整范围（可多选）。"

**调用 `AskUserQuestion` 工具**，注意 **multiSelect=true**：

questions:
  - question: "本轮 iteration 要调整哪些文档/动作？（可多选）"
    header: "迭代范围"
    multiSelect: true
    options:
      - label: "改 requirements"
        description: "新增 / 修改 EARS SHALL 条款。"
      - label: "改 design"
        description: "架构 / 接口 / 数据模型调整。"
      - label: "改 tasks"
        description: "新增任务或调整已有任务范围。"
      - label: "重跑测试"
        description: "不改文档，重新验证当前实现。"

**约束**：
- multiSelect=true（**唯一**使用类型 C 复选框的场景）。
- 允许用户全不选（视为本轮 iteration 取消）；ESC 等价。
- 调用工具后立即 end turn。
""",
}


def _fill_selector(key: str, ctx: dict[str, str]) -> Optional[str]:
    tpl = SELECTOR_PROMPTS.get(key)
    if not tpl:
        return None
    out = tpl
    for k, v in ctx.items():
        out = out.replace(f"<{k}>", str(v))
    return out


# -------------------------------------------------------------------------
# 状态行 footer 模板（详见 SKILL.md §Status Footer）
# -------------------------------------------------------------------------

STATUS_FOOTER_TEMPLATE = """## 🪧 spec-mode 状态行（必须在本响应末尾输出）

请在本次响应正文之后**额外**输出一行格式如下的状态行，紧贴响应末尾、之前空一行：

─── spec-mode ─── spec: <slug> | session: <session_short> | phase: <phase> | /specode:end 退出

如果是只读模式，请使用：

─── spec-mode ─── spec: <slug> | session: <session_short> | phase: <phase> | [只读] | /specode:end 退出

具体值：
  slug:    <slug>
  session: <session_short>
  phase:   <phase>
  mode:    <mode>

状态行的唯一目的是让用户和你自己都看到当前仍在 spec 模式。**不要省略**；如果本轮要调 `AskUserQuestion` 工具呈现选择器，状态行应放在工具调用**之前**的 chat 文本里（与正文空一行隔开），然后再调工具。
"""

DOC_PRIORITY_REMINDER_ACTIVE = """## 📝 文档优先提醒（用户输入侧）

active spec：<slug>（phase=<phase>）
此 spec 的可写文档：
  • requirements.md / bugfix.md
  • design.md
  • tasks.md（末尾自带 `## 测试要点` 节，按需顺手按 SHALL 补几行作为参考）
  • implementation-log.md（如有）

请评估用户本次输入是否涉及以下变更：

- 需求 / 验收标准调整 → 先 Edit `requirements.md` 或 `bugfix.md`
- 架构 / 接口 / 数据模型决策 → 先 Edit `design.md`
- 任务范围 / 状态推进 → 先 Edit `tasks.md`
- 实现期间的设计偏离 / 关键决策 → 在 `implementation-log.md` 追加条目
- 仅闲聊 / 状态查询 / 无关讨论 → 无需文档变更

文档变更要**在同一轮 turn 内先于代码改动落盘**；不要把"待会儿写"留作 verbal commitment——chat 内容不会进入 next session。
"""

DOC_PRIORITY_REMINDER_READONLY = """## 📝 文档优先提醒（用户输入侧 / 只读模式）

active spec：<slug>（phase=<phase>，**只读**）
你当前没有持锁，**不应**对该 spec 的文档发起 Edit/Write。如需修改，请先：

  1. 使用 `/specode:continue <slug>` 并在 selector 中选"强制接管"获取锁；
  2. 或退出本会话后由锁主推进。

只读模式下可以：阅读、回答用户基于已有文档的问题、协助分析；**不要**写 spec 文档或源码以"模拟落地"。
"""

CODE_DOC_SYNC_STOP = """## 🔄 代码-文档同步提醒（turn 结束侧）

active spec：<slug>（phase=<phase>）

本 turn 即将结束。如果你在本 turn 内修改了源代码，请自检以下三项：

1. `tasks.md` 是否更新？ —— 推进任务标记（`[ ]` → `[~]` → `[x]` / blocked）
2. `implementation-log.md` 是否记录？ —— 实现说明、设计偏离、技术决策
3. `design.md` 接口契约是否变化？ —— 若改了，同步 Edit

如有遗漏，请在 chat 显式承诺下一轮第一件事就是补齐。

（本提醒**不阻断 turn**——是否补齐由你判断。但代价是 next session `/specode:continue` 时，未写入文档的变更**全部丢失**。）
"""

SPEC_MODE_CONTINUE_REMINDER = """## ⛔ 你仍处于 spec 模式

spec=<slug>, phase=<phase>, mode=active

下一 turn 必须继续遵守：
  - selector / 文档优先 / 状态行 footer 三项纪律
  - 通过 /specode:end 才能正式退出 spec 模式
"""

SPEC_MODE_READONLY_REMINDER = """## 🔒 你处于 spec 只读模式

spec=<slug>, phase=<phase>, mode=readonly

只读模式下：
  - 不要 Edit/Write 该 spec 的任何文档或源码
  - 状态行 footer 必须带 [只读] 标记
  - 如需写入，请走 `/specode:continue` 选"强制接管"
"""


# -------------------------------------------------------------------------
# 帮助 fast-path 文本（hook emit verbatim）
# -------------------------------------------------------------------------

HELP_OUTPUT_TEXT = """specode v0.6 — Specification-driven workflow

命令一览：

  /specode:spec <需求>                  开始新 spec（持久会话，需要 /specode:end 关闭）
  /specode:continue [slug]              恢复 / 接管已有 spec（slug 缺省时列出可选）
  /specode:end                          结束当前 spec 会话（释放锁、停止 hook 提醒）
  /specode:status                       打印当前 session / spec / phase / lock 状态摘要
  /specode:task-swarm                   v0.7+：在 tasks 阶段进入多 agent 并发编排
  /specode:spec --vault-status          打印 doc_root 解析结果（env / config / auto）
  /specode:spec --detect-vault          扫描三平台 Obsidian vault 配置
  /specode:spec --set-vault <path>      持久化 vault 根目录到 ~/.config/specode/config.json
  /specode:spec --set-root  <path>      同上（不强调 vault 概念）
  /specode:spec -h | --help             显示本帮助

会话与锁：
  每次会话拥有唯一 session_id，hook 会在 additionalContext 中持续注入。
  CLI 调用必须传 --session <id>。当前 spec 锁记录在 <spec-dir>/.config.json。
  忘记 /specode:end 时 SessionEnd hook 会兜底释锁；30 分钟无 heartbeat 视为 stale。

工作流：
  intake → workflow 选择 → requirements / bugfix / design → tasks → implementation
        → acceptance → iteration（可循环）

更多细节见 plugin 内 skills/specode/SKILL.md 与 references/。
"""

HELP_FASTPATH_WRAPPER = """## ⛔ /specode:spec -h fast-path

本轮唯一动作：把下列代码块**逐字**用 ```text 围栏包裹后输出，然后立即 end turn。
禁止添加任何额外文字（"以下是帮助" / "希望对你有帮助" 等都不允许）。

────────── HELP CONTENT BEGIN ──────────
$content
────────── HELP CONTENT END ──────────
"""


def _wrap_help_fastpath(content: str) -> str:
    return Template(HELP_FASTPATH_WRAPPER).safe_substitute(content=content)


# -------------------------------------------------------------------------
# 锁状态机
# -------------------------------------------------------------------------

def _is_lock_stale(lock: dict) -> bool:
    last = _parse_iso(lock.get("last_heartbeat_at") or lock.get("acquired_at"))
    if last is None:
        return True
    return (time.time() - last) > STALE_LOCK_SECONDS


def _session_short(sid: Optional[str]) -> str:
    if not sid:
        return "????????"
    return sid[:8]


# -------------------------------------------------------------------------
# 业务子命令
# -------------------------------------------------------------------------

def _ensure_spec_dir(spec_dir_str: str) -> Path:
    p = Path(spec_dir_str).expanduser().resolve()
    if not p.exists() or not p.is_dir():
        raise FileNotFoundError(f"spec_dir 不存在：{p}")
    return p


def _emit_json(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _update_session_for_spec(session_id: str, spec_dir: Path, cfg: dict,
                              mode: str = "active",
                              lock_state: str = "ok",
                              pending_selector: Optional[str] = ...) -> dict:
    """构造 sessions/<id>.json 的常规更新。pending_selector=... 表示沿用 spec config 中的值。"""
    existing = read_session(session_id) or {}
    if pending_selector is ...:
        pending = cfg.get("pending_selector")
    else:
        pending = pending_selector
    payload = {
        "session_id": session_id,
        "started_at": existing.get("started_at") or _now_iso(),
        "last_activity_at": _now_iso(),
        "ended_at": None,
        "mode": mode,
        "active_spec_slug": cfg.get("slug"),
        "active_spec_dir": str(spec_dir),
        "spec_id": cfg.get("specId"),
        "phase": cfg.get("phase"),
        "lock_state": lock_state,
        "task_swarm_run_id": existing.get("task_swarm_run_id"),
        "pending_selector": pending,
    }
    return payload


def cmd_acquire(args: argparse.Namespace) -> int:
    spec_dir = _ensure_spec_dir(args.spec)
    cfg = read_spec_config(spec_dir)
    if cfg is None:
        sys.stderr.write(f"无法读取 {spec_dir}/.config.json\n")
        return 1

    now = _now_iso()
    lock = cfg.get("lock") or {}
    holder = lock.get("holder")

    if holder and holder != args.session and not _is_lock_stale(lock) and not args.force:
        _emit_json({
            "ok": False,
            "reason": "LockHeld",
            "holder": holder,
            "last_heartbeat_at": lock.get("last_heartbeat_at"),
        })
        return 4

    # 备份用于回滚
    prior_cfg = json.loads(json.dumps(cfg))
    prior_session_blob: Optional[str] = None
    sp = session_file_path(args.session)
    if sp.exists():
        try:
            prior_session_blob = sp.read_text(encoding="utf-8")
        except Exception:
            prior_session_blob = None

    cfg["lock"] = {
        "holder": args.session,
        "acquired_at": now,
        "last_heartbeat_at": now,
    }
    try:
        write_spec_config_atomic(spec_dir, cfg)
    except Exception as e:
        sys.stderr.write(f"写入 spec config 失败：{e}\n")
        return 1

    try:
        session_payload = _update_session_for_spec(args.session, spec_dir, cfg,
                                                   mode="active", lock_state="ok")
        write_session_atomic(args.session, session_payload)
    except Exception as e:
        # 回滚 spec config
        try:
            write_spec_config_atomic(spec_dir, prior_cfg)
        except Exception:
            pass
        sys.stderr.write(f"写入 sessions 失败，已回滚 spec config：{e}\n")
        return 1

    _emit_json({"ok": True, "holder": args.session, "acquired_at": now})
    return 0


def cmd_release(args: argparse.Namespace) -> int:
    spec_dir = _ensure_spec_dir(args.spec)
    cfg = read_spec_config(spec_dir)
    if cfg is None:
        sys.stderr.write(f"无法读取 {spec_dir}/.config.json\n")
        return 0  # release 容忍：spec config 缺失视作已释放
    prior_cfg = json.loads(json.dumps(cfg))
    lock = cfg.get("lock") or {}
    if lock.get("holder") == args.session:
        cfg["lock"] = None
        try:
            write_spec_config_atomic(spec_dir, cfg)
        except Exception as e:
            sys.stderr.write(f"释放锁写入失败：{e}\n")
            return 1
    # 更新 sessions
    try:
        existing = read_session(args.session) or {}
        existing["last_activity_at"] = _now_iso()
        existing["lock_state"] = "released"
        write_session_atomic(args.session, existing)
    except Exception as e:
        # 回滚 spec config
        try:
            write_spec_config_atomic(spec_dir, prior_cfg)
        except Exception:
            pass
        sys.stderr.write(f"写入 sessions 失败，已回滚 spec config：{e}\n")
        return 1
    _emit_json({"ok": True, "released_at": _now_iso()})
    return 0


def cmd_heartbeat(args: argparse.Namespace) -> int:
    spec_dir = _ensure_spec_dir(args.spec)
    cfg = read_spec_config(spec_dir)
    if cfg is None:
        sys.stderr.write(f"无法读取 {spec_dir}/.config.json\n")
        return 1
    lock = cfg.get("lock") or {}
    if lock.get("holder") != args.session:
        _emit_json({"ok": False, "reason": "lock_lost", "holder": lock.get("holder")})
        return 1
    prior_cfg = json.loads(json.dumps(cfg))
    now = _now_iso()
    cfg["lock"]["last_heartbeat_at"] = now
    try:
        write_spec_config_atomic(spec_dir, cfg)
    except Exception as e:
        sys.stderr.write(f"heartbeat 写入失败：{e}\n")
        return 1
    try:
        existing = read_session(args.session) or {}
        existing["last_activity_at"] = now
        existing["lock_state"] = "ok"
        write_session_atomic(args.session, existing)
    except Exception as e:
        try:
            write_spec_config_atomic(spec_dir, prior_cfg)
        except Exception:
            pass
        sys.stderr.write(f"heartbeat sessions 写入失败，已回滚：{e}\n")
        return 1
    _emit_json({"ok": True, "last_heartbeat_at": now})
    return 0


def cmd_verify_lock(args: argparse.Namespace) -> int:
    spec_dir = _ensure_spec_dir(args.spec)
    cfg = read_spec_config(spec_dir)
    if cfg is None:
        sys.stderr.write(f"无法读取 {spec_dir}/.config.json\n")
        return 3
    lock = cfg.get("lock") or {}
    holder = lock.get("holder")
    if not holder:
        _emit_json({"ok": False, "reason": "not_held"})
        return 3
    if holder != args.session:
        if _is_lock_stale(lock):
            _emit_json({"ok": False, "reason": "stale_lock", "holder": holder})
            return 3
        _emit_json({"ok": False, "reason": "evicted", "holder": holder})
        return 3
    _emit_json({"ok": True, "holder": holder, "last_heartbeat_at": lock.get("last_heartbeat_at")})
    return 0


def cmd_phase_transition(args: argparse.Namespace) -> int:
    if args.frm not in VALID_PHASES or args.to not in VALID_PHASES:
        sys.stderr.write(f"非法 phase：{args.frm} → {args.to}\n")
        return 1
    spec_dir = _ensure_spec_dir(args.spec)
    cfg = read_spec_config(spec_dir)
    if cfg is None:
        sys.stderr.write(f"无法读取 {spec_dir}/.config.json\n")
        return 1
    lock = cfg.get("lock") or {}
    if lock.get("holder") != args.session:
        _emit_json({"ok": False, "reason": "lock_lost"})
        return 1
    if cfg.get("phase") != args.frm:
        _emit_json({
            "ok": False,
            "reason": "phase_mismatch",
            "current": cfg.get("phase"),
            "expected_from": args.frm,
        })
        return 1
    prior_cfg = json.loads(json.dumps(cfg))
    prior_session = read_session(args.session)
    cfg["phase"] = args.to
    # 自动推断 pending_selector
    auto = _auto_pending_selector(args.to, cfg)
    cfg["pending_selector"] = auto
    try:
        write_spec_config_atomic(spec_dir, cfg)
    except Exception as e:
        sys.stderr.write(f"phase-transition 写 spec config 失败：{e}\n")
        return 1
    try:
        payload = _update_session_for_spec(args.session, spec_dir, cfg,
                                           mode="active", lock_state="ok",
                                           pending_selector=auto)
        write_session_atomic(args.session, payload)
    except Exception as e:
        try:
            write_spec_config_atomic(spec_dir, prior_cfg)
            if prior_session is not None:
                write_session_atomic(args.session, prior_session)
        except Exception:
            pass
        sys.stderr.write(f"phase-transition 写 sessions 失败，已回滚：{e}\n")
        return 1
    _emit_json({"ok": True, "phase": args.to, "pending_selector": auto})
    return 0


def _auto_pending_selector(phase: str, cfg: dict) -> Optional[str]:
    """根据 phase 推断默认 pending_selector（命令层可显式覆写）。"""
    workflow = cfg.get("workflow")
    if phase == "intake":
        return "workflow-choice"
    if phase == "requirements":
        return "doc-confirm-requirements"
    if phase == "bugfix":
        return "doc-confirm-bugfix"
    if phase == "design":
        return "doc-confirm-design"
    if phase == "tasks":
        return "tasks-execution"
    if phase == "implementation":
        return None
    if phase == "acceptance":
        return "acceptance-gate"
    if phase == "iteration":
        return "iteration-scope"
    return None


def cmd_load(args: argparse.Namespace) -> int:
    spec_dir = _ensure_spec_dir(args.spec)
    cfg = read_spec_config(spec_dir)
    if cfg is None:
        sys.stderr.write(f"无法读取 {spec_dir}/.config.json\n")
        return 1
    _emit_json({
        "ok": True,
        "spec_dir": str(spec_dir),
        "config": cfg,
    })
    return 0


def cmd_continue(args: argparse.Namespace) -> int:
    spec_dir = _ensure_spec_dir(args.spec)
    cfg = read_spec_config(spec_dir)
    if cfg is None:
        sys.stderr.write(f"无法读取 {spec_dir}/.config.json\n")
        return 1
    lock = cfg.get("lock") or {}
    holder = lock.get("holder")
    mode = "active"
    lock_state = "ok"
    pending = cfg.get("pending_selector")

    if holder and holder != args.session and not _is_lock_stale(lock) and not args.force:
        if args.readonly:
            mode = "readonly"
            lock_state = "readonly"
        else:
            # 提示走 takeover selector
            cfg["pending_selector"] = "takeover-options"
            try:
                write_spec_config_atomic(spec_dir, cfg)
            except Exception as e:
                sys.stderr.write(f"写 spec config 失败：{e}\n")
                return 1
            try:
                payload = _update_session_for_spec(args.session, spec_dir, cfg,
                                                   mode="readonly", lock_state="readonly",
                                                   pending_selector="takeover-options")
                write_session_atomic(args.session, payload)
            except Exception as e:
                sys.stderr.write(f"写 sessions 失败：{e}\n")
                return 1
            _emit_json({
                "ok": False,
                "reason": "LockHeld",
                "holder": holder,
                "pending_selector": "takeover-options",
                "spec_dir": str(spec_dir),
            })
            return 4
    else:
        # 抢锁（force / stale / 同 session / 无 holder）
        prior_cfg = json.loads(json.dumps(cfg))
        now = _now_iso()
        cfg["lock"] = {
            "holder": args.session,
            "acquired_at": now,
            "last_heartbeat_at": now,
        }
        try:
            write_spec_config_atomic(spec_dir, cfg)
        except Exception as e:
            sys.stderr.write(f"写 spec config 失败：{e}\n")
            return 1
        try:
            payload = _update_session_for_spec(args.session, spec_dir, cfg,
                                               mode=mode, lock_state=lock_state,
                                               pending_selector=pending)
            write_session_atomic(args.session, payload)
        except Exception as e:
            try:
                write_spec_config_atomic(spec_dir, prior_cfg)
            except Exception:
                pass
            sys.stderr.write(f"写 sessions 失败，已回滚 spec config：{e}\n")
            return 1
        # 更新 active-pointer
        try:
            root = Path(cfg.get("doc_root") or spec_dir.parent.parent)
            active_path = root / ".active-specode.json"
            _atomic_write_json(active_path, {
                "active_spec_slug": cfg.get("slug"),
                "active_spec_dir": str(spec_dir),
                "specId": cfg.get("specId"),
                "updatedAt": now,
                "session_id": args.session,
            })
        except Exception:
            pass

    _emit_json({
        "ok": True,
        "spec_dir": str(spec_dir),
        "mode": mode,
        "phase": cfg.get("phase"),
        "pending_selector": pending,
    })
    return 0


def cmd_end(args: argparse.Namespace) -> int:
    existing = read_session(args.session)
    if existing is None:
        # 即使 sessions 文件不存在，也写一份 ended 状态，便于排查
        existing = {
            "session_id": args.session,
            "started_at": _now_iso(),
        }
    spec_dir_str = existing.get("active_spec_dir")
    prior_cfg: Optional[dict] = None
    spec_dir: Optional[Path] = None
    if spec_dir_str:
        try:
            spec_dir = Path(spec_dir_str)
            if spec_dir.exists():
                cfg = read_spec_config(spec_dir)
                if cfg is not None:
                    prior_cfg = json.loads(json.dumps(cfg))
                    lock = cfg.get("lock") or {}
                    if lock.get("holder") == args.session:
                        cfg["lock"] = None
                        try:
                            write_spec_config_atomic(spec_dir, cfg)
                        except Exception as e:
                            sys.stderr.write(f"释锁写入失败：{e}\n")
                            return 1
        except Exception as e:
            sys.stderr.write(f"end 读取 spec config 出错：{e}\n")

    existing["mode"] = "ended"
    existing["ended_at"] = _now_iso()
    existing["lock_state"] = "released"
    existing["pending_selector"] = None
    try:
        write_session_atomic(args.session, existing)
    except Exception as e:
        # 回滚 spec config
        if spec_dir is not None and prior_cfg is not None:
            try:
                write_spec_config_atomic(spec_dir, prior_cfg)
            except Exception:
                pass
        sys.stderr.write(f"sessions 写入失败，已回滚：{e}\n")
        return 1

    _emit_json({"ok": True, "ended_at": existing["ended_at"]})
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    sess = read_session(args.session)
    if sess is None:
        _emit_json({"ok": False, "reason": "session_not_found", "session_id": args.session})
        return 0
    payload = {"ok": True, "session": sess}
    spec_dir_str = sess.get("active_spec_dir")
    if spec_dir_str:
        try:
            cfg = read_spec_config(Path(spec_dir_str))
            if cfg is not None:
                payload["spec_config"] = cfg
        except Exception:
            pass
    _emit_json(payload)
    return 0


def cmd_read_session(args: argparse.Namespace) -> int:
    sess = read_session(args.session)
    if sess is None:
        _emit_json({"ok": False, "reason": "session_not_found"})
        return 0
    _emit_json(sess)
    return 0


def cmd_list_specs(args: argparse.Namespace) -> int:
    """列出当前 doc_root 下所有 spec 的状态摘要。

    输出 JSON:
      {ok, root, source, specs: [...], reason?}
    每个 spec 元素：
      {slug, dir, specId, displayName, phase, iterationRound,
       lock_state, holder, last_heartbeat_at, pending_selector,
       mtimes: {...}}
    """
    import datetime as _dt
    try:
        import spec_vault  # type: ignore
    except Exception as e:
        _emit_json({
            "ok": False,
            "reason": f"spec_vault_import_failed: {e}",
            "root": None,
            "source": "error",
            "specs": [],
        })
        return 0

    override = args.root
    try:
        root, source = spec_vault.resolve_doc_root(override)
    except Exception as e:
        _emit_json({
            "ok": False,
            "reason": f"resolve_doc_root_failed: {e}",
            "root": None,
            "source": "error",
            "specs": [],
        })
        return 0

    if root is None:
        _emit_json({
            "ok": False,
            "reason": "no_doc_root",
            "root": None,
            "source": source,
            "specs": [],
        })
        return 0

    specs_dir = Path(root) / "specs"
    if not specs_dir.exists() or not specs_dir.is_dir():
        _emit_json({
            "ok": True,
            "root": str(root),
            "source": source,
            "specs": [],
        })
        return 0

    spec_doc_names = [
        "requirements.md",
        "bugfix.md",
        "design.md",
        "tasks.md",
        "implementation-log.md",
    ]

    entries: list[dict] = []
    try:
        children = sorted(specs_dir.iterdir(), key=lambda p: p.name)
    except Exception:
        children = []

    for child in children:
        if not child.is_dir():
            continue
        cfg_path = child / ".config.json"
        if not cfg_path.exists():
            continue
        try:
            with cfg_path.open("r", encoding="utf-8") as fh:
                cfg = json.load(fh)
            if not isinstance(cfg, dict):
                continue
        except Exception:
            continue

        lock = cfg.get("lock") or {}
        # 业务侧实际字段名是 holder；兼容历史 session_id / claude_session_id 兜底
        holder_id = (
            lock.get("holder") or lock.get("session_id") or lock.get("claude_session_id")
            if isinstance(lock, dict) else None
        )
        if holder_id:
            if _is_lock_stale(lock):
                lock_state = "stale"
            else:
                lock_state = "held"
        else:
            lock_state = "free"
        holder_short = holder_id[:8] if isinstance(holder_id, str) and holder_id else None

        mtimes: dict[str, str] = {}
        for name in spec_doc_names:
            doc_path = child / name
            try:
                if doc_path.exists():
                    ts = doc_path.stat().st_mtime
                    mtimes[name] = (
                        _dt.datetime.utcfromtimestamp(ts)
                        .strftime("%Y-%m-%dT%H:%M:%SZ")
                    )
            except Exception:
                continue

        display_name = cfg.get("displayName") or cfg.get("requirementName")

        entries.append({
            "slug": cfg.get("slug") or child.name,
            "dir": str(child),
            "specId": cfg.get("specId"),
            "displayName": display_name,
            "phase": cfg.get("phase"),
            "iterationRound": cfg.get("iterationRound", 0),
            "lock_state": lock_state,
            "holder": holder_short,
            "last_heartbeat_at": lock.get("last_heartbeat_at") if isinstance(lock, dict) else None,
            "pending_selector": cfg.get("pending_selector"),
            "mtimes": mtimes,
        })

    _emit_json({
        "ok": True,
        "root": str(root),
        "source": source,
        "specs": entries,
    })
    return 0


# -------------------------------------------------------------------------
# Hook 子命令
# -------------------------------------------------------------------------

def _read_stdin_payload() -> dict:
    """读 hook stdin payload。**不要 block**：如 stdin 不是管道，立刻返回 {}。"""
    data: dict = {}
    try:
        if sys.stdin is None:
            return data
        # 判断是否 tty/无管道
        try:
            isatty = sys.stdin.isatty()
        except Exception:
            isatty = True
        if isatty:
            return data
        raw = sys.stdin.read()
        if not raw:
            return data
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict):
                return obj
        except Exception:
            return data
    except Exception:
        return data
    return data


def _emit_hook_additional_context(text: str, hook_event_name: str = "UserPromptSubmit") -> None:
    """按宿主 hook 协议 emit additionalContext JSON。"""
    payload = {
        "hookSpecificOutput": {
            "hookEventName": hook_event_name,
            "additionalContext": text,
        }
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _bypass_active() -> bool:
    return os.environ.get("SPECODE_GUARD", "").lower() == "off"


def _safe_hook(fn):
    """装饰器：hook 子命令的最外层异常吞并，恒 exit 0。"""
    def wrapper(args: argparse.Namespace) -> int:
        if _bypass_active():
            return 0
        # log hook invocation（0.10.0+；日志失败不阻断 hook）
        with contextlib.suppress(Exception):
            _log_event("hook_invoked", {"hook": fn.__name__}, session_id=None)
        try:
            fn(args)
        except SystemExit:
            raise
        except BaseException:
            with contextlib.suppress(Exception):
                # 写一份本地 trace 便于排查；忽略 IO 错误
                err = traceback.format_exc()
                sys.stderr.write(f"specode hook 异常已吞并：\n{err}\n")
                _log_event("hook_exception", {"hook": fn.__name__, "trace_head": err[:500]}, session_id=None)
        return 0
    return wrapper


# -------------------------------------------------------------------------
# 0.10.0+ 工具调用日志 hook（PreToolUse / PostToolUse 全通配，仅落日志）
# -------------------------------------------------------------------------

@_safe_hook
def hook_on_log_pre_tool_use(args: argparse.Namespace) -> None:
    """PreToolUse 全通配 hook：抓主代理每个工具调用前的 payload。仅落日志，不注入。"""
    payload = _read_stdin_payload()
    session_id = payload.get("session_id") or payload.get("sessionId") or args.session_override
    _log_event("tool_pre", {
        "tool_name": payload.get("tool_name") or payload.get("toolName"),
        "tool_input": payload.get("tool_input") or payload.get("toolInput"),
    }, session_id=session_id)


@_safe_hook
def hook_on_log_post_tool_use(args: argparse.Namespace) -> None:
    """PostToolUse 全通配 hook：抓主代理每个工具调用后的 payload。仅落日志，不注入。"""
    payload = _read_stdin_payload()
    session_id = payload.get("session_id") or payload.get("sessionId") or args.session_override
    _log_event("tool_post", {
        "tool_name": payload.get("tool_name") or payload.get("toolName"),
        "tool_response_head": str(payload.get("tool_response") or payload.get("toolResponse") or "")[:300],
    }, session_id=session_id)


# ---- on-session-start ----

@_safe_hook
def hook_on_session_start(args: argparse.Namespace) -> None:
    payload = _read_stdin_payload()
    session_id = payload.get("session_id") or payload.get("sessionId") or args.session_override
    if not session_id:
        return
    existing = read_session(session_id)
    if existing is None:
        new_payload = {
            "session_id": session_id,
            "started_at": _now_iso(),
            "last_activity_at": _now_iso(),
            "ended_at": None,
            "mode": "idle",
            "active_spec_slug": None,
            "active_spec_dir": None,
            "spec_id": None,
            "phase": None,
            "lock_state": "released",
            "task_swarm_run_id": None,
            "pending_selector": None,
        }
        try:
            write_session_atomic(session_id, new_payload)
        except Exception:
            pass
        existing = new_payload
    else:
        existing["last_activity_at"] = _now_iso()
        # 断线重连：如果原 ended，重新激活为 idle
        if existing.get("mode") == "ended":
            existing["mode"] = "idle"
            existing["ended_at"] = None
        try:
            write_session_atomic(session_id, existing)
        except Exception:
            pass

    mode = existing.get("mode") or "idle"
    slug = existing.get("active_spec_slug") or "无"
    text = (
        "## Specode session 就绪\n\n"
        f"当前会话 session_id: {session_id}\n"
        f"后续调用 specode CLI 时请始终用 `--session {session_id}` 传入。\n\n"
        f"（此 session 当前 mode={mode}，spec={slug}；\n"
        "  如需开始新 spec，使用 `/specode:spec <需求>`；\n"
        "  如需恢复，使用 `/specode:continue [slug]`。）\n"
    )
    if mode == "active" and existing.get("active_spec_slug"):
        text += "\n"
        text += SPEC_MODE_CONTINUE_REMINDER.replace("<slug>", existing.get("active_spec_slug") or "?").replace("<phase>", existing.get("phase") or "?")

    _emit_hook_additional_context(text, hook_event_name="SessionStart")


# ---- on-user-prompt ----

FAST_PATH_HELP = re.compile(r"^\s*/specode:spec\s+(-h|--help)\s*$", re.IGNORECASE)
FAST_PATH_VAULT = re.compile(
    r"^\s*/specode:spec\s+--(vault-status|detect-vault|sync-status)\s*$",
    re.IGNORECASE,
)


def _run_subcmd(argv: list[str]) -> str:
    """运行 spec_vault.py 等子命令，捕获 stdout。失败返回错误描述。"""
    try:
        proc = subprocess.run(
            [sys.executable, str(THIS_DIR / argv[0])] + argv[1:],
            capture_output=True, text=True, timeout=10,
        )
        out = proc.stdout.strip()
        if proc.returncode not in (0, 3):
            out = (out + "\n[exit=" + str(proc.returncode) + "]\n" + proc.stderr).strip()
        return out or "(无输出)"
    except Exception as e:
        return f"(子命令执行失败: {e})"


@_safe_hook
def hook_on_user_prompt(args: argparse.Namespace) -> None:
    payload = _read_stdin_payload()
    session_id = payload.get("session_id") or payload.get("sessionId")
    prompt = payload.get("prompt") or ""
    if not session_id:
        return

    # fast-path: help
    if FAST_PATH_HELP.match(prompt):
        text = _wrap_help_fastpath(HELP_OUTPUT_TEXT.rstrip())
        _emit_hook_additional_context(text, hook_event_name="UserPromptSubmit")
        return

    # fast-path: vault-status / detect-vault / sync-status
    m = FAST_PATH_VAULT.match(prompt)
    if m:
        flag = m.group(1).lower()
        if flag == "vault-status":
            content = _run_subcmd(["spec_vault.py", "status"])
        elif flag == "detect-vault":
            content = _run_subcmd(["spec_vault.py", "detect"])
        elif flag == "sync-status":
            # v0.6 暂未实现 sync-status CLI；输出占位
            content = json.dumps({
                "note": "sync-status 在 v0.6 尚未实现；将随 v0.7 task-swarm 引入。",
            }, ensure_ascii=False, indent=2)
        else:
            content = "(unknown vault fast-path)"
        text = (
            "## ⛔ /specode:spec --" + flag + " fast-path\n\n"
            "本轮唯一动作：把下列代码块**逐字**用 ```text 围栏包裹后输出，然后立即 end turn。\n"
            "禁止添加任何额外文字。\n\n"
            "────────── CONTENT BEGIN ──────────\n"
            f"{content}\n"
            "────────── CONTENT END ──────────\n"
        )
        _emit_hook_additional_context(text, hook_event_name="UserPromptSubmit")
        return

    # 常规路径：按 mode 叠加
    sess = read_session(session_id)
    if sess is None:
        return
    sess["last_activity_at"] = _now_iso()
    try:
        write_session_atomic(session_id, sess)
    except Exception:
        pass

    mode = sess.get("mode") or "idle"
    if mode in ("idle", "ended"):
        return

    slug = sess.get("active_spec_slug") or "?"
    phase = sess.get("phase") or "?"
    spec_dir = sess.get("active_spec_dir")
    pending = sess.get("pending_selector")
    short = _session_short(session_id)

    parts: list[str] = []

    # (a) session_id 提醒
    parts.append(
        "## Specode session 提醒\n\n"
        f"当前会话 session_id: {session_id}\n"
        f"调用任何 specode CLI 时请使用 `--session {session_id}`。\n"
    )

    # (b) selector 提示
    if mode == "active" and pending:
        ctx: dict[str, str] = {
            "slug": slug,
            "phase": phase,
            "spec_dir": spec_dir or "?",
            "source_text_head": "?",
            "n_required": "?",
            "n_optional": "?",
            "other_id_short": "?",
            "last_heartbeat": "?",
            "n_pass": "?",
            "n_fail": "?",
        }
        # 填入 spec config 中的派生值
        if spec_dir:
            try:
                cfg = read_spec_config(Path(spec_dir)) or {}
                src = cfg.get("source_text") or ""
                if src:
                    ctx["source_text_head"] = src[:60].replace("\n", " ")
                lock = cfg.get("lock") or {}
                other = lock.get("holder")
                if other and other != session_id:
                    ctx["other_id_short"] = _session_short(other)
                    ctx["last_heartbeat"] = str(lock.get("last_heartbeat_at") or "?")
            except Exception:
                pass
        sel = _fill_selector(pending, ctx)
        if sel:
            parts.append(sel)
    elif mode == "readonly" and pending:
        parts.append(
            "## ℹ️ 只读模式：当前 pending_selector="
            f"`{pending}` （仅信息提示，只读不能确认）\n"
        )

    # (c) 文档优先提醒
    if mode == "active":
        parts.append(
            DOC_PRIORITY_REMINDER_ACTIVE
            .replace("<slug>", slug)
            .replace("<phase>", phase)
        )
    elif mode == "readonly":
        parts.append(
            DOC_PRIORITY_REMINDER_READONLY
            .replace("<slug>", slug)
            .replace("<phase>", phase)
        )

    # (d) 状态行 footer
    if mode in ("active", "readonly"):
        footer = (
            STATUS_FOOTER_TEMPLATE
            .replace("<slug>", slug)
            .replace("<session_short>", short)
            .replace("<phase>", phase)
            .replace("<mode>", mode)
        )
        parts.append(footer)

    # (e) 模式提醒
    if mode == "active":
        parts.append(
            SPEC_MODE_CONTINUE_REMINDER
            .replace("<slug>", slug)
            .replace("<phase>", phase)
        )
    elif mode == "readonly":
        parts.append(
            SPEC_MODE_READONLY_REMINDER
            .replace("<slug>", slug)
            .replace("<phase>", phase)
        )

    if not parts:
        return
    text = "\n\n".join(p.rstrip() for p in parts) + "\n"
    _emit_hook_additional_context(text, hook_event_name="UserPromptSubmit")


# ---- on-stop ----

@_safe_hook
def hook_on_stop(args: argparse.Namespace) -> None:
    payload = _read_stdin_payload()
    session_id = payload.get("session_id") or payload.get("sessionId")
    if not session_id:
        return
    sess = read_session(session_id)
    if sess is None:
        return
    sess["last_activity_at"] = _now_iso()
    try:
        write_session_atomic(session_id, sess)
    except Exception:
        pass
    mode = sess.get("mode") or "idle"
    if mode in ("idle", "ended"):
        return
    slug = sess.get("active_spec_slug") or "?"
    phase = sess.get("phase") or "?"
    if mode == "active":
        text_parts = [
            CODE_DOC_SYNC_STOP.replace("<slug>", slug).replace("<phase>", phase),
            SPEC_MODE_CONTINUE_REMINDER.replace("<slug>", slug).replace("<phase>", phase),
        ]
    else:
        text_parts = [
            SPEC_MODE_READONLY_REMINDER.replace("<slug>", slug).replace("<phase>", phase),
        ]
    text = "\n\n".join(p.rstrip() for p in text_parts) + "\n"
    _emit_hook_additional_context(text, hook_event_name="Stop")


# ---- on-session-end ----

@_safe_hook
def hook_on_session_end(args: argparse.Namespace) -> None:
    payload = _read_stdin_payload()
    session_id = payload.get("session_id") or payload.get("sessionId")
    if not session_id:
        return
    sess = read_session(session_id)
    if sess is None:
        return
    spec_dir_str = sess.get("active_spec_dir")
    if spec_dir_str:
        try:
            spec_dir = Path(spec_dir_str)
            if spec_dir.exists():
                cfg = read_spec_config(spec_dir)
                if cfg is not None:
                    lock = cfg.get("lock") or {}
                    if lock.get("holder") == session_id:
                        cfg["lock"] = None
                        with contextlib.suppress(Exception):
                            write_spec_config_atomic(spec_dir, cfg)
        except Exception:
            pass
    sess["mode"] = "ended"
    sess["ended_at"] = _now_iso()
    sess["lock_state"] = "released"
    sess["pending_selector"] = None
    with contextlib.suppress(Exception):
        write_session_atomic(session_id, sess)
    # 不输出 additionalContext


# ---- v0.7 on-task-completed（task-swarm 节点提醒） ----

TASK_COMPLETED_TRAILER = "\n\n本提醒仅供参考；fork 谁、是否 fork、何时 writeback 仍由你判断；可忽略。"


def _run_task_swarm_plan(run_id: str) -> Optional[dict]:
    """调子进程 task_swarm.py plan --run <run_id>，解析 stdout JSON 返回 dict。

    任何失败（exit != 0、JSON 解析失败、子进程异常）返回 None。
    """
    try:
        proc = subprocess.run(
            [sys.executable, str(THIS_DIR / "task_swarm.py"), "plan", "--run", run_id],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    out = (proc.stdout or "").strip()
    if not out:
        return None
    try:
        obj = json.loads(out)
        if isinstance(obj, dict):
            return obj
    except Exception:
        return None
    return None


def _format_plan_context(plan: dict) -> str:
    """按 references/task-swarm.md §6 hook 提醒矩阵把 plan dict 渲染成 additionalContext 文本。"""
    phase = str(plan.get("phase") or "?")
    action = str(plan.get("action") or "")
    group = plan.get("group")
    rnd = plan.get("round")
    in_flight = plan.get("in_flight") or []
    fork = plan.get("fork") or []
    msg = str(plan.get("message") or "")
    n_fork = len(fork) if isinstance(fork, list) else 0
    n_in_flight = len(in_flight) if isinstance(in_flight, list) else 0

    # 选择具体建议文本（references/task-swarm.md §6 9 种状态）
    if action == "deadloop" or phase == "error":
        body = (
            f"⚠️ 死循环检测：g{group} 已连续 3 轮同一 fail 签名。\n"
            "建议停止本 group，向用户报告 `failed-deadloop`，让用户介入。"
        )
    elif action == "all-done" or phase == "done":
        body = (
            "全部 group 已完成。请按 SKILL.md 退出 task-swarm 模式，"
            "回到 spec-mode acceptance phase。"
        )
    elif phase == "coding" and action == "coding-waiting":
        body = (
            f"coding phase 还在等 {n_in_flight} 个 subagent；"
            "无需 fork 新 agent，等齐后再判断。"
        )
    elif phase == "coding" and action == "coding-fork":
        body = (
            f"本 group 开始 coding。请按下面 {n_fork} 个 coder agent_key fork"
            "（同 message 内并发）。"
        )
    elif phase == "review" and action == "review-fork":
        body = (
            "本 group coder 已全部返回。请 fork **1 个** `task-swarm-reviewer`，"
            "prompt 已生成。"
        )
    elif phase == "p0-fix" and action == "p0-fix-fork":
        body = (
            f"reviewer 提了带证据 P0。请按 P0 涉及文件 fork **{n_fork}** 个 "
            "`task-swarm-coder`（p0-fix），prompt 已生成。\n"
            "提醒：reviewer 修复**只触发一次**，不 re-review。"
        )
    elif phase == "p0-fix" and action == "p0-fix-waiting":
        body = f"p0-fix 仍有 {n_in_flight} 个 coder 未返回，等齐后再判断。"
    elif phase == "validation" and action == "validation-fork":
        body = (
            "reviewer 无带证据 P0（或全部降级为 advisory）。"
            "请 fork **1 个** `task-swarm-validator`，prompt 已生成。"
        )
    elif phase == "validation" and action == "validation-fork-after-p0":
        body = (
            "p0-fix coder 已返回。请 fork **1 个** `task-swarm-validator`，"
            "prompt 已生成。"
        )
    elif phase == "validation" and action == "validation-after-vfix":
        body = (
            "v-fix coder 已返回。请 fork **1 个** `task-swarm-validator` 验证。"
        )
    elif phase == "writeback" and action == "writeback":
        body = (
            "validator pass。请调 `task_swarm.py writeback "
            f"--run <run_id> --group {group}` 回写 tasks.md，然后进入下一 group。"
        )
    elif phase == "v-fix" and action == "v-fix-fork":
        body = (
            f"validator fail。请按 validation.md 的 fix_targets 各文件 "
            f"fork **{n_fork}** 个 `task-swarm-coder`（v-fix）。\n"
            "注意：validator fail 循环修复直到 pass。"
            f"本轮是 g{group}-r{rnd}。"
        )
    elif phase == "v-fix" and action == "v-fix-waiting":
        body = f"v-fix 仍有 {n_in_flight} 个 coder 未返回，等齐后再判断。"
    else:
        body = msg or f"phase={phase} action={action}（详见 plan 输出）"

    header = (
        f"## task-swarm 节点提醒（phase={phase}, "
        f"group={group if group is not None else '?'}, "
        f"round={rnd if rnd is not None else '?'}）\n\n"
    )
    return header + body + TASK_COMPLETED_TRAILER


@_safe_hook
def hook_on_task_completed(args: argparse.Namespace) -> None:
    payload = _read_stdin_payload()
    session_id = (
        payload.get("session_id")
        or payload.get("sessionId")
        or args.session_override
    )
    if not session_id:
        return
    sess = read_session(session_id)
    if sess is None:
        return
    run_id = sess.get("task_swarm_run_id")
    if not run_id:
        return

    plan = _run_task_swarm_plan(run_id)
    if isinstance(plan, dict):
        text = _format_plan_context(plan)
    else:
        # plan 调用失败 → 兜底文本
        text = (
            "## task-swarm 节点提醒\n\n"
            f"无法自动获取 task-swarm run `{run_id}` 的下一步建议——"
            "请手动调用：\n\n"
            "```bash\n"
            f"task_swarm.py plan --run {run_id}\n"
            "```\n\n"
            "拿到输出后再判断 fork 谁 / 是否 writeback。"
            + TASK_COMPLETED_TRAILER
        )
    _emit_hook_additional_context(text, hook_event_name="PostToolUse")


# ---- v0.8 on-heartbeat-quiet（静默续锁） ----

@_safe_hook
def hook_on_heartbeat_quiet(args: argparse.Namespace) -> None:
    payload = _read_stdin_payload()
    session_id = (
        payload.get("session_id")
        or payload.get("sessionId")
        or args.session_override
    )
    if not session_id:
        return
    sess = read_session(session_id)
    if sess is None:
        return
    if sess.get("mode") != "active":
        return
    spec_dir_str = sess.get("active_spec_dir")
    if not spec_dir_str:
        return
    spec_dir = Path(spec_dir_str)
    if not spec_dir.exists():
        return
    cfg = read_spec_config(spec_dir)
    if cfg is None:
        return
    lock = cfg.get("lock") or {}
    if not isinstance(lock, dict):
        return
    holder = lock.get("holder") or lock.get("session_id") or lock.get("claude_session_id")
    if holder != session_id:
        return

    now = _now_iso()
    lock["last_heartbeat_at"] = now
    cfg["lock"] = lock
    try:
        write_spec_config_atomic(spec_dir, cfg)
    except Exception:
        return
    sess["last_activity_at"] = now
    with contextlib.suppress(Exception):
        write_session_atomic(session_id, sess)
    # 不输出 additionalContext


# ---- v0.8 on-pre-tool-use（tasks.md 直写提醒） ----

@_safe_hook
def hook_on_pre_tool_use(args: argparse.Namespace) -> None:
    payload = _read_stdin_payload()
    session_id = (
        payload.get("session_id")
        or payload.get("sessionId")
        or args.session_override
    )
    if not session_id:
        return
    sess = read_session(session_id)
    if sess is None:
        return
    if sess.get("mode") != "active":
        return
    run_id = sess.get("task_swarm_run_id")
    if not run_id:
        return
    spec_dir_str = sess.get("active_spec_dir")
    if not spec_dir_str:
        return

    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return
    file_path = tool_input.get("file_path") or ""
    if not file_path or not isinstance(file_path, str):
        return

    try:
        edited = Path(file_path).resolve()
    except Exception:
        return
    try:
        tasks_md = (Path(spec_dir_str) / "tasks.md").resolve()
    except Exception:
        return

    if edited != tasks_md:
        return

    text = (
        "## ⚠ 检测到正在直接 Edit/Write `tasks.md`\n\n"
        f"task-swarm run `{run_id}` 进行中。直接编辑 `tasks.md` 会破坏 "
        "line-safe diff 约束，并让主代理 / state.json 之间的同步失效。\n\n"
        "请放弃当前编辑，改走：\n\n"
        "```bash\n"
        f"task_swarm.py writeback --run {run_id} --group <N>\n"
        "```\n\n"
        "本提醒**不阻断**当前工具调用——是否继续由你判断；"
        "若坚持直写，请准备好向用户解释 writeback CLI 的回写日志为何出现 diff 越界。"
    )
    _emit_hook_additional_context(text, hook_event_name="PreToolUse")


# -------------------------------------------------------------------------
# argparse 入口
# -------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="spec_session.py", description="specode session / lock / hook entry")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("acquire")
    p.add_argument("--spec", required=True)
    p.add_argument("--session", required=True)
    p.add_argument("--force", action="store_true")

    p = sub.add_parser("release")
    p.add_argument("--spec", required=True)
    p.add_argument("--session", required=True)

    p = sub.add_parser("heartbeat")
    p.add_argument("--spec", required=True)
    p.add_argument("--session", required=True)

    p = sub.add_parser("verify-lock")
    p.add_argument("--spec", required=True)
    p.add_argument("--session", required=True)

    p = sub.add_parser("phase-transition")
    p.add_argument("--spec", required=True)
    p.add_argument("--session", required=True)
    p.add_argument("--from", dest="frm", required=True)
    p.add_argument("--to", required=True)

    p = sub.add_parser("load")
    p.add_argument("--spec", required=True)

    p = sub.add_parser("continue")
    p.add_argument("--spec", required=True)
    p.add_argument("--session", required=True)
    p.add_argument("--force", action="store_true")
    p.add_argument("--readonly", action="store_true")

    p = sub.add_parser("end")
    p.add_argument("--session", required=True)

    p = sub.add_parser("status")
    p.add_argument("--session", required=True)

    p = sub.add_parser("read-session")
    p.add_argument("--session", required=True)

    p = sub.add_parser("list-specs")
    p.add_argument("--root", default=None,
                   help="doc root override；缺省按三层 resolve_doc_root")

    # hook 子命令（无必需参数；从 stdin 拿 session_id）
    for name in (
        "on-session-start",
        "on-user-prompt",
        "on-stop",
        "on-session-end",
        "on-task-completed",
        "on-heartbeat-quiet",
        "on-pre-tool-use",
        "on-log-pre-tool-use",
        "on-log-post-tool-use",
    ):
        ph = sub.add_parser(name)
        ph.add_argument("--session-override", default=None,
                        help="测试用：覆盖 stdin payload 中的 session_id")
        if name == "on-heartbeat-quiet":
            ph.add_argument("--quiet", action="store_true")

    return parser


COMMANDS = {
    "acquire": cmd_acquire,
    "release": cmd_release,
    "heartbeat": cmd_heartbeat,
    "verify-lock": cmd_verify_lock,
    "phase-transition": cmd_phase_transition,
    "load": cmd_load,
    "continue": cmd_continue,
    "end": cmd_end,
    "status": cmd_status,
    "read-session": cmd_read_session,
    "list-specs": cmd_list_specs,
    "on-session-start": hook_on_session_start,
    "on-user-prompt": hook_on_user_prompt,
    "on-stop": hook_on_stop,
    "on-session-end": hook_on_session_end,
    "on-task-completed": hook_on_task_completed,
    "on-heartbeat-quiet": hook_on_heartbeat_quiet,
    "on-pre-tool-use": hook_on_pre_tool_use,
    "on-log-pre-tool-use": hook_on_log_pre_tool_use,
    "on-log-post-tool-use": hook_on_log_post_tool_use,
}


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    fn = COMMANDS.get(args.cmd)
    if fn is None:
        parser.print_help()
        return 1
    # log cli 调用（0.10.0+；只记业务命令，hook 调用由 _safe_hook 已记）
    if not args.cmd.startswith("on-"):
        with contextlib.suppress(Exception):
            session_id = getattr(args, "session", None) or getattr(args, "session_override", None)
            _log_event("cli_call", {
                "script": "spec_session.py",
                "cmd": args.cmd,
                "spec": getattr(args, "spec", None),
                "phase_from": getattr(args, "frm", None),
                "phase_to": getattr(args, "to", None),
                "force": getattr(args, "force", False),
                "readonly": getattr(args, "readonly", False),
            }, session_id=session_id)
    rc = fn(args) or 0
    if not args.cmd.startswith("on-"):
        with contextlib.suppress(Exception):
            session_id = getattr(args, "session", None) or getattr(args, "session_override", None)
            _log_event("cli_exit", {"script": "spec_session.py", "cmd": args.cmd, "exit_code": rc}, session_id=session_id)
    return rc


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
