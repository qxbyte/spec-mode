"""Tests for spec_choice.py — non-interactive selector emitter (post-0.4.0).

The script must never read stdin. timeout failures here = regression.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "spec_choice.py"


def _run(args: list[str], stdin: str | None = None, timeout: float = 3.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        input=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        text=True,
    )


def test_emits_sentinel_and_exits_zero_with_no_stdin():
    proc = _run([
        "--title", "Pick one",
        "--option", "First",
        "--option", "Second",
    ])
    assert proc.returncode == 0
    assert "AWAITING_USER_CHOICE" in proc.stdout
    assert "Pick one" in proc.stdout
    assert "1. First" in proc.stdout
    assert "2. Second" in proc.stdout


def test_does_not_block_on_piped_empty_stdin():
    """CodeBuddy-style: pipe attached but no data. Must NOT hang."""
    proc = _run(
        ["--title", "T", "--option", "A", "--option", "B"],
        stdin="",
    )
    assert proc.returncode == 0
    assert "AWAITING_USER_CHOICE" in proc.stdout


def test_does_not_consume_piped_stdin_data():
    """Even with data on stdin, script must not read it (no input() calls)."""
    proc = _run(
        ["--title", "T", "--option", "A", "--option", "B"],
        stdin="2\nignored garbage\n",
    )
    assert proc.returncode == 0
    assert "AWAITING_USER_CHOICE" in proc.stdout


def test_no_curses_flag_is_noop():
    """--no-curses must still emit cleanly (back-compat for older callers)."""
    proc = _run([
        "--title", "T",
        "--option", "A",
        "--option", "B",
        "--no-curses",
    ])
    assert proc.returncode == 0
    assert "AWAITING_USER_CHOICE" in proc.stdout


def test_recommended_marker_in_output():
    proc = _run([
        "--title", "T",
        "--option", "Alpha::desc::recommended",
        "--option", "Beta",
    ])
    assert proc.returncode == 0
    assert "(Recommended)" in proc.stdout
    assert "desc" in proc.stdout


def test_default_index_overrides_recommended():
    proc = _run([
        "--title", "T",
        "--option", "Alpha::::recommended",
        "--option", "Beta",
        "--option", "Gamma",
        "--default-index", "3",
    ])
    # Default appears in the "Select 1-3 [3]:" prompt line.
    assert "Select 1-3 [3]" in proc.stdout


def test_print_default_short_circuits():
    proc = _run([
        "--title", "x",
        "--option", "Alpha::desc::recommended",
        "--option", "Beta",
        "--print-default",
    ])
    assert proc.returncode == 0
    assert proc.stdout.strip() == "Alpha"
    assert "AWAITING_USER_CHOICE" not in proc.stdout


def test_print_default_explicit_index():
    proc = _run([
        "--title", "x",
        "--option", "Alpha",
        "--option", "Beta",
        "--option", "Gamma",
        "--default-index", "3",
        "--print-default",
    ])
    assert proc.returncode == 0
    assert proc.stdout.strip() == "Gamma"


def test_print_default_json():
    proc = _run([
        "--title", "x",
        "--option", "Alpha::first option::recommended",
        "--option", "Beta",
        "--print-default",
        "--json",
    ])
    assert proc.returncode == 0
    import json
    record = json.loads(proc.stdout.strip())
    assert record["label"] == "Alpha"
    assert record["index"] == 1
    assert record["description"] == "first option"


# Note: static AST-aware check lives in test_no_blocking_io.py — it covers
# spec_choice.py (and every other runtime script) using tokenize so docstring
# mentions of 'input()' don't trigger false positives.
