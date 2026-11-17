"""PutnamBench loader — Putnam competition Lean 4 (hardest/frontier eval tier).

Source: https://huggingface.co/datasets/amitayusht/PutnamBench. Field ``lean4_statement``
is either a pure ``theorem ... := sorry`` or a "find-the-value" problem with an
``abbrev <name>_solution := sorry`` the statement references. The latter need the model
to supply a closed-form answer and trip our anti-cheat ``sorry`` filter, so we keep only
the **pure-proof** theorems here. Statements get a broad ``open`` preamble so the
unqualified names typecheck, and end in ``:= by``. Eval only — no reference proofs.
"""

from __future__ import annotations

from .loaders import write_jsonl
from .minif2f import to_statement

# Names Putnam statements commonly use unqualified.
_PREAMBLE = "open BigOperators Topology Filter Finset Polynomial Nat\nopen scoped BigOperators Topology"


def is_pure_proof(lean4_statement: str) -> bool:
    """Keep theorems that don't reference a `_solution` abbrev (find-the-value problems)."""
    return "solution" not in lean4_statement and "abbrev" not in lean4_statement


def putnam_row_to_task(row: dict, timeout_s: int = 90) -> dict:
    stmt = to_statement(row.get("lean4_statement") or "")
    return {
        "id": row.get("name"),
        "source": "putnam",
        "nl_statement": (row.get("informal_statement") or "").strip(),
        "imports": ["Mathlib"],
        "formal_statement": f"{_PREAMBLE}\n{stmt}",
        "proof": "",
        "difficulty": "hard",
        "tags": [],
        "timeout_s": timeout_s,
        "environment": "lean-4.28.0",
    }


def load_putnam_rows(split: str = "train", limit: int | None = None, streaming: bool = True) -> list[dict]:
    from datasets import load_dataset

    ds = load_dataset("amitayusht/PutnamBench", split=split, streaming=streaming)
    rows: list[dict] = []
    for raw in ds:
        lean4 = raw.get("lean4_statement") or ""
        if not is_pure_proof(lean4):
            continue
        task = putnam_row_to_task(raw)
        if task["formal_statement"]:
            rows.append(task)
        if limit is not None and len(rows) >= limit:
            break
    return rows


def prepare_putnam(out_path: str, limit: int | None = None) -> dict:
    rows = load_putnam_rows(limit=limit)
    write_jsonl(rows, out_path)
    return {"written": len(rows), "path": out_path}
