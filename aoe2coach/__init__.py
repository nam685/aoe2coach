"""Standalone AoE2 DE 1v1 coach core. Shared by nam-website prod + elluminate eval."""

from . import combat, const, efficiency, mistakes, population, spatial
from .coach import BENCHMARKS, CoachOutput, build_coach_prompt, coach, parse_opening, run_claude_coach
from .entrypoint import analyze_replay
from .metrics import compute_metrics, first_military_building, production_milestones
from .mistakes import detect_mistakes
from .parser import ParsedRec, parse_rec
from .reconstruct import Reconstruction, reconstruct
from .timeline import build_timeline, render_dual_log, render_salient_log

__all__ = [
    "const",
    "combat",
    "efficiency",
    "mistakes",
    "detect_mistakes",
    "population",
    "spatial",
    "BENCHMARKS",
    "CoachOutput",
    "build_coach_prompt",
    "coach",
    "parse_opening",
    "run_claude_coach",
    "analyze_replay",
    "compute_metrics",
    "first_military_building",
    "production_milestones",
    "ParsedRec",
    "parse_rec",
    "Reconstruction",
    "reconstruct",
    "build_timeline",
    "render_dual_log",
    "render_salient_log",
]
