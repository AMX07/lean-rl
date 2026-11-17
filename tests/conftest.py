import os
from pathlib import Path

import pytest
import pytest_asyncio

ROOT = Path(__file__).resolve().parents[1]


@pytest_asyncio.fixture
async def checker():
    from lean_verifier_env.local_lean import LocalLeanChecker

    repl = ROOT / ".tools/repl/.lake/build/bin/repl"
    if not repl.exists():
        pytest.skip("Run bash scripts/setup_lean.sh for real-Lean integration tests")
    os.environ["ELAN_HOME"] = str(ROOT / ".tools/elan")
    os.environ["PATH"] = str(ROOT / ".tools/elan/bin") + os.pathsep + os.environ["PATH"]
    backend = LocalLeanChecker(repl_cmd=str(repl), project_dir=str(ROOT / ".tools/repl"),
                               preload_imports=("Init",), pool_size=1)
    try:
        yield backend
    finally:
        await backend.close()
