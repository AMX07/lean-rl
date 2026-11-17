"""Factory for selecting a verification backend.

``axle`` (default) is the hosted AXLE API — zero local setup, but capped at 20
concurrent requests. ``local`` runs Lean 4 + Mathlib via a local REPL pool with no rate
limit; use it for training. The backend is chosen by ``load_environment`` and never
exposed to the model.
"""

from __future__ import annotations

import os

from .checker import Checker


def load_checker(backend: str | None = None, **cfg) -> Checker:
    """Construct a ``Checker`` for ``backend`` (``"axle"`` or ``"local"``).

    ``backend`` falls back to ``LEAN_CHECKER_BACKEND`` then ``"axle"``. Unknown config
    keys are ignored so the same flat kwargs can be passed for either backend.
    """
    backend = (backend or os.environ.get("LEAN_CHECKER_BACKEND") or "axle").lower()

    if backend == "axle":
        from .axiom_client import AxleClient

        return AxleClient(
            url=cfg.get("axle_url"),
            timeout_s=cfg.get("axle_timeout_s"),
            max_concurrency=cfg.get("axle_max_concurrency"),
        )

    if backend == "local":
        from .local_lean import DEFAULT_TIMEOUT_S, LocalLeanChecker

        return LocalLeanChecker(
            repl_cmd=cfg.get("lean_repl_cmd"),
            project_dir=cfg.get("lean_project_dir"),
            pool_size=cfg.get("lean_pool_size"),
            timeout_s=cfg.get("lean_timeout_s", DEFAULT_TIMEOUT_S),
            lean_path=cfg.get("lean_path"),
        )

    raise ValueError(f"unknown checker backend {backend!r}; expected 'axle' or 'local'")
