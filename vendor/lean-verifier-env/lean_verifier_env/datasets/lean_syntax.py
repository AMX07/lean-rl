"""Modernize Lean 4 statements for a newer Mathlib toolchain.

Benchmark datasets (ProofNet, PutnamBench, miniF2F) were formalized on older Mathlib,
so a chunk of their statements no longer parse under our pinned toolchain — most commonly
the deprecated big-operator binder syntax ``∑ x in s`` (now ``∑ x ∈ s``). This rewrites
the known-safe deprecations so the statements parse, making the benchmark fair instead of
silently scoring those tasks 0 for everyone.
"""

from __future__ import annotations

import re

# Big-operator / set-builder binders that switched `in` -> `∈`.
_BIGOPS = "∑∏⨆⨅⋃⋂⨁⨂"
# `<op> <binder...> in <set>` -> `<op> <binder...> ∈ <set>`. The binder part stops at the
# first ` in ` (non-greedy) and must not itself contain a comma (which ends the binder).
_IN_BINDER = re.compile(rf"([{_BIGOPS}]\s*[^,\n]*?)\s+in\s+")

# Small, conservative lemma/identifier renames (extend as needed; only unambiguous ones).
_RENAMES = {
    r"\bNat\.coprime\b": "Nat.Coprime",
    r"\bnat\.coprime\b": "Nat.Coprime",
}


def modernize(stmt: str) -> str:
    """Rewrite known-deprecated Lean syntax so the statement parses on newer Mathlib."""
    # Repeatedly fix big-operator `in` (handles nested operators).
    prev = None
    out = stmt
    while prev != out:
        prev = out
        out = _IN_BINDER.sub(r"\1 ∈ ", out)
    for pat, repl in _RENAMES.items():
        out = re.sub(pat, repl, out)
    return out
