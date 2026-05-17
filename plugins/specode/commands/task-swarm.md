---
description: 把 specode tasks.md 委派给 task-swarm 多角色 agent 并发执行（coder→reviewer→validator 循环到收敛；物理隔离防自我认可）
argument-hint: "[<spec-dir>/tasks.md] [--parallel N] [--max-rounds N] [--dry-run]"
---

启动 task-swarm 编排器处理任务清单：`$ARGUMENTS`

## 执行规则

1. **必须先读取 `references/task-swarm.md`** 获取完整编排步骤、subagent 协议、回写规则。
2. **必须使用 Task 工具 fork 专用 subagent**：
   - `subagent_type="task-swarm-coder"` 给写代码任务
   - `subagent_type="task-swarm-reviewer"` 给评审任务
   - `subagent_type="task-swarm-validator"` 给验收 / 检查点任务
   - `subagent_type="task-swarm-planner"` 给拆分任务（一般不需要）
   - **不要**用 `general-purpose`，那会让角色隔离失效
3. **你是编排器，不是执行者** — 不要自己直接写代码、做评审、跑测试。所有具体任务必须 fork subagent。
4. **按一级阶段聚合派发** — coder 一次拿一整阶段（多个子任务），reviewer 一次评一整阶段，validator 直接复用 specode 检查点任务。详见 task-swarm.md。
5. **可并发的阶段必须并发派发** — 在同一个回复里发出多个 Task 工具调用。
6. **每阶段完成立刻回写 tasks.md** — 走 `verify-lock` 三检守，然后 Edit tasks.md 改 checkbox。绝不在内存累积变更。

## 参数

- `$ARGUMENTS` 第一个位置参数：tasks.md 路径（缺省取当前 active spec 的 tasks.md）
- `--parallel N` 限制最大并发阶段数（默认 3）
- `--max-rounds N` 每阶段 reviewer / validator 循环各自的最大轮次（默认 3）。reviewer 提 P0 或 validator fail 时会回 coder 修复并复审/重验，直到收敛或达上限
- `--dry-run` 只解析任务、打印阶段派发计划，不真派发 subagent

## 前提检查

执行前确认：
1. spec session 当前持有 lock（`verify-lock` 返回 ok）
2. plugin agents 已注册（specode plugin 安装时自动）
3. 当前 phase 是 `tasks` 或 `implementation`

任一不满足 → 不要 fork，告诉用户原因。

## 缺省行为

- tasks.md 缺失/找不到 → 提示用户用 `/specode:task-swarm <path>` 或先走 `/spec` 流程生成 tasks.md
- 解析失败（依赖成环 / 标签错误）→ 指出具体行号 + 错误，不要尝试 fork
- session lock 丢失 → 立即停止，提示用户 `/continue <slug>` 重新接管
