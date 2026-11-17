# Local patch of lean-verifier-env

Source copied from the published `pradheep/lean-verifier-env` 0.10.0 wheel
(author Pradheep; declared license MIT). Upstream:
https://github.com/Mantissagithub/lean-verifier-env

Local version: `0.10.0+local.2`. This copy is installed editable in the workspace
virtual environment. It has not been published or submitted upstream.

## Changes

- `reward.py`: keep the task's trusted theorem statement; only accept tactic
  bodies or an exact standalone declaration, stripping its header before
  reconstructing the check input. Reject surrounding sections, namespaces,
  variables, and additional declarations. Match the whole target statement,
  including any `let ... := ...` in its type, rather than splitting at the
  first `:=`.
- `lean_source.py`: shared lexical masking of nested comments and strings.
- `filters.py`: apply forbidden-token checks to code rather than comment/string
  contents, so harmless mentions of `sorry` no longer cause false rejections.
- `pyproject.toml`: reproducible editable installation with the legacy framework
  version used in the real-Lean tests.

This deliberately narrows the accepted submission format. Arbitrary full Lean
files, surrounding declarations, and some otherwise valid Lean syntax using the
restricted command tokens are rejected. This patch prevents the reproduced
assumption attack by restricting context; it is not a general elaborated-type
comparator or an exhaustive soundness audit. Proof-local `have` lemmas remain
supported. The shared candidate scorer covers single/repair/tool/final-RLM
scoring; the separate step-mode completion logic is unchanged.

Regression command (run from workspace root):

```sh
UV_CACHE_DIR=/tmp/lean-rl-uv-cache uv run --python .venv/bin/python examples/run_verifier.py
```

All 12 examples run against the real local Lean REPL. See `examples/README.md`
and the before/after JSON results for the findings.

## Source-complete environment update (local.2)

The repository now represents all 38 files in the published Prime source archive.
It restores the scripts/config/sandbox and adds tests, uv locking, setup scripts,
containers, CI, and training templates; see the root README and docs.

Full framework rollout testing also required compatibility fixes: remove the
obsolete string dataset `task` column, use registered `vf.stop` callbacks instead
of overriding the runtime's final `is_completed` method, return messages (not
messages/state tuples) from repair/step responses, and read the latest trajectory
completion before scoring. Step sessions register cleanup. Single, repair, tool,
and step run end-to-end with simulated model responses and actual Lean checks.
These changes preserve the reward formulas and original correctness fixes.
RLM sandbox runs now require an explicitly configured user-owned image.
