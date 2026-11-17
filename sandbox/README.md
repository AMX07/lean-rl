# Lean + Mathlib sandbox

Restored from published 0.10.0 source. The local Dockerfile targets Lean 4.28.0
and Mathlib v4.28.0 to match scoring. The original image used Lean 4.31.0-rc2.

```sh
docker build -t lean-rlm-sandbox:local sandbox
printf 'import Mathlib\nexample : 1 + 1 = 2 := by norm_num\n' \
  | docker run --rm -i lean-rlm-sandbox:local lean_check
```

For hosted recursive runs, publish to your own accessible registry and set
`LEAN_RLM_SANDBOX_IMAGE` or the `sandbox_image` argument. There is no default
dependency on the upstream author's registry account.

The CLI provides process feedback; the environment separately checks the final
proof of the required theorem. See `docs/TRAINING.md`. Docker builds were not
executed locally because the Docker daemon was unavailable.
