"""Eval-facing entrypoint: parse a replay → produce the eval data-contract dict. No DB."""

import json

from .classify import classify
from .coach import BENCHMARKS, coach
from .econ import estimate_economy
from .metrics import compute_metrics
from .mistakes import detect_mistakes
from .parser import parse_rec
from .reconstruct import reconstruct
from .timeline import build_timeline, render_dual_log


def analyze_replay(
    path: str,
    owner_profile_id: int,
    *,
    result: str | None = None,
    model: str = "opus",
    civ: str = "",
    elo_band: str = "",
    match_id: str | None = None,
    use_v2: bool = True,
    map_pngs=None,
    claude_bin: str = "claude",
) -> dict:
    """Parse a .aoe2record and return BOTH the coach inputs and output (one dict per replay).

    All values are stringified for elluminate TEXT columns. Raises on parse/coach failure
    (the eval wants real exceptions). `result` defaults to the parser's RESIGN heuristic.

    When `use_v2` (default), runs the agentic Coach v2 over the full preprocessing bundle
    (#1 reconstruction, #3 candidates, #6 mistakes, #2 economy, #7 map PNGs if provided) and adds a
    `facts_json` column. The coach degrades gracefully to single-shot facts-only on any failure.
    The legacy columns (coach_output, opening, metrics_json, salient_log) are always preserved.
    """
    rec = parse_rec(path, owner_profile_id)
    timeline = build_timeline(rec.ops, rec.me["number"])
    metrics = compute_metrics(timeline, rec.duration_ms)
    salient_log = render_dual_log(rec.ops, rec.me["number"], rec.opponent["number"], timeline["action_count"])
    game_result = result if result is not None else rec.my_result

    base = {
        "match_id": str(match_id if match_id is not None else rec.me.get("profile_id", "")),
        "metrics_json": json.dumps(metrics),
        "salient_log": salient_log,
        "game_result": game_result,
        "civ": civ or rec.me.get("civ_name", ""),
        "elo_band": elo_band,
    }

    if use_v2:
        recon = reconstruct(rec)
        cands = classify(recon)
        flagged = detect_mistakes(recon)
        econ = estimate_economy(rec.ops, player=rec.me["number"], gaia_list=rec.gaia_objects, recon=recon)
        out = coach(
            metrics,
            salient_log,
            benchmarks=BENCHMARKS,
            result=game_result,
            model=model,
            claude_bin=claude_bin,
            reconstruction=recon,
            candidates=cands,
            economy=econ,
            mistakes=flagged,
            map_pngs=map_pngs,
        )
        base["facts_json"] = json.dumps(recon.to_dict())
        base["coach_tier"] = out.tier
    else:
        out = coach(metrics, salient_log, benchmarks=BENCHMARKS, result=game_result, model=model)

    base["coach_output"] = out.raw_text
    base["opening"] = out.opening_tag
    return base
