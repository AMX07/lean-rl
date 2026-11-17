# Real Lean verifier experiment

Tested lean-verifier-env 0.10.0 and local patches 0.10.0+local.1/local.2 with verifiers 0.1.14, Lean 4.28.0, and
leanprover-community/repl v4.28.0 (commit 527590ce2b9f3b5c4a9a1031e5b8fcfb909b9a4a).
Uses the unmodified LocalLeanChecker with the patched candidate scorer and rubric
functions. No mocked verification and no inference/API calls. Core Lean's Init
is sufficient for these examples; Mathlib is not required.

## Example and scope

`increment.py` returns n + 1. Its deliberately buggy variant returns n + 2.
The Lean theorem inlines the function body using `let result := ...` and asserts
that it equals n + 1 for all natural numbers. This is a handwritten model for
nonnegative integer inputs, not a verified Python-to-Lean translation or a claim
about all Python runtime behavior.

## Run in this workspace

```sh
UV_CACHE_DIR=/tmp/lean-rl-uv-cache uv run --python .venv/bin/python examples/run_verifier.py
```

The script writes complete Lean submissions and machine-readable results into
`results/`. Local Lean/REPL binaries reside in the ignored `.tools/` directory.
Python dependencies can be installed in a new uv virtual environment with:

```sh
uv venv .venv
uv pip install --python .venv/bin/python -e vendor/lean-verifier-env
```

## Observed results after patch

| Case | Binary | Shaped | Process |
|---|---:|---:|---:|
| Correct implementation, rfl proof | 1 | 1.15 | 1.30 |
| Buggy implementation, rfl proof | 0 | 0.15 | 0 |
| Correct implementation, unfinished skip proof | 0 | 0.15 | 0 |
| False target with sorry placeholder | 0 | 0 | 0 |
| Correct proof with harmless comment containing sorry | 1 | 1.15 | 1.30 |
| False target with added False assumption | 0 | 0 | 0 |

All 12 regression cases pass, including exact standalone declarations, changed
target statements, trailing declarations, dedented declarations, nested comments,
and real placeholders following comments. Rejections for forbidden constructs
and mismatched theorem context must receive zero in all three reward modes.

The original six-case results are preserved in `results/before-fix.json`.
`results/results.json` contains the patched run. The original exploit's source
is preserved in `results/extra_assumption.submitted.lean`; other `.lean` files
show the actual reconstructed checker input.

The shaped partial rewards are intentional and do not indicate verification.

### Original false rejection: comments

The original filter searched raw source for forbidden words, including comments. Therefore
`-- sorry is not used here` followed by `rfl` is rejected before verification,
although the same file passes Lean. The patch masks comments and strings before
checking forbidden tokens.

### Original false acceptance relative to the requested theorem: extra assumption

A full declaration can introduce `variable (h : False)` and `include h` before
the exact requested theorem header. Its proof uses `False.elim h`.
The original environment accepts the header text and awards full reward, including after
its axiom audit. However, Lean prints the actual type as:

```lean
False → ∀ (n : Nat), (let result := n + 2; result) = n + 1
```

This is a valid conditional theorem with no axioms; it does not prove the requested
unconditional statement. At n = 0 the requested statement says 2 = 1.
This is a theorem-matching failure in the environment, not unsoundness in Lean.
The raw printed type and axiom-audit evidence are saved in `results/results.json`.
The patched scorer rejects this wrapper before executing it and gives zero reward
in all modes. It accepts proof bodies or exact standalone declarations and rebuilds
the theorem using the trusted statement. Arbitrary full-file command contexts are
now unsupported; see the vendored package README for scope and limitations.
