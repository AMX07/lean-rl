"""Anti-cheat filtering for candidate Lean proofs.

Per the build plan (section 6, "Reward and anti-cheat policy") the environment must
reject forbidden constructs *before* spending an AXLE call. A proof that uses any of
these scores 0.0 with a ``forbidden`` metric and the Lean checker is never invoked.

This module is pure / offline so it can be unit-tested without network access.
"""

from __future__ import annotations

import re

from .lean_source import strip_lean_comments_and_strings

# Imports always allowed in addition to whatever the task declares.
DEFAULT_ALLOWED_IMPORTS = frozenset({"Mathlib", "Init", "Std", "Aesop"})

# Heartbeat ceiling. Above this we treat `set_option maxHeartbeats` as an attempt to
# brute-force / hang the checker.
MAX_HEARTBEATS = 1_000_000

# Word-boundary patterns for forbidden tokens. Each maps to a short reason code.
_FORBIDDEN_TOKENS: dict[str, str] = {
    r"\bsorry\b": "sorry",
    r"\badmit\b": "admit",
    r"\bunsafe\b": "unsafe",
    # Soundness escapes: trust the compiler / reduce Bool natively (can be exploited and
    # is not kernel-checked) — these prove things outside the trusted core.
    r"\bnative_decide\b": "native_decide",
    r"\bLean\.ofReduceBool\b": "ofReduceBool",
    r"\bofReduceBool\b": "ofReduceBool",
    # Metaprogramming / elaboration escapes that can fabricate a "proof".
    r"\bmacro_rules\b": "metaprogramming",
    r"\brun_cmd\b": "metaprogramming",
    r"\belab\b": "metaprogramming",
    r"\bimplemented_by\b": "metaprogramming",
    r"\binit_quot\b": "metaprogramming",
    r"\bextern\b": "metaprogramming",
}

# A local `axiom` declaration lets the model assume arbitrary (possibly false)
# propositions, which undermines the proof. Match a declaration, not the substring.
_AXIOM_DECL = re.compile(r"(?:^|\n)\s*axiom\s+\w", re.MULTILINE)

_IMPORT_LINE = re.compile(r"(?:^|\n)\s*import\s+([\w.]+)", re.MULTILINE)

_SET_HEARTBEATS = re.compile(r"set_option\s+maxHeartbeats\s+(\d+)")


def check_forbidden(code: str, allowed_imports: frozenset[str] | set[str] | None = None) -> list[str]:
    """Return a list of reason codes for every forbidden construct found.

    An empty list means the code passed all anti-cheat checks. The full constructed
    Lean file (imports + statement + proof) should be passed in.
    """
    code = strip_lean_comments_and_strings(code)
    allowed = set(DEFAULT_ALLOWED_IMPORTS)
    if allowed_imports:
        allowed |= set(allowed_imports)

    reasons: list[str] = []

    for pattern, reason in _FORBIDDEN_TOKENS.items():
        if re.search(pattern, code):
            reasons.append(reason)

    if _AXIOM_DECL.search(code):
        reasons.append("local_axiom")

    for match in _SET_HEARTBEATS.finditer(code):
        if int(match.group(1)) > MAX_HEARTBEATS:
            reasons.append("max_heartbeats")
            break

    for match in _IMPORT_LINE.finditer(code):
        # Allow an import if its top-level namespace is allowlisted (e.g. `Mathlib.Tactic`).
        top = match.group(1).split(".", 1)[0]
        if top not in allowed:
            reasons.append(f"unknown_import:{match.group(1)}")

    return reasons


def is_clean(code: str, allowed_imports: frozenset[str] | set[str] | None = None) -> bool:
    """Convenience boolean wrapper around :func:`check_forbidden`."""
    return not check_forbidden(code, allowed_imports)
