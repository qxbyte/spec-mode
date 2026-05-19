---
description: 结束当前 specode 持久会话（不删除文档）
---

/specode:end

## 立即调用

```sh
sh "${CLAUDE_PLUGIN_ROOT:-${CODEBUDDY_PLUGIN_ROOT}}/scripts/run.sh" \
   "${CLAUDE_PLUGIN_ROOT:-${CODEBUDDY_PLUGIN_ROOT}}/scripts/spec_session.py" \
   end --session <id>
```

强制双写：`~/.specode/sessions/<id>.json` + 当前 spec 的 `.config.json`。落地动作：写 `mode=ended` / `ended_at` / 清 `active_spec_*` / 释锁 / 清 `task_swarm_run_id`。

- 任一写失败视为整命令失败；**不接受** in-memory 半成功
- 调用模板规约见 SKILL.md §CLI 调用规约（**禁止**裸 `python3 spec_session.py …`）
