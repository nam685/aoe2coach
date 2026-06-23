"""Tests for the #1 reconstruction core: const maps, timeline extensions, spatial,
population, combat/engagements, efficiency, and the reconstruct() assembler.

Synthetic ops are (t_ms, Action, data) tuples faithful to mgz.fast.parse_action shapes.
"""

import json
import os
import struct

import pytest
from mgz.fast import Action, parse_action

from aoe2coach import const

_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "aoc_reference_technologies_100.json")


def _authoritative_techs():
    """The technologies map from aoc-reference-data dataset 100 (DE dat tech ids -> name)."""
    with open(_FIXTURE) as fh:
        return {int(k): v for k, v in json.load(fh)["technologies"].items()}


REC_PATH = "/home/namle685/projects/aoe2coach-analysis/game.aoe2record"
REC2_PATH = "/home/namle685/projects/aoe2coach-analysis/game2.aoe2record"
RELIC_PROFILE_ID = 14697894

requires_rec = pytest.mark.skipif(not os.path.exists(REC_PATH), reason="calibration rec not present")
requires_rec2 = pytest.mark.skipif(not os.path.exists(REC2_PATH), reason="second rec not present")


# --------------------------------------------------------------------------- const
def test_military_and_university_tech_maps():
    assert const.MILITARY_TECHS[199] == "Fletching"
    assert const.MILITARY_TECHS[67] == "Forging"
    assert const.MILITARY_TECHS[435] == "Bloodlines"
    assert const.UNIVERSITY_TECHS[93] == "Ballistics"
    assert const.tech_name(199) == "Fletching"
    assert const.tech_name(93) == "Ballistics"
    assert const.tech_name(22) == "Loom"  # falls through to ECO_TECHS
    assert const.tech_name(999999) == "#999999"


def test_every_tech_id_matches_authoritative_dataset():
    """ZERO-mismatch audit: every id in MILITARY/UNIVERSITY/ECO must equal the aoc-reference name."""
    auth = _authoritative_techs()
    mismatches = []
    for label, mapping in (
        ("MILITARY", const.MILITARY_TECHS),
        ("UNIVERSITY", const.UNIVERSITY_TECHS),
        ("ECO", const.ECO_TECHS),
    ):
        for tid, name in mapping.items():
            if auth.get(tid) != name:
                mismatches.append(f"{label} {tid}: ours={name!r} authoritative={auth.get(tid)!r}")
    assert mismatches == []


def test_known_correct_tech_pairs():
    """Hardcoded ground-truth ids that the old hand-built maps got wrong."""
    assert const.tech_name(254) == "Light Cavalry"
    assert const.tech_name(265) == "Paladin"
    assert const.tech_name(384) == "Eagle Warrior"
    assert const.tech_name(434) == "Elite Eagle Warrior"
    assert const.tech_name(47) == "Chemistry"
    assert const.tech_name(50) == "Masonry"
    assert const.tech_name(100) == "Crossbowman"
    assert const.tech_name(237) == "Arbalester"
    assert const.tech_name(199) == "Fletching"
    assert const.tech_name(209) == "Cavalier"
    assert const.tech_name(408) == "Spies/Treason"
    # ids the old map mislabeled as Eagle Warrior / Masonry must NOT resolve that way anymore
    assert const.MILITARY_TECHS[254] == "Light Cavalry"
    assert const.UNIVERSITY_TECHS[47] == "Chemistry"


def test_siege_unit_ids():
    assert 280 in const.SIEGE_UNIT_IDS  # Mangonel
    assert (42 in const.SIEGE_UNIT_IDS) or (331 in const.SIEGE_UNIT_IDS)  # Trebuchet
    assert 83 not in const.SIEGE_UNIT_IDS  # Villager is not siege


def test_pop_providing_buildings():
    assert const.POP_PER_BUILDING["House"] == 5
    assert const.POP_PER_BUILDING["Town Center"] == 5
    assert const.POP_PER_BUILDING["Castle"] == 20
    assert const.POP_CAP == 200


# ----------------------------------------------------------------------- timeline
def _ops_timeline():
    return [
        (585_000, Action.RESEARCH, {"player_id": 1, "technology_id": 101}),  # ME feudal click
        (600_000, Action.RESEARCH, {"player_id": 1, "technology_id": 199}),  # ME Fletching (mil)
        (700_000, Action.RESEARCH, {"player_id": 1, "technology_id": 93}),  # ME Ballistics (univ)
        (610_000, Action.RESEARCH, {"player_id": 1, "technology_id": 22}),  # ME Loom (eco)
        (300_000, Action.BUILD, {"player_id": 1, "building_id": 87, "x": 30.0, "y": 30.0}),  # Archery Range (mil bldg)
        (610_000, Action.DE_QUEUE, {"player_id": 1, "unit_id": 4, "amount": 3}),  # Archer x3
        (1_800_000, Action.DE_QUEUE, {"player_id": 1, "unit_id": 280, "amount": 1}),  # Mangonel (siege)
        (2_400_000, Action.DE_QUEUE, {"player_id": 1, "unit_id": 331, "amount": 1}),  # Trebuchet
        (605_000, Action.RESEARCH, {"player_id": 2, "technology_id": 67}),  # OPP Forging
    ]


def test_timeline_collects_mil_and_university_techs():
    from aoe2coach.timeline import build_timeline

    tl = build_timeline(_ops_timeline(), me_number=1)
    names = [m["name"] for m in tl["mil_techs"]]
    assert names == ["Fletching", "Ballistics"]  # eco tech (Loom) excluded; first-occurrence order


def test_timeline_units_carry_unit_id():
    from aoe2coach.timeline import build_timeline

    tl = build_timeline(_ops_timeline(), me_number=1)
    archer = next(u for u in tl["units"] if u["name"] == "Archer")
    assert archer["unit_id"] == 4 and archer["amount"] == 3


def test_milestones_first_military_building_siege_treb():
    from aoe2coach.metrics import compute_metrics
    from aoe2coach.timeline import build_timeline

    tl = build_timeline(_ops_timeline(), me_number=1)
    m = compute_metrics(tl, duration_ms=2_700_000)
    assert m["first_unit_s"]["Archer"] == 610
    assert m["first_siege_s"] == 1800  # Mangonel
    assert m["first_treb_s"] == 2400
    assert m["first_military_building"] == {"name": "Archery Range", "t_s": 300}


def test_tech_name_full_resolution_and_split():
    # military vs university vs eco classification is disjoint and complete on these ids.
    assert const.tech_name(67) == "Forging"
    assert const.tech_name(435) == "Bloodlines"
    assert const.tech_name(93) == "Ballistics"
    assert const.tech_name(101) == "Feudal Age"
    # Husbandry (39) is eco-only so the eco/military split stays unambiguous.
    assert 39 not in const.MILITARY_TECHS
    assert 39 in const.ECO_TECHS


def test_timeline_separates_military_and_university_techs():
    from aoe2coach.timeline import build_timeline

    tl = build_timeline(_ops_timeline(), me_number=1)
    assert [t["name"] for t in tl["military_techs"]] == ["Fletching"]
    assert [t["name"] for t in tl["university_techs"]] == ["Ballistics"]
    assert [t["name"] for t in tl["eco_techs"]] == ["Loom"]
    # builds now carry coords + id for spatial.
    b = tl["builds"][0]
    assert b["x"] == 30.0 and b["y"] == 30.0 and b["building_id"] == 87


# ------------------------------------------------------------------------- spatial
def _ops_spatial():
    # ME (player 1) base in the south (~y 90); a forward barracks near the opponent (north, y 20).
    return [
        (1000, Action.BUILD, {"player_id": 1, "building_id": 109, "x": 46.0, "y": 92.0}),  # TC
        (2000, Action.BUILD, {"player_id": 1, "building_id": 70, "x": 44.0, "y": 90.0}),  # House
        (3000, Action.BUILD, {"player_id": 1, "building_id": 68, "x": 48.0, "y": 94.0}),  # Mill
        (4000, Action.BUILD, {"player_id": 1, "building_id": 12, "x": 46.0, "y": 22.0}),  # FORWARD Barracks
        (5000, Action.WALL, {"player_id": 1, "building_id": 72, "x": 34, "y": 69, "x_end": 22, "y_end": 67}),
        (
            6000,
            Action.BUILD,
            {"player_id": 1, "building_id": 999999, "x": None, "y": None},
        ),  # missing coords -> skipped
        (1500, Action.BUILD, {"player_id": 2, "building_id": 109, "x": 46.0, "y": 18.0}),  # OPP TC (north)
    ]


def test_spatial_buildings_and_skips_missing_coords():
    from aoe2coach import spatial

    blds = spatial.buildings(_ops_spatial(), player=1)
    names = [b["name"] for b in blds]
    assert names == ["Town Center", "House", "Mill", "Barracks"]  # the None-coord build is dropped
    assert blds[0]["t_s"] == 1


def test_spatial_base_centroid_excludes_military():
    from aoe2coach import spatial

    c = spatial.base_centroid(_ops_spatial(), player=1)
    # mean of TC/House/Mill (military Barracks excluded): x=(46+44+48)/3=46, y=(92+90+94)/3=92
    assert round(c["x"], 1) == 46.0 and round(c["y"], 1) == 92.0


def test_spatial_forward_buildings():
    from aoe2coach import spatial

    fwd = spatial.forward_buildings(_ops_spatial(), player=1)
    assert len(fwd) == 1 and fwd[0]["name"] == "Barracks"
    assert fwd[0]["dist"] > 30.0  # the barracks is ~70 tiles from the south base centroid


def test_spatial_walls():
    from aoe2coach import spatial

    w = spatial.walls(_ops_spatial(), player=1)
    assert len(w) == 1
    assert (w[0]["x"], w[0]["y"], w[0]["x_end"], w[0]["y_end"]) == (34.0, 69.0, 22.0, 67.0)


def test_spatial_opp_centroid_and_start_position():
    from aoe2coach import spatial

    opp_c = spatial.base_centroid(_ops_spatial(), player=2)
    assert round(opp_c["y"], 1) == 18.0
    sp = spatial.start_position({"position": {"x": 46.0, "y": 18.0}})
    assert sp == {"x": 46.0, "y": 18.0}
    assert spatial.start_position({}) is None  # graceful on missing


def test_spatial_eco_exposure_front_safe():
    from aoe2coach import spatial

    my_c = {"x": 46.0, "y": 92.0}
    opp_c = {"x": 46.0, "y": 18.0}  # opponent is north
    blds = [
        {"name": "House", "x": 46.0, "y": 90.0},  # near my base -> safe
        {"name": "Mill", "x": 46.0, "y": 30.0},  # pushed toward opp -> front
        {"name": "Barracks", "x": 46.0, "y": 40.0},  # military -> not in eco_exposure
    ]
    out = spatial.eco_exposure(my_c, opp_c, blds)
    front_names = [b["name"] for b in out["front"]]
    safe_names = [b["name"] for b in out["safe"]]
    assert "Mill" in front_names and "House" in safe_names
    assert "Barracks" not in front_names + safe_names  # military excluded from eco exposure
    assert out["axis_len"] == 74.0


def test_spatial_eco_exposure_degrades_without_opp_centroid():
    from aoe2coach import spatial

    out = spatial.eco_exposure({"x": 1, "y": 1}, None, [{"name": "House", "x": 1, "y": 1}])
    assert out["front"] == [] and out["axis_len"] is None and len(out["safe"]) == 1


# ---------------------------------------------------------------------- population
def test_population_housed_ceiling_and_steps():
    from aoe2coach import population

    ops = [
        (1000, Action.BUILD, {"player_id": 1, "building_id": 70, "x": 1, "y": 1}),  # House +5
        (2000, Action.BUILD, {"player_id": 1, "building_id": 70, "x": 2, "y": 2}),  # House +5
        (3000, Action.BUILD, {"player_id": 1, "building_id": 82, "x": 3, "y": 3}),  # Castle +20
        (4000, Action.BUILD, {"player_id": 1, "building_id": 12, "x": 4, "y": 4}),  # Barracks +0 (no housing)
        (5000, Action.BUILD, {"player_id": 2, "building_id": 70, "x": 5, "y": 5}),  # opp house (ignored)
    ]
    assert population.housed_pop_ceiling(ops, player=1) == 30
    steps = population.pop_ceiling_steps(ops, player=1)
    assert [s["ceiling"] for s in steps] == [5, 10, 30]


def test_population_clamped_to_cap():
    from aoe2coach import const, population

    ops = [(i * 1000, Action.BUILD, {"player_id": 1, "building_id": 70, "x": 1, "y": 1}) for i in range(60)]
    # 60 houses * 5 = 300 -> clamped to POP_CAP (200)
    assert population.housed_pop_ceiling(ops, player=1) == const.POP_CAP


# ------------------------------------------------------------------------- combat
def test_combat_pin_zone_thirds():
    from aoe2coach import combat

    my_c = {"x": 0.0, "y": 0.0}
    opp_c = {"x": 0.0, "y": 90.0}
    assert combat.pin_zone(0, 10, my_c, opp_c) == combat.ZONE_OWN  # frac ~0.11
    assert combat.pin_zone(0, 45, my_c, opp_c) == combat.ZONE_CENTER  # frac 0.5
    assert combat.pin_zone(0, 80, my_c, opp_c) == combat.ZONE_OPP  # frac ~0.89
    # missing centroid -> neutral center, never raises
    assert combat.pin_zone(0, 80, None, opp_c) == combat.ZONE_CENTER


def test_combat_engagements_cluster_and_zone():
    from aoe2coach import combat

    my_c = {"x": 0.0, "y": 0.0}
    opp_c = {"x": 0.0, "y": 90.0}
    ops = [
        (100_000, Action.ATTACK_GROUND, {"player_id": 1, "x": 0.0, "y": 80.0}),  # opp_base
        (110_000, Action.PATROL, {"player_id": 1, "x": 0.0, "y": 82.0}),  # opp_base, within 30s
        (300_000, Action.DE_ATTACK_MOVE, {"player_id": 1, "x": 0.0, "y": 45.0}),  # center, new cluster
        (105_000, Action.MOVE, {"player_id": 1, "x": 0.0, "y": 80.0}),  # eco MOVE -> excluded by default
    ]
    eng = combat.engagements(ops, player=1, my_centroid=my_c, opp_centroid=opp_c)
    assert len(eng) == 2
    assert eng[0]["zone"] == combat.ZONE_OPP and eng[0]["n_commands"] == 2
    assert eng[1]["zone"] == combat.ZONE_CENTER
    assert all(e["zone"] in combat.ZONES for e in eng)


# --------------------------------------------------------------------- efficiency
def test_efficiency_tc_idle_gaps():
    from aoe2coach import efficiency

    # villagers queued at 0s, 25s, 90s -> gaps 25s (<=30 no idle), 65s (idle 35s).
    ops = [
        (0, Action.DE_QUEUE, {"player_id": 1, "unit_id": 83, "amount": 1}),
        (25_000, Action.DE_QUEUE, {"player_id": 1, "unit_id": 83, "amount": 1}),
        (90_000, Action.DE_QUEUE, {"player_id": 1, "unit_id": 83, "amount": 1}),
    ]
    idle = efficiency.tc_idle(ops, player=1)
    assert idle["villager_gaps_s"] == [25, 65]
    assert idle["longest_villager_gap_s"] == 65
    assert idle["tc_idle_s"] == 35  # only the 65s gap exceeds the 30s threshold, by 35s
    assert idle["precap_window_s"] == 90  # no explicit precap -> defaults to last queue time


def test_tc_idle_only_counts_before_precap_cutoff():
    """TC idle is only a mistake while you still want villagers. A big gap AFTER the pop-cap cutoff
    is intentional quiet and must NOT count; a gap straddling the cutoff is clipped at it."""
    from aoe2coach import efficiency

    # villagers at 0, 25, then a long quiet, then a queue at 300s (gap 275s).
    ops = [
        (0, Action.DE_QUEUE, {"player_id": 1, "unit_id": 83, "amount": 1}),
        (25_000, Action.DE_QUEUE, {"player_id": 1, "unit_id": 83, "amount": 1}),
        (300_000, Action.DE_QUEUE, {"player_id": 1, "unit_id": 83, "amount": 1}),
    ]
    # cutoff at 100s: the 25->300 gap is clipped to 25->100 = 75s, idle = 75-30 = 45.
    idle = efficiency.tc_idle(ops, player=1, precap_s=100)
    assert idle["precap_window_s"] == 100
    assert idle["tc_idle_s"] == 45
    # cutoff at 25s (the moment after the 2nd queue): the big late gap starts AT the cap -> 0 idle.
    idle2 = efficiency.tc_idle(ops, player=1, precap_s=25)
    assert idle2["tc_idle_s"] == 0
    # Longest gap is PRE-CAP only now (Nam): the 25->300 gap is clipped to the 100s cutoff -> 75s,
    # not the post-cap 275s. Its window ends at the cutoff.
    assert idle["longest_villager_gap_s"] == 75
    assert idle["longest_villager_gap_window_s"] == [25, 100]


def test_precap_cutoff_reaches_200_pop():
    from aoe2coach import efficiency, production

    # 50 villagers popped + non-vil army units pushing est pop to 200.
    sim = production.VillagerSim(pop_times_s=[float(i * 5) for i in range(1, 60)], starting=3)
    produced_units = [{"unit_id": 4, "amount": 150, "t_s": 200}]  # 150 archers at 200s
    cutoff = efficiency.precap_cutoff_s(sim, produced_units, duration_s=600)
    # est pop = villagers_present(t) + 150 (from t>=200); crosses 200 once ~47 vils have popped.
    # villagers_present(235) = 3 + 47 = 50, + 150 = 200 -> cutoff ~235. Well within the game.
    assert 200 <= cutoff <= 260


def test_precap_cutoff_falls_back_when_cap_never_reached():
    from aoe2coach import efficiency, production

    sim = production.VillagerSim(pop_times_s=[float(i * 25) for i in range(1, 11)], starting=3)
    # only ~13 pop ever -> cap never reached -> fall back to whole game (or last vil queue).
    cutoff = efficiency.precap_cutoff_s(sim, [], duration_s=900)
    assert cutoff >= 250  # last pop (250) or duration (900) — never raises, never 0


def test_efficiency_apm_split_classifies():
    from aoe2coach import efficiency

    ops = [
        (0, Action.DE_QUEUE, {"player_id": 1, "unit_id": 83, "amount": 1}),  # eco (vil)
        (0, Action.DE_QUEUE, {"player_id": 1, "unit_id": 4, "amount": 1}),  # military (archer)
        (0, Action.BUILD, {"player_id": 1, "building_id": 12, "x": 1, "y": 1}),  # military bldg
        (0, Action.BUILD, {"player_id": 1, "building_id": 68, "x": 1, "y": 1}),  # eco bldg (Mill)
        (0, Action.RESEARCH, {"player_id": 1, "technology_id": 22}),  # eco tech (Loom)
        (0, Action.RESEARCH, {"player_id": 1, "technology_id": 199}),  # military tech (Fletching)
        (0, Action.MOVE, {"player_id": 1, "x": 1, "y": 1}),  # military control
        (0, Action.GATHER_POINT, {"player_id": 1, "target_id": 1, "target_type": 5, "x": 1, "y": 1}),  # eco
        (0, Action.RESEARCH, {"player_id": 1, "technology_id": 101}),  # age click -> other
        (0, Action.DE_QUEUE, {"player_id": 2, "unit_id": 83, "amount": 1}),  # opp -> ignored
    ]
    s = efficiency.apm_split(ops, player=1, duration_ms=60_000)
    assert s["actions_eco"] == 4  # vil, Mill, Loom, gather_point
    assert s["actions_military"] == 4  # archer, barracks, Fletching, MOVE
    assert s["actions_other"] == 1  # age click
    assert s["actions_total"] == 9
    assert s["apm_total"] == 9  # 9 actions over 1 minute


# ------------------------------------------------------------------ FIDELITY (bytes -> parse_action)
# These build the DE "71094" wrapped action payload (player_id + length prefix) so synthetic op
# fixtures can't silently drift from the real mgz.fast.parse_action field shapes. If a future patch
# changes a layout, these fail FIRST (the WORK-vanished lesson from the spec).


def _wrap(payload: bytes, player_id: int = 1) -> bytes:
    """Wrap a 71094 payload so parse_action takes the DE path (len(data) == length + 3)."""
    return struct.pack("<bh", player_id, len(payload)) + payload


def test_fidelity_gather_point_carries_target_type():
    # GATHER_POINT 71094: '<h2xffii' (selected, x, y, target_id, target_type) + selected*I
    payload = struct.pack("<h2xffii", 1, 46.5, 19.5, 3762, 305) + struct.pack("<I", 961536)
    out = parse_action(Action.GATHER_POINT, _wrap(payload, player_id=2))
    assert out["player_id"] == 2
    assert out["target_id"] == 3762
    assert out["target_type"] == 305  # #2 economy joins this against gaia object_id
    assert out["object_ids"] == [961536]


def test_fidelity_order_carries_target_id_no_target_type():
    # ORDER 71094: '<I2fh' (target_id, x, y, selected) + 4 skip + selected*I
    payload = struct.pack("<I2fh", 3762, 46.5, 19.5, 1) + struct.pack("<4x") + struct.pack("<I", 247922687)
    out = parse_action(Action.ORDER, _wrap(payload, player_id=2))
    assert out["target_id"] == 3762
    assert "target_type" not in out  # ORDER lacks target_type; #2 must join target_id->gaia
    assert out["object_ids"] == [247922687]


def test_fidelity_build_carries_coords():
    # BUILD 71094: '<h2xffI8xhbb' (selected, x, y, building_id, unk2, unk3, unk4) + selected*I
    payload = struct.pack("<h2xffI8xhbb", 2, 39.0, 93.0, 70, 0, 0, 0) + struct.pack("<2I", 3774, 3772)
    out = parse_action(Action.BUILD, _wrap(payload))
    assert out["building_id"] == 70 and out["x"] == 39.0 and out["y"] == 93.0


def test_fidelity_de_queue_unit_amount():
    # DE_QUEUE 71094: '<h4xhhh' (selected, building_type, unit_id, amount) + selected*I
    payload = struct.pack("<h4xhhh", 1, 0, 83, 3) + struct.pack("<I", 1)
    out = parse_action(Action.DE_QUEUE, _wrap(payload))
    assert out["unit_id"] == 83 and out["amount"] == 3


@requires_rec
def test_fidelity_gaia_objects_shape_on_real_header():
    # The starting GAIA object table must carry the fields #2 joins on. Guard against patch drift.
    import mgz.fast.header

    with open(REC_PATH, "rb") as f:
        h = mgz.fast.header.parse(f)
    gaia = h["players"][0]
    assert gaia["number"] == 0
    obj = gaia["objects"][0]
    assert {"class_id", "object_id", "instance_id", "position"} <= set(obj.keys())


# ---------------------------------------------------------------- parser exposure (real rec)
@requires_rec
def test_parser_surfaces_gaia_and_start_positions():
    from aoe2coach.parser import parse_rec

    rec = parse_rec(REC_PATH, RELIC_PROFILE_ID)
    assert len(rec.gaia_objects) > 1000  # ~4560 starting map objects
    assert rec.map_dim == 120
    # both human players have a start position
    assert rec.me["number"] in rec.start_positions
    assert rec.opponent["number"] in rec.start_positions
    me_pos = rec.start_positions[rec.me["number"]]
    assert "x" in me_pos and "y" in me_pos


# ------------------------------------------------------------------- reconstruct() (real recs)
@requires_rec
def test_reconstruct_calibration_golden():
    from aoe2coach.parser import parse_rec
    from aoe2coach.reconstruct import reconstruct

    rec = parse_rec(REC_PATH, RELIC_PROFILE_ID)
    r = reconstruct(rec).to_dict()

    # Age arrivals within +-3s of known CaptureAge values (9:34 / 20:55 / 40:23).
    assert abs(r["ages"]["feudal_arrival_s"] - (9 * 60 + 34)) <= 3
    assert abs(r["ages"]["castle_arrival_s"] - (20 * 60 + 55)) <= 3
    assert abs(r["ages"]["imperial_arrival_s"] - (40 * 60 + 23)) <= 3
    # ages monotonic
    assert r["ages"]["feudal_arrival_s"] < r["ages"]["castle_arrival_s"] < r["ages"]["imperial_arrival_s"]

    # Building counts ~ 185 ME / 105 OPP (validation table; allow slack for missing-coord skips).
    assert 160 <= len(r["spatial"]["me"]["buildings"]) <= 210
    assert 90 <= len(r["spatial"]["opp"]["buildings"]) <= 130

    # villagers_produced >= 107 (live max alive); queued is an upper bound, labeled produced.
    assert r["counts"]["villagers_produced"] >= 107

    # centroids in-bounds (0..map_dim)
    mc = r["spatial"]["me"]["base_centroid"]
    assert 0 <= mc["x"] <= 120 and 0 <= mc["y"] <= 120
    oc = r["spatial"]["opp"]["base_centroid"]
    assert 0 <= oc["x"] <= 120 and 0 <= oc["y"] <= 120

    # consumer requirements present
    assert r["production"]["milestones"]["first_military_building"] is not None
    assert r["production"]["vils_at_feudal_click"] is not None
    assert r["spatial"]["opp"]["base_centroid"] is not None
    assert "eco_exposure" in r["spatial"]["me"]
    for e in r["combat"]["me"]["engagements"]:
        assert e["zone"] in ("own_base", "center", "opp_base")

    # real APM is a sane rate, not a tally; eco+military are a subset of total (rest uncategorized)
    eff = r["efficiency"]
    assert 0 < eff["apm_total"] < 400
    assert eff["apm_eco"] + eff["apm_military"] <= eff["apm_total"] + 1  # +1 for rounding slack

    # JSON-serializable
    import json

    json.dumps(r)


@requires_rec2
def test_reconstruct_second_rec_sane():
    from aoe2coach.parser import parse_rec
    from aoe2coach.reconstruct import reconstruct

    rec = parse_rec(REC2_PATH, RELIC_PROFILE_ID)
    r = reconstruct(rec).to_dict()
    import json

    json.dumps(r)  # must not crash / must serialize
    # any age that exists is in-bounds and ordering holds where present
    arrivals = [r["ages"][f"{a}_arrival_s"] for a in ("feudal", "castle", "imperial")]
    present = [a for a in arrivals if a is not None]
    assert present == sorted(present)
    if r["spatial"]["me"]["base_centroid"]:
        mc = r["spatial"]["me"]["base_centroid"]
        assert 0 <= mc["x"] <= 200 and 0 <= mc["y"] <= 200


def test_civ_names_de_ids_including_post_gurjaras():
    """Regression: DE civilization_id map must cover the newer civs (43+), not fall back to #id.
    Anchors verified against real recs + aoc-reference-data."""
    assert const.civ_name(8) == "Persians"
    assert const.civ_name(21) == "Incas"
    assert const.civ_name(31) == "Vietnamese"
    assert const.civ_name(36) == "Burgundians"
    assert const.civ_name(41) == "Bengalis"
    assert const.civ_name(43) == "Romans"
    assert const.civ_name(44) == "Armenians"
    assert const.civ_name(45) == "Georgians"
    assert const.civ_name(52) == "Jurchens"
    assert const.civ_name(999) == "#999"  # unknown still falls back
