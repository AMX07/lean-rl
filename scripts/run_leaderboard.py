#!/usr/bin/env python3
"""In-process tiered leaderboard for the Lean verifier env (build plan P1c).

Runs ``env.evaluate(...)`` directly in this process — no ``prime eval`` subprocess — so it
avoids the sandbox/exit-144 flakiness and the timeout-truncated output we hit running the
CLI in the harness. Sweeps a list of models x datasets, captures pass@1 and pass@k, and
writes a markdown leaderboard to ``outputs/``.

Example (local Lean backend):
    OPENROUTER_API_KEY=... python scripts/run_leaderboard.py \
        --models deepseek/deepseek-chat,openai/gpt-oss-120b \
        --datasets minif2f-test,proofnet,putnam \
        --num-examples 20 --rollouts-per-example 4 \
        --backend local --lean-repl-cmd /path/repl --lean-project-dir /path/mathlib

The aggregation/rendering helpers (``run_one``, ``render_leaderboard``) are import-safe and
unit-tested offline in ``tests/test_leaderboard.py`` (no client/Lean needed).
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

# Allow running as a plain script (python scripts/run_leaderboard.py).
sys.path.insert(0, str(Path(__file__).resolve().parents[0].parent))

from lean_verifier_env import load_environment  # noqa: E402
from lean_verifier_env.report import pass_at_k, render_markdown, summarize  # noqa: E402

# provider shorthand -> (api_base_url, api_key_var). Mirrors `prime eval run -p`.
# A raw base_url string also works in place of a shorthand (uses OPENAI_API_KEY).
PROVIDERS = {
    "prime": ("https://api.pinference.ai/api/v1", "PRIME_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
}


def _build_client(provider: str):
    """Build an AsyncOpenAI client for a provider shorthand (or a raw base_url).

    Reuses verifiers' client plumbing so this matches ``prime eval run`` exactly,
    including the Prime key fallback from ``~/.prime/config.json`` and the
    ``X-Prime-Team-ID`` header for ``--provider prime``.
    """
    from verifiers.types import ClientConfig
    from verifiers.utils.client_utils import load_prime_config

    base_url, key_var = PROVIDERS.get(provider, (provider, "OPENAI_API_KEY"))
    # Prime resolves its key from ~/.prime/config.json when PRIME_API_KEY is unset
    # (handled inside setup_client); other providers need their key exported.
    if key_var == "PRIME_API_KEY":
        if not (os.environ.get("PRIME_API_KEY") or load_prime_config().get("api_key")):
            raise SystemExit("missing Prime credentials: run `prime login` or set PRIME_API_KEY")
    elif not os.environ.get(key_var):
        raise SystemExit(f"missing API key: set {key_var}")
    # Environment.evaluate_sync expects verifiers' ClientConfig, not a raw SDK client.
    return ClientConfig(api_base_url=base_url, api_key_var=key_var)


def _extract_rewards_metrics(outputs):
    """Pull (rewards, metrics) out of a verifiers eval result.

    verifiers >=0.1.x returns a ``GenerateOutputs`` TypedDict
    (``{"outputs": [RolloutOutput, ...], ...}``) where each rollout carries its
    own ``reward`` and ``metrics``. Falls back to the older attribute-style
    (``outputs.reward`` / ``outputs.metrics``) for compatibility.
    """
    rollouts = outputs.get("outputs") if isinstance(outputs, dict) else None
    if rollouts is not None:
        rewards = [float(o.get("reward") or 0.0) for o in rollouts]
        names: set[str] = set()
        for o in rollouts:
            names |= set((o.get("metrics") or {}).keys())
        metrics = {
            name: [float((o.get("metrics") or {}).get(name, 0.0)) for o in rollouts]
            for name in names
        }
        return rewards, metrics
    rewards = list(getattr(outputs, "reward", []) or [])
    metrics = dict(getattr(outputs, "metrics", {}) or {})
    return rewards, metrics


def run_one(env, client, model, num_examples, rollouts_per_example, sampling_args=None):
    """Evaluate one (env, model) and return a leaderboard row dict.

    pass@1 = mean per-rollout success; pass@k = fraction of examples any rollout solved.
    Uses the ``verified`` metric as the success signal, falling back to raw reward.
    """
    # verifiers' Environment.evaluate is a coroutine; use the sync wrapper since this
    # script runs synchronously.
    outputs = env.evaluate_sync(
        client,
        model,
        sampling_args=sampling_args,
        num_examples=num_examples,
        rollouts_per_example=rollouts_per_example,
    )
    rewards, metrics = _extract_rewards_metrics(outputs)
    success = metrics.get("verified", rewards)
    summary = summarize(rewards, metrics)
    p_at_k, k = pass_at_k(success, num_examples)
    return {
        "model": model,
        "n": num_examples,
        "k": k,
        "pass@1": summary["pass_rate"],
        f"pass@{k}": p_at_k,
        "reward_mean": summary["reward_mean"],
    }


def render_leaderboard(rows: list[dict], title: str = "Lean verifier leaderboard") -> str:
    """Render rows (one per dataset x model) as a markdown table."""
    lines = [f"# {title}", ""]
    if not rows:
        return "\n".join(lines + ["(no results)", ""])
    by_dataset: dict[str, list[dict]] = {}
    for row in rows:
        by_dataset.setdefault(row.get("dataset", "?"), []).append(row)
    for dataset, drows in by_dataset.items():
        k = max((r.get("k", 1) for r in drows), default=1)
        lines += [
            f"## {dataset}",
            "",
            f"| model | n | pass@1 | pass@{k} | mean reward |",
            "|-------|---|--------|---------|-------------|",
        ]
        for r in sorted(drows, key=lambda x: x.get(f"pass@{k}", x["pass@1"]), reverse=True):
            pk = r.get(f"pass@{k}", r["pass@1"])
            lines.append(
                f"| {r['model']} | {r['n']} | {r['pass@1']:.1%} | {pk:.1%} | {r['reward_mean']:.3f} |"
            )
        lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="In-process Lean leaderboard (pass@1 + pass@k).")
    ap.add_argument("--models", required=True, help="comma-separated model ids")
    ap.add_argument("--datasets", required=True, help="comma-separated dataset names")
    ap.add_argument("--num-examples", type=int, default=20)
    ap.add_argument("--rollouts-per-example", type=int, default=1)
    ap.add_argument("--provider", default="openrouter",
                    help="inference provider: prime (Prime Intellect), openrouter, openai, "
                         "or a raw OpenAI-compatible base URL (default: openrouter)")
    ap.add_argument("--backend", default="local", help="checker backend: local or axle")
    ap.add_argument("--lean-repl-cmd")
    ap.add_argument("--lean-project-dir")
    ap.add_argument("--lean-pool-size", type=int)
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--output", default=None, help="markdown path (default outputs/leaderboard_<ts>.md)")
    args = ap.parse_args(argv)

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    datasets = [d.strip() for d in args.datasets.split(",") if d.strip()]
    client = _build_client(args.provider)
    sampling_args = {"temperature": args.temperature} if args.temperature is not None else None

    checker_cfg = {}
    if args.lean_repl_cmd:
        checker_cfg["lean_repl_cmd"] = args.lean_repl_cmd
    if args.lean_project_dir:
        checker_cfg["lean_project_dir"] = args.lean_project_dir
    if args.lean_pool_size:
        checker_cfg["lean_pool_size"] = args.lean_pool_size

    rows: list[dict] = []
    for dataset in datasets:
        # The leaderboard is an evaluation harness, so it may load the eval-only benchmark
        # presets (minif2f-test/proofnet/putnam) — opt in past the contamination gate.
        env = load_environment(
            dataset_name=dataset, checker_backend=args.backend, eval_split=True, **checker_cfg
        )
        for model in models:
            print(f"[run] {dataset} x {model} ...", file=sys.stderr)
            row = run_one(env, client, model, args.num_examples, args.rollouts_per_example, sampling_args)
            row["dataset"] = dataset
            rows.append(row)
            k = row["k"]
            pk = row.get(f"pass@{k}", 0.0)
            print(f"      pass@1={row['pass@1']:.1%} pass@{k}={pk:.1%}", file=sys.stderr)

    md = render_leaderboard(rows)
    out_path = Path(args.output) if args.output else Path("outputs") / f"leaderboard_{int(time.time())}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")
    print(md)
    print(f"\nwrote {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
