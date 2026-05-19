---
description: 显示当前 specode 会话状态
---

/specode:status

## 立即调用

```sh
sh "${CLAUDE_PLUGIN_ROOT:-${CODEBUDDY_PLUGIN_ROOT}}/scripts/run.sh" \
   "${CLAUDE_PLUGIN_ROOT:-${CODEBUDDY_PLUGIN_ROOT}}/scripts/spec_session.py" \
   status --session <id>
```

输出含 session + spec_config + lock 摘要。

- session 文件：`~/.specode/sessions/<session_id>.json`
- spec 配置：`<active_spec_dir>/.config.json`（路径取自 session 文件 `active_spec_dir`）
- 字段语义见 SKILL.md §Session Lifecycle
- 调用模板规约见 SKILL.md §CLI 调用规约（**禁止**裸 `python3 spec_session.py …`）
