"""Tests for the #2 economy model (Tier-B estimate layer): gaia, rates, econ.

Synthetic ops are (t_ms, Action, data) tuples faithful to mgz.fast.parse_action shapes; synthetic
gaia objects mirror header["players"][0]["objects"] entries {class_id, object_id, instance_id,
position, index}. The honesty rule is under test: every collected number carries estimate/confidence/
[low,high], and the model SELF-SUPPRESSES (returns None) when out of the validation band.

The REWORKED attribution model (2026-06): gather points resolve a resource via (1) dropoff building
id (Lumber Camp→wood, Mining Camp→gold/stone by nearest mine, Mill/TC/Farm→food), (2) target_type as
a GAIA object_id, then (3) the nearest GAIA resource to the gather x/y. Newly-popped villagers (from
the #1 production simulation) are attributed to the trailing-window gather-point distribution, so the
per-age worker split shows meaningful WOOD (the old model under-signaled wood → ~100% food).
"""

import os

import pytest
from mgz.fast import Action

from aoe2coach import econ, gaia, rates

REC_PATH = "/home/namle685/projects/aoe2coach-analysis/game.aoe2record"
REC2_PATH = "/home/namle685/projects/aoe2coach-analysis/game2.aoe2record"
RELIC_PROFILE_ID = 14697894

requires_rec = pytest.mark.skipif(not os.path.exists(REC_PATH), reason="calibration rec not present")
requires_rec2 = pytest.mark.skipif(not os.path.exists(REC2_PATH), reason="second rec not present")


# ----------------------------------------------------------------------------- gaia
def _gaia_objs():
    """Synthetic gaia table covering each resource family + a decoration id (None) + a building."""
    return [
        {"class_id": 20, "object_id": 1902, "instance_id": 100, "position": {"x": 50.0, "y": 50.0}},  # tree -> wood
        {"class_id": 70, "object_id": 305, "instance_id": 101, "position": {"x": 48.0, "y": 51.0}},  # sheep -> food
        {"class_id": 70, "object_id": 822, "instance_id": 102, "position": {"x": 47.0, "y": 49.0}},  # boar -> food
        {"class_id": 10, "object_id": 1053, "instance_id": 103, "position": {"x": 52.0, "y": 52.0}},  # berries -> food
        {"class_id": 10, "object_id": 66, "instance_id": 104, "position": {"x": 55.0, "y": 55.0}},  # gold mine -> gold
        {
            "class_id": 10,
            "object_id": 102,
            "instance_id": 105,
            "position": {"x": 56.0, "y": 56.0},
        },  # stone mine -> stone
        {
            "class_id": 10,
            "object_id": 1358,
            "instance_id": 106,
            "position": {"x": 60.0, "y": 60.0},
        },  # decoration -> None
        {"class_id": 30, "object_id": 69, "instance_id": 107, "position": {"x": 40.0, "y": 40.0}},  # relic -> None
    ]


def test_gaia_objects_indexes_by_instance_id():
    g = gaia.gaia_objects(_gaia_objs())
    assert g[100]["object_id"] == 1902
    assert g[104]["class_id"] == 10
    assert len(g) == 8


def test_gaia_objects_empty_on_missing():
    assert gaia.gaia_objects([]) == {}
    assert gaia.gaia_objects(None) == {}


def test_resource_class_each_family():
    objs = {o["instance_id"]: o for o in _gaia_objs()}
    assert gaia.resource_class(objs[100]) == "wood"  # tree
    assert gaia.resource_class(objs[101]) == "food"  # sheep (class 70)
    assert gaia.resource_class(objs[102]) == "food"  # boar (class 70)
    assert gaia.resource_class(objs[103]) == "food"  # berries
    assert gaia.resource_class(objs[104]) == "gold"  # gold mine
    assert gaia.resource_class(objs[105]) == "stone"  # stone mine


def test_resource_class_decoration_and_relic_and_unknown_are_none():
    objs = {o["instance_id"]: o for o in _gaia_objs()}
    assert gaia.resource_class(objs[106]) is None  # class-10 decoration id -> not a resource
    assert gaia.resource_class(objs[107]) is None  # relic
    assert gaia.resource_class({"class_id": 10, "object_id": 999999}) is None  # unknown class-10
    assert gaia.resource_class(None) is None  # never raises
    assert gaia.resource_class({}) is None


# ----------------------------------------------------------- nearest_resource + dropoff resolution
def test_nearest_resource_picks_closest_gaia():
    res = gaia.resource_points(_gaia_objs())
    # near the tree at (50,50)
    r, d = gaia.nearest_resource(res, 50.5, 50.5)
    assert r == "wood" and d < 1.0
    # restricted to gold/stone near the gold mine
    r2, _ = gaia.nearest_resource(res, 55.2, 55.1, classes={"gold", "stone"})
    assert r2 == "gold"


def test_nearest_resource_empty_table_returns_none():
    assert gaia.nearest_resource([], 1.0, 2.0) == (None, float("inf"))


# ------------------------------------------------------------------------- resolve_gather_resource
def test_resolve_gather_resource_dropoff_building_ids():
    res = gaia.resource_points(_gaia_objs())
    by_objid = gaia.by_object_id(_gaia_objs())
    # Lumber Camp (562) target_type -> wood regardless of x/y
    assert econ.resolve_gather_resource({"target_type": 562, "x": 0.0, "y": 0.0}, by_objid, res) == "wood"
    # Mill (68) -> food
    assert econ.resolve_gather_resource({"target_type": 68, "x": 0.0, "y": 0.0}, by_objid, res) == "food"
    # Town Center (621) -> food
    assert econ.resolve_gather_resource({"target_type": 621, "x": 0.0, "y": 0.0}, by_objid, res) == "food"
    # Mining Camp (584) -> nearest gold/stone mine: place near the gold mine (55,55)
    assert econ.resolve_gather_resource({"target_type": 584, "x": 55.0, "y": 55.0}, by_objid, res) == "gold"


def test_resolve_gather_resource_gaia_objectid_then_nearest():
    res = gaia.resource_points(_gaia_objs())
    by_objid = gaia.by_object_id(_gaia_objs())
    # target_type is a GAIA object_id (66 = gold mine) -> gold
    assert econ.resolve_gather_resource({"target_type": 66, "x": 0.0, "y": 0.0}, by_objid, res) == "gold"
    # target_type -1 (bare ground) but x/y sits on the tree -> nearest -> wood (vils gather-pointed to LC)
    assert econ.resolve_gather_resource({"target_type": -1, "x": 50.0, "y": 50.0}, by_objid, res) == "wood"
    # target_type -1 far from any resource -> None (a MOVE, not a gather; never fabricate)
    assert econ.resolve_gather_resource({"target_type": -1, "x": 5.0, "y": 5.0}, by_objid, res) is None


# ----------------------------------------------------------- active_farms (dedup reseeds)
def test_active_farms_dedup_reseeds_on_same_tile():
    ops = [
        (10_000, Action.BUILD, {"player_id": 1, "building_id": 50, "x": 30.0, "y": 30.0}),  # farm A
        (20_000, Action.BUILD, {"player_id": 1, "building_id": 50, "x": 31.0, "y": 31.0}),  # farm B
        (900_000, Action.BUILD, {"player_id": 1, "building_id": 50, "x": 30.0, "y": 30.0}),  # RESEED of A
        (910_000, Action.BUILD, {"player_id": 2, "building_id": 50, "x": 30.0, "y": 30.0}),  # opp -> ignored
    ]
    # two distinct farm tiles for player 1 (the reseed of A does not inflate the count)
    assert econ.active_farms(ops, player=1) == 2


def test_active_farm_times_first_build_per_tile_sorted():
    ops = [
        (20_000, Action.BUILD, {"player_id": 1, "building_id": 50, "x": 31.0, "y": 31.0}),  # farm B @20
        (10_000, Action.BUILD, {"player_id": 1, "building_id": 50, "x": 30.0, "y": 30.0}),  # farm A @10
        (900_000, Action.BUILD, {"player_id": 1, "building_id": 50, "x": 30.0, "y": 30.0}),  # RESEED A (not new)
        (5_000, Action.BUILD, {"player_id": 2, "building_id": 50, "x": 30.0, "y": 30.0}),  # opp -> ignored
        (15_000, Action.BUILD, {"player_id": 1, "building_id": 12}),  # not a farm -> ignored
    ]
    # one FIRST-build timestamp per distinct tile, sorted ascending; reseed keeps the earliest.
    assert econ.active_farm_times(ops, player=1) == [10, 20]


def test_farms_by_counts_le_t():
    times = [10, 20, 100]
    assert econ.farms_by(times, 5) == 0
    assert econ.farms_by(times, 10) == 1
    assert econ.farms_by(times, 50) == 2
    assert econ.farms_by(times, 100) == 3
    assert econ.farms_by(times, 999) == 3


# ----------------------------------------------------------------------------- rates
def _recon_with_eco(eco_techs):
    return {"techs": {"eco": eco_techs}}


def test_rate_at_base_no_upgrades():
    recon = _recon_with_eco([])
    assert rates.rate_at("wood", 0, recon) == pytest.approx(0.55)
    assert rates.rate_at("gold", 0, recon) == pytest.approx(0.5175)
    assert rates.rate_at("stone", 0, recon) == pytest.approx(0.5175)
    assert rates.rate_at("food", 0, recon) == pytest.approx(0.45)


def test_rate_at_unknown_resource_zero():
    assert rates.rate_at("uranium", 0, _recon_with_eco([])) == 0.0


def test_rate_at_applies_wood_upgrade_after_timing_only():
    recon = _recon_with_eco([{"name": "Double-Bit Axe", "t_s": 600}])
    assert rates.rate_at("wood", 599, recon) == pytest.approx(0.55)  # before timing
    assert rates.rate_at("wood", 600, recon) == pytest.approx(0.55 * 1.20)  # at/after timing
    # wood upgrade does not affect gold
    assert rates.rate_at("gold", 600, recon) == pytest.approx(0.5175)


def test_rate_at_stacks_multiplicatively():
    recon = _recon_with_eco(
        [
            {"name": "Double-Bit Axe", "t_s": 600},
            {"name": "Bow Saw", "t_s": 1200},
            {"name": "Wheelbarrow", "t_s": 700},
        ]
    )
    # at t=1200: DBA(+20%) * BowSaw(+20%) * Wheelbarrow(+5%)
    expected = 0.55 * 1.20 * 1.20 * 1.05
    assert rates.rate_at("wood", 1200, recon) == pytest.approx(expected)


# ------------------------------------------------------------------------- gather_focus_events
def _assign_ops(me=1):
    # GATHER_POINT onto a Lumber Camp (562) -> wood; a gold mine gaia objectid (66) -> gold;
    # a bare-ground -1 that sits on the tree (nearest -> wood); a -1 far from anything (dropped);
    # an opponent gather point (dropped).
    return [
        (10_000, Action.GATHER_POINT, {"player_id": me, "target_type": 562, "x": 0.0, "y": 0.0, "object_ids": [9]}),
        (20_000, Action.GATHER_POINT, {"player_id": me, "target_type": 66, "x": 0.0, "y": 0.0, "object_ids": [9]}),
        (30_000, Action.GATHER_POINT, {"player_id": me, "target_type": -1, "x": 50.0, "y": 50.0, "object_ids": [9]}),
        (40_000, Action.GATHER_POINT, {"player_id": me, "target_type": -1, "x": 5.0, "y": 5.0, "object_ids": [9]}),
        (50_000, Action.GATHER_POINT, {"player_id": 2, "target_type": 562, "x": 0.0, "y": 0.0, "object_ids": [9]}),
    ]


def test_gather_focus_events_resolve_and_classify():
    res = gaia.resource_points(_gaia_objs())
    by_objid = gaia.by_object_id(_gaia_objs())
    evs = econ.gather_focus_events(_assign_ops(), player=1, gaia_by_objid=by_objid, resource_points=res)
    assert [(e["t_s"], e["resource"]) for e in evs] == [(10, "wood"), (20, "gold"), (30, "wood")]


# ------------------------------------------------------------------- worker_split_at_ages
def test_worker_split_shows_meaningful_wood_not_all_food():
    # Synthetic: villagers pop steadily; gather focus alternates wood/food before feudal.
    focus = [
        {"t_s": 30, "resource": "food"},
        {"t_s": 60, "resource": "wood"},
        {"t_s": 90, "resource": "wood"},
        {"t_s": 120, "resource": "food"},
        {"t_s": 150, "resource": "wood"},
    ]
    pop_times = [25 * i for i in range(1, 12)]  # 25..275
    ages = {"feudal_arrival_s": 200, "castle_arrival_s": None, "imperial_arrival_s": None}
    split = econ.worker_split_at_ages(focus, pop_times, starting=3, ages=ages)
    feud = split["feudal"]
    assert feud is not None
    # wood share is meaningful (not ~0), food is not ~100%
    assert feud["shares"]["wood"] > 0.2
    assert feud["shares"]["food"] < 0.9
    # shares normalize to ~1
    assert abs(sum(feud["shares"].values()) - 1.0) < 0.01
    assert split["castle"] is None  # age not reached


def test_worker_split_farms_floor_food_displacing_wood():
    """Late-game move: villagers gather-pointed to WOOD then pulled onto FARMS (no signal emitted).
    Farm count must FLOOR food and pull those workers OUT of wood."""
    # Heavy wood gather intent throughout; almost no food gather points.
    focus = [{"t_s": t, "resource": "wood"} for t in range(30, 2000, 60)]
    focus += [{"t_s": 40, "resource": "gold"}]  # a little gold intent
    pop_times = [25 * i for i in range(1, 80)]  # plenty of villagers
    ages = {"feudal_arrival_s": 200, "castle_arrival_s": 800, "imperial_arrival_s": 1800}
    # 40 active farms by imperial -> 40 food workers floored.
    farm_times = [50 + 30 * i for i in range(40)]  # all built by ~1250s, well before imperial
    no_anchor = econ.worker_split_at_ages(focus, pop_times, starting=3, ages=ages)
    anchored = econ.worker_split_at_ages(focus, pop_times, starting=3, ages=ages, farm_times_s=farm_times)
    imp_no = no_anchor["imperial"]
    imp_yes = anchored["imperial"]
    # Without anchor, gather intent makes food tiny and wood huge.
    assert imp_no["shares"]["food"] < 0.1
    # With the farm anchor, food rises sharply and wood drops.
    assert imp_yes["alloc"].get("food", 0) >= 40
    assert imp_yes["shares"]["food"] > imp_no["shares"]["food"] + 0.2
    assert imp_yes["shares"]["wood"] < imp_no["shares"]["wood"]
    # food can never exceed workers present.
    assert imp_yes["alloc"]["food"] <= imp_yes["workers_present"]
    # shares still normalize.
    assert abs(sum(imp_yes["shares"].values()) - 1.0) < 0.02


def test_worker_split_early_game_keeps_gather_food_when_no_farms():
    """Early game (no farms yet) keeps the sheep/berry gather-intent food, not farm-driven."""
    focus = [
        {"t_s": 30, "resource": "food"},
        {"t_s": 60, "resource": "food"},
        {"t_s": 90, "resource": "wood"},
    ]
    pop_times = [25 * i for i in range(1, 8)]
    ages = {"feudal_arrival_s": 200, "castle_arrival_s": None, "imperial_arrival_s": None}
    # no farms at all by feudal
    split = econ.worker_split_at_ages(focus, pop_times, starting=3, ages=ages, farm_times_s=[])
    feud = split["feudal"]
    # gather-intent food (sheep/berries) preserved, not zeroed by absent farms.
    assert feud["shares"]["food"] > 0.4


# ------------------------------------------------------------------- continuous series (graph data)
def test_worker_split_series_one_point_per_villager_count():
    """Continuous series: starting count first (all food), then one point per villager popped,
    villager count strictly increasing, alloc summing to workers-present."""
    focus = [{"t_s": 30, "resource": "food"}, {"t_s": 90, "resource": "wood"}]
    pop_times = [25 * i for i in range(1, 6)]  # 5 pops
    series = econ.worker_split_series(focus, pop_times, starting=3)
    assert len(series) == 1 + 5  # starting point + one per pop
    assert series[0]["vils"] == 3 and series[0]["t_s"] == 0
    assert series[0]["alloc"] == {"food": 3.0}  # pre-placed villagers seed food
    assert [p["vils"] for p in series] == [3, 4, 5, 6, 7, 8]  # strictly increasing
    for p in series:
        assert abs(sum(p["alloc"].values()) - p["vils"]) < 0.05  # land map: alloc sums to vils


def test_worker_split_series_food_overridden_by_farms_when_exceeding():
    """Per the owner: when (reseed-excluded) active farms exceed gather-intent food, FOOD is overridden
    to the farm count, and `active_farms` rides along as the food floor drawn in-graph."""
    focus = [{"t_s": t, "resource": "wood"} for t in range(30, 2000, 60)]  # heavy wood intent, ~no food
    pop_times = [25 * i for i in range(1, 60)]
    farm_times = [50 + 30 * i for i in range(30)]  # 30 distinct farms built by ~950s
    series = econ.worker_split_series(focus, pop_times, starting=3, farm_times_s=farm_times)
    late = series[-1]
    # food was overridden up to the active-farm count (gather intent was nearly all wood).
    assert late["active_farms"] == 30
    assert late["alloc"]["food"] >= 30
    # the food line equals the farm floor here (farms drive food), reconciling the old contradiction.
    assert late["alloc"]["food"] == late["active_farms"]


def test_resource_balance_series_is_monotonic_cumulative_spend():
    """Cumulative spend per resource at each villager-count checkpoint — non-decreasing, and the last
    point equals the whole-game spent_by_resource total (it shares the time axis via t_s)."""
    ops = [(i * 25_000, Action.DE_QUEUE, {"player_id": 1, "unit_id": 83, "amount": 1}) for i in range(1, 11)]
    pop_times = [25 * i for i in range(1, 11)]
    series = econ.resource_balance_series(ops, player=1, pop_times_s=pop_times, starting=3)
    assert series[0]["vils"] == 3 and series[0]["t_s"] == 0
    assert all("t_s" in p for p in series)  # carries the real-time axis
    food = [p["spent"].get("food", 0) for p in series]
    assert food == sorted(food)  # cumulative spend never decreases
    total = econ.spend.spent_by_resource(ops, 1)
    assert series[-1]["spent"] == total


# ------------------------------------------------------------------- collected_estimate (band/labels)
def test_collected_estimate_carries_band_and_labels():
    out = econ.collected_estimate({"food": 20, "wood": 18, "gold": 6, "stone": 2})
    if out is not None:
        for res in ("wood", "food", "gold", "stone"):
            if res in out and out[res] is not None:
                band = out[res]
                assert band["estimate"] is True
                assert band["low"] <= band["value"] <= band["high"]


def test_collected_estimate_suppresses_when_no_signal():
    # zero worker-seconds -> cannot honestly estimate anything -> None (suppressed), never a bare 0.
    assert econ.collected_estimate({}) is None


# ------------------------------------------------------------------- full estimate_economy
def test_estimate_economy_assembles_and_is_json_serializable():
    import json

    recon = {
        "techs": {"eco": [{"name": "Double-Bit Axe", "t_s": 600}]},
        "ages": {"feudal_arrival_s": 200, "castle_arrival_s": 800, "imperial_arrival_s": None},
        "meta": {"duration_s": 3000, "my_civ": "Britons"},
    }
    ops = _assign_ops() + [
        (i * 25_000, Action.DE_QUEUE, {"player_id": 1, "unit_id": 83, "amount": 1}) for i in range(20)
    ]
    out = econ.estimate_economy(ops, player=1, gaia_list=_gaia_objs(), recon=recon)
    assert out["estimate"] is True
    assert "worker_split_at_ages" in out
    assert "collected" in out  # may be a band dict or None (suppressed)
    assert "qualitative" in out  # narrative shape always present
    json.dumps(out)  # must serialize


# ------------------------------------------------------------------------ FIDELITY / real rec
@requires_rec
def test_real_rec_gather_focus_resolves_wood():
    from aoe2coach.parser import parse_rec

    rec = parse_rec(REC_PATH, RELIC_PROFILE_ID)
    res = gaia.resource_points(rec.gaia_objects)
    by_objid = gaia.by_object_id(rec.gaia_objects)
    evs = econ.gather_focus_events(rec.ops, player=rec.me["number"], gaia_by_objid=by_objid, resource_points=res)
    assert len(evs) >= 40
    from collections import Counter

    c = Counter(e["resource"] for e in evs)
    # The OLD model resolved ~0 wood (vils gather-pointed to a lumber camp, never re-clicked on trees).
    # The reworked model MUST resolve substantial wood.
    assert c["wood"] >= 30, f"wood gather points should dominate, got {c}"


@requires_rec
def test_real_rec_feudal_split_has_meaningful_wood():
    """The reworked split must NOT be ~100% food in feudal — wood (and usually gold) are present."""
    from aoe2coach.parser import parse_rec
    from aoe2coach.reconstruct import reconstruct

    rec = parse_rec(REC_PATH, RELIC_PROFILE_ID)
    recon = reconstruct(rec).to_dict()
    out = econ.estimate_economy(rec.ops, player=rec.me["number"], gaia_list=rec.gaia_objects, recon=recon)
    feud = out["worker_split_at_ages"]["feudal"]
    assert feud is not None
    assert feud["shares"].get("wood", 0) >= 0.1, f"feudal wood share too low: {feud['shares']}"
    assert feud["shares"].get("food", 1.0) <= 0.9, f"feudal still ~all food: {feud['shares']}"


@requires_rec
def test_real_rec_collected_estimate_in_band_or_suppressed():
    """THE GATE (spec): collected total within +-10% of 63,808 OR the suppression path fired.
    Either outcome is a pass — suppression is spec-sanctioned success, not failure."""
    from aoe2coach.parser import parse_rec
    from aoe2coach.reconstruct import reconstruct

    rec = parse_rec(REC_PATH, RELIC_PROFILE_ID)
    recon = reconstruct(rec).to_dict()
    out = econ.estimate_economy(rec.ops, player=rec.me["number"], gaia_list=rec.gaia_objects, recon=recon)
    collected = out["collected"]
    truth_total = 63808
    if collected is None:
        assert out["qualitative"] is not None  # suppressed -> honest fallback fired. PASS.
    else:
        total = sum(b["value"] for b in collected.values() if b is not None)
        assert abs(total - truth_total) / truth_total <= 0.10


@requires_rec2
def test_real_rec2_runs_and_serializes():
    import json

    from aoe2coach.parser import parse_rec
    from aoe2coach.reconstruct import reconstruct

    rec = parse_rec(REC2_PATH, RELIC_PROFILE_ID)
    recon = reconstruct(rec).to_dict()
    out = econ.estimate_economy(rec.ops, player=rec.me["number"], gaia_list=rec.gaia_objects, recon=recon)
    json.dumps(out)
    assert out["estimate"] is True
    # game2 feudal also shows meaningful wood (not ~100% food).
    feud = out["worker_split_at_ages"]["feudal"]
    if feud is not None:
        assert feud["shares"].get("wood", 0) >= 0.1


# ------------------------------------------------------------------- validate_collected (measure)
def test_validate_collected_suppressed():
    assert econ.validate_collected(None, {"food": 1, "wood": 1, "gold": 1, "stone": 1}) == {"suppressed": True}


# ----------------------------------------------------------------------------- fishing -> food (#1)
def test_fishing_food_workers_counts_ships_and_traps():
    ops = [
        (10_000, Action.DE_QUEUE, {"player_id": 1, "unit_id": 13, "amount": 3}),  # 3 fishing ships
        (20_000, Action.BUILD, {"player_id": 1, "building_id": 199}),  # fish trap
        (30_000, Action.BUILD, {"player_id": 1, "building_id": 199}),  # fish trap
        (40_000, Action.DE_QUEUE, {"player_id": 2, "unit_id": 13, "amount": 5}),  # opp -> ignored
    ]
    assert econ.fishing_food_workers(ops, player=1) == 5  # 3 ships + 2 traps
    # time-windowed: only the ships at 10s
    assert econ.fishing_food_workers(ops, player=1, at_s=15) == 3


def test_fishing_food_workers_zero_on_land_map():
    # no Dock units / fish traps -> graceful 0 (land game).
    ops = [(10_000, Action.DE_QUEUE, {"player_id": 1, "unit_id": 83, "amount": 4})]
    assert econ.fishing_food_workers(ops, player=1) == 0


def test_worker_split_folds_fishing_into_food():
    focus = [{"t_s": 30, "resource": "wood"}, {"t_s": 60, "resource": "gold"}]
    pop_times = [25 * i for i in range(1, 8)]  # 25..175
    ages = {"feudal_arrival_s": 200, "castle_arrival_s": None, "imperial_arrival_s": None}
    fishing_ops = [
        (50_000, Action.DE_QUEUE, {"player_id": 1, "unit_id": 13, "amount": 4}),  # 4 fishing ships
        (80_000, Action.BUILD, {"player_id": 1, "building_id": 199}),  # 1 fish trap
    ]
    no_fish = econ.worker_split_at_ages(focus, pop_times, starting=3, ages=ages)
    with_fish = econ.worker_split_at_ages(focus, pop_times, starting=3, ages=ages, fishing_ops=fishing_ops, player=1)
    assert with_fish["feudal"]["fishing_workers"] == 5
    # fishing adds to food workers present, raising the food share
    assert with_fish["feudal"]["workers_present"] == no_fish["feudal"]["villagers_present"] + 5
    assert with_fish["feudal"]["shares"]["food"] > no_fish["feudal"]["shares"]["food"]


# ----------------------------------------------------------------------------- floating heuristic (#3)
def test_floating_signal_flags_intent_over_spend():
    # workers piled on wood (intent .6) but only .2 of spend is wood -> wood floats.
    worker_share = {"wood": 0.6, "food": 0.3, "gold": 0.1, "stone": 0.0}
    spend_share = {"wood": 0.2, "food": 0.5, "gold": 0.3, "stone": 0.0}
    out = econ.floating_signal(worker_share, spend_share)
    flags = {f["resource"] for f in out["flags"]}
    assert "wood" in flags  # excess 0.4 > FLOAT_MARGIN
    assert "food" not in flags  # spending exceeds intent -> not floating
    assert out["estimate"] is True
    # never a bank total — only the two shares + gap
    wood = next(f for f in out["flags"] if f["resource"] == "wood")
    assert set(wood) == {"resource", "worker_share", "spend_share", "excess"}


def test_floating_signal_empty_on_no_signal():
    assert econ.floating_signal({}, {"wood": 1.0}) == {}
    assert econ.floating_signal({"wood": 1.0}, {}) == {}


def test_floating_signal_balanced_no_flags():
    ws = {"wood": 0.3, "food": 0.4, "gold": 0.3, "stone": 0.0}
    ss = {"wood": 0.3, "food": 0.4, "gold": 0.3, "stone": 0.0}
    out = econ.floating_signal(ws, ss)
    assert out["flags"] == []


def test_mid_game_worker_share_normalizes():
    focus = [{"t_s": t, "resource": ("wood" if t % 2 else "food")} for t in range(300, 2000, 60)]
    pop_times = [25 * i for i in range(1, 60)]
    share = econ.mid_game_worker_share(focus, pop_times, starting=3, duration_s=2400)
    assert share  # non-empty
    assert abs(sum(share.values()) - 1.0) < 1e-6


# ----------------------------------------------------------------------- two-block economy.json (#4)
def test_estimate_economy_two_blocks_distinct_units():
    recon = {
        "techs": {"eco": [{"name": "Double-Bit Axe", "t_s": 600}]},
        "ages": {"feudal_arrival_s": 200, "castle_arrival_s": 800, "imperial_arrival_s": 2000},
        "meta": {"duration_s": 3000, "my_civ": "Britons"},
    }
    ops = _assign_ops() + [
        (i * 25_000, Action.DE_QUEUE, {"player_id": 1, "unit_id": 83, "amount": 1}) for i in range(20)
    ]
    # add some spending: a barracks + archers + a tech
    ops += [
        (300_000, Action.BUILD, {"player_id": 1, "building_id": 12}),
        (400_000, Action.DE_QUEUE, {"player_id": 1, "unit_id": 4, "amount": 6}),
        (500_000, Action.RESEARCH, {"player_id": 1, "technology_id": 22}),
    ]
    out = econ.estimate_economy(ops, player=1, gaia_list=_gaia_objs(), recon=recon)
    # TWO clearly-named, never-conflated blocks.
    wa = out["worker_allocation"]
    rb = out["resource_balance"]
    assert wa["unit"] == "workers"
    assert rb["unit"] == "resource_amounts"
    assert "per_age" in wa and "mid_game_share" in wa
    assert "spent_by_resource" in rb and "floating" in rb
    # spending is near-exact (non-zero given the commands)
    assert sum(rb["spent_by_resource"].values()) > 0
    # NO fabricated gathered/bank totals
    assert rb["collected"] is None
    assert out["collected"] is None
    assert rb["relic_gold"] == "unavailable"
    import json

    json.dumps(out)  # serializable


@requires_rec
def test_real_rec_two_blocks_wood_present_no_fabricated_totals():
    """game1: WOOD must be a top worker allocation AND a top spend (not ~0); no fabricated bank
    totals; relic gold unavailable."""
    from aoe2coach.parser import parse_rec
    from aoe2coach.reconstruct import reconstruct

    rec = parse_rec(REC_PATH, RELIC_PROFILE_ID)
    recon = reconstruct(rec).to_dict()
    out = econ.estimate_economy(rec.ops, player=rec.me["number"], gaia_list=rec.gaia_objects, recon=recon)
    wa, rb = out["worker_allocation"], out["resource_balance"]
    # WOOD is a meaningful worker allocation (the old bug read ~all food).
    assert wa["mid_game_share"].get("wood", 0) >= 0.15, wa["mid_game_share"]
    # WOOD is a top spend resource (lots of farms/buildings/military).
    spent = rb["spent_by_resource"]
    assert spent["wood"] > 0
    assert spent["wood"] >= max(spent.values()) * 0.5, spent  # wood is among the largest spends
    # no fabricated gathered/bank totals anywhere
    assert rb["collected"] is None and out["collected"] is None
    assert rb["relic_gold"] == "unavailable"


@requires_rec
def test_real_rec_imperial_is_farm_food_heavy_not_all_wood():
    """The late-game-economy-attribution fix: game1 imperial WAS food~.18/wood~.68 (villagers
    gather-pointed to wood then pulled onto farms, never re-signalled). The farm anchor must drive
    food UP (farms = farmers) and wood DOWN to a realistic level."""
    from aoe2coach.parser import parse_rec
    from aoe2coach.reconstruct import reconstruct

    rec = parse_rec(REC_PATH, RELIC_PROFILE_ID)
    recon = reconstruct(rec).to_dict()
    out = econ.estimate_economy(rec.ops, player=rec.me["number"], gaia_list=rec.gaia_objects, recon=recon)
    imp = out["worker_split_at_ages"]["imperial"]
    assert imp is not None
    # food rises sharply once farms anchor it (was the absurd ~0.18; now a realistic ~0.32+).
    assert imp["shares"]["food"] >= 0.3, imp["shares"]
    # wood is no longer the absurd 0.68 — it has dropped to a realistic level.
    assert imp["shares"]["wood"] < 0.6, imp["shares"]
    assert imp["shares"]["wood"] < 0.675, imp["shares"]  # strictly below the buggy value
    # food allocation is anchored at >= the active-farm count by imperial (each farm = a farmer).
    ft = econ.active_farm_times(rec.ops, rec.me["number"])
    fbi = econ.farms_by(ft, recon["ages"]["imperial_arrival_s"])
    assert imp["alloc"]["food"] >= fbi, (imp["alloc"], fbi)


@requires_rec
def test_real_rec_floating_wood_no_longer_wildly_overflagged():
    """With the farm-anchored worker share feeding the floating signal, wood's mid-game worker share
    drops, so floating-wood must no longer read the absurd ~0.86 excess."""
    from aoe2coach.parser import parse_rec
    from aoe2coach.reconstruct import reconstruct

    rec = parse_rec(REC_PATH, RELIC_PROFILE_ID)
    recon = reconstruct(rec).to_dict()
    out = econ.estimate_economy(rec.ops, player=rec.me["number"], gaia_list=rec.gaia_objects, recon=recon)
    flags = {f["resource"]: f for f in out["resource_balance"]["floating"].get("flags", [])}
    # mid-game wood worker share is no longer ~0.86 (was wildly over-flagged before the fix).
    assert out["worker_allocation"]["mid_game_share"].get("wood", 0) < 0.6
    if "wood" in flags:
        assert flags["wood"]["excess"] < 0.4, flags["wood"]
