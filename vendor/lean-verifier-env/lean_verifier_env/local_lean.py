"""Local Lean 4 + Mathlib verification backend.

Runs proofs through a pool of persistent `leanprover-community/repl` subprocesses
instead of the hosted AXLE API. This removes the 20-concurrent rate limit: parallelism
is bounded by ``pool_size`` (CPU), which is what RL training needs.

The REPL protocol (JSON in / JSON out, blank-line terminated):

    -> {"cmd": "import Mathlib"}              <- {"env": 0, "messages": [...]}
    -> {"cmd": "theorem ...", "env": 0}       <- {"messages": [...], "sorries": [...]}

Mathlib is imported once per worker to get a reusable environment; subsequent checks
run against that env so they don't pay the (tens of seconds) import cost each time.

Setup is external (not a pip dep): install Lean via elan, build the `repl` binary, and
point ``LEAN_PROJECT_DIR`` at a Mathlib project. See the README "Verification backends".
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import shutil
from dataclasses import dataclass

from .checker import CheckResult

# The repl binary, run directly (not via `lake env`, which hangs a persistent pool).
# LEAN_PATH is injected separately so `import Mathlib` resolves.
DEFAULT_REPL_CMD = "repl"
DEFAULT_TIMEOUT_S = 60.0

# Axioms a sound Mathlib proof is allowed to depend on. Anything else (notably `sorryAx`
# or a user-declared axiom) means the "proof" is cheating.
ALLOWED_AXIOMS = frozenset({"propext", "Classical.choice", "Quot.sound"})
_THM_NAME = re.compile(r"\b(?:theorem|lemma|def|example)\s+([A-Za-z_][\w'.]*)")
_AXIOM_LINE = re.compile(r"depends on axioms:\s*\[([^\]]*)\]")


def theorem_name(statement: str) -> str | None:
    m = _THM_NAME.search(statement)
    return m.group(1) if m else None


def disallowed_axioms_from_messages(messages: list[dict]) -> list[str]:
    """Parse `#print axioms` output; return axioms outside the allowed set (empty = clean)."""
    for m in messages:
        text = str(m.get("data", ""))
        match = _AXIOM_LINE.search(text)
        if match:
            axioms = [a.strip() for a in match.group(1).split(",") if a.strip()]
            return [a for a in axioms if a not in ALLOWED_AXIOMS]
        if "does not depend on any axioms" in text:
            return []
    return []


class LeanReplError(RuntimeError):
    """Raised when the Lean REPL cannot be started or produces unparseable output."""


def parse_repl_output(data: dict) -> CheckResult:
    """Convert a Lean REPL response into a :class:`CheckResult` (pure / testable).

    A proof is okay only if there are no error-severity messages and no leftover
    ``sorry`` holes. Warnings and info messages do not fail a proof.
    """
    messages = data.get("messages") or []
    errors = [str(m.get("data", "")).strip() for m in messages if m.get("severity") == "error"]
    sorries = data.get("sorries") or []
    if sorries:
        errors.append("declaration uses 'sorry'")
    return CheckResult(okay=not errors, errors=errors, raw=data)


def split_imports(code: str) -> tuple[list[str], str]:
    """Split leading ``import`` lines from the rest of a Lean file.

    Returns ``(import_names, body)``. Lets the checker run the body against a
    preloaded environment when the imports are already loaded.
    """
    lines = code.splitlines()
    imports: list[str] = []
    idx = 0
    while idx < len(lines):
        stripped = lines[idx].strip()
        if stripped == "":
            idx += 1
            continue
        if stripped.startswith("import "):
            imports.append(stripped.split(None, 1)[1].strip())
            idx += 1
            continue
        break
    return imports, "\n".join(lines[idx:])


@dataclass
class _Worker:
    proc: asyncio.subprocess.Process | None
    env_id: int | None = None


class LocalLeanChecker:
    """Pooled local Lean checker. Implements the ``Checker`` protocol."""

    def __init__(
        self,
        repl_cmd: str | None = None,
        project_dir: str | None = None,
        preload_imports: tuple[str, ...] = ("Mathlib",),
        pool_size: int | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        lean_path: str | None = None,
    ) -> None:
        self.repl_cmd = shlex.split(repl_cmd or os.environ.get("LEAN_REPL_CMD", DEFAULT_REPL_CMD))
        self.project_dir = project_dir or os.environ.get("LEAN_PROJECT_DIR")
        self.preload_imports = tuple(preload_imports)
        if pool_size is None:
            pool_size = int(os.environ.get("LEAN_POOL_SIZE", os.cpu_count() or 4))
        self.pool_size = max(1, pool_size)
        self.timeout_s = timeout_s
        # Run the repl binary directly with LEAN_PATH set, NOT `lake env repl`: the lake
        # wrapper breaks the persistent stdin/stdout pipe and hangs. lean_path is
        # resolved once via `lake env printenv LEAN_PATH` in project_dir if not given.
        self.lean_path = lean_path or os.environ.get("LEAN_PATH")
        self._pool: asyncio.Queue[_Worker] | None = None
        self._lock = asyncio.Lock()
        self._sessions: dict[str, _Worker] = {}  # step-mode: pinned worker per proof
        self._session_seq = 0

    # -- public API -------------------------------------------------------------

    async def check(self, code: str, environment: str, timeout_s: float | None = None) -> CheckResult:
        await self._ensure_pool()
        assert self._pool is not None
        timeout = timeout_s if timeout_s is not None else self.timeout_s

        imports, body = split_imports(code)
        reuse_env = set(imports) <= set(self.preload_imports)

        worker = await self._pool.get()
        try:
            if reuse_env and worker.env_id is not None:
                cmd = {"cmd": body, "env": worker.env_id}
            else:
                cmd = {"cmd": code}
            try:
                data = await self._roundtrip(worker, cmd, timeout)
            except asyncio.TimeoutError:
                await self._kill(worker)
                worker = await self._spawn_worker()
                return CheckResult(okay=False, timed_out=True, errors=["Lean check timed out"])
            return parse_repl_output(data)
        finally:
            self._pool.put_nowait(worker)

    async def verify_proof(
        self, statement: str, proof: str, environment: str, timeout_s: float | None = None
    ) -> CheckResult:
        header = "\n".join(f"import {name}" for name in self.preload_imports)
        code = f"{header}\n{statement}\n{proof}" if header else f"{statement}\n{proof}"
        return await self.check(code, environment, timeout_s)

    async def audit_axioms(
        self,
        code: str,
        environment: str,
        timeout_s: float | None = None,
        target_name: str | None = None,
    ) -> list[str] | None:
        """Return axioms the proved theorem depends on that are NOT allowed (empty = clean).

        Appends `#print axioms <name>` and parses the result. Returns None if the theorem
        name can't be found. Call only on a verified proof. Catches `sorryAx`/user-axiom
        smuggling that the regex filter can't see."""
        name = target_name or theorem_name(code)
        if not name:
            return None
        audited = f"{code}\n#print axioms {name}"
        result = await self.check(audited, environment, timeout_s)
        if result.raw is None:
            return None
        return disallowed_axioms_from_messages(result.raw.get("messages") or [])

    # -- step-level (stateful) interaction: pinned worker + proofState --------------

    async def open_session(self) -> str:
        """Pin a worker for a single proof's tactic sequence; returns a session id."""
        await self._ensure_pool()
        assert self._pool is not None
        worker = await self._pool.get()
        self._session_seq += 1
        sid = f"s{self._session_seq}"
        self._sessions[sid] = worker
        return sid

    async def close_session(self, sid: str) -> None:
        worker = self._sessions.pop(sid, None)
        if worker is not None and self._pool is not None:
            self._pool.put_nowait(worker)

    async def init_proof_state(self, sid: str, statement: str, timeout_s: float | None = None):
        """Open the theorem with a `sorry` hole; return (proof_state_id, goal) for its goal."""
        worker = self._sessions[sid]
        imports, body = split_imports(statement)
        reuse = set(imports) <= set(self.preload_imports)
        cmd = {"cmd": (body if reuse else statement) + "\n  sorry"}
        if reuse and worker.env_id is not None:
            cmd["env"] = worker.env_id
        data = await self._roundtrip(worker, cmd, timeout_s if timeout_s is not None else self.timeout_s)
        sorries = data.get("sorries") or []
        if not sorries:
            return None, None
        return sorries[0].get("proofState"), sorries[0].get("goal", "")

    async def apply_tactic(self, sid: str, proof_state: int, tactic: str, timeout_s: float | None = None):
        """Run one tactic against a proof state. Returns (new_state, goals, errors, done)."""
        worker = self._sessions[sid]
        cmd = {"tactic": tactic, "proofState": proof_state}
        try:
            data = await self._roundtrip(worker, cmd, timeout_s if timeout_s is not None else self.timeout_s)
        except asyncio.TimeoutError:
            return proof_state, None, ["tactic timed out"], False
        goals = data.get("goals")
        errors = [str(m.get("data", "")).strip() for m in (data.get("messages") or []) if m.get("severity") == "error"]
        new_state = data.get("proofState", proof_state)
        done = goals == [] and not errors
        return new_state, goals, errors, done

    async def close(self) -> None:
        """Terminate all worker processes."""
        if self._pool is None:
            return
        while not self._pool.empty():
            await self._kill(self._pool.get_nowait())
        self._pool = None

    # -- pool / process management (overridable in tests) -----------------------

    async def _ensure_pool(self) -> None:
        async with self._lock:
            if self._pool is not None:
                return
            await self._resolve_lean_path()
            pool: asyncio.Queue[_Worker] = asyncio.Queue()
            for _ in range(self.pool_size):
                pool.put_nowait(await self._spawn_worker())
            self._pool = pool

    def _subprocess_env(self, **extra: str) -> dict:
        """os.environ with the Lean toolchain bin dir prepended to PATH (plus any
        ``extra`` vars). The Python process may run under a venv that didn't inherit
        elan, but both `lake env` and the `repl` binary (which shells out to `lean`)
        need the toolchain executables — without them the REPL silently returns an
        empty response. Resolve the bin dir via PATH, falling back to the default elan
        install dir."""
        exe = shutil.which("lean") or shutil.which("lake")
        bindir = os.path.dirname(exe) if exe else os.path.expanduser("~/.elan/bin")
        env = {**os.environ, **extra}
        if os.path.isdir(bindir):
            env["PATH"] = os.pathsep.join([bindir, os.environ.get("PATH", "")])
        return env

    async def _resolve_lean_path(self) -> None:
        """If LEAN_PATH is unknown, derive it once from the project via lake."""
        if self.lean_path or not self.project_dir:
            return
        lake = shutil.which("lake") or os.path.expanduser("~/.elan/bin/lake")
        try:
            proc = await asyncio.create_subprocess_exec(
                lake, "env", "printenv", "LEAN_PATH",
                cwd=self.project_dir,
                env=self._subprocess_env(),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
            self.lean_path = out.decode().strip() or None
        except (FileNotFoundError, asyncio.TimeoutError):
            self.lean_path = None  # fall back to inherited environment

    async def _spawn_worker(self) -> _Worker:
        env = self._subprocess_env(**({"LEAN_PATH": self.lean_path} if self.lean_path else {}))
        try:
            proc = await asyncio.create_subprocess_exec(
                *self.repl_cmd,
                cwd=self.project_dir,
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise LeanReplError(
                f"Could not start Lean REPL {self.repl_cmd!r}. Install Lean (elan), build "
                "leanprover-community/repl, and set LEAN_REPL_CMD / LEAN_PROJECT_DIR. "
                "See the README 'Verification backends' section."
            ) from exc
        worker = _Worker(proc=proc)
        if self.preload_imports:
            cmd = {"cmd": "\n".join(f"import {name}" for name in self.preload_imports)}
            data = await self._roundtrip(worker, cmd, self.timeout_s)
            worker.env_id = data.get("env")
        return worker

    async def _roundtrip(self, worker: _Worker, cmd: dict, timeout: float) -> dict:
        assert worker.proc is not None and worker.proc.stdin is not None
        worker.proc.stdin.write((json.dumps(cmd) + "\n\n").encode())
        await worker.proc.stdin.drain()
        raw = await asyncio.wait_for(self._read_response(worker.proc.stdout), timeout)
        if not raw.strip():
            raise LeanReplError("empty response from Lean REPL")
        return json.loads(raw)

    @staticmethod
    async def _read_response(stdout: asyncio.StreamReader) -> str:
        """Read one blank-line-terminated JSON object from the REPL."""
        chunks: list[bytes] = []
        while True:
            line = await stdout.readline()
            if line == b"":  # EOF
                break
            if line.strip() == b"":
                if chunks:
                    break
                continue
            chunks.append(line)
        return b"".join(chunks).decode()

    @staticmethod
    async def _kill(worker: _Worker) -> None:
        if worker.proc is None:
            return
        try:
            worker.proc.kill()
            await worker.proc.wait()
        except ProcessLookupError:
            pass
