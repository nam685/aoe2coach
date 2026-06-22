"""Efficiency metrics: TC idle (villager-queue gaps), real APM, and eco/military APM split.

The old `compute_metrics` APM was a build/research/queue tally, not an action rate. Here APM is a
true per-minute rate over ALL of a player's actions, and `apm_split` classifies each action as
eco / military / uncategorized per the spec's rules. Pure functions over ops; never raises.

Counts derived here are command counts (exact); any *unit* counts the assembler exposes are
labeled `produced` elsewhere — efficiency emits rates, not unit counts.
"""

from mgz.fast import Action

from . import const

# Villager training gap longer than this is counted as TC idle time (s). A healthy TC re-queues a
# villager every ~25s; gaps beyond this threshold are real idle, not just train time. Tunable.
IDLE_GAP_THRESHOLD_S = 30

# Action sets for the eco/military APM split (spec §efficiency.py).
_ECO_BUILDING_NAMES = None  # computed lazily below


def _eco_building_names():
    """Building names considered eco (everything in BUILDING_NAMES not in MILITARY_BUILDINGS)."""
    global _ECO_BUILDING_NAMES
    if _ECO_BUILDING_NAMES is None:
        _ECO_BUILDING_NAMES = {n for n in const.BUILDING_NAMES.values() if n not in const.MILITARY_BUILDINGS}
    return _ECO_BUILDING_NAMES


# Army-control actions → military.
_MILITARY_CONTROL_ACTIONS = {
    Action.MOVE,
    Action.ATTACK_GROUND,
    Action.STANCE,
    Action.PATROL,
    Action.DE_ATTACK_MOVE,
    Action.DELETE,
    Action.STOP,
    Action.GUARD,
    Action.FOLLOW,
    Action.FORMATION,
}


def tc_idle(ops, player, threshold_s=IDLE_GAP_THRESHOLD_S):
    """Villager-production idle from gaps between consecutive villager DE_QUEUE commands.

    Returns:
      {"tc_idle_s": total idle seconds (sum of gaps over threshold),
       "longest_villager_gap_s": longest single gap (0 if <2 villagers),
       "villager_gaps_s": [each gap in seconds, in order]}

    A "gap" is the time between two consecutive villager queues; only the portion of gaps EXCEEDING
    a normal train time is idle, so we count gap-minus-threshold summed over gaps > threshold. This
    is an exact, command-derived idle signal (matches CaptureAge's IDL-TC intent), not an estimate.
    """
    times = sorted(
        t
        for t, a, d in ops
        if a == Action.DE_QUEUE and d.get("player_id") == player and d.get("unit_id") == const.VILLAGER_ID
    )
    gaps = [(times[i] - times[i - 1]) // 1000 for i in range(1, len(times))]
    idle = sum(max(0, g - threshold_s) for g in gaps)
    longest = max(gaps) if gaps else 0
    return {"tc_idle_s": idle, "longest_villager_gap_s": longest, "villager_gaps_s": gaps}


def _classify(action_type, data):
    """Return "eco" | "military" | "other" for one of `player`'s actions."""
    if action_type == Action.DE_QUEUE:
        return "eco" if data.get("unit_id") == const.VILLAGER_ID else "military"
    if action_type == Action.BUILD:
        name = const.building_name(data.get("building_id"))
        if name in const.MILITARY_BUILDINGS:
            return "military"
        if name in _eco_building_names():
            return "eco"
        return "other"
    if action_type == Action.RESEARCH:
        tech = data.get("technology_id")
        if tech in const.ECO_TECHS:
            return "eco"
        if tech in const.MILITARY_TECHS or tech in const.UNIVERSITY_TECHS:
            return "military"
        return "other"  # age techs + unknown → uncategorized
    if action_type in (Action.GATHER_POINT, Action.DE_MULTI_GATHERPOINT, Action.BUY, Action.SELL, Action.BACK_TO_WORK):
        return "eco"
    if action_type in _MILITARY_CONTROL_ACTIONS:
        return "military"
    return "other"


def apm_split(ops, player, duration_ms):
    """Real per-minute action rates for `player`, split eco vs military.

    Returns {"apm_total", "apm_eco", "apm_military", "apm_other",
             "actions_total", "actions_eco", "actions_military", "actions_other"}.
    APM = round(count / minutes). Every action is counted in apm_total; eco/military/other are a
    partition of it. Pure; uncategorized actions count toward total but not eco/military.
    """
    minutes = max(duration_ms / 60000, 1 / 60)
    counts = {"eco": 0, "military": 0, "other": 0}
    for _t, action_type, data in ops:
        if data.get("player_id") != player:
            continue
        counts[_classify(action_type, data)] += 1
    total = counts["eco"] + counts["military"] + counts["other"]
    return {
        "apm_total": round(total / minutes),
        "apm_eco": round(counts["eco"] / minutes),
        "apm_military": round(counts["military"] / minutes),
        "apm_other": round(counts["other"] / minutes),
        "actions_total": total,
        "actions_eco": counts["eco"],
        "actions_military": counts["military"],
        "actions_other": counts["other"],
    }


def apm(ops, player, duration_ms):
    """Convenience: the single total APM number (true action rate over all of player's actions)."""
    return apm_split(ops, player, duration_ms)["apm_total"]
