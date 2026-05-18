# Changelog

## Unreleased

### Added

- **Local-only telemetry** (`~/.specode/telemetry.jsonl`): opt-in via
  `SPECODE_TELEMETRY=on`, append-only single file so `grep` / `jq` stay
  trivial. **Absolutely no remote upload** — purely for the user's own
  inspection of flow execution.
  - Events: `spec.init` / `spec.phase_transition` / `spec.end` /
    `inv.violation` (INV-1..9, with `spec_slug` + `phase`) /
    `swarm.run_start` / `swarm.stage_round` / `swarm.stage_done` /
    `swarm.writeback` / `swarm.run_end`.
  - Records carry `spec_slug` / `cwd` / `run_id` so you can aggregate per
    spec or per project.
  - Size cap defaults to 50 MB (overridable via
    `SPECODE_TELEMETRY_MAX_BYTES`); rotates to `telemetry.jsonl.0` once.
  - All IO errors are swallowed — telemetry never breaks a hook.
- `python3 scripts/spec_state.py telemetry-summary [--days N] [--json]`
  aggregates the telemetry file locally: counts per event, INV violations
  top-list, per-spec phase-transition / violation totals, and task-swarm
  average rounds per converged/failed stage.

### Fixed

- **task-swarm fork description now carries scope** (`[validator-fail-fix]`
  / `[advisory]` / `[re-run]`). Previously a checkpoint stage's r2 coder
  was labelled `阶段 N coder-r2: <title>` with no scope, and the
  orchestrator commonly improvised a description like "修复 N 个 P0" off
  the validator outbox — reading as if the reviewer had triggered a fix
  loop (reviewer is advisory and never does). With scope baked in, the
  Task UI now reads e.g. `阶段 5 coder-r2 [validator-fail-fix]: ...` and
  `commands/task-swarm.md` explicitly tells the orchestrator to copy
  `<json.description>` verbatim.
- **validator agent now forbidden from using P0/P1 severity labels** in
  fix guidance (`agents/task-swarm-validator.md`). Those tags are
  reviewer terminology; validator fail is itself blocking. This prevents
  the orchestrator from observing "(P0)" markers in validator output and
  carrying them into the fork description.

## 0.3.0 (2026-05-18)

### Added

- **Task-Swarm control inversion (CLI-driven orchestrator)**: the main model
  no longer drives the state machine in long context — it now follows a tiny
  `init → loop(next → fork → parse → advance) → writeback → done` protocol
  while all determinism (round counting, convergence, dead-loop detection,
  outbox parsing, tasks.md write-back) lives in Python scripts.
  - New CLI: `scripts/task_swarm.py` with 7 sub-commands
    (`init` / `next` / `parse` / `advance` / `writeback` / `status` / `resolve`).
  - New modules: `task_swarm_state.py` (state machine + round tracking),
    `task_swarm_parse_md.py` (stage-aggregated dispatch from tasks.md),
    `task_swarm_outbox.py` (schema-checked result/review/validation parsing),
    `task_swarm_prompt.py` (pre-rendered subagent prompts), and
    `task_swarm_writeback.py` (line-safe tasks.md edits — checkbox toggle +
    `> ` notes only).
  - Per-run workspace under `.task-swarm/runs/<run_id>/` with predictable
    `agents/stage-N-{role}[-rN]/` layout — easy to inspect, replay, or clean.
- **INV-7 / INV-8 / INV-9 / INV-10** hooks (task-swarm period only):
  - **INV-7** — `Task` calls must use `subagent_type` prefixed with
    `specode:task-swarm-`; otherwise the hook denies (prevents accidental
    fall-back to `general-purpose`, which would bypass tool-whitelist
    isolation).
  - **INV-8** — subagents may only write files declared in their `@writes`
    block (or under their own `outbox/`); any out-of-bound write is denied.
  - **INV-9** — during a task-swarm run, edits to `tasks.md` must go through
    the `writeback` sub-command; direct `Edit`/`Write` is diffed and rejected
    unless the change is purely checkbox toggles + `> ` annotations.
  - **INV-10** — subagent outbox files must pass a schema check (required
    sections, `STATUS:` line, judgment field); `parse` surfaces a
    `schema-error` which the orchestrator handles by retrying instead of
    advancing on garbage.
  - Hook implementation: `scripts/task_swarm_guard.py` + extensions in
    `scripts/spec_guard.py`. Matcher now also covers the `Task` tool.
- **Independent reviewer / validator rounds**: `--reviewer-rounds N` (default
  **1**) and `--validator-rounds N` (default **3**). Rationale —
  reviewer is an LLM reading code (subjective, prone to corrective spirals);
  validator runs commands (objective failure signal, deserves more retries).
  `--max-rounds N` remains as a fallback default for both.
- **Reviewer P0 evidence tags**: every P0 finding must carry one of
  `[req:x.y]` / `[security]` / `[contract]`. Untagged P0s are auto-downgraded
  to `advisory_p0` (logged for audit, but do not count toward `p0_count` and
  do not trigger a coder fix round). If every P0 in a review is untagged, the
  judgment flips from `p0` to `approved` and the stage converges directly.
  This stops cheap-prose reviewers from forcing infinite fix loops.
- Help-output (`/spec -h`) now surfaces `--freeform` and `--strict`.

### Changed

- Reviewer "exit fix loop" behavior is now **advisory**, not hard-block.
  When a reviewer round reports the same P0s as the previous round, the
  orchestrator records the loop signal and stops the stage with a
  `failed` mark via the same loop-detection path as validator, instead
  of a separate hard-stop branch. Keeps loop handling symmetric across
  roles.
- `references/task-swarm.md` rewritten as a **design spec** (why the
  state machine exists, how iron rules are upheld) — the runtime model
  reads `commands/task-swarm.md` for the actual 7-step protocol.
- `agents/task-swarm-reviewer.md` updated to require P0 evidence tags
  and the loop-detection self-report convention.
- `agents/task-swarm-coder.md` clarified the fix-round contract (only
  touch P0-listed locations or validator-fail repair guidance; no scope
  creep).

### Fixed

- Long-context drift on round counting: the main model no longer
  computes round numbers or convergence in prompt — all of that lives
  in `task_swarm_state.py` and is checked by tests.
- Outbox format drift: `task_swarm_outbox.py` validates against a
  schema instead of relying on the main model to "eyeball" review.md /
  validation.md.

### Tests

- 6 new test files (`test_task_swarm_cli.py`, `test_task_swarm_guard.py`,
  `test_task_swarm_hook_integration.py`, `test_task_swarm_outbox.py`,
  `test_task_swarm_parse_md.py`, `test_task_swarm_prompt.py`,
  `test_task_swarm_state.py`). Total suite now **135 tests**.

### Migration

No user action required — `/task-swarm`, `/spec`, `/continue`, `/status`,
`/end` slash commands are unchanged; subagent names are unchanged;
`~/.specode/` state schema is unchanged. The orchestrator protocol is
**internal** to the plugin: the main model loads it from
`commands/task-swarm.md` automatically on plugin update.

If you had pinned `0.2.0`, the only behavior change on bumping to
`0.3.0` is that reviewer P0s without `[req:x.y]` / `[security]` /
`[contract]` evidence tags will no longer trigger coder fix rounds —
they become advisory. Audit logs continue to record them.

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
