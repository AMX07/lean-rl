"""verifiers entrypoint: ``load_environment`` for the Lean proof verifier.

The default mode is single-turn proof completion. Training-oriented modes add Lean
diagnostic feedback (`repair`), a constrained verifier tool (`tool` / `agentic`),
tactic-level state (`step`), or Prime's recursive RLM wrapper (`rlm`).
"""

from __future__ import annotations

import verifiers as vf

from .agentic import AGENTIC_SYSTEM_PROMPT, LeanToolEnv
from .checkers import load_checker
from .datasets.loaders import load_dataset_split
from .multiturn import LeanRepairEnv
from .reward import make_repair_rubric, make_rubric, make_tool_rubric

SYSTEM_PROMPT = (
    "You are a Lean 4 theorem prover. You are given a theorem statement ending in "
    "`:= by`. Reply with the Lean tactic proof that completes it. Return only Lean "
    "code, with no prose. Do not use `sorry`, `admit`, axioms, or `unsafe`."
)


def load_environment(
    dataset_name: str = "toy",
    system_prompt: str | None = None,
    checker_backend: str | None = None,
    mode: str = "single",
    max_turns: int = 1,
    difficulty: str | None = None,
    max_difficulty: str | None = None,
    reward_shaping: bool = False,
    reward_mode: str | None = None,
    eval_split: bool = False,
    **kwargs,
) -> vf.Environment:
    """Build the Lean verifier environment.

    Args:
        dataset_name: ``"toy"`` (curated) or a path to a generated/cleaned JSONL file.
        system_prompt: instruction prepended to every task.
        difficulty / max_difficulty: curriculum bucket filter (easy/medium/hard).
        eval_split: required (``True``) to load an eval-only benchmark preset
            (``minif2f-test``, ``proofnet``, ``putnam``). Without it those presets raise,
            so a training run can't silently train on a test benchmark. Use
            ``lean-workbook`` / ``minif2f-valid`` for training (no flag needed).
        checker_backend: ``"axle"`` (default, hosted, 20-concurrent cap) or ``"local"``
            (Lean 4 + Mathlib REPL, scales with CPU). Falls back to the
            ``LEAN_CHECKER_BACKEND`` env var.
        mode: ``"single"`` (default), ``"repair"``, ``"tool"`` / ``"agentic"``,
            ``"step"``, or ``"rlm"``. For training a tool-using model, start with
            ``mode="tool"``: the model only sees ``lean_check(proof)`` while the env
            injects the hidden theorem/imports into the checker call.
        max_turns: ``1`` (default) gives single-turn proof completion. ``>1`` enables
            the v2 multi-turn repair loop where the model sees the Lean diagnostics from
            a failed attempt and revises, up to ``max_turns`` proof submissions.

    Backend config is read from the remaining ``kwargs`` (e.g. ``axle_url``,
    ``axle_timeout_s``, ``lean_repl_cmd``, ``lean_project_dir``, ``lean_pool_size``,
    ``lean_timeout_s``); unrecognized keys are passed through to the env.

    Returns a :class:`vf.SingleTurnEnv` (max_turns==1) or :class:`LeanRepairEnv`.
    """
    checker_cfg = {
        key: kwargs.pop(key)
        for key in (
            "axle_url",
            "axle_timeout_s",
            "axle_max_concurrency",
            "lean_repl_cmd",
            "lean_project_dir",
            "lean_pool_size",
            "lean_timeout_s",
            "lean_path",
        )
        if key in kwargs
    }
    dataset = load_dataset_split(
        dataset_name, difficulty=difficulty, max_difficulty=max_difficulty,
        allow_eval_split=eval_split,
    )
    checker = load_checker(checker_backend, **checker_cfg)

    if mode == "rlm":
        from .rlm import make_rlm_env

        # Hosted-scoring decoupling: the model verifies *in-sandbox* (Lean image), but the
        # rubric's final check runs wherever the env runs. On Prime's hosted container there
        # is no local Lean, so `scoring_backend` lets the rubric score via a backend that
        # works there (e.g. "axle") independent of the in-sandbox verification path. Defaults
        # to the main checker (correct for local `prime eval run`, which has Lean).
        scoring_backend = kwargs.pop("scoring_backend", None)
        scoring_checker = (
            load_checker(scoring_backend, **checker_cfg) if scoring_backend else checker
        )
        # RLM needs many REPL turns to decompose -> recurse -> assemble; 1 is degenerate.
        return make_rlm_env(
            scoring_checker, dataset, system_prompt=system_prompt,
            max_turns=max_turns if max_turns > 1 else 30, **kwargs,
        )

    if mode == "step":
        from .step import STEP_SYSTEM_PROMPT, StepLeanEnv
        from .reward import make_step_rubric

        return StepLeanEnv(
            checker=checker,
            max_turns=max_turns if max_turns > 1 else 12,
            dataset=dataset,
            rubric=make_step_rubric(),
            system_prompt=system_prompt or STEP_SYSTEM_PROMPT,
            **kwargs,
        )

    if mode in {"tool", "agentic"}:
        return LeanToolEnv(
            checker=checker,
            max_turns=max_turns if max_turns > 1 else 4,
            dataset=dataset,
            rubric=make_tool_rubric(),
            system_prompt=system_prompt or AGENTIC_SYSTEM_PROMPT,
            **kwargs,
        )

    # mode="repair", or the back-compat path where max_turns>1 implies repair.
    if mode == "repair" or max_turns > 1:
        return LeanRepairEnv(
            checker=checker,
            max_turns=max_turns if max_turns > 1 else 3,
            dataset=dataset,
            rubric=make_repair_rubric(),
            system_prompt=system_prompt or SYSTEM_PROMPT,
            **kwargs,
        )

    return vf.SingleTurnEnv(
        dataset=dataset,
        rubric=make_rubric(checker, reward_shaping=reward_shaping, reward_mode=reward_mode),
        system_prompt=system_prompt or SYSTEM_PROMPT,
        **kwargs,
    )
