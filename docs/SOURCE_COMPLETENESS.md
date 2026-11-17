# Source provenance and completeness

On 2026-11-17, downloaded the published source with:

```sh
uvx prime env pull pradheep/lean-verifier-env@0.10.0 --target /tmp/lean-rl-upstream-010
```

All **38 files** in that archive are represented in this repository. Generated
Prime account metadata is excluded. `upstream-manifest.json` records original
paths, SHA-256 hashes, and current locations. A test enforces file coverage.

The initial repository had every Python module and dataset from the wheel, but
omitted published scripts, endpoint config, sandbox files, and original README
and build metadata. Those are now recovered. Package code remains in
`vendor/lean-verifier-env/lean_verifier_env`; scripts/configs/sandbox are at root.

The advertised GitHub URL returned repository-not-found. The source archive
contains **no tests, `configs/rl` directory, training Dockerfile, or license text**,
despite references to some of these in its README. We cannot claim to possess
unpublished files from the author's development repository. Tests, training
templates, setup scripts, root Dockerfile, CI, and uv lockfile here are our
additions, not recovered upstream files.

## Additional local changes

- Restore published helper files and optional package metadata.
- Pin legacy verifiers and create a root uv project/lockfile.
- Update evaluation/search client calls for that runtime's API.
- Fix dataset routing and repair/step callbacks for complete runtime rollouts,
  verified using simulated model responses and real Lean checking.
- Align sandbox Lean/Mathlib with scoring at version 4.28.0.
- Require an explicit user-owned RLM sandbox image.
- Add tests, setup, containers, and dependency/build validation.
- Add schema-validated training templates based on Prime RL v0.6.0,
  commit `a7f7aae114b0937fa09dbd7155be6b64358db8a6`.

Original README and pyproject are preserved verbatim under `docs/UPSTREAM_*`.
