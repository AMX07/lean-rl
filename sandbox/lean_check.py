#!/usr/bin/env python3
"""In-sandbox Lean verifier for the RLM proving mode.

The RLM root model (running Python in the sandbox REPL) calls this to verify a
candidate proof *inside the sandbox*, against the baked-in Mathlib. Verification =
compile a complete Lean file with `lake env lean`; a clean compile (exit 0, no
`error`) means the proof type-checks.

Usage:
    lean_check path/to/file.lean      # verify a file
    echo '<lean code>' | lean_check   # verify code on stdin

Prints "✓ verified" + exit 0 on success, or "✗ does not compile:" + the Lean
diagnostics + exit 1 on failure.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

PROJECT = os.environ.get("LEAN_SANDBOX_PROJECT", "/opt/mathlib")
TIMEOUT_S = int(os.environ.get("LEAN_CHECK_TIMEOUT_S", "300"))


def _read_code() -> str:
    if len(sys.argv) > 1 and sys.argv[1] not in ("-", "--stdin"):
        with open(sys.argv[1], "r", encoding="utf-8") as f:
            return f.read()
    return sys.stdin.read()


def main() -> int:
    code = _read_code()
    # Write into the project dir so `import Mathlib` resolves against its oleans.
    fd, path = tempfile.mkstemp(suffix=".lean", dir=PROJECT)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(code)
        try:
            proc = subprocess.run(
                ["lake", "env", "lean", path],
                cwd=PROJECT,
                capture_output=True,
                text=True,
                timeout=TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            print(f"✗ verification timed out after {TIMEOUT_S}s")
            return 1
    finally:
        os.unlink(path)

    out = (proc.stdout + proc.stderr).strip()
    low = out.lower()
    # `sorry`/`admit` only emit a *warning* ("declaration uses 'sorry'") and exit 0, so an
    # error-only check would pass them. Reject any sorry/admit as unproven.
    if "sorry" in low or "declaration uses" in low:
        print("✗ proof is incomplete (uses sorry/admit):")
        print(out)
        return 1
    if proc.returncode == 0 and "error" not in low:
        print("✓ verified")
        return 0
    print("✗ does not compile:")
    print(out)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
