"""Agentic (tool-use) Lean proving environment.

The model reasons step-by-step and calls a ``lean_check`` *tool* to verify candidate
proofs against the real Lean checker mid-rollout, iterating on the diagnostics before
committing. Reward is strict: 1.0 if the model produced a proof that actually verifies
within its turn budget. The tool *is* the verifier and the model authors the proof, so
this is a sound prover-RL signal — not "repair backend success becomes reward".

Built on ``vf.StatefulToolEnv`` so the env injects the **required** statement/imports
into every tool call (``update_tool_args``); the model only ever sees ``proof``
(``add_tool(..., args_to_skip=...)``). This prevents earning reward by checking a
different, easier theorem.
"""

from __future__ import annotations

import verifiers as vf

from .checker import Checker
from .reward import evaluate_candidate, evaluation_trace, write_eval_to_state

AGENTIC_SYSTEM_PROMPT = (
    "You are a Lean 4 theorem prover with access to a `lean_check` tool. Given a theorem "
    "statement ending in `:= by`, work out a tactic proof and call `lean_check` with the "
    "proof to verify it against Lean. Read the returned diagnostics and revise until it "
    "verifies, then stop. Do not use `sorry`, `admit`, axioms, or `unsafe`."
)


def make_lean_tool(checker: Checker):
    """Build the async ``lean_check`` tool bound to a ``Checker``.

    Hidden args (``_info``/``_state``) are injected by the env, not the model.
    """

    # `info`/`state` are injected by the env (real dict / State objects) and stripped
    # from the model-facing schema via add_tool(args_to_skip=...). They are annotated as
    # `str` only so the strict tool-schema generator accepts them (it rejects `dict`).
    async def lean_check(proof: str, info: str = "", state: str = "") -> str:
        """Verify a Lean tactic proof for the current theorem.

        Args:
            proof: the Lean tactic block to place after the theorem's `:= by`.

        Returns whether the proof verifies, or the Lean error messages to fix.
        """
        ev = await evaluate_candidate(checker, proof, info or {})
        if state is not None:
            state.setdefault("lean_attempts", []).append(ev.reward)
            state.setdefault("lean_attempt_traces", []).append(evaluation_trace(proof, ev))
            if ev.reward >= state.get("best_reward", 0.0):
                state["best_reward"] = ev.reward
                write_eval_to_state(state, ev, proof=proof)
        if ev.reward == 1.0:
            return "✓ Proof verified."
        return "✗ Proof did not verify:\n" + (ev.feedback or "(no diagnostics returned)")

    lean_check.__name__ = "lean_check"
    return lean_check


class LeanToolEnv(vf.StatefulToolEnv):
    """Agentic env exposing ``lean_check`` as a model-callable tool."""

    def __init__(self, checker: Checker, max_turns: int = 4, **kwargs) -> None:
        self.checker = checker
        super().__init__(tools=[], max_turns=max_turns, **kwargs)
        self.add_tool(make_lean_tool(checker), args_to_skip=["info", "state"])

    async def setup_state(self, state, **kwargs):
        maybe_state = await super().setup_state(state, **kwargs)
        if maybe_state is not None:
            state = maybe_state
        state.setdefault("best_reward", 0.0)
        state.setdefault("lean_attempts", [])
        state.setdefault("lean_attempt_traces", [])
        return state

    def update_tool_args(self, tool_name, tool_args, messages, state, **kwargs):
        if tool_name == "lean_check":
            tool_args["info"] = state.get("info") or {}
            tool_args["state"] = state
        return tool_args
