#!/bin/sh
# Local end-to-end verification helper for spec-mode-plugin.
#
# Usage:
#   sh scripts/verify_local.sh setup     # set up demo state for testing
#   sh scripts/verify_local.sh status    # show what's currently active
#   sh scripts/verify_local.sh teardown  # clean up demo state
#   sh scripts/verify_local.sh tail      # tail today's audit log

set -e

PLUGIN_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TODAY=$(date -u +%Y-%m-%d)
AUDIT="$HOME/.spec-mode/audit/$TODAY.log"

case "${1:-help}" in
  setup)
    python3 "$PLUGIN_DIR/scripts/spec_state.py" demo-activate \
      --slug demo-verify --phase implementation
    echo ""
    echo "Now start Claude Code with the plugin:"
    echo "  claude --plugin-dir $PLUGIN_DIR"
    echo ""
    echo "And in another terminal tail the audit log:"
    echo "  mkdir -p ~/.spec-mode/audit && touch $AUDIT && tail -f $AUDIT"
    ;;
  status)
    python3 "$PLUGIN_DIR/scripts/spec_state.py" status
    echo ""
    python3 "$PLUGIN_DIR/scripts/spec_sync.py" status 2>/dev/null || true
    echo ""
    if [ -e "$HOME/.spec-mode/.any-active" ]; then
      echo "sentinel: present ($HOME/.spec-mode/.any-active)"
    else
      echo "sentinel: missing (hooks will short-circuit)"
    fi
    ;;
  teardown)
    python3 "$PLUGIN_DIR/scripts/spec_state.py" demo-deactivate \
      --session "${TERM_SESSION_ID:-demo-verify}" 2>/dev/null || true
    # Remove the demo spec dir if it lives under document_root
    ROOT=$(python3 -c "import sys; sys.path.insert(0,'$PLUGIN_DIR/scripts'); import spec_state; r=spec_state.get_document_root(); print(r or '')")
    if [ -n "$ROOT" ] && [ -d "$ROOT/demo-verify" ]; then
      rm -rf "$ROOT/demo-verify"
      echo "✓ removed $ROOT/demo-verify"
    fi
    python3 "$PLUGIN_DIR/scripts/spec_state.py" sync-sentinel
    ;;
  tail)
    mkdir -p "$HOME/.spec-mode/audit"
    touch "$AUDIT"
    tail -f "$AUDIT"
    ;;
  *)
    cat <<USAGE
spec-mode-plugin local verification helper.

Commands:
  setup      Create a demo spec under document_root and activate it.
             After running this, start Claude Code with --plugin-dir to see
             hooks fire against the active demo.
  status     Print active-spec info, ledger summary, and sentinel state.
  tail       Tail today's audit log at ~/.spec-mode/audit/<date>.log
  teardown   Deactivate the demo spec and remove the spec dir.

USAGE
    ;;
esac
