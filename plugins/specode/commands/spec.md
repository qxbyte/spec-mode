---
description: 进入 specode 持久会话，开始新 spec 或调用子命令
argument-hint: "<需求> | <名称>: <需求> | -h | --set-vault <p> | --set-root <p> | --detect-vault | --vault-status | --sync-status"
---

/specode:spec $ARGUMENTS

## 立即调用

解析 `<名称>：<内容>` → 推导英文 slug，然后：

```sh
sh "${CLAUDE_PLUGIN_ROOT:-${CODEBUDDY_PLUGIN_ROOT}}/scripts/run.sh" \
   "${CLAUDE_PLUGIN_ROOT:-${CODEBUDDY_PLUGIN_ROOT}}/scripts/spec_init.py" \
   --name <slug> --requirement-name "<显示名>" --source-text "<原文>" --session <id>
```

- doc-root 三层解析：`--root` / `SPECODE_ROOT` env / `~/.config/specode/config.json.obsidianRoot` / Obsidian vault 自检测
- 三层全 miss → exit 3 + 引导提示；**不**回退到 cwd / ~/specs
- fast-path 参数（`-h` / `--vault-status` / `--detect-vault` / `--sync-status`）由 hook 拦截并预渲染输出，模型只负责 verbatim print
- 详细流程见 SKILL.md §Session Lifecycle / references/obsidian.md
- 调用模板规约见 SKILL.md §CLI 调用规约（**禁止**裸 `python3 spec_init.py …`）
