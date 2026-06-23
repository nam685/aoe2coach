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

from . import const, production, rates, spend
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

# Water FOOD workers: a Fishing Ship (Dock unit, id 13) gathers food from shore/deep fish, and a Fish
# Trap (BUILD id 199) is a buildable food source a fishing ship works. On a no-water (land) map there
# are zero of both, so this gracefully contributes nothing. Counted as FOOD workers in the model.
_FISHING_SHIP_UNIT_ID = 13
_FISH_TRAP_BUILDING_ID = 199


def fishing_food_workers(ops, player, *, at_s=None):
    """Count `player`'s WATER food workers — Fishing Ships queued (Dock unit 13) + Fish Traps built
    (BUILD 199) — optionally only those committed at or before `at_s` seconds.

    A Fishing Ship is a queued unit (DE_QUEUE, respects `amount`); a Fish Trap is a placed building
    (BUILD). Both feed FOOD. Returns an int; zero on no-water games (graceful). Pure; never raises.
    """
    n = 0
    for t, action_type, data in ops:
        if data.get("player_id") != player:
            continue
        if at_s is not None and t // 1000 > at_s:
            continue
        if action_type == Action.DE_QUEUE and data.get("unit_id") == _FISHING_SHIP_UNIT_ID:
            n += int(data.get("amount", 1) or 1)
        elif action_type == Action.BUILD and data.get("building_id") == _FISH_TRAP_BUILDING_ID:
            n += 1
    return n


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


def worker_split_at_ages(focus_events, pop_times_s, starting, ages, fishing_ops=None, player=None):
    """Estimated WORKER-on-resource split at each age boundary (the primary deliverable; unit=workers).

    Each villager POPPED (from the production simulation) by the age-arrival time is attributed to the
    trailing-window gather-point distribution at its pop time (fractional, to smooth the single-focus
    flip-flop). Starting villagers seed food (they begin on sheep/berries). FISHING workers (Fishing
    Ships + Fish Traps committed by the age time) are added as FOOD workers — zero on land maps, so
    this is a no-op there. Returns {age: {estimate, alloc:{resource: count}, shares:{resource: frac},
    villagers_present, fishing_workers} | None}.

    `fishing_ops`/`player`: when both given, fishing food workers committed by each age time are
    counted via fishing_food_workers and folded into the food allocation/share.
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
        vils_present = starting + sum(1 for pt in pop_times_s if pt <= t)
        # Water food workers committed by this age time (0 on land maps -> graceful no-op).
        fishing = (
            fishing_food_workers(fishing_ops, player, at_s=t) if (fishing_ops is not None and player is not None) else 0
        )
        acc["food"] += fishing
        present = vils_present + fishing
        grand = sum(acc.values())
        # Normalize the resource shares to the physical workers-present count.
        shares = {r: acc[r] / grand for r in acc} if grand else {}
        alloc = {r: round(shares.get(r, 0.0) * present) for r in _RESOURCES if shares.get(r, 0.0) > 0}
        out[age] = {
            "estimate": True,
            "unit": "workers",
            "villagers_present": vils_present,
            "fishing_workers": fishing,
            "workers_present": present,
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


# --- Floating heuristic (#3) -------------------------------------------------------------------
# FLOATING is a HEURISTIC mismatch between gathering INTENT (worker share, from the gather model) and
# SPENDING share (near-exact, from spend.py) — NOT an absolute bank total (those stay suppressed).
# We only judge the MID-GAME: end-game float (a maxed eco with nothing left to spend on) is normal,
# so a late float is not a mistake. A resource floats when its mid-game worker share EXCEEDS its
# mid-game spend share by a sustained margin.

# Mid-game window as a fraction of game duration. Skip the opening (no spend signal yet, villagers
# still being seeded) and the end-game (float is normal once maxed). Castle/early-Imperial is where a
# real float (e.g. banked stone with no castle, banked wood with no production) actually hurts.
MIDGAME_START_FRAC = 0.25
MIDGAME_END_FRAC = 0.80

# A resource is flagged floating when (worker_share - spend_share) exceeds this. The worker share is
# the gathering INTENT; if you put 30% of workers on a resource but only spend 10% of your outlay on
# it, ~20% of your gathering is piling up. Tunable; deliberately conservative to avoid false fires.
FLOAT_MARGIN = 0.15


def mid_game_worker_share(focus_events, pop_times_s, starting, duration_s, fishing_ops=None, player=None):
    """Worker SHARE per resource over the mid-game window (gathering INTENT, fractions summing ~1).

    Integrates Σ workers_on_R(t) over [MIDGAME_START_FRAC, MIDGAME_END_FRAC]·duration using the
    trailing-window gather distribution and the simulated worker population — the same intent signal
    as the per-age split, but averaged over the mid-game so a single flip-flop can't dominate.
    Returns {resource: frac} or {} if no signal. Fishing adds to food intent (0 on land). Pure."""
    if not duration_s:
        return {}
    start = int(duration_s * MIDGAME_START_FRAC)
    end = int(duration_s * MIDGAME_END_FRAC)
    if end <= start:
        return {}
    acc = dict.fromkeys(_RESOURCES, 0.0)
    step = 30
    t = start
    while t < end:
        mid = t + step // 2
        present = starting + sum(1 for pt in pop_times_s if pt <= mid)
        if fishing_ops is not None and player is not None:
            present += fishing_food_workers(fishing_ops, player, at_s=mid)
        wd = _focus_window_dist(focus_events, mid)
        tot = sum(wd.values())
        if tot and present:
            for r, n in wd.items():
                if r in acc:
                    acc[r] += present * (n / tot) * step
        elif present:
            acc["food"] += present * step  # no gather signal -> assume food (sheep/berries)
        t += step
    grand = sum(acc.values())
    if grand <= 0:
        return {}
    return {r: acc[r] / grand for r in _RESOURCES}


def floating_signal(worker_share, spend_share):
    """HEURISTIC floating flags from the mid-game worker share vs spend share (both fractions).

    For each resource, excess = worker_share - spend_share. A POSITIVE sustained excess beyond
    FLOAT_MARGIN means gathering intent outruns spending -> that resource is floating. Returns
    {"flags": [{resource, worker_share, spend_share, excess}], "estimate": True, "basis": "..."}.
    Empty flags = no float detected. NEVER reports a bank total — only the two shares + their gap.
    Returns {} (no signal) if either share is empty."""
    if not worker_share or not spend_share:
        return {}
    flags = []
    for r in _RESOURCES:
        ws = worker_share.get(r, 0.0)
        ss = spend_share.get(r, 0.0)
        excess = ws - ss
        if excess > FLOAT_MARGIN:
            flags.append(
                {
                    "resource": r,
                    "worker_share": round(ws, 3),
                    "spend_share": round(ss, 3),
                    "excess": round(excess, 3),
                }
            )
    flags.sort(key=lambda f: -f["excess"])
    return {
        "estimate": True,
        "flags": flags,
        "worker_share": {r: round(worker_share.get(r, 0.0), 3) for r in _RESOURCES},
        "spend_share": {r: round(spend_share.get(r, 0.0), 3) for r in _RESOURCES},
        "basis": (
            "MID-GAME heuristic: per-resource mid-game gathering-intent SHARE (worker allocation) "
            "minus SPENDING share (near-exact from commands). A positive sustained gap = floating R. "
            "End-game float is normal and excluded. NOT a bank total."
        ),
    }


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
    """Assemble the full ESTIMATE block as TWO distinct, never-conflated blocks:

      worker_allocation  — villager + fishing COUNTS per resource (unit=workers): per age + a
                           mid-game share. The gathering INTENT signal. tier=estimate.
      resource_balance   — per-resource SPENDING (near-exact from BUILD+DE_QUEUE+RESEARCH commands)
                           + qualitative FLOATING flags (heuristic mid-game intent-vs-spend gap).
                           NO fabricated gathered/bank totals. tier=mixed (spend near-exact / float
                           heuristic).

    Returns a JSON-serializable dict, always labeled `estimate: true`. Collected gathered totals stay
    SUPPRESSED (we never fabricate bank totals); relic gold is always `unavailable`.
    """
    # Accept either #1's Reconstruction object or its dict form.
    if not isinstance(recon, dict):
        recon = recon.to_dict()
    gaia_by_objid = gaia_mod.by_object_id(gaia_list)
    resource_points = gaia_mod.resource_points(gaia_list)
    ages = recon.get("ages", {})
    civ = recon.get("meta", {}).get("my_civ")
    starting = const.starting_villagers(civ)
    duration_s = recon.get("meta", {}).get("duration_s")

    focus = gather_focus_events(ops, player, gaia_by_objid, resource_points)
    sim = production.simulate_villagers(ops, player, civ, ages)
    farm_count = active_farms(ops, player)
    fishing_total = fishing_food_workers(ops, player)

    # --- worker_allocation block (unit = workers; villager + fishing counts) ---
    split = worker_split_at_ages(focus, sim.pop_times_s, starting, ages, fishing_ops=ops, player=player)
    mg_worker_share = mid_game_worker_share(
        focus, sim.pop_times_s, starting, duration_s, fishing_ops=ops, player=player
    )
    focus_by_res = Counter(e["resource"] for e in focus)
    worker_allocation = {
        "unit": "workers",
        "tier": "estimate",
        "estimate": True,
        "per_age": split,
        "mid_game_share": {r: round(s, 3) for r, s in mg_worker_share.items()},
        "n_gather_focus_events": len(focus),
        "gather_focus_by_resource": dict(focus_by_res),
        "fishing_workers_total": fishing_total,
        "active_farms": farm_count,
        "note": (
            "WORKER COUNTS per resource (villagers + fishing), NOT resource amounts. e.g. 'food: 18' "
            "means 18 workers gathering food. From GATHER_POINT intent + the #1 production sim; "
            "fishing (ships+traps) folds into food (0 on land maps)."
        ),
    }

    # --- resource_balance block (unit = resource amounts; near-exact SPENDING + heuristic floating) ---
    spent = spend.spent_by_resource(ops, player)
    spend_sh = spend.spend_share(spent)
    floating = floating_signal(mg_worker_share, spend_sh)
    resource_balance = {
        "unit": "resource_amounts",
        "tier": "spending-near-exact + floating-heuristic",
        "estimate": True,
        "spent_by_resource": spent,
        "spend_share": {r: round(spend_sh.get(r, 0.0), 3) for r in _RESOURCES} if spend_sh else {},
        "floating": floating,
        "collected": None,  # gathered/bank totals are NOT fabricated — suppressed by design
        "relic_gold": "unavailable",
        "note": (
            "SPENDING is near-exact (sum of BUILD+DE_QUEUE+RESEARCH costs; ignores cancels -> slight "
            "over-count). FLOATING is a HEURISTIC mid-game gap between gathering INTENT (worker share) "
            "and spend share — NOT a bank total. Gathered/collected totals are deliberately suppressed "
            "(never fabricated); relic gold is unavailable (no command signal)."
        ),
    }

    qualitative = _qualitative_shape(focus, recon, farm_count)

    return {
        "estimate": True,
        "worker_allocation": worker_allocation,
        "resource_balance": resource_balance,
        # Back-compat top-level keys retained for existing consumers/tests.
        "n_gather_focus_events": len(focus),
        "gather_focus_by_resource": dict(focus_by_res),
        "worker_split_at_ages": split,
        "collected": None,
        "qualitative": qualitative,
        "note": (
            "Tier-B ESTIMATE layer, TWO blocks (never conflated): worker_allocation (villager+fishing "
            "COUNTS per resource, unit=workers) and resource_balance (near-exact SPENDING + qualitative "
            "FLOATING flags, NO fabricated gathered totals). Relic gold unavailable (no command signal)."
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
