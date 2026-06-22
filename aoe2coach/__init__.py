"""Standalone AoE2 DE 1v1 coach core. Shared by nam-website prod + elluminate eval."""

from . import const
from .coach import BENCHMARKS, CoachOutput, build_coach_prompt, coach, parse_opening, run_claude_coach
from .entrypoint import analyze_replay
from .metrics import compute_metrics
from .parser import ParsedRec, parse_rec
from .timeline import build_timeline, render_dual_log, render_salient_log

__all__ = [
    "const",
    "BENCHMARKS",
    "CoachOutput",
    "build_coach_prompt",
    "coach",
    "parse_opening",
    "run_claude_coach",
    "analyze_replay",
    "compute_metrics",
    "ParsedRec",
    "parse_rec",
    "build_timeline",
    "render_dual_log",
    "render_salient_log",
]
