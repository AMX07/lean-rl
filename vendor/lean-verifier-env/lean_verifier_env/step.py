"""Step-level (tactic-by-tactic) Lean environment — the LeanDojo-style interaction mode.

Closes the main gap vs tactic-step gyms: instead of submitting one whole proof, the model
emits ONE tactic per turn and sees the **real resulting proof state** (remaining goals) from
Lean, looping until ``no goals`` or the turn budget. This exposes the fine-grained state
signal whole-proof scoring throws away.

Statefulness: a Lean ``proofState`` lives inside one repl process, so each rollout pins a
dedicated worker via ``LocalLeanChecker.open_session`` (bounded by ``pool_size``). Local
backend only — AXLE has no proofState API.
"""

from __future__ import annotations

import verifiers as vf

from .filters import check_forbidden
from .multiturn import _last_assistant_content, _resolve, _turn_count

STEP_SYSTEM_PROMPT = (
    "You are proving a Lean 4 theorem interactively. Each turn, reply with exactly ONE Lean "
    "tactic (no prose, no ```), which will be applied to the current goal. You will then see "
    "the remaining goals. Continue until there are no goals left. Do not use `sorry`, "
    "`admit`, axioms, `native_decide`, or `unsafe`."
)


def _format_goals(goals) -> str:
    if not goals:
        return "No goals — proof complete."
    return "Current goals:\n" + "\n\n".join(str(g) for g in goals)


class StepLeanEnv(vf.MultiTurnEnv):
    """Tactic-by-tactic proving with real proof-state feedback (local checker only)."""

    def __init__(self, checker, max_turns: int = 12, **kwargs) -> None:
        super().__init__(max_turns=max_turns, **kwargs)
        self.checker = checker

    async def setup_state(self, state, **kwargs):
        maybe_state = await super().setup_state(state, **kwargs)
        if maybe_state is not None:
            state = maybe_state
        info = state.get("info") or {}
        sid = await self.checker.open_session()
        ps, goal = await self.checker.init_proof_state(sid, info.get("formal_statement", ""),
                                                       timeout_s=info.get("timeout_s"))
        state["step_sid"] = sid
        state["step_proof_state"] = ps
        state["step_goals"] = [goal] if goal else None
        state["step_done"] = False
        state["step_reward"] = 0.0
        if ps is None:  # statement didn't parse -> unprovable
            state["step_done"] = True
            await self._close(state)
        return state

    async def _close(self, state) -> None:
        sid = state.get("step_sid")
        if sid and not state.get("step_closed"):
            await self.checker.close_session(sid)
            state["step_closed"] = True

    async def _ensure_applied(self, messages, state) -> None:
        if state.get("applied_turn") == _turn_count(state) or state.get("step_done"):
            return
        tactic = (_last_assistant_content(messages) or "").strip()
        if not tactic:
            return
        state["applied_turn"] = _turn_count(state)
        if check_forbidden(tactic):  # anti-cheat per tactic
            state["step_done"] = True
            state["step_goals"] = None
            state["step_feedback"] = "Tactic rejected (forbidden construct)."
            return
        info = state.get("info") or {}
        ps, goals, errors, done = await self.checker.apply_tactic(
            state["step_sid"], state["step_proof_state"], tactic, timeout_s=info.get("timeout_s"))
        if errors:
            state["step_feedback"] = "Tactic failed:\n" + "\n".join(errors[:3])
            # keep the same proof state so the model can retry
        else:
            state["step_proof_state"] = ps
            state["step_goals"] = goals
            state["step_feedback"] = _format_goals(goals)
        if done:
            state["step_done"] = True
            state["step_reward"] = 1.0

    async def is_completed(self, *args, **kwargs) -> bool:
        messages, state = _resolve(args, kwargs)
        if _last_assistant_content(messages) is not None:
            await self._ensure_applied(messages, state)
        finished = state.get("step_done", False) or (
            self.max_turns > 0 and _turn_count(state) >= self.max_turns)
        if finished:
            await self._close(state)
        return finished

    async def env_response(self, *args, **kwargs):
        messages, state = _resolve(args, kwargs)
        await self._ensure_applied(messages, state)
        feedback = state.get("step_feedback") or _format_goals(state.get("step_goals"))
        return [{"role": "user", "content": feedback}], state
