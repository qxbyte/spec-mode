# Task-Swarm 设计规约（CLI 编排 + 多角色 agent 物理隔离）

specode "任务执行" selector 的第三个选项 `用 task-swarm 多 agent 并发`。

> ⚠️ 本文档不是运行时手册。运行时模型只看 `commands/task-swarm.md` 里的 7 步 CLI 协议。本文档是**设计规约**：为什么这样设计、状态机怎么运转、铁律如何兜底——给读代码、修脚本、扩展功能的人看。

## 它解决什么

specode 默认的 §7 Task Execution 是**单 agent 顺序执行**：主会话一个一个跑任务、自己写代码、自己跑验证、自己打 `[x]`。等于让同一个 LLM 上下文自我背书——这是"自我认可"问题。

task-swarm 模式把任务派发给**不同角色的独立子 agent**：

- **coder** 只写代码，没有评审能力
- **reviewer** 只评审，**工具层面拿不到 Edit/Write**——想改代码也改不了
- **validator** 只验收，同样**没有 Edit/Write**，必须用真实命令证明结论
- **planner** 只拆任务（一般 specode 已经在 tasks 阶段拆好，planner 备用）

子 agent 之间**无共享上下文**，只能通过 `outbox → inbox` 文件交换信息。
这是工具+上下文的双重物理隔离。

## 总体架构（控制反转）

旧设计：**主模型**理解全部状态机，自己解析 tasks.md / outbox，自己 Edit tasks.md。问题——长上下文里轮号心算容易乱、subagent 输出格式漂移就误判、回写时容易动到 traceability。

新设计：**脚本**持有所有确定性逻辑，**主模型只负责派单与文本生成**：

| 决策点 | 由谁负责 |
|---|---|
| 解析 tasks.md → 派发计划 | `task_swarm_parse_md.py` |
| 状态机推进（轮号、收敛、死循环） | `task_swarm_state.py` |
| 解析 result.md / review.md / validation.md | `task_swarm_outbox.py` |
| 渲染 subagent prompt（含 @writes、修复指引） | `task_swarm_prompt.py` |
| 回写 tasks.md（行级安全 Edit） | `task_swarm_writeback.py` |
| 综合调度 + JSON 指令 | `task_swarm.py` CLI |
| 工具层兜底（INV-7/8/9） | `task_swarm_guard.py` + `spec_guard.py` |

主模型只跑：`init → loop(next → fork → parse → advance → ...) → writeback → done`。

## CLI 协议（`commands/task-swarm.md` 的实现细节）

七个子命令：

| 子命令 | 输入 | 输出 |
|---|---|---|
| `init --tasks <path>` | tasks.md 路径 | run_id + 初始派发计划 |
| `next --run <id>` | run_id | `{"action": "fork|writeback|wait|done", ...}` |
| `parse --run <id> --stage N --role R --round K` | outbox 文件 | `{"judgment": "...", ...}`（含 schema-error 兜底） |
| `advance --run <id> ... --judgment J` | 判定 | 推进 state.json |
| `writeback --run <id> --stage N` | 收敛状态 | 安全 Edit tasks.md |
| `status --run <id>` | — | 人话状态汇报 |
| `resolve --run <id>` | — | run_dir 路径 |

主模型必须按 next 返回的指令字段去 fork——`subagent_type`、`prompt_file`、`workspace` 都由脚本提供。

## 角色到 subagent 的映射

| @role | subagent_type | 职责 | 工具 |
| --- | --- | --- | --- |
| `coder` | `specode:task-swarm-coder` | 写 / 改业务代码，按子任务清单顺序完成阶段下所有叶子；修复轮按 validation.md 的失败指引定向修补 | Bash, Read, Edit, Write, Grep, Glob |
| `reviewer` | `specode:task-swarm-reviewer` | 评审上游 coder 的产出，输出 P0/P1/P2 分级建议（**advisory 模式**：不参与修复循环，不阻塞推进；产出会作为 `> ⚠️` 注释写入 tasks.md 供使用者审阅） | Bash, Read, Grep, Glob **(无 Edit/Write)** |
| `validator` | `specode:task-swarm-validator` | 跑测试 / lint / 端到端检查，给 pass/fail 判定；fail 时**必须**输出"给 coder 的修复指引"（validator 是阻塞门，coder ↔ validator 形成修复循环） | Bash, Read, Grep, Glob **(无 Edit/Write)** |
| `planner` | `specode:task-swarm-planner` | 把粗粒度需求拆成 task-swarm 风格的 tasks.md | Bash, Read, Grep, Glob, Write |

reviewer 和 validator 故意没有 Edit/Write —— 这是工具层面的物理隔离。

## 按一级阶段聚合派发

specode tasks.md 的天然层级：

```markdown
- [ ] 1. 实现登录流程           ← 一级阶段
  - [ ] 1.1 写 user model        ← 叶子任务
    - 文件：`src/models/user.py`
    - _需求：1.1_
- [ ] 2. 检查点 — 跑通登录流程   ← specode 内置 validator 任务
```

派发规则（由 `task_swarm_parse_md.py` + `task_swarm_state.py` 实现）：

| 角色 | 派发粒度 | 数量 |
| --- | --- | --- |
| **coder** | 每个一级阶段一个（包揽阶段下所有叶子） | = 阶段数 |
| **reviewer** | 每个一级阶段一个 | = 阶段数 |
| **validator** | 复用 specode 的"检查点"任务 | = 检查点数 |

并发判定：**互不冲突**（"文件:" 行的并集不相交）的阶段可以并发，受 `--parallel N` 约束（默认 3）。冲突或依赖未满足 → `next` 返回 `wait`。

### 子任务标签（`@swarm:`）

| 标签 | 行为 |
| --- | --- |
| `@swarm:full` | 单独走 coder+reviewer+validator |
| `@swarm:coder-only` | 只 coder |
| `@swarm:skip` | 完全跳过 |
| 无标签 | 默认按阶段聚合 |

启发式默认（无标签时）：
- `[*]` 可选任务 → 自动 coder-only
- 无 `_需求：` traceability → 自动 coder-only

冲突时优先级（高→低）：`skip > full > coder-only > 启发式`。
解析器在冲突时**不报错**，仅在 `warnings` 数组里留 `[INFO]` / `[WARN]` 行，可用 `init` 输出查看。

### 标签命名空间（避免混淆）

task-swarm 涉及**两套独立的标签命名空间**，二者作用完全不同、不能互换：

| 命名空间 | 出现位置 | 用途 | 由谁解析 |
|---|---|---|---|
| `@swarm:<word>` | tasks.md 的 **leaf 标题或子项行** | 控制派发策略（`full` / `coder-only` / `skip`） | `task_swarm_parse_md._arbitrate_tags` |
| `[<word>]` | reviewer 的 **review.md P0 行** | 标注 P0 证据来源（`req:x.y` / `security` / `contract`） | `task_swarm_outbox.parse_review` |

二者**互不相干**——`@swarm:` 决定一个叶子任务是否参与评审/验收循环；`[req:...]` 决定 reviewer 提的 P0 是否阻塞 coder 修复轮。不要在同一行混用、也不要把 `@swarm:` 写到 review.md 或把 `[req:...]` 写到 tasks.md。

## 状态机

每个 stage 的生命周期：

```
pending → running → converged ✔
                 └→ failed ✗
                 └→ skipped (全部 leaf 是 @swarm:skip)
```

`task_swarm_state.next_action()` 决定下一步（**R3 重构后**：reviewer 退出循环，coder ↔ validator 是唯一阻塞循环）：

```
pending stage:
  └ kind=checkpoint → fork validator r1
  └ kind=stage      → fork coder r1

running stage, last action was:
  coder ok:
    └ kind=checkpoint → validator (re-run，validator 自己的轮号)
    └ kind=stage, has full/default leaves → reviewer (advisory)
    └ kind=stage, all coder-only → converge

  reviewer (任何 judgment) → converge（**advisory，不进循环**；
     P0 / advisory_p0 摘要会被 writeback 写到 tasks.md 注释里给使用者看）

  validator pass → converge

  validator fail:
    └ round >= validator_rounds → fail
    └ else → coder (validator-fail-fix scope) → validator re-run

  validator loop / schema-error → fail
```

修复循环上限：

- **validator 默认 3 轮**（`--validator-rounds N`）—— validator 是跑代码下结论，测试 fail 是客观信号，给足修复机会
- `--max-rounds N` 作为 fallback 默认
- `--reviewer-rounds` 已弃用（reviewer 不再参与循环）。参数保留仅为兼容旧脚本

### reviewer P0 证据标签（advisory 分级）

reviewer 输出 P0 时必须带证据标签之一，否则 `task_swarm_outbox.parse_review` 会把它分类为 `advisory_p0`（仍写入 tasks.md 注释、但以 `(adv)` 前缀标记）：

| 标签 | 含义 |
|---|---|
| `[req:x.y]` | 直接违反某条 `_需求：x.y_` 的 SHALL |
| `[security]` | 安全 / 数据完整性问题 |
| `[contract]` | 接口契约不一致（上下游对返回类型/字段名理解不同） |

设计意图：reviewer 的所有担忧都会作为 `> ⚠️ 评审建议` 注释写入 tasks.md，**带证据标签的 P0** 以醒目形式呈现，**无标签的 advisory** 以 `(adv)` 前缀呈现。使用者一眼区分"客观依据"与"风格意见"，决定是否人工开新 spec 跟进。**所有 P0 / advisory 都不触发 coder 重派**——reviewer 是 advisory，不参与循环。

## 死循环识别（成本控制）

validator prompt 强制要求：若**本轮**的 fail 项与上轮 inbox 的 prev-validation.md 完全一致，在文件**顶部**加 `## 进入死循环风险` 节。`task_swarm_outbox.parse_validation` 检测到该节立即把 judgment 升级为 `loop`，主编排器收到 loop 后立刻标 stage failed。

reviewer 由于不参与循环，死循环识别**对 reviewer 不再适用**（reviewer 只跑一次）。

## 三检写守（具体落地）

`writeback` 子命令内部自动执行：

1. `spec_session.verify_and_heartbeat(spec_dir, session_id)` — INV-3 lock check + 续锁（单次调用）
2. 调用 `task_swarm_writeback.apply_writeback()` 做行级安全 Edit + 追加 reviewer advisory 注释
3. 通过 `diff_safe_line_by_line` 二次确认 diff 只包含 checkbox 切换 + `> ` 注释
4. verify-lock 异常时把详细信息放进 JSON `warnings` 字段透出给主编排器

主编排器**不应**直接 Edit tasks.md——INV-9 hook 会拦下任何非 checkbox / 非注释的改动。

## subagent 工作目录布局

```
.task-swarm/
  active-run                          # 当前 run_id pointer（hook 用）
  runs/
    20260517-153012-ab12cd/
      state.json                      # 状态机持久化
      agents/
        stage-1-coder/                # 普通 stage 初轮
          task.md                     # 预渲染的 subagent prompt
          inbox/                      # 上游产物（脚本中继过来）
          outbox/result.md
        stage-1-reviewer/             # 普通 stage 唯一一次 reviewer (advisory)
          inbox/  ← coder outbox 自动 cp
          outbox/review.md
        stage-2-validator/            # checkpoint 初验
        stage-2-coder-r2/             # checkpoint validator-fail-fix
          inbox/
            prev-result.md
            validation.md
          outbox/result.md
        stage-2-validator-r2/         # checkpoint 复验
          inbox/
            prev-validation.md
            upstream-result.md
          outbox/validation.md
```

后缀规则：
- 无后缀 = 初轮
- `-r2`、`-r3` = 第 N 轮
- **reviewer 没有 `-rN` 工作区**（advisory 模式只跑一次）
- coder 与 validator 各有自己的轮号空间（互不串号），由 `task_swarm_state.stage["rounds"]` 跟踪

## 与 specode 铁律的兜底关系

| specode 铁律 | task-swarm 兜底机制 |
| --- | --- |
| Document-first | `writeback` 子命令在每阶段收敛后回写 tasks.md |
| Post-`/continue` sync | UserPromptSubmit 注入"current step"提示，模型不会遗忘上下文 |
| INV-3 Write-before-verify-lock | `writeback` 内部强制 `verify-lock + heartbeat` |
| Phase gate (INV-6) | task-swarm 只在 implementation phase 被调起 |
| Forced writes | `writeback` 失败立即 abort，不在内存累积 |
| INV-1 (源文件 = tasks.md 列出) | subagent 工作区内的产物自动归类为 "swarm 内部"，不走 INV-1；业务代码仍走 INV-1 |
| INV-2 (改源码必须同 turn 改 spec) | `writeback` 每阶段写 tasks.md，自动满足 |

新增铁律（仅 task-swarm 期间生效）：

| 铁律 | 内容 |
| --- | --- |
| **INV-7** | `Task` 调用 `subagent_type` 必须带 `specode:task-swarm-` 前缀，否则 hook deny |
| **INV-8** | subagent 写边界——只能写自己 task.md 中 `@writes` 列出的文件或自己 outbox/；越界（包括 spec 文档）一律 hook deny |
| **INV-9** | task-swarm 运行期编辑 tasks.md 必须走 `writeback` 子命令；直接 Edit 时 hook 校验 diff，只放行 checkbox 切换 + `> ` 注释，其余 deny |
| **INV-10** | subagent outbox 必须通过 schema 校验（必需节、STATUS 行、判定字段）；由 `task_swarm.py parse` **CLI 子命令兜底**（非 Stop hook，因为 subagent Stop 不在父会话 hook 拦截范围内）：parse 返回 `judgment=schema-error` 时同时**清空 outbox** 与 **重置 in_flight**，并在 JSON 里附 `retry: true` + `outbox_snapshot`，主编排器照原 stage/role/round 重派 subagent，prompt 不变 |

## 调试

| 想看什么 | 命令 |
|---|---|
| run 全貌 | `task_swarm.py status --run <id>` |
| subagent 拿到的 prompt | `cat .task-swarm/runs/<id>/agents/<stage>/task.md` |
| subagent 产出 | `cat .task-swarm/runs/<id>/agents/<stage>/outbox/*` |
| 历史轮 | `ls .task-swarm/runs/<id>/agents/stage-3-*` |
| 清理 | `rm -rf .task-swarm/runs/<id>` |

## 关键原则（写给将来扩展功能的人）

1. **不要把决策逻辑挪回 prompt**——任何"状态机"或"格式解析"应该新增 Python 函数 + 单测，不要改 references 文档让模型猜。
2. **每条新增铁律都要有 hook 兜底**——prompt-only 约束等于没有约束。
3. **outbox schema 是接口**——改 schema 要同步改 `task_swarm_outbox.py` + 三个 agent.md + INV-10 hook。
4. **state.json 是持久化的**——schema 变更要带迁移逻辑或版本号 bump。
5. **subagent prompt 由脚本渲染**——不要让主编排器自己拼 prompt。新增字段先加到 `StageContext`，再改 `render_*_prompt`。

## 完整示例

`references/task-swarm-example.md` —— 一份完整的 specode 风格 tasks.md 样本（5 阶段 / 5 子任务 / 7 个 subagent）。

## 用户怎么用

### 方式 1：从 specode selector 触发（推荐）
走正常 specode 流程到 tasks 确认后，在"任务执行"selector 选择 `用 task-swarm 多 agent 并发`。

### 方式 2：手动触发
```
/specode:task-swarm <spec-dir>/tasks.md
```
