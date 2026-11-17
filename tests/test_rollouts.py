"""Full framework rollouts: simulated model responses, actual Lean verification."""
import json

import httpx
from openai import AsyncOpenAI
import pytest
from verifiers.clients.openai_chat_completions_client import OpenAIChatCompletionsClient

from lean_verifier_env import load_environment


@pytest.mark.lean
@pytest.mark.parametrize("mode", ["single", "repair", "tool", "step"])
async def test_complete_rollout(mode, checker, monkeypatch, tmp_path):
    # Inject only the configured real backend, not a mock checker.
    monkeypatch.setattr("lean_verifier_env.env.load_checker", lambda *a, **k: checker)
    task = tmp_path / "tasks.jsonl"
    task.write_text(json.dumps({"id": "inc", "imports": ["Init"],
        "formal_statement": "theorem inc (n : Nat) : n + 1 = n + 1 := by",
        "proof": "rfl"}) + "\n")
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        turn = len(requests)
        message = {"role": "assistant", "content": "rfl"}
        finish = "stop"
        if mode in {"repair", "step"} and turn == 1:
            message["content"] = "skip"
        if mode == "tool" and turn == 1:
            message = {"role": "assistant", "content": None, "tool_calls": [
                {"id": "check1", "type": "function", "function":
                 {"name": "lean_check", "arguments": json.dumps({"proof": "rfl"})}}]}
            finish = "tool_calls"
        return httpx.Response(200, json={"id": f"test-{turn}", "object": "chat.completion",
            "created": 0, "model": "test-model", "choices": [
                {"index": 0, "message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})

    sdk = AsyncOpenAI(api_key="test-placeholder", http_client=httpx.AsyncClient(
        transport=httpx.MockTransport(respond)))
    client = OpenAIChatCompletionsClient(sdk)
    env = load_environment(dataset_name=str(task), mode=mode, checker_backend="local",
                           max_turns=1 if mode == "single" else 2)
    try:
        outputs = await env.evaluate(client=client, model="test-model", num_examples=1,
                                     rollouts_per_example=1, max_concurrent=1)
        assert outputs["outputs"][0]["reward"] == 1, outputs
        assert len(requests) == (1 if mode == "single" else 2)
        if mode == "repair":
            assert "unsolved goals" in json.dumps(requests[-1])
        if mode == "tool":
            assert "Proof verified" in json.dumps(requests[-1])
    finally:
        await client.close()
