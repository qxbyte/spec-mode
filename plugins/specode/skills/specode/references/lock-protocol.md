# Lock Protocol — 锁状态机与多窗口接管

每个 spec 自己的 `<spec-dir>/.config.json.lock` 字段管单写权。**持有者键 = `session_id`**（与 `~/.specode/sessions/<id>.json` 文件名同源）。

## 0. 设计原则

- 锁存放在 spec 自身 `.config.json`，**不**放在 `<doc-root>/.active-specode.json`。原因：spec 文档可能跨设备同步（Obsidian），边界判断必须以 spec 自身状态为准；如果索引文件丢失，锁不能跟着消失。
- **任何 spec 文档写入前，必须持锁**。这是不可绕过的铁律（由 SKILL.md §Multi-Window + Lock 的"写前三重校验"保证）。
- 多窗口可同时打开**不同** spec；同一 spec 同一时刻只允许一个会话写入。
- 锁主即会话：所有 `acquire` / `release` / `heartbeat` / `verify-lock` 必须传 `--session <session_id>`，CLI 拒绝匿名调用。

## 1. `.config.json.lock` 字段

```json
{
 "specId": "uuid-of-spec",
 "currentPhase": "tasks",
 "workflow": "requirements",
 "lock": {
 "session_id": "abc-def-1234-...",
 "acquired_at": "2026-05-19T10:00:00Z",
 "last_heartbeat_at": "2026-05-19T10:25:00Z",
 "agent": "cli-agent",
 "pid": 12345
 },
 "evicted_sessions": [
 {
 "session_id": "old-session-id",
 "evicted_at": "2026-05-19T10:30:00Z",
 "evicted_by": "abc-def-1234-...",
 "reason": "force_acquire"
 }
 ]
}
```

`lock: null` 表示空闲，可被任意会话直接 acquire。`evicted_sessions` 数组追加而不清理（每条记录极小；运维需要时手动编辑）。

## 2. 五个核心命令

| 命令 | 行为 | 退出码 |
|---|---|---|
| `spec_session.py acquire --spec <dir> --session <id>` | 持锁；若已被自己持有 → 续约；他持 → 抛 LockHeld；stale 静默接管（记 evicted_sessions reason=`stale`） | 0 / 4 (LockHeld) |
| `spec_session.py acquire --spec <dir> --session <id> --force` | 强制接管；写 evicted_sessions reason=`force_acquire` | 0 |
| `spec_session.py release --spec <dir> --session <id>` | 释放自持锁；非自持时静默；同 turn 写 `sessions/<id>.json.lock_state=released` | 0 |
| `spec_session.py heartbeat --spec <dir> --session <id>` | 刷 `lock.last_heartbeat_at` + `sessions/<id>.json.last_activity_at`；不持锁 → exit 1 `lock_lost` | 0 / 1 |
| `spec_session.py verify-lock --spec <dir> --session <id>` | 检查持有状态：`ok` / `evicted` / `not_held` / `stale_lock` | 0 (ok) / 3 (其他) |

退出码 `3` 细分：

- `verify-lock` 输出 stdout `evicted` → 被驱逐窗口。
- `verify-lock` 输出 stdout `not_held` → 锁字段为 null 或被他人持有。
- `verify-lock` 输出 stdout `stale_lock` → 距 `last_heartbeat_at` 超过 stale 阈值。

约定：CLI 写 stdout 的关键状态字（`ok` / `evicted` / `not_held` / `stale_lock` / `LockHeld`）固定，方便主会话脚本化处理。

## 3. Stale 阈值

- 默认 `lock.last_heartbeat_at` 超过 **1800 秒（30 分钟）** → 视为 stale。
- 下一次 `acquire` 静默接管（不抛 LockHeld，evicted_sessions reason=`stale`）。
- 通过环境变量 `SPECODE_LOCK_STALE_SECONDS` 覆盖。
- `UserPromptSubmit` 的 `on-heartbeat-quiet` hook 每轮静默续约（自动）；主会话也可显式调 `spec_session.py heartbeat` 强制刷新。

## 4. 心跳触发点（主会话行为契约）

主会话在持久 session 中**必须**在以下时机调 `heartbeat`：

1. **每次写 spec 文档前**（Edit / Write 工具调用之前一行）。
2. **每次回答用户消息前**，如果距上次心跳超过 5 分钟。
3. **每次完成一个 task-swarm subagent 后**。

只读命令（`spec_status.py` / `spec_lint.py` / `load --json` / `read-session` / `verify-lock`）**不**触发心跳。

## 5. 写前三重校验（铁律）

任何 spec 文档写入前，主会话必须按顺序确认：

1. **specId 校验**：active-pointer 里的 `specId == <spec-dir>/.config.json.specId`。
2. **边界校验**：`<spec-dir>` 物理位于 `<doc-root>` 之下（不允许 `../` 穿越）。
3. **锁校验**：`spec_session.py verify-lock --spec <dir> --session <id>` 返回 `ok`。

任一失败 → **拒绝写入**，在 chat 报告原因（哪一项校验失败、当前持有者、可能的处置），**不要**静默继续。

> 写前三重校验是 spec-mode 边界纪律的核心。0.6.0 不再用 PreToolUse hook 阻断，靠 SKILL.md + 本协议自律 + CLI 在 exit code 3/4 上的报错。模型若执意写，能写出去，但 reviewer / 验收门 / 下一会话的 verify-lock 都会发现错位。

## 6. `/specode:continue` 接管协议

```
用户：/specode:continue <slug>
 │
 ▼
1. 解析 spec_dir
 │
 ▼
2. spec_session.py acquire --spec <dir> --session <id>
 │
 ├── exit 0 → 持锁成功 → 走 §6.1 后续步骤
 │
 └── exit 4 LockHeld → 走 §6.2 接管三选项
```

### 6.1 持锁成功

1. `spec_session.py load --spec <dir>` 拿 phase / iteration / tasks 计数 / 文档 mtime。
2. `spec_session.py continue --spec <dir> --session <id>` 写 `sessions/<id>.json`（mode=active / active_spec_slug / lock_state=ok）+ 更新 `<doc-root>/.active-specode.json` active-pointer。
3. 输出"已加载 spec"报告 + 状态行 footer + end turn。

### 6.2 LockHeld 三选项

1. 输出锁状态摘要：`持有者 session_id 前 8 位 + 最近 heartbeat 时间`。
2. 呈现 `takeover-options` 选择器（类型 A，详见 `references/selectors.md` §(6)）。**无推荐项**——让用户根据对方是否仍活跃自己判断。
3. End turn 等用户选。

| 选项 | 后续操作 |
|---|---|
| **1. 强制接管** | `spec_session.py acquire --spec <dir> --session <id> --force` → exit 0 + evicted_sessions 追加记录 → §6.1 后续。告知用户"对方下一次写操作会被 verify-lock 拒绝"。 |
| **2. 只读查看** | **不**调 acquire，直接 `spec_session.py load`；写 `sessions/<id>.json.mode=readonly`、`lock_state=readonly`；**不**更新 active-pointer 的 specSlug 绑定；状态行 footer 加 `[只读]`。后续所有 Edit/Write 在 SKILL.md 层面被劝阻（不阻断，但模型必须主动拒绝）。 |
| **3. 取消** | 不做任何写动作；回到对话起点。 |

## 7. 被驱逐窗口的行为

被强制接管的旧会话在**下一次写操作前**调 `verify-lock`：

- 返回 `evicted` → 该会话必须立即停止当前工作，输出：

```text
⚠ 你的会话已被 session <newId 前 8 位> 强制接管。
当前 spec 在此窗口已转为只读。继续工作请用
 /specode:continue <slug>
选择"强制接管"恢复可写。
```

- 同步把本窗口 `<doc-root>/.active-specode.json` 对应条目改为 `status: "evicted"`；写 `sessions/<id>.json.mode=readonly`、`lock_state=evicted`。
- 后续在该 spec 的任何写操作 → 主动拒绝，向用户提示重新走 `/specode:continue` 接管。
- 状态行 footer 加 `[只读]`。

## 8. 只读模式

进入条件：

- `/specode:continue <slug>` LockHeld 时选 2 `只读查看`。
- 被强制接管的旧会话 verify-lock=evicted。

行为约束：

- `spec_session.py load --spec <dir>`（**不**调 acquire）。
- **不**更新 active-pointer 的 specSlug 绑定。
- 状态行 footer 加 `[只读]`。
- 禁止所有写操作：Edit / Write / heartbeat / `spec_session.py continue|acquire|phase-transition` 都不调；如用户要求修改，SKILL.md 引导"请先 `/specode:continue <slug>` 选强制接管"。
- 用户要切回可写 → 再次 `/specode:continue <slug>` 选 1 `强制接管`。

## 9. 锁状态机（汇总）

```
 ┌──────────┐
 │ null │ (lock=null, 空闲)
 └────┬─────┘
 │ acquire
 ▼
 ┌─── force_acquire ──► ┌──────┐ ──── stale ────► (下一次 acquire 静默接管)
 │ │ ok │
 │ ◄── release/end ────└──┬───┘
 │ │
 │ │ 他人 force_acquire
 │ ▼
 ┌────────────┐ verify-lock ┌──────────┐
 │ evicted │ ◄────────── │ evicted? │
 └────┬───────┘ └──────────┘
 │ /specode:continue 强制接管
 ▼
 ok（新会话）

侧路：
 - heartbeat 在 ok 状态下刷新 last_heartbeat_at
 - not_held = lock=null 或被别人持有；verify-lock 路径
 - readonly = 只读模式（lock 字段不变，仅 sessions/<id>.json.mode=readonly）
```

## 10. 原子性保证

- 所有 `.config.json` 读改写序列被 `_file_lock(config_path)`（`fcntl.flock` / `msvcrt.locking`）保护。
- 写入用 `tempfile.NamedTemporaryFile` → `os.replace` → `os.fsync`，crash-safe。
- 同时写 `<spec-dir>/.config.json` + `~/.specode/sessions/<id>.json` 时 CLI 必须两边都成功才算成功；任一失败 → 回滚已写字段 + exit 1。
- `_file_lock` 在不支持平台（罕见）静默退化为无锁原子写入（仍不会出半文件，但极端竞争下可能出现 lost update —— 已知风险）。

## 11. evicted_sessions 数组

- 每次驱逐追加一条（保留历史）。
- 被驱逐会话用它来判断"我是否被驱逐了"——`verify-lock` 检查当前 session_id 是否在数组里。
- 不自动清理；运维需要时手动编辑 `.config.json`。

## 12. 跨文档引用

- 三层文档根目录解析 → `references/obsidian.md`。
- 选择器三种类型与 `takeover-options` 文本骨架 → `references/selectors.md`。
- phase 切换协议 → `references/workflow.md` §Phase-gate 输出顺序。
- 写前三重校验在 SKILL.md §Multi-Window + Lock 也有摘要。
