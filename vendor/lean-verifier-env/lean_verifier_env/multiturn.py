"""Multi-turn Lean proof repair environment (v2).

Build plan v2 scope: "multi-turn retry loop; model can see prior Lean diagnostics and
revise proof". Reward still comes from strict verification of the model's own proof —
`repair_proofs` is **not** a reward source (that stays out per the plan).

Flow per rollout:
    1. model submits a proof
    2. env verifies it (via the same `evaluate_candidate` as single-turn)
    3. if it verifies -> done (reward 1.0)
    4. else, if turns remain -> env replies with the normalized Lean diagnostics and
       asks for a revision; loop
    5. on max_turns the last attempt's outcome is the reward

The check is done in ``is_completed`` (cached once per turn) so the same result drives
both the completion decision and the feedback message — the checker runs at most once
per turn.
"""

from __future__ import annotations

import verifiers as vf

from .checker import Checker
from .reward import evaluate_candidate, extract_proof, write_eval_to_state

REPAIR_TEMPLATE = (
    "Your proof did not verify.\n\n"
    "Lean reported:\n{feedback}\n\n"
    "Revise the proof and return only the corrected Lean code."
)


def _last_assistant_content(messages) -> str | None:
    if not isinstance(messages, list):
        return None
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            return msg.get("content") or ""
    return None


def _turn_count(state) -> int:
    trajectory = state.get("trajectory")
    if isinstance(trajectory, list):
        return len(trajectory)
    return int(state.get("turn", 0))


def _resolve(args, kwargs):
    """Extract (messages, state) regardless of how the verifiers runtime calls the hook.

    Different verifiers versions call `is_completed`/`env_response` with different positional
    orders (messages,state) or just (state). This resolves both robustly."""
    state = kwargs.get("state")
    messages = kwargs.get("messages")
    for a in args:
        if state is None and isinstance(a, dict) and any(
            k in a for k in ("completion", "info", "turn", "prompt", "trajectory")
        ):
            state = a
        elif messages is None and isinstance(a, list):
            messages = a
    if state is None:
        state = next((a for a in args if isinstance(a, dict)), {})
    if messages is None and isinstance(state, dict):
        trajectory = state.get("trajectory") or []
        messages = (trajectory[-1].get("completion") if trajectory else None) or state.get("completion") or state.get("prompt") or []
    return messages or [], state if isinstance(state, dict) else {}


class LeanRepairEnv(vf.MultiTurnEnv):
    """Multi-turn env that feeds Lean diagnostics back for proof repair."""

    def __init__(self, checker: Checker, max_turns: int = 3, **kwargs) -> None:
        super().__init__(max_turns=max_turns, **kwargs)
        self.checker = checker

    async def _ensure_checked(self, messages, state) -> None:
        """Verify the latest assistant proof once per turn; cache in ``state``."""
        turn = _turn_count(state)
        if state.get("checked_turn") == turn and "last_eval_reward" in state:
            return
        content = _last_assistant_content(messages)
        if content is None:
            return
        proof = extract_proof(content)
        ev = await evaluate_candidate(self.checker, proof, state.get("info") or {})
        write_eval_to_state(state, ev, proof=proof, append_attempt=True)
        state["checked_turn"] = turn
        state.setdefault("attempts", []).append(ev.reward)

    @vf.stop(priority=10)
    async def proof_verified(self, *args, **kwargs) -> bool:
        messages, state = _resolve(args, kwargs)
        if _last_assistant_content(messages) is None:
            return False  # no proof submitted yet (initial prompt)
        await self._ensure_checked(messages, state)
        return state.get("last_eval_reward", 0.0) == 1.0

    async def env_response(self, *args, **kwargs):
        messages, state = _resolve(args, kwargs)
        await self._ensure_checked(messages, state)
        feedback = state.get("lean_feedback") or "(no diagnostics returned)"
        reply = [{"role": "user", "content": REPAIR_TEMPLATE.format(feedback=feedback)}]
        return reply
