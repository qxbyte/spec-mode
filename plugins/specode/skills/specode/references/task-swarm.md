# task-swarm 协议参考（references/task-swarm.md）

本文档是 `/specode:task-swarm` 命令背后的完整协议。
主代理在 task-swarm run 期间必须严格按本协议工作。

---

## 1. 角色与并发度

| 角色 | 是否并发 | 工具白名单（物理隔离） | 何时被 fork |
|---|---|---|---|
| `task-swarm-coder` | **多实例并行** | `Bash, Read, Edit, Write, Grep, Glob` | coding / p0-fix / v-fix 各 phase |
| `task-swarm-reviewer` | **单实例** | `Bash, Read, Grep, Glob`（无 Edit/Write） | review phase（每 review-round 一次） |
| `task-swarm-validator` | **单实例** | `Bash, Read, Grep, Glob`（无 Edit/Write） | validation phase（每 validation-round 一次） |
| `task-swarm-planner` | 视情况 | `Bash, Read, Grep, Glob, Write` | 可选：tasks.md 不够细时 |

reviewer / validator 单实例的理由：
- reviewer = 一个上帝视角的读代码人，要对全部 coder 产物有整体判断；切成多份会破坏交叉关联检测。
- validator = 跑测试的客观信号，并发跑没意义；同一测试套件单进程跑一次就够。
- coder = 并行收益最大（多 stage 互不干扰时各占一份文件）。

---

## 2. 文件冲突避免

`task_swarm.py init` 解析 tasks.md 时按以下规则把 stage 切成 group：

1. 提取每个 stage 的 `@writes:<files>` 列表（含通配符展开）。
2. 在同一 group 内：任意两个 stage 的 @writes 集合**不相交**且**无 @depends-on 关系**。
3. 跨 group：上一 group 全部 pass 后才能开始下一 group。
4. 一个 stage 即使可以并发也不会被拆——以"stage = coder 任务粒度的最大单元"为铁律。

主代理在 coding phase **同一 message 内**发出多个 Task block（每个对应当前 group 的一个 stage），由宿主并行执行。

**强约束**：派发 coder 时，必须先调 `task_swarm.py plan` 拿当前 group 的 stage 列表，**逐字拷贝**到 Task block——绝不可凭印象自己派；脚本已经处理过文件冲突分组。

---

## 3. Phase 状态机

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
- "整个 group 一起 coding → 一次 reviewer → 一次 validator"，reviewer / validator 看的是 group 范围。
- reviewer P0 → coder 修复**只触发一次**（修完不再 re-review，直接进 validation）。
- validator fail → coder 修复**循环**到 pass。
- 死循环检测：v-fix → validation 连续 3 轮同一 fail 签名（测试名 + assertion 哈希）→ 整个 group failed。

---

## 4. 子代理产物 schema

每个子代理 fork 时主代理把 prompt 文件预渲染到：

```
.task-swarm/runs/<run_id>/agents/<agent-key>/task.md
```

产物路径：

```
.task-swarm/runs/<run_id>/agents/<agent-key>/outbox/
 result.md ← coder
 review.md ← reviewer
 validation.md ← validator
```

`agent-key` 命名约定：
- coder：`coder-g{group}-s{stage}-r{round}`
- p0-fix coder：`coder-p0fix-g{group}-r{round}-f{file-idx}`
- v-fix coder：`coder-vfix-g{group}-r{round}-f{file-idx}`
- reviewer：`reviewer-g{group}-r{round}`
- validator：`validator-g{group}-r{round}`

### 4.1 coder result.md schema

```markdown
# <agent-key>：<阶段标题或修复任务>

## 上下文
- specId / spec_dir / group / stage / round

## 子任务状态
- 2.1 user model: done — src/models/user.py
- 2.2 user service: failed — ImportError, 缺 deps

## 关键变更
- ...

## 给下游 reviewer 的提示（可选）
- ...

STATUS: ok | failed: <原因> | blocked: <原因>
```

### 4.2 reviewer review.md schema

```markdown
# reviewer-g{group}-r{round}

## 结论
needs-changes | approved-with-comments | approved

## P0（必须带证据标签：[req:x.y] / [security] / [contract]）
- src/auth/service.py:34 [req:1.2] — login 失败未区分锁/密码错
（如无 P0：本节写 `(none)`）

## P1
- src/models/user.py:12 — email 字段格式校验缺失

## P2
- 命名 `auth_svc` 可改为 `auth_service`

## 给使用者的提示
- 一句话总结

STATUS: ok
```

**advance --phase review 解析**：
1. 提取所有 P0 项 + 证据标签。
2. **无证据标签的 P0 自动降级为 advisory**。
3. 若降级后仍有 P0 → 下一 phase = `p0-fix`，state.json 写 `p0_pending[]`。
4. 若无 P0 → 下一 phase = `validation`。
5. 所有 P0/P1/P2 项（含降级的）都写入 `findings[]`，writeback 时落到 tasks.md。

### 4.3 validator validation.md schema

```markdown
# validator-g{group}-r{round}

## 判定
pass | fail

## 复现命令
` ` `bash
cd <project root>
pytest tests/test_auth.py -v
` ` `

## 按子任务的验证结果
- [x] 1.1 user model: pass
- [ ] 1.3 controller: fail — 5 次失败未锁账号 (_需求：1.3_)

## 失败现场（fail 时必填）
` ` `
FAILED tests/test_auth.py::test_lockout_after_5_failures
AssertionError: expected 423, got 401
` ` `

## 给 coder 的修复指引（fail 时必填，不带 P0/P1 标签）
### 修复 1 — lockout 计数器
- 文件: src/api/login.py
- 位置: login 失败分支
- 问题: 没有调用 lockout 计数器
- 建议: 引入 src/auth/lockout.py，记录失败次数，第 5 次返回 423
- _需求：1.3_

STATUS: ok
```

**advance --phase validation 解析**：
1. 抓"判定"行 → pass 或 fail。
2. fail → 解析"给 coder 的修复指引"→ 输出 `fix_targets[]`（按文件分组）→ 下一 phase = `v-fix`。
3. pass → 下一 phase = `writeback`。
4. **死循环检测**：比对本轮 fail 签名（测试名 + assertion 文本哈希）与上一轮，连续 3 轮相同 → state.json 标 group `failed-deadloop`。

---

## 5. tasks.md 写回格式

`task_swarm.py writeback --run <id> --group <N>` 干两件事：

1. group 内所有 stage 的 `[ ]` → `[x]`。
2. 在每个 group 最后一个 stage 下方追加一段 `> ` 注释块，含：
 - validator 最终结论（pass 轮号 + 命令）
 - 所有 review findings（P0 含证据标签、修复状态；P1/P2 含修复状态）
 - validator 历轮简报（fail → pass 的轮次链）

writeback 严格 line-safe：禁止改动 stage 标题、`@writes` / `@reads` / `_需求：x.y_` 等任何已有内容；只允许 checkbox toggle + 新增 `> ` 行。任何越界 diff 让 writeback `exit 1` 报错，主代理不能继续。

### 5.1 修复状态标签

| 标签 | 含义 |
|---|---|
| `[P0 已修复]` | 带证据标签的 P0 + p0-fix 阶段 coder STATUS: ok |
| `[P0 未修复]` | 带证据标签的 P0 + p0-fix coder failed / 主代理选择跳过 |
| `[P1 未修复]` / `[P2 未修复]` | reviewer 列出但默认不修；状态默认为"未修复" |
| `[adv 未修复]` | reviewer 列为 P0 但未带证据标签，被自动降级 |

---

## 6. on-task-completed hook 提醒矩阵

`PostToolUse` matcher=`Task` 每次 subagent 返回都触发。hook 读 sessions/<id>.json 看是否在 run 中 → 调 `task_swarm.py plan --run <id>` 拿提示。

| 当前 state | 注入文本要点 |
|---|---|
| coding 进行中，仍有 coder 未返回 | "coding phase 还在等 N 个 subagent，无需 fork 新 agent；等齐后再判断。" |
| coding 全部返回 | "本 group coder 已全部返回。请 fork **1 个** `task-swarm-reviewer`。" |
| review 返回，含带证据 P0 | "reviewer 提了 N 个带证据 P0。请按 P0 涉及文件 fork M 个 `task-swarm-coder`（p0-fix）。提醒：reviewer 修复**只触发一次**，不 re-review。" |
| review 返回，无 P0（或全降级） | "reviewer 无带证据 P0。请 fork **1 个** `task-swarm-validator`。" |
| p0-fix 全部返回 | "p0-fix coder 已返回。请 fork **1 个** `task-swarm-validator`。" |
| validation 返回 pass | "validator pass。请调 `task_swarm.py writeback` 回写 tasks.md，然后进入下一 group。" |
| validation 返回 fail | "validator fail。请按 validation.md 的 fix_targets 各文件 fork **N 个** `task-swarm-coder`（v-fix）。" |
| v-fix 全部返回 | "v-fix coder 已返回。请 fork **1 个** `task-swarm-validator` 验证。" |
| v-fix 已连续 3 轮同 fail 签名 | "⚠️ 死循环检测：g{g} 已连续 3 轮同一 fail。建议停止本 group，向用户报告 `failed-deadloop`。" |
| 所有 group 完成 | "全部 group 已完成。请按 SKILL.md 退出 task-swarm 模式，回到 spec-mode acceptance phase。" |

所有提醒**末尾固定加**："本提醒仅供参考；fork 谁、是否 fork、何时 writeback 仍由你判断；可忽略。"

---

## 7. 信息流总览

```
主代理（spec-mode 主会话，持锁）
 │
 ├─[调]── task_swarm.py init ─────────────► state.json (groups, stages)
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
 ├─[调]── task_swarm.py advance --phase coding ──► state.json 更新
 │
 ├─[fork]── Task(reviewer) ─► outbox/review.md
 ├─[调]── task_swarm.py advance --phase review ──► state.json + p0_pending[]
 ├─[fork]── Task(coder p0-fix x M) ─► outbox/result.md ...
 ├─[fork]── Task(validator) ─► outbox/validation.md
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
2. 所有跨进程信息走文件系统（outbox + state.json）。
3. `state.json` 是唯一事实源；主代理状态丢了可以从 `state.json` + outbox 文件完全恢复（resume 暂未实现，但数据结构已为之留路）。
4. hook 只读、只提醒——任何"该做什么"由主代理决定。

---

## 8. 死循环保护规则

- 连续 3 轮 v-fix → validation 出现**完全相同**的 fail 签名（测试名 + assertion 文本哈希）→ 整个 group 标 `failed-deadloop`。
- state.json 不再推进；主代理向用户报告并退出 task-swarm。
- writeback 该 group 时注释块会写明"failed-deadloop（连续 3 轮同一 fail 签名）"。
- 用户介入后可：手改源码 → 重跑 `/specode:task-swarm`；或调 `task_swarm.py resolve --run <id> --abort` 中止。

---

## 9. CLI 接口速查

```text
task_swarm.py init --tasks <abs> [--max-parallel N] [--max-rounds N]
 [--session <session_id>] [--spec <abs>]
 → {"run_id", "groups": [...], "spec_dir": ...}

task_swarm.py status --run <run_id>
 → 当前 phase / group / round / 待派 subagent 列表

task_swarm.py plan --run <run_id>
 → 下一步该 fork 哪些 subagent 的 JSON（不改 state）

task_swarm.py advance --run <run_id> --phase <coding|review|p0-fix|validation|v-fix>
 --round <n>
 → 解析 outbox、更新 state.json、返回下一步建议

task_swarm.py writeback --run <run_id> --group <N>
 → 当前 group 全部 pass 后回写 tasks.md（line-safe diff）

task_swarm.py heartbeat --run <run_id>
 → 刷新 state.json.last_activity_at（spec 锁需主代理另调 spec_session.py）

task_swarm.py resolve --run <run_id> [--abort]
 → 标记完成或中止；清理 sessions.task_swarm_run_id
```
