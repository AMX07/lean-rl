# Lean RL verifier experiments

Local experiments and a correctness patch for Prime Intellect's
[`pradheep/lean-verifier-env`](https://app.primeintellect.ai/dashboard/environments/pradheep/lean-verifier-env),
based on published version 0.10.0 by Pradheep. The patched copy is version
`0.10.0+local.1`; this is an independent project, not an upstream release.

The example models a Python increment function in Lean, verifies its proof,
and tests malformed and adversarial submissions against the real Lean backend.
The local patch rejects theorem wrappers that introduce extra assumptions and
stops rejecting harmless comments containing forbidden words.

- [Experiment, results, and regression runner](examples/README.md)
- [Patch scope and compatibility limitations](vendor/lean-verifier-env/README.md)
- [Original results](examples/results/before-fix.json)
- [Patched results](examples/results/results.json)

## Python setup

```sh
uv venv .venv
uv pip install --python .venv/bin/python -e vendor/lean-verifier-env
```

The regression runner also needs Lean 4.28.0 and the community REPL v4.28.0.
It expects a workspace-local elan installation in `.tools/elan` and a built
REPL at `.tools/repl/.lake/build/bin/repl`. These downloaded tools and the
virtual environment are intentionally excluded from Git. Mathlib is not needed
for the small examples, which use Lean's `Init` library.

```sh
uv run --python .venv/bin/python examples/run_verifier.py
```

The recorded patched run passed all 12 regression cases. This is not a general
soundness audit: the patch deliberately restricts submissions to tactic bodies
or exact standalone declarations rather than arbitrary Lean files. The Python
model is translated by hand; no Python-to-Lean translation is verified here.

Vendored package authorship and its declared MIT license are retained in
`vendor/lean-verifier-env/pyproject.toml`.
