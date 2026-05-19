---
name: specode
description: Specification-driven workflow. All hooks are advisory injections — never blocking. Activates only when the user explicitly invokes `/specode:spec`, `/specode:continue`, `/specode:status`, `/specode:end`, `/specode:task-swarm`, or explicitly asks to use spec mode. Every active-spec turn must respect the phase order, selector format, code-doc sync reminders, and the status footer.
---

# specode — Spec-Mode 工作流

文件优先的规范驱动工作流。`requirements.md` / `bugfix.md` / `design.md` / `tasks.md` / `implementation-log.md` 是事实源；代码改动总是滞后于文档落地。所有 hook 都是**提示式注入**，永远不阻断；hook 注入失败或缺失时，本 SKILL.md 的硬约束仍然完整有效。

## Activation Guard

只在以下任一情况激活：

- 用户当前输入包含 `/specode:spec`、`/specode:continue`、`/specode:status`、`/specode:end`、`/specode:task-swarm`。
- 用户显式说"使用 spec 模式" / "use spec mode"。
- 当前会话的 `~/.specode/sessions/<session_id>.json` 中 `mode=active` 或 `mode=readonly`。

`mode=ended` 或 sessions 文件不存在且无触发条件 → **不要激活**，按普通对话处理。

## Session Lifecycle

持久会话是**唯一**模式（无 `--persist` 标志）。所有写操作必须同时更新 `<spec-dir>/.config.json` + `~/.specode/sessions/<session_id>.json` 两处；CLI 用 tempfile + `os.replace` + `os.fsync` 保证原子性；任一写失败 → CLI 整体 exit 1 + 回滚 + 你在 chat 如实报告，**禁止把 in-memory 半成功状态当成已落地**。

### CLI 调用规约（强制）

所有 specode CLI **必须**通过 `run.sh` 包装调用，脚本路径用 `$CLAUDE_PLUGIN_ROOT`（fallback `$CODEBUDDY_PLUGIN_ROOT`）拼绝对路径——**禁止**假设 cwd 在 scripts 目录，**禁止**裸 `python3 <脚本名>` 调用：

```bash
sh "${CLAUDE_PLUGIN_ROOT:-${CODEBUDDY_PLUGIN_ROOT}}/scripts/run.sh" \
   "${CLAUDE_PLUGIN_ROOT:-${CODEBUDDY_PLUGIN_ROOT}}/scripts/<name>.py" \
   <verb> <args...>
```

`run.sh` 自动探测 `python3 → python → py` 三档解释器并 exec 透传参数；任何 `python3 spec_session.py ...` 形式的裸调用在大多数 cwd 都会 `No such file or directory`。下表中的脚本名是简写，**实际 Bash 工具调用必须套用上面模板**。

四个命令的 CLI 展开：

| 命令 | 解析 → 关键 CLI 调用 |
|---|---|
| `/specode:spec <需求>` | 解析名称前缀 `<名称>：<内容>` / 推导英文 slug → `spec_init.py --name <slug> --requirement-name "<显示名>" --source-text "<需求>" --session <session_id>` |
| `/specode:continue [slug]` | 无 slug：`spec_session.py list-specs` 列表 → 用户回编号；有 slug：`spec_session.py acquire --spec <dir> --session <id>`（LockHeld → `takeover-options` 选择器）→ `continue` + `load` → 状态摘要 + 状态行 footer → end turn |
| `/specode:end` | `spec_session.py end --session <id>`（释锁 + mode=ended） |
| `/specode:status` | `spec_session.py status --session <id>` 或 `spec_status.py` |

→ 完整 phase 子步骤、`/continue` 接管流程详见 `references/workflow.md`。

### session_id 的获取

- `SessionStart` hook 注入当前 `session_id`；`UserPromptSubmit` hook 每轮重复注入避免遗忘。
- 调任何 specode CLI 时必须传 `--session <session_id>`。
- 永远**不要** invent session_id、不要从用户输入解析、不要在 chat echo 完整 ID（状态行只取前 8 位）。

## Status Footer

active spec 期间，**每一次响应末尾**必须额外输出状态行，与正文空一行隔开：

```text
─── spec-mode ─── spec: <slug> | session: <session_id 前 8 位> | phase: <phase> | /specode:end 退出
```

只读模式追加 `[只读]` 字段。状态行是机器友好格式（`─── spec-mode ───` 三符号包裹），不允许装饰、不允许 emoji。当本 turn 输出 selector 时，状态行放在 selector **之前**，再空一行接 selector。`mode=ended` 或不在 spec 模式 → **不**输出状态行。

## Selectors

每个 phase-gate 节点必须**调用宿主内置 `AskUserQuestion` 工具**呈现选择器；工具自动渲染上下键导航 + 回车提交 + ESC 取消 + "Other" 自定义输入。**严禁**自己在 chat 输出 markdown 列表 + "请回复编号"；**严禁**自己加 `Type something` / `Chat about this` 等保留位（工具内置 Other）；**严禁**等待用户回复文本编号。

三种类型映射到 `AskUserQuestion`：

| 类型 | `AskUserQuestion` 参数形态 | 何时用 |
|---|---|---|
| **A 单列单选** | `questions=[1 question]` + `multiSelect=false` | 一个问题、互斥选项、单选。绝大多数 phase-gate。 |
| **B wizard** | `questions=[2-4 question]` + 每个 `multiSelect=false` | 一组无依赖子问题打包；**仅用于需求澄清问答**。 |
| **C 复选框多选** | `questions=[1 question]` + `multiSelect=true` | 非互斥选项可同时勾选。**仅 iteration-scope 一个场景**。 |

`AskUserQuestion` 工具铁约束（详见工具自身文档）：
- 一次调用 `questions` 数组 **1-4 项**（B 类型 wizard 即占用全部 4 个 slot）。
- 每个 question 的 `options` **2-4 项**；超过 4 项请收敛或拆 wizard。
- `header` 是 chip-tab 短标签（≤12 字符）。

→ 7 个场景的完整 `AskUserQuestion` 调用模板详见 `references/selectors.md`；常量库实现在 `spec_session.py` 的 `SELECTOR_PROMPTS` 字典。

### 7 个固定场景

| 场景 key | 类型 | 触发 phase | header |
|---|---|---|---|
| `workflow-choice` | A | 进入 requirements 前 | 工作流选择 |
| `clarification-wizard` | B | intake，写需求前 | 需求澄清 wizard |
| `clarification-done` | A | intake 澄清结束 | 澄清完成? |
| `doc-confirm-{requirements,bugfix,design}` | A | 对应文档生成后 | 需求/设计/缺陷确认 |
| `tasks-execution` | A | tasks.md 生成后（合并旧 doc-confirm-tasks，含「需要调整」回退） | 执行方式 |
| `takeover-options` | A | `/specode:continue` LockHeld | 接管选项 |
| `acceptance-gate` | A | acceptance 完成 | 验收门 |
| `iteration-scope` | C | iteration 子循环开始 | 迭代范围 |

### 看到 hook 注入"必须呈现 X 选择器"时的硬约束

- 当前 turn **唯一**正确动作 = 调用 `AskUserQuestion` 工具（按提示词给出的 questions / options 逐字传参）→ 工具返回后 turn 自然结束。
- 类型与场景映射固定——不允许自行变换类型（如把 A 改 C）。
- 没看到 hook 提示但自己判断到了 phase-gate（如 hook 失败）→ 仍应按上表查类型并调 `AskUserQuestion`。
- **绝对不允许**的退化路径：
  - ❌ 在 chat 输出 markdown 列表 + "请回复 1/2/3" 让用户回复编号；
  - ❌ 加 `Type something` / `Chat about this` / `AWAITING_USER_CHOICE` 等历史保留位（工具内置 Other / ESC）；
  - ❌ 在 selector 之外多写"也可以聊聊"之类的兜底文本；
  - ❌ 在 `AskUserQuestion` 调用前没在 chat 给出 1-3 行上下文摘要（让用户知道这次选什么）。
- 工具调用前在 chat 可以写一段 ≤8 行的简报（如 doc-confirm 时列 3-8 条关键变更要点）；工具调用本身就是 end turn 触发器，不需要 sentinel。

→ 完整调用模板详见 `references/selectors.md`。

## Code-Doc Sync Reminders

### Spec 文档清单

| 文档 | 何时更新 |
|---|---|
| `requirements.md` / `bugfix.md` | 需求 / 验收标准 / 缺陷范围调整 |
| `design.md` | 架构 / 接口 / 数据模型决策 |
| `tasks.md` | 任务范围 / 状态推进 `[ ]` → `[~]` → `[x]`；末尾自带 `## 测试要点` 节，spec-writer 在 tasks phase 按 SHALL 补几行，供测试人员参考 |
| `implementation-log.md` | 实施期间记录设计偏离 / 关键决策（可选；≥30 字） |

→ 5 份文档的章节模板与 EARS SHALL 写法详见 `references/templates.md`。

### Document-first 响应约束

1. 看到「📝 文档优先提醒（输入侧）」+ 用户输入含需求 / 设计 / 任务 / 验收调整 → 本 turn **优先 Edit 对应文档**，再处理代码。
2. 看到「🔄 代码-文档同步提醒（输出侧）」+ 本 turn 触碰过 Write/Edit 源码 → turn 结束前补齐文档；无法当 turn 补齐则在 chat 显式承诺下一轮第一件事补齐，并立刻做到。
3. 没看到提醒（hook 失败 / 无 active spec）→ 仍保持 document-first 纪律。**这是硬约束，不依赖 hook 触发**。

## Help Fast-path

`/specode:spec -h` / `--help` 由 hook 注入完整帮助文本，要求逐字打印。同样的 fast-path 适用于 `--vault-status` / `--detect-vault` / `--sync-status`：hook 给出预渲染输出，模型只负责 verbatim print，**禁止补充解释**。

## Workflow Selection

进入 requirements 前由 `workflow-choice` 选择器决定走哪条流程：

- **Requirements-first**：行为优先，先 EARS SHALL，再补技术设计。
- **Technical Design-first**：架构约束已知，先 design.md 框架，再反推需求。
- **Bugfix**：缺陷修复，用 `bugfix.md`（Current / Expected / Unchanged）替代 `requirements.md`。

→ 三档判定细则详见 `references/workflow.md` §3。

## Phase Order

`intake → requirements/bugfix → design → tasks → implementation → acceptance → iteration`

每个 phase 切换通过 `spec_session.py phase-transition --from <p> --to <p2>`，自动更新 sessions.phase + 对应 `pending_selector`。

**进入 acceptance phase 时主代理必须调一次 `spec_lint.py --spec <spec-dir>`**（通过 §CLI 调用规约的 run.sh 模板），把 traceability / log / EARS 三类 WARNING 列给用户参考，再呈现 `acceptance-gate` 选择器。lint 是 advisory，所有 WARNING `exit 0`，**不阻断**验收决策。

→ 每个 phase 的输入 / 产出文档 / 子步骤详见 `references/workflow.md`。

## Document Root Resolution

三层解析（无 fallback；详见 `references/obsidian.md`）：

1. `--root <p>` 或 `SPECODE_ROOT` env（最高优先级）
2. `~/.config/specode/config.json.obsidianRoot`
3. 自动检测已安装 Obsidian vault → `<vault>/spec-in/<os>-<user>/specs`

三层全 miss → `spec_init.py` exit 3 + 引导提示；**不**回退到 cwd / `~/specs` / 项目目录。

`/specode:continue` 查找 spec 时**禁止 Grep 项目目录**——spec 不在项目里。正确流程：`spec_vault.py status` + `spec_session.py list-specs`，详见 `references/obsidian.md` §5.1。

## Multi-Window + Lock

不同窗口可并行不同 spec；同一 spec 同时只一个会话持锁（lock 字段持有者键 = `session_id`，30 分钟无 heartbeat 视为 stale）。

每次 spec 文档写入前三重校验：specId 匹配 / spec_dir 在 documentRoot 下 / `verify-lock` 返回 ok。`/specode:continue` 命中 LockHeld → 呈现 `takeover-options` 选择器（强制接管 / 只读查看 / 取消）。

→ 锁状态机与接管流程详见 `references/lock-protocol.md`。

## Pre-requirements Clarification

需求有歧义时进入 plan-mode，**不写任何 spec 文档**。用 `clarification-wizard`（类型 B）一次性收齐 2–5 个阻塞性澄清点（覆盖 scope / behavior / UX / data / validation / acceptance），用户回复后用 `clarification-done`（类型 A）决定进入下一阶段或继续澄清。**绝不**凭空 invent 业务规则。

→ wizard 详细出题策略详见 `references/workflow.md` §intake + `references/selectors.md` §clarification-wizard。

## Task-Swarm（多 agent 并发任务执行）

`tasks-execution` 选择器若选中"用 task-swarm 多 agent 并发"，主代理切到 task-swarm 编排模式：`task_swarm.py init` 解析 tasks.md 并按文件冲突切 group → 多 coder 并发 → reviewer（单实例，advisory）→ p0-fix coder（仅一次）→ validator（单实例，循环修复直到 pass）→ `task_swarm.py writeback`。state.json 是单一事实源；`on-task-completed` hook 在每个 subagent 返回后注入"下一步该做什么"提示。

→ 完整协议、agent 角色边界、产物 schema、writeback 格式详见 `references/task-swarm.md` + `references/task-swarm-example.md`。

## Output Language

User-facing 输出（摘要、问题、确认、状态、错误）——**中文**。

Exceptions（保留英文 / 原样）：技术名、命令、文件路径、代码标识符；代码块内容；本 skill 自身的规则文件（SKILL.md / references）。需求若是英文，生成的 spec 文档可英文；其他 agent 输出（摘要、确认）仍中文。

## Document Output Brevity

写 / 更新 spec 文档时**绝不**在 chat reprint 全文。报告只含：

- 文件路径（一行）
- 3–8 条章节标题或关键变更 bullets
- 未决问题（如有）
- 下一步动作

never paste 文档正文、EARS SHALL 全集、代码块、完整任务列表、设计 rationale。用户显式要求才例外。

## References

- `references/workflow.md` — phase 序列、三档工作流、phase-gate 输出顺序、`/specode:continue` 完整流程
- `references/lock-protocol.md` — 锁状态机、接管三选项、只读模式、被驱逐窗口行为
- `references/obsidian.md` — vault 三层解析、目录约定、`list-specs` 查找流程
- `references/selectors.md` — 三类选择器骨架 + 8 场景常量库 + 输出格式约束
- `references/templates.md` — 5 份文档模板、EARS SHALL 写法、traceability 规范
- `references/iteration.md` — iteration 子循环、文档累积规则
- `references/task-swarm.md` — task-swarm 编排协议、角色边界、产物 schema、writeback 格式
- `references/task-swarm-example.md` — 完整 tasks.md 示例

## Session Logging（0.10.0+）

specode 自带**会话日志收集**，默认开启。日志内容：每个 hook 触发、主代理工具调用（Bash / Read / Write / Edit 等）的 tool_input / tool_response、specode CLI 调用的 cmd / argv / exit_code、session phase / lock 状态变化。**用途**：排查"主代理为什么走偏 / 选错 selector / 漏 fork spec-writer"等问题时回溯现场，配合截图反馈给开发者。

- **存储位置**：`~/.specode/logs/<session_id>.jsonl`（每行一个 JSON event）
- **关闭**：`export SPECODE_LOG=off` 临时关 / 编辑 `~/.config/specode/config.json` 设 `"logging": false` 永久关
- **隐私**：默认 redact 黑名单（`password / api_key / token / secret / authorization / cookie` 等键名匹配 → 占位 `<redacted>`）；字符串字段超 500 字符自动截断；可在 config 加 `redact_keys` 列表扩展
- **回放**：`sh "$CLAUDE_PLUGIN_ROOT/scripts/run.sh" "$CLAUDE_PLUGIN_ROOT/scripts/spec_log.py" replay --session <id>` 按时序打印 events
- **占用查询**：`spec_log.py status` 输出当前 `~/.specode/logs/` 大小；超过 100MB 会提示手动清理 `rm -rf ~/.specode/logs/`

日志收集任何异常都吞并，绝不阻断业务流程。

## Iron Rules

1. **持久会话是唯一模式**——`/specode:end` 是退出口；不退出 hook 永远继续注入。
2. **文档优先**——需求 / 设计 / 任务调整必须先 Edit 对应文档，再代码或解释。
3. **强制双写 + 原子写**——`/specode:spec` / `/specode:continue` / `/specode:end` 任何写入失败视为整命令失败；不接受 in-memory 半成功。
4. **selector 由你按骨架生成 + 必须以 sentinel 结尾 + end turn**——hook 只注入"该呈现哪个"，文本由你写。
5. **状态行 footer 每轮必输**——缺失视为流程违规；hook 不会因此阻断，但用户与下一轮上下文都能察觉。
6. **CLI 调用必须走 `run.sh` 包装 + `$CLAUDE_PLUGIN_ROOT` 绝对路径**——见 §Session Lifecycle "CLI 调用规约"；任何 `python3 spec_session.py ...` 裸调用一律视为流程违规，发现立即换模板重试，不要在错误路径上循环。
7. **`requirements.md` / `bugfix.md` / `design.md` / `tasks.md` 4 份核心文档必须 fork `spec-writer` subagent 写**——主代理用 Write / Edit 直接写这 4 份文档视为流程违规。subagent 的工具白名单（无 Bash）是物理隔离边界，绕过它就是绕过整套 review/validator 兜底。`implementation-log.md` 例外，主代理可以直接追加。
8. **文档头 `Status` / `Review Status` 字段不允许主代理手改**——这些字段反映 phase / 评审状态，由 `phase-transition` CLI 与 selector 流程驱动改变。主代理写完 `requirements.md` 把 `Status: Requirements Draft` 改成 `Requirements Complete` 是越权（这是 selector 走完后才该发生的事）；保持模板默认值不动。文档**正文**该怎么写还是怎么写，只是别动 frontmatter 状态字段。
