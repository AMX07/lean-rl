"""ProofNet loader — undergraduate-level Lean 4 benchmark (harder eval tier).

Source: https://huggingface.co/datasets/UDACA/proofnet-lean4. Schema:
    name, split, informal_prefix, formal_statement, goal, header

``formal_statement`` ends in ``:=`` (term placeholder, no sorry); ``header`` carries
``import Mathlib`` plus ``open ...`` lines the statement needs to typecheck. We convert
to the shared schema with the opens prepended and the statement ending in ``:= by``.
Eval only — no reference proofs.
"""

from __future__ import annotations

from .loaders import write_jsonl
from .minif2f import to_statement


def _opens(header: str) -> str:
    return "\n".join(ln for ln in header.splitlines() if ln.strip().startswith("open"))


def proofnet_row_to_task(row: dict, timeout_s: int = 60) -> dict:
    opens = _opens(row.get("header", ""))
    stmt = to_statement(row.get("formal_statement", ""))
    formal = f"{opens}\n{stmt}" if opens else stmt
    return {
        "id": row.get("name"),
        "source": "proofnet",
        "nl_statement": row.get("informal_prefix", "").strip(),
        "imports": ["Mathlib"],
        "formal_statement": formal,
        "proof": "",
        "difficulty": "hard",
        "tags": [],
        "timeout_s": timeout_s,
        "environment": "lean-4.28.0",
    }


def load_proofnet_rows(split: str = "test", limit: int | None = None, streaming: bool = True) -> list[dict]:
    from datasets import load_dataset

    ds = load_dataset("UDACA/proofnet-lean4", split=split, streaming=streaming)
    rows: list[dict] = []
    for raw in ds:
        task = proofnet_row_to_task(raw)
        if task["formal_statement"]:
            rows.append(task)
        if limit is not None and len(rows) >= limit:
            break
    return rows


def prepare_proofnet(out_path: str, split: str = "test", limit: int | None = None) -> dict:
    rows = load_proofnet_rows(split=split, limit=limit)
    write_jsonl(rows, out_path)
    return {"written": len(rows), "split": split, "path": out_path}
