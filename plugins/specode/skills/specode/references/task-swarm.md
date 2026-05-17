# Task-Swarm Mode（多角色 agent 并发执行）

specode "任务执行" selector 的第三个选项 `用 task-swarm 多 agent 并发`。
本文档是 task-swarm 模式的**单一权威**：编排步骤、subagent 协议、回写规则、铁律协调全部在这里。

## 它解决什么

specode 默认的 §7 Task Execution 是**单 agent 顺序执行**：主会话一个一个跑任务、自己写代码、自己跑验证、自己打 `[x]`。
等于让同一个 LLM 上下文自己给自己背书——这是"自我认可"问题。

task-swarm 模式把任务派发给**不同角色的独立子 agent**：

- **coder** 只写代码，没有评审能力
- **reviewer** 只评审，**工具层面拿不到 Edit/Write**——想改代码也改不了
- **validator** 只验收，同样**没有 Edit/Write**，必须用真实命令证明结论
- **planner** 只拆任务（一般 specode 已经在 tasks 阶段拆好，planner 备用）

子 agent 之间**无共享上下文**，只能通过 `outbox → inbox` 文件交换信息。
这是工具+上下文的双重物理隔离。

## 触发前提

1. 当前 phase 是 `tasks` 或 `implementation`
2. 当前 session 持有 spec lock（`verify-lock` 返回 `ok`）
3. plugin 安装时 `plugins/specode/agents/task-swarm-*.md` 已自动注册为 Claude Code subagent

任一条件不满足 → 温柔降级到第一项 `开始 required tasks`，并告诉用户原因。

## 角色到 subagent 的映射

| @role | subagent_type | 它能用的工具 |
| --- | --- | --- |
| `coder` | `task-swarm-coder` | Bash, Read, Edit, Write, Grep, Glob |
| `reviewer` | `task-swarm-reviewer` | Bash, Read, Grep, Glob **(无 Edit/Write！)** |
| `validator` | `task-swarm-validator` | Bash, Read, Grep, Glob **(无 Edit/Write！)** |
| `planner` | `task-swarm-planner` | Bash, Read, Grep, Glob, Write |

**reviewer 和 validator 故意没有 Edit/Write** —— 这是工具层面的物理隔离，让"想改也改不了"。

## 按一级阶段聚合派发（核心设计）

specode tasks.md 的天然层级：

```markdown
- [ ] 1. 实现登录流程           ← 一级阶段
  - [ ] 1.1 写 user model        ← 叶子任务
    - 文件：`src/models/user.py`
    - 验证：`pytest tests/test_user.py`
    - _需求：1.1_
  - [ ] 1.2 写 auth service
    - 文件：`src/auth/service.py`
    - _需求：1.2_
- [ ] 2. 检查点 — 跑通登录流程   ← specode 内置 validator 任务
- [ ] 3. 实现登出
  - [ ] 3.1 ...
- [*] 4. 优化（可选）            ← `[*]` 可选标记
```

派发规则：

| 角色 | 派发粒度 | 数量 |
| --- | --- | --- |
| **coder** | 每个一级阶段 **一个**（包揽阶段下所有叶子任务） | = 阶段数 |
| **reviewer** | 每个一级阶段 **一个**（评审整阶段的所有产物） | = 阶段数 |
| **validator** | 直接复用 specode 的"检查点"任务（含"检查点"字样的一级任务） | = 检查点数 |

例：5 阶段 / 5 检查点 / 20 子任务 → **15 个 subagent**，而不是 1:3 朴素展开的 60 个。

### 派发顺序

- **阶段内**：天然串行（同一个 coder 顺序做所有子任务，上下文连贯）
- **阶段间**：取该阶段下所有叶子任务的 "文件：" 并集做冲突检测，**互不冲突的阶段可并发**
- **同阶段** coder → reviewer → validator：严格串行
- "检查点"任务 = 它所跟随阶段的 validator，不重复 fork

### 子任务级控制（可选标签）

用户在 tasks.md 的叶子任务后可加 task-swarm 专属标签：

| 标签 | 行为 |
| --- | --- |
| `@swarm:full` | 该叶子单独走 coder+reviewer+validator（高风险/SHALL 涉及核心）|
| `@swarm:coder-only` | 只 coder，不纳入阶段评审 |
| `@swarm:skip` | 完全跳过，回退给主会话处理 |
| 无标签 | 默认按阶段聚合 |

启发式默认（无标签时）：
- `[*]` 可选任务 → 自动 coder-only
- 没有 `_需求：` traceability 的辅助任务 → 自动 coder-only

## 编排步骤（主会话必须按顺序执行）

### Step 1：建立 run 工作区

```bash
RUN_ID="$(date +%Y%m%d-%H%M%S)-$(openssl rand -hex 3)"
RUN_DIR="<project root>/.task-swarm/runs/$RUN_ID"
mkdir -p "$RUN_DIR/agents"
```

记住 `RUN_ID`、`RUN_DIR`。

### Step 2：解析 tasks.md

逐行扫描 tasks.md。识别：
- 一级阶段：`- [ ] N. 标题`（N 是数字）
- 叶子任务：`  - [ ] N.M 标题`（缩进 + 二级编号）
- 检查点任务：一级且标题含"检查点"
- 子任务的 `文件：...` / `验证：...` / `_需求：x.y_` 元信息行
- 可选标签：`@swarm:full|coder-only|skip`

按"一级阶段"分组，得到派发计划：

```
阶段 1: [叶子 1.1, 叶子 1.2, 叶子 1.3] → coder + reviewer
阶段 2: 检查点 → validator (依赖阶段 1)
阶段 3: [叶子 3.1, 3.2] → coder + reviewer
阶段 4: 检查点 → validator (依赖阶段 3)
阶段 5: [叶子 5.1 (optional)] → coder-only
```

### Step 3：拓扑排序 + 派发循环

派发循环：
1. 找所有依赖都已完成的阶段
2. 取这些阶段的 "文件" 并集，互不冲突的阶段可并发
3. 在同一回复里发出多个 Task 工具调用（并发派发）
4. 等任意 subagent 返回 → 处理结果（Step 5）→ 解锁下游

### Step 4：派发一个阶段（核心）

#### 4a. 准备阶段工作区

```bash
STAGE_DIR="$RUN_DIR/agents/stage-${STAGE_NUM}-coder"
mkdir -p "$STAGE_DIR/inbox" "$STAGE_DIR/outbox"

# 中继：把每个依赖阶段的 outbox 拷到本阶段 inbox
for dep_stage in $DEPENDS_ON; do
  DEP_OUTBOX="$RUN_DIR/agents/stage-${dep_stage}-coder/outbox"
  [ -d "$DEP_OUTBOX" ] && cp "$DEP_OUTBOX"/* "$STAGE_DIR/inbox/" 2>/dev/null
  DEP_REVIEW="$RUN_DIR/agents/stage-${dep_stage}-reviewer/outbox"
  [ -d "$DEP_REVIEW" ] && cp "$DEP_REVIEW"/* "$STAGE_DIR/inbox/" 2>/dev/null
done
```

#### 4b. 构造 coder prompt（包含本阶段所有子任务清单）

```
你正在 task-swarm 流程中作为 CODER 子 agent 执行 specode 的阶段 N。

# 阶段 N: <一级任务标题>

## 本阶段子任务清单（按顺序完成）
- 1.1 写 user model
  - 文件: src/models/user.py
  - 需求: 1.1
- 1.2 写 auth service
  - 文件: src/auth/service.py
  - 需求: 1.2
- 1.3 写 controller
  - 文件: src/api/login.py
  - 需求: 1.3

## 边界
- 项目根: <pwd>
- 你只能修改这些路径: src/models/user.py, src/auth/service.py, src/api/login.py
- 你的私有工作区: <STAGE_DIR 绝对路径>
- inbox（只读）: <STAGE_DIR>/inbox
- outbox（你的产出）: <STAGE_DIR>/outbox
- 参考 spec 文档（只读，绝对不要修改）: <spec-dir>/requirements.md, design.md

## inbox 内容
<ls inbox 列表>

## 输出协议
按子任务顺序完成。每完成一个，在 outbox/result.md 追加一行：
- 1.1 写 user model: done — src/models/user.py
- 1.2 写 auth service: done — src/auth/service.py
- 1.3 写 controller: failed — ImportError, 缺 src/api/__init__.py

末行: STATUS: ok 或 STATUS: failed: <原因>
绝不修改 spec-dir 内任何文件（requirements/design/tasks/config）。
```

#### 4c. 派发

```
Task(
  description="阶段 N coder: <标题简称>",
  subagent_type="task-swarm-coder",
  prompt=<上述构造的 prompt>
)
```

#### 4d. coder 结束后立刻派 reviewer

把 coder 的 outbox 中继到 reviewer 的 inbox，构造 reviewer prompt（明确告知"这是阶段批评审，按子任务分节给担忧，必须输出 P0/P1/P2 分级"），`subagent_type="task-swarm-reviewer"`。

#### 4e. **循环：reviewer P0 修复轮**（核心）

每个阶段不是跑一遍就结束，而是 `coder → reviewer → 修复 coder → 复审 reviewer → ...` 循环到 reviewer **无 P0**。

```python
# 伪代码（主编排器在脑中跑）
round = 1
MAX_ROUNDS = 3          # 默认上限，可被 /task-swarm --max-rounds N 覆盖

while True:
    review = fork_reviewer(round)         # 见 4d
    p0_count = count_p0_in(review)
    if p0_count == 0:
        break                              # ✅ 进入 4f validator
    if "进入死循环风险" in review:         # reviewer 自报"我跟上轮提的一模一样"
        mark_stage_failed("reviewer 死循环")
        return
    if round >= MAX_ROUNDS:
        mark_stage_failed(f"已 {round} 轮仍有 P0")
        return
    round += 1
    fork_coder_fix_round(round, review)    # 进入修复轮，见下
```

**派发修复轮 coder**：

```bash
# 工作区命名加 -r<round> 后缀，保留每轮历史
COD_DIR_R="$RUN_DIR/agents/stage-${STAGE_NUM}-coder-r${ROUND}"
mkdir -p "$COD_DIR_R/inbox" "$COD_DIR_R/outbox"

# inbox 必须包含：
# - prev-result.md  (上一轮 coder 的产出)
# - review.md       (这一轮 reviewer 的 P0 清单)
cp "$RUN_DIR/agents/stage-${STAGE_NUM}-coder${PREV_SUFFIX}/outbox/result.md" \
   "$COD_DIR_R/inbox/prev-result.md"
cp "$RUN_DIR/agents/stage-${STAGE_NUM}-reviewer${REV_SUFFIX}/outbox/review.md" \
   "$COD_DIR_R/inbox/review.md"
```

修复轮的 coder prompt **必须**明确告知：
- 这是修复轮第 R 轮（共最多 MAX_ROUNDS 轮）
- 只修 inbox/review.md 里的 P0 项，**不要**碰 P1/P2
- 修完每条 P0 要在 outbox/result.md 用 `- [x] <P0 摘要> — 已修复: ...` 标记
- 不要重写整个阶段，是定向补丁

复审同理：reviewer 工作区也命名 `-r<round>`，inbox 加 `prev-review.md`。

#### 4f. **循环：validator fail 修复轮**

reviewer 全清后才派 validator。validator 判 fail 也走类似循环：

```python
round = 1
while True:
    val = fork_validator(round)
    if val.judgment == "pass":
        break                                          # ✅ 阶段完成
    if "进入死循环风险" in val:
        mark_stage_failed("validator 死循环")
        return
    if round >= MAX_ROUNDS:
        mark_stage_failed(f"validator 已 {round} 轮仍 fail")
        return
    round += 1
    # validator fail 后回 coder，coder inbox 是 validation.md（含修复指引）
    fork_coder_fix_round_for_validator(round, val)
    # coder 修完后**必须**再走一遍 reviewer（轻量 review，可以是同一轮号）
    # 然后才回到 validator
    fork_reviewer_quick_check(round, ...)
```

注意：validator fail 后的 coder 修复轮**必须再过一次 reviewer**——避免 coder 改坏新地方却没人发现。reviewer 在这种"checkpoint 复审"模式下只看 validator 失败点附近的改动，不重新评全阶段。

#### 4g. 循环上限统一约定

- 默认 `MAX_ROUNDS = 3`
- `reviewer 循环` 和 `validator 循环` **各自独立计数**（即最坏情况：3 轮 reviewer P0 修复 + 3 轮 validator fail 修复 = 7 次 coder fork）
- 用户可在调用时 `--max-rounds 5` 覆盖

#### 4h. 死循环识别（成本控制）

reviewer 和 validator 在 prompt 里被要求：如果新一轮的 P0/fail 跟上一轮**完全相同**，在产物顶部加 `## 进入死循环风险` 标记。主编排器看到这个标记立即终止本阶段循环，避免无效消耗。

#### 4i. 检查点阶段单独派 validator（仍走 4f 循环）

specode 的"检查点"任务直接对应 validator。它没有先跑的 reviewer（因为整个阶段已经审过了），但 fail 时同样走 4f 的循环（fork coder 修复 → 再过一次 reviewer → 重新 validator）。

```
Task(
  description="检查点 N: <标题>",
  subagent_type="task-swarm-validator",
  prompt=<包含上游 coder 与 reviewer 全部产物的 inbox 列表 + 运行命令清单>
)
```

### Step 5：回写 tasks.md（铁律核心）

主会话**只在每个阶段循环最终收敛后**才回写 checkbox 状态。中间轮（reviewer 提 P0 / validator fail）只在子任务下追加进度注释，不改 `[ ]` 字符——避免循环跑到一半就误标 `[x]`。

#### 5a. 三检写守（specode §10）

每次写 tasks.md 之前：

```bash
python3 plugins/specode/scripts/spec_session.py verify-lock <spec-dir> --session <id>
# 返回 evicted → 立即停 swarm，告诉用户被其他窗口接管

python3 plugins/specode/scripts/spec_session.py heartbeat <spec-dir>
```

#### 5b. 中间轮回写规则（循环跑到一半时）

每轮 reviewer / validator 跑完后，主会话**只追加注释**到子任务下面，不动 checkbox：

```markdown
- [ ] 1.3 写 controller
  - 文件: src/api/login.py
  - _需求：1.3_
  > 第 1 轮 reviewer 提 P0: 缺 rate limit、login 失败分支未区分错误码
  > 第 2 轮 reviewer 提 P0: rate limit 用了非线程安全的全局变量（新引入）
```

```markdown
- [ ] 2. 检查点 — 跑通登录流程
  > 第 1 轮 validator fail: pytest test_lockout_after_5_failures 失败 (423 vs 401)
```

这样用户随时打开 tasks.md 能看到当前循环到第几轮、卡在哪。

#### 5c. 终态回写规则（阶段循环收敛后）

阶段循环结束（reviewer 0 P0 + validator pass，或达上限 failed）→ 主会话才改 checkbox：

**成功收敛**：
- 阶段下每个 done 的子任务: `[ ]` → `[x]`
- 一级阶段（所有叶子都 done）: `[ ]` → `[x]`
- 检查点任务（validator pass）: `[ ]` → `[x]`
- 把循环过程中追加的 `> 第 N 轮...` 注释**保留**（审计价值），可以再加一行 `> ✔ 第 N 轮收敛，共 R 轮迭代`

**失败收敛**：
- 子任务 `failed` 或 `[ ]` 保持，追加 `> ✗ 已达 ${MAX_ROUNDS} 轮上限仍未收敛，需要人工介入`
- 一级阶段保留 `[~]`（in-progress）或回 `[ ]`（看用户后续怎么处理）
- 检查点 fail 同样保持 `[ ]` + 追加失败摘要

#### 5d. 只动 checkbox + 注释行

无论中间轮还是终态，**禁止改**：
- `_需求：x.y_` traceability 行
- `文件：xxx` / `验证：xxx` 元信息行
- 子任务标题文字
- 嵌套缩进结构

每次回写完立刻 Write 落盘，**不在内存累积多个变更**。

### Step 6：终态汇报

所有阶段处理完后，主会话给用户（包含**每阶段循环次数**，方便发现需求 / 设计粗糙的阶段）：

```
🐝 task-swarm 已完成 spec <slug> (run: <RUN_ID>)
   max-rounds: 3  ·  默认每阶段 reviewer 循环 + validator 循环各 ≤3 轮

按阶段：
  阶段 1 实现登录:     ✔ 1 轮 reviewer · ✔ 1 轮 validator      (检查点 2)
  阶段 3 实现登出:     ✔ 2 轮 reviewer · ✗ 3 轮 validator FAIL (检查点 4)
    - 3.2 撤销 session: validator 第 3 轮仍 fail，未达 _需求：3.2_
      最后 fail: AssertionError: token still valid after logout
  阶段 5 优化（可选）: 已跳过 (@swarm:coder-only)

tasks.md 已同步：5 done / 1 in-progress (循环上限) / 1 failed
subagent 工作区: .task-swarm/runs/<RUN_ID>/

接下来：
- 看阶段 3 的 5 轮迭代历史: ls .task-swarm/runs/<RUN_ID>/agents/stage-3-*
- 人工修 3.2 后用 /spec-accept 继续验收
- 或重跑该阶段: /task-swarm --resume <RUN_ID> --stage 3
- 或 /continue 切换到其他 spec
```

随后正常进入 §8 Acceptance 阶段。

## 关键原则

1. **主会话是编排器，不是执行者** — 主会话自己不写业务代码、不做评审。所有任务必须 fork subagent。
2. **每个 fork 必须用对应 subagent_type** — `task-swarm-coder` / `-reviewer` / `-validator` / `-planner`。不要 fall back 到 `general-purpose`。
3. **subagent 之间无共享上下文** — 它们只能看自己 inbox 里的文件。主会话负责中继 outbox → inbox。
4. **subagent 绝不碰 spec 目录** — spec 文档锁仍由主会话持有，subagent 只动业务代码。
5. **每阶段回写 tasks.md 都走 verify-lock** — 防止跨窗口接管时数据撕裂。
6. **失败不要硬扛** — 上游失败级联跳过下游，给用户清晰的失败链。
7. **循环到收敛或上限才算阶段结束** — 不是 coder→reviewer→validator 跑一遍就完。reviewer 提 P0 必须回 coder 修复并复审；validator fail 必须回 coder 修复并重审 + 重新验。默认上限 3 轮，达到上限标 failed。
8. **中间轮不动 tasks.md checkbox** — 阶段循环跑到一半只追加 `> 第 N 轮...` 注释，避免误标 `[x]`。
9. **死循环识别** — reviewer / validator 自报"跟上轮一模一样的 P0/fail"时主会话立即终止本阶段循环，标 failed，省成本。

## 与 specode 铁律的逐条对照

| specode 铁律 | task-swarm 模式表现 |
| --- | --- |
| Document-first | ✅ 主会话每阶段实时回写 tasks.md checkbox |
| Post-`/continue` sync | ✅ task-swarm 期间用户若提需求变更 → 退回 specode 路由，同 turn 写文档 |
| INV-4 (测试要点 follow-mode) | ✅ task-swarm 不动 requirements/bugfix，不触发 |
| Write-before-verify-lock | ✅ 主会话每次回写前调 verify-lock |
| Phase gate compliance | ✅ task-swarm 只在"任务执行"阶段被调起 |
| Forced writes | ✅ 每个阶段回写失败立即停 swarm，不在内存累积 |
| 三检写守 (specId/boundary/lock) | ✅ 主会话回写 tasks.md 时正常守 |
| Boundary anti-contamination | ✅ subagent 工作区在 `.task-swarm/runs/`，不与 spec 目录交叉 |
| INV-1 (源文件改动 = tasks.md 列出) | ✅ subagent 改的源文件都在 tasks.md 的"文件："行声明过 |
| INV-2 (改源码必须同 turn 改 spec 文档) | ✅ 主会话每阶段回写 tasks.md，自动满足 |

## subagent 工作目录布局

每阶段的初轮目录名为 `stage-N-<role>`；修复轮加 `-r<N>` 后缀，保留每轮历史便于审计：

```
.task-swarm/
  runs/
    20260517-153012-ab12cd/
      state.json                                  # 运行状态（可选）
      orchestrator.log
      agents/
        stage-1-coder/                            # 初轮 coder
          task.md                                 # subagent 拿到的完整 prompt
          inbox/                                  # 上游产物
          outbox/
            result.md                             # 子任务状态汇总
        stage-1-reviewer/                         # 初轮 reviewer（假设 0 P0，跳过修复轮）
          task.md
          inbox/  ← 复制了 stage-1-coder/outbox
          outbox/
            review.md
        stage-1-validator/                        # 初轮 validator（pass，跳过修复轮）
          task.md
          inbox/
          outbox/
            validation.md

        stage-3-coder/                            # 阶段 3 初轮
        stage-3-reviewer/                         # 初轮 reviewer 提了 P0
          outbox/review.md  ← 含 P0 清单
        stage-3-coder-r2/                         # 修复轮 2
          inbox/
            prev-result.md  ← 上轮 coder 产出
            review.md       ← reviewer 的 P0
          outbox/
            result.md       ← 含 "P0 修复清单"
        stage-3-reviewer-r2/                      # 复审
          inbox/
            prev-review.md
            coder-r2__result.md
          outbox/review.md  ← 假设 0 P0，跳出循环
        stage-3-validator/                        # 进入 validator，pass，跳过修复轮

        stage-4-validator/                        # 阶段 4 是检查点，fail
          outbox/validation.md  ← 含修复指引
        stage-4-coder-r2/                         # validator fail 触发的 coder 修复
        stage-4-reviewer-r2/                      # 修后必须再过一次 reviewer
        stage-4-validator-r2/                     # 再验，pass
```

主会话可以在任何时候 `cat .task-swarm/runs/<RUN_ID>/agents/stage-X-Y/outbox/*` 看产物。
后缀规则：
- 无后缀 = 初轮
- `-r2`、`-r3` = 第 N 轮迭代（覆盖 reviewer / validator 循环）
- reviewer 和 validator 循环**共用同一轮号空间**（实际是嵌套循环外层用 `-r<N>`），主编排器派发时自己跟踪

## 用户怎么用

### 方式 1：从 specode selector 触发（推荐）

走正常 specode 流程到 tasks 确认后，在"任务执行"selector 选择 `用 task-swarm 多 agent 并发`。

### 方式 2：手动触发

```
/specode:task-swarm <spec-dir>/tasks.md
```

或者直接调 task-swarm（这个文档）作为参考，给主 Claude 一句"按 task-swarm 模式跑当前 spec 的 tasks.md"。

## 调试

| 想看什么 | 命令 |
| --- | --- |
| 某个 subagent 收到的 prompt | `cat .task-swarm/runs/<RUN>/agents/<stage>/task.md` |
| 某个 subagent 的产出 | `cat .task-swarm/runs/<RUN>/agents/<stage>/outbox/*` |
| 历史 run | `ls .task-swarm/runs/` |
| 清理 | `rm -rf .task-swarm/runs/<RUN>` |

## 完整示例

参考 `references/task-swarm-example.md` —— 一份完整的 specode 风格 tasks.md 样本，演示阶段拆分、检查点任务、`@swarm:` 子任务标签的写法，以及预期的派发结构（5 阶段 / 5 子任务 / 7 个 subagent）。
