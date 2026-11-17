"""Regression checks using the patched environment and real Lean, without mocks."""
import asyncio
import json
import os
from pathlib import Path
from importlib.metadata import version

from lean_verifier_env.local_lean import LocalLeanChecker
from lean_verifier_env.reward import build_lean_file, evaluate_candidate, make_rubric

ROOT = Path(__file__).resolve().parents[1]
os.environ["ELAN_HOME"] = str(ROOT / ".tools/elan")
os.environ["PATH"] = str(ROOT / ".tools/elan/bin") + os.pathsep + os.environ["PATH"]

GOOD = "theorem increment_correct (n : Nat) : (let result := n + 1; result) = n + 1 := by"
BAD = "theorem increment_correct (n : Nat) : (let result := n + 2; result) = n + 1 := by"

CASES = [
    ("correct", GOOD, "rfl"),
    ("buggy_implementation", BAD, "rfl"),
    ("incomplete_proof", GOOD, "skip"),
    ("forbidden_placeholder", BAD, "sorry"),
    ("harmless_comment", GOOD, "-- sorry is not used here\nrfl"),
    ("extra_assumption", BAD,
     "section\nvariable (h : False)\ninclude h\n" + BAD + "\n  exact False.elim h\nend"),
    ("standalone_declaration", GOOD, GOOD + "\n  rfl"),
    ("changed_statement", BAD, GOOD + "\n  rfl"),
    ("trailing_declaration", GOOD, GOOD + "\n  rfl\ntheorem unrelated : True := by trivial"),
    ("dedented_escape", BAD, "  skip\ntheorem unrelated : True := by trivial"),
    ("nested_comment", GOOD, "/- sorry /- admit -/ unsafe -/\nrfl"),
    ("comment_then_real_placeholder", BAD, "/- harmless -/\nsorry"),
]

EXPECTED_VERIFIED = {"correct", "harmless_comment", "standalone_declaration", "nested_comment"}


async def main():
    checker = LocalLeanChecker(
        repl_cmd=str(ROOT / ".tools/repl/.lake/build/bin/repl"),
        project_dir=str(ROOT / ".tools/repl"),
        preload_imports=("Init",), pool_size=1, timeout_s=60,
    )
    output = ROOT / "examples/results"
    output.mkdir(exist_ok=True)
    records = []
    print("Testing lean-verifier-env", version("lean-verifier-env"), flush=True)
    try:
        for name, statement, proof in CASES:
            info = {"imports": ["Init"], "formal_statement": statement,
                    "environment": "lean-4.28.0", "timeout_s": 60}
            code = build_lean_file(info["imports"], statement, proof)
            (output / f"{name}.lean").write_text(code + "\n")
            ev = await evaluate_candidate(checker, proof, info)
            raw = await checker.check(code, environment="lean-4.28.0")
            # Use the installed rubric's actual functions and weights, not copied formulas.
            rewards = {}
            for mode in ("binary", "shaped", "process"):
                rubric = make_rubric(checker, reward_mode=mode)
                state = {}
                scores = [await fn(completion=proof, info=info, state=state)
                          for fn in rubric.funcs]
                rewards[mode] = sum(s * w for s, w in zip(scores, rubric.weights))
            printed = None
            if name == "extra_assumption":
                # Preserve the original exploit separately from the canonical check input.
                submitted = "import Init\n" + proof
                (output / "extra_assumption.submitted.lean").write_text(submitted + "\n")
                check = await checker.check(submitted + "\n#print increment_correct\n#print axioms increment_correct",
                                            environment="lean-4.28.0")
                printed = check.raw
            record = {"case": name, "lean_accepts": raw.okay,
                      "env_verified": ev.metrics["verified"], "rewards": rewards,
                      "error_kind": ev.error_kind, "feedback": ev.feedback,
                      "metrics": ev.metrics, "printed_theorem": printed}
            records.append(record)
            print(f"{name}: verified={ev.metrics['verified']}, rewards={rewards}, reason={ev.error_kind}", flush=True)
    finally:
        await checker.close()
    (output / "results.json").write_text(json.dumps(records, indent=2) + "\n")
    for record in records:
        expected = float(record["case"] in EXPECTED_VERIFIED)
        assert record["env_verified"] == expected, record
        assert record["rewards"]["binary"] == expected, record
        if record["error_kind"] in {"wrong_theorem", "forbidden"}:
            assert all(value == 0 for value in record["rewards"].values()), record
        if expected:
            assert record["metrics"]["axiom_audit_ran"] == 1, record
    print(f"All {len(records)} real-Lean regression cases passed.")


if __name__ == "__main__":
    asyncio.run(main())
