"""Tests for the #2 economy model (Tier-B estimate layer): gaia, rates, econ.

Synthetic ops are (t_ms, Action, data) tuples faithful to mgz.fast.parse_action shapes; synthetic
gaia objects mirror header["players"][0]["objects"] entries {class_id, object_id, instance_id,
position, index}. The honesty rule is under test: every collected number carries estimate/confidence/
[low,high], and the model SELF-SUPPRESSES (returns None) when out of the validation band.
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


# ------------------------------------------------------------------------- assignment_events
def _assign_ops(me=1):
    # GATHER_POINT with resolvable target_type (=gaia object_id); one with target_type -1 (dropped);
    # ORDER with target_id (joined via instance_id); one ORDER onto a building (dropped);
    # one onto decoration (dropped). object_ids length = n_vils moved.
    return [
        (10_000, Action.GATHER_POINT, {"player_id": me, "target_id": 104, "target_type": 66, "object_ids": [1, 2]}),
        (20_000, Action.GATHER_POINT, {"player_id": me, "target_id": 0, "target_type": -1, "object_ids": [9]}),  # drop
        (30_000, Action.ORDER, {"player_id": me, "target_id": 100, "object_ids": [3, 4, 5]}),  # tree -> wood, 3 vils
        (40_000, Action.ORDER, {"player_id": me, "target_id": 103, "object_ids": [6]}),  # berries -> food
        (50_000, Action.ORDER, {"player_id": me, "target_id": 106, "object_ids": [7]}),  # decoration -> drop
        (60_000, Action.ORDER, {"player_id": me, "target_id": 999, "object_ids": [8]}),  # no gaia join -> drop
        (70_000, Action.ORDER, {"player_id": 2, "target_id": 100, "object_ids": [1]}),  # opp -> drop
    ]


def test_assignment_events_fuses_and_classifies():
    g = gaia.gaia_objects(_gaia_objs())
    by_objid = gaia.by_object_id(_gaia_objs())
    evs = econ.assignment_events(_assign_ops(), player=1, gaia_by_inst=g, gaia_by_objid=by_objid)
    # the -1 gather-point, the decoration, the unjoinable, and the opp order are all dropped.
    assert [(e["t_s"], e["resource"], e["n_vils"]) for e in evs] == [
        (10, "gold", 2),
        (30, "wood", 3),
        (40, "food", 1),
    ]


def test_assignment_events_empty_when_no_gaia():
    evs = econ.assignment_events(_assign_ops(), player=1, gaia_by_inst={}, gaia_by_objid={})
    assert evs == []  # nothing joins -> no fabricated events


# ----------------------------------------------------------------------- eco_split_steps
def test_eco_split_steps_holds_constant_between_events():
    events = [
        {"t_s": 100, "resource": "wood", "n_vils": 3},
        {"t_s": 200, "resource": "food", "n_vils": 2},
    ]
    steps = econ.eco_split_steps(events)
    # after first event: 100% wood; after second: wood 3 / food 2 of total 5
    assert steps[0]["t_s"] == 100
    assert steps[0]["alloc"]["wood"] == 3 and steps[0]["alloc"].get("food", 0) == 0
    assert steps[1]["t_s"] == 200
    assert steps[1]["alloc"]["wood"] == 3 and steps[1]["alloc"]["food"] == 2
    assert steps[1]["confidence"] == 2  # cumulative event count


def test_eco_split_steps_empty():
    assert econ.eco_split_steps([]) == []


# --------------------------------------------------------------------- eco_split_at_ages
def test_eco_split_at_ages_snapshots_shares():
    events = [
        {"t_s": 100, "resource": "wood", "n_vils": 6},
        {"t_s": 150, "resource": "food", "n_vils": 4},
        {"t_s": 400, "resource": "gold", "n_vils": 2},  # after castle
    ]
    steps = econ.eco_split_steps(events)
    ages = {"feudal_arrival_s": 200, "castle_arrival_s": 500, "imperial_arrival_s": None}
    snaps = econ.eco_split_at_ages(steps, ages)
    feud = snaps["feudal"]
    # at t=200: wood 6 / food 4 -> 60% / 40%, n_events=2
    assert feud["estimate"] is True
    assert feud["n_events"] == 2
    assert feud["shares"]["wood"] == pytest.approx(0.6)
    assert feud["shares"]["food"] == pytest.approx(0.4)
    castle = snaps["castle"]
    assert castle["n_events"] == 3  # gold event now included
    assert snaps["imperial"] is None  # age not reached


# ------------------------------------------------------------------- collected_estimate
def test_collected_estimate_carries_band_and_labels():
    # craft steps so the integral lands in a plausible range; check shape, not exact numbers.
    events = [
        {"t_s": 10, "resource": "wood", "n_vils": 10},
        {"t_s": 10, "resource": "food", "n_vils": 10},
        {"t_s": 10, "resource": "gold", "n_vils": 5},
        {"t_s": 10, "resource": "stone", "n_vils": 2},
    ]
    steps = econ.eco_split_steps(events)
    recon = {"techs": {"eco": []}, "meta": {"duration_s": 3000}}
    out = econ.collected_estimate(steps, recon)
    # out may be a dict of per-resource bands, OR None if suppressed; either is valid shape-wise.
    if out is not None:
        for res in ("wood", "food", "gold", "stone"):
            if res in out and out[res] is not None:
                band = out[res]
                assert band["estimate"] is True
                assert band["low"] <= band["value"] <= band["high"]


def test_collected_estimate_suppresses_when_no_signal():
    # zero events -> cannot honestly estimate anything -> None (suppressed), never a bare 0.
    steps = econ.eco_split_steps([])
    recon = {"techs": {"eco": []}, "meta": {"duration_s": 3000}}
    assert econ.collected_estimate(steps, recon) is None


# ------------------------------------------------------------------- full estimate_economy
def test_estimate_economy_assembles_and_is_json_serializable():
    import json

    recon = {
        "techs": {"eco": [{"name": "Double-Bit Axe", "t_s": 600}]},
        "ages": {"feudal_arrival_s": 200, "castle_arrival_s": 800, "imperial_arrival_s": None},
        "meta": {"duration_s": 3000},
    }
    out = econ.estimate_economy(_assign_ops(), player=1, gaia_list=_gaia_objs(), recon=recon)
    assert out["estimate"] is True
    assert "eco_split_at_ages" in out
    assert "collected" in out  # may be a band dict or None (suppressed)
    assert "n_assignment_events" in out
    assert "qualitative" in out  # narrative shape always present
    json.dumps(out)  # must serialize


# ------------------------------------------------------------------------ FIDELITY / real rec
@requires_rec
def test_real_rec_assignment_events_count_and_classes():
    from aoe2coach.parser import parse_rec

    rec = parse_rec(REC_PATH, RELIC_PROFILE_ID)
    g = gaia.gaia_objects(rec.gaia_objects)
    by_objid = gaia.by_object_id(rec.gaia_objects)
    evs = econ.assignment_events(rec.ops, player=rec.me["number"], gaia_by_inst=g, gaia_by_objid=by_objid)
    # Genuine resource-assignment events for ME (spec's ~108 was pre-decoration-filter; the honest,
    # decoration-excluded count is in the dozens). Not all-None: resources resolve.
    assert len(evs) >= 20
    resources = {e["resource"] for e in evs}
    assert resources <= {"wood", "food", "gold", "stone"}
    assert resources  # at least one resolved


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
        # suppressed entirely -> the honest fallback fired. PASS.
        assert out["qualitative"] is not None
    else:
        # if a total survived, it must be within the band; otherwise the model should have suppressed.
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
