"""Derived metrics + production milestones from the salient timeline.

Back-compat: `compute_metrics` keeps every key the existing coach reads, and ADDITIVELY
exposes the production milestones the reconstruction core needs (first-of-each-unit,
first siege, first treb, first military building). Opening classification stays in #3.
"""

from collections import defaultdict

from . import const
from .timeline import AGE_RESEARCH_MS


def first_military_building(builds):
    """First BUILD whose name is in const.MILITARY_BUILDINGS → {"name", "t_s"} or None.

    `builds` is timeline["builds"] (command order; each has t in ms). We scan by time so the
    earliest military-producing/aggression building wins regardless of command ordering.
    """
    candidates = [b for b in builds if b["name"] in const.MILITARY_BUILDINGS]
    if not candidates:
        return None
    first = min(candidates, key=lambda b: b["t"])
    return {"name": first["name"], "t_s": first["t"] // 1000}


def production_milestones(timeline):
    """Exact production milestones from the unit queue stream.

    Returns:
      first_unit_s            {unit_name: t_s} — first DE_QUEUE time per distinct unit
      first_military_unit_s   t_s of the first NON-villager unit (None if none)
      first_siege_s           t_s of the first siege unit (None if none)
      first_treb_s            t_s of the first trebuchet (None if none)
      first_military_building {"name", "t_s"} or None
    """
    first_unit_ms: dict[str, int] = {}
    first_military_ms = None
    first_siege_ms = None
    first_treb_ms = None

    for u in timeline["units"]:
        name, uid, t = u["name"], u["unit_id"], u["t"]
        if name not in first_unit_ms:
            first_unit_ms[name] = t
        if uid != const.VILLAGER_ID and (first_military_ms is None or t < first_military_ms):
            first_military_ms = t
        if uid in const.SIEGE_UNIT_IDS and (first_siege_ms is None or t < first_siege_ms):
            first_siege_ms = t
        if uid in const.TREBUCHET_UNIT_IDS and (first_treb_ms is None or t < first_treb_ms):
            first_treb_ms = t

    def _s(ms):
        return ms // 1000 if ms is not None else None

    return {
        "first_unit_s": {n: t // 1000 for n, t in first_unit_ms.items()},
        "first_military_unit_s": _s(first_military_ms),
        "first_siege_s": _s(first_siege_ms),
        "first_treb_s": _s(first_treb_ms),
        "first_military_building": first_military_building(timeline["builds"]),
    }


def compute_metrics(timeline, duration_ms):
    up = timeline["uptimes"]
    minutes = max(duration_ms / 60000, 1 / 60)
    apm = round(timeline["action_count"] / minutes)

    army = defaultdict(int)
    villagers = 0
    for u in timeline["units"]:
        if u["name"] == "Villager":
            villagers += u["amount"]
        else:
            army[u["name"]] += u["amount"]

    def _arrival_s(age):
        click_ms = up[age]
        if click_ms is None:
            return None
        return (click_ms + AGE_RESEARCH_MS[age]) // 1000

    out = {
        "feudal_uptime_s": _arrival_s("feudal"),
        "castle_uptime_s": _arrival_s("castle"),
        "imperial_uptime_s": _arrival_s("imperial"),
        "apm": apm,
        "villager_count": villagers,
        "army": [{"name": n, "amount": a} for n, a in sorted(army.items(), key=lambda x: -x[1])],
        "eco_tech_timings": [{"name": e["name"], "t_s": e["t"] // 1000} for e in timeline["eco_techs"]],
        "estimates": [],
    }
    out.update(production_milestones(timeline))
    return out
