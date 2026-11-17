"""Task generation from Lean source files (build plan v3).

Turns Lean files into declaration-level proof tasks in the shared JSONL schema, then
hands them to the cleaning pipeline (:mod:`clean`) for validation / rechecking.

Two splitters:
- a local, offline splitter (:func:`split_declarations`) — the default, fully testable.
- an AXLE-backed splitter (:func:`axle_splitter`) using ``extract_theorems`` for robust
  dependency-aware splitting on real files (build plan: ``extract_decls`` task gen).

Difficulty bucketing (:func:`estimate_difficulty`) provides curriculum buckets for v3.
Generation is tooling only — none of this is ever a reward source.
"""

from __future__ import annotations

import re

_DECL_RE = re.compile(r"^\s*(?:@\[[^\]]*\]\s*)?(?:theorem|lemma)\b", re.MULTILINE)
_NAME_RE = re.compile(r"\b(?:theorem|lemma)\s+([A-Za-z_][\w'.]*)")

DIFFICULTY_BUCKETS = ("easy", "medium", "hard")


def statement_from_theorem(decl_code: str) -> str:
    """Strip a declaration's proof, returning the signature ending in ``:= by``.

    The first top-level ``:=`` separates the statement from the proof in a theorem
    (hypotheses use ``:``, theorems have no default args), so we cut there.
    """
    idx = decl_code.find(":=")
    signature = (decl_code[:idx] if idx != -1 else decl_code).rstrip()
    return f"{signature} := by"


def declaration_name(decl_code: str) -> str:
    match = _NAME_RE.search(decl_code)
    return match.group(1) if match else ""


def split_imports_and_body(source: str) -> tuple[list[str], str]:
    lines = source.splitlines()
    imports: list[str] = []
    idx = 0
    while idx < len(lines):
        stripped = lines[idx].strip()
        if stripped == "":
            idx += 1
            continue
        if stripped.startswith("import "):
            imports.append(stripped.split(None, 1)[1].strip())
            idx += 1
            continue
        break
    return imports, "\n".join(lines[idx:])


def split_declarations(source: str) -> list[str]:
    """Split a Lean file body into top-level theorem/lemma declaration blocks."""
    _, body = split_imports_and_body(source)
    starts = [m.start() for m in _DECL_RE.finditer(body)]
    blocks: list[str] = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(body)
        block = body[start:end].strip()
        if block:
            blocks.append(block)
    return blocks


def estimate_difficulty(formal_statement: str, proof: str = "") -> str:
    """Heuristic curriculum bucket from statement/proof size and hypothesis count."""
    n_hyp = formal_statement.count(":") - 1  # subtract the type colon
    score = len(formal_statement) // 40 + len(proof) // 60 + max(0, n_hyp)
    if score <= 1:
        return "easy"
    if score <= 3:
        return "medium"
    return "hard"


def _row_from_declaration(decl_code: str, imports: list[str], source_name: str, environment: str) -> dict:
    statement = statement_from_theorem(decl_code)
    idx = decl_code.find(":=")
    proof = decl_code[idx + 2 :].strip() if idx != -1 else ""
    # The statement already ends in ``:= by``; drop a leading ``by`` from the proof so
    # the reference proof is just the tactic block.
    proof = re.sub(r"^by\b\s*", "", proof)
    name = declaration_name(decl_code)
    return {
        "id": f"{source_name}_{name}" if name else source_name,
        "source": source_name,
        "nl_statement": "",
        "imports": imports or ["Mathlib"],
        "formal_statement": statement,
        "proof": proof,
        "difficulty": estimate_difficulty(statement, proof),
        "tags": [],
        "timeout_s": 10,
        "environment": environment,
    }


def tasks_from_lean_source(
    source: str,
    source_name: str = "generated",
    environment: str = "lean-4.28.0",
    declarations: list[str] | None = None,
) -> list[dict]:
    """Build task rows from a Lean source string.

    ``declarations`` lets an external splitter (e.g. :func:`axle_splitter`) supply the
    declaration blocks; otherwise the local :func:`split_declarations` is used.
    """
    imports, _ = split_imports_and_body(source)
    blocks = declarations if declarations is not None else split_declarations(source)
    return [_row_from_declaration(b, imports, source_name, environment) for b in blocks]


async def axle_splitter(client, source: str, environment: str) -> list[str]:
    """Split a Lean file into declaration blocks using AXLE ``extract_theorems``.

    Returns the declaration code strings, ready to pass as ``declarations`` to
    :func:`tasks_from_lean_source`.
    """
    decls = await client.extract_theorems(source, environment=environment)
    return [d["code"] for d in decls if d.get("code")]
