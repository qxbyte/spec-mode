---
description: 多 agent 并发执行 tasks.md（state.json 单一事实源；主代理按 plan→fork→advance 循环驱动）
argument-hint: "[<spec-dir>/tasks.md] [--max-parallel N] [--max-rounds N]"
---

## 立即调用

```sh
sh "${CLAUDE_PLUGIN_ROOT:-${CODEBUDDY_PLUGIN_ROOT}}/scripts/run.sh" \
   "${CLAUDE_PLUGIN_ROOT:-${CODEBUDDY_PLUGIN_ROOT}}/scripts/task_swarm.py" \
   init --tasks <abs> --session <id>
```

拿到 `run_id` + `run_dir` 后按下面「7 步循环」`plan → fork → advance → writeback → resolve`，**所有** task_swarm.py 子命令都套同样的 run.sh 包装模板。

- state.json 位置：`<run_dir>/state.json`（唯一事实源，主代理可随时读）
- 完整协议见 references/task-swarm.md
- 调用模板规约见 SKILL.md §CLI 调用规约（**禁止**裸 `python3 task_swarm.py …`）

# /specode:task-swarm — task-swarm 编排器

将一份已确认的 `tasks.md` 委派给一组 agent **并发**执行：多 coder 写代码、一个 reviewer 评审、一个 validator 验证 + 循环修复，直至 group 收敛。整个过程 tasks.md 是唯一对外可见的"事实文档"，writeback 走 CLI line-safe diff。

参考协议：`references/task-swarm.md`（详细规格、§11.1-§11.7 全部子节）。

## 何时用

- `tasks.md` 已确认并落盘。
- 任务执行 selector 选了「用 task-swarm 多 agent 并发」。
- spec 锁仍由主会话持有（主会话是唯一持锁者；subagent 不动锁）。

## 7 步循环（主代理必须遵守）

```text
1. init —— 一次性建 run_id / 切 group / 写 state.json
 task_swarm.py init --tasks <abs> [--max-parallel N] [--session <id>]
 输出：{run_id, run_dir, groups}

2. plan —— 查询下一步该 fork 哪些 subagent（确定性，不改 state）
 task_swarm.py plan --run <run_id>
 输出 JSON 含 fork[] 列表（agent_key + task.md 路径）

3. fork —— **同一 message 内**发 N 个 Task block（按 plan.fork 逐字拷贝）
 每个 Task 的 prompt 直接用 `cat <task.md>` 的内容；不要凭印象自创 agent_key

4. （hook 提示）每个 subagent 返回时 PostToolUse hook 自动 inject 节点提醒；
 主代理可读可忽略。等齐当前 phase 全部 subagent 返回再进 advance。

5. advance —— 解析 outbox，推进 state.json
 task_swarm.py advance --run <run_id> --phase <coding|review|p0-fix|validation|v-fix>
 --round <n>
 输出含下一步建议；按建议回到第 2 步继续 plan→fork 循环

6. writeback —— 当前 group validator pass 后回写 tasks.md
 task_swarm.py writeback --run <run_id> --group <N>
 line-safe diff（仅 checkbox toggle + `> ` 注释块）；越界 exit 1

7. done —— 所有 group 完成 → resolve 并退出 task-swarm，回到 spec-mode acceptance phase
 task_swarm.py resolve --run <run_id>
```

## Phase 状态机（详见 references/task-swarm.md §11.3）

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

关键规则：

- reviewer 是 advisory（**只触发一次** p0-fix，不 re-review）。
- validator 是阻塞（fail → v-fix → validator 循环，直到 pass 或连续 3 轮同 fail 签名 → `failed-deadloop`）。
- reviewer P0 必须带证据标签 `[req:x.y]` / `[security]` / `[contract]`；无证据 P0 自动降级为 advisory（仅写注释，不进 fix loop）。

## 文件冲突 / group 切分

`task_swarm.py init` 按 §11.2 自动切 group：

- 同一 group 内：任意两 stage 的 `@writes` 集合不相交且无 `@depends-on` 关系。
- 跨 group：上一 group 全部 pass（writeback 完成）后才能开始下一 group。
- 一个 stage 即使可以并发也不会被拆——「stage = coder 任务粒度的最大单元」。

## heartbeat

主代理每 5 分钟 / 每完成一个 subagent 后调用：

```sh
task_swarm.py heartbeat --run <run_id>
spec_session.py heartbeat --spec <dir> --session <id>
```

确保长流程下 spec 锁不被 stale 回收。

## 异常处理

- coder STATUS=failed/blocked → 整个 group 标 failed，主代理向用户报告并中止后续 group。
- writeback 越界（line-safe diff 失败）→ exit 1，主代理不能继续，必须修复 tasks.md 后重跑。
- 死循环检测（连续 3 轮同 fail 签名）→ state.json 标 `failed-deadloop`，停循环、报告用户介入。

## 命令调用样例

```sh
# 一次性 init
task_swarm.py init --tasks /abs/path/to/specs/<slug>/tasks.md --max-parallel 4 --session <id>

# 循环
task_swarm.py plan --run 20260519-141200-ab12cd
# … 按输出 fork subagents …
task_swarm.py advance --run 20260519-141200-ab12cd --phase coding --round 1
# … 直到 plan.action == "writeback" …
task_swarm.py writeback --run 20260519-141200-ab12cd --group 1
# … 继续下一 group …
task_swarm.py resolve --run 20260519-141200-ab12cd
```

/specode:task-swarm $ARGUMENTS
