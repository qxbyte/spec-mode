---
description: 恢复或切换 specode 会话
argument-hint: "[spec-slug]"
---

/specode:continue $ARGUMENTS

## 立即调用

所有 CLI **必须**走完整路径模板（绝不裸调 `python3 spec_session.py …`）：

无 slug —— 列出可恢复 spec：

```sh
sh "${CLAUDE_PLUGIN_ROOT:-${CODEBUDDY_PLUGIN_ROOT}}/scripts/run.sh" \
   "${CLAUDE_PLUGIN_ROOT:-${CODEBUDDY_PLUGIN_ROOT}}/scripts/spec_session.py" \
   list-specs --session <id>
```

有 slug —— 接管并加载：

```sh
sh "${CLAUDE_PLUGIN_ROOT:-${CODEBUDDY_PLUGIN_ROOT}}/scripts/run.sh" \
   "${CLAUDE_PLUGIN_ROOT:-${CODEBUDDY_PLUGIN_ROOT}}/scripts/spec_session.py" \
   acquire --spec <dir> --session <id>
# 接 continue + load 子命令同模板
```

- spec 目录根：`~/.config/specode/config.json.obsidianRoot` 或 vault 自检测；**不要** Grep 项目目录
- LockHeld → 呈现 `takeover-options` 选择器（强制接管 / 只读 / 取消）
- 详细流程见 SKILL.md §Session Lifecycle / §CLI 调用规约
