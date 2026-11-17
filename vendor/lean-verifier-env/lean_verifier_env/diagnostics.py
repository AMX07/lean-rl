"""Lean diagnostic normalization + classification (v1).

Build plan v1 scope: "include Lean error messages as feedback; add partial metrics".
Risks table: "Huge diagnostics -> truncate and normalize Lean errors before feedback".

Two jobs:
- :func:`normalize_errors` — collapse/truncate raw AXLE error strings so feedback and
  logs stay compact and bounded.
- :func:`classify` — derive the partial metrics ``syntax_ok`` and ``typecheck_ok`` from
  the error messages. These give a gradient (``syntax_ok >= typecheck_ok >= verified``)
  for monitoring; they are never the reward.
"""

from __future__ import annotations

import re

MAX_MESSAGES = 5
MAX_MESSAGE_CHARS = 400
MAX_TOTAL_CHARS = 2000

# Parse/syntax-level failures: the code did not even parse.
_SYNTAX_MARKERS = (
    "unexpected token",
    "unexpected identifier",
    "unexpected end of input",
    "expected '",
    "expected term",
    "expected command",
    "syntax error",
    "parse error",
    "unterminated",
)

# Elaboration/type errors: it parsed but did not type-check. Note that "unsolved goals"
# is deliberately excluded — that means the code type-checks but the proof is incomplete.
_TYPE_MARKERS = (
    "type mismatch",
    "application type mismatch",
    "unknown identifier",
    "unknown constant",
    "unknown namespace",
    "function expected",
    "failed to synthesize",
    "invalid field",
)

_WS = re.compile(r"\s+")


def normalize_errors(errors: list[str]) -> str:
    """Collapse whitespace, truncate each message, and cap the total length."""
    cleaned: list[str] = []
    for msg in errors[:MAX_MESSAGES]:
        flat = _WS.sub(" ", str(msg)).strip()
        if len(flat) > MAX_MESSAGE_CHARS:
            flat = flat[:MAX_MESSAGE_CHARS] + " …[truncated]"
        if flat:
            cleaned.append(flat)
    if len(errors) > MAX_MESSAGES:
        cleaned.append(f"…[{len(errors) - MAX_MESSAGES} more]")
    joined = "\n".join(cleaned)
    if len(joined) > MAX_TOTAL_CHARS:
        joined = joined[:MAX_TOTAL_CHARS] + " …[truncated]"
    return joined


def _matches(errors: list[str], markers: tuple[str, ...]) -> bool:
    blob = "\n".join(str(e) for e in errors).lower()
    return any(marker in blob for marker in markers)


def classify(okay: bool, errors: list[str]) -> tuple[float, float]:
    """Return ``(syntax_ok, typecheck_ok)`` partial metrics.

    A verified proof scores 1.0 on both. Otherwise the error text is inspected:
    a parse error zeroes both; a type error zeroes only ``typecheck_ok``; anything
    else (e.g. unsolved goals) leaves both at 1.0 since the code parsed and elaborated.
    """
    if okay:
        return 1.0, 1.0
    # Type markers are unambiguous; check them before the broader syntax markers so a
    # "type mismatch: expected ..." message is not misread as a parse error.
    if _matches(errors, _TYPE_MARKERS):
        return 1.0, 0.0
    if _matches(errors, _SYNTAX_MARKERS):
        return 0.0, 0.0
    return 1.0, 1.0


def proof_progress(raw: dict | None, header_lines: int, proof_lines: int,
                   syntax_ok: float, typecheck_ok: float) -> float:
    """Dense [0,1] signal: how far the proof elaborated before its first error.

    Uses Lean error line positions (from the repl ``raw`` messages) — the fraction of
    proof lines that parsed/elaborated before the earliest error. Falls back to the
    syntax/typecheck gradient when positions aren't available (e.g. the AXLE backend).
    This mirrors the "process-verified RL" dense credit signal.
    """
    proof_lines = max(1, proof_lines)
    if raw:
        positions = [
            m["pos"]["line"]
            for m in (raw.get("messages") or [])
            if m.get("severity") == "error" and isinstance(m.get("pos"), dict) and "line" in m["pos"]
        ]
        if positions:
            done = min(positions) - 1 - header_lines  # proof lines before the first error
            return max(0.0, min(1.0, done / proof_lines))
    # No positional info: coarse fallback.
    return 0.5 * typecheck_ok + 0.2 * syntax_ok
