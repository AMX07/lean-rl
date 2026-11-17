"""Check real reference proofs; no model inference, trainer, or paid API calls."""
import argparse
import asyncio
import os
from pathlib import Path

from lean_verifier_env.checkers import load_checker
from lean_verifier_env.datasets.loaders import load_dataset_split
from lean_verifier_env.reward import evaluate_candidate


async def main(core=False):
    if core:
        from lean_verifier_env.local_lean import LocalLeanChecker
        root = Path(__file__).resolve().parents[1]
        os.environ["ELAN_HOME"] = str(root / ".tools/elan")
        os.environ["PATH"] = str(root / ".tools/elan/bin") + os.pathsep + os.environ["PATH"]
        checker = LocalLeanChecker(repl_cmd=str(root / ".tools/repl/.lake/build/bin/repl"),
                                   project_dir=str(root / ".tools/repl"),
                                   preload_imports=("Init",), pool_size=1)
        rows = [{"answer": "rfl", "info": {"id": "core_increment", "imports": ["Init"],
                 "formal_statement": "theorem increment (n : Nat) : n + 1 = n + 1 := by"}}]
    else:
        checker = load_checker("local", lean_pool_size=1)
        rows = load_dataset_split("toy")
    try:
        for row in rows:
            result = await evaluate_candidate(checker, row["answer"], row["info"])
            print(row["info"]["id"], result.reward, result.feedback)
            if result.reward != 1:
                raise SystemExit("Reference proof failed")
    finally:
        await checker.close()
    print(f"All {len(rows)} reference proofs verified.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core", action="store_true", help="Use workspace Lean without Mathlib")
    asyncio.run(main(parser.parse_args().core))
