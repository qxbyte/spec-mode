"""Static guard: no specode runtime script may contain a blocking stdin read.

`input()` / `sys.stdin.read*()` / `getpass.getpass()` all block under a CLI
agent harness (no TTY), causing the kind of multi-minute zombie processes
documented in CHANGELOG 0.4.0. To prevent regressions, every Python file
under scripts/ is grepped for these tokens; any match fails CI.

Whitelist: add a line  `# stdin-block: <reason>`  immediately above the
forbidden call in the source if you have a genuinely TTY-only utility that
should never run under an agent.
"""
from __future__ import annotations

import io
import re
import tokenize
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"

FORBIDDEN_PATTERNS = [
    re.compile(r"\binput\s*\("),
    re.compile(r"\braw_input\s*\("),
    re.compile(r"\bsys\.stdin\.read\b"),
    re.compile(r"\bsys\.stdin\.readline\b"),
    re.compile(r"\bsys\.stdin\.readlines\b"),
    re.compile(r"\bgetpass\.getpass\s*\("),
]

WHITELIST_MARKER = "# stdin-block:"


def _code_lines_only(path: Path) -> dict[int, str]:
    """Return {lineno: line_text} for lines containing real code tokens.

    Uses tokenize to skip comments, docstrings, and other string literals so
    documentation that mentions 'input()' or 'sys.stdin.read' doesn't trip
    the scanner.
    """
    src = path.read_text(encoding="utf-8")
    raw_lines = src.splitlines()
    code_linenos: set[int] = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type in (tokenize.NAME, tokenize.OP, tokenize.NUMBER):
                code_linenos.add(tok.start[0])
    except tokenize.TokenizeError:
        # If tokenize chokes, fall back to "every line is code" — safer
        # than missing a real offender.
        return {i + 1: line for i, line in enumerate(raw_lines)}
    return {lineno: raw_lines[lineno - 1] for lineno in code_linenos if lineno - 1 < len(raw_lines)}


def _scan_file(path: Path) -> list[tuple[int, str]]:
    """Return [(lineno, line)] of forbidden hits not preceded by whitelist marker."""
    hits: list[tuple[int, str]] = []
    code_lines = _code_lines_only(path)
    raw_lines = path.read_text(encoding="utf-8").splitlines()
    for lineno in sorted(code_lines):
        line = code_lines[lineno]
        for pat in FORBIDDEN_PATTERNS:
            if pat.search(line):
                # Whitelist: preceding non-blank source line is a marker.
                prev_idx = lineno - 2
                while prev_idx >= 0 and not raw_lines[prev_idx].strip():
                    prev_idx -= 1
                if prev_idx >= 0 and raw_lines[prev_idx].strip().startswith(WHITELIST_MARKER):
                    continue
                hits.append((lineno, line.rstrip()))
                break
    return hits


def test_no_runtime_script_blocks_on_stdin():
    offenders: dict[str, list[tuple[int, str]]] = {}
    for py in sorted(SCRIPTS_DIR.glob("*.py")):
        if py.name.startswith("test_"):
            continue
        hits = _scan_file(py)
        if hits:
            offenders[py.name] = hits

    if offenders:
        msg_lines = [
            "Found blocking stdin reads in runtime scripts — these will hang under CLI agent harnesses.",
            "Either remove the call, or add a `# stdin-block: <reason>` marker on the line directly above.",
            "",
        ]
        for fname, hits in offenders.items():
            msg_lines.append(f"  {fname}:")
            for lineno, line in hits:
                msg_lines.append(f"    L{lineno}: {line}")
        raise AssertionError("\n".join(msg_lines))


def test_scanner_self_check_detects_input_call(tmp_path):
    """Sanity: the scanner actually catches a forbidden pattern in a fixture."""
    bad = tmp_path / "bad.py"
    bad.write_text("def main():\n    x = input('hi')\n    return x\n", encoding="utf-8")
    hits = _scan_file(bad)
    assert len(hits) == 1
    assert hits[0][0] == 2


def test_scanner_respects_whitelist_marker(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text(
        "def main():\n"
        "    # stdin-block: this util only runs under interactive CLI testing\n"
        "    x = input('hi')\n"
        "    return x\n",
        encoding="utf-8",
    )
    hits = _scan_file(bad)
    assert hits == []
