import importlib.util
import json
from pathlib import Path
import tomllib

import pytest
import verifiers as vf

from lean_verifier_env import load_environment
from lean_verifier_env.datasets.loaders import load_dataset_split
from lean_verifier_env.filters import check_forbidden
from lean_verifier_env.reward import evaluate_candidate
from lean_verifier_env.rlm import _import_rlm_env

ROOT = Path(__file__).resolve().parents[1]
INFO = {"imports": ["Init"], "formal_statement": "theorem inc (n : Nat) : n + 1 = n + 1 := by"}


@pytest.mark.parametrize("mode", ["single", "repair", "tool", "step"])
def test_environment_loads(mode):
    env = vf.load_environment("lean-verifier-env", mode=mode, checker_backend="local", lean_pool_size=1)
    assert len(env.dataset) == 5


def test_rlm_dependency_available():
    assert _import_rlm_env().__name__ == "RLMEnv"


def test_rlm_requires_owned_image(monkeypatch):
    monkeypatch.delenv("LEAN_RLM_SANDBOX_IMAGE", raising=False)
    with pytest.raises(ValueError, match="requires sandbox_image"):
        load_environment(mode="rlm")


def test_restored_script_clients(monkeypatch):
    from verifiers.types import ClientConfig
    monkeypatch.setenv("OPENAI_API_KEY", "test-placeholder-not-a-real-key")
    for filename in ("run_leaderboard", "run_maxproof_lite"):
        spec = importlib.util.spec_from_file_location(filename, ROOT / "scripts" / (filename + ".py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        client = module._build_client("openai")
        if filename == "run_leaderboard":
            assert isinstance(client, ClientConfig)
        else:
            assert callable(client.chat.completions.create)


@pytest.mark.parametrize("name", ["minif2f-test", "proofnet", "putnam"])
def test_benchmarks_are_held_out(name):
    with pytest.raises(ValueError):
        load_dataset_split(name)
    assert len(load_dataset_split(name, allow_eval_split=True)) > 0


def test_published_source_coverage():
    manifest = json.loads((ROOT / "docs/upstream-manifest.json").read_text())
    assert len(manifest["files"]) == 38
    for entry in manifest["files"]:
        assert (ROOT / entry["local_path"]).is_file(), entry


@pytest.mark.parametrize("path", sorted((ROOT / "configs/rl").glob("*.toml")))
def test_training_environment_configs(path):
    config = tomllib.loads(path.read_text())
    for spec in config["orchestrator"]["train"]["env"]:
        assert spec["id"] == "lean-verifier-env"  # never silently load the unpatched Hub copy
        assert spec["args"]["dataset_name"] == "lean-workbook"
        env = load_environment(**spec["args"])
        assert len(env.dataset) > 0


def test_comment_filter_keeps_real_placeholders():
    assert not check_forbidden('/- sorry /- admit -/ unsafe -/\nexact "sorry"')
    assert "sorry" in check_forbidden('/- harmless -/\nsorry')


@pytest.mark.lean
async def test_regression_suite(checker):
    # Import the runnable experiment without requiring examples to be a package.
    spec = importlib.util.spec_from_file_location("regressions", ROOT / "examples/run_verifier.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name, statement, proof in module.CASES:
        result = await evaluate_candidate(checker, proof, {**INFO, "formal_statement": statement})
        assert result.reward == float(name in module.EXPECTED_VERIFIED), name


@pytest.mark.lean
async def test_tool_retries_keep_best_reward(checker):
    from lean_verifier_env.agentic import make_lean_tool
    tool = make_lean_tool(checker)
    state = {}
    await tool("skip", INFO, state)
    assert state["best_reward"] == 0
    await tool("rfl", INFO, state)
    assert state["best_reward"] == 1
    await tool("sorry", INFO, state)
    assert state["best_reward"] == 1
    assert len(state["lean_attempt_traces"]) == 3


@pytest.mark.lean
async def test_repair_feedback_and_success(checker):
    env = load_environment(mode="repair", checker_backend="local")
    env.checker = checker
    state = {"info": INFO, "turn": 0}
    messages = [{"role": "assistant", "content": "skip"}]
    feedback = await env.env_response(messages, state)
    assert "unsolved goals" in feedback[0]["content"]
    state["turn"] = 1
    assert await env.proof_verified([{"role": "assistant", "content": "rfl"}], state)
    assert state["last_eval_reward"] == 1


@pytest.mark.lean
async def test_step_proof_state(checker):
    sid = await checker.open_session()
    try:
        ps, goal = await checker.init_proof_state(sid, INFO["formal_statement"])
        assert ps is not None and goal
        _, goals, errors, done = await checker.apply_tactic(sid, ps, "rfl")
        assert done and goals == [] and errors == []
    finally:
        await checker.close_session(sid)


@pytest.mark.lean
async def test_recursive_final_reward(checker):
    from lean_verifier_env.rlm import make_rlm_rubric
    rubric = make_rlm_rubric(checker)
    state = {"final_answer": "rfl", "info": INFO}
    assert await rubric.funcs[0](state=state) == 1
