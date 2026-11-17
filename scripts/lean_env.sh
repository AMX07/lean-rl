# Source this file from bash or zsh after running scripts/setup_lean.sh.
_lean_rl_root="$(git rev-parse --show-toplevel)"
export ELAN_HOME="$_lean_rl_root/.tools/elan"
export PATH="$ELAN_HOME/bin:$PATH"
export LEAN_CHECKER_BACKEND=local
export LEAN_REPL_CMD="$_lean_rl_root/.tools/repl/.lake/build/bin/repl"
export LEAN_PROJECT_DIR="$_lean_rl_root/.tools/mathlib"
export LEAN_POOL_SIZE=2
unset _lean_rl_root
