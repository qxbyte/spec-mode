"""Local-only telemetry for specode flow events.

Opt-in via env: SPECODE_TELEMETRY ∈ {"on","1","true","yes"} (case-insensitive).
Disabled by default. Events go to ~/.specode/telemetry.jsonl (single
append-only file so `grep` / `jq` stay trivial). Absolutely no remote upload.

Distinct from ~/.specode/audit/ (always-on hook-decision audit). Telemetry
records higher-level workflow events:
  spec.init / spec.phase_transition / spec.end
  inv.violation
  swarm.run_start / swarm.stage_round / swarm.stage_done / swarm.writeback

When the file passes SPECODE_TELEMETRY_MAX_BYTES (default 50 MB), the current
file is renamed to telemetry.jsonl.0 (overwriting any prior .0) and a fresh
file begins. Older .0 contents are discarded — this is best-effort local
analytics, not durable storage.

All write/IO errors are swallowed: telemetry must never break a hook.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SPECODE_DIR = Path.home() / ".specode"
TELEMETRY_FILE = SPECODE_DIR / "telemetry.jsonl"
ROTATED_FILE = SPECODE_DIR / "telemetry.jsonl.0"

DEFAULT_MAX_BYTES = 50 * 1024 * 1024
_ENV_FLAG = "SPECODE_TELEMETRY"
_ENV_MAX = "SPECODE_TELEMETRY_MAX_BYTES"
_ENV_PATH = "SPECODE_TELEMETRY_FILE"

_TRUTHY = {"on", "1", "true", "yes", "y"}


def _env_path() -> Path:
    raw = os.environ.get(_ENV_PATH)
    return Path(raw).expanduser() if raw else TELEMETRY_FILE


def _rotated_for(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".0")


def is_enabled() -> bool:
    return (os.environ.get(_ENV_FLAG) or "").strip().lower() in _TRUTHY


def _max_bytes() -> int:
    raw = os.environ.get(_ENV_MAX)
    if not raw:
        return DEFAULT_MAX_BYTES
    try:
        v = int(raw)
        return v if v > 0 else DEFAULT_MAX_BYTES
    except ValueError:
        return DEFAULT_MAX_BYTES


def _maybe_rotate(path: Path, max_bytes: int) -> None:
    try:
        size = path.stat().st_size
    except OSError:
        return
    if size <= max_bytes:
        return
    try:
        rotated = _rotated_for(path)
        try:
            rotated.unlink()
        except FileNotFoundError:
            pass
        os.replace(path, rotated)
    except OSError:
        pass


def emit(event: str, **fields: Any) -> None:
    """Record one telemetry event. No-op when SPECODE_TELEMETRY is off.

    `event` is a dotted namespace ("spec.init", "swarm.stage_done").
    `fields` is the event payload — keep it small, JSON-serializable.
    Common identity fields like spec_slug / project_root / run_id should be
    passed by the caller so users can grep / aggregate by them.
    """
    if not is_enabled():
        return
    path = _env_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    _maybe_rotate(path, _max_bytes())
    record = {"ts": datetime.now(timezone.utc).isoformat(), "event": event}
    record.update(fields)
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass


def iter_records(path: Path | None = None, include_rotated: bool = True):
    """Yield decoded telemetry records, oldest first.

    When include_rotated, telemetry.jsonl.0 is read before telemetry.jsonl
    so the chronological order is preserved across one rotation boundary.
    """
    target = path or _env_path()
    files: list[Path] = []
    if include_rotated:
        rotated = _rotated_for(target)
        if rotated.exists():
            files.append(rotated)
    if target.exists():
        files.append(target)
    for fp in files:
        try:
            with fp.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue
        except OSError:
            continue
