"""Small population search over model-authored Lean proofs.

This is an evaluation/data-mining harness, not an RL reward path. It generates a small
candidate population, verifies every unique proof with the shared strict scorer, then
patches and independently rewrites the strongest failures. Only verified candidates
for the exact target theorem are selectable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from .checker import Checker
from .reward import Evaluation, evaluate_candidate, evaluation_trace, extract_proof, proof_hash

BatchGenerator = Callable[[list[str]], Awaitable[list[str]]]


@dataclass
class SearchResult:
    task_id: str
    attempts: list[dict]
    selected_proof: str | None = None

    @property
    def verified(self) -> bool:
        return self.selected_proof is not None


def _task_context(info: dict) -> str:
    imports = "\n".join(f"import {name}" for name in info.get("imports") or ["Mathlib"])
    statement = info.get("formal_statement") or ""
    return f"{imports}\n{statement}".strip()


def generation_prompt(info: dict, candidate_index: int) -> str:
    return (
        "Produce one Lean 4 proof for the exact target below. Return only a proof body or "
        "the complete target declaration. Do not use sorry, admit, axioms, native_decide, "
        "unsafe, or a different theorem. Try a distinct approach from other candidates.\n\n"
        f"Candidate: {candidate_index}\n{_task_context(info)}"
    )


def repair_prompt(info: dict, attempt: dict, strategy: str) -> str:
    proof = attempt["proof"]
    feedback = attempt.get("feedback") or "Lean rejected the proof without diagnostics."
    if strategy == "patch":
        instruction = "Make the smallest correction that addresses Lean's diagnostics."
    else:
        instruction = "Discard the failed approach and derive an independent proof."
    return (
        f"{instruction} Return only Lean code for the exact target. Do not use sorry, admit, "
        "axioms, native_decide, unsafe, or a different theorem.\n\n"
        f"Target:\n{_task_context(info)}\n\nFailed proof:\n{proof}\n\nLean diagnostics:\n{feedback}"
    )


def _failure_rank(attempt: dict) -> tuple:
    metrics = attempt.get("metrics") or {}
    return (
        -float(metrics.get("progress", 0.0)),
        -float(metrics.get("typecheck_ok", 0.0)),
        -float(metrics.get("syntax_ok", 0.0)),
        len(attempt.get("proof") or ""),
        -attempt["attempt"],
    )


def _select(attempts: list[dict]) -> dict | None:
    verified = [attempt for attempt in attempts if attempt["reward"] == 1.0]
    if not verified:
        return None
    return min(verified, key=lambda attempt: (len(attempt["proof"]), attempt["attempt"]))


async def _generate(generator: BatchGenerator, prompts: list[str]) -> list[str]:
    candidates = await generator(prompts)
    if len(candidates) != len(prompts):
        raise ValueError("generator must return one candidate per prompt")
    return [extract_proof(candidate) for candidate in candidates]


async def run_search(
    checker: Checker,
    info: dict,
    generator: BatchGenerator,
    *,
    num_candidates: int = 2,
    rewrite_rounds: int = 1,
    beam_width: int = 1,
) -> SearchResult:
    """Run cost-bounded generate -> verify -> patch/rewrite -> select search."""
    if num_candidates < 1:
        raise ValueError("num_candidates must be at least 1")
    if rewrite_rounds < 0:
        raise ValueError("rewrite_rounds cannot be negative")
    if beam_width < 1:
        raise ValueError("beam_width must be at least 1")

    task_id = str(info.get("id") or "unknown")
    attempts: list[dict] = []
    evaluations: dict[str, tuple[Evaluation, int]] = {}

    async def record(proof: str, *, phase: str, round_index: int, strategy: str,
                     parent_hash: str | None = None) -> None:
        candidate_hash = proof_hash(proof)
        cached = evaluations.get(candidate_hash)
        if cached is None:
            ev = await evaluate_candidate(checker, proof, info)
            original_attempt = len(attempts) + 1
            evaluations[candidate_hash] = (ev, original_attempt)
            duplicate_of = None
            checker_called = bool(ev.metrics.get("checked", 0.0))
        else:
            ev, original_attempt = cached
            duplicate_of = original_attempt
            checker_called = False
        row = {
            "task_id": task_id,
            "attempt": len(attempts) + 1,
            "phase": phase,
            "round": round_index,
            "strategy": strategy,
            "parent_hash": parent_hash,
            **evaluation_trace(proof, ev),
            "duplicate_of": duplicate_of,
            "checker_called": checker_called,
            "selected": False,
        }
        attempts.append(row)

    prompts = [generation_prompt(info, i + 1) for i in range(num_candidates)]
    for proof in await _generate(generator, prompts):
        await record(proof, phase="generate", round_index=0, strategy="direct")

    for round_index in range(1, rewrite_rounds + 1):
        if _select(attempts) is not None:
            break
        unique_failures = [a for a in attempts if a["reward"] == 0.0 and a["duplicate_of"] is None]
        parents = sorted(unique_failures, key=_failure_rank)[:beam_width]
        if not parents:
            break
        jobs = [(parent, strategy) for parent in parents for strategy in ("patch", "rewrite")]
        repair_prompts = [repair_prompt(info, parent, strategy) for parent, strategy in jobs]
        repaired = await _generate(generator, repair_prompts)
        for proof, (parent, strategy) in zip(repaired, jobs):
            await record(
                proof,
                phase="repair",
                round_index=round_index,
                strategy=strategy,
                parent_hash=parent["proof_hash"],
            )

    selected = _select(attempts)
    if selected is not None:
        selected["selected"] = True
    return SearchResult(
        task_id=task_id,
        attempts=attempts,
        selected_proof=selected["proof"] if selected is not None else None,
    )
