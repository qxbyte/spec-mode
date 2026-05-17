# Contributing

## Runtime is stdlib-only

The runtime code under `plugins/specode/scripts/` MUST use only the
Python standard library. This is a hard rule, declared in `plugin.json`:

```json
"requires": { "python": ">=3.9", "stdlib_only": true }
```

Reason: plugin users install the plugin via `--plugin-dir`. They don't run a
`pip install -r requirements.txt`. Pulling in third-party packages would
either silently break for users without those packages, or require a heavier
install path that fights the purpose of the plugin.

Tests under `tests/` MAY use `pytest` (it's a dev dependency, not runtime).

## Test conventions

Run the suite from the repo root:

```sh
python3 -m pytest plugins/specode/tests/ -v
```

When adding behavior to `spec_sync.py` or `spec_guard.py`, add:

1. A unit test under `plugins/specode/tests/test_spec_sync.py` for the
   pure function.
2. An integration test under `plugins/specode/tests/test_spec_guard.py`
   exercising the handler path with a fabricated stdin payload through
   `hook_caller`.

Use the `workspace` fixture for handler tests — it creates a tmp spec_dir
+ project_root and monkey-patches `spec_state.find_active_spec` so you don't
need a real Obsidian vault.

## Hook safety contract

Every handler in `spec_guard.py` MUST:

1. Catch all exceptions internally and return 0 from `main()` (the dispatcher
   already wraps handler calls in try/except). Never wedge a user's Claude
   Code session because of a plugin bug.
2. Honor `SPECODE_GUARD=off` for global bypass.
3. Audit log via `_audit()` for any decision that *did work* — silent fast-
   exits when no active spec are deliberately *not* audited (avoid log spam).
4. Use `deny(msg)` (exit 2 + stderr) ONLY for genuine invariant violations
   that the model should react to.

## Performance budget

| Hook | Budget |
|---|---|
| `SessionStart` / `SessionEnd` | <500 ms |
| `UserPromptSubmit` | <80 ms (fires every user turn — keep it cheap) |
| `PreToolUse` / `PostToolUse` | <100 ms |
| `Stop` | <300 ms (allowed slightly larger; runs once per turn) |

If your change crosses these budgets, profile first; don't accept the
regression.

## Sentinel discipline

`~/.specode/.any-active` is the shell-short-circuit sentinel. Maintain
its truth via `spec_state.sync_any_active_sentinel()` — never write it
ad-hoc. If you add a code path that activates or deactivates a spec, call
sync after.

## Decision history

Two non-obvious design calls are encoded in the rules:

- **1A**: freeform mode relaxes INV-1 (file-not-in-tasks check) but does
  NOT exempt INV-2 (turn conservation) or INV-6 (phase gate). Freeform is
  an INV-1 escape hatch, not a full specode bypass.
- **2A**: `implementation-log.md` counts as a doc change for INV-2.
  Cosmetic-doc abuse (one space added to design.md to satisfy INV-2) is
  caught by `spec_lint.py` as a WARNING, not by hook denial.

Change these only via an explicit design-doc decision, not silently.
