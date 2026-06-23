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

import bisect
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
_CAMP_BUILDING_IDS = (562, 584)  # Lumber Camp / Mining Camp — drop-off camps = gather-intent on BUILD
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
        if data.get("player_id") != player:
            continue
        if action_type == Action.GATHER_POINT:
            r = resolve_gather_resource(data, gaia_by_objid, resource_points)
        elif action_type == Action.BUILD and data.get("building_id") in _CAMP_BUILDING_IDS:
            # Building a resource drop-off camp IS a gather-intent signal toward that camp's resource:
            # a Mining Camp → the nearest gold/stone, a Lumber Camp → wood. This captures villagers
            # PULLED off their current resource to go build/mine elsewhere — a move that emits no
            # GATHER_POINT, so the old model wrongly kept them on the stale (often wood) signal.
            r = resolve_gather_resource(
                {"target_type": data.get("building_id"), "x": data.get("x"), "y": data.get("y")},
                gaia_by_objid,
                resource_points,
            )
        else:
            continue
        if r is None:
            continue
        out.append({"t_s": t // 1000, "resource": r})
    out.sort(key=lambda e: e["t_s"])
    return out


def _farm_first_build_by_tile(ops, player):
    """{(round(x), round(y)): first_build_t_s} for each DISTINCT farm tile `player` built, deduping
    reseeds (a farm rebuilt on the ~same rounded tile is the SAME farm, keeping the earliest build).
    Shared dedup logic for active_farms (count) and active_farm_times (timeline)."""
    first_t = {}
    for t, action_type, data in ops:
        if action_type != Action.BUILD or data.get("player_id") != player:
            continue
        if data.get("building_id") != _FARM_BUILDING_ID:
            continue
        x = data.get("x")
        y = data.get("y")
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            tile = (round(x), round(y))
            ts = t // 1000
            if tile not in first_t or ts < first_t[tile]:
                first_t[tile] = ts
    return first_t


def active_farms(ops, player):
    """Number of DISTINCT farm tiles `player` built, deduping reseeds (a farm rebuilt on the ~same
    tile is the same farm, not a new one). Late-game food workers ≈ number of active farms."""
    return len(_farm_first_build_by_tile(ops, player))


def active_farm_times(ops, player):
    """Sorted list of the FIRST-build time (seconds) for each DISTINCT farm tile `player` built,
    deduping reseeds (reusing active_farms' dedup logic — a reseed on the same rounded tile is NOT a
    new farm and keeps the earliest build time). Each farm ≈ a farmer, so this is the time-aware floor
    on late-game FOOD workers. Pair with farms_by(t) for a bisect count of farms built by time t."""
    return sorted(_farm_first_build_by_tile(ops, player).values())


def farms_by(farm_times_s, t_s):
    """Number of distinct farms first-built at or before `t_s` — bisect over the sorted
    active_farm_times list. Each farm ≈ one food worker, so this floors the late-game food count."""

    return bisect.bisect_right(farm_times_s, t_s)


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


def _farm_anchored_split(acc, present, farm_food_floor, fishing):
    """Recompute the per-resource worker counts so ACTIVE FARMS anchor the FOOD count (each farm = a
    farmer), displacing wood-misattributed villagers OUT of wood (the late-game bug fix).

    `acc` is the raw gather-INTENT attribution (Counter of fractional worker counts, before fishing).
    Logic:
      - gather_food   = acc["food"] (sheep/berry/farm gather-intent food, strong early game)
      - food          = min(present, max(gather_food, farm_food_floor) + fishing)
                        farms FLOOR food late-game; early-game keeps the larger gather-intent food;
                        never exceeds workers present.
      - remaining     = present - food, split across wood/gold/stone by their gather-INTENT
                        proportions (the non-food part of the trailing-window distribution). So pulling
                        villagers onto farms moves them OUT of wood.
    Returns ({resource: float count}, food_count).
    """
    gather_food = acc.get("food", 0.0)
    food = min(float(present), max(gather_food, float(farm_food_floor)) + fishing)
    food = max(food, 0.0)
    remaining = max(present - food, 0.0)
    # Non-food gather-intent proportions drive how the remaining workers split.
    non_food = {r: acc.get(r, 0.0) for r in ("wood", "gold", "stone")}
    nf_total = sum(non_food.values())
    counts = dict.fromkeys(_RESOURCES, 0.0)
    counts["food"] = food
    if remaining > 0:
        if nf_total > 0:
            for r, v in non_food.items():
                counts[r] = remaining * (v / nf_total)
        else:
            # No non-food gather intent at all -> the remaining workers default to wood (the usual
            # un-signalled bulk economy resource), rather than vanishing.
            counts["wood"] = remaining
    return counts, food


def worker_split_at_ages(focus_events, pop_times_s, starting, ages, fishing_ops=None, player=None, farm_times_s=None):
    """Estimated WORKER-on-resource split at each age boundary (the primary deliverable; unit=workers).

    Each villager POPPED (from the production simulation) by the age-arrival time is attributed to the
    trailing-window gather-point distribution at its pop time (fractional, to smooth the single-focus
    flip-flop). Starting villagers seed food (they begin on sheep/berries). FISHING workers (Fishing
    Ships + Fish Traps committed by the age time) are added as FOOD workers — zero on land maps.

    FARM ANCHOR (late-game-attribution fix): players SET the TC gather point to WOOD then PULL those
    choppers off wood to spam-build FARMS — a move that emits NO "now on food" signal, so the gather
    model wrongly leaves them on wood (game1 imperial read 68% wood). Each active farm ≈ a farmer, so
    when `farm_times_s` (from active_farm_times) is given, FOOD is floored at the farm count built by
    the age time: `food = min(present, max(gather_food, farms_by(t)) + fishing)`. The remaining workers
    split across wood/gold/stone by their gather-INTENT proportions, so farms pull villagers OUT of
    wood. Early game (few/no farms) keeps the larger sheep/berry gather-intent food.

    Returns {age: {estimate, alloc:{resource: count}, shares:{resource: frac}, villagers_present,
    fishing_workers} | None}.
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
        present = vils_present + fishing
        # FARM ANCHOR: floor food on the active-farm count by this age, displacing wood.
        farm_floor = farms_by(farm_times_s, t) if farm_times_s else 0
        counts, _food = _farm_anchored_split(acc, present, farm_floor, fishing)
        grand = sum(counts.values())
        shares = {r: counts[r] / grand for r in _RESOURCES if grand} if grand else {}
        alloc = {r: round(counts[r]) for r in _RESOURCES if counts[r] > 0}
        out[age] = {
            "estimate": True,
            "unit": "workers",
            "villagers_present": vils_present,
            "fishing_workers": fishing,
            "workers_present": present,
            "n_attributed": attributed,
            "alloc": alloc,
            "shares": {r: round(shares.get(r, 0.0), 3) for r in _RESOURCES if shares.get(r, 0.0) > 0},
        }
    return out


def worker_split_series(focus_events, pop_times_s, starting, fishing_ops=None, player=None, farm_times_s=None):
    """Continuous worker-on-resource split at EVERY villager count from `starting` to the max popped.

    Identical farm-anchored model to worker_split_at_ages, but emits ONE snapshot per villager POP
    (indexed by villager count) rather than only at the three age boundaries — the data behind the
    villager-count-indexed economy graph. The gather-intent accumulator `acc` grows exactly as in the
    age model, so a point read at an age-arrival villager count matches that age's per_age snapshot.

    Each point: {vils, t_s, alloc:{resource: count}, active_farms, fishing}. `t_s` is the pop time at
    which that villager count was reached (the `starting` count is emitted at t_s=0). `active_farms` is
    the farm-count floor at t_s (drawn in-graph instead of a standalone seeded-total that contradicts
    the food count). `alloc` sums to workers-present (= vils + fishing); fishing folds into food.
    """
    pts = sorted(p for p in pop_times_s)
    acc = Counter()
    acc["food"] += starting  # pre-placed villagers start on food (sheep/berries)
    series = []

    def _point(vils, t):
        t = int(t)
        fishing = (
            fishing_food_workers(fishing_ops, player, at_s=t) if (fishing_ops is not None and player is not None) else 0
        )
        present = vils + fishing
        farm_floor = farms_by(farm_times_s, t) if farm_times_s else 0
        counts, _food = _farm_anchored_split(acc, present, farm_floor, fishing)
        return {
            "vils": vils,
            "t_s": t,
            "alloc": {r: round(counts[r], 2) for r in _RESOURCES if counts[r] > 0},
            "active_farms": farm_floor,
            "fishing": fishing,
        }

    series.append(_point(starting, 0))
    for i, pt in enumerate(pts):
        wd = _focus_window_dist(focus_events, int(pt))
        tot = sum(wd.values())
        if tot:
            for r, n in wd.items():
                acc[r] += n / tot
        series.append(_point(starting + i + 1, pt))
    return series


def resource_balance_series(ops, player, pop_times_s, starting):
    """Cumulative near-exact SPEND per resource at each villager-count checkpoint (start→max popped).

    Shares the villager-count x-axis with worker_split_series so the two economy graphs stack visually.
    At each checkpoint (vils, t_s) the value is the cumulative spend (BUILD+train+research costs) up to
    t_s — monotonic, honest, log-scalable. Collected/bank totals stay suppressed (never fabricated).

    Each point: {vils, t_s, spent:{resource: amount}}. `attach_floating` later adds a `floating` dict.
    """
    pts = sorted(pop_times_s)
    checkpoints = [(starting, 0)] + [(starting + i + 1, int(pt)) for i, pt in enumerate(pts)]
    series = []
    for vils, t in checkpoints:
        series.append({"vils": vils, "t_s": t, "spent": spend.spent_by_resource(ops, player, end_s=t)})
    return series


def attach_floating(rb_series, wa_series):
    """Add a per-resource FLOATING estimate to each resource_balance.series point (in place, returns it).

    floating(R, t) = max(0, total_spent(t) × worker_share(R, t) − spent(R, t)): if spending had matched
    gathering EFFORT (the worker-share), you'd have spent total_spent × worker_share on R; you only spent
    spent(R); the surplus is floating (gathered-but-unspent). This is the honest floating SIGNAL —
    anchored to near-exact spend × the trusted worker-share — NOT a fabricated bank total (the book-rate
    collected integral is unreliable, ~3× over on wood). worker_share is cumulative worker-seconds
    (∫ alloc dt), so it tracks where effort actually went over time.

    rb_series and wa_series are index-aligned (same per-villager-count checkpoints). Each rb point gains
    a `floating` dict {resource: amount} holding only resources with a positive surplus.
    """
    ws = dict.fromkeys(_RESOURCES, 0.0)  # cumulative worker-seconds per resource
    n = min(len(rb_series), len(wa_series))
    for i in range(n):
        if i > 0:
            dt = max(0, wa_series[i]["t_s"] - wa_series[i - 1]["t_s"])
            alloc = wa_series[i].get("alloc", {})  # allocation reached by this checkpoint, over the interval
            for r in _RESOURCES:
                ws[r] += alloc.get(r, 0) * dt
        tot_ws = sum(ws.values())
        spent = rb_series[i].get("spent", {})
        tot_spent = sum(spent.values())
        floating = {}
        if tot_ws > 0 and tot_spent > 0:
            for r in _RESOURCES:
                surplus = tot_spent * (ws[r] / tot_ws) - spent.get(r, 0)
                if surplus > 1:
                    floating[r] = int(round(surplus))
        rb_series[i]["floating"] = floating
    return rb_series


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


def mid_game_worker_share(
    focus_events, pop_times_s, starting, duration_s, fishing_ops=None, player=None, farm_times_s=None
):
    """Worker SHARE per resource over the mid-game window (fractions summing ~1) — the SAME
    FARM-ANCHORED allocation as the per-age split (NOT raw gather intent), so the floating signal it
    feeds reflects the corrected (lower) wood share.

    Integrates Σ workers_on_R(t) over [MIDGAME_START_FRAC, MIDGAME_END_FRAC]·duration. In each window
    the workers-present are split via the gather-INTENT distribution and then re-anchored on the
    active-farm count (`farms_by(mid)`), exactly like worker_split_at_ages, so wood-misattributed
    farmers move OUT of wood. Returns {resource: frac} or {} if no signal. Fishing adds to food (0 on
    land). Pure."""
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
        vils = starting + sum(1 for pt in pop_times_s if pt <= mid)
        fishing = (
            fishing_food_workers(fishing_ops, player, at_s=mid)
            if (fishing_ops is not None and player is not None)
            else 0
        )
        present = vils + fishing
        if present:
            # Per-window gather-intent attribution, then the farm anchor (same logic as the per-age
            # split) so this mid-game share reflects the corrected wood/food allocation.
            window = Counter({"food": starting})  # starting vils seed food in every window
            wd = _focus_window_dist(focus_events, mid)
            tot = sum(wd.values())
            attributable = vils - starting
            if tot and attributable > 0:
                for r, n in wd.items():
                    window[r] += attributable * (n / tot)
            elif attributable > 0:
                window["food"] += attributable  # no gather signal -> assume food
            farm_floor = farms_by(farm_times_s, mid) if farm_times_s else 0
            counts, _food = _farm_anchored_split(window, present, farm_floor, fishing)
            for r in _RESOURCES:
                acc[r] += counts[r] * step
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
    farm_times = active_farm_times(ops, player)
    farm_count = len(farm_times)
    fishing_total = fishing_food_workers(ops, player)

    # --- worker_allocation block (unit = workers; villager + fishing counts) ---
    # Farm-anchored: active farms FLOOR the food count (each farm = a farmer), pulling wood-
    # misattributed villagers OUT of wood (the late-game-attribution fix).
    split = worker_split_at_ages(
        focus, sim.pop_times_s, starting, ages, fishing_ops=ops, player=player, farm_times_s=farm_times
    )
    mg_worker_share = mid_game_worker_share(
        focus, sim.pop_times_s, starting, duration_s, fishing_ops=ops, player=player, farm_times_s=farm_times
    )
    focus_by_res = Counter(e["resource"] for e in focus)
    wa_series = worker_split_series(
        focus, sim.pop_times_s, starting, fishing_ops=ops, player=player, farm_times_s=farm_times
    )
    worker_allocation = {
        "unit": "workers",
        "tier": "estimate",
        "estimate": True,
        "per_age": split,
        "series": wa_series,
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
        "series": attach_floating(resource_balance_series(ops, player, sim.pop_times_s, starting), wa_series),
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
