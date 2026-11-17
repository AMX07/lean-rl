"""Recursive Language Model (RLM) proving mode — wraps Prime's `RLMEnv`.

RLM (Prime Intellect's 2026 paradigm, https://www.primeintellect.ai/blog/rlm): the root
model works in a sandboxed REPL, decomposes the problem, and recursively dispatches
sub-pieces to fresh sub-LLM instances (`llm_batch`), with tools usable only by sub-LLMs.

For Lean this is recursive *subgoal decomposition*: the root model uses the Python REPL as
scratch space, batches proof obligations with `llm_batch`, checks candidates with the
in-sandbox `lean_check` CLI, and assembles + verifies the whole proof. Reward = the main
theorem verifies.

Requires verifiers >= 0.1.14 (ships `RLMEnv`) and `prime_sandboxes` (RLMEnv runs the REPL in
a Prime-hosted sandbox). Imported lazily so the rest of the package works without them.
"""

from __future__ import annotations

from .checker import Checker
from .reward import (
    _build_rubric,
    _metric_funcs,
    evaluate_candidate,
    write_eval_to_state,
)

RLM_SYSTEM_PROMPT = (
    "You are proving a Lean 4 theorem in a Python REPL. Use the REPL as scratch space, "
    "decompose the target into independent helper lemmas or tactic sketches, and batch "
    "sub-LLM requests with llm_batch([...]) when possible. Sub-LLMs can use lean_check(code) "
    "to verify complete Lean files through the configured checker. Assemble the final "
    "complete Lean file for the exact target theorem, then set answer['content'] to that "
    "file and answer['ready'] = True. Never use `sorry`, `admit`, axioms, `native_decide`, "
    "`unsafe`, or a different theorem."
)

# Lean + Mathlib sandbox image (built from sandbox/Dockerfile, pushed to the Prime
# registry). Override with the `sandbox_image` kwarg or LEAN_RLM_SANDBOX_IMAGE env var.
LEAN_SANDBOX_IMAGE = "cmg769y0r000vlqtz7j8u5o8a/lean-rlm-sandbox:v1"

# System prompt for the in-sandbox path: the root model runs Python in a sandbox with
# Lean + Mathlib + the `lean_check` CLI baked in. This follows Prime's RLM shape: keep
# the root context small, batch semantic subproblems through llm_batch, and write the
# final answer into answer["content"] only when ready.
RLM_SANDBOX_SYSTEM_PROMPT = (
    "You are proving a Lean 4 theorem by recursive decomposition, working in a Python REPL "
    "inside a sandbox that has Lean 4 + Mathlib installed.\n\n"
    "Use the REPL as persistent scratch space. Use llm_batch([prompt1, prompt2, ...]) to "
    "ask sub-LLMs for independent helper lemmas or proof attempts in parallel; do not call "
    "sub-LLMs one-by-one when a batch is possible.\n\n"
    "To verify a candidate, run the baked `lean_check` command from Python. It compiles a "
    "complete Lean file against Mathlib and returns '✓ verified' or compiler errors:\n"
    "    import subprocess\n"
    "    def check_lean(code: str) -> str:\n"
    "        r = subprocess.run(['lean_check'], input=code, capture_output=True, text=True)\n"
    "        return (r.stdout + r.stderr).strip()\n\n"
    "Strategy:\n"
    "1. Restate the exact theorem header you were given.\n"
    "2. Split hard goals into `have`-style helper lemmas or tactic sketches.\n"
    "3. Send independent helper tasks through llm_batch([...]) and compare the returned proofs.\n"
    "4. Verify each useful candidate and the final assembled file with check_lean(...).\n"
    "5. Only after the exact original theorem verifies, set answer['content'] to the final "
    "complete Lean file and answer['ready'] = True.\n\n"
    "Never use `sorry`, `admit`, axioms, `native_decide`, `unsafe`, or a different theorem. "
    "The final scorer re-checks the exact required theorem."
)


def _import_rlm_env():
    try:
        from verifiers.envs.experimental.rlm_env import RLMEnv
    except Exception as exc:  # pragma: no cover - depends on optional runtime
        raise RuntimeError(
            "mode='rlm' needs verifiers>=0.1.14 (ships RLMEnv) and prime_sandboxes. "
            "Install with the 'rlm' extra and run on Prime where sandboxes are available."
        ) from exc
    return RLMEnv


def _answer_content(state: dict) -> str:
    final = state.get("final_answer")
    if isinstance(final, str):
        return final
    if isinstance(final, dict):
        return str(final.get("content") or "")
    ans = state.get("answer")
    if isinstance(ans, dict):
        return str(ans.get("content") or "")
    if isinstance(ans, str):
        return ans
    completion = state.get("completion") or []
    if isinstance(completion, list):
        for msg in reversed(completion):
            if msg.get("role") == "assistant" and msg.get("content"):
                return str(msg["content"])
    return ""


def make_lean_check_tool(checker: Checker):
    async def lean_check(code: str) -> str:
        """Verify a complete Lean 4 file (imports + theorem + proof). Returns whether it
        compiles, with errors if not. Use this to check each subgoal proof."""
        result = await checker.check(code, environment="lean-4.28.0", timeout_s=60)
        if result.okay:
            return "✓ verified"
        return "✗ does not compile:\n" + "\n".join(result.errors[:5])

    lean_check.__name__ = "lean_check"
    return lean_check


def make_rlm_rubric(checker: Checker):
    """Reward = the root model's final assembled proof verifies the main theorem (strict)."""

    async def rlm_reward(state, info=None, **kwargs) -> float:
        info = info or state.get("info") or {}
        answer = _answer_content(state)
        ev = await evaluate_candidate(checker, answer, info)
        write_eval_to_state(state, ev, proof=answer)
        return ev.reward

    metrics = _metric_funcs()
    return _build_rubric(funcs=[rlm_reward, *metrics], weights=[1.0, *[0.0] * len(metrics)])


def make_rlm_env(
    checker: Checker,
    dataset,
    system_prompt: str | None = None,
    in_sandbox_verify: bool = True,
    sandbox_image: str | None = None,
    sandbox_cpu_cores: int = 4,
    sandbox_memory_gb: int = 8,
    sandbox_disk_size_gb: int = 12,
    lean_check_timeout_s: int = 300,
    **rlm_kwargs,
):
    """Build a Lean RLM environment over Prime's `RLMEnv` + a strict final-proof rubric.

    Two verification paths:

    * ``in_sandbox_verify=True`` (default): the root model verifies proofs *inside* the
      sandbox via the `lean_check` CLI baked into a Lean+Mathlib image
      (``sandbox/Dockerfile``). Self-contained — needs no Lean on Prime's host container.
      The image + resources are sized for Mathlib (override via ``sandbox_image`` or the
      ``LEAN_RLM_SANDBOX_IMAGE`` env var).
    * ``in_sandbox_verify=False``: legacy path — exposes a host-side `lean_check` sub-tool
      backed by our ``Checker`` (requires Lean on the host running the env).

    ``rlm_kwargs`` pass through to ``RLMEnv`` (sub_model, max_turns, ...).
    """
    import os

    rlm_env_cls = _import_rlm_env()

    if in_sandbox_verify:
        image = sandbox_image or os.environ.get("LEAN_RLM_SANDBOX_IMAGE") or LEAN_SANDBOX_IMAGE
        sandbox_env = dict(rlm_kwargs.pop("sandbox_environment_vars", {}) or {})
        sandbox_env.setdefault("LEAN_CHECK_TIMEOUT_S", str(lean_check_timeout_s))
        rlm_kwargs.setdefault("repl_language", "python")
        return rlm_env_cls(
            dataset=dataset,
            rubric=make_rlm_rubric(checker),
            system_prompt=system_prompt or RLM_SANDBOX_SYSTEM_PROMPT,
            sandbox_docker_image=image,
            sandbox_cpu_cores=sandbox_cpu_cores,
            sandbox_memory_gb=sandbox_memory_gb,
            sandbox_disk_size_gb=sandbox_disk_size_gb,
            sandbox_environment_vars=sandbox_env,
            **rlm_kwargs,
        )

    rlm_kwargs.setdefault("repl_language", "python")
    return rlm_env_cls(
        sub_tools=[make_lean_check_tool(checker)],
        dataset=dataset,
        rubric=make_rlm_rubric(checker),
        system_prompt=system_prompt or RLM_SYSTEM_PROMPT,
        **rlm_kwargs,
    )
