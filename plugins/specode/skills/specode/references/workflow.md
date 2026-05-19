# Workflow — Phase 协议详解

SKILL.md §Phase Order / §Workflow Selection 的运维细节版本。本文件**不**重复激活规则、状态行 footer、selector 三种类型与场景表 —— 那些在 SKILL.md 与 `references/selectors.md` 里。

## 0. Phase 序列总图

```
intake ──► requirements / bugfix ──► design ──► tasks ──► implementation ──► acceptance ──► iteration
 │ │ │ │ │ │
 │ ▼ ▼ ▼ ▼ ▼
 │ doc-confirm-* doc-confirm-* tasks-execution 推进 [ ] → [~] → [x] acceptance-gate iteration 子循环
 │ 选择器 选择器 选择器
 │
 ├─ 需求有歧义 → clarification-wizard（类型 B）+ clarification-done（类型 A）
 └─ workflow 不明 → workflow-choice（类型 A）
```

phase 切换永远走 `spec_session.py phase-transition --spec <dir> --session <id> --from <p> --to <p>`。**不要**手动改 `<spec-dir>/.config.json.currentPhase`。

## 1. `/specode:spec <需求>` Intake

### 1.1 输入解析

接受三种形式：

```text
/specode:spec <需求文本>
/specode:spec <需求文本> --root /path/to/dir
/specode:spec <文件路径>
```

`<需求文本>` 解析步骤：

1. **名称前缀解析**：检测前 30 字符内是否含 `<名称>：<内容>`（全角 `：`）或 `<名称>: <内容>`（半角 `:` 必须有空格）。命中：
 - 左半部分 → 显示名（中文允许；保留为 `requirementName`）
 - 右半部分 → 源需求文本（`--source-text`）
 - 不对路径 / URL / 无冒号输入做拆分
2. **slug 推导**（由你负责，CLI 不会从中文推 slug）：
 - 读完用户需求后，给一个**短 + 语义 + 英文 + 小写 + 连字符** ≤64 字符 slug
 - 例：`login-password-rule`、`undo-redo`、`dark-mode`、`api-rate-limit`
3. **文件路径模式**：若 `<需求>` 是一个可读文件路径，先 Read 该文件，把内容当成源文本继续走前两步。
4. 提取根目录提示（`--root`）、工作流提示（如用户已说"做个 bugfix"）、约束、验证期望。

### 1.2 调 `spec_init.py`

```bash
python3 plugins/specode/scripts/spec_init.py \
 --name <slug> \
 --requirement-name "<中文显示名>" \
 --source-text "<原始需求文本>" \
 --session <session_id> \
 [--root <override>] \
 [--detect-vault]
```

CLI 行为：

1. 三层文档根目录解析（详见 `references/obsidian.md`）。
2. 在 `<doc_root>/specs/<slug>/` 写 5 份骨架文档（按 `references/templates.md` 模板）+ `.config.json`（`specId` / `createdAt` / `phase=intake` / `lock` 字段指向 `--session`）。
3. 更新 `<doc_root>/.active-specode.json` active-pointer。
4. 强制双写 `~/.specode/sessions/<session_id>.json`（mode=active / active_spec_slug / phase=intake / lock_state=ok）。
5. 三步任一失败 → 回滚 + exit 1（半成功是禁区）。
6. 三层全 miss → exit 3 + 引导（SKILL.md §Document Root Resolution 给出引导文本）。
7. 成功 → 输出 JSON：`{"spec_dir": "...", "specId": "...", "session_id": "<id>", "phase": "intake"}`。

### 1.3 进入 intake 阶段后

如果需求有歧义 → 进 §1.4 澄清子流程；否则跳到 §2 workflow 选择。

只给了 root 没给需求 → 用一句话问用户"请告诉我本次要做的需求"，end turn。

### 1.4 Pre-requirements Clarification（Plan-mode）

→ 详见 SKILL.md §Pre-requirements Clarification 与 `references/selectors.md` §Plan-mode 澄清问答示例。

约束摘要：

- 留在 `intake` phase。
- **一次性** wizard 问完 2–4 个**无依赖**决策点；不要逐 turn 散问（`AskUserQuestion` 工具 `questions` 数组上限 4 个）。
- 子问题必须是"是 / 否 / 选哪条"具体问题；非互斥应拆类型。
- 子问题之间无依赖；每个 question `multiSelect=false`。
- "Other" 由工具自动提供；不需要手工加 `Type something` 等保留位。
- inputs 不足以构成决策点 → 不要塞进 wizard。
- 一个决策点都没有 → 跳到 `clarification-done`，不调 wizard 工具。

用户回复后下一轮：

1. 解析回答 → 把解决的内容写入待生成的 requirements.md / bugfix.md 待用（不要 mutating 任何文件）。
2. 呈现 `clarification-done`（类型 A，推荐选项 1 `进入下一阶段`）。
3. End turn。
4. 用户选 1 → 进 §2 workflow 选择；用户选 2 → 再发一轮 wizard。

## 2. Workflow 选择

`workflow-choice` 选择器（类型 A） → 详见 `references/selectors.md` §(1)。

三档定义：

| 选项 | 何时选 | 后续 phase 序列 |
|---|---|---|
| **Requirements first**（默认推荐） | 行为优先的新特性；先把 SHALL 写清楚，再补技术设计 | requirements → design → tasks → implementation → acceptance → iteration |
| **Technical Design first** | 架构约束已知；先把 design.md 框架定下来，再反推 requirements | design → requirements → tasks → implementation → acceptance → iteration |
| **Bugfix** | 缺陷修复 / 回归测试 | bugfix → design → tasks → implementation → acceptance → iteration |

用户选完 → 调 `spec_session.py phase-transition --from intake --to requirements / design / bugfix` → 进入对应 phase。

工作流选择写入 `<spec-dir>/.config.json.workflow` 字段（`requirements` / `design` / `bugfix` 之一）。

## 3. Requirements-first Flow

### 3.1 phase=requirements

1. fork `spec-writer` agent 生成 `requirements.md`。章节模板见 `references/templates.md` §requirements.md。
2. 按 SKILL.md §Document Output Brevity 报路径 + 3–8 条变更要点 + 未决问题。
3. 呈现 `doc-confirm-requirements`（类型 A，推荐选项 1 `确认`）。
4. End turn 等用户选。
5. 选 1 `确认` → phase-transition → design；选 2 `查看全文` → 完整 echo 文档后**再次**呈现同一 selector + end turn；选 3 `继续沟通` → 接收用户反馈 → 改文档 → 重出 step 2–3。

### 3.2 phase=design

1. fork spec-writer 生成 `design.md`（章节见 templates.md）。
2. 报路径 + 摘要。
3. `doc-confirm-design` 选择器。
4. End turn 等确认 → 通过则 phase-transition → tasks。

### 3.3 phase=tasks

1. fork spec-writer 生成 `tasks.md`。要求：
 - 嵌套 checkbox（顶层任务 / 子任务 / 检查点任务）。
 - 每条具体任务**必须**带 `_需求：x.y_` 或 `_需求：可选_` traceability 标签。
 - 可选任务用 `[*]` 标记；checkpoint 任务用 `[ ]` 但标题含"检查点"。
 - 验收节固定四行：所有 required 任务完成 / 所有验证命令通过 / 跳过 optional 已记录 / 用户确认验收。
2. 报路径 + 摘要（任务总数 / required 数 / optional 数）。
3. `doc-confirm-tasks` 选择器 → 用户确认。
4. 确认后立即呈现 `tasks-execution` 选择器（类型 A）：
 - 选 1 `开始 required` → phase-transition → implementation，逐个推进 required。
 - 选 2 `开始 required + optional` → phase-transition → implementation，required 后顺带 optional。
 - 选 3 `用 task-swarm 多 agent 并发`→ 调 `task_swarm.py init --tasks <spec_dir>/tasks.md --session <id>` 切到 task-swarm 编排模式；详见 `references/task-swarm.md`。
 - 选 4 `暂不 coding` → 留在 tasks phase；告知用户随时 `/specode:end` 或后续 `/specode:continue` 继续。

## 4. Technical-design-first Flow

1. `design.md` first（spec-writer 生成，章节同 §3.2）。问用户做 high-level 还是 low-level design 时合并到一份。
2. End turn → `doc-confirm-design` → 确认。
3. 从已 approved 的 design.md 反推 `requirements.md`。
4. `doc-confirm-requirements` → 确认。
5. `tasks.md` 同 §3.3。
6. `tasks-execution` 同 §3.3。

## 5. Bugfix Flow

1. `bugfix.md`（不写 `requirements.md`，二者**互斥**）。章节见 templates.md：
 - 问题摘要 / 复现步骤 / 当前行为（错误行为，WHEN ... THEN ... [错误]） / 期望行为（WHEN ... SHALL [正确]）/ 保持不变的行为（WHEN ... SHALL CONTINUE TO ...）/ 影响范围 / 证据 / 约束 / 待确认问题。
2. 调研代码后再断根因 —— 不要凭空断言根因。
4. `doc-confirm-bugfix` → 确认。
5. `design.md`：根因 / 修复策略 / 回归风险 / 测试策略。`doc-confirm-design` → 确认。
6. `tasks.md`：**复现测试 first** → 最小修复 → 不变行为回归测试 → 最终验证。`doc-confirm-tasks` → `tasks-execution`。

## 6. phase=implementation

### 6.1 写代码前

1. 解析 active spec：从 `sessions/<id>.json` 拿 `active_spec_dir`。
2. **写前三重校验**（详见 `references/lock-protocol.md`）：specId / 边界 / 锁。任一失败 → 拒写。
3. 加载 spec 目录下全部文档（**不**碰其他 spec）。
4. 找目标任务（用户指定）或下一条 pending required 任务。
5. **Heartbeat**：`spec_session.py heartbeat --spec <dir> --session <id>`（写文档前必调；距上次心跳 > 5 分钟也调）。
6. 把任务标记从 `[ ]` 改成 `[~]`（in-progress）。

### 6.2 写代码

1. 做满足该任务对应 `_需求：x.y_` 的**最小**改动。不要顺手重构无关代码。
2. 跑该任务对应的验证命令或最近的项目测试。
3. 验证通过 → 把任务标记从 `[~]` 改成 `[x]`。
4. 验证不通过 → 留 `[ ]` 或 `[~]`，在 chat 报告 blocker、在 `implementation-log.md` 追加一条 ≥30 字的记录（什么任务、什么 blocker、下一步怎么处理）。
5. 任务被跳过 → 标 `[-]` 并在 chat / log 说明。

### 6.3 turn 结束前自检

看到 `on-stop` 注入的「🔄 代码-文档同步提醒（输出侧）」时：

1. `tasks.md` 是否更新？（推进 `[ ]` → `[~]` → `[x]` / blocker）
2. `implementation-log.md` 是否记录？（实现说明、设计偏离、关键决策）
3. `design.md` 接口契约是否变化？（若改了，**同 turn** Edit）

任一遗漏 → 在 chat 显式承诺"下一轮第一件事补齐 X"，并在下一轮立刻做到。

### 6.4 任务标记语义

```
[ ] pending [~] in progress [x] completed
[-] skipped [*] optional
```

## 7. phase=acceptance

1. 触发：所有 required 任务标 `[x]`。
2. phase-transition → acceptance。
3. **先调一次** `spec_lint.py --spec <spec-dir>`（通过 SKILL.md §CLI 调用规约的 run.sh 模板），把 traceability / log / EARS 三类 WARNING 列在 chat 给用户参考。lint 是 advisory，不阻断验收。
4. 做一份**验收摘要**（chat）：tasks.md 完成度（done/total）/ lint WARNING 列表 / 余留风险 / 未决问题。若 tasks.md 末尾 `## 测试要点` 节存在，简述本次需要测试人员关注的要点（参考信息，不参与验收门判定）。
5. 呈现 `acceptance-gate`（类型 A）：
 - 若 tasks.md 全 `[x]` → 推荐选项 1 `验收通过，进入 iteration`。
 - 否则 → 无推荐项。
6. 用户选 1 → 调 `spec_session.py phase-transition --from acceptance --to iteration`（同时 `iterationRound += 1`，记 `iterationHistory`）。
7. 用户选 2 `继续修改` → 留在 acceptance；视具体未达标项回退到 requirements / design / tasks（**走 phase-transition**，不要手改 `.config.json`）。

## 8. phase=iteration

iteration 是已交付 spec 的**常驻**状态。子循环规则见 `references/iteration.md`。

简要：

- 用户提"我想加一个 X 功能" → `spec_session.py iterate <spec-dir>` → 进入 `iteration.requirements` 子 phase → 在 requirements.md 末尾追加 `## 迭代 N 新增需求` 节，走 confirm → design → tasks → implementation → acceptance 子循环 → 回到 iteration。
- 用户提"改 acceptance 里一条规则" → 直接编辑 tasks.md 对应任务或 `## 测试要点` 行，不走完整子循环。
- 用户运行 `/specode:end` → 释放锁 + sessions mode=ended，spec 文档保留。

## 9. `/specode:continue` — 上下文加载 + 多窗口

`/specode:continue` 是"加载并报告"型命令。它**恢复上下文然后停**；不开始实现、不跑验证、不评估验收。

### 9.1 无参数形式

```text
/specode:continue
```

步骤：

1. 调 `spec_vault.py status` 拿当前已配置 root（仅读 config.json，不重新检测）。
 - 无配置 root → 提示用户运行 `/specode:spec --set-vault <path>` 或 `--set-root <path>` 后 end turn。
2. 调 `spec_session.py list-specs` 拿 root 下全部 spec（含 slug / phase / lock_state / holder / iterationRound / mtimes）。
3. 在 chat 写 1-2 行上下文摘要（"找到 N 个可继续 spec，当前 root：<root>，其中 <m> 个锁定 / <n> 个空闲"），然后**调 `AskUserQuestion` 工具**呈现选择器（详见 `references/obsidian.md` §5.1）：
 - 类型 A 单列单选；`multiSelect=false`。
 - 选项 ≤ 4 项；超过时按 last_heartbeat_at 取最近 4 个，其余在 chat 引导用户用 `/specode:continue <slug>` 显式指定。
 - 每个选项 `label=<slug>`，`description` 简述 phase / 迭代 / 锁状态 / 最近修改 mtime。
4. 锁状态描述用固定词：`持有锁` / `锁定于 <id 前 8 位>` / `空闲` / `已过期`。
5. 工具返回后下一轮进入 §9.2 with slug。
6. `list-specs.specs == []` → **不**调工具，直接在 chat 引导用户用 `/specode:spec <需求>` 创建新 spec。

### 9.2 有 slug 形式

```text
/specode:continue <slug>
```

步骤：

1. 解析 `spec_dir = <root>/specs/<slug>`。
2. `spec_session.py acquire --spec <dir> --session <id>`：
 - exit 0 → 持锁成功，进入 step 3。
 - exit 4 `LockHeld` → 输出锁状态摘要 → 呈现 `takeover-options` 选择器（详见 SKILL.md §Multi-Window + Lock）→ end turn。
 - 选 1 `强制接管` → `acquire --force` → 继续 step 3。
 - 选 2 `只读查看` → 跳 acquire，调 `load` 拿数据，写 `sessions/<id>.json.mode=readonly` → 进 step 5。
 - 选 3 `取消` → 退出。
3. `spec_session.py load --spec <dir>` → 拿 phase / iteration round / tasks 计数 / 文档 mtime。
4. `spec_session.py continue --spec <dir> --session <id>` → 绑定 sessions + 写 active-pointer（只读模式跳过这步）。
5. 输出"已加载 spec"报告：

```text
已加载 spec：<slug>
 specId：<id>
 phase：<phase>
 iteration：第 N 轮（若 > 0）
 session：<session 前 8 位>（active / readonly）
 lock：本会话持有 | ⚠ 锁定于 <other 前 8 位> | 空闲

 requirements.md ← N 条验收标准 | 修改：<time>
 design.md ← | 修改：<time>
 tasks.md ← N/M 已完成，P 待处理 | 修改：<time>
```

6. 状态行 footer。
7. **End turn 等用户下一句**。不开始任务、不跑验证、不评估验收。

> ⛔ 从此刻起，本会话进入"已落地 spec 的持续沟通"模式。用户后续任何对需求 / 设计 / 任务的调整 —— 哪怕只是聊一句 —— 都必须**同 turn 写回**对应文档。chat 累积到"下一轮再写"是禁区——next session 看不到。

## 10. Phase-gate 输出顺序（铁律）

每个 phase-gate 的 turn 严格按此顺序：

1. 先做工具调用（Write/Edit 文档 / Read 验证文档）。
2. 在 chat 正文输出：文档**绝对路径**、简短摘要、3–8 条关键变更要点、未决问题。
3. 空一行 → 状态行 footer。
4. **调 `AskUserQuestion` 工具**呈现选择器（类型按 SKILL.md §Selectors 表查；具体参数见 `references/selectors.md` §8 场景常量库）。
5. 工具调用本身就是 turn 终止；不需要 sentinel，不需要在工具调用之后追加任何文本。

用户回复（即 `AskUserQuestion` 工具返回值）→ 下一轮按用户选择做对应动作；选 `查看全文`（doc-confirm-* 选项 2）就完整 echo 文档后**再次**调同一选择器工具。

绝不在同一轮里"先调工具再继续到下一阶段"——工具调用结束了本轮，下一阶段在新一轮处理。

## 11. 与 task-swarm 的交接

`tasks-execution` 选项 3 `用 task-swarm 多 agent 并发` 由 `task_swarm.py` 编排器实现。

选 3 → 主会话切到 task-swarm 编排模式（按 `commands/task-swarm.md` 协议），所有 group 完成后回到 implementation → acceptance 通路。详见 `references/task-swarm.md`。

## 12. CLI 命令参考

| 命令 | 用途 |
|---|---|
| `spec_vault.py detect` | 列出已知 Obsidian vault |
| `spec_vault.py status` | 当前 doc root + 来源 |
| `spec_vault.py set --vault <p>` / `set --root <p>` | 永久绑定 vault / 根目录 |
| `spec_init.py --name <slug> --requirement-name "..." --source-text "..." --session <id>` | 创建新 spec |
| `spec_session.py acquire / release / heartbeat / verify-lock --spec <dir> --session <id>` | 锁管理 |
| `spec_session.py phase-transition --spec <dir> --session <id> --from <p> --to <p>` | phase 切换（必走 CLI） |
| `spec_session.py load --spec <dir>` | 只读加载状态摘要 |
| `spec_session.py continue --spec <dir> --session <id>` | 接管 / 恢复 |
| `spec_session.py end --session <id>` | `/specode:end` 入口 |
| `spec_session.py status --session <id>` / `read-session --session <id>` | 状态查询（只读） |
| `spec_lint.py` | traceability 缺失 / log 过短 / EARS 缺动词 等 WARNING（acceptance phase 由主代理调一次）|
| `spec_status.py` | `/specode:status` 命令入口（聚合输出） |

CLI 退出码语义：0 ok / 1 lock_lost 或写失败 / 3 evicted / not_held / stale_lock 或 vault miss / 4 LockHeld。所有 hook 子命令始终 exit 0（仅注入提示，不阻断）。
