"""miniF2F loader — the held-out evaluation benchmark (build plan section 7).

Source: https://huggingface.co/datasets/cat-searcher/minif2f-lean4 (the standard Lean 4
port of miniF2F). Real schema:

    id, split ('valid'/'test'), formal_statement, header, informal_stmt, informal_proof

``formal_statement`` ends in ``:= sorry`` (term-mode placeholder) and has no reference
proof — these are unsolved benchmark problems. We convert to the shared task schema with
the statement ending in ``:= by`` (tactic mode) and an empty proof. ``header`` lists
specific Mathlib modules; we just use ``import Mathlib`` (a superset), matching the
checker's preloaded environment.

This is **eval only** — never mix it into the training pool.
"""

from __future__ import annotations

import re

from .loaders import write_jsonl

_SORRY_TAIL = re.compile(r"\bsorry\s*$")
_TRAILING_ASSIGN = re.compile(r":=\s*$")


def to_statement(formal_statement: str) -> str:
    """``theorem ... := sorry`` / ``:= by sorry`` -> ``theorem ... := by`` (tactic mode).

    Also modernizes deprecated Lean syntax so older-Mathlib benchmark statements parse.
    """
    from .lean_syntax import modernize

    s = modernize(_SORRY_TAIL.sub("", formal_statement.strip()).rstrip())
    if s.endswith("by"):
        return s
    return _TRAILING_ASSIGN.sub(":= by", s) if s.endswith(":=") else s + " := by"


def minif2f_row_to_task(row: dict, timeout_s: int = 60) -> dict:
    return {
        "id": row.get("id"),
        "source": "minif2f",
        "nl_statement": row.get("informal_stmt", ""),
        "imports": ["Mathlib"],
        "formal_statement": to_statement(row.get("formal_statement", "")),
        "proof": "",  # eval benchmark — no reference proof
        "difficulty": "hard",
        "tags": [],
        "timeout_s": timeout_s,
        "environment": "lean-4.28.0",
    }


def load_minif2f_rows(split: str = "test", limit: int | None = None, streaming: bool = True) -> list[dict]:
    """Load miniF2F as task rows. ``split`` is ``"test"`` or ``"valid"`` (the held-out
    halves of miniF2F); HF exposes them as ``test`` / ``validation``."""
    from datasets import load_dataset

    hf_split = {"valid": "validation", "validation": "validation", "test": "test"}.get(split, split)
    ds = load_dataset("cat-searcher/minif2f-lean4", split=hf_split, streaming=streaming)
    rows: list[dict] = []
    for raw in ds:
        task = minif2f_row_to_task(raw)
        if task["formal_statement"]:
            rows.append(task)
        if limit is not None and len(rows) >= limit:
            break
    return rows


def prepare_minif2f(out_path: str, split: str = "test", limit: int | None = None) -> dict:
    """Materialize a miniF2F split to ``out_path`` (JSONL) for use as an eval set."""
    rows = load_minif2f_rows(split=split, limit=limit)
    write_jsonl(rows, out_path)
    return {"written": len(rows), "split": split, "path": out_path}
