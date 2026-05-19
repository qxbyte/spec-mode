# Iteration — 子循环规则与文档累积

`iteration` 是 spec 已交付后的**常驻**状态。本文件给出触发条件、phase 子循环、文档累积写法、退出条件。

## 0. Phase 生命周期回顾

```
intake → requirements / bugfix → design → tasks → implementation → acceptance → iteration
 ↑
 交付完成后的常驻状态
```

| Phase | 含义 | 进入条件 |
|---|---|---|
| `intake` | 初始解析，文档尚未写入 | `/specode:spec` 触发后 |
| `requirements` | requirements.md / bugfix.md 编写或确认中 | 工作流确认后开始第一份文档 |
| `design` | design.md 编写或确认中 | requirements / bugfix 确认后（或 design-first 工作流直接进入） |
| `tasks` | tasks.md 编写或确认中 | design 确认后 |
| `implementation` | 正在执行代码任务 | tasks 确认并选择执行 |
| `acceptance` | 代码完成，跑 UAT | 所有 required 任务 `[x]` |
| `iteration` | 已交付需求的持续演进 | `acceptance-gate` 选 `验收通过` 或 `/specode:continue` 一个已 accept 的 spec |

## 1. 三阶段语义对照

| Phase | 一句话 | 允许操作 |
|---|---|---|
| `implementation` | 正在写代码兑现 tasks | 编辑代码、改 tasks 状态、追加 implementation-log |
| `acceptance` | 代码写完，跑 UAT | 不允许新功能改动（只允许回退 / 测试修复） |
| `iteration` | 已交付，等待下一轮演进 | 全部允许；需求变更走子循环 |

## 2. 触发条件

进入 iteration 的两种触发：

1. **`acceptance-gate` 选 `验收通过`**：调 `spec_session.py iterate <spec-dir>`（实际由 `phase-transition --from acceptance --to iteration` 完成；CLI 内部把 `iterationRound` +1、`iterationHistory` 追加一条）。
2. **`/specode:continue <slug>` 一个已 accept 的 spec**：`spec_session.py continue` 读 `.config.json.currentPhase`，若是 `iteration` 则进入；若仍是 `acceptance` / `implementation` 等，按落盘值进入对应 phase（**不**写死 iteration —— 避免把还在 design 阶段的 spec 误置为 iteration、跳过未完成的设计门控）。

进入 iteration 后的默认动作：

- 状态行 footer 显示 `phase: iteration`。
- SKILL.md 不自动呈现 `iteration-scope` 选择器 —— 默认进入 iteration 后停在 chat 等用户提出下一步；模型可在判断到调整范围明确时主动呈现。
- 起，若用户明示"开始下一轮迭代" → 呈现 `iteration-scope`（类型 C）让用户选本轮调整范围。

## 3. iteration 子循环

iteration **不是**"一切都允许"的自由阶段，而是"在已交付基础上，**重新走一遍完整循环**"。

```
iteration ← 默认停留状态
 ├─ 用户："我想加一个 X 功能"
 │ → spec_session.py iterate <spec-dir> （iterationRound +1）
 │ → 进入 iteration.requirements 子 phase
 │ → 在 requirements.md 末尾追加 "## 迭代 N 新增需求" 节
 │ → 走 doc-confirm-requirements → design → tasks
 │ → implementation → acceptance
 │ → acceptance-gate 通过 → 自动回到 iteration（round +1）
 │
 ├─ 用户："改一下 acceptance 里某条规则"
 │ → 直接编辑 tasks.md 对应任务或末尾 `## 测试要点` 行
 │ →（不需走完整子循环 —— 视为微调）
 │
 ├─ 用户："只重跑测试"
 │ → 不改文档，跑 tasks.md 任务对应的"验证：xxx"小项
 │ → 更新 implementation-log 记录实际结果
 │
 └─ 用户：/specode:end
 → 释放 session 锁，sessions/<id>.json.mode=ended
 → spec 文档保留
```

iteration 期间所有 phase 限制**放松**：

- 可重走 requirements → design → tasks → implementation 子循环。
- 可直接补 implementation-log.md。
- 但**仍**走 phase-transition CLI 切换子 phase —— 不要手改 `.config.json.currentPhase`。

## 4. `.config.json` 字段

```json
{
 "specId": "uuid-of-spec",
 "currentPhase": "iteration",
 "iterationRound": 2,
 "iterationHistory": [
 { "round": 1, "startedAt": "2026-05-01T...", "completedAt": "2026-05-08T...", "newReqCount": 3 },
 { "round": 2, "startedAt": "2026-05-11T...", "newReqCount": 0 }
 ],
 ...
}
```

- `iterationRound` 在首次 `iterate` 时 0 → 1；每次再次跑通 `acceptance-gate=验收通过` 后 +1。
- `iterationHistory` 保留历史轮次（startedAt / completedAt / newReqCount）便于追溯。
- 用 `spec_session.py iterate <spec-dir> --session <id>` 推进，**不要**手动改字段。

## 5. 文档累积写法（铁律）

| 文档 | 累积规则 |
|---|---|
| `requirements.md` | **原内容不动**；末尾追加 `## 迭代 N 新增需求` 节；新 SHALL 编号前缀 `[迭代 N]`，如 `[迭代 2] 5.1 WHEN ... SHALL ...`。原编号继续延续（不重排）。 |
| `bugfix.md` | 同 requirements.md：末尾追加 `## 迭代 N 新增问题` 节；新条目带 `[迭代 N]` 前缀。 |
| `design.md` | 原节内**可修改**；必须在 `## 变更历史` 节追加 `### 迭代 N` 子节（无此节则创建），说明本轮架构 / 接口 / 数据模型变更。原节内修改的地方留 `<!-- [迭代 N] -->` 注释标记。 |
| `tasks.md` | 原 `[x]` 任务**不清理**；新任务追加 `## 迭代 N 任务` 节；任务编号续延（如旧最后是 `5.`，新任务从 `6.` 起）；新任务 traceability 引用 requirements.md 的 `[迭代 N]` 前缀编号。末尾 `## 测试要点` 节可按需追加新行供测试人员参考。 |
| `implementation-log.md` | 按日期继续追加，每条记录开头加 `[迭代 N]` 前缀。 |

### 5.1 累积示例

```markdown
（requirements.md 末尾）

## 迭代 2 新增需求

### 需求 5：[新需求标题]

**用户故事：** 作为 ...

#### 验收标准

[迭代 2] 5.1 WHEN ... SHALL ...
[迭代 2] 5.2 IF ... THEN ... SHALL ...
```

```markdown
（design.md 变更历史节）

## 变更历史

### 迭代 1

- 2026-05-08：架构无变化，仅修复 §错误处理 中条目 3 的描述。

### 迭代 2

- 2026-05-11：新增 `[Component] PasswordPolicy`（§组件与接口 §3）支持需求 5.1 / 5.2。
- 2026-05-12：调整数据模型 `User.password` 字段为 `salted_hash + algo_version`（迁移计划见 §流程）。
```

```markdown
（tasks.md 新任务节）

## 迭代 2 任务

- [ ] 6. [新阶段任务标题]
 - [ ] 6.1 [子任务]
 - 文件：`src/auth/password_policy.py`
 - 验证：`pytest tests/test_password_policy.py`
 - _需求：5.1_
 - [ ] 6.2 [子任务]
 - _需求：5.2_
```

```markdown
（tasks.md 末尾 `## 测试要点` 按需追加新行后片段）

## 测试要点

- 输入少于 8 位密码点击提交 → 系统提示"密码长度不足"（需求 1.1）
- 连续 5 次错误密码登录 → 账号锁定 15 分钟（需求 1.2）
- [迭代 2] 输入弱密码（如 12345678）→ 提示"密码强度不足"（需求 5.1）
- [迭代 2] 修改密码时复用历史密码 → 拒绝并提示原因（需求 5.2）
```

## 6. `/specode:continue` 进入 iteration 的判断

恢复 spec 时 `spec_session.py continue` 的 `--phase` **不**写死 `iteration`：

- 默认值 `None`，由 CLI 读 `<spec-dir>/.config.json.currentPhase`。
- 只有用户明确通过 `acceptance-gate=验收通过` 才被改写为 `iteration`。
- 这样可避免把还在 `design` 阶段的 spec 误置为 iteration、跳过未完成的设计门控。

## 7. `iteration-scope` 选择器（类型 C）

iteration 子循环开始时主会话呈现 `iteration-scope`（类型 C 复选框）让用户勾选本轮调整范围。详见 `references/selectors.md` §(8)。

选项（可多选）：

1. 改 requirements
2. 改 design
3. 改 tasks
4. 重跑测试

允许"全不选"（视为本轮 iteration 取消）。

该选择器**不**自动呈现 —— 默认在 chat 等用户提出下一步；模型在判断到调整范围明确时可主动呈现。

## 8. 退出条件

iteration 子循环按以下任一条件退出：

1. **用户运行 `/specode:end`**：调 `spec_session.py end --session <id>` → 释放锁 + sessions/<id>.json.mode=ended。spec 文档保留；下次 `/specode:continue <slug>` 仍可恢复。
2. **再次跑通 `acceptance-gate=验收通过`**：本轮迭代完成 → `iterationRound +1` → 回到 iteration 默认停留状态。

iteration 状态本身**不会自动**退出 —— 它是 spec 的"已交付"常驻态。`/specode:end` 关闭的只是当前 session，不是 spec。

## 9. 跨文档引用

- phase 序列与 `acceptance-gate` 选择器详情 → `references/workflow.md` §7、`references/selectors.md` §(7)。
- 6 份文档的章节模板 → `references/templates.md`。
- 锁状态机（iteration 期间仍受锁保护）→ `references/lock-protocol.md`。
- 入口选择器 `iteration-scope`→ `references/selectors.md` §(8)。
