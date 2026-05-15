# Changelog

## 0.1.0 (Phase 1 — 2026-05-15)

- Repo skeleton extracted from `~/Git/skills/spec-mode/`.
- `plugin.json` for Claude Code / CodeBuddy.
- `hooks/hooks.json` wiring SessionStart / UserPromptSubmit / PreToolUse /
  PostToolUse / Stop / SessionEnd → `scripts/spec_guard.py`.
- `scripts/spec_guard.py`: dispatch entry, audit-log every event, all handlers
  return ok. Supports `SPEC_MODE_GUARD=off` global bypass.
- `scripts/spec_sync.py` / `spec_state.py`: stub modules for Phase 2-3.
- Existing scripts/commands/references/assets copied verbatim.
