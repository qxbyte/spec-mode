# spec-mode-plugin

Specification-driven workflow plugin for **Claude Code** and **CodeBuddy**.

Adds a hard, harness-enforced sync guard between source code and spec documents:
once spec-mode is active, any source-code change that does not correspond to an
approved task — or follow a same-turn change to `design.md` / `tasks.md` /
`bugfix.md` — is rejected by a `PreToolUse` hook. A turn that touches code but
leaves all spec docs untouched cannot end: the `Stop` hook refuses until the
model logs the change.

This is the harness-level successor to the original `spec-mode` skill at
`~/Git/skills/spec-mode/`, which relied on prompt instructions ("iron rules")
and therefore degraded as context grew. Plugin hooks are deterministic and
context-independent.

## Status

Phase 1 (hook wiring) — handlers dispatch and audit-log only. No real
enforcement yet. See `/Users/xueqiang/Desktop/spec-mode-plugin-design.md` for
the full design and the Phase 2-6 plan.

## Local install (development)

```sh
# In Claude Code:
/plugin install /Users/xueqiang/Git/spec-mode-plugin

# Verify wiring (start a fresh session, then):
ls -la ~/.spec-mode/audit/
tail -f ~/.spec-mode/audit/$(date -u +%Y-%m-%d).log
```

You should see `SessionStart` / `UserPromptSubmit` / `PreToolUse` / etc. log
lines as you interact.

## Global bypass

```sh
export SPEC_MODE_GUARD=off
```

Forces every hook to exit 0 immediately. For debugging only.

## License

MIT
