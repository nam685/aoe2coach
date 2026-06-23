"""Per-match agent workspace builder (sub-project #4, Coach v2).

`build_workspace(...)` materializes a throwaway temp dir laid out so the agentic coach's read-only
tools (Read/Grep/Glob) have *everything local and nothing else*. It is populated from the full
preprocessing bundle (#1 facts, #2 economy, #3 candidates + reference library, #6 flagged mistakes,
#7 strategic-map PNGs). The coach reads facts.json for numbers, the salient.log for sequence, the
strategic_map.png(s) multimodally, and progressively discloses the candidate's reference file.

Honesty is carried in the data the producers emit (exact/estimate/produced labels, self-suppressed
econ) — this module copies it faithfully and never invents numbers. Nothing here calls an LLM or the
network; the LLM step lives in coach.py (`run_agentic_coach`).
"""

from __future__ import annotations

import dataclasses
import json
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

# Default reference library: #3's build-order YAMLs. The agent reads ONE on demand.
_DEFAULT_REFERENCE_ROOT = Path(__file__).parent / "buildorders" / "data"


def _as_dict(obj):
    """Accept a Reconstruction/ClassificationResult/Candidate/Flagged object OR its dict form."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    return obj


def _candidate_dicts(candidates) -> list[dict]:
    """Normalize `candidates` (a #3 ClassificationResult, a list of Candidate, or a list of dicts)
    into a list of plain dicts with at least build_id/name/confidence."""
    if candidates is None:
        return []
    # A ClassificationResult exposes `.candidates`.
    if hasattr(candidates, "candidates"):
        candidates = candidates.candidates
    out = []
    for c in candidates:
        d = _as_dict(c)
        out.append(d)
    return out


def _reference_filename(build_id: str) -> str:
    """The reference file copied into references/ for a build_id (the #3 YAML slug)."""
    return f"{build_id}.yaml"


def _render_candidates_md(candidate_dicts: list[dict], notes: list[str] | None) -> str:
    """The candidates.md content: the 1-3 pre-narrowed builds, each -> its reference path."""
    lines = ["# Candidate build orders (pre-narrowed by the deterministic classifier #3)", ""]
    if not candidate_dicts:
        lines.append("_No candidate builds were produced — classify the opening from the facts yourself,")
        lines.append("then Grep references/ for the build that fits and read it._")
    for i, c in enumerate(candidate_dicts, start=1):
        bid = c.get("build_id") or c.get("slug") or "?"
        name = c.get("name") or c.get("display_name") or bid
        conf = c.get("confidence")
        conf_s = f"{conf:.2f}" if isinstance(conf, (int, float)) else str(conf)
        ref = f"references/{_reference_filename(bid)}"
        lines.append(f"## {i}. {name}  (confidence {conf_s})")
        lines.append(f"- reference: `{ref}`  — Read this file to get the verified targets.")
        matched = c.get("matched_signals") or []
        missed = c.get("missed_signals") or []
        if matched:
            lines.append(f"- matched signals: {'; '.join(matched)}")
        if missed:
            lines.append(f"- missed signals: {'; '.join(missed)}")
        lines.append("")
    if notes:
        lines.append("## Classifier notes")
        for n in notes:
            lines.append(f"- {n}")
        lines.append("")
    lines.append(
        "If none of the above fits what facts.json shows, Grep references/ and Read the build that does — "
        "and say the classifier's candidates were off."
    )
    return "\n".join(lines) + "\n"


# The thin user-turn instructions. The system prompt (COACH_SYSTEM_V2) carries the contract;
# TASK.md just points at the files and triggers the run.
_TASK_TEMPLATE = """\
Coach this Age of Empires II 1v1 match.

Your inputs are all in your current working directory:
  facts.json       — STRUCTURED MATCH FACTS (#1 Reconstruction). All numbers come from here.
  salient.log      — mechanical event log; use ONLY for sequence/context, never for numbers.
  candidates.md    — 1-3 pre-narrowed build orders, each with a reference file path.
  references/      — the build-order reference library (read ONE on demand to get targets).
  mistakes.json    — deterministically flagged mistakes (#6); narrate the flagged ones, hedge by tier.
  economy.json     — ESTIMATED economy (#2): per-age + per-villager-count worker split (farm-anchored
                     food) and cumulative spend-over-time series. Qualitative trends only, never exact.
  strategic_map.png{extra_maps} — the strategic map(s) (#7). Read these images for spatial context
                     (base layout, forward buildings, walls, where engagements happened).
  map_legend.md    — what the colors/markers on the map mean.

Follow the process in your instructions and produce exactly two sections: WHAT HAPPENED and ANALYSIS.
"""

_MAP_LEGEND = """\
# Strategic map legend (#7 — operational macro only, never unit micro)

- ME is drawn in BLUE, OPP in RED (fixed, regardless of in-game color).
- Large bright dot = a player's base (Town Center / centroid).
- Small dim dots = buildings.
- Violet dots (outlined) = forward / proxy buildings (built toward the opponent).
- Amber dots = engagement markers — sized by aggressive-command volume, labeled by zone
  (own_base | center | opp_base). These come from attack-intent COMMANDS, NOT from observed
  casualties (replays log no combat/deaths). Treat them as "where pressure was applied", not kills.
- Thin lines on a side's color = that player's walls.
- strategic_map.png is the whole-game overview; eng01/eng02/... are snapshots at each engagement.
"""


def build_candidates_md(candidates, notes=None) -> str:
    """Public helper (also used by the single-shot fallback): render candidates.md text."""
    return _render_candidates_md(_candidate_dicts(candidates), notes)


@contextmanager
def build_workspace(
    reconstruction,
    candidates=None,
    *,
    reference_root: str | Path | None = None,
    salient_log: str = "",
    economy=None,
    mistakes=None,
    map_pngs=None,
    classifier_notes=None,
    debug: bool = False,
):
    """Materialize a per-match agent workspace and yield its Path. Cleaned up on exit (unless debug).

    Args:
      reconstruction: #1 Reconstruction object or dict — the authoritative facts. (required)
      candidates:     #3 ClassificationResult, list[Candidate], or list[dict] — pre-narrowed builds.
      reference_root: dir of the build-order reference library to COPY into references/.
                      Defaults to #3's bundled buildorders/data.
      salient_log:    the dual mechanical log (sequence/context).
      economy:        #2 estimate_economy(...) dict (ESTIMATE-labeled). Optional.
      mistakes:       #6 list[Flagged] / list[dict] of flagged mistakes. Optional.
      map_pngs:       list of #7 PNG paths (render_maps output: overall first, then engagements).
      classifier_notes: optional list[str] of classifier notes for candidates.md.
      debug:          leave the workspace on disk (do not delete) for inspection.

    The dir contains NO CLAUDE.md (it would leak into the system prompt). references/ is COPIED (not
    symlinked) so the agent's filesystem access stays inside the workspace.
    """
    recon = _as_dict(reconstruction)
    if recon is None:
        raise ValueError("build_workspace requires a reconstruction")

    root = Path(tempfile.mkdtemp(prefix="aoe2coach-"))
    try:
        # facts.json — authoritative numbers (round-trips the reconstruction).
        (root / "facts.json").write_text(json.dumps(recon, indent=2), encoding="utf-8")

        # salient.log — sequence/context.
        (root / "salient.log").write_text(salient_log or "", encoding="utf-8")

        # references/ — COPY the build-order library (read-only retrieval target).
        ref_src = Path(reference_root) if reference_root is not None else _DEFAULT_REFERENCE_ROOT
        ref_dst = root / "references"
        ref_dst.mkdir()
        if ref_src.is_dir():
            for p in sorted(ref_src.glob("*.yaml")):
                shutil.copy2(p, ref_dst / p.name)

        # candidates.md — the 1-3 pre-narrowed builds -> reference paths.
        cand_dicts = _candidate_dicts(candidates)
        notes = classifier_notes
        if notes is None and hasattr(candidates, "notes"):
            notes = candidates.notes
        (root / "candidates.md").write_text(_render_candidates_md(cand_dicts, notes), encoding="utf-8")

        # mistakes.json — #6 flagged mistakes (honest [] = no detectable mistakes).
        mistakes_list = [_as_dict(m) for m in (mistakes or [])]
        (root / "mistakes.json").write_text(json.dumps(mistakes_list, indent=2), encoding="utf-8")

        # economy.json — #2 ESTIMATE block (may self-suppress collected totals).
        econ_dict = _as_dict(economy)
        (root / "economy.json").write_text(
            json.dumps(econ_dict if econ_dict is not None else {"estimate": True, "unavailable": True}, indent=2),
            encoding="utf-8",
        )

        # strategic_map.png (overall) + engagement snapshots; map_legend.md.
        extra_maps = ""
        pngs = list(map_pngs or [])
        if pngs:
            shutil.copy2(pngs[0], root / "strategic_map.png")
            eng_names = []
            for i, p in enumerate(pngs[1:], start=1):
                name = f"engagement_{i:02d}.png"
                shutil.copy2(p, root / name)
                eng_names.append(name)
            if eng_names:
                extra_maps = " + " + ", ".join(eng_names)
        (root / "map_legend.md").write_text(_MAP_LEGEND, encoding="utf-8")

        # TASK.md — the thin user-turn pointer.
        (root / "TASK.md").write_text(_TASK_TEMPLATE.format(extra_maps=extra_maps), encoding="utf-8")

        yield root
    finally:
        if not debug:
            shutil.rmtree(root, ignore_errors=True)
