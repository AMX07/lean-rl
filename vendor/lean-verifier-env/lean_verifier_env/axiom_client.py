"""Thin wrapper around the AXLE (Axiom Lean Engine) proof-checking API.

Per the build plan (section 5, "AXLE integration layer") all external tool calls go
through one client so timeouts, retries, caching, and secret handling live in a single
place. For v0 we use two AXLE operations:

- ``check``        -> fast diagnostics on a full Lean file (used for the reward).
- ``verify_proof`` -> validate a candidate proof against a statement (kept for v0+).

Results are cached by a hash of ``environment + code`` so the same full Lean file is
never checked twice during a rollout or eval (plan section 5 / testing checklist).

The API key is read once from the ``AXLE_API_KEY`` environment variable. It is never
logged. Construction never fails on a missing key; the error is raised only when a
network call is actually attempted, with a clear setup message.
"""

from __future__ import annotations

import asyncio
import hashlib
import os

import httpx

from .checker import CheckResult

DEFAULT_URL = "https://axle.axiommath.ai"
DEFAULT_TIMEOUT_S = 30.0
DEFAULT_RETRIES = 2
# AXLE's documented concurrent-request limit (AXLE_MAX_CONCURRENCY). The client never
# exceeds this regardless of how many rollouts call it at once, so we don't get 429s.
DEFAULT_MAX_CONCURRENCY = 20


def _parse_declarations(data: dict | list) -> list[dict]:
    """Normalize an AXLE extract_theorems/extract_decls response into a list of
    ``{"name", "code", "dependencies"}`` dicts. Best-effort across plausible shapes."""
    if isinstance(data, dict):
        items = data.get("theorems") or data.get("declarations") or data.get("results") or []
    else:
        items = data
    out: list[dict] = []
    for item in items:
        if isinstance(item, str):
            out.append({"name": "", "code": item, "dependencies": []})
        elif isinstance(item, dict):
            code = item.get("code") or item.get("declaration") or item.get("theorem") or ""
            if code:
                out.append(
                    {
                        "name": item.get("name", ""),
                        "code": code,
                        "dependencies": item.get("dependencies") or [],
                    }
                )
    return out


class AxleError(RuntimeError):
    """Base error for AXLE client failures."""


class AxleNotConfigured(AxleError):
    """Raised when an AXLE call is attempted without an API key configured."""


class AxleTimeout(AxleError):
    """Raised when AXLE does not respond within the configured timeout/retries."""


class AxleClient:
    """Async wrapper around the AXLE HTTP API with caching and timeouts."""

    def __init__(
        self,
        api_key: str | None = None,
        url: str | None = None,
        timeout_s: float | None = None,
        retries: int = DEFAULT_RETRIES,
        max_concurrency: int | None = None,
    ) -> None:
        self._api_key = api_key or os.environ.get("AXLE_API_KEY")
        self.url = (url or os.environ.get("AXLE_API_URL") or DEFAULT_URL).rstrip("/")
        self.timeout_s = timeout_s if timeout_s is not None else DEFAULT_TIMEOUT_S
        self.retries = retries
        if max_concurrency is None:
            max_concurrency = int(os.environ.get("AXLE_MAX_CONCURRENCY", DEFAULT_MAX_CONCURRENCY))
        self.max_concurrency = max_concurrency
        self._sem = asyncio.Semaphore(max_concurrency)
        self._cache: dict[str, CheckResult] = {}

    # -- public API -------------------------------------------------------------

    async def check(self, code: str, environment: str, timeout_s: float | None = None) -> CheckResult:
        """Check a full Lean file for errors. Cached by (environment, code)."""
        key = self._cache_key("check", environment, code)
        if key in self._cache:
            return self._cache[key]
        # Cap in-flight AXLE calls at the documented concurrency limit. Cache hits above
        # never reach here, so they don't consume a slot.
        async with self._sem:
            result = await self._raw_check(code, environment, timeout_s)
        self._cache[key] = result
        return result

    async def verify_proof(
        self, statement: str, proof: str, environment: str, timeout_s: float | None = None
    ) -> CheckResult:
        """Validate a candidate ``proof`` against a ``statement``. Cached."""
        key = self._cache_key("verify_proof", environment, statement + "\n" + proof)
        if key in self._cache:
            return self._cache[key]
        async with self._sem:
            result = await self._raw_verify_proof(statement, proof, environment, timeout_s)
        self._cache[key] = result
        return result

    # -- code transformation ops (task generation only; never a reward source) --

    async def extract_theorems(self, code: str, environment: str, timeout_s: float | None = None) -> list[dict]:
        """Split a Lean file into theorems with their dependencies (for task gen).

        Best-effort response parsing — AXLE's exact shape is normalized by
        :func:`_parse_declarations`. Verify against the live API before relying on it.
        """
        async with self._sem:
            data = await self._post("/api/v1/extract_theorems", {"content": code, "environment": environment}, timeout_s)
        return _parse_declarations(data)

    async def theorem2sorry(self, code: str, environment: str, timeout_s: float | None = None) -> str:
        """Strip proofs, replacing them with ``sorry`` (used to build statements)."""
        async with self._sem:
            data = await self._post("/api/v1/theorem2sorry", {"content": code, "environment": environment}, timeout_s)
        return str(data.get("content") or data.get("result") or "")

    # -- network layer (override / monkeypatch in tests) ------------------------

    async def _raw_check(self, code: str, environment: str, timeout_s: float | None) -> CheckResult:
        payload = {"content": code, "environment": environment}
        try:
            return self._parse(await self._post("/api/v1/check", payload, timeout_s))
        except AxleTimeout:
            return CheckResult(okay=False, timed_out=True, errors=["AXLE request timed out"])

    async def _raw_verify_proof(
        self, statement: str, proof: str, environment: str, timeout_s: float | None
    ) -> CheckResult:
        payload = {"statement": statement, "proof": proof, "environment": environment}
        try:
            return self._parse(await self._post("/api/v1/verify_proof", payload, timeout_s))
        except AxleTimeout:
            return CheckResult(okay=False, timed_out=True, errors=["AXLE request timed out"])

    # -- internals --------------------------------------------------------------

    async def _post(self, path: str, payload: dict, timeout_s: float | None) -> dict:
        """POST to AXLE and return the raw JSON. Raises AxleTimeout / AxleError."""
        if not self._api_key:
            raise AxleNotConfigured(
                "AXLE_API_KEY is not set. Export it (see README) before running an eval; "
                "the environment will not silently score 0.0 without a checker."
            )
        timeout = timeout_s if timeout_s is not None else self.timeout_s
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        last_exc: Exception | None = None
        async with httpx.AsyncClient(base_url=self.url, timeout=timeout) as client:
            for _ in range(self.retries + 1):
                try:
                    resp = await client.post(path, json=payload, headers=headers)
                except httpx.TimeoutException as exc:
                    last_exc = exc
                    continue
                if resp.status_code in (429, 503):
                    last_exc = AxleError(f"AXLE transient error {resp.status_code}")
                    continue
                resp.raise_for_status()
                return resp.json()
        if isinstance(last_exc, httpx.TimeoutException):
            raise AxleTimeout(f"AXLE call to {path} timed out")
        raise AxleError(f"AXLE call to {path} failed: {last_exc}")

    @staticmethod
    def _parse(data: dict) -> CheckResult:
        okay = bool(data.get("okay", False))
        messages = data.get("lean_messages") or {}
        errors = list(messages.get("errors") or [])
        return CheckResult(okay=okay, errors=errors, raw=data)

    @staticmethod
    def _cache_key(op: str, environment: str, code: str) -> str:
        digest = hashlib.sha256(f"{op}\0{environment}\0{code}".encode()).hexdigest()
        return digest
