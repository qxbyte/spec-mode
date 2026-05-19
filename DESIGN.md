# specode 0.5 → 0.8 重建设计文档

> 起草时间：2026-05-19
>
> 背景：0.5.0 把项目精简回骨架（删除全部 hook、INV-1 ~ INV-11、scripts、tests、references）。
> 本设计文档规定 0.6.0 / 0.7.0 / 0.8.0 三个里程碑的范围、决策与验收口径。
>
> 真实源：本文档是接下来三个版本的唯一参照。实施期间若决策有调整，先改本文档再改代码。

---

## 0. 设计原则

1. **不复活 INV-1 ~ INV-11 任何一条强制拦截**。0.4.0 的"hook 硬保险"思路被实践证明：长 context 下的副作用（合法热修被打断、模型在拒绝后反复试错）大于收益。
2. **subagent 边界 = frontmatter `tools:` 白名单（物理隔离）**：能力剥离在 agent 初始化时完成。reviewer / validator 没有 Edit/Write 工具；spec-writer 没有 Bash 工具。"无该工具"是进程层面的事实，不依赖 prompt 自律、不依赖运行时 hook 拦截。
3. **hook 仅做注入式提醒，永远不阻断**：所有 hook `exit 0`，通过 `hookSpecificOutput.additionalContext` JSON 把"该做什么"注入到模型的上下文里。**禁止 `exit 2` 阻断**，这是与 0.4.0 hook 模型的根本切割。模型可以无视 hook 的提示（代价是流程出错），但 hook 不会替模型决定终止哪次工具调用。
4. **Selector 由模型按统一模板生成，不再有"选择器生成脚本"**：删除 `spec_choice.py` 这类"脚本输出 selector 文本"的中间层。每个 phase-gate 节点由 `UserPromptSubmit` hook 在合适时机注入"此处该呈现 X 选择器"的模板提示，模型自行格式化输出，并以 `AWAITING_USER_CHOICE` sentinel 结尾。Hook 知道当前 phase，模型负责生成文本——两层各做各的事。
5. **文档-代码同步用"双侧提醒 hook"实现，不再叫 INV-1/INV-2**：
 - **输入侧**（`UserPromptSubmit`）：用户每次提交 prompt 时，hook 列出 spec 的 6 份文档名（`requirements.md` / `bugfix.md` / `design.md` / `tasks.md` / `acceptance-checklist.md` / `implementation-log.md`），提醒模型"用户本次输入是否需要变更某份文档？如是请先 Edit 文档再处理代码"。
 - **输出侧**（`Stop`）：模型 turn 结束前，hook 提醒"如果本 turn 改了代码，请决定是否需要同步更新对应文档"。
 - 两个 hook 都**只注入提示文本，永远 exit 0**，由模型自己判断是否落实。代价是 next session 可能丢上下文——但这是模型可见、可问责的事，比 hook exit 2 阻断后的反复试错更健康。
6. **SKILL.md + CLI 工具协作**：
 - SKILL.md 告诉模型什么时候必须调哪个 CLI（verify-lock / phase-transition / heartbeat / writeback）。
 - CLI 工具是确定性的：脚本管状态机和原子操作，模型只做自然语言对接。
 - 模型违反规则的代价是 spec 文档与代码失同步——靠 lint + 提醒 hook 注入显式提示，不靠 exit 2 阻断。
7. **task-swarm 状态机精简**：CLI 仅提供 `init / status / writeback / heartbeat`；不再实现 `next / parse / advance` 全状态机。主会话按 `commands/specode:task-swarm.md` 协议读 `state.json` 自行推进。
8. **stdlib-only**：所有运行时脚本仅依赖 Python 标准库；测试可用 pytest。
9. **spec 会话状态绑定宿主 `session_id`（唯一不变标识）**：
 - 每个宿主会话的 specode 状态写在 `~/.specode/sessions/<session_id>.json`，文件名即标识。
 - `<spec-dir>/.config.json` 的 lock 字段也以 `session_id` 为持有者键——锁主即会话。
 - 所有 hook 通过 stdin payload 直接拿 `session_id`，不需要"猜当前会话"。
 - `SessionStart` / `/specode:spec` / `/specode:continue` / `/specode:end` / `SessionEnd` 都强制写 sessions 文件——任何写失败视为整命令失败，禁止 in-memory 半成功。
 - 模型无须自己记 session_id：UserPromptSubmit hook 在 additionalContext 里始终包含当前 session_id，模型调 CLI 时把它作为 `--session <id>` 参数传入。
10. **持久会话是唯一模式，无 `--persist` 标志**：所有 `/specode:spec <需求>` 都创建持久会话，靠 `/specode:end` 显式结束；结束后 sessions 文件 mode 置 `ended`，hook 立刻停止注入 spec 提醒。这样模型从下一 turn 起看到的上下文与 spec 状态完全一致，不会"刚 `/specode:end` 但 hook 还在催"。

---

## 1. 模块拆分（最终态）

```
plugins/specode/
 .claude-plugin/plugin.json
 hooks/ ← 4 个 hook（SessionStart / UserPromptSubmit / Stop / SessionEnd）；
 PostToolUse Task；heartbeat + PreToolUse tasks.md guard
 hooks.json
 scripts/ ← spec_* 系列；task_swarm_*
 run.sh
 run.cmd
 spec_vault.py ← v0.6
 spec_init.py ← v0.6
 spec_session.py ← v0.6（含全部 hook 子命令 on-* + 业务子命令）
 spec_lint.py ← v0.6
 spec_status.py ← v0.6
 task_swarm.py ← v0.7
 task_swarm_state.py ← v0.7
 task_swarm_parse_md.py ← v0.7
 task_swarm_outbox.py ← v0.7
 task_swarm_writeback.py ← v0.7
 task_swarm_prompt.py ← v0.7
 agents/
 task-swarm-planner.md ← 已保留
 task-swarm-coder.md ← 已保留
 task-swarm-reviewer.md ← 已保留
 task-swarm-validator.md ← 已保留
 spec-writer.md ← v0.6 新增
 commands/
 spec.md ← 保留
 continue.md ← 保留
 end.md ← 保留
 status.md ← 保留
 task-swarm.md ← v0.7 重写
 skills/specode/
 SKILL.md ← v0.6 重写
 references/ ← v0.6 / v0.7 分批补回
 workflow.md
 lock-protocol.md
 obsidian.md
 prompts.md
 templates.md
 iteration.md
 task-swarm.md ← v0.7
 task-swarm-example.md ← v0.7
 assets/
 templates/ ← 已保留
 tests/ ← 补回；不复活 INV 测试
```

用户级运行时状态：

```
~/.specode/
 sessions/<session_id>.json ← v0.6；每个宿主会话一份；hook + CLI 共同维护
 config.json (optional) ← v0.6；obsidianRoot 等持久配置（也可放 ~/.config/specode/）
```

**永远不再引入**：`spec_guard.py`、`spec_sync.py`（INV 检查器）、`bash_guard.py`、`task_swarm_guard.py`、`.sync-ledger.json`、advisory ledger、sentinel 短路 (`~/.specode/.any-active`)、telemetry (`~/.specode/telemetry.jsonl`)、audit log (`~/.specode/audit/`)。

---

## 2. spec-mode 业务逻辑迁移清单

来源：`/Users/xueqiang/Git/skills/spec-mode/`（deprecated skill）。
全部"模型自律 + CLI 协作"路径，去掉 INV 字眼。

| 业务点 | 来源 | 迁移目标 | 说明 |
|---|---|---|---|
| Phase 序列（intake → requirements/bugfix → design → tasks → implementation → acceptance → iteration） | `references/workflow.md` | `skills/specode/references/workflow.md` + SKILL.md | 完整迁移；selector 由 hook 注入提示词后模型生成（见 §3.7） |
| 三档分类（Requirements / Technical Design / Bugfix） | `references/workflow.md` | SKILL.md §Workflow Selection | 完整迁移 |
| EARS SHALL 写法 + `_需求：x.y_` traceability | `references/templates.md` | `skills/specode/references/templates.md` + `assets/templates/` | 完整迁移 |
| 锁协议（`<spec-dir>/.config.json` 的 lock 字段；acquire / release / heartbeat / verify-lock） | `references/lock-protocol.md` | `skills/specode/references/lock-protocol.md` + `spec_session.py` | 完整迁移，去掉"INV-3 阻断" |
| Obsidian vault 三层根目录解析 | `references/obsidian.md` | `skills/specode/references/obsidian.md` + `spec_vault.py` + `spec_init.py` | 完整迁移；三层 miss → hard stop + 引导，不发明 fallback |
| 澄清子流程（Template B + 澄清完成 selector） | `references/prompts.md` | `skills/specode/references/prompts.md` | 完整迁移；选择器**模板化**，不再用脚本输出 |
| 文档确认 / 任务执行 / 接管 / 验收 selector | `references/prompts.md` | 同上 | 完整迁移；任务执行 selector "task-swarm 多 agent 并发"；统一格式见 §3.7 |
| Iteration 文档累积规则 | `references/iteration.md` | `skills/specode/references/iteration.md` | 完整迁移 |
| Spec 文档名前缀解析（`<名称>：<内容>`） | `references/workflow.md` §intake | SKILL.md + `spec_init.py --name` | 完整迁移 |
| 多窗口接管三选项（强制 / 只读 / 取消） | `references/lock-protocol.md` | 同 lock-protocol.md | 完整迁移 |
| Heartbeat 触发点（写前 / 长间隔后） | 同上 | 同上 + `spec_session.py heartbeat` | 完整迁移 |

**不迁移**：
- spec-mode 原 skill 的"六条铁律 INV-1 ~ INV-6"——它们在 0.4.0 已经被 hook 化，0.5.0 已删，0.6+ 不再以任何形式回来。SKILL.md 改用"文档优先纪律 + selector 提醒"措辞。
- `spec_choice.py`（选择器生成脚本）——0.5.0 已删除，0.6+ 不复活。选择器由模型按模板生成（§3.7），hook 仅注入"该呈现哪个选择器"的提示词。

---

## 3. CLI 接口契约

### 3.1 `spec_vault.py`

```text
spec_vault.py detect # 三平台 obsidian.json 解析，输出已知 vault 列表
spec_vault.py status # 当前根目录 + 来源（env / config / auto）
spec_vault.py set --vault <p> # 写 ~/.config/specode/config.json.obsidianRoot
spec_vault.py set --root <p> # 写同字段但跳过 vault 概念
```

退出码：0 ok / 3 用户引导（含 hard-stop 提示）。

### 3.2 `spec_init.py`

```text
spec_init.py \
 --name <slug> \
 --requirement-name "<显示名>" \
 --source-text "<原始需求文本>" \
 --session <session_id> \
 [--root <override>] \
 [--detect-vault]
```

行为：

1. 三层根目录解析（`--root` / `SPECODE_ROOT` → `~/.config/specode/config.json.obsidianRoot` → 自动 vault 检测）。
2. 三层全 miss → 输出引导 + exit 3（不发明 fallback）。
3. 在 doc_root 下创建 `specs/<slug>/`，写 `.config.json`（含 specId、createdAt、phase=intake、lock 字段指向当前 `--session <id>`）。
4. 更新 `<doc_root>/.active-specode.json`（active-pointer）。
5. **强制写**`~/.specode/sessions/<session_id>.json`（mode=active、active_spec_slug=`<slug>`、active_spec_dir、phase=intake、lock_state=ok）。
6. 步骤 3 / 4 / 5 中任一失败 → 回滚已写文件 + exit 1（"半成功"是禁区）。
7. 输出 JSON：`{"spec_dir": "...", "specId": "...", "session_id": "<id>", "phase": "intake"}`。

`/specode:spec` 命令永远是持久会话——`--persist` 标志已删。一次性工作流不再支持；如需快速试验请用 `/specode:end` 立即结束。

### 3.3 Session 状态文件 schema

文件路径：`~/.specode/sessions/<session_id>.json`

```json
{
 "session_id": "abc-def-1234-...",
 "started_at": "2026-05-19T09:30:00Z",
 "last_activity_at": "2026-05-19T10:05:00Z",
 "ended_at": null,
 "mode": "active",
 "active_spec_slug": "design-login-page",
 "active_spec_dir": "/abs/path/to/specs/design-login-page",
 "spec_id": "uuid-of-spec",
 "phase": "tasks",
 "lock_state": "ok",
 "task_swarm_run_id": null
}
```

字段语义：

| 字段 | 取值 | 维护者 |
|---|---|---|
| `session_id` | hook payload 中的 session UUID | SessionStart hook 创建时写入 |
| `started_at` / `last_activity_at` | ISO8601 UTC | SessionStart 写 started_at；每个 hook 触发刷新 last_activity_at |
| `ended_at` | ISO8601 或 null | `/specode:end` 或 SessionEnd hook 写入 |
| `mode` | `active` / `readonly` / `ended` | `/specode:spec` / `/specode:continue` / `/specode:end` 命令切换 |
| `active_spec_slug` / `active_spec_dir` / `spec_id` | 当前激活 spec 的标识与路径 | `/specode:spec` / `/specode:continue` 写入；`/specode:end` 清为 null |
| `phase` | spec 当前 phase（镜像自 `<spec-dir>/.config.json`） | `phase-transition` 命令同步写 |
| `lock_state` | `ok` / `readonly` / `evicted` / `released` | acquire / release / verify-lock 写 |
| `task_swarm_run_id` | task-swarm 运行 ID 或 null | task_swarm.py init 写、writeback 完成后清 |

**强制写入语义**：
- 任何修改这个文件的 CLI 子命令必须先 `tempfile.NamedTemporaryFile` 原子写、`os.replace` 切换、`os.fsync` 强制刷盘。
- 写失败 → 整个命令视为失败、回滚已变更的其他文件（如 `<spec-dir>/.config.json` 的 lock 字段）、exit 1。

**hook 读取语义**：
- 任何 hook 读取该文件前应处理"文件不存在"（如 SessionStart 还没来得及触发）→ 视作"无 specode 会话"，立即 exit 0，不注入提示。
- `mode=ended` 视作"无 specode 会话"——hook 停止所有 spec 提醒。

### 3.4 `spec_session.py`

子命令：

```text
# —— 业务子命令（被 SKILL.md 引导主会话调用；都接 --session）——
acquire --spec <dir> --session <id> [--force]
release --spec <dir> --session <id>
heartbeat --spec <dir> --session <id>
verify-lock --spec <dir> --session <id>
phase-transition --spec <dir> --session <id> --from <p> --to <p>
load --spec <dir> # 只读，不需要 session
continue --spec <dir> --session <id> # 接管/恢复，写 sessions/<id>.json
end --session <id> # /specode:end 命令入口：释放锁 + sessions mode=ended
status --session <id> # 单独窗口的状态查询
read-session --session <id> # 输出当前 sessions/<id>.json（json）

# —— hook 子命令（仅由 hooks/hooks.json 调用；全部 exit 0，仅注入提示）——
on-session-start # v0.6；SessionStart；创建 sessions/<id>.json（mode 初始为 "idle"）
 # 注入 additionalContext：当前 session_id（让模型记住）+
 # 若 mode=active 则注入 spec 模式提醒 + 状态行模板
on-user-prompt # v0.6；UserPromptSubmit；读 sessions/<id>.json：
 # - 命中 /specode:spec -h 或 /specode:spec --help → 注入帮助文本 fast path（§3.6）
 # - mode=active → 叠加注入：
 # (a) 按 §3.7.2 表注入「应呈现哪个选择器」提示（如有 pending）
 # (b) 按 §3.8.2 注入「文档优先（输入侧）」提醒
 # (c) 注入「状态行 footer」要求（§3.5 模板）
 # (d) 注入当前 session_id 字符串
 # - mode=readonly → 同上但状态行标 [只读]
 # - mode=ended → 不注入
on-stop # v0.6；Stop；读 sessions/<id>.json：
 # - mode=active → 注入 §3.8.3「代码-文档同步（输出侧）」提醒
 # + 提示"仍在 spec 模式，下一 turn 需继续遵守流程"
 # - mode=ended / 不存在 → 不注入
on-session-end # v0.6；SessionEnd；
 # - 若 sessions/<id>.json 存在 → 写 mode=ended, ended_at=now
 # - 若该 session 持有 spec 锁 → 释放（写 <spec-dir>/.config.json）
 # 不输出 additionalContext（事件已结束）
on-task-completed # v0.7；PostToolUse matcher=Task；详见 §11.6
on-heartbeat-quiet [--quiet] # v0.8；UserPromptSubmit；mode!=active → exit 0；
 # mode=active → 续约一次锁；不注入 additionalContext
on-pre-tool-use # v0.8；PreToolUse；不在 task-swarm run 期间 → exit 0；
 # 命中 tasks.md 直写时 exit 0，注入「应走 writeback」提醒
```

退出码：

| 子命令 | 0 | 1 | 3 | 4 |
|---|---|---|---|---|
| acquire | 持锁成功 | lock_lost | — | LockHeld |
| release | 释放成功 | — | — | — |
| heartbeat | 续约成功 | lock_lost | — | — |
| verify-lock | ok | — | evicted / not_held / stale_lock | — |
| phase-transition | 成功 | lock_lost | — | — |
| end | 成功 | sessions 写失败 | — | — |
| status / read-session | 成功 | — | — | — |
| on-session-start / on-user-prompt / on-stop / on-session-end / on-task-completed / on-heartbeat-quiet / on-pre-tool-use | 始终 0 | — | — | — |

**所有 hook 子命令永远 exit 0**——这是 §0 第 3 条原则的物理落地。

锁存储：`<spec-dir>/.config.json` 的 `lock` 字段，持有者键 = `session_id`。stale 周期：默认 30 分钟。

### 3.5 状态行 footer 模板

每次 active spec 的 user prompt 时，`on-user-prompt` 在 additionalContext 末段注入：

```text
## 🪧 spec-mode 状态行（必须在本响应末尾输出）

请在本次响应正文之后**额外**输出一行格式如下的状态行，紧贴响应末尾、之前空一行：

─── spec-mode ─── spec: <slug> | session: <session_id 短 8 位> | phase: <phase> | /specode:end 退出

如果是只读模式，请使用：

─── spec-mode ─── spec: <slug> | session: <session_id 短 8 位> | phase: <phase> | [只读] | /specode:end 退出

具体值：
 slug: <active_spec_slug>
 session: <session_id 的前 8 位>
 phase: <phase>
 mode: <active | readonly>

状态行的唯一目的是让用户和你自己都看到当前仍在 spec 模式。**不要省略**；如果本轮响应是 selector（AWAITING_USER_CHOICE），把状态行放在 sentinel **之前**一行（空行隔开）。
```

SKILL.md §Status Footer 一节同步声明：
- active spec 期间，**每一次**响应末尾都必须输出状态行；缺失视作流程违规。
- 状态行是机器友好格式（用 `─── spec-mode ───` 三符号包裹），不要用其他装饰。
- session 字段取 session_id 的前 8 位（够用且可读），完整 ID 由 hook 提示给到模型。

### 3.6 `/specode:spec -h` 帮助 fast-path

`on-user-prompt` 检查 `payload.prompt` 是否匹配 `^/specode:spec\s+(-h|--help)\s*$`（容忍前后空白）。若命中：

- 立即输出 additionalContext：
 ```text
 ## ⛔ /specode:spec -h fast-path

 本轮唯一动作：把下列代码块**逐字**用 ```text 围栏包裹后输出，然后立即 end turn。
 禁止添加任何额外文字（"以下是帮助" / "希望对你有帮助" 等都不允许）。

 ────────── HELP CONTENT BEGIN ──────────
 <完整帮助文本（来自 references/help-output.md 的常量副本）>
 ────────── HELP CONTENT END ──────────
 ```
- 帮助文本由 hook 进程从一个内置常量（或从 plugin 安装路径下的 `references/help-output.md` 直接读取）拿到，**hook 控制内容**——模型只负责打印。
- 同样的 fast-path 也用于 `/specode:spec --vault-status`、`/specode:spec --detect-vault`、`/specode:spec --sync-status`：hook 直接执行对应 CLI（`spec_vault.py status` 等），把 stdout 包成 additionalContext 让模型 verbatim 打印。

这把 spec-mode 原 SKILL.md "Help Output (Fast Path)" 的不稳定路径（模型读文件 → 提取 → 打印）替换为稳定的 hook 注入路径。

### 3.7 选择器统一格式（由模型按模板生成）

**核心约定**：模型在每个 phase-gate 节点输出一段**结构化文本**作为"选择器"。`UserPromptSubmit` 的 `on-user-prompt` hook 在合适时机注入提示，告诉模型"此处应呈现 **哪种类型** 的 **哪个场景** 选择器"。文本骨架由模型按 §3.7.1 ~ §3.7.3 三种类型自己生成；hook 不构造选择器文本。

三种类型对应桌面截图中由用户确认的三种 UI 形态：

| 类型 | 别名 | 文件名参考 | 何时用 |
|---|---|---|---|
| **A：单列单选** | single-select | `单列选择器（用于文档确认下一步执行）.png` | 一个问题，互斥选项，单选 |
| **B：多项串行决策（wizard）** | wizard | `多项串行决策性选择器（用于写需求文档前需求确认环节）.png` | 一组**有顺序**的决策问题，每个串行问完才能 Submit |
| **C：复选框多选** | multi-select | `复选框型选择器（用于多项选择组合方案的情况）.png` | 一个问题，非互斥选项，可同时选多项 |

公共铁规则（三种类型都遵守）：

- 必须以 `AWAITING_USER_CHOICE` 单独一行**结尾**——turn 终止 sentinel；输出该段后立即 end turn。
- 每个选项都带编号 + 标签 + 简短说明。
- 选项列表末尾固定两个保留位：
 - 倒数第二项 `Type something` —— 自由文本输入逃生口。
 - 最末一项 `Chat about this` 或 `Submit`（见各类型）。
- 状态行 footer（§3.5）若与 selector 同 turn 输出，状态行放在 selector **之前**，中间空一行。

#### 3.7.1 类型 A：单列单选

文本骨架：

```text
=== <选择器标题> ===
当前阶段：<phase>
<1-2 行上下文，如：spec 名 / 刚生成的文档路径 / 摘要>

1. <选项 1 标签>
 <一句话说明>
2. <选项 2 标签>（推荐）
 <一句话说明>
3. <选项 3 标签>
 <一句话说明>
4. Type something
 自定义回复
5. Chat about this
 就这个选择器讨论，不下决定

请回复选项编号或选项名称。
AWAITING_USER_CHOICE
```

约束：
- 「（推荐）」最多一个；无强推荐时全部不带括号。
- 正式选项个数建议 2–5；过多说明问题没切清楚，重新拆。

#### 3.7.2 类型 B：多项串行决策（wizard）

适用：写需求文档前的"需求澄清环节"——多个相互独立但需在同一轮全部回答的子问题。每个子问题是一个 chip-tab；用户在每个 tab 单选后 Submit。

文本骨架：

```text
=== <wizard 标题> ===
当前阶段：<phase>
本 wizard 共 <N> 个决策点，全部确认后 Submit。

▼ 决策点 1/<N>：<子问题 1 标题>
 <1-2 行子问题说明>
 1.1 <选项 A>
 <一句话说明>
 1.2 <选项 B>（推荐）
 <一句话说明>
 1.3 <选项 C>
 <一句话说明>

▼ 决策点 2/<N>：<子问题 2 标题>
 <1-2 行子问题说明>
 2.1 <选项 A>
 <一句话说明>
 2.2 <选项 B>
 <一句话说明>

...

▼ 决策点 <N>/<N>：<子问题 N 标题>
 N.1 <选项 A>
 N.2 <选项 B>
 N.3 Type something

请按格式回复，每行一个决策点：
 1: 1.2
 2: 2.1
 3: N.3 "<自定义文本>"

或回复 `Chat about this` 就 wizard 整体讨论而不下决定。
AWAITING_USER_CHOICE
```

约束：
- 决策点之间**无依赖**——若有依赖应拆成两次 wizard（前者 Submit 后再下一个）。
- 决策点个数建议 2–5；超过 5 个用户体验差。
- 每个决策点独立编号 `<N>.<M>`，方便用户用编号一次性回复。
- 子选项中 `Type something` 是单点逃生口；wizard 整体保留 `Chat about this` 作为"先讨论不下决定"逃生口。

#### 3.7.3 类型 C：复选框多选

适用：多个**非互斥**的组合方案，用户可同时勾选多项。

文本骨架：

```text
=== <选择器标题> ===
当前阶段：<phase>
<1-2 行上下文>

请勾选适用项（可多选）：

 [ ] 1. <选项 1 标签>
 <一句话说明>
 [ ] 2. <选项 2 标签>
 <一句话说明>
 [ ] 3. <选项 3 标签>
 <一句话说明>
 [ ] 4. Type something
 自定义补充

回复格式：
 - 单项：`2`
 - 多项：`1,3` 或 `1 3`
 - 全不选：`none`
 - 自定义：`4 "<自定义文本>"`
 - 整体讨论而不下决定：`Chat about this`

AWAITING_USER_CHOICE
```

约束：
- 至少允许"全不选"——多选场景下"不选任何项"是合法答案。
- 选项个数 2–6；超过 6 拆 wizard。
- 不写「（推荐）」标记——多选场景没有"推荐组合"，由用户判断。

#### 3.7.4 各场景与类型映射

| 场景 | 类型 | 触发 phase | 标题 | 选项（标签） |
|---|---|---|---|---|
| **workflow 选择** | A 单选 | 进入 requirements 前 | 工作流选择 | Requirements first / Technical Design first / Bugfix |
| **需求澄清问答** | **B wizard** | intake，写需求文档前 | 需求澄清（共 N 个决策点） | 每个决策点 2–4 个单选项，含「Type something」 |
| **澄清完成** | A 单选 | intake 澄清结束 | 需求澄清是否完成？ | 进入下一阶段（推荐）/ 继续澄清 |
| **文档确认** | A 单选 | requirements / bugfix / design / tasks 任一文档生成后 | 文档确认 | 确认（推荐）/ 查看全文 / 继续沟通 |
| **任务执行** | A 单选 | tasks.md 确认后 | 任务执行选择 | 开始 required / 开始 required + optional / 用 task-swarm 多 agent 并发/ 暂不 coding |
| **接管选项** | A 单选 | `/specode:continue` 命中 LockHeld | 该 spec 已被其他窗口持有 | 强制接管 / 只读查看 / 取消 |
| **验收门** | A 单选 | acceptance 完成 | 验收结论 | 验收通过，进入 iteration（推荐）/ 继续修改 |
| **iteration 范围**（可选扩展） | **C 复选** | iteration 子循环开始 | 本轮 iteration 调整范围 | 改 requirements / 改 design / 改 tasks / 重跑测试 |

**默认类型选择理由**：

- spec-mode 工作流绝大多数节点是**互斥**选择 → 默认类型 A。
- **需求澄清问答**一次问多个独立澄清点（spec-mode 原 Template B 就是 5 题串）→ 用类型 B wizard 一次性收齐，避免来回 5 轮 turn。
- **iteration 范围**在多文档同时调整时是典型多选 → 用类型 C；非 iteration 阶段默认不出现复选场景。
- "任务执行"目前是 A（原 spec-mode 设计）。如果未来允许 `required + optional + task-swarm` 同时启动，可升级为 C；v0.6 不动。

#### 3.7.5 hook 注入的提示词（按类型分发）

`on-user-prompt` hook 在 active spec 时按 `sessions/<id>.json.pending_selector` 字段查 §3.7.4 表，找到 (类型, 场景)，注入对应提示。`pending_selector` 由命令层（`/specode:spec`、`/specode:continue`、`spec_session.py phase-transition` 等）在切到 phase-gate 节点时写入；hook 仅读不写。

注入文本模板（hook 拼出来的 `additionalContext`）：

**类型 A 单选**：

```text
## ⛔ 必须呈现「<场景中文名>」选择器（类型 A 单列单选）

当前 phase: <phase>
具体选项与说明请按 §3.7.4 表「<场景>」一行：
 标题：<标题>
 选项：<选项标签清单>
 推荐：<选项编号 或 无>

请按 §3.7.1 类型 A 骨架输出。状态行 footer 在前、selector 在后、`AWAITING_USER_CHOICE` 末行，立即 end turn。
```

**类型 B wizard**：

```text
## ⛔ 必须呈现「<场景中文名>」选择器（类型 B 多项串行决策）

当前 phase: <phase>
本 wizard 决策点清单（由 hook 给出占位；具体子问题由你结合 inputs 自行生成）：
 1. <子问题 1 占位>
 2. <子问题 2 占位>
 ...
 N. <子问题 N 占位>

请按 §3.7.2 类型 B 骨架输出。每个决策点独立编号。状态行 footer 在前、selector 在后、`AWAITING_USER_CHOICE` 末行，立即 end turn。

注：子问题与选项由你结合用户最近输入与 `requirements.md` / `bugfix.md` 上下文**自行生成**——不要凭空 invent 业务规则；若 inputs 不足以构成一个决策点，就不要把它放进 wizard。
```

**类型 C 复选框**：

```text
## ⛔ 必须呈现「<场景中文名>」选择器（类型 C 复选框多选）

当前 phase: <phase>
具体选项与说明请按 §3.7.4 表「<场景>」一行：
 标题：<标题>
 选项：<选项标签清单>

请按 §3.7.3 类型 C 骨架输出。允许"全不选"。状态行 footer 在前、selector 在后、`AWAITING_USER_CHOICE` 末行，立即 end turn。
```

注：
- hook 提示文本里**不构造完整 selector 文本**——只告诉模型"该用哪种类型 + 哪些选项标签"。文本由模型按 §3.7.1 ~ §3.7.3 骨架自己写。这是 §0 第 4 条原则的实现。
- 若 `pending_selector=null`（如 implementation 中段）→ hook 不注入这一段。
- `Chat about this` / `Type something` 等保留位由模型按对应类型骨架**自动补全**，hook 提示不必列出。

#### 3.7.6 SKILL.md 对模型的约束

SKILL.md §Selectors 一节声明：

- 看到 `additionalContext` 含"必须呈现 X 选择器（类型 ?）"提示 → 当前 turn **唯一**正确动作是按 §3.7.1 / 3.7.2 / 3.7.3 对应类型骨架输出选择器并 end turn。
- 没看到该提示但自己判断到了 phase-gate → 按 §3.7.4 表查类型并按对应骨架输出。
- 类型与场景映射是固定的（§3.7.4）——不允许"我觉得这里改用 C 类型更好"自行变换。
- 选项列表禁止改写为自由叙述（"你可以选 A 或者继续聊聊"）；必须严格按各类型骨架的编号与缩进格式。
- 保留位**必须留**：A 末尾 `Type something` / `Chat about this`；B 整体 `Chat about this` + 每决策点末项 `Type something`；C 末尾 `Type something` + 回复格式中的 `none` / `Chat about this`。
- 不允许跳过 selector 直接做下一步——这是 0.5.0 之前 INV-6 想保证的"phase-gate 不被绕过"，现在改由 selector 的存在事实保证。

#### 3.7.7 场景提示词常量库（hook 直接 emit 的固定文本）

`spec_session.py` 内置以下常量字典 `SELECTOR_PROMPTS: dict[str, str]`，`on-user-prompt` 命中 `pending_selector` 时，从该字典查出对应文本作为 `additionalContext`。所有 `<占位>` 由 hook 进程在 emit 前用 sessions/spec.config 实际值替换。

##### (1) workflow-choice （类型 A 单选）

```text
## ⛔ 必须呈现「工作流选择」选择器（类型 A 单列单选）

active spec: <slug>（phase=<phase>）
你即将进入需求/设计文档生成，先决定走哪条工作流。

标题：工作流选择
正式选项（**逐字使用**）：

 1. Requirements first
 行为优先的新特性：先把 SHALL 写清楚，再补技术设计。
 2. Technical Design first
 架构约束已知的新特性：先把 design.md 框架定下来，再反推 requirements。
 3. Bugfix
 缺陷修复 / 回归测试：用 bugfix.md（Current/Expected/Unchanged）替代 requirements.md。

请按 §3.7.1 类型 A 骨架输出（编号 1-3 + 保留位 4 Type something + 5 Chat about this）。
状态行 footer 在前，`AWAITING_USER_CHOICE` 末行，立即 end turn。
```

##### (2) clarification-wizard （类型 B wizard）

```text
## ⛔ 必须呈现「需求澄清问答」选择器（类型 B 多项串行决策 / wizard）

active spec: <slug>（phase=intake）
源需求摘要：<source-text 前 60 字>

本 wizard 用于在写 requirements.md / bugfix.md 之前**一次性**收齐影响 scope / behavior / UX /
data / validation / acceptance 的所有阻塞性澄清项。

请你结合源需求摘要、用户最近输入、`assets/templates/*.md` 模板的章节结构，**自行决定**
本 wizard 包含哪些决策点（通常 2–5 个），每个决策点 2–4 个互斥选项。

每个决策点必须满足：
- 标题是一个"是 / 否 / 选哪条"的具体问题，不能是开放式叙述
- 选项之间互斥；如果发现要"可多选"则该决策点拆错了类型——回到正文继续叙述
- 末项保留 `Type something`（允许用户自定义）

请按 §3.7.2 类型 B 骨架输出。整体保留 `Chat about this` 逃生口。
状态行 footer 在前，`AWAITING_USER_CHOICE` 末行，立即 end turn。

注：不要凭空 invent 业务规则；inputs 不足以构成决策点则不要放进 wizard。如果连一个决策点都没有
（需求已足够清晰），直接跳到 `clarification-done`，不输出 wizard。
```

##### (3) clarification-done （类型 A 单选）

```text
## ⛔ 必须呈现「需求澄清是否完成？」选择器（类型 A 单列单选）

active spec: <slug>（phase=intake）
用户刚刚回答了上一轮 wizard 的澄清问题。

标题：需求澄清是否完成？
正式选项（**逐字使用**）：

 1. 进入下一阶段（推荐）
 用户回答已经覆盖所有阻塞项，可开始写 requirements.md / bugfix.md。
 2. 继续澄清
 还有未解决的歧义，再发一轮 wizard。

请按 §3.7.1 类型 A 骨架输出（编号 1-2 + 保留位 3 Type something + 4 Chat about this）。
推荐选项编号：1
状态行 footer 在前，`AWAITING_USER_CHOICE` 末行，立即 end turn。
```

##### (4) doc-confirm-* （类型 A 单选；4 个变体共享模板）

key 取值之一：`doc-confirm-requirements` / `doc-confirm-bugfix` / `doc-confirm-design` / `doc-confirm-tasks`

```text
## ⛔ 必须呈现「<文档名> 文档确认」选择器（类型 A 单列单选）

active spec: <slug>（phase=<phase>）
刚生成 / 更新的文档：<spec_dir>/<doc-filename>
关键变更摘要：
 • <由模型自行从最近 Edit 工具调用中提取 3-8 条变更要点>

标题：<文档名> 文档确认
正式选项（**逐字使用**）：

 1. 确认（推荐）
 文档内容符合预期，进入下一 phase。
 2. 查看全文
 先在 chat 完整 echo 该文档（不进入下一 phase）。
 3. 继续沟通
 文档需要修改，告诉你具体怎么改。

请按 §3.7.1 类型 A 骨架输出（编号 1-3 + 保留位 4 Type something + 5 Chat about this）。
推荐选项编号：1
状态行 footer 在前，`AWAITING_USER_CHOICE` 末行，立即 end turn。
```

##### (5) tasks-execution （类型 A 单选）

```text
## ⛔ 必须呈现「任务执行选择」选择器（类型 A 单列单选）

active spec: <slug>（phase=tasks）
tasks.md 已确认。required 任务数：<n_required>，optional 任务数：<n_optional>。

标题：任务执行选择
正式选项（**逐字使用**）：

 1. 开始 required
 仅执行 required 任务，逐个推进 `[ ]` → `[~]` → `[x]`。
 2. 开始 required + optional
 required 完成后顺带处理 optional 任务。
 3. 用 task-swarm 多 agent 并发
 委派给 task-swarm 编排器，多 coder 并发 + reviewer + validator。**v0.6 此选项不可用**，请回退选 1 / 2。
 4. 暂不 coding
 文档已落地但暂不开始实现。`/specode:end` 关闭会话。

请按 §3.7.1 类型 A 骨架输出（编号 1-4 + 保留位 5 Type something + 6 Chat about this）。
推荐选项编号：1
状态行 footer 在前，`AWAITING_USER_CHOICE` 末行，立即 end turn。
```

##### (6) takeover-options （类型 A 单选）

```text
## ⛔ 必须呈现「该 spec 已被其他窗口持有」选择器（类型 A 单列单选）

active spec: <slug>（phase=<phase>）
锁持有者: session_id=<other-id 前 8 位>, 最近 heartbeat: <iso>

标题：该 spec 已被其他窗口持有
正式选项（**逐字使用**）：

 1. 强制接管
 驱逐对方窗口的锁，本会话成为新锁主；对方下一次写操作会被 verify-lock 拒绝。
 2. 只读查看
 不持锁，加载文档进入只读模式；所有 Edit/Write 在 SKILL.md 层面被劝阻。
 3. 取消
 不接管，关闭本次 `/specode:continue`。

请按 §3.7.1 类型 A 骨架输出（编号 1-3 + 保留位 4 Type something + 5 Chat about this）。
推荐选项：无（让用户根据对方是否仍活跃自己判断）
状态行 footer 在前，`AWAITING_USER_CHOICE` 末行，立即 end turn。
```

##### (7) acceptance-gate （类型 A 单选）

```text
## ⛔ 必须呈现「验收结论」选择器（类型 A 单列单选）

active spec: <slug>（phase=acceptance）
acceptance-checklist.md 已填写。已通过：<n_pass>，未通过 / 待复核：<n_fail>。

标题：验收结论
正式选项（**逐字使用**）：

 1. 验收通过，进入 iteration（推荐）
 所有 SHALL 已满足；如有后续调整走 iteration 子循环。
 2. 继续修改
 仍有未达标项，回到 requirements / design / tasks 调整。

请按 §3.7.1 类型 A 骨架输出（编号 1-2 + 保留位 3 Type something + 4 Chat about this）。
推荐选项编号：1（当 n_fail=0）；其他情况无推荐。
状态行 footer 在前，`AWAITING_USER_CHOICE` 末行，立即 end turn。
```

##### (8) iteration-scope （类型 C 复选； 启用，）

```text
## ⛔ 必须呈现「本轮 iteration 调整范围」选择器（类型 C 复选框多选）

active spec: <slug>（phase=iteration）

标题：本轮 iteration 调整范围
正式选项（**逐字使用**）：

 1. 改 requirements
 新增 / 修改 EARS SHALL 条款。
 2. 改 design
 架构 / 接口 / 数据模型调整。
 3. 改 tasks
 新增任务或调整已有任务范围。
 4. 重跑测试
 不改文档，重新验证当前实现。

请按 §3.7.3 类型 C 骨架输出（编号 1-4 + 保留位 5 Type something；含"全不选 = none / 整体讨论 = Chat about this"回复格式说明）。
允许"全不选"（视为本轮 iteration 取消）。
状态行 footer 在前，`AWAITING_USER_CHOICE` 末行，立即 end turn。
```

##### 常量库实现要求

- `SELECTOR_PROMPTS` 字典 key 为字符串（如 `'workflow-choice'`），值为 `str`（含 `<占位>` 待替换）。
- `pending_selector` 字段取值就是这些 key；命令层切到 phase-gate 时写入。
- `on-user-prompt` 拿到 pending_selector 后做两步：
 1. 字符串模板替换（`<slug>` → `sessions.active_spec_slug` 等）
 2. 包入 `hookSpecificOutput.additionalContext`，emit 到 stdout

测试要求（v0.6）：对每个常量做 snapshot 单元测试，确保字符串骨架与 §3.7.4 表完全一致；任何修改必须同步改测试。

---

### 3.8 代码-文档同步提醒（替代原 INV-1 / INV-2）

原 INV-1 / INV-2 用 hook 强制实现"双向同步"：

- INV-1（PreToolUse）：源码改动前要求同 turn 有文档改动 → `exit 2` 阻断
- INV-2（Stop）：turn 结束前要求触碰过代码的 turn 至少触碰一份文档 → `exit 2` 阻断

0.4.0 已经把它们降为 advisory（sticky ledger）；0.5.0 完全删除；0.6.0 用**两段提醒文本注入**实现同样的纪律保证，**不阻断**——由模型自行决定是否落实。

#### 3.8.1 Spec 文档清单（提醒中列出的固定名单）

| 文档名 | 用途 | 何时该更新 |
|---|---|---|
| `requirements.md` | 需求-first 工作流的需求文档（EARS SHALL 写法） | 需求 / 验收标准调整 |
| `bugfix.md` | bugfix 工作流的问题描述（与 requirements.md 互斥） | 缺陷范围 / 复现步骤 / 期望行为调整 |
| `design.md` | 技术设计（架构 / 接口 / 数据模型） | 架构 / 接口 / 数据模型决策调整 |
| `tasks.md` | 任务拆分 + 进度 + `_需求：x.y_` traceability | 任务范围调整 / 状态推进 `[ ]` → `[~]` → `[x]` |
| `acceptance-checklist.md` | 验收检查表（跟随 requirements/bugfix） | requirements/bugfix 改动后**同 turn** 重写 |
| `implementation-log.md` | 实现记录（可选；记录设计偏离 / 关键决策） | 实施期间记录设计偏离、解决方案、技术决策 |

#### 3.8.2 `on-user-prompt` 注入的「输入侧」提醒文本

每次用户提交 prompt 时，若 active spec 存在，`on-user-prompt` 把以下文本作为 `additionalContext` 一段注入（与 §3.7 的 selector 提示并列）：

```text
## 📝 文档优先提醒（用户输入侧）

active spec：<slug>（phase=<p>）
此 spec 的可写文档：
 • requirements.md / bugfix.md
 • design.md
 • tasks.md
 • acceptance-checklist.md
 • implementation-log.md（如有）

请评估用户本次输入是否涉及以下变更：

- 需求 / 验收标准调整 → 先 Edit `requirements.md` 或 `bugfix.md`，**同 turn** 重写 `acceptance-checklist.md`
- 架构 / 接口 / 数据模型决策 → 先 Edit `design.md`
- 任务范围 / 状态推进 → 先 Edit `tasks.md`
- 实现期间的设计偏离 / 关键决策 → 在 `implementation-log.md` 追加条目
- 仅闲聊 / 状态查询 / 无关讨论 → 无需文档变更

文档变更要**在同一轮 turn 内先于代码改动落盘**；不要把"待会儿写"留作 verbal commitment——chat 内容不会进入 next session。
```

#### 3.8.3 `on-stop` 注入的「输出侧」提醒文本

每次模型 turn 结束时，若 active spec 存在，`on-stop` 注入以下 `additionalContext`：

```text
## 🔄 代码-文档同步提醒（turn 结束侧）

active spec：<slug>（phase=<p>）

本 turn 即将结束。如果你在本 turn 内修改了源代码，请自检以下三项：

1. `tasks.md` 是否更新？ —— 推进任务标记（`[ ]` → `[~]` → `[x]` / blocked）
2. `implementation-log.md` 是否记录？ —— 实现说明、设计偏离、技术决策
3. `design.md` 接口契约是否变化？ —— 若改了，同步 Edit

如有遗漏，请在 chat 显式承诺下一轮第一件事就是补齐。

（本提醒**不阻断 turn**——是否补齐由你判断。但代价是 next session `/specode:continue` 时，未写入文档的变更**全部丢失**。）
```

#### 3.8.4 SKILL.md 对模型的约束

SKILL.md §Code-Doc Sync Reminders 一节声明：

- 看到「📝 文档优先提醒（输入侧）」 + 用户输入含需求 / 设计 / 任务调整 → 本 turn **优先 Edit 对应文档**，再处理代码或解释。
- 看到「🔄 代码-文档同步提醒（输出侧）」 + 本 turn 触碰过 Write/Edit 源码 → turn 结束前补齐文档；若实在无法当 turn 补齐，在 chat 显式承诺下一轮第一件事补——并立刻在下一轮做到。
- 没看到提醒（hook 失败 / 无 active spec）→ 仍保持文档优先纪律。这是 SKILL.md 的硬约束，不依赖 hook 触发。
- `implementation-log.md` 是"轻量级补救手段"：若改了代码但暂时没法重写 design.md / tasks.md，至少在 log 里记一行——为 next session 留线索。空 log 等于没改过。

### 3.9 `spec_lint.py`

无参。检查：

- `acceptance-checklist.md.mtime < requirements.md.mtime` → WARNING（follow-mode 落后）。
- `tasks.md` 里的 `_需求：x.y_` 标签是否都能追溯到 `requirements.md` / `bugfix.md` 章节。
- `implementation-log.md` 条目是否过短（< 30 字符）或缺文件引用 → WARNING。
- EARS SHALL 缺动词或缺 trigger → WARNING。

退出码：0（无错或仅 WARNING）；其他不使用（lint 不强制阻断）。

### 3.10 `spec_status.py`

无参。读 `~/.specode/sessions/` + `.active-specode.json` + `.config.json`，输出当前 session / spec / phase / lock / tasks 计数。

### 3.11 `task_swarm.py`（v0.7）

详细协议见 §6。CLI 接口：

```text
task_swarm.py init --tasks <tasks.md 绝对路径> [--max-parallel N]
task_swarm.py status --run <run_id>
task_swarm.py plan --run <run_id>
 # 输出下一步该 fork 的 subagent 列表（JSON）
task_swarm.py advance --run <run_id> --phase <coding|review|p0-fix|validation|v-fix>
 --round <n>
 # subagent 全部返回后调用：解析 outbox、推进 state.json、返回下一步建议
task_swarm.py writeback --run <run_id> --group <N>
 # 当前 group 全部 pass 后回写 tasks.md（line-safe diff）
task_swarm.py heartbeat --run <run_id>
 # 透传给 spec_session.py heartbeat
```

`plan` / `advance` 是**确定性查询**：脚本负责"按文件冲突分组、按 phase 推进状态机"这些计算；主代理负责"按 plan 输出 fork subagent、按 advance 输出决定下一步派谁"。状态机在脚本里，派发与解析在主代理里——两层各做各的事（同 §0 第 4 条选择器原则）。

---

## 4. spec-mode + task-swarm 集成点

### 4.1 用户流程（不变）

```
/specode:spec <需求>
 │
 ▼
[intake] ──澄清?──► Template B 开放式问答 + 「澄清完成?」选择器（模型按 §3.7 生成）
 │
 ▼
workflow 选择器
 ├─ Requirements first ──► requirements.md
 ├─ Technical Design first ─► design.md
 └─ Bugfix ─────────────► bugfix.md
 │
 ▼
[requirements/bugfix] ─文档确认─► [design] ─文档确认─► [tasks]
 │
 ▼
 任务执行 selector：
 ┌─ 开始 required
 ├─ 开始 required + optional
 ├─ 用 task-swarm 多 agent 并发 ◄── └─ 暂不 coding
 │
 ▼
 [implementation] ◄── 主会话线性 / task-swarm 编排
 │
 ▼
 [acceptance] ──验收门──► [iteration]（循环）
```

### 4.2 task-swarm 与主会话的集成边界

详细协议见 §6。这里只列与 spec-mode 主流程的交接点：

- **谁持锁**：始终是主会话。subagent 不直接持锁、不直接 verify-lock。
- **何时进入 task-swarm**：tasks.md 确认后，"任务执行"选择器（§3.7.2）若选中"用 task-swarm 多 agent 并发"，主会话切到 task-swarm 编排模式（按 `commands/specode:task-swarm.md` 协议）。
- **何时退出**：所有 group 完成（writeback 写完最后一组），主会话返回 spec-mode 的 implementation→acceptance 通路。
- **退出 task-swarm 后**：`acceptance-checklist.md` 仍由主会话填，validator 的 pass 不等价于 spec acceptance（lint + 用户验收 selector 仍需走完）。
- **tasks.md 写回**：永远走 `task_swarm.py writeback`。CLI 严格 diff——只允许 checkbox toggle + `> ` 注释块。的 `on-pre-tool-use` hook 在主会话直写 tasks.md 时**提醒**（不阻断）。
- **heartbeat**：主编排器每 5 分钟 / 每完成一个 subagent 后调一次 `task_swarm.py heartbeat`（透传给 `spec_session.py heartbeat`）。
- **stage 收敛规则**（保留 0.3.0 语义）：
 - reviewer advisory（带 `[req:x.y]` / `[security]` / `[contract]` 证据标签的 P0 才进入 tasks.md 注释；未带证据降级为 advisory）。
 - validator 阻塞（fail → r2 coder 修复轮，最多 `--max-rounds` 轮）。
 - reviewer 不触发 r2 coder；只有 validator fail 触发。

### 4.3 spec-writer agent（v0.6）

```yaml
---
name: spec-writer
description: 由 spec 主会话委派的文档生成 agent。读 .config.json 的 phase，按当前 phase（requirements / bugfix / design / tasks）写对应 markdown，使用 assets/templates 模板。严格只产文档，不写代码。
tools: Read, Write, Edit, Grep, Glob
model: sonnet
---
```

**工具白名单为何不含 Bash**：物理隔离原则。Bash 一旦给出，agent 就可以跑任意命令、git commit、调修源码——边界靠 prompt 自律就太弱了。去掉 Bash 后：

- agent 进程从启动就没有 shell 能力，无法运行代码、跑测试、动 .config.json、动锁。
- 所有 lock / heartbeat / verify-lock / phase-transition 一律由**主会话**在 fork 前后调用 `spec_session.py`。
- agent 只剩 Read（读模板与上份文档）+ Write/Edit（写新文档）+ Grep/Glob（检索引用）。

**职责边界**：

- ✅ 写 `requirements.md` / `bugfix.md` / `design.md` / `tasks.md`
- ✅ 写 `acceptance-checklist.md`（跟随 requirements/bugfix）
- ❌ 不写源码（虽然 Write/Edit 工具层不约束路径，但 prompt 协议明确要求只写 spec 文档；越界由 reviewer 在下一阶段挑出来）
- ❌ 不切 phase
- ❌ 不动 `.config.json` / 锁

**调用时机**：

- 主会话每到 requirements / design / tasks 三个文档生成点 → fork spec-writer。
- prompt 注入：澄清结果（intake outcome）、上一份文档路径、模板路径、`_需求：x.y_` traceability 要求。
- spec-writer 写完返回路径 + 章节摘要；主会话拿到后调用 `spec_choice.py` 走"文档确认"selector。

---

## 5. Hook 策略

**总原则**：所有 hook 永远 `exit 0`，仅通过 `hookSpecificOutput.additionalContext` JSON 把提示注入模型上下文。**没有 `exit 2`，没有拦截**。Hook 在节点上把"该想什么"递给模型，让不让做由模型自己定。

所有 hook 共用 `spec_session.py on-*` 子命令家族；读取 / 写入 `~/.specode/sessions/<session_id>.json`（§3.3）以维持"会话即状态"模型。

### 5.1 `SessionStart` `on-session-start`

```sh
sh "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" \
 "${CLAUDE_PLUGIN_ROOT}/scripts/spec_session.py" on-session-start
```

行为：

1. 从 stdin payload 拿 `session_id`、`cwd`。
2. **强制写**`~/.specode/sessions/<session_id>.json`，初始状态：
 ```json
 { "session_id": "...", "started_at": "<now>",
 "mode": "idle", "active_spec_slug": null, "active_spec_dir": null,
 "phase": null, "lock_state": "released" }
 ```
 （如文件已存在——可能是断线重连——保留原状态，仅刷新 `last_activity_at`）
3. 注入 `additionalContext`：
 ```text
 ## Specode session 就绪

 当前会话 session_id: <id>
 后续调用 specode CLI 时请始终用 `--session <id>` 传入。

 （此 session 当前 mode=<idle|active|readonly>，spec=<slug 或 无>；
 如需开始新 spec，使用 `/specode:spec <需求>`；如需恢复，使用 `/specode:continue [slug]`。）
 ```
4. 任何异常 catch + `exit 0`。

### 5.2 `UserPromptSubmit` `on-user-prompt`

```sh
sh "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" \
 "${CLAUDE_PLUGIN_ROOT}/scripts/spec_session.py" on-user-prompt
```

读 stdin payload、读 `~/.specode/sessions/<session_id>.json` 决定注入哪些段落（**全部叠加**到一个 `additionalContext`）：

**先做 fast-path 拦截**：

- payload.prompt 匹配 `^/spec\s+(-h|--help)\s*$` → 注入 §3.6「/specode:spec -h fast-path」并立即返回（其他段落跳过）。
- 匹配 `^/specode:spec\s+--(vault-status|detect-vault|sync-status)\s*$` → hook 直接执行对应 CLI，注入"逐字打印以下"提示并立即返回。

**否则按 mode 叠加注入**：

| 段落 | mode=active | mode=readonly | mode=idle/ended | 来源 |
|---|---|---|---|---|
| (a) session_id 提醒 | ✅ 注入完整 session_id | ✅ | ✅ | §5.1 一段简化版 |
| (b) selector 提示 | ✅ 命中 §3.7.2 时 | ✅（只读不能确认，仅 informative） | ❌ | §3.7.3 |
| (c) 文档优先提醒 | ✅ §3.8.2 完整文本 | ⚠️ 简化版（只读不让编辑） | ❌ | §3.8.2 |
| (d) 状态行 footer 要求 | ✅ §3.5 模板 | ✅ 加 [只读] 标记 | ❌ | §3.5 |
| (e) spec 模式提醒 | ✅"仍在 spec 模式，遵循流程" | ✅ "只读模式" | ❌ | 固定常量 |

任何异常 catch + `exit 0`。

### 5.3 `Stop` `on-stop`

```sh
sh "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" \
 "${CLAUDE_PLUGIN_ROOT}/scripts/spec_session.py" on-stop
```

每次模型 turn 结束时触发：

- mode=idle / ended / sessions 文件不存在 → 立即 `exit 0`，不注入。
- mode=active → 叠加注入：
 - §3.8.3「🔄 代码-文档同步提醒（输出侧）」
 - 「⛔ 你仍处于 spec 模式（spec=<slug>, phase=<p>, mode=active）。下一 turn 必须继续遵守 selector / 文档优先 / 状态行 footer 三项纪律。如需退出，使用 `/specode:end`。」
- mode=readonly → 仅注入"只读模式"提示（无文档同步要求，因为只读不能写）。
- **不检测**本 turn 是否真的改了代码——hook 文本永远说"如果你改了代码…"，自检责任在模型。
- 任何异常 catch + `exit 0`。

### 5.4 `SessionEnd` `on-session-end`

```sh
sh "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" \
 "${CLAUDE_PLUGIN_ROOT}/scripts/spec_session.py" on-session-end
```

行为：

1. 读 sessions/<id>.json。
2. 若 mode 仍是 `active` 或 `readonly` 且持有某 spec 锁 → 释放锁（写 `<spec-dir>/.config.json` 的 lock 字段 = null）。这是"忘了 /specode:end"的兜底，保证 stale lock 不积累。
3. 将 sessions/<id>.json 写为 `{mode: "ended", ended_at: <now>, ...}`。
4. 不输出 additionalContext（事件已结束，模型不会再读）。
5. 异常 catch + `exit 0`。

### 5.5 `PostToolUse` `on-task-completed`

```sh
sh "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" \
 "${CLAUDE_PLUGIN_ROOT}/scripts/spec_session.py" on-task-completed
```

- matcher：`Task`（subagent 完成时触发）。
- 不在 task-swarm run 期间 → 立即 `exit 0`。
- 在 task-swarm run 期间 → 调 `task_swarm.py plan --run <id>` 拿到"下一步该做什么"，包成 `additionalContext` 注入。提示包含：当前 phase、轮号、当前 group 未返回 subagent 数；全部返回时给出"该按 §11.6 fork 下一阶段 subagent 了"建议。
- **不阻断、不替主代理决策**。提醒文本结尾固定加一句"是否 fork、fork 谁仍由你判断；本提醒可忽略"。
- 任何异常一律 catch + `exit 0`。

详细行为见 §11.6 hook 提醒矩阵。

### 5.6 `UserPromptSubmit` `on-heartbeat-quiet`

```sh
sh "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" \
 "${CLAUDE_PLUGIN_ROOT}/scripts/spec_session.py" on-heartbeat-quiet --quiet
```

- mode ≠ active → 立即 `exit 0`，不输出任何 JSON。
- mode = active → 续约一次锁（`<spec-dir>/.config.json.lock.last_heartbeat_at = now`），刷新 `sessions/<id>.json.last_activity_at`，`exit 0`；**不注入** `additionalContext`（避免与 `on-user-prompt` 混叠）。
- 不读 stdin，不写 audit。
- 与 `on-user-prompt` 在同一 event 注册为**第二个** handler（hooks.json 同 event 多 handler）。heartbeat 静默执行，与 `on-user-prompt` 互不打架。

### 5.7 `PreToolUse` `on-pre-tool-use`

```sh
sh "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" \
 "${CLAUDE_PLUGIN_ROOT}/scripts/spec_session.py" on-pre-tool-use
```

- matcher：`Edit|Write|MultiEdit`。
- 不在 task-swarm run 期间 → 立即 `exit 0`。
- 在 task-swarm run 期间 + `tool_input.file_path` 指向 active spec 的 `tasks.md` + 不是 writeback 子进程触发的 → `exit 0` + 注入「⛔ 检测到正在直接 Edit/Write `tasks.md`，task-swarm run 期间应走 `task_swarm.py writeback` 以保证 line-safe diff，请放弃当前编辑」。
- **不阻断**——模型若执意写，仍可写；后续 reviewer + writeback 校验会发现 diff 不合规并报告。
- 其他情况 → `exit 0`，不注入。

### 5.8 全局 bypass

`SPECODE_GUARD=off` → 所有 hook 都立即 `exit 0`，不读 payload、不注入提示、不写 sessions 文件。仅作调试逃生口。

### 5.9 hook 列表对照

| Event | Handler 子命令 | 引入版本 | 触发条件 | 是否注入 |
|---|---|---|---|---|
| `SessionStart` | `on-session-start` | **v0.6** | 每次新宿主会话启动 | session_id + spec 模式状态 |
| `UserPromptSubmit` | `on-user-prompt` | **v0.6** | 用户提交 prompt | fast-path / selector / 文档优先 / 状态行 footer / 模式提醒（按 mode 叠加） |
| `Stop` | `on-stop` | **v0.6** | 模型 turn 结束 | 代码-文档同步 + spec 模式延续提醒（active 时） |
| `SessionEnd` | `on-session-end` | **v0.6** | 宿主会话退出 | 否（写 mode=ended + 释锁兜底） |
| `PostToolUse` Task | `on-task-completed` | **v0.7** | subagent 完成 | task-swarm 下一步提醒 |
| `UserPromptSubmit` | `on-heartbeat-quiet` | v0.8 | 用户提交 prompt | 否（仅续锁） |
| `PreToolUse` | `on-pre-tool-use` | v0.8 | Edit/Write/MultiEdit | tasks.md 直写命中时 |

v0.6 完成后获得："session 生命周期管理 + 选择器提醒 + 双侧文档同步提醒 + 状态行 footer + /specode:spec -h fast-path"——完整覆盖 spec 模式 5 类节点。v0.7 增加 task-swarm 节点提醒。v0.8 是稳健性加固。

---

## 6. 测试策略

| 版本 | 测试 |
|---|---|
| v0.6 | 单元：`spec_vault.py` 三层解析、`spec_init.py` 目录创建（含 sessions/<id>.json 强制写）、`spec_session.py` 锁状态机、`on-session-start/-user-prompt/-stop/-session-end` 各场景注入文本、状态行 footer 模板、`/specode:spec -h` fast-path、`spec_lint.py` 规则<br>集成：从 `/specode:spec <需求>` 到 acceptance 全流程跑通；SessionStart→UserPromptSubmit→Stop→SessionEnd 事件链完整 |
| v0.7 | 单元：`task_swarm_state.py` 状态机（含 group 分组、phase 转换、validator 循环计数）、`task_swarm_parse_md.py` stage 拆分、`task_swarm_outbox.py` schema 校验、`task_swarm_writeback.py` line-safe diff、`on-task-completed` hook 输出<br>集成：一个 5 阶段 spec（含跨阶段同文件冲突 + 一次 P0 修复 + 一次 validator 循环）跑完 task-swarm |
| v0.8 | 单元：两个新 hook 的 exit code 矩阵<br>集成：heartbeat 续约、stale 锁回收、tasks.md 直写被提醒 |

**不测**：INV 系列（已删除）、telemetry、audit log。

---

## 7. 文档同步

每个版本发布时同步更新：

- `CHANGELOG.md`：用 spec-mode 迁移 + task-swarm 重建的语境，不再用 INV 措辞
- `README.md` / `README.zh-CN.md`：恢复 Usage 章节（不带 INV 表）
- `CONTRIBUTING.md` / `DEV.md`：补回 lock 状态机说明、CLI 接口测试约定

---

## 8. 升级与兼容

| 0.5.0 → 0.6.0 | 用户首次启用持久 spec session 时由 `spec_init.py` 引导建 vault；之前若有 `~/.specode/sessions/` 残留可保留 |
| 0.6.0 → 0.7.0 | 已有 spec session 不受影响；`/specode:task-swarm` 命令开放 |
| 0.7.0 → 0.8.0 | 引入 2 个 hook；用户可 `SPECODE_GUARD=off` 全局关闭 |

每个版本独立可用：v0.6 可单跑线性 coding；v0.7 可单跑 task-swarm；v0.8 是稳健性加固。

---

## 9. 风险与未决

| 风险 | 缓解 |
|---|---|
| 长 context 下主会话漂移 task-swarm 状态机 | `state.json` 单一事实源 + `commands/specode:task-swarm.md` 显式协议 + `task_swarm.py plan` 确定性查询 + `on-task-completed` hook 节点提醒 |
| `session_id` 在多窗口下混淆 | hook payload 自带 session_id 唯一；sessions/<id>.json 文件名即标识；CLI 命令必须 `--session <id>` 传入；mismatch → CLI exit 1 |
| `/specode:end` 后 hook 仍催 spec 流程 | `/specode:end` 写 sessions mode=ended；hook 看到 ended 立即不注入；SessionEnd hook 兜底（即使忘 /specode:end，会话结束也置 ended + 释锁）|
| 模型忘记当前 session_id | `on-session-start` 注入完整 session_id；`on-user-prompt` 每轮重复包含 session_id 一段；CLI 接 `--session` 失败 → exit 1 提示重读 hook 注入 |
| sessions 文件半成功（写一半挂） | 所有 CLI 写入路径强制 tempfile + atomic rename + fsync；写失败回滚 spec.config.json 已变更字段；exit 1 上报 |
| 主代理凭印象派 coder（不按 plan 输出） | SKILL.md §task-swarm 硬约束"派发前必须先调 `plan`，输出**逐字拷贝**到 Task block"；reviewer 在事后查跨文件契约违规 |
| 多 coder 并发时跨 stage 同文件冲突 | `init` 时按 `@writes` 集合不相交切 group；同一 group 内文件物理不重叠 |
| 多窗口同时 fork spec-writer | spec-writer 内主代理 fork 前先 `verify-lock`；锁仍是主会话独占 |
| Obsidian vault 检测在新平台失效 | spec-mode 已验证 macOS / Windows / Linux；保留 fallback 链允许 `SPECODE_ROOT` 跳过 |
| reviewer 假阳性 P0 | 强制证据标签（`[req:x.y]` / `[security]` / `[contract]`），未带证据自动降级为 advisory（写入 tasks.md 但不进 fix loop） |
| v-fix 陷入死循环（无穷 validator fail） | 连续 3 轮同 fail 签名（测试名 + assertion 哈希）→ 整个 group 标 `failed-deadloop`、停止循环、上报用户 |
| 用户在 task-swarm 期间手动 Edit tasks.md | v0.7 仅靠 SKILL.md 警示；v0.8 由 `on-pre-tool-use` hook 提醒（不阻断） |

未决待实施过程中再确认：

- ~~`spec_init.py` 是否支持 `--persist=false` 模式~~ — **已决**：不支持，所有 `/specode:spec` 都持久（§0 第 10 条）。
- `spec-writer` 是否需要单独的 `references/spec-writer.md` 详细 prompt 协议，还是 SKILL.md 一段即可。
- task-swarm 的 `state.json` 是否要支持中断恢复（v0.7 不实现 resume，但 state.json 已经为之留路：所有信息从 outbox + state.json 可重建）。
- P1/P2 是否提供"主代理显式 opt-in 修复"路径（v0.7 不默认；可作为 v0.8 扩展）。
- reviewer 单实例 vs 大 group 时上下文超限：单 group 含 10+ stage 时 reviewer 可能上下文不够——是否需要按"reviewer 分包"降级（v0.7 不处理；先做简单单实例，规模问题留观察）。

---

## 10. 实施清单（手）

按这个顺序推进 v0.6.0：

1. 在 `plugins/specode/scripts/` 新建 `run.sh` / `run.cmd` / `spec_vault.py`，最小可跑（三层根目录解析）。
2. 新建 `spec_init.py`：创建 `<doc_root>/specs/<slug>/{requirements.md,...}` 骨架 + `.config.json` + 更新 active-pointer。
3. 新建 `spec_session.py`：
 - **数据层**：sessions/<id>.json schema（§3.3）的读 / 写 / 原子 rename + fsync 工具函数。
 - 业务子命令：`acquire` / `release` / `heartbeat` / `verify-lock` / `phase-transition` / `load` / `continue` / `end` / `status` / `read-session`。
 - 每个**写**类命令必须把状态同步到 sessions/<id>.json + `<spec-dir>/.config.json` 两处，原子写、fsync、失败回滚。
 - hook 子命令（v0.6 必须四个，均 exit 0 纯注入）：
 - `on-session-start`：写 sessions 文件初始态 + 注入 session_id 提醒（§5.1）。
 - `on-user-prompt`：fast-path / selector（§3.7.3）/ 文档优先（§3.8.2）/ 状态行 footer（§3.5）/ 模式提醒（§5.2）。
 - `on-stop`：代码-文档同步（§3.8.3）+ spec 模式延续提醒（§5.3）。
 - `on-session-end`：写 mode=ended + 释锁兜底（§5.4）。
4. 新建 `plugins/specode/hooks/hooks.json`：注册四个 event。
 - `SessionStart` → `spec_session.py on-session-start`
 - `UserPromptSubmit` → `spec_session.py on-user-prompt`
 - `Stop` → `spec_session.py on-stop`
 - `SessionEnd` → `spec_session.py on-session-end`
5. 新建 `spec_lint.py`：lint 规则。
6. 新建 `spec_status.py`：状态查询输出（也作为 `/specode:status` 命令的 CLI 入口）。
7. 重写 `skills/specode/SKILL.md`：
 - 完整 spec-mode 流程，去 INV 措辞。
 - 新增 §Session Lifecycle：`/specode:spec` / `/specode:continue` / `/specode:end` 三命令的强制写入语义（§3.3）+ session_id 用法。
 - 新增 §Status Footer：状态行 footer 模板要求（§3.5），active / readonly 两种格式。
 - 新增 §Selectors：选择器统一骨架（§3.7.1）+ 6 个场景表（§3.7.2）+ 看到 hook 提醒时的强约束（§3.7.4）。
 - 新增 §Code-Doc Sync Reminders：6 份文档清单（§3.8.1）+ 看到双侧提醒后的响应约束（§3.8.4）。
 - 新增 §Help Fast-path：`/specode:spec -h` 必须逐字打印 hook 注入的帮助文本（§3.6）。
8. 补 `skills/specode/references/{workflow,lock-protocol,obsidian,prompts,templates,iteration}.md`。
 - `prompts.md` 改写：从"调用 `spec_choice.py` 的命令样例"改成"6 个场景的标题/选项常量表 + 输出格式"。
 - `lock-protocol.md` 中锁持有者键改成 `session_id`，与 §3.3 一致。
9. 新增 `agents/spec-writer.md`（tools: `Read, Write, Edit, Grep, Glob`——无 Bash）。
10. 更新 5 个 `commands/*.md` 让其与 SKILL.md 一致：
 - `spec.md` 删除 `--persist` 提示，明确所有 `/specode:spec <需求>` 都持久。
 - `end.md` 强调 mode=ended 写入与锁释放。
 - 全部命令在执行时通过 `--session <session_id>` 把当前 session 传给 CLI。
11. 写 `plugins/specode/tests/`：单元 + 集成。
 - 单元：sessions schema 读写原子性、`spec_vault.py` 三层、`spec_init.py` 目录创建（含 sessions 写）、`spec_session.py` 锁状态机、四个 hook 子命令各 mode 分支、状态行 footer 模板、`/specode:spec -h` fast-path、`spec_lint.py` 规则。
 - 集成：从 `SessionStart → /specode:spec <需求> → … → /specode:end → SessionEnd` 完整事件链；多窗口同 spec 接管三选项；`/specode:end` 后 hook 立刻停止注入。
12. 升版本到 0.6.0；写 CHANGELOG；hook 已引入 4 个（`on-session-start` / `on-user-prompt` / `on-stop` / `on-session-end`），均 `exit 0` 纯注入。

每完成一步在 git 留一个独立 commit。

---

## 11. task-swarm 详细规格（v0.7 范围）

### 11.1 角色与并发度

| 角色 | 是否并发 | 工具白名单（物理隔离） | 何时被 fork |
|---|---|---|---|
| `task-swarm-coder` | **多实例并行** | `Bash, Read, Edit, Write, Grep, Glob` | coding / p0-fix / v-fix 各 phase |
| `task-swarm-reviewer` | **单实例** | `Bash, Read, Grep, Glob`（无 Edit/Write） | review phase（每个 review-round 一次） |
| `task-swarm-validator` | **单实例** | `Bash, Read, Grep, Glob`（无 Edit/Write） | validation phase（每个 validation-round 一次） |
| `task-swarm-planner` | 视情况 | `Bash, Read, Grep, Glob, Write` | 可选：tasks.md 不够细时主代理先 fork planner 拆分 |

reviewer / validator 单实例的理由：

- reviewer = 一个上帝视角的读代码人，要对全部 coder 产物有整体判断；切成多份会破坏交叉关联检测（接口契约 / 跨模块一致性）。
- validator = 跑测试的客观信号，并发跑没意义；同一测试套件单进程跑一次就够。
- coder = 并行收益最大（多 stage 互不干扰时各占一份文件），且 reviewer / validator 反正还会合并审视——并发不影响下游判断。

### 11.2 文件冲突避免（主代理委派约束）

`task_swarm.py init` 解析 tasks.md 时，按以下规则把 stage 切成 **group**：

1. 提取每个 stage 的 `@writes:<files>` 列表（含通配符展开）。
2. 在同一 group 内：任意两个 stage 的 `@writes` 集合**不相交**且**无 `@depends-on` 关系**。
3. 跨 group：上一 group 全部 pass 后才能开始下一 group。
4. 一个 stage 即使可以并发也不会被拆——以"stage = coder 任务粒度的最大单元"为铁律。

`init` 输出示例：

```json
{
 "run_id": "20260519-141200-ab12cd",
 "groups": [
 [{"stage": 1, "writes": ["src/models/user.py"]},
 {"stage": 2, "writes": ["src/auth/service.py"]}],
 [{"stage": 3, "writes": ["src/api/login.py"], "depends_on": [1, 2]}]
 ],
 "tasks_md": "/abs/path/to/tasks.md"
}
```

主代理在 coding phase **同一 message 内**发出多个 Task block（每个对应当前 group 的一个 stage），由宿主并行执行。SKILL.md 强约束：

> 派发 coder 时，必须先调 `task_swarm.py plan` 拿当前 group 的 stage 列表，**逐字拷贝**到 Task block——绝不可凭印象自己派；脚本已经处理过文件冲突分组。如果 tasks.md 在 task-swarm 期间被改了（新增 stage），先 `task_swarm.py reinit`（v0.7 不实现 reinit → 报 error 让用户重跑 `/specode:task-swarm`）。

### 11.3 Phase 状态机

```
init → coding → review ─┬─► p0-fix ──► validation
 │ │
 └─►(no P0) ────────┘
 │
 ┌────────────────┤
 │ │
 (pass) (fail)
 │ │
 ▼ ▼
 writeback v-fix ──► validation (循环)
 │
 ▼
 next group / done
```

| Phase | 触发 | 子代理 | 完成条件 | 失败行为 |
|---|---|---|---|---|
| `coding` | 进入新 group | 并发 N 个 coder | 全部 STATUS: ok | 任一 fail → 主代理报告用户、整个 group failed |
| `review` | coding 完成 | 单个 reviewer | review.md 含分级 P0/P1/P2 | reviewer fail → 主代理报告，**继续走** validation（reviewer 是 advisory） |
| `p0-fix` | review 含**带证据标签**的 P0 | 并发 M 个 coder（按 P0 涉及文件分组） | 全部 STATUS: ok（不再 review） | 任一 fail → 主代理把 P0 标"未修复"写入 tasks.md，继续走 validation |
| `validation` | p0-fix 完成 或 review 无 P0 | 单个 validator | validator pass | validator fail → 进入 v-fix |
| `v-fix` | validation fail | 并发 M 个 coder（按 validator 修复指引涉及文件分组） | 全部 STATUS: ok | 任一 fail → 主代理报告用户、整个 group failed |
| `validation` (再) | v-fix 完成 | 单个 validator | pass → writeback；fail → v-fix 循环 | 死循环检测：连续 3 轮同一 fail 签名 → 整个 group failed |
| `writeback` | validation pass | 主代理调 CLI | tasks.md `[ ]` → `[x]` + 评审块追加 | line-safe diff 失败 → 主代理报错、不推进 |

**关键差别（与原 0.3.0 方案）**：

- 0.3.0 是"每 stage 一个 coder→reviewer→validator 循环"；新方案是"整个 group 一起 coding → 一次 reviewer → 一次 validator"。reviewer / validator 看的是 group 范围。
- reviewer P0 → coder 修复**只触发一次**（修完不再 re-review，直接进 validation）。理由：reviewer 是 advisory，循环 review 容易陷入风格争论。
- validator fail → coder 修复**循环**到 pass。理由：validator 是客观测试信号，必须达成。
- 死循环检测：v-fix → validation 连续 3 轮同一 fail 签名（测试名 + assertion）→ 整个 group failed 上报用户，不无限循环。

### 11.4 子代理产物 schema（信息回流）

每个子代理 fork 时主代理把 prompt 文件预渲染到：

```
.task-swarm/runs/<run_id>/agents/<agent-key>/task.md
```

子代理在 prompt 中被要求把产物写到：

```
.task-swarm/runs/<run_id>/agents/<agent-key>/outbox/
 result.md ← coder
 review.md ← reviewer
 validation.md ← validator
```

`agent-key` 命名约定：

- coder：`coder-g{group}-s{stage}-r{round}` 例如 `coder-g1-s2-r1`
- p0-fix coder：`coder-p0fix-g{group}-r{round}-f{file-idx}` 例如 `coder-p0fix-g1-r1-f0`
- v-fix coder：`coder-vfix-g{group}-r{round}-f{file-idx}`
- reviewer：`reviewer-g{group}-r{round}`（v0.7 round 恒为 1）
- validator：`validator-g{group}-r{round}`

#### 11.4.1 coder result.md schema

```markdown
# <agent-key>：<阶段标题或修复任务>

## 上下文
- specId: <id>
- spec_dir: <abs>
- 当前 group / stage / round: ...

## 子任务状态
- 2.1 user model: done — src/models/user.py
- 2.2 user service: failed — ImportError, 缺 deps

## 关键变更
- ...

## 给下游 reviewer 的提示（可选）
- ...

STATUS: ok | failed: <原因> | blocked: <原因>
```

主代理拿到 result.md 后 `task_swarm.py advance --phase coding`，脚本解析 STATUS 行 + 子任务状态，更新 state.json。

#### 11.4.2 reviewer review.md schema

```markdown
# reviewer-g{group}-r{round}

## 结论
needs-changes | approved-with-comments | approved

## P0（必须带证据标签：[req:x.y] / [security] / [contract]）
- src/auth/service.py:34 [req:1.2] — login 失败未区分锁/密码错
- src/api/login.py:8 [security] — 缺 rate limit
（如无 P0：本节写 `(none)`）

## P1
- src/models/user.py:12 — email 字段格式校验缺失

## P2
- 命名 `auth_svc` 可改为 `auth_service`

## 给使用者的提示
- 一句话总结

STATUS: ok
```

`task_swarm.py advance --phase review` 解析逻辑：

1. 提取所有 P0 项 + 证据标签。
2. **无证据标签的 P0 自动降级为 P1**（写入 review.md 解析报告 `downgraded_p0[]`）。
3. 若降级后仍有 P0 → 下一 phase = `p0-fix`，state.json 写入 `p0_pending[]`。
4. 若无 P0 → 下一 phase = `validation`。
5. 所有 P0/P1/P2 项（含降级的）都写入 state.json `findings[]`，writeback 时落到 tasks.md（详见 §11.5）。

#### 11.4.3 validator validation.md schema

```markdown
# validator-g{group}-r{round}

## 判定
pass | fail

## 复现命令
\`\`\`bash
cd <project root>
pytest tests/test_auth.py -v
\`\`\`

## 按子任务的验证结果
- [x] 1.1 user model: pass
- [ ] 1.3 controller: fail — 5 次失败未锁账号 (_需求：1.3_)

## 失败现场（fail 时必填）
\`\`\`
FAILED tests/test_auth.py::test_lockout_after_5_failures
AssertionError: expected 423, got 401
\`\`\`

## 给 coder 的修复指引（fail 时必填，不带 P0/P1 标签）
### 修复 1 — lockout 计数器
- 文件: src/api/login.py
- 位置: login 失败分支
- 问题: 没有调用 lockout 计数器
- 建议: 引入 src/auth/lockout.py，记录失败次数，第 5 次返回 423
- 涉及需求: _需求：1.3_

STATUS: ok
```

`task_swarm.py advance --phase validation` 解析逻辑：

1. 抓"判定"行 → pass 或 fail。
2. fail → 解析"给 coder 的修复指引"→ 输出 `fix_targets[]`（按文件分组）→ 下一 phase = `v-fix`。
3. pass → 下一 phase = `writeback`。
4. **死循环检测**：比对本轮 fail 签名（测试名 + assertion 文本哈希）与上一轮，连续 3 轮相同 → state.json 标 group `failed-deadloop`，下一 phase = `error`。

### 11.5 tasks.md 写回格式（findings + status）

`task_swarm.py writeback --run <id> --group <N>` 干两件事：

1. group 内所有 stage 的 `[ ]` → `[x]`。
2. 在每个 stage 下方追加一段 `> ` 注释块，含：
 - validator 最终结论（pass 轮号 + 命令）
 - 所有 review findings（P0 含证据标签、修复状态；P1/P2 含修复状态）
 - validator 历轮简报（fail → pass 的轮次链）

示例 diff（before/after）：

```markdown
## 阶段 1: 用户认证
- [ ] 1.1 user model @writes:src/models/user.py _需求：1.1_
- [ ] 1.2 auth service @writes:src/auth/service.py _需求：1.2_
- [ ] 1.3 controller @writes:src/api/login.py _需求：1.3_
```

→

```markdown
## 阶段 1: 用户认证
- [x] 1.1 user model @writes:src/models/user.py _需求：1.1_
- [x] 1.2 auth service @writes:src/auth/service.py _需求：1.2_
- [x] 1.3 controller @writes:src/api/login.py _需求：1.3_

> ✅ validator g1-r2 pass: `pytest tests/test_auth.py -v`
>
> 评审建议（task-swarm reviewer-g1-r1）：
> - [P0 已修复] src/auth/service.py:34 [req:1.2] — login 失败未区分锁/密码错
> - [P0 已修复] src/api/login.py:8 [security] — 缺 rate limit
> - [P0 未修复] src/api/login.py:22 [contract] — token vs session_id 不一致（fix 失败：ImportError）
> - [P1 未修复] src/models/user.py:12 — email 字段格式校验缺失
> - [P2 未修复] 命名 `auth_svc` → `auth_service`
> - [adv 未修复] src/auth/service.py:50 — error wrapping 风格（无证据标签，自动降级）
>
> validator 历轮：
> - g1-r1: fail — test_lockout_after_5_failures 不通过
> - g1-r2: pass
```

**修复状态标签规则**：

| 标签 | 含义 |
|---|---|
| `[P0 已修复]` | 带证据标签的 P0 + p0-fix 阶段 coder STATUS: ok |
| `[P0 未修复]` | 带证据标签的 P0 + p0-fix coder failed / 主代理选择跳过 |
| `[P1 未修复]` / `[P2 未修复]` | reviewer 列出但默认不修；状态默认为"未修复" |
| `[adv 未修复]` | reviewer 列为 P0 但未带证据标签，被自动降级；状态恒为"未修复"（不进入 fix loop） |
| `[P1 已修复]` / `[P2 已修复]` | 仅当主代理显式选择 fork coder 修 P1/P2 时出现（v0.7 不默认；可作为 v0.8 扩展） |

writeback 严格 line-safe：禁止改动 stage 标题、`@writes` / `@reads` / `_需求：x.y_` 等任何已有内容；只允许 checkbox 字符 toggle + 新增 `> ` 行。任何越界 diff 让 writeback `exit 1` 报错，主代理不能继续。

### 11.6 `on-task-completed` hook 提醒矩阵

`PostToolUse` matcher=`Task` 每次 subagent 返回都触发。hook 读 stdin payload + 调 `task_swarm.py plan --run <id>`，按当前 state.json 输出对应 `additionalContext`：

| 当前 state | 注入文本（要点） |
|---|---|
| coding 进行中，仍有 coder 未返回 | "coding phase 还在等 N 个 subagent，无需 fork 新 agent；等齐后再判断。" |
| coding 全部返回 | "本 group coder 已全部返回。请 fork **1 个** `task-swarm-reviewer`，prompt 见 `.task-swarm/runs/<id>/agents/reviewer-g{g}-r1/task.md`。" |
| review 返回，含带证据 P0 | "reviewer 提了 N 个带证据 P0。请按 P0 涉及文件 fork M 个 `task-swarm-coder`（p0-fix），prompt 已生成。提醒：reviewer 修复**只触发一次**，不 re-review。" |
| review 返回，无 P0（或全降级） | "reviewer 无带证据 P0。请 fork **1 个** `task-swarm-validator`，prompt 已生成。" |
| p0-fix 全部返回 | "p0-fix coder 已返回。请 fork **1 个** `task-swarm-validator`。" |
| validation 返回 pass | "validator pass。请调 `task_swarm.py writeback --run <id> --group <g>` 回写 tasks.md，然后进入下一 group。" |
| validation 返回 fail | "validator fail。请按 validation.md 的 fix_targets 各文件 fork **N 个** `task-swarm-coder`（v-fix）。注意：validator fail 循环修复直到 pass。本轮是 g{g}-r{r}。" |
| v-fix 全部返回 | "v-fix coder 已返回。请 fork **1 个** `task-swarm-validator` 验证。" |
| v-fix 已连续 3 轮同 fail 签名 | "⚠️ 死循环检测：g{g} 已连续 3 轮同一 fail。建议停止本 group，向用户报告 `failed-deadloop`，让用户介入。" |
| 所有 group 完成 | "全部 group 已完成。请按 SKILL.md 退出 task-swarm 模式，回到 spec-mode acceptance phase。" |

所有提醒**末尾固定加**："本提醒仅供参考；fork 谁、是否 fork、何时 writeback 仍由你判断。"——保留主代理决策权。

### 11.7 信息流总览

```
主代理（spec-mode 主会话，持锁）
 │
 ├─[调]── task_swarm.py init ─────────────► state.json (groups, stages)
 │ ▲
 │ ┌──────────────────────────────────────┘
 │ │
 ├─[读]── task_swarm.py plan ──► 当前应 fork 的 subagent 列表
 │
 ├─[fork]── Task(coder1) ─┐
 │ [fork]── Task(coder2)─┼─► （并发执行）
 │ [fork]── Task(coderN)─┘
 │ ┌─► 各自写 outbox/result.md
 │ ←─── PostToolUse hook 注入（每返回一个）
 │
 │── Bash: cat agents/coder-*/outbox/result.md
 │
 ├─[调]── task_swarm.py advance --phase coding ──► state.json 更新
 │ + 下一步建议
 │
 ├─[fork]── Task(reviewer) ─► outbox/review.md
 │ ←─── PostToolUse hook 提醒"该 p0-fix 或 validator"
 │
 ├─[调]── task_swarm.py advance --phase review ──► state.json + p0_pending[]
 │
 ├─[fork]── Task(coder p0-fix x M) ─► outbox/result.md ...
 │
 ├─[fork]── Task(validator) ─► outbox/validation.md
 │ ←─── PostToolUse hook 提醒"pass→writeback 或 fail→v-fix"
 │
 ├─[调]── task_swarm.py advance --phase validation
 │
 │ if fail:
 │ ├─[fork]── Task(coder v-fix x M) ─► outbox/...
 │ └─ loop 回 validator
 │
 │ if pass:
 │ └─[调]── task_swarm.py writeback --run <id> --group <g>
 │ ─► tasks.md 行级安全更新
 │
 └─ 进入下一 group / 全部完成 → 退出 task-swarm 模式
```

**关键不变量**：

1. 主代理是**唯一**持有 spec 锁的实体；subagent 不动锁。
2. 所有跨进程信息走文件系统（outbox/result.md / review.md / validation.md + state.json）。不依赖 prompt 之间 in-memory 传递。
3. `state.json` 是唯一事实源；主代理状态丢了可以从 `state.json` + outbox 文件完全恢复（v0.7 不实现 resume，但数据结构为之留路）。
4. hook 只读、只提醒——任何"该做什么"由主代理决定。hook 失效时 task-swarm 仍可跑（主代理按 SKILL.md / commands/specode:task-swarm.md 协议自行推进），只是少一份保险。
