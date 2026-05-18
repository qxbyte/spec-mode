---
description: 多角色 agent 并发执行 tasks.md（CLI 驱动调度协议；脚本管状态机，模型只负责派单与文本生成）
argument-hint: "[<spec-dir>/tasks.md] [--parallel N] [--max-rounds N]"
---

把 tasks.md 委派给 task-swarm 编排器：`$ARGUMENTS`

## 协议（强制按顺序执行）

你是 task-swarm 调度器。**不要尝试理解状态机** — 状态机在 `scripts/task_swarm.py` 里。你只执行以下循环。

### 1. 初始化（只跑一次）

```bash
sh ${CLAUDE_PLUGIN_ROOT}/scripts/run.sh ${CLAUDE_PLUGIN_ROOT}/scripts/task_swarm.py init \
  --tasks <tasks.md 绝对路径> \
  [--parallel N] [--max-rounds N]
```

返回 JSON 含 `run_id`。**把 `run_id` 记住**——后续每个子命令都要传 `--run <run_id>`。

如返回 `error` → 把错误原文呈现给用户后停止。

### 2. 主循环

每一轮做四步，按 JSON 返回的 `action` 字段分支：

#### 2.1 拿下一步指令

```bash
sh ${CLAUDE_PLUGIN_ROOT}/scripts/run.sh ${CLAUDE_PLUGIN_ROOT}/scripts/task_swarm.py next --run <run_id>
```

返回 `{"action": "fork|writeback|wait|done", ...}`。

#### 2.2 按 action 执行

**`action == "fork"`** — 派发 subagent：

```
Task(
  description=<json.description>,
  subagent_type=<json.subagent_type>,        ← 必须是 "specode:task-swarm-{coder|reviewer|validator}"
  prompt=<cat json.prompt_file 的内容>          ← prompt 已由脚本预渲染好，不要改
)
```

subagent 返回后**立刻**：

```bash
sh ${CLAUDE_PLUGIN_ROOT}/scripts/run.sh ${CLAUDE_PLUGIN_ROOT}/scripts/task_swarm.py parse \
  --run <run_id> --stage <json.stage> --role <json.role> --round <json.round>
```

拿到 `{"judgment": "...", ...}`，再 advance：

```bash
sh ${CLAUDE_PLUGIN_ROOT}/scripts/run.sh ${CLAUDE_PLUGIN_ROOT}/scripts/task_swarm.py advance \
  --run <run_id> --stage <json.stage> --role <json.role> --round <json.round> \
  --judgment <parse.judgment>
```

**schema-error 重派**：若 `parse.retry == true`（judgment=schema-error），**不要** advance。
脚本已自动清空 outbox 与 in_flight。把 `parse.outbox_snapshot` 字典里的内容拼到下一次 fork 的 prompt
里（告诉 subagent 上次为何被拒），然后直接回到 2.1 调 `next`——会拿到对同一 stage/role/round 的 fork 指令。

**`action == "writeback"`**：

```bash
bash <json.cmd>     # 即: sh ${CLAUDE_PLUGIN_ROOT}/scripts/run.sh ${CLAUDE_PLUGIN_ROOT}/scripts/task_swarm.py writeback --run <run_id> --stage N
```

脚本内部已处理 verify-lock + heartbeat + 行级安全 Edit。**不要**自己 Edit tasks.md。

**`action == "wait"`**：当前并发已满或文件有冲突。等下一个 subagent 完成后再 `next`。

**`action == "done"`**：把 `json.summary` 用人话呈现给用户，结束。

#### 2.3 回到 2.1

## 严禁

- ❌ 不要自己读 tasks.md 决定派什么——`next` 会告诉你
- ❌ 不要自己解析 review.md / validation.md——`parse` 会给你结构化判定
- ❌ 不要自己 Edit tasks.md——hook 会拦下来（INV-9），用 `writeback` 子命令
- ❌ 不要省略 `parse → advance` 这一对调用——否则 state.json 不前进，`next` 卡死
- ❌ 不要用 `general-purpose` 作为 subagent_type——hook 会拦下来（INV-7）
- ❌ 不要自行拼 subagent prompt——`next` 给的 `prompt_file` 已包含 @writes 边界、修复轮指引、检查点专用文案

## 前提

- 已有 active spec session 且持锁
- plugin agents 已注册（specode plugin 安装时自动）
- 当前 spec 阶段是 `tasks` 或 `implementation`

任一不满足 → 不要 init，告诉用户原因。

## 调试

| 想看什么 | 命令 |
|---|---|
| run 当前状态 | `task_swarm.py status --run <id>` |
| 某 subagent 的 prompt | `cat .task-swarm/runs/<id>/agents/<stage>/task.md` |
| 某 subagent 的产出 | `cat .task-swarm/runs/<id>/agents/<stage>/outbox/*` |
| 所有 run | `ls .task-swarm/runs/` |

## 协议背后的设计

参考 `references/task-swarm.md` —— 文档解释为什么状态机、解析、回写要全部下沉到脚本（防"自我认可"、防模型在长循环里数错轮号、防 outbox 格式漂移）。运行时**不需要**读那份文档，按上面循环走即可。
