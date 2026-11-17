"""Lean + AXLE proof-verification environment for Prime Intellect / verifiers.

Public surface:
    - ``load_environment`` : verifiers entrypoint (single-turn proof completion).
    - ``Checker`` / ``CheckResult`` : the verification-backend seam.
    - ``AxleClient``       : hosted AXLE backend (20-concurrent cap).
    - ``LocalLeanChecker`` : local Lean 4 + Mathlib REPL backend (scales with CPU).
    - ``load_checker``     : backend factory.
    - ``make_rubric``      : binary reward + monitor metrics.
"""

from .agentic import LeanToolEnv
from .axiom_client import AxleClient
from .checker import Checker, CheckResult
from .checkers import load_checker
from .env import load_environment
from .datasets.generate import tasks_from_lean_source
from .local_lean import LocalLeanChecker
from .multiturn import LeanRepairEnv
from .report import render_markdown, render_search_markdown, summarize, summarize_search
from .reward import make_repair_rubric, make_rubric, make_step_rubric, make_tool_rubric
from .rlm import make_rlm_env
from .search import SearchResult, run_search
from .step import StepLeanEnv

__all__ = [
    "load_environment",
    "Checker",
    "CheckResult",
    "AxleClient",
    "LocalLeanChecker",
    "LeanRepairEnv",
    "LeanToolEnv",
    "StepLeanEnv",
    "load_checker",
    "make_rubric",
    "make_repair_rubric",
    "make_tool_rubric",
    "make_step_rubric",
    "make_rlm_env",
    "tasks_from_lean_source",
    "summarize",
    "render_markdown",
    "summarize_search",
    "render_search_markdown",
    "SearchResult",
    "run_search",
]
__version__ = "0.10.0"
