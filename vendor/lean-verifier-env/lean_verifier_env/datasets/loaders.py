"""Dataset loaders for the Lean verifier environment.

v0 only ships the hand-written ``toy.jsonl`` curated split (build plan section 7).
Loaders for the Lean Workbook / miniF2F / ProofNet ladder come later and must route
through the cleaning + rechecking pipeline in ``clean.py`` before use.

Each raw row uses the shared JSONL schema (plan section 7):

    {"id", "source", "nl_statement", "imports", "formal_statement",
     "proof", "difficulty", "tags", "timeout_s"}

and is normalized into the verifiers column layout: ``question``, ``answer``, ``info``,
without legacy string ``task`` routing (unsupported by the pinned runtime).
"""

from __future__ import annotations

import json
from pathlib import Path

from datasets import Dataset

_DATA_DIR = Path(__file__).parent

PROMPT_TEMPLATE = (
    "Prove the following theorem in Lean 4. Fill in the proof after `:= by`. "
    "Return only Lean code.\n\n{statement}"
)


def _required(row: dict, key: str) -> object:
    if key not in row or row[key] in (None, ""):
        raise ValueError(f"toy/dataset row {row.get('id', '<no id>')!r} missing required field {key!r}")
    return row[key]


def _to_example(row: dict) -> dict:
    statement = _required(row, "formal_statement")
    imports = row.get("imports") or ["Mathlib"]
    return {
        "question": PROMPT_TEMPLATE.format(statement=statement),
        "answer": row.get("proof") or "",
        "info": {
            "id": row.get("id"),
            "source": row.get("source", "toy"),
            "imports": imports,
            "formal_statement": statement,
            "environment": row.get("environment", "lean-4.28.0"),
            "timeout_s": row.get("timeout_s", 10),
            "difficulty": row.get("difficulty", "unknown"),
        },
    }


# Curriculum ordering: earlier buckets are easier. "toy"/"unknown" sort to the ends.
_DIFFICULTY_ORDER = {"toy": 0, "easy": 1, "medium": 2, "hard": 3, "unknown": 4}


def load_jsonl(path: str | Path) -> list[dict]:
    """Read a JSONL file, skipping blank lines. Raises on malformed JSON rows."""
    rows: list[dict] = []
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"malformed JSON at {path}:{lineno}: {exc}") from exc
    return rows


def write_jsonl(rows: list[dict], path: str | Path) -> None:
    """Write rows to a JSONL file (one compact JSON object per line)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def filter_by_difficulty(
    rows: list[dict],
    difficulty: str | None = None,
    max_difficulty: str | None = None,
) -> list[dict]:
    """Select a curriculum bucket: an exact ``difficulty`` or everything up to and
    including ``max_difficulty`` in the curriculum order."""
    if difficulty is not None:
        return [r for r in rows if r.get("difficulty") == difficulty]
    if max_difficulty is not None:
        ceiling = _DIFFICULTY_ORDER.get(max_difficulty, 99)
        return [r for r in rows if _DIFFICULTY_ORDER.get(r.get("difficulty", "unknown"), 99) <= ceiling]
    return rows


def _rows_to_dataset(rows: list[dict]) -> Dataset:
    return Dataset.from_list([_to_example(r) for r in rows])


def load_toy() -> Dataset:
    """Load the curated toy split as a verifiers-compatible ``Dataset``."""
    return _rows_to_dataset(load_jsonl(_DATA_DIR / "toy.jsonl"))


# Standard test benchmarks. Training on these contaminates downstream evaluation on the
# same set, so they ship for *evaluation only* and require an explicit opt-in. The
# trainable presets (`toy`, `lean-workbook`, `minif2f-valid`) are clean for training:
# lean-workbook has zero statement overlap with these benchmarks, and minif2f-valid is
# disjoint from minif2f-test.
EVAL_ONLY_SPLITS = frozenset({"minif2f-test", "proofnet", "putnam"})


def load_dataset_split(
    name: str = "toy",
    difficulty: str | None = None,
    max_difficulty: str | None = None,
    *,
    allow_eval_split: bool = False,
) -> Dataset:
    """Dispatch on dataset name, optionally filtered to a curriculum bucket.

    ``name`` is ``"toy"`` (curated) or a path to a generated/cleaned ``.jsonl`` file
    (see :mod:`lean_verifier_env.datasets.generate`). ``difficulty`` selects one bucket;
    ``max_difficulty`` selects everything up to that bucket (easy -> hard curriculum).

    The eval-only benchmark presets (:data:`EVAL_ONLY_SPLITS`) are gated: requesting one
    without ``allow_eval_split=True`` raises, so a training run can't silently train on a
    test benchmark. Raw JSONL paths are never gated.
    """
    if name in EVAL_ONLY_SPLITS and not allow_eval_split:
        raise ValueError(
            f"{name!r} is an eval-only benchmark — training on it contaminates downstream "
            f"evaluation on the same set. For training use 'lean-workbook' (clean: zero "
            f"overlap with miniF2F/ProofNet/Putnam) or 'minif2f-valid'. To evaluate on it, "
            f"pass eval_split=True (env arg \"eval_split\": true)."
        )
    named = {
        "toy": _DATA_DIR / "toy.jsonl",
        "lean-workbook": _DATA_DIR / "cleaned" / "lean_workbook_train.jsonl",
        "minif2f-valid": _DATA_DIR / "cleaned" / "minif2f_valid.jsonl",
        "minif2f-test": _DATA_DIR / "cleaned" / "minif2f_test.jsonl",
        "proofnet": _DATA_DIR / "cleaned" / "proofnet_test.jsonl",
        "putnam": _DATA_DIR / "cleaned" / "putnam.jsonl",
    }
    if name in named:
        rows = load_jsonl(named[name])
    elif Path(name).is_file():
        rows = load_jsonl(name)
    else:
        raise ValueError(
            f"dataset {name!r} is not available. Use one of {sorted(named)} or a path to "
            "a generated/cleaned JSONL file."
        )
    rows = filter_by_difficulty(rows, difficulty, max_difficulty)
    if not rows:
        raise ValueError(f"no tasks left in {name!r} after difficulty filter")
    return _rows_to_dataset(rows)
