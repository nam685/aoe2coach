"""Eval-facing entrypoint: parse a replay → produce the eval data-contract dict. No DB."""

import json

from .coach import BENCHMARKS, coach
from .metrics import compute_metrics
from .parser import parse_rec
from .timeline import build_timeline, render_dual_log


def analyze_replay(
    path: str,
    owner_profile_id: int,
    *,
    result: str | None = None,
    model: str = "sonnet",
    civ: str = "",
    elo_band: str = "",
    match_id: str | None = None,
) -> dict:
    """Parse a .aoe2record and return BOTH the coach inputs and output (one dict per replay).

    All values are stringified for elluminate TEXT columns. Raises on parse/coach failure
    (the eval wants real exceptions). `result` defaults to the parser's RESIGN heuristic.
    """
    rec = parse_rec(path, owner_profile_id)
    timeline = build_timeline(rec.ops, rec.me["number"])
    metrics = compute_metrics(timeline, rec.duration_ms)
    salient_log = render_dual_log(rec.ops, rec.me["number"], rec.opponent["number"], timeline["action_count"])
    game_result = result if result is not None else rec.my_result
    out = coach(metrics, salient_log, benchmarks=BENCHMARKS, result=game_result, model=model)
    return {
        "match_id": str(match_id if match_id is not None else rec.me.get("profile_id", "")),
        "metrics_json": json.dumps(metrics),
        "salient_log": salient_log,
        "game_result": game_result,
        "coach_output": out.raw_text,
        "opening": out.opening_tag,
        "civ": civ or rec.me.get("civ_name", ""),
        "elo_band": elo_band,
    }
