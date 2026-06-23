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


# AoE2 hard population cap: once the player reaches it (and stops queuing villagers), a quiet TC is
# intentional, not idle. We only count idle BEFORE the player's estimated pop reaches this.
POP_CAP = const.POP_CAP


def precap_cutoff_s(sim, produced_units, duration_s, pop_cap=POP_CAP):
    """First time `t` (seconds) where the player's ESTIMATED population reaches `pop_cap`.

    Pop is estimated as sim.villagers_present(t) + cumulative NON-villager units produced by t. This
    deliberately OVER-estimates pop (army `*_produced` is a cumulative-queued upper bound, deaths
    unlogged), which is the safe direction for a CUTOFF: it lands the pre-cap window EARLY, so it
    trims clearly-intentional late-game TC quiet and never inflates the idle headline.

    If the cap is never reached, fall back to the last villager-queue time (production really did
    keep wanting villagers to the end) — or the whole game if that's later. Returns an int seconds.
    `sim` is a production.VillagerSim; `produced_units` is recon.production.produced_units (list of
    {unit_id, amount, t_s}); `duration_s` is the game length. Pure; never raises on missing fields.
    """
    duration_s = int(duration_s or 0)
    # Cumulative non-villager produced amount by time t, from a sorted (t_s, amount) prefix sum.
    nonvil = sorted(
        (int(u.get("t_s", 0) or 0), int(u.get("amount", 0) or 0))
        for u in (produced_units or [])
        if isinstance(u, dict) and u.get("unit_id") != const.VILLAGER_ID
    )
    times = [t for t, _ in nonvil]
    cum, running = [], 0
    for _, amt in nonvil:
        running += amt
        cum.append(running)

    def _nonvil_by(t):
        import bisect

        i = bisect.bisect_right(times, t)
        return cum[i - 1] if i > 0 else 0

    step = 5
    t = 0
    while t <= duration_s:
        est_pop = sim.villagers_present(t) + _nonvil_by(t)
        if est_pop >= pop_cap:
            return t
        t += step
    # Cap never reached: the player kept wanting villagers — use the last villager queue if known,
    # else the whole game. The last pop time approximates the last villager queue intent.
    last_vil = int(sim.pop_times_s[-1]) if sim.pop_times_s else 0
    return max(last_vil, duration_s) if last_vil else duration_s


def tc_idle(ops, player, threshold_s=IDLE_GAP_THRESHOLD_S, precap_s=None, age_windows=None):
    """Villager-production idle from gaps between consecutive villager DE_QUEUE commands.

    Returns:
      {"tc_idle_s": total idle seconds (sum of over-threshold gaps WITHIN the pre-cap window),
       "precap_window_s": the pre-cap cutoff in seconds (idle% = tc_idle_s / precap_window_s),
       "longest_villager_gap_s": longest single gap over the WHOLE game (0 if <2 villagers),
       "longest_villager_gap_window_s": [start_s, end_s] of that gap (None if <2 villagers),
       "idle_gap_windows_s": [[start_s, end_s], ...] for every gap OVER threshold (whole game),
       "villager_gaps_s": [each gap in seconds, in order]}

    A "gap" is the time between two consecutive villager queues; only the portion of gaps EXCEEDING
    a normal train time is idle, so we count gap-minus-threshold summed over gaps > threshold. This
    is an exact, command-derived idle signal (matches CaptureAge's IDL-TC intent), not an estimate.

    PRE-CAP ONLY (Nam): TC idle is only a MISTAKE while you still want villagers. After ~200 pop you
    stop making them, so a quiet TC then is intentional, not idle. `precap_s` is the first time the
    player's estimated pop reaches the cap (from `precap_cutoff_s`); when given, `tc_idle_s` sums only
    the over-threshold idle that occurred BEFORE it. A gap straddling the cutoff is clipped at it.
    Longest-gap / windows are reported over the whole game as before (context, not the headline).

    AGE-UP IS NOT IDLE (Nam): an age advance sits in the TC's OWN production queue, so the TC
    physically cannot make villagers while it loads (Feudal ~130s / Castle ~160s / Imperial ~190s).
    `age_windows` is a list of [start_s, end_s] research spans; their overlap with each villager gap
    is subtracted before the over-threshold idle is measured, so Castle-click loading is not billed
    as idle.
    """
    times = sorted(
        t
        for t, a, d in ops
        if a == Action.DE_QUEUE and d.get("player_id") == player and d.get("unit_id") == const.VILLAGER_ID
    )
    # gaps[k] is the gap between villager queue k and k+1 (i.e. between times[k] and times[k+1]).
    gaps = [(times[i] - times[i - 1]) // 1000 for i in range(1, len(times))]
    if precap_s is None:
        precap_s = times[-1] // 1000 if times else 0
    age_windows = age_windows or []

    # Longest gap + idle windows are measured ONLY within the pre-cap window (Nam): a gap after the
    # player stopped making villagers (~200 pop) is intentional, not a mistake. A gap counts if it
    # STARTS before the cap, and only its portion up to the cutoff is taken.
    longest = 0
    longest_window = None
    idle_windows = []
    for k, _g in enumerate(gaps):
        start_s = times[k] // 1000
        if start_s >= precap_s:
            continue  # gap starts after the player stopped wanting villagers — not idle
        end_s = min(times[k + 1] // 1000, precap_s)
        capped = end_s - start_s
        if capped > longest:
            longest = capped
            longest_window = [start_s, end_s]
        if capped > threshold_s:
            idle_windows.append([start_s, end_s])
    idle = 0
    for k in range(1, len(times)):
        start_s = times[k - 1] // 1000
        end_s = times[k] // 1000
        clipped_end = min(end_s, precap_s)
        if clipped_end <= start_s:
            continue  # gap starts at/after the cap -> intentional quiet, not idle
        # An age advance occupies the TC, so subtract its overlap with this gap — that's advancing,
        # not idling (fixes the Castle-click-loading-counted-as-idle bug).
        busy = 0
        for a_start, a_end in age_windows:
            overlap = min(clipped_end, a_end) - max(start_s, a_start)
            if overlap > 0:
                busy += overlap
        idle += max(0, (clipped_end - start_s) - busy - threshold_s)

    return {
        "tc_idle_s": idle,
        "precap_window_s": int(precap_s),
        "longest_villager_gap_s": longest,
        "longest_villager_gap_window_s": longest_window,
        "idle_gap_windows_s": idle_windows,
        "villager_gaps_s": gaps,
    }


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
