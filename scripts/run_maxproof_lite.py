#!/usr/bin/env python3
"""Run cost-bounded MaxProof-lite search and write an attempt archive + report.

The model API must be OpenAI-compatible. The search archive is JSONL with one row per
candidate; it can be analyzed directly or transformed into external SFT/repair data after
successful proofs are rechecked.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[0].parent))

from lean_verifier_env.checkers import load_checker  # noqa: E402
from lean_verifier_env.datasets.loaders import load_dataset_split  # noqa: E402
from lean_verifier_env.report import render_search_markdown, summarize_search  # noqa: E402
from lean_verifier_env.search import run_search  # noqa: E402

PROVIDERS = {
    "prime": ("https://api.pinference.ai/api/v1", "PRIME_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
}


def _build_client(provider: str):
    from verifiers.types import ClientConfig
    from verifiers.utils.client_utils import load_prime_config, setup_openai_client

    base_url, key_var = PROVIDERS.get(provider, (provider, "OPENAI_API_KEY"))
    if key_var == "PRIME_API_KEY":
        if not (os.environ.get("PRIME_API_KEY") or load_prime_config().get("api_key")):
            raise SystemExit("missing Prime credentials: run `prime login` or set PRIME_API_KEY")
    elif not os.environ.get(key_var):
        raise SystemExit(f"missing API key: set {key_var}")
    return setup_openai_client(ClientConfig(api_base_url=base_url, api_key_var=key_var))


def _batch_generator(client, model: str, temperature: float):
    async def generate(prompts: list[str]) -> list[str]:
        async def one(prompt: str) -> str:
            response = await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
            )
            return response.choices[0].message.content or ""

        return await asyncio.gather(*(one(prompt) for prompt in prompts))

    return generate


def _write_jsonl(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


async def _run(args) -> tuple[list[dict], str]:
    client = _build_client(args.provider)
    generator = _batch_generator(client, args.model, args.temperature)
    checker_cfg = {
        key: value for key, value in {
            "lean_repl_cmd": args.lean_repl_cmd,
            "lean_project_dir": args.lean_project_dir,
            "lean_pool_size": args.lean_pool_size,
        }.items() if value is not None
    }
    checker = load_checker(args.backend, **checker_cfg)
    dataset = load_dataset_split(args.dataset, allow_eval_split=True)
    limit = min(args.max_tasks, len(dataset))
    archive: list[dict] = []
    for index in range(limit):
        info = dict(dataset[index]["info"])
        print(f"[search] {index + 1}/{limit} {info.get('id')}", file=sys.stderr)
        result = await run_search(
            checker,
            info,
            generator,
            num_candidates=args.num_candidates,
            rewrite_rounds=args.rewrite_rounds,
            beam_width=args.beam_width,
        )
        for attempt in result.attempts:
            attempt["model"] = args.model
            attempt["dataset"] = args.dataset
        archive.extend(result.attempts)
    return archive, render_search_markdown(summarize_search(archive))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate, verify, repair, and select Lean proofs.")
    parser.add_argument("--model", required=True)
    parser.add_argument("--dataset", default="toy")
    parser.add_argument("--provider", default="openrouter")
    parser.add_argument("--backend", default="local", choices=("local", "axle"))
    parser.add_argument("--max-tasks", type=int, default=3)
    parser.add_argument("--num-candidates", type=int, default=2)
    parser.add_argument("--rewrite-rounds", type=int, default=1)
    parser.add_argument("--beam-width", type=int, default=1)
    parser.add_argument("--temperature", type=float, default=0.6)
    parser.add_argument("--lean-repl-cmd")
    parser.add_argument("--lean-project-dir")
    parser.add_argument("--lean-pool-size", type=int)
    parser.add_argument("--output", help="JSONL archive path")
    args = parser.parse_args(argv)

    archive, report = asyncio.run(_run(args))
    output = Path(args.output) if args.output else Path("outputs") / f"maxproof_lite_{int(time.time())}.jsonl"
    _write_jsonl(archive, output)
    report_path = output.with_suffix(".md")
    report_path.write_text(report, encoding="utf-8")
    print(report)
    print(f"wrote {output} and {report_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
