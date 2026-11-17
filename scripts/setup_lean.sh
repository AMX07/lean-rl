#!/usr/bin/env bash
# Workspace-local toolchain; pass --with-mathlib for the bundled training datasets.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export ELAN_HOME="$ROOT/.tools/elan"
export PATH="$ELAN_HOME/bin:$PATH"
mkdir -p "$ROOT/.tools"
if [[ ! -x "$ELAN_HOME/bin/elan" ]]; then
  curl -fsSL https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh -o "$ROOT/.tools/elan-init.sh"
  sh "$ROOT/.tools/elan-init.sh" -y --no-modify-path --default-toolchain leanprover/lean4:v4.28.0
fi
elan toolchain install leanprover/lean4:v4.28.0
if [[ ! -d "$ROOT/.tools/repl" ]]; then
  git clone --depth 1 --branch v4.28.0 https://github.com/leanprover-community/repl.git "$ROOT/.tools/repl"
fi
test "$(git -C "$ROOT/.tools/repl" rev-parse HEAD)" = 527590ce2b9f3b5c4a9a1031e5b8fcfb909b9a4a
(cd "$ROOT/.tools/repl" && lake build)
if [[ "${1:-}" == "--with-mathlib" ]]; then
  if [[ ! -d "$ROOT/.tools/mathlib" ]]; then
    git clone --depth 1 --branch v4.28.0 https://github.com/leanprover-community/mathlib4.git "$ROOT/.tools/mathlib"
  fi
  test "$(cat "$ROOT/.tools/mathlib/lean-toolchain")" = leanprover/lean4:v4.28.0
  (cd "$ROOT/.tools/mathlib" && lake exe cache get)
fi
printf 'Lean setup ready. Run: source scripts/lean_env.sh\n'
