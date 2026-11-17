"""Verification backend seam.

The reward path talks to a single ``Checker`` object with ``check`` / ``verify_proof``.
Two backends implement it: :class:`~lean_verifier_env.axiom_client.AxleClient` (hosted
AXLE, capped at 20 concurrent requests) and
:class:`~lean_verifier_env.local_lean.LocalLeanChecker` (local Lean 4 + Mathlib REPL,
parallelism bounded by CPU instead of a rate limit). Keeping the result type and the
protocol here lets both backends import them without a circular dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class CheckResult:
    """Outcome of a backend ``check`` / ``verify_proof`` call."""

    okay: bool
    errors: list[str] = field(default_factory=list)
    timed_out: bool = False
    raw: dict | None = None


@runtime_checkable
class Checker(Protocol):
    """Protocol every verification backend implements."""

    async def check(self, code: str, environment: str, timeout_s: float | None = None) -> CheckResult:
        """Check a full Lean file for errors."""
        ...

    async def verify_proof(
        self, statement: str, proof: str, environment: str, timeout_s: float | None = None
    ) -> CheckResult:
        """Validate a candidate ``proof`` against a ``statement``."""
        ...
