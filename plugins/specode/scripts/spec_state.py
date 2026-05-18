# State probe for the specode plugin's hooks.
#
# Read-only against existing specode artifacts (.active-specode.json,
# <spec-dir>/.config.json) — state authoring stays with spec_session.py /
# spec_init.py, driven by the slash commands. This module only:
#   - discovers the configured document_root
#   - detects whether any spec is currently active (and where)
#   - maintains ~/.specode/.any-active sentinel for hooks.json shell short-circuit
#   - records/clears per-Claude-session metadata under ~/.specode/sessions/
#
# Phase 2 scope. Phase 4 will refine session-id binding (TERM_SESSION_ID -> spec
# session linkage), turn ledger, etc.

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import spec_telemetry  # noqa: E402


USER_CONFIG = Path.home() / ".config/specode/config.json"
SPECODE_DIR = Path.home() / ".specode"
SESSIONS_DIR = SPECODE_DIR / "sessions"
ANY_ACTIVE_SENTINEL = SPECODE_DIR / ".any-active"
AUDIT_DIR = Path(
    os.environ.get("SPECODE_AUDIT_DIR")
    or SPECODE_DIR / "audit"
)


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
    active = _read_json(root / ".active-specode.json")
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

    # Mirror spec_session.normalize_session_id's fallback: when no env-derived
    # id is provided, the writer used "default" — so look that up first before
    # falling back to lastActivityAt ordering.
    lookup_sids: list[str] = []
    if prefer_session_id:
        lookup_sids.append(prefer_session_id)
    else:
        lookup_sids.append("default")
    for target in lookup_sids:
        for sid, entry, spec_dir in candidates:
            if sid == target:
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
        "=== specode active ===\n"
        f"spec:          {info.get('spec_slug')}\n"
        f"phase:         {info.get('current_phase')}\n"
        f"spec_dir:      {info.get('spec_dir')}\n"
        f"session_id:    {info.get('session_id')}\n"
        f"last activity: {info.get('last_activity_at')}\n"
        "========================="
    )


def sync_any_active_sentinel() -> bool:
    is_active = find_active_spec() is not None
    SPECODE_DIR.mkdir(parents=True, exist_ok=True)
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


# --- audit log readers ------------------------------------------------------

def _audit_log_for(date: Optional[str]) -> Path:
    d = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return AUDIT_DIR / f"{d}.log"


def _fmt_audit_line(rec: dict) -> str:
    ts = rec.get("ts") or ""
    event = rec.get("event") or ""
    decision = rec.get("decision") or ""
    tool = rec.get("tool") or "-"
    msg = rec.get("msg") or ""
    return f"{ts}  {event:<18} {decision:<24} {tool:<10} {msg}"


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
    .active-specode.json. Idempotent: re-running updates lastActivityAt.
    """
    root = Path(args.root).expanduser() if args.root else get_document_root()
    if not root:
        print("ERR: no document_root configured (~/.config/specode/config.json missing)", file=sys.stderr)
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

    active_path = root / ".active-specode.json"
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
    active_path = root / ".active-specode.json"
    active = _read_json(active_path)
    if not active:
        print("(no .active-specode.json to clear)")
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


def _cmd_audit_tail(args: argparse.Namespace) -> int:
    path = _audit_log_for(args.date)
    if not path.exists():
        print(f"(no audit log at {path})", file=sys.stderr)
        return 0
    raw = "json" if args.json else "text"
    with path.open("r", encoding="utf-8") as f:
        lines = f.readlines()
    for line in lines[-args.n:]:
        line = line.rstrip("\n")
        if not line:
            continue
        if raw == "json":
            print(line)
            continue
        try:
            print(_fmt_audit_line(json.loads(line)))
        except json.JSONDecodeError:
            print(line)
    if not args.follow:
        return 0
    with path.open("r", encoding="utf-8") as f:
        f.seek(0, 2)
        try:
            while True:
                line = f.readline()
                if not line:
                    time.sleep(0.5)
                    continue
                line = line.rstrip("\n")
                if raw == "json":
                    print(line, flush=True)
                else:
                    try:
                        print(_fmt_audit_line(json.loads(line)), flush=True)
                    except json.JSONDecodeError:
                        print(line, flush=True)
        except KeyboardInterrupt:
            return 0


def _cmd_audit_summary(args: argparse.Namespace) -> int:
    if not AUDIT_DIR.exists():
        print(f"(no audit dir at {AUDIT_DIR})", file=sys.stderr)
        return 0
    files = sorted(AUDIT_DIR.glob("*.log"))
    if args.days and args.days > 0:
        files = files[-args.days:]
    by_event: dict[str, int] = {}
    by_decision: dict[str, int] = {}
    deny_lines: list[str] = []
    total = 0
    for fp in files:
        try:
            content = fp.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in content.splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            total += 1
            ev = rec.get("event") or "?"
            dec = rec.get("decision") or "?"
            by_event[ev] = by_event.get(ev, 0) + 1
            by_decision[dec] = by_decision.get(dec, 0) + 1
            if dec.startswith("deny") and len(deny_lines) < args.show_deny:
                deny_lines.append(_fmt_audit_line(rec))
    print(f"audit summary: {total} records across {len(files)} log file(s)")
    if files:
        print(f"  range: {files[0].stem} → {files[-1].stem}")
    print("\nby event:")
    for k, v in sorted(by_event.items(), key=lambda x: (-x[1], x[0])):
        print(f"  {v:7d}  {k}")
    print("\nby decision:")
    for k, v in sorted(by_decision.items(), key=lambda x: (-x[1], x[0])):
        print(f"  {v:7d}  {k}")
    if deny_lines:
        print(f"\nrecent denies (up to {args.show_deny}):")
        for line in deny_lines[-args.show_deny:]:
            print(f"  {line}")
    return 0


def _cmd_telemetry_summary(args: argparse.Namespace) -> int:
    if not spec_telemetry.is_enabled() and not args.force:
        print(
            f"(telemetry is disabled — set {spec_telemetry._ENV_FLAG}=on to record events,\n"
            f" or pass --force to read the existing file anyway)",
            file=sys.stderr,
        )
    path = spec_telemetry._env_path()
    rotated = spec_telemetry._rotated_for(path)
    if not path.exists() and not rotated.exists():
        print(f"(no telemetry file at {path})", file=sys.stderr)
        return 0

    cutoff: Optional[datetime] = None
    if args.days and args.days > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)

    by_event: dict[str, int] = {}
    by_inv: dict[str, int] = {}
    spec_phase_transitions: dict[str, int] = {}
    spec_inv_counts: dict[str, int] = {}
    swarm_runs: dict[str, dict] = {}
    total = 0

    for rec in spec_telemetry.iter_records(path):
        if cutoff is not None:
            try:
                ts = datetime.fromisoformat(rec.get("ts", ""))
            except ValueError:
                ts = None
            if ts is None or ts < cutoff:
                continue
        total += 1
        ev = rec.get("event", "?")
        by_event[ev] = by_event.get(ev, 0) + 1

        if ev == "spec.phase_transition":
            slug = rec.get("spec_slug") or "?"
            spec_phase_transitions[slug] = spec_phase_transitions.get(slug, 0) + 1
        elif ev == "inv.violation":
            inv = rec.get("inv") or "?"
            by_inv[inv] = by_inv.get(inv, 0) + 1
            slug = rec.get("spec_slug") or "?"
            spec_inv_counts[slug] = spec_inv_counts.get(slug, 0) + 1
        elif ev == "swarm.run_start":
            rid = rec.get("run_id") or "?"
            swarm_runs.setdefault(rid, {})["start"] = rec
        elif ev == "swarm.stage_done":
            rid = rec.get("run_id") or "?"
            run = swarm_runs.setdefault(rid, {})
            run.setdefault("stages_done", []).append(rec)
        elif ev == "swarm.run_end":
            rid = rec.get("run_id") or "?"
            swarm_runs.setdefault(rid, {})["end"] = rec

    if args.json:
        rounds_per_stage: list[int] = []
        for run in swarm_runs.values():
            for s in run.get("stages_done") or []:
                rounds = s.get("rounds") or {}
                total_rounds = sum(v for v in rounds.values() if isinstance(v, int))
                if total_rounds:
                    rounds_per_stage.append(total_rounds)
        avg = (sum(rounds_per_stage) / len(rounds_per_stage)) if rounds_per_stage else 0
        print(json.dumps({
            "total_records": total,
            "by_event": by_event,
            "by_inv": by_inv,
            "phase_transitions_by_spec": spec_phase_transitions,
            "inv_violations_by_spec": spec_inv_counts,
            "swarm_runs": len(swarm_runs),
            "swarm_avg_total_rounds_per_stage": round(avg, 2),
        }, ensure_ascii=False, indent=2))
        return 0

    print(f"telemetry summary: {total} record(s) from {path}")
    if cutoff is not None:
        print(f"  window: last {args.days} day(s)")
    print("\nby event:")
    for k, v in sorted(by_event.items(), key=lambda x: (-x[1], x[0])):
        print(f"  {v:6d}  {k}")
    if by_inv:
        print("\ninvariant violations (top):")
        for k, v in sorted(by_inv.items(), key=lambda x: (-x[1], x[0])):
            print(f"  {v:6d}  {k}")
    if spec_phase_transitions:
        print("\nphase transitions per spec (top 10):")
        ranked = sorted(spec_phase_transitions.items(), key=lambda x: (-x[1], x[0]))[:10]
        for slug, count in ranked:
            print(f"  {count:6d}  {slug}")
    if spec_inv_counts:
        print("\ninv violations per spec (top 10):")
        ranked = sorted(spec_inv_counts.items(), key=lambda x: (-x[1], x[0]))[:10]
        for slug, count in ranked:
            print(f"  {count:6d}  {slug}")
    if swarm_runs:
        rounds_per_stage: list[int] = []
        for run in swarm_runs.values():
            for s in run.get("stages_done") or []:
                rounds = s.get("rounds") or {}
                total_rounds = sum(v for v in rounds.values() if isinstance(v, int))
                if total_rounds:
                    rounds_per_stage.append(total_rounds)
        avg = (sum(rounds_per_stage) / len(rounds_per_stage)) if rounds_per_stage else 0
        print(f"\ntask-swarm: {len(swarm_runs)} run(s)")
        print(f"  avg total rounds per converged/failed stage: {avg:.2f}")
        print(f"  stages with recorded rounds: {len(rounds_per_stage)}")
    return 0


def main(argv) -> int:
    p = argparse.ArgumentParser(prog="spec_state.py")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="Print active-spec info (JSON) or null")
    sub.add_parser("sync-sentinel", help="Re-sync ~/.specode/.any-active to current state")
    sp = sub.add_parser("demo-activate", help="(testing) Mark a fake spec as active")
    sp.add_argument("--slug", default="demo-phase-2")
    sp.add_argument("--phase", default="implementation")
    sp.add_argument("--root")
    sp.add_argument("--session")
    sd = sub.add_parser("demo-deactivate", help="(testing) End the fake spec session")
    sd.add_argument("--root")
    sd.add_argument("--session")
    at = sub.add_parser("audit-tail", help="Pretty-print the last N lines of an audit log")
    at.add_argument("-n", type=int, default=50, help="lines to show (default 50)")
    at.add_argument("--date", help="YYYY-MM-DD UTC; default today")
    at.add_argument("--follow", action="store_true", help="keep streaming new entries")
    at.add_argument("--json", action="store_true", help="output raw JSON lines")
    asum = sub.add_parser("audit-summary", help="Aggregate event/decision counts")
    asum.add_argument("--days", type=int, default=7, help="how many most-recent daily logs to scan (default 7; 0=all)")
    asum.add_argument("--show-deny", type=int, default=10, help="how many recent deny entries to include (default 10)")
    tsum = sub.add_parser("telemetry-summary", help="Aggregate ~/.specode/telemetry.jsonl (opt-in)")
    tsum.add_argument("--days", type=int, default=0, help="restrict to last N days (default 0=all)")
    tsum.add_argument("--json", action="store_true", help="emit raw aggregates as JSON")
    tsum.add_argument("--force", action="store_true", help="read the file even if telemetry is disabled")
    args = p.parse_args(argv)
    return {
        "status": _cmd_status,
        "sync-sentinel": _cmd_sync_sentinel,
        "demo-activate": _cmd_demo_activate,
        "demo-deactivate": _cmd_demo_deactivate,
        "audit-tail": _cmd_audit_tail,
        "audit-summary": _cmd_audit_summary,
        "telemetry-summary": _cmd_telemetry_summary,
    }[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
