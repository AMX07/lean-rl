"""Lean Workbook loader (the main training pool, build plan section 7).

Source: https://huggingface.co/datasets/internlm/Lean-Workbook — Lean 4 contest/olympiad
problems. Real schema (one row):

    id, status, tactic, state_before, state_after,
    natural_language_statement, answer, formal_statement

``formal_statement`` ends in ``:= by sorry`` (a placeholder); ``tactic`` is the reference
proof for ``status == "proved"`` rows. Everything is Mathlib-based.

This module normalizes rows into the shared task schema and is meant to be run *once*
(offline prep) to materialize a filtered subset to ``datasets/cleaned/*.jsonl``, which
training then loads by path. Filtering + difficulty bucketing reuse ``clean.filter_rows``.
"""

from __future__ import annotations

import re

from .clean import filter_rows
from .loaders import write_jsonl

_SORRY_TAIL = re.compile(r"\bsorry\s*$")


def strip_sorry(formal_statement: str) -> str:
    """Turn ``... := by sorry`` into ``... := by`` (the proof-less task statement)."""
    return _SORRY_TAIL.sub("", formal_statement.strip()).rstrip()


def workbook_row_to_task(row: dict, timeout_s: int = 30) -> dict:
    """Map one raw Lean Workbook row into the shared task schema."""
    return {
        "id": row.get("id"),
        "source": "lean_workbook",
        "nl_statement": row.get("natural_language_statement", ""),
        "imports": ["Mathlib"],
        "formal_statement": strip_sorry(row.get("formal_statement", "")),
        "proof": (row.get("tactic") or "").strip(),
        "difficulty": "unknown",  # filled by clean.normalize's heuristic
        "tags": [],
        "timeout_s": timeout_s,
        "environment": "lean-4.28.0",
    }


def load_lean_workbook_rows(
    split: str = "train",
    limit: int | None = None,
    streaming: bool = True,
) -> list[dict]:
    """Stream Lean Workbook and yield **single-tactic complete proofs** as task rows.

    Lean Workbook is a tactic-prediction dataset: a problem can span several rows
    (state -> tactic -> next_state steps of a proof search), which can't be reliably
    reassembled into one proof. We keep only problems whose id has a single row that
    takes the goal straight to ``no goals`` — these are complete one-tactic proofs
    (``ring``/``simp``/``nlinarith``/...), which are also the most tractable for a small
    model. Rows for an id are contiguous in the dataset, so we group consecutively.
    """
    from datasets import load_dataset  # imported lazily; HF access only when used

    ds = load_dataset("internlm/Lean-Workbook", split=split, streaming=streaming)
    rows: list[dict] = []
    cur_id = None
    group: list[dict] = []

    def flush() -> dict | None:
        if len(group) == 1 and group[0].get("state_after") == "no goals":
            task = workbook_row_to_task(group[0])
            task["proof"] = (group[0].get("tactic") or "").strip()
            return task if task["formal_statement"] and task["proof"] else None
        return None

    for raw in ds:
        rid = raw.get("id")
        if rid != cur_id:
            task = flush()
            if task:
                rows.append(task)
                if limit is not None and len(rows) >= limit:
                    return rows
            cur_id, group = rid, []
        group.append(raw)
    task = flush()
    if task and (limit is None or len(rows) < limit):
        rows.append(task)
    return rows


def prepare_subset(
    out_path: str,
    limit: int = 500,
    shortest_proof_first: bool = True,
) -> dict:
    """Materialize a filtered Lean Workbook subset to ``out_path`` (JSONL).

    Runs rows through ``clean.filter_rows`` (dedup — Lean Workbook has several proof rows
    per problem — plus statement check and forbidden-construct rejection). Every Workbook
    problem rates medium/hard on the generic difficulty heuristic, so the tractable
    curriculum lever for a small model is the **reference proof length**: with
    ``shortest_proof_first`` the easiest (single-tactic ``nlinarith``/``simp``/``norm_num``)
    problems come first. Does NOT recheck proofs with Lean — do that separately with the
    local checker on the written subset before training. Returns a summary dict.
    """
    rows = load_lean_workbook_rows(limit=limit * 4)
    report = filter_rows(rows)
    cleaned = report.cleaned
    if shortest_proof_first:
        cleaned.sort(key=lambda r: len(r.get("proof", "")))
    cleaned = cleaned[:limit]
    write_jsonl(cleaned, out_path)
    return {"written": len(cleaned), "rejected": len(report.rejected), "path": out_path}
