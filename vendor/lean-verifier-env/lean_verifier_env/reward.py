"""Binary reward + monitor metrics for the Lean verifier environment.

Reward policy (build plan section 6):

    proof verifies cleanly        -> 1.0
    syntax / typecheck error      -> 0.0
    timeout                       -> 0.0  (+ timeout metric)
    contains sorry / admit        -> 0.0  (+ forbidden metric, no AXLE call)
    declares local axiom          -> 0.0  (+ forbidden metric, no AXLE call)
    unsafe / metaprogramming      -> 0.0  (+ forbidden metric, no AXLE call)

Only deterministic verification produces reward 1.0. Everything else is a metric with
weight 0.0 so formatting/length never dominates correctness.

This is "hidden scorer" mode: the model never calls the checker. It submits proof text,
the environment constructs the full Lean file and verifies it after the fact. The
checker is any backend implementing the ``Checker`` protocol (AXLE or local Lean).
"""

from __future__ import annotations

import hashlib
import inspect
import re
import time
from dataclasses import dataclass, field

import verifiers as vf

from .checker import Checker
from .diagnostics import classify, normalize_errors, proof_progress
from .filters import check_forbidden
from .lean_source import strip_lean_comments_and_strings as _strip_lean_comments_and_strings

_METRIC_NAMES = [
    "verified",
    "syntax_ok",
    "typecheck_ok",
    "progress",
    "forbidden",
    "wrong_theorem",
    "axiom_violation",
    "axiom_audit_ran",
    "verified_without_axiom_audit",
    "timeout",
    "checked",
    "full_declaration",
    "proof_len",
    "check_ms",
]

# Matches ```lean / ```lean4 / ```  fences. `lean\d*` consumes the version digits so
# they don't leak into the extracted code (e.g. the "4" of ```lean4).
_FENCE = re.compile(r"```(?:lean\d*)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_proof(completion) -> str:
    """Pull the proof text out of a model completion (chat list or raw string)."""
    if isinstance(completion, list):
        text = ""
        for msg in completion:
            if msg.get("role") == "assistant":
                text = msg.get("content") or ""
    else:
        text = completion or ""
    text = str(text)
    fences = _FENCE.findall(text)
    if fences:
        # Use the last fenced block; models often restate the statement first.
        return fences[-1].strip()
    return text.strip()


_DECL_START = re.compile(r"^\s*(?:theorem|lemma|example|def)\b")
_DECL_NAME = re.compile(r"\b(?:theorem|lemma|def)\s+([A-Za-z_][\w'.]*)")


def _normalize_proof_body(proof: str) -> str:
    """Indent a screened proof body under a statement ending in `:= by`.

    Strips a leading `by` (the statement already has one) and indents the block by 2
    spaces, preserving relative indentation inside the candidate.
    """
    proof = re.sub(r"^\s*by\b[ \t]*", "", proof)
    lines = proof.split("\n")
    # Indentation is formatting, not a security boundary: _candidate_body also
    # rejects declaration and scope-control tokens.
    return "\n".join(("  " + ln) if ln.strip() else ln for ln in lines)


def _is_full_declaration(proof: str, statement: str) -> bool:
    """Detect declaration-shaped submissions for strict header validation."""
    if not proof:
        return False
    if _DECL_START.match(proof):
        return True
    return bool(re.search(r"\b(?:theorem|lemma|example|def)\b", _strip_lean_comments_and_strings(proof)))


def _candidate_body(proof: str, statement: str) -> str | None:
    """Accept a body or one exact declaration, never candidate command context.

    Full-file section/namespace/variable wrappers can change the elaborated type
    without changing the theorem's source header. Fail closed on these formats.
    Even an accepted full declaration is reconstructed using the trusted header.
    """
    # Only tactic-mode task statements are supported by this environment.
    if not re.search(r":=\s*by\s*$", statement):
        return None
    if _is_full_declaration(proof, statement):
        # Match the WHOLE statement, including `let ... := ...` inside its type.
        pattern = r"\s*" + r"\s+".join(re.escape(t) for t in statement.split()) + r"\b"
        match = re.match(pattern, proof)
        if not match:
            return None
        proof = proof[match.end():].lstrip()
    # A full declaration must contain only the target and its tactic body.
    # Lean can accept subsequent commands even when they are indented.
    if re.search(r"\b(?:theorem|lemma|example|def|section|namespace|variable|include|omit|end)\b",
                 _strip_lean_comments_and_strings(proof)):
        return None
    return proof


def _proves_required_statement(proof: str, statement: str) -> bool:
    """Only the environment's trusted declaration may be checked for reward."""
    return _candidate_body(proof, statement) is not None


def build_lean_file(imports: list[str], statement: str, proof: str) -> str:
    """Assemble imports + statement + proof into one checkable Lean file.

    Accepts a body or an exact standalone declaration. Full declarations are
    reduced to their proof body; candidate command context is never executed.
    Invalid formats are embedded as tactic text (and rejected by the scorer).
    """
    header = "\n".join(f"import {name}" for name in imports)
    candidate = _candidate_body(proof, statement)
    body = f"{statement}\n{_normalize_proof_body(candidate if candidate is not None else proof)}"
    return f"{header}\n{body}" if header else body


@dataclass
class Evaluation:
    """Outcome of checking one candidate proof — shared by single- and multi-turn."""

    reward: float
    metrics: dict[str, float]
    feedback: str = ""
    errors: list[str] = field(default_factory=list)
    forbidden: list[str] = field(default_factory=list)
    error_kind: str = "proof_error"


async def evaluate_candidate(checker: Checker, proof: str, info: dict) -> Evaluation:
    """Verify one candidate proof against the task. The single source of truth for
    reward + metrics + feedback, used by both the single-turn reward and the
    multi-turn repair env."""
    info = info or {}
    imports = info.get("imports", ["Mathlib"])
    statement = info.get("formal_statement", "")
    environment = info.get("environment", "lean-4.28.0")
    timeout_s = info.get("timeout_s")

    code = build_lean_file(imports, statement, proof)
    metrics = {name: 0.0 for name in _METRIC_NAMES}
    metrics["proof_len"] = float(len(proof))
    metrics["full_declaration"] = 1.0 if _is_full_declaration(proof, statement) else 0.0

    if not _proves_required_statement(proof, statement):
        metrics["forbidden"] = 1.0
        metrics["wrong_theorem"] = 1.0
        return Evaluation(
            reward=0.0,
            metrics=metrics,
            feedback="Proof rejected: answer does not prove the required theorem.",
            forbidden=["wrong_theorem"],
            error_kind="wrong_theorem",
        )

    # Anti-cheat runs first; a forbidden proof never reaches the checker.
    forbidden = check_forbidden(code, allowed_imports=set(imports))
    if forbidden:
        metrics["forbidden"] = 1.0
        return Evaluation(
            reward=0.0,
            metrics=metrics,
            feedback="Proof rejected: " + ", ".join(forbidden),
            forbidden=forbidden,
            error_kind="forbidden",
        )

    started = time.monotonic()
    result = await checker.check(code, environment=environment, timeout_s=timeout_s)
    metrics["checked"] = 1.0
    metrics["check_ms"] = (time.monotonic() - started) * 1000.0
    if result.timed_out:
        metrics["timeout"] = 1.0
        return Evaluation(
            reward=0.0,
            metrics=metrics,
            feedback="Verification timed out.",
            error_kind="timeout",
        )

    syntax_ok, typecheck_ok = classify(result.okay, result.errors)
    metrics["syntax_ok"] = syntax_ok
    metrics["typecheck_ok"] = typecheck_ok
    header_lines = code.count("\n") - proof.count("\n")
    metrics["progress"] = 1.0 if result.okay else proof_progress(
        result.raw, header_lines, proof.count("\n") + 1, syntax_ok, typecheck_ok
    )

    # Gold-standard anti-cheat: a verified proof must not depend on disallowed axioms
    # (sorryAx, native_decide's ofReduceBool, user axioms). Catches smuggling the regex
    # filter can't see. Local-only (`audit_axioms`); other backends skip.
    if result.okay and hasattr(checker, "audit_axioms"):
        target = _DECL_NAME.search(statement)
        audit = checker.audit_axioms
        audit_kwargs = {"environment": environment, "timeout_s": timeout_s}
        if "target_name" in inspect.signature(audit).parameters:
            audit_kwargs["target_name"] = target.group(1) if target else None
        bad = await audit(code, **audit_kwargs)
        if bad is not None:
            metrics["axiom_audit_ran"] = 1.0
        if bad:
            metrics["forbidden"] = 1.0
            metrics["axiom_violation"] = 1.0
            return Evaluation(
                reward=0.0, metrics=metrics, forbidden=bad,
                feedback="Proof rejected: depends on disallowed axioms: " + ", ".join(bad),
                error_kind="axiom_violation",
            )

    if result.okay and metrics["axiom_audit_ran"] == 0.0:
        metrics["verified_without_axiom_audit"] = 1.0

    metrics["verified"] = 1.0 if result.okay else 0.0
    if result.okay:
        error_kind = "verified"
    elif syntax_ok == 0.0:
        error_kind = "syntax_error"
    elif typecheck_ok == 0.0:
        error_kind = "type_error"
    else:
        error_kind = "proof_error"
    return Evaluation(
        reward=1.0 if result.okay else 0.0,
        metrics=metrics,
        feedback=normalize_errors(result.errors),
        errors=result.errors,
        error_kind=error_kind,
    )


def evaluation_trace(proof: str, ev: Evaluation) -> dict:
    """Return a JSON-serializable attempt record for monitoring or external data work."""
    return {
        "proof": proof,
        "proof_hash": proof_hash(proof),
        "reward": ev.reward,
        "metrics": dict(ev.metrics),
        "error_kind": ev.error_kind,
        "feedback": ev.feedback,
        "errors": list(ev.errors),
        "forbidden": list(ev.forbidden),
    }


def proof_hash(proof: str) -> str:
    return hashlib.sha256(proof.encode("utf-8")).hexdigest()


def write_eval_to_state(
    state: dict,
    ev: Evaluation,
    *,
    proof: str | None = None,
    append_attempt: bool = False,
) -> None:
    """Stash an evaluation in rollout state so metric functions can read it back."""
    state["lean_metrics"] = ev.metrics
    state["lean_errors"] = ev.errors
    state["lean_feedback"] = ev.feedback
    state["lean_error_kind"] = ev.error_kind
    if ev.forbidden:
        state["forbidden_reasons"] = ev.forbidden
    else:
        state.pop("forbidden_reasons", None)
    state["last_eval_reward"] = ev.reward
    if proof is not None:
        trace = evaluation_trace(proof, ev)
        state["lean_final_trace"] = trace
        if append_attempt:
            state.setdefault("lean_attempt_traces", []).append(trace)


def make_reward(checker: Checker):
    """Return the single-turn binary reward closure over a ``Checker`` backend."""

    async def lean_reward(completion, info, state, **kwargs) -> float:
        proof = extract_proof(completion)
        ev = await evaluate_candidate(checker, proof, info or {})
        write_eval_to_state(state, ev, proof=proof)
        return ev.reward

    return lean_reward


def _metric_funcs() -> list:
    """Zero-weight metric functions that read diagnostics stashed in ``state``."""

    def _metric(name: str):
        async def metric(state, **kwargs) -> float:
            return float((state.get("lean_metrics") or {}).get(name, 0.0))

        metric.__name__ = name
        return metric

    return [_metric(name) for name in _METRIC_NAMES]


def _attempt_metric_funcs() -> list:
    """Zero-weight counters over structured attempt traces in multi-turn/tool modes."""

    def _traces(state) -> list[dict]:
        return list(state.get("lean_attempt_traces") or [])

    async def num_checks(state, **kwargs) -> float:
        return float(len(_traces(state) or state.get("lean_attempts", [])))

    async def checker_calls(state, **kwargs) -> float:
        return float(sum((t.get("metrics") or {}).get("checked", 0.0) for t in _traces(state)))

    async def forbidden_attempts(state, **kwargs) -> float:
        return float(sum((t.get("metrics") or {}).get("forbidden", 0.0) for t in _traces(state)))

    async def forbidden_rollout(state, **kwargs) -> float:
        return 1.0 if any((t.get("metrics") or {}).get("forbidden", 0.0) for t in _traces(state)) else 0.0

    async def wrong_theorem_attempts(state, **kwargs) -> float:
        return float(sum((t.get("metrics") or {}).get("wrong_theorem", 0.0) for t in _traces(state)))

    async def timeout_attempts(state, **kwargs) -> float:
        return float(sum((t.get("metrics") or {}).get("timeout", 0.0) for t in _traces(state)))

    async def unique_attempts(state, **kwargs) -> float:
        return float(len({t.get("proof_hash") for t in _traces(state) if t.get("proof_hash")}))

    return [
        num_checks,
        checker_calls,
        forbidden_attempts,
        forbidden_rollout,
        wrong_theorem_attempts,
        timeout_attempts,
        unique_attempts,
    ]


# Reward modes (verified is always the weight-1.0 main reward; these add small partial
# credit to specific metrics so a weak model has a gradient before it can fully prove —
# defeats the zero-advantage filter). "process" uses the dense first-error progress signal.
_MODE_WEIGHTS = {
    "binary": {},
    "shaped": {"syntax_ok": 0.05, "typecheck_ok": 0.1},
    "process": {"progress": 0.3},
}


def _build_rubric(funcs, weights) -> vf.Rubric:
    """Construct a Rubric, disabling parallel scoring when the installed verifiers
    supports it (older versions used by some runtimes don't accept the kwarg)."""
    try:
        return vf.Rubric(funcs=funcs, weights=weights, parallelize_scoring=False)
    except TypeError:
        rubric = vf.Rubric(funcs=funcs, weights=weights)
        if hasattr(rubric, "parallelize_scoring"):
            rubric.parallelize_scoring = False
        return rubric


def make_rubric(checker: Checker, reward_shaping: bool = False, reward_mode: str | None = None) -> vf.Rubric:
    """Single-turn rubric: binary verified reward (weight 1.0) + metrics.

    ``reward_mode`` (``"binary"``|``"shaped"``|``"process"``) controls partial credit;
    ``reward_shaping=True`` is a back-compat alias for ``"shaped"``. ``process`` weights the
    dense first-error ``progress`` signal. The checker is called once per row; metric
    functions read state the reward wrote.
    """
    mode = reward_mode or ("shaped" if reward_shaping else "binary")
    shaped = _MODE_WEIGHTS.get(mode, {})
    metrics = _metric_funcs()
    weights = [1.0] + [shaped.get(name, 0.0) for name in _METRIC_NAMES]
    return _build_rubric(funcs=[make_reward(checker), *metrics], weights=weights)


def make_repair_rubric() -> vf.Rubric:
    """Multi-turn rubric: the repair env already verified each turn and stashed the
    final outcome in ``state``; the reward just reads it (no re-check)."""

    async def repair_reward(state, **kwargs) -> float:
        return float(state.get("last_eval_reward", 0.0))

    metrics = [*_metric_funcs(), *_attempt_metric_funcs()]
    return _build_rubric(funcs=[repair_reward, *metrics], weights=[1.0, *[0.0] * len(metrics)])


def make_tool_rubric() -> vf.Rubric:
    """Agentic rubric: the tool env recorded each ``lean_check`` outcome in ``state``;
    reward is 1.0 if any in-budget attempt verified (the model found a valid proof)."""

    async def tool_reward(state, **kwargs) -> float:
        return float(state.get("best_reward", 0.0))

    metrics = [*_metric_funcs(), *_attempt_metric_funcs()]
    return _build_rubric(funcs=[tool_reward, *metrics], weights=[1.0, *[0.0] * len(metrics)])


def make_step_rubric() -> vf.Rubric:
    """Step-level rubric: the tactic-by-tactic env stashed `step_reward` (1.0 if the proof
    reached `no goals`)."""

    async def step_reward(state, **kwargs) -> float:
        return float(state.get("step_reward", 0.0))

    async def num_tactics(state, **kwargs) -> float:
        return float(state.get("applied_turn", 0))

    metrics = [*_metric_funcs(), num_tactics]
    return _build_rubric(funcs=[step_reward, *metrics], weights=[1.0, *[0.0] * len(metrics)])
