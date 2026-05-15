# Iteration Phase

Spec lifecycle after first delivery. Defines how `iteration` differs from `implementation` / `acceptance`, what triggers it, and how subsequent rounds accumulate in spec documents.

## Phase 生命周期

```
intake → requirements → design → tasks → implementation → acceptance → iteration
                                                                         ↑
                                                          交付完成后的常驻状态
```

| Phase | 含义 | 进入条件 |
|---|---|---|
| `intake` | 初始解析，文档尚未写入 | `/spec` 触发后 |
| `requirements` | requirements.md / bugfix.md 编写或确认中 | 开始写第一份文档 |
| `design` | design.md 编写或确认中 | requirements 确认后 |
| `tasks` | tasks.md 编写或确认中 | design 确认后 |
| `implementation` | 正在执行代码任务 | tasks 确认并选择执行 |
| `acceptance` | 代码完成，UAT 验收 | 所有 required tasks `[x]` |
| `iteration` | 已交付需求的持续演进 | 用户在 acceptance 完成后输入 `/spec-accept`，或 `/spec-continue` 一个已 accept 的 spec |

## 三阶段语义对照

| Phase | 一句话 | 允许操作 |
|---|---|---|
| `implementation` | 正在写代码兑现 tasks | 编辑代码、改 tasks |
| `acceptance` | 代码写完，跑 UAT | 改 acceptance-checklist；不允许改代码（除非回退） |
| `iteration` | 已交付，等待下一轮演进 | 全部允许，需求变更走子循环 |

## iteration 子循环

iteration 不是"一切都允许"的自由阶段，而是"在已交付基础上，重新走一遍完整循环"。

```
iteration                                  ← 默认停留状态
   ├─ user: "我想加一个 X 功能"
   │     → spec_session.py iterate（round + 1）
   │     → 进入 iteration.requirements 子 phase
   │     → 在 requirements.md 末尾追加 "## 迭代 N 新增需求" 节
   │     → 走 confirm → design → tasks → implementation → acceptance
   │     → 全部完成后 → 自动回到 iteration
   │
   ├─ user: "改一下 acceptance 里的某条规则"
   │     → 直接编辑 acceptance-checklist.md（不需走完整循环）
   │
   └─ user: /spec-end
         → 释放 session 锁，spec 文档保留
```

## .config.json 字段

```json
{
  "iterationRound": 2,
  "iterationHistory": [
    { "round": 1, "startedAt": "...", "completedAt": "2026-05-08T...", "newReqCount": 3 },
    { "round": 2, "startedAt": "2026-05-11T...", "newReqCount": 0 }
  ]
}
```

- `iterationRound` 在首次 `iterate` 时 0 → 1，每次重新走完 acceptance 后 +1
- `iterationHistory` 保留历史轮次，便于追溯
- 用 `spec_session.py iterate <spec-dir>` 推进，不要手动改字段

## 文档累积写法（铁律）

| 文档 | 累积规则 |
|---|---|
| `requirements.md` | 原内容不动；末尾追加 `## 迭代 N 新增需求` 节；新 SHALL 前缀 `[迭代N]` |
| `tasks.md` | 原 `[x]` 不清理；新任务追加 `## 迭代 N 任务` 节 |
| `design.md` | 原节内可修改；必须在 `## 变更历史` 追加 `### 迭代 N`（无此节则创建） |
| `acceptance-checklist.md` | 跟随式重写；保留前几轮"结论=通过"的行，结论列改为 `已验收（迭代 N-1）` |

## /spec-continue 进入 iteration 的判断

恢复 spec 时 `spec_session.py continue` 的 `--phase` **不**写死 `iteration`：

- 默认值 `None`，由代码读 `.config.json.currentPhase`
- 只有用户明确通过 `/spec-accept` 或类似动作进入 iteration，phase 才被改写
- 这样可避免把还在 `design` 阶段的 spec 误置为 iteration、跳过未完成的设计门控

## /spec-accept（可选未实装命令）

文档约定：在 acceptance 阶段所有 required checklist 行结论=通过后，用户输入 `/spec-accept` 或交互式选择"验收通过"，agent 调：

```bash
python3 scripts/spec_session.py iterate <spec-dir>
```

将 phase 置为 `iteration`、`iterationRound` +1。
