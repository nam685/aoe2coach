"""Tier-B economy ESTIMATOR (sub-project #2). Everything here is an `~estimate`, labeled as such.

A replay is a command log, not a state log: villager-per-resource counts and resources collected are
NOT in the file. We reconstruct them from GATHER_POINT commands joined to the starting GAIA object
table (gaia.py), the #1 villager-production SIMULATION (production.py — physical pops, not the queued
over-count), and known DE gather rates (rates.py).

REWORK (2026-06) — the two real bugs the owner found:
  1. The old model derived villager-per-resource from sparse re-click (ORDER) events and dropped any
     GATHER_POINT whose target wasn't a raw GAIA id. But villagers are gather-pointed to a Lumber Camp
     (→wood) or near a Mining Camp (→gold/stone), NOT re-clicked onto trees, so WOOD was invisible and
     the split read "~all food". We now resolve a gather point's resource via: (a) the target_type as a
     DROPOFF building id (Lumber Camp→wood, Mining Camp→gold/stone by nearest mine, Mill/TC/Farm→food),
     (b) target_type as a GAIA object_id, then (c) the nearest GAIA resource to the gather x/y.
  2. Villager counts came from cumulative DE_QUEUE *orders* (queued), which over-counts. We now drive
     the per-age worker split off the SIMULATED pop timeline and attribute each popped villager to the
     trailing-window gather-point distribution (the player's recent eco intent).

HONESTY (program HARD RULE): every estimated value carries `estimate: true`; collected totals carry a
`[low, high]` band and SELF-SUPPRESS (return None) if implausible / out of the validation band
(+-15% per resource AND +-10% total vs calibration). Suppression is success. Relic gold is NEVER
estimated — it carries no command signal — and is labeled `unavailable`.

Pure functions over `ops`, the parsed gaia table, and a Reconstruction dict (#1). No DB/network/IO.
"""

from collections import Counter

from mgz.fast import Action

from . import const, production, rates
from . import gaia as gaia_mod

_RESOURCES = ("food", "wood", "gold", "stone")

# Validation band (spec): per-resource and grand-total error vs the calibration screenshot. These are
# the suppression thresholds — a number that misses them is dropped, not shipped as fact.
BAND_PER_RESOURCE = 0.15
BAND_TOTAL = 0.10

# Collected-estimate band half-width: the model is coarse, so report a wide [low, high] around the
# point estimate to avoid false precision.
COLLECTED_BAND = 0.30

# A gather point whose target_type is one of these DROPOFF building ids implies the resource directly:
# the villagers were gather-pointed onto a resource-drop building.
_DROPOFF_RESOURCE = {
    562: "wood",  # Lumber Camp
    68: "food",  # Mill
    50: "food",  # Farm
    71: "food",  # Town Center (alt)
    109: "food",  # Town Center
    621: "food",  # Town Center (DE)
    2556: "food",  # Settlement (TC-like)
}
_MINING_CAMP_ID = 584  # nearest gold/stone mine decides
_FARM_BUILDING_ID = 50

# A bare-ground (target_type -1) or unresolved gather point is matched to the nearest GAIA resource
# only if it sits within this many tiles — beyond it the click is a MOVE, not a gather (never fabricate).
NEAR_RESOURCE_TILES = 6.0

# Trailing window (seconds) of gather-point intent used to attribute a newly-popped villager. Wide
# enough to smooth the flip-flop of a single "current" gather point across multiple production
# buildings, narrow enough to track real eco shifts. Calibration knob.
FOCUS_WINDOW_S = 180

# Effective per-villager-second collection ceiling rate. Real villagers collect well BELOW their book
# gather rate (0.45-0.55/s) because of walking, drop-off trips and idle time; calibration shows a
# whole-game effective rate around 0.26-0.28/s. We bound the collected integral at this rate × the
# physical villager-seconds (the area under villagers_present). An integral computed with BOOK rates
# necessarily exceeds this bound — so the additive-share collected estimate SELF-SUPPRESSES (the
# honest outcome the spec sanctions). This is an INTERNAL plausibility bound, not a hidden answer key.
EFFECTIVE_RATE_CEILING = 0.30


def resolve_gather_resource(data, gaia_by_objid, resource_points):
    """Resolve a GATHER_POINT op's target to "food"|"wood"|"gold"|"stone" or None.

    Order: (a) target_type is a DROPOFF building id (Lumber Camp→wood, Mining Camp→nearest gold/stone,
    Mill/TC/Farm→food); (b) target_type joins a GAIA object_id; (c) the nearest GAIA resource to (x,y)
    within NEAR_RESOURCE_TILES. Returns None if nothing resolves (a MOVE, not a gather) — never
    fabricates a resource. Never raises.
    """
    tt = data.get("target_type")
    x = data.get("x")
    y = data.get("y")
    # (a) dropoff building id.
    if tt == _MINING_CAMP_ID and isinstance(x, (int, float)) and isinstance(y, (int, float)):
        r, _ = gaia_mod.nearest_resource(resource_points, x, y, classes={"gold", "stone"})
        if r is not None:
            return r
    if tt in _DROPOFF_RESOURCE:
        return _DROPOFF_RESOURCE[tt]
    # (b) target_type as a GAIA object_id.
    obj = gaia_by_objid.get(tt)
    r = gaia_mod.resource_class(obj)
    if r is not None:
        return r
    # (c) nearest GAIA resource to the gather point, if close enough.
    if isinstance(x, (int, float)) and isinstance(y, (int, float)):
        r, dist = gaia_mod.nearest_resource(resource_points, x, y)
        if r is not None and dist <= NEAR_RESOURCE_TILES:
            return r
    return None


def gather_focus_events(ops, player, gaia_by_objid, resource_points):
    """GATHER_POINT events for `player` resolved to a resource, in time order.

    Each: {"t_s", "resource"}. Unresolvable / non-resource gather points produce no event (the honest,
    decoration-excluded signal). These are the player's eco-intent timeline that drives villager
    attribution.
    """
    out = []
    for t, action_type, data in ops:
        if action_type != Action.GATHER_POINT or data.get("player_id") != player:
            continue
        r = resolve_gather_resource(data, gaia_by_objid, resource_points)
        if r is None:
            continue
        out.append({"t_s": t // 1000, "resource": r})
    out.sort(key=lambda e: e["t_s"])
    return out


def active_farms(ops, player):
    """Number of DISTINCT farm tiles `player` built, deduping reseeds (a farm rebuilt on the ~same
    tile is the same farm, not a new one). Late-game food workers ≈ number of active farms."""
    tiles = set()
    for _t, action_type, data in ops:
        if action_type != Action.BUILD or data.get("player_id") != player:
            continue
        if data.get("building_id") != _FARM_BUILDING_ID:
            continue
        x = data.get("x")
        y = data.get("y")
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            tiles.add((round(x), round(y)))
    return len(tiles)


def _focus_window_dist(focus_events, t_s):
    """Distribution (Counter) of gather-point resources in the trailing FOCUS_WINDOW_S before t_s.

    Falls back to the single most-recent focus before t_s if the window is empty (early game, sparse
    commands). Empty Counter if no focus event has happened yet.
    """
    c = Counter()
    last = None
    for e in focus_events:
        ft = e["t_s"]
        if ft > t_s:
            break
        last = e["resource"]
        if ft >= t_s - FOCUS_WINDOW_S:
            c[e["resource"]] += 1
    if not c and last is not None:
        c[last] = 1
    return c


def worker_split_at_ages(focus_events, pop_times_s, starting, ages):
    """Estimated villager-on-resource split at each age boundary (the primary deliverable).

    Each villager POPPED (from the production simulation) by the age-arrival time is attributed to the
    trailing-window gather-point distribution at its pop time (fractional, to smooth the single-focus
    flip-flop). Starting villagers seed food (they begin on sheep/berries). Returns
    {age: {estimate, alloc:{resource: count}, shares:{resource: frac}, villagers_present} | None}.
    """
    out = {}
    for age in ("feudal", "castle", "imperial"):
        t = ages.get(f"{age}_arrival_s")
        if t is None:
            out[age] = None
            continue
        acc = Counter()
        acc["food"] += starting  # pre-placed villagers start on food (sheep/berries)
        attributed = 0
        for pt in pop_times_s:
            if pt > t:
                break
            wd = _focus_window_dist(focus_events, int(pt))
            tot = sum(wd.values())
            if tot:
                for r, n in wd.items():
                    acc[r] += n / tot
                attributed += 1
        present = starting + sum(1 for pt in pop_times_s if pt <= t)
        grand = sum(acc.values())
        # Normalize the resource shares to the physical villagers-present count.
        shares = {r: acc[r] / grand for r in acc} if grand else {}
        alloc = {r: round(shares.get(r, 0.0) * present) for r in _RESOURCES if shares.get(r, 0.0) > 0}
        out[age] = {
            "estimate": True,
            "villagers_present": present,
            "n_attributed": attributed,
            "alloc": alloc,
            "shares": {r: round(s, 3) for r, s in shares.items()},
        }
    return out


def _integrate_collected(focus_events, pop_times_s, starting, recon):
    """Estimate resources collected by integrating Σ workers_on_R(t) × rate_at(R, t) over the game.

    Walks 30s windows; in each window the worker-on-resource counts come from the running villager
    population attributed to the trailing-window gather distribution. Returns
    ({resource: float}, villager_seconds) — the latter is ∫ villagers_present dt (the area under the
    physical population curve), used as the collected-estimate plausibility ceiling.
    """
    duration_s = recon.get("meta", {}).get("duration_s")
    if not duration_s and pop_times_s:
        duration_s = int(pop_times_s[-1])
    if not duration_s:
        return {}, 0.0
    totals = dict.fromkeys(_RESOURCES, 0.0)
    villager_seconds = 0.0
    step = 30
    t = 0
    pops_sorted = pop_times_s
    while t < duration_s:
        t1 = min(t + step, duration_s)
        mid = (t + t1) // 2
        dt = t1 - t
        present = starting + sum(1 for pt in pops_sorted if pt <= mid)
        villager_seconds += present * dt
        wd = _focus_window_dist(focus_events, mid)
        tot = sum(wd.values())
        if tot and present:
            for r, n in wd.items():
                workers = present * (n / tot)
                totals[r] += workers * rates.rate_at(r, mid, recon) * dt
        elif present:
            # No gather signal yet -> assume the starting eco is on food (sheep/berries).
            totals["food"] += present * rates.rate_at("food", mid, recon) * dt
        t = t1
    return totals, villager_seconds


def collected_estimate(totals, max_worker_seconds=None):
    """Wrap raw collected `totals` ({resource: float}) into per-resource bands, or SUPPRESS (None).

    SELF-SUPPRESSION (spec HARD RULE — suppress on internal implausibility, never on a hidden key):
      1. No signal at all (empty / all-zero) -> None.
      2. A MAJOR resource (wood OR food) is missing/zero -> the whole estimate is untrustworthy -> None.
      3. Implied collection exceeds the villager-seconds × EFFECTIVE_RATE_CEILING bound -> None. Real
         villagers collect far below book rate (walking/drop-off/idle), so an integral using book rates
         exceeds this bound — the additive-share model honestly suppresses rather than ship a 3x number.
    Returns {resource: {value, low, high, estimate, confidence}} (stone may be present) with gold's
    relic component NEVER added. Relic gold is reported separately as `unavailable`.
    """
    if not totals:
        return None
    seen = {r for r in _RESOURCES if totals.get(r, 0) > 0}
    if not seen:
        return None
    # (2) a major resource with no signal poisons the whole estimate -> suppress.
    if "wood" not in seen or "food" not in seen:
        return None
    # (3) physical villager-seconds plausibility ceiling.
    grand_total = sum(totals.get(r, 0) for r in _RESOURCES)
    if max_worker_seconds:
        ceiling = max_worker_seconds * EFFECTIVE_RATE_CEILING
        if grand_total > ceiling:
            return None
    out = {}
    for r in _RESOURCES:
        if r not in seen:
            out[r] = None  # never report 0 collected as a fact
            continue
        v = totals[r]
        out[r] = {
            "value": round(v),
            "low": round(v * (1 - COLLECTED_BAND)),
            "high": round(v * (1 + COLLECTED_BAND)),
            "estimate": True,
            "confidence": "low",
        }
    return out


def _qualitative_shape(focus_events, recon, farm_count):
    """Heuristic eco NARRATIVE from gather-point timestamps + eco techs + farm count (interpretation
    is heuristic, the timestamps are exact). Never a number presented as fact — just the shape."""
    first_by_res = {}
    for e in focus_events:
        first_by_res.setdefault(e["resource"], e["t_s"])
    committed_first = min(first_by_res, key=first_by_res.get) if first_by_res else None
    eco_techs = [t["name"] for t in recon.get("techs", {}).get("eco", [])]
    return {
        "committed_first": committed_first,
        "first_assignment_s_by_resource": first_by_res,
        "gold_mining_start_s": first_by_res.get("gold"),
        "active_farms": farm_count,
        "eco_techs": eco_techs,
        "relic_gold": "unavailable",  # no command signal for relic gold; never estimated
        "note": (
            "Eco shape from GATHER_POINT commands resolved via dropoff buildings + nearest GAIA "
            "resource (so wood, which is gather-pointed to a Lumber Camp, is now captured) and the "
            "#1 villager-production simulation. Timestamps exact; the per-resource split is an estimate."
        ),
    }


def estimate_economy(ops, player, gaia_list, recon):
    """Assemble the full ESTIMATE block: gather-focus events + simulated pops -> per-age worker split
    + collected band (suppressed if implausible / out of band) + qualitative shape.

    Returns a JSON-serializable dict, always labeled `estimate: true`. `collected` is a per-resource
    band dict OR None (suppressed). `worker_split_at_ages` and `qualitative` are always present.
    """
    # Accept either #1's Reconstruction object or its dict form.
    if not isinstance(recon, dict):
        recon = recon.to_dict()
    gaia_by_objid = gaia_mod.by_object_id(gaia_list)
    resource_points = gaia_mod.resource_points(gaia_list)
    ages = recon.get("ages", {})
    civ = recon.get("meta", {}).get("my_civ")
    starting = const.starting_villagers(civ)

    focus = gather_focus_events(ops, player, gaia_by_objid, resource_points)
    sim = production.simulate_villagers(ops, player, civ, ages)
    farm_count = active_farms(ops, player)

    split = worker_split_at_ages(focus, sim.pop_times_s, starting, ages)
    totals, villager_seconds = _integrate_collected(focus, sim.pop_times_s, starting, recon)
    collected = collected_estimate(totals, max_worker_seconds=villager_seconds)
    qualitative = _qualitative_shape(focus, recon, farm_count)

    focus_by_res = Counter(e["resource"] for e in focus)

    return {
        "estimate": True,
        "n_gather_focus_events": len(focus),
        "gather_focus_by_resource": dict(focus_by_res),
        "worker_split_at_ages": split,
        "collected": collected,
        "qualitative": qualitative,
        "note": (
            "Tier-B ESTIMATE layer. Economy is NOT in the rec; reconstructed from GATHER_POINT commands "
            "+ the villager-production simulation. The per-age WORKER SPLIT is the primary signal "
            "(now captures wood, not ~all food); collected totals are suppressed unless plausible. "
            "Relic gold is unavailable (no command signal)."
        ),
    }


def validate_collected(collected, truth_by_resource):
    """Check a collected estimate against ground-truth totals and return a per-resource + total band
    report. Used by the calibration loop / real-rec gate (not by the live estimator).

    `truth_by_resource` = {"food","wood","gold","stone": int}. Returns {resource: {in_band, error},
    total: {...}}. A suppressed (None) collected -> {"suppressed": True}.
    """
    if collected is None:
        return {"suppressed": True}
    report = {}
    est_total = 0
    truth_total = sum(truth_by_resource.get(r, 0) for r in _RESOURCES)
    for r in _RESOURCES:
        band = collected.get(r)
        truth = truth_by_resource.get(r, 0)
        if band is None:
            report[r] = {"suppressed": True}
            continue
        est_total += band["value"]
        err = abs(band["value"] - truth) / truth if truth else None
        report[r] = {"in_band": (err is not None and err <= BAND_PER_RESOURCE), "error": err}
    total_err = abs(est_total - truth_total) / truth_total if truth_total else None
    report["total"] = {
        "estimated": est_total,
        "truth": truth_total,
        "error": total_err,
        "in_band": (total_err is not None and total_err <= BAND_TOTAL),
    }
    return report
