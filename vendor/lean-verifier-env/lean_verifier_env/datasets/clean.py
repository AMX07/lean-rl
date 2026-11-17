"""Dataset filtering + rechecking pipeline (build plan section 8).

This is the scaffold for turning raw candidate task pools (Lean Workbook, miniF2F,
ProofNet) into trusted, version-pinned tasks. v0 only exercises the offline filtering
steps; the AXLE rechecking step (5-6) is wired to the same :class:`AxleClient` used by
the reward and is intentionally left as the place where later versions plug in.

Pipeline per row:

    1. normalize into the shared JSONL schema
    2. pin Lean/Mathlib/AXLE version metadata
    3. reject unknown imports unless allowlisted
    4. reject forbidden constructs (sorry/admit/local axiom/unsafe/metaprogramming)
    5. (later) run AXLE check on imports + statement
    6. (later) if a known proof exists, verify it in the pinned environment

Cleaned rows are written to ``cleaned/*.jsonl``; rejected rows (with a reason) go to
``rejected_tasks.jsonl`` for debugging.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from ..filters import check_forbidden
from .generate import estimate_difficulty

DEFAULT_ENVIRONMENT = "lean-4.28.0"


@dataclass
class CleanReport:
    cleaned: list[dict] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)


@dataclass
class RecheckReport:
    verified: list[dict] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)


async def recheck_rows(rows: list[dict], checker, max_concurrent: int = 8) -> RecheckReport:
    """Run a ``Checker`` over rows' reference proofs (build plan step 6); keep only those
    that actually verify in our pinned Lean/Mathlib. Drops rows whose proof fails (e.g.
    Lean Workbook proofs that broke under a newer Mathlib), so training tasks are known
    solvable. Concurrency is bounded by the checker's own pool; ``max_concurrent`` adds a
    semaphore for safety."""
    from ..reward import build_lean_file

    report = RecheckReport()
    sem = asyncio.Semaphore(max_concurrent)

    async def check_one(row: dict):
        code = build_lean_file(row.get("imports", ["Mathlib"]), row["formal_statement"], row.get("proof", ""))
        async with sem:
            result = await checker.check(
                code, environment=row.get("environment", DEFAULT_ENVIRONMENT),
                timeout_s=row.get("timeout_s", 30),
            )
        if result.okay:
            report.verified.append(row)
        else:
            report.failed.append({**row, "lean_error": (result.errors[:1] or [""])[0][:200]})

    await asyncio.gather(*(check_one(r) for r in rows))
    return report


async def filter_parseable_rows(rows: list[dict], checker, max_concurrent: int = 8) -> list[dict]:
    """Keep only rows whose *statement* parses+typechecks in our pinned Lean/Mathlib.

    For eval benchmarks (no reference proof) — checks ``statement`` + ``sorry`` and keeps
    rows whose only error is the sorry hole. Drops statements that don't parse (older-Mathlib
    drift: renamed lemmas, missing instances), so the benchmark contains only fair, solvable
    tasks and pass rates aren't diluted by dead statements."""
    from ..reward import build_lean_file

    sem = asyncio.Semaphore(max_concurrent)
    kept: list[dict] = []

    async def check_one(row: dict):
        code = build_lean_file(row.get("imports", ["Mathlib"]), row["formal_statement"], "sorry")
        async with sem:
            result = await checker.check(
                code, environment=row.get("environment", DEFAULT_ENVIRONMENT),
                timeout_s=row.get("timeout_s", 30),
            )
        hard = [e for e in result.errors if "sorry" not in e.lower()]
        if not hard:
            kept.append(row)

    await asyncio.gather(*(check_one(r) for r in rows))
    return kept


def normalize(row: dict) -> dict:
    """Coerce a raw row into the shared schema with pinned version metadata (steps 1-2).

    A missing/unknown difficulty is filled with the heuristic curriculum bucket.
    """
    statement = row.get("formal_statement", "")
    difficulty = row.get("difficulty")
    if difficulty in (None, "", "unknown"):
        difficulty = estimate_difficulty(statement, row.get("proof", "") or "")
    return {
        "id": row.get("id"),
        "source": row.get("source", "unknown"),
        "nl_statement": row.get("nl_statement", ""),
        "imports": row.get("imports") or ["Mathlib"],
        "formal_statement": statement,
        "proof": row.get("proof", ""),
        "difficulty": difficulty,
        "tags": row.get("tags", []),
        "timeout_s": row.get("timeout_s", 10),
        "environment": row.get("environment", DEFAULT_ENVIRONMENT),
    }


def _looks_like_statement(statement: str) -> bool:
    """Cheap sanity check that a row carries a real theorem statement (step 4)."""
    return ":=" in statement and any(
        statement.lstrip().startswith(kw)
        for kw in ("theorem", "lemma", "example", "def")
    )


def filter_rows(rows: list[dict]) -> CleanReport:
    """Run the offline filtering steps (1-4). Returns cleaned + rejected partitions.

    Improves on v0 with id de-duplication and a statement sanity check so noisy
    candidate pools (Lean Workbook etc.) cannot smuggle malformed rows into training.
    """
    report = CleanReport()
    seen_ids: set[str] = set()
    for raw in rows:
        row = normalize(raw)
        if not row["formal_statement"]:
            report.rejected.append({**row, "reason": "missing_statement"})
            continue
        if not _looks_like_statement(row["formal_statement"]):
            report.rejected.append({**row, "reason": "not_a_statement"})
            continue
        rid = row.get("id")
        if rid is not None and rid in seen_ids:
            report.rejected.append({**row, "reason": "duplicate_id"})
            continue
        if rid is not None:
            seen_ids.add(rid)
        code = "\n".join(f"import {n}" for n in row["imports"])
        code += "\n" + row["formal_statement"] + "\n" + (row["proof"] or "")
        forbidden = check_forbidden(code, allowed_imports=set(row["imports"]))
        if forbidden:
            report.rejected.append({**row, "reason": ",".join(forbidden)})
            continue
        report.cleaned.append(row)
    return report
