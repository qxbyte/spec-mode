#!/usr/bin/env sh
# specode python launcher (POSIX shells: bash / zsh / dash / Git Bash / MSYS).
#
# Probes python3 → python → py (in that order) and execs the first one found,
# forwarding all arguments. Enables cross-platform invocation from hooks.json
# and SKILL/references command samples without hard-coding `python3`.

for cand in python3 python py; do
  if command -v "$cand" >/dev/null 2>&1; then
    if [ "$cand" = "py" ]; then
      exec py -3 "$@"
    fi
    exec "$cand" "$@"
  fi
done

echo "specode: cannot find python interpreter (tried python3, python, py)" >&2
exit 127
