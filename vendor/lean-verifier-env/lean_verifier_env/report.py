"""Eval report (build plan v4 / milestone M6).

Summarizes an evaluation into a compact markdown report: pass rate, per-metric means,
and a by-difficulty breakdown (curriculum credibility). Consumes either a verifiers
``GenerateOutputs`` (from ``env.evaluate``) or raw rewards/metrics, so it works after a
``vf-eval`` run or programmatically.

Usage:
    python -m lean_verifier_env.report results.json
where results.json is ``{"rewards": [...], "metrics": {...}, "difficulties": [...]}``.
"""

from __future__ import annotations

import json
import sys


_SAFETY_METRICS = {
    "forbidden": "forbidden proof",
    "forbidden_rollout": "rollout with a forbidden attempt",
    "wrong_theorem": "wrong theorem",
    "axiom_violation": "axiom violation",
    "verified_without_axiom_audit": "verified without axiom audit",
    "timeout": "timeout",
}


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def summarize(
    rewards: list[float],
    metrics: dict[str, list[float]] | None = None,
    difficulties: list[str] | None = None,
) -> dict:
    """Aggregate per-rollout rewards/metrics into report figures.

    ``verified`` (if present) is the correctness signal for the pass rate; otherwise the
    raw reward is used.
    """
    metrics = metrics or {}
    n = len(rewards)
    pass_values = metrics.get("verified", rewards)
    summary = {
        "n": n,
        "pass_rate": _mean(pass_values),
        "reward_mean": _mean(rewards),
        "metric_means": {name: _mean(values) for name, values in metrics.items()},
        "by_difficulty": {},
    }
    if difficulties:
        buckets: dict[str, list[float]] = {}
        for diff, val in zip(difficulties, pass_values):
            buckets.setdefault(diff or "unknown", []).append(val)
        summary["by_difficulty"] = {
            diff: {"n": len(vals), "pass_rate": _mean(vals)} for diff, vals in sorted(buckets.items())
        }
    return summary


def pass_at_k(values: list[float], num_examples: int) -> tuple[float, int]:
    """Fraction of examples solved by at least one of their k rollouts.

    ``values`` is the flat per-rollout success list from ``env.evaluate`` with
    ``rollouts_per_example=k``. verifiers repeats the dataset *dataset-major* (``inputs =
    inputs.repeat(k)``), so example ``i``'s rollouts are at indices ``i, i+num_examples,
    i+2*num_examples, ...``. Returns ``(pass@k, k)``; a value >= 0.5 counts as a success.
    """
    if not values or num_examples <= 0:
        return 0.0, 0
    k = len(values) // num_examples
    if k <= 0:
        return 0.0, 0
    hits = 0
    for i in range(num_examples):
        group = [values[i + j * num_examples] for j in range(k) if i + j * num_examples < len(values)]
        if any(v >= 0.5 for v in group):
            hits += 1
    return hits / num_examples, k


def from_generate_outputs(outputs) -> dict:
    """Build a summary from a verifiers ``GenerateOutputs`` (duck-typed)."""
    info = getattr(outputs, "info", None) or []
    difficulties = [(i or {}).get("difficulty") for i in info] if info else None
    return summarize(
        rewards=list(getattr(outputs, "reward", []) or []),
        metrics=dict(getattr(outputs, "metrics", {}) or {}),
        difficulties=difficulties,
    )


def render_markdown(summary: dict, title: str = "Lean verifier eval report") -> str:
    """Render a summary as a markdown report."""
    lines = [
        f"# {title}",
        "",
        f"- examples: **{summary['n']}**",
        f"- pass rate (verified): **{summary['pass_rate']:.1%}**",
        f"- mean reward: **{summary['reward_mean']:.3f}**",
    ]
    if summary["metric_means"]:
        lines += ["", "## Metrics (mean)", "", "| metric | mean |", "|--------|------|"]
        for name, value in summary["metric_means"].items():
            lines.append(f"| {name} | {value:.3f} |")
        safety = [
            (label, summary["metric_means"][name])
            for name, label in _SAFETY_METRICS.items()
            if name in summary["metric_means"]
        ]
        if safety:
            lines += ["", "## Safety telemetry", "", "| signal | rate |", "|--------|------|"]
            for label, value in safety:
                lines.append(f"| {label} | {value:.1%} |")
    if summary["by_difficulty"]:
        lines += ["", "## By difficulty", "", "| bucket | n | pass rate |", "|--------|---|-----------|"]
        for bucket, stats in summary["by_difficulty"].items():
            lines.append(f"| {bucket} | {stats['n']} | {stats['pass_rate']:.1%} |")
    return "\n".join(lines) + "\n"


def summarize_search(attempts: list[dict]) -> dict:
    """Summarize a MaxProof-lite JSONL attempt archive."""
    by_task: dict[str, list[dict]] = {}
    for attempt in attempts:
        by_task.setdefault(str(attempt.get("task_id") or "unknown"), []).append(attempt)
    solved = {
        task_id for task_id, rows in by_task.items()
        if any(float(row.get("reward", 0.0)) == 1.0 for row in rows)
    }
    total = len(attempts)

    def rate(predicate) -> float:
        return sum(1 for attempt in attempts if predicate(attempt)) / total if total else 0.0

    solved_attempts = sum(
        min(int(row.get("attempt", 0)) for row in by_task[task_id] if row.get("reward") == 1.0)
        for task_id in solved
    )
    return {
        "tasks": len(by_task),
        "solved": len(solved),
        "pass_rate": len(solved) / len(by_task) if by_task else 0.0,
        "attempts": total,
        "checker_calls": sum(bool(a.get("checker_called")) for a in attempts),
        "attempts_per_solved": solved_attempts / len(solved) if solved else 0.0,
        "forbidden_rate": rate(lambda a: a.get("error_kind") in {"forbidden", "axiom_violation"}),
        "wrong_theorem_rate": rate(lambda a: a.get("error_kind") == "wrong_theorem"),
        "timeout_rate": rate(lambda a: a.get("error_kind") == "timeout"),
        "duplicate_rate": rate(lambda a: a.get("duplicate_of") is not None),
    }


def render_search_markdown(summary: dict, title: str = "Lean MaxProof-lite report") -> str:
    """Render search effectiveness, cost, and anti-hacking telemetry."""
    return "\n".join([
        f"# {title}",
        "",
        f"- tasks: **{summary['tasks']}**",
        f"- solved: **{summary['solved']}**",
        f"- verified pass rate: **{summary['pass_rate']:.1%}**",
        f"- generated attempts: **{summary['attempts']}**",
        f"- Lean checker calls: **{summary['checker_calls']}**",
        f"- attempts to first solution: **{summary['attempts_per_solved']:.2f}**",
        "",
        "## Safety telemetry",
        "",
        "| signal | rate |",
        "|--------|------|",
        f"| forbidden proof | {summary['forbidden_rate']:.1%} |",
        f"| wrong theorem | {summary['wrong_theorem_rate']:.1%} |",
        f"| timeout | {summary['timeout_rate']:.1%} |",
        f"| duplicate candidate | {summary['duplicate_rate']:.1%} |",
        "",
    ])


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print("usage: python -m lean_verifier_env.report results.json", file=sys.stderr)
        return 2
    with open(argv[0], encoding="utf-8") as fh:
        data = json.load(fh)
    summary = summarize(
        rewards=data.get("rewards", []),
        metrics=data.get("metrics"),
        difficulties=data.get("difficulties"),
    )
    print(render_markdown(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
