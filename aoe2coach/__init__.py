"""Standalone AoE2 DE 1v1 coach core. Shared by nam-website prod + elluminate eval."""

from . import buildorders, combat, const, econ, efficiency, gaia, mistakes, population, rates, spatial
from .classify import Candidate, ClassificationResult, classify
from .coach import BENCHMARKS, CoachOutput, build_coach_prompt, coach, parse_opening, run_claude_coach
from .econ import (
    assignment_events,
    collected_estimate,
    eco_split_at_ages,
    eco_split_steps,
    estimate_economy,
)
from .entrypoint import analyze_replay
from .metrics import compute_metrics, first_military_building, production_milestones
from .mistakes import detect_mistakes
from .parser import ParsedRec, parse_rec
from .reconstruct import Reconstruction, reconstruct
from .timeline import build_timeline, render_dual_log, render_salient_log

__all__ = [
    "const",
    "buildorders",
    "Candidate",
    "ClassificationResult",
    "classify",
    "combat",
    "econ",
    "efficiency",
    "gaia",
    "mistakes",
    "detect_mistakes",
    "population",
    "rates",
    "spatial",
    "assignment_events",
    "collected_estimate",
    "eco_split_at_ages",
    "eco_split_steps",
    "estimate_economy",
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
