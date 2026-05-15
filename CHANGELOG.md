# Changelog

## 0.1.0 (2026-05-15)

### Phase 1 — bootstrap

- Initial plugin skeleton.
- `plugin.json` for Claude Code / CodeBuddy.
- `hooks/hooks.json` wiring SessionStart / UserPromptSubmit / PreToolUse /
  PostToolUse / Stop / SessionEnd → `scripts/spec_guard.py`.
- `scripts/spec_guard.py`: dispatch entry, audit-log every event, all
  handlers return ok. Supports `SPEC_MODE_GUARD=off` global bypass.

### Phase 2 — state layer + injection + short-circuit

- `scripts/spec_state.py`: read-only probe against existing
  `.active-spec-mode.json` / per-spec `.config.json`. Owns
  `~/.spec-mode/{sessions,.any-active}`. CLI: status / sync-sentinel /
  demo-activate / demo-deactivate.
- `spec_guard.py`: SessionStart writes Claude-session record;
  UserPromptSubmit injects a status block via
  `hookSpecificOutput.additionalContext` when a spec is active; other
  handlers fast-exit when no active spec.
- `hooks/hooks.json`: shell short-circuit on `$HOME/.spec-mode/.any-active`
  for the non-session hooks so Python doesn't even start when idle.

### Phase 3 — Code-Doc Sync Guard (CDSG)

- `scripts/spec_sync.py`: tasks_files extraction (FILE: lines + Affected
  Files section + glob), path classification (spec-doc / project-code /
  outside), `.sync-ledger.json` + per-turn ledger, legality checks. CLI:
  status / freeform / extract.
- `spec_guard.py`:
  - `UserPromptSubmit` refreshes turn_id, re-extracts tasks_files, mirrors
    freeformMode into ledger, extends the injected status block (mode /
    turn / tasks_files count).
  - `PreToolUse` enforces INV-1 (Code-Doc Sync) on project-code edits.
  - `PostToolUse` appends changes to ledger turn_code/doc_changes.
  - `Stop` enforces INV-2 (turn conservation) and INV-4 (acceptance-
    checklist follow-mode). Resets turn on pass.
- `SKILL.md`: three new dispatch entries for `--freeform` / `--strict` /
  `--sync-status`.

Hard-decision compliance (design doc §13):
- 1A: freeform does NOT exempt INV-2.
- 2A: `implementation-log.md` counts as a doc change for INV-2.

### Phase 4 — INV-3 + INV-6

- `spec_sync.py`: `check_verify_lock` (delegates to `spec_session._verify`;
  denies on `evicted`, allows on `ok`/`not_held`/`stale_lock` for backward
  compat); `check_phase_gate` against `PHASES_FORBID_CODE = {intake,
  requirements, bugfix, design, tasks}`.
- `spec_guard.py` `PreToolUse`:
  - spec-doc edits → INV-3 verify-lock first.
  - project-code edits → INV-6 phase gate (absolute; freeform does NOT
    exempt), then INV-1.
  - outside edits → pass through.

### Phase 5 — CodeBuddy static adapter

- `hooks/hooks.json`: env var fallback to
  `${CLAUDE_PLUGIN_ROOT:-${CODEBUDDY_PLUGIN_ROOT}}`.
- `adapters/codebuddy/README.md`: open items + suggested wrapper script if
  CodeBuddy doesn't honor `${VAR:-fallback}` expansion inside hook command
  strings.

### Phase 6 — tests + docs + stub

- `tests/` with 19 pytest cases (unit + integration). Covers all six
  invariants, freeform behavior, glob matching, lock states, phase gate
  matrix.
- `README.md` rewritten as full reference (rules, architecture, install,
  usage, performance, bypass, CodeBuddy note, tests).
- `CONTRIBUTING.md`: stdlib-only runtime rule, test conventions.
