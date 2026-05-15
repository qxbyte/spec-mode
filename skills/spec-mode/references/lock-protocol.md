# Spec-mode Lock Protocol

Per-spec write lock for safe multi-window operation. Implemented in
`scripts/spec_session.py`; this document is the behavioral contract.

## 设计原则

- **锁存放在 spec 自身 `.config.json`**，不放在 `.active-spec-mode.json`。原因：spec 文档可能跨设备同步（Obsidian），边界判断必须以 spec 自身状态为准；如果索引文件丢失，锁不能跟着消失。
- **任何 spec 文档写入前，必须持锁。** 这是不可绕过的铁律。
- **多窗口可同时打开不同 spec**；同一 spec 同一时刻只允许一个 session 写入。

## .config.json 字段

```json
{
  "specId": "uuid",
  "lock": {
    "sessionId": "TERM_SESSION_A1B2",
    "acquiredAt": "2026-05-11T10:00:00Z",
    "lastHeartbeatAt": "2026-05-11T10:25:00Z",
    "agent": "claude-code",
    "pid": 12345
  },
  "evictedSessions": [
    {
      "sessionId": "TERM_SESSION_C3D4",
      "evictedAt": "2026-05-11T10:30:00Z",
      "evictedBy": "TERM_SESSION_A1B2",
      "reason": "force_acquire"
    }
  ]
}
```

`lock: null` 表示空闲，可被任意 session 直接获取。

## 五个核心操作

| 命令 | 行为 | 退出码 |
|---|---|---|
| `spec_session.py acquire <dir> --session <id>` | 拿锁；自持续约；他持抛 `LockHeld`；stale 自动接管 | 0 / 4(LockHeld) |
| `spec_session.py acquire <dir> --session <id> --force` | 强制接管，记录 `evictedSessions` | 0 |
| `spec_session.py release <dir> --session <id>` | 释放自持锁；非自持时静默 | 0 |
| `spec_session.py heartbeat <dir> --session <id>` | 续约；不持锁抛 `lock_lost` | 0 / 1 |
| `spec_session.py verify-lock <dir> --session <id>` | 检查持有状态：`ok` / `evicted` / `not_held` | 0(ok) / 3(其他) |

## Stale 阈值

- 默认 `lastHeartbeatAt` 超过 **1800 秒（30 分钟）** → 视为 stale
- 下一次 `acquire` 静默接管（记录到 `evictedSessions`，reason=`stale`）
- 通过环境变量 `SPEC_MODE_LOCK_STALE_SECONDS` 覆盖

## 心跳触发点（agent 行为契约）

agent 在持久 session 中必须在以下时机调 `heartbeat`：

1. **每次写 spec 文档前**（Edit / Write 工具调用前）
2. **每次回答用户消息前**（如果中间间隔 > 5 分钟）

只读命令（`spec_status.py`、`spec_lint.py`、`load --json`）**不**触发心跳。

## /spec-continue 接管流程

```
用户：/spec-continue <slug>

代码：
  1. 解析 slug → spec_dir
  2. acquire(currentSession)
     成功 → 进入 spec
     失败（LockHeld）→ 先向用户输出锁状态摘要（持有者 sessionId + 最后活动时间），
                    然后运行 `references/prompts.md` 中的「/spec-continue 接管」选择器：
                       - 强制接管 → acquire --force
                       - 只读查看 → 加载文档但不 acquire，footer 标记 [只读]
                       - 取消     → 退出
```

接管选择器命令、措辞、推荐项见 `references/prompts.md`（统一选择器命令节）。

## 被驱逐窗口的行为

旧 session 在下一次写操作前调 `verify-lock`：

- 返回 `evicted` → agent 必须立即停止当前工作，输出：

  ```
  ⚠ 你的会话已被 session <newId> 强制接管。当前 spec 在此窗口已转为只读。
  继续工作请用 /spec-continue 强制接管回来。
  ```

  并将本窗口 `.active-spec-mode.json` 对应条目改为 `status: "evicted"`。后续在该 spec 的任何写操作 → 直接拒绝。

## 只读模式

- agent 调 `spec_session.py load --json` 加载文档（**不**调 acquire）
- 不更新 `.active-spec-mode.json` 中本 session 的 specSlug 绑定
- footer 格式：

  ```
  ─── spec-mode ─── spec: <slug> | session: <id> | phase: <phase> | [只读] | /spec-end 退出
  ```

- 禁止所有写操作：Edit / Write / heartbeat / `spec_session.py continue|start|iterate` 全部拒绝
- 用户要切换为可写：再次 `/spec-continue <slug>` 并选"强制接管"

## 写前三重校验（铁律）

任何 spec 文档写入前 agent 必须确认：

1. **specId 校验**：active pointer.specId == .config.json.specId
2. **边界校验**：spec_dir 在 documentRoot 下（`spec_session.ensure_within_root`）
3. **锁校验**：`spec_session.py verify-lock` 返回 `ok`

任一失败 → 拒绝写入，输出错误，**不**静默继续。

## 原子性保证

- 所有 `.config.json` 读改写序列被 `_file_lock(config_path)`（`fcntl.flock` / `msvcrt.locking`）保护
- 写入使用 `write_json` 的 temp + `os.replace()` 模式，crash-safe
- `_file_lock` 在不支持平台（罕见）静默退化，原子写入仍保证不会写出半文件

## evictedSessions 数组

- 每次驱逐追加一条记录（保留历史）
- 用于 `verify-lock` 检测自己是否被驱逐
- 不会自动清理（数量极小，每次驱逐一条；如有运维需求可手动编辑）
