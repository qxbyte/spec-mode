# Changelog

## Unreleased

_no entries yet_

## 0.2.0 (2026-05-17)

### Renamed (breaking-ish — see Migration)

- Plugin renamed: **`spec-mode` → `specode`**. All identifiers follow:
  - plugin / skill name (manifests, frontmatter, namespace `specode:*`)
  - directory tree (`plugins/specode/`, `skills/specode/`)
  - env vars (`SPEC_MODE_ROOT` → `SPECODE_ROOT`, `SPEC_MODE_GUARD` → `SPECODE_GUARD`)
  - runtime state (`~/.spec-mode/` → `~/.specode/`,
    `~/.config/spec-mode/` → `~/.config/specode/`)
  - vault index file (`.active-spec-mode.json` → `.active-specode.json`)
- The slash command `/spec` itself is unchanged (it was never `/spec-mode`).

### Added

- **Task-Swarm Mode**: a third option in the "任务执行" selector that delegates
  task execution to specialized subagents shipped with the plugin
  (`specode:task-swarm-coder`, `-reviewer`, `-validator`, `-planner`).
- Reviewer and validator subagents are spawned with **no Edit/Write tools** —
  enforces anti-self-approval at the tool layer (not just prompt-level).
- Stage-aggregated dispatch: one coder per top-level tasks.md stage (covering
  all its leaf subtasks), one reviewer per stage, validator reuses the
  built-in "检查点" tasks. Cuts subagent count from naive `N×3` to roughly
  `N` for a spec with `N` stages.
- **Convergence loop per stage**: each stage runs `coder → reviewer → validator`
  in a loop. Reviewer must classify findings as P0 (blocking) / P1 / P2; any
  P0 triggers a focused coder fix round + re-review. Validator fail triggers
  another coder fix round + mandatory re-review + re-validate. Default
  `--max-rounds 3` per loop; reviewer/validator self-report "same finding as
  previous round" to short-circuit infinite loops. tasks.md `[x]` is only
  written when both loops converge cleanly; intermediate rounds only append
  `> 第 N 轮…` progress notes under the affected subtask.
- New command `/task-swarm <spec-dir>/tasks.md` for manual triggering of
  task-swarm mode outside the standard selector flow.
- New plugin subdirectory `agents/` carrying the 4 task-swarm subagents.
  Claude Code auto-registers these (namespaced as `specode:task-swarm-*`).
- New references:
  - `references/task-swarm.md` — full protocol (single authority for editing
    behavior, subagent contract, write-back rules, loop semantics, iron-rule
    interaction)
  - `references/task-swarm-example.md` — sample tasks.md showing stage
    layout, `@swarm:*` labels, and expected subagent count
- New `migrate-from-spec-mode.sh` — one-shot migration script for users
  upgrading from 0.1.0; handles state dirs, vault index file rename,
  stale plugin install detection, and `SPEC_MODE_*` env var detection.
  Supports `--dry-run`.
- README: new `Uninstall` section (en + zh) covering plugin uninstall,
  marketplace removal, optional state cleanup, cache GC behavior, and
  temporary disable/enable.
- CONTRIBUTING: new `Release` section documenting semver bump rules
  (with examples), pre-release checklist, cutting a release, re-tagging
  caveats, and post-release verification.

### Changed

- `references/prompts.md` task execution selector now lists 4 options
  (added `用 task-swarm 多 agent 并发` before `暂不 coding`).
- `references/workflow.md` §7.1 documents the task-swarm delegation flow
  and how it co-exists with lock / INV-4 / phase-gate iron rules.
- `references/help-output.md` now lists `/task-swarm` in the cheat sheet.
- `SKILL.md` References section links the two new task-swarm docs.
- README (en + zh): new Task-Swarm Mode section.

### Fixed

- **P0 — subagent_type must be plugin-prefixed**: dispatching with the
  bare name `Task(subagent_type="task-swarm-coder", ...)` is rejected by
  Claude Code with `"Agent type not found"`. All 13 references in
  `commands/task-swarm.md` and `references/task-swarm.md` now use the
  fully-qualified `specode:task-swarm-coder` (and `-reviewer` /
  `-validator` / `-planner`). The `agents/*.md` frontmatter `name` is
  intentionally left as the bare form — the plugin loader applies the
  namespace automatically. Without this fix, task-swarm mode would have
  failed on the first Task dispatch.
- Doc improvements (from LLM-perspective review of `task-swarm.md`):
  - role mapping table gains a `职责` column documenting what each role
    actually does, plus a note that `planner` is rarely needed in the
    specode + task-swarm flow (specode tasks phase already does splitting)
  - 5-tier precedence rules for `@swarm:*` subtask labels (`skip` always
    wins > `full` > `coder-only` > heuristic); conflicts → INFO log;
    unknown tag → WARN log
  - typed pseudocode signatures in the 4e/4f convergence loops:
    `@dataclass ReviewResult` / `ValidationResult`, typed `fork_reviewer`
    / `fork_coder_fix_round` / `fork_validator` / `fork_reviewer_quick_check`

### Migration

Users upgrading from 0.1.0:

```sh
./migrate-from-spec-mode.sh --dry-run    # preview
./migrate-from-spec-mode.sh              # apply
claude plugin marketplace remove spec-mode
claude plugin marketplace add https://github.com/qxbyte/specode
claude plugin install specode@specode
```

If you had `SPEC_MODE_ROOT` / `SPEC_MODE_GUARD` exported in your shell rc,
rename them to `SPECODE_ROOT` / `SPECODE_GUARD`. The migration script
detects and prints reminders for these.

## 0.1.0 (2026-05-15)

### Phase 1 — bootstrap

- Initial plugin skeleton.
- `plugin.json` for Claude Code / CodeBuddy.
- `hooks/hooks.json` wiring SessionStart / UserPromptSubmit / PreToolUse /
  PostToolUse / Stop / SessionEnd → `scripts/spec_guard.py`.
- `scripts/spec_guard.py`: dispatch entry, audit-log every event, all
  handlers return ok. Supports `SPECODE_GUARD=off` global bypass.

### Phase 2 — state layer + injection + short-circuit

- `scripts/spec_state.py`: read-only probe against existing
  `.active-specode.json` / per-spec `.config.json`. Owns
  `~/.specode/{sessions,.any-active}`. CLI: status / sync-sentinel /
  demo-activate / demo-deactivate.
- `spec_guard.py`: SessionStart writes Claude-session record;
  UserPromptSubmit injects a status block via
  `hookSpecificOutput.additionalContext` when a spec is active; other
  handlers fast-exit when no active spec.
- `hooks/hooks.json`: shell short-circuit on `$HOME/.specode/.any-active`
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
