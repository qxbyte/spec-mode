# State probe for the spec-mode plugin's hooks.
#
# Read-only against existing spec-mode artifacts (.active-spec-mode.json,
# <spec-dir>/.config.json) — state authoring stays with spec_session.py /
# spec_init.py, driven by the slash commands. This module only:
#   - discovers the configured document_root
#   - detects whether any spec is currently active (and where)
#   - maintains ~/.spec-mode/.any-active sentinel for hooks.json shell short-circuit
#   - records/clears per-Claude-session metadata under ~/.spec-mode/sessions/
#
# Phase 2 scope. Phase 4 will refine session-id binding (TERM_SESSION_ID -> spec
# session linkage), turn ledger, etc.

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


USER_CONFIG = Path.home() / ".config/spec-mode/config.json"
SPEC_MODE_DIR = Path.home() / ".spec-mode"
SESSIONS_DIR = SPEC_MODE_DIR / "sessions"
ANY_ACTIVE_SENTINEL = SPEC_MODE_DIR / ".any-active"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def get_document_root() -> Optional[Path]:
    cfg = _read_json(USER_CONFIG)
    if not cfg:
        return None
    raw = (
        cfg.get("obsidianRoot")
        or cfg.get("specRoot")
        or cfg.get("documentRoot")
    )
    if not raw:
        vault = cfg.get("vaultPath")
        if vault:
            raw = str(Path(vault) / "spec-in")
    return Path(raw).expanduser() if raw else None


def find_active_spec(prefer_session_id: Optional[str] = None) -> Optional[dict]:
    """Return info about an active spec on this machine, or None.

    Selection order:
      1. Session whose id == prefer_session_id (typically $TERM_SESSION_ID).
      2. Most recently active session by lastActivityAt.
    """
    root = get_document_root()
    if not root or not root.exists():
        return None
    active = _read_json(root / ".active-spec-mode.json")
    if not active:
        return None
    sessions = active.get("sessions") or {}

    candidates = []
    for sid, entry in sessions.items():
        if entry.get("status") != "active":
            continue
        slug = entry.get("specSlug")
        if not slug:
            continue
        candidates.append((sid, entry, root / slug))

    if not candidates:
        return None

    if prefer_session_id:
        for sid, entry, spec_dir in candidates:
            if sid == prefer_session_id:
                return _build_spec_info(sid, entry, spec_dir)

    candidates.sort(key=lambda c: c[1].get("lastActivityAt") or "", reverse=True)
    sid, entry, spec_dir = candidates[0]
    return _build_spec_info(sid, entry, spec_dir)


def _build_spec_info(sid: str, entry: dict, spec_dir: Path) -> dict:
    current_phase = "unknown"
    spec_config = _read_json(spec_dir / ".config.json")
    if spec_config:
        current_phase = spec_config.get("currentPhase") or current_phase
    return {
        "spec_slug": entry.get("specSlug"),
        "spec_dir": str(spec_dir),
        "current_phase": current_phase,
        "session_id": sid,
        "spec_id": entry.get("specId"),
        "last_activity_at": entry.get("lastActivityAt"),
    }


def render_status_block(info: dict) -> str:
    return (
        "=== spec-mode active ===\n"
        f"spec:          {info.get('spec_slug')}\n"
        f"phase:         {info.get('current_phase')}\n"
        f"spec_dir:      {info.get('spec_dir')}\n"
        f"session_id:    {info.get('session_id')}\n"
        f"last activity: {info.get('last_activity_at')}\n"
        "========================="
    )


def sync_any_active_sentinel() -> bool:
    is_active = find_active_spec() is not None
    SPEC_MODE_DIR.mkdir(parents=True, exist_ok=True)
    if is_active:
        ANY_ACTIVE_SENTINEL.touch(exist_ok=True)
    else:
        try:
            ANY_ACTIVE_SENTINEL.unlink()
        except FileNotFoundError:
            pass
    return is_active


def write_claude_session(claude_session_id: str, payload: dict) -> None:
    if not claude_session_id:
        return
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    target = SESSIONS_DIR / f"{claude_session_id}.json"
    record = {
        "claude_session_id": claude_session_id,
        "term_session_id": os.environ.get("TERM_SESSION_ID"),
        "started_at": now_iso(),
        "cwd": payload.get("cwd"),
        "transcript_path": payload.get("transcript_path"),
    }
    target.write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def clear_claude_session(claude_session_id: str) -> None:
    if not claude_session_id:
        return
    target = SESSIONS_DIR / f"{claude_session_id}.json"
    try:
        target.unlink()
    except FileNotFoundError:
        pass


# --- CLI -------------------------------------------------------------------

def _cmd_status(_args: argparse.Namespace) -> int:
    info = find_active_spec(prefer_session_id=os.environ.get("TERM_SESSION_ID"))
    print(json.dumps(info, ensure_ascii=False, indent=2))
    return 0


def _cmd_sync_sentinel(_args: argparse.Namespace) -> int:
    active = sync_any_active_sentinel()
    print(f"any-active: {active}")
    print(f"sentinel:   {'exists' if ANY_ACTIVE_SENTINEL.exists() else 'missing'}")
    return 0


def _cmd_demo_activate(args: argparse.Namespace) -> int:
    """Fabricate an active spec entry under document_root for Phase 2 testing.

    Creates <root>/<slug>/.config.json minimal contents + appends to
    .active-spec-mode.json. Idempotent: re-running updates lastActivityAt.
    """
    root = Path(args.root).expanduser() if args.root else get_document_root()
    if not root:
        print("ERR: no document_root configured (~/.config/spec-mode/config.json missing)", file=sys.stderr)
        return 2
    root.mkdir(parents=True, exist_ok=True)

    slug = args.slug
    spec_dir = root / slug
    spec_dir.mkdir(parents=True, exist_ok=True)

    spec_config_path = spec_dir / ".config.json"
    spec_config = _read_json(spec_config_path) or {}
    spec_config.setdefault("specId", f"demo-{slug}-{int(datetime.now().timestamp())}")
    spec_config["slug"] = slug
    spec_config["currentPhase"] = args.phase
    spec_config_path.write_text(
        json.dumps(spec_config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    active_path = root / ".active-spec-mode.json"
    active = _read_json(active_path) or {
        "version": 2,
        "documentRoot": str(root.resolve()),
        "updatedAt": None,
        "sessions": {},
    }
    sid = args.session or os.environ.get("TERM_SESSION_ID") or "demo-phase-2"
    active["sessions"][sid] = {
        "sessionId": sid,
        "specSlug": slug,
        "specId": spec_config["specId"],
        "status": "active",
        "boundAt": now_iso(),
        "lastActivityAt": now_iso(),
    }
    active["updatedAt"] = now_iso()
    active_path.write_text(
        json.dumps(active, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    sync_any_active_sentinel()
    print(f"✓ demo spec '{slug}' activated under {root}")
    print(f"  spec_dir: {spec_dir}")
    print(f"  session:  {sid}")
    print(f"  phase:    {args.phase}")
    print(f"  sentinel: {'exists' if ANY_ACTIVE_SENTINEL.exists() else 'missing'}")
    return 0


def _cmd_demo_deactivate(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser() if args.root else get_document_root()
    if not root:
        print("ERR: no document_root configured", file=sys.stderr)
        return 2
    active_path = root / ".active-spec-mode.json"
    active = _read_json(active_path)
    if not active:
        print("(no .active-spec-mode.json to clear)")
        sync_any_active_sentinel()
        return 0
    sid = args.session or os.environ.get("TERM_SESSION_ID") or "demo-phase-2"
    sessions = active.get("sessions") or {}
    if sid in sessions:
        sessions[sid]["status"] = "ended"
        sessions[sid]["lastActivityAt"] = now_iso()
        active["updatedAt"] = now_iso()
        active_path.write_text(
            json.dumps(active, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"✓ session '{sid}' marked ended")
    else:
        print(f"(session '{sid}' not found in active map)")
    sync_any_active_sentinel()
    print(f"  sentinel: {'exists' if ANY_ACTIVE_SENTINEL.exists() else 'missing'}")
    return 0


def main(argv) -> int:
    p = argparse.ArgumentParser(prog="spec_state.py")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="Print active-spec info (JSON) or null")
    sub.add_parser("sync-sentinel", help="Re-sync ~/.spec-mode/.any-active to current state")
    sp = sub.add_parser("demo-activate", help="(testing) Mark a fake spec as active")
    sp.add_argument("--slug", default="demo-phase-2")
    sp.add_argument("--phase", default="implementation")
    sp.add_argument("--root")
    sp.add_argument("--session")
    sd = sub.add_parser("demo-deactivate", help="(testing) End the fake spec session")
    sd.add_argument("--root")
    sd.add_argument("--session")
    args = p.parse_args(argv)
    return {
        "status": _cmd_status,
        "sync-sentinel": _cmd_sync_sentinel,
        "demo-activate": _cmd_demo_activate,
        "demo-deactivate": _cmd_demo_deactivate,
    }[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
