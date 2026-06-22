"""Tests for sub-project #6: coaching knowledge base + deterministic mistake detectors.

Synthetic Reconstruction dicts (faithful to #1's Reconstruction.to_dict() shape) drive each
detector just over / just under threshold. Plus schema validation, the needs-#2 guard,
missing-input degradation, determinism, and the calibration-game golden test on the real recs.
"""

import copy
import json
import os

import pytest

from aoe2coach import mistakes
from aoe2coach.mistakes import detect, detectors
from aoe2coach.mistakes._schema import Detector, Rubric, SchemaError, Source, validate

REC_PATH = "/home/namle685/projects/aoe2coach-analysis/game.aoe2record"
REC2_PATH = "/home/namle685/projects/aoe2coach-analysis/game2.aoe2record"
RELIC_PROFILE_ID = 14697894

requires_rec = pytest.mark.skipif(not os.path.exists(REC_PATH), reason="calibration rec not present")
requires_rec2 = pytest.mark.skipif(not os.path.exists(REC2_PATH), reason="second rec not present")

EXPECTED_IDS = {
    "idle-tc",
    "long-vil-gap",
    "slow-feudal",
    "slow-castle",
    "slow-imperial",
    "late-loom-or-eco-up",
    "too-few-villagers",
    "villager-stall-late",
    "exposed-gold",
    "got-housed",
    "no-map-presence",
    "leaky-or-late-walls",
    "walled-too-early",
    "floating-resources",
    "over-collecting-one-res",
}
NEEDS_2 = {"floating-resources", "over-collecting-one-res"}


# --------------------------------------------------------------------- library + schema
def test_library_loads_all_entries_and_validates():
    lib = mistakes.load_library()
    assert set(lib.keys()) == EXPECTED_IDS
    # required Nam-requested detectors present and enabled
    assert "villager-stall-late" in lib and not lib["villager-stall-late"].disabled
    assert "exposed-gold" in lib and not lib["exposed-gold"].disabled


def test_needs_2_entries_are_disabled():
    lib = mistakes.load_library()
    for mid in NEEDS_2:
        assert lib[mid].confidence_tier == "needs-#2"
        assert lib[mid].disabled  # fn: disabled — reference-only


def test_index_refs_all_resolve():
    index = mistakes.load_index()
    refs = set((index.get("refs") or {}).keys())
    lib = mistakes.load_library()
    for r in lib.values():
        assert r.source.ref in refs


def test_every_entry_has_user_facing_study_link():
    lib = mistakes.load_library()
    for r in lib.values():
        assert r.source.study.get("url", "").startswith("http")
        assert r.source.study.get("title")


def test_load_one_returns_raw_dict():
    raw = mistakes.load_one("idle-tc")
    assert raw["id"] == "idle-tc"
    assert raw["detector"]["fn"] == "idle_tc"
    with pytest.raises(FileNotFoundError):
        mistakes.load_one("does-not-exist")


def test_schema_rejects_bad_tier():
    bad = Rubric(
        id="x",
        name="X",
        explanation="e",
        severity="high",
        confidence_tier="bogus",
        detector=Detector(fn="idle_tc", inputs=["efficiency.tc_idle_s"]),
        fix="f",
        source=Source(ref="r", study={"url": "http://x"}),
    )
    with pytest.raises(SchemaError):
        validate(bad)


def test_schema_rejects_unknown_input_path():
    bad = Rubric(
        id="x",
        name="X",
        explanation="e",
        severity="high",
        confidence_tier="exact",
        detector=Detector(fn="idle_tc", inputs=["efficiency.not_a_field"]),
        fix="f",
        source=Source(ref="r", study={"url": "http://x"}),
    )
    with pytest.raises(SchemaError):
        validate(bad)


def test_schema_needs_2_must_be_disabled():
    bad = Rubric(
        id="x",
        name="X",
        explanation="e",
        severity="low",
        confidence_tier="needs-#2",
        detector=Detector(fn="idle_tc", inputs=["efficiency.tc_idle_s"]),
        fix="f",
        source=Source(ref="r", study={"url": "http://x"}),
    )
    with pytest.raises(SchemaError):
        validate(bad)


# --------------------------------------------------------------- synthetic reconstruction
def _base_recon():
    """A clean reconstruction with NO mistakes — every detector should pass on this baseline."""
    return {
        "meta": {"map": "Arabia", "duration_s": 1800, "my_civ": "Franks", "result": "win"},
        "ages": {
            "feudal_arrival_s": 600,
            "castle_arrival_s": 1000,
            "imperial_arrival_s": 1600,
            "feudal_click_s": 540,
            "castle_click_s": 940,
            "imperial_click_s": 1540,
        },
        "techs": {
            "eco": [
                {"name": "Loom", "t_s": 100},
                {"name": "Double-Bit Axe", "t_s": 700},
                {"name": "Horse Collar", "t_s": 750},
                {"name": "Wheelbarrow", "t_s": 1100},
            ],
            "military": [],
            "university": [],
        },
        "production": {
            "produced_units": [
                {"name": "Villager", "unit_id": 83, "amount": 1, "t_s": 50},
                {"name": "Villager", "unit_id": 83, "amount": 1, "t_s": 1700},  # still making vils post-Imp
                {"name": "Knight", "unit_id": 38, "amount": 5, "t_s": 1200},
            ],
            "milestones": {},
            "vils_at_feudal_click": 22,
        },
        "counts": {"villagers_produced": 110, "army_produced": []},
        "spatial": {
            "me": {
                "base_centroid": {"x": 40, "y": 90},
                "buildings": [],
                "forward": [{"name": "Barracks", "x": 40, "y": 40, "dist": 50.0}],
                "walls": [
                    {"x": 30, "y": 70, "x_end": 25, "y_end": 70, "name": "Palisade Wall", "t_s": 700},
                    {"x": 25, "y": 70, "x_end": 20, "y_end": 70, "name": "Palisade Wall", "t_s": 710},
                    {"x": 20, "y": 70, "x_end": 15, "y_end": 70, "name": "Palisade Wall", "t_s": 720},
                ],
                "eco_exposure": {
                    "front": [],
                    "safe": [{"name": "Mining Camp", "x": 40, "y": 85}],
                    "axis_len": 74.0,
                },
            },
            "opp": {"base_centroid": {"x": 40, "y": 18}},
        },
        "population": {"me": {"housed_pop_ceiling": 120, "pop_ceiling_steps": []}},
        "combat": {"me": {"engagements": [{"zone": "center", "n_commands": 3}, {"zone": "opp_base", "n_commands": 2}]}},
        "efficiency": {
            "tc_idle_s": 10,
            "longest_villager_gap_s": 30,
            "villager_gaps_s": [25, 30],
            "apm_total": 80,
        },
    }


def test_clean_recon_flags_nothing():
    flagged = mistakes.detect_mistakes(_base_recon())
    assert flagged == [], [f.id for f in flagged]


# ---- per-detector trigger/no-trigger pairs (one pair each enabled detector) -------------
def test_idle_tc_trigger_and_not():
    r = _base_recon()
    # tolerance = 25 + 2.0*(1800/60=30) = 85. 86 trips, 85 doesn't.
    r["efficiency"]["tc_idle_s"] = 86
    out = detectors.idle_tc(r, mistakes.load_library()["idle-tc"].detector.params)
    assert out is not None and out.observed["tc_idle_s"] == 86 and out.observed["tolerance_s"] == 85
    r["efficiency"]["tc_idle_s"] = 85
    assert detectors.idle_tc(r, mistakes.load_library()["idle-tc"].detector.params) is None


def test_long_vil_gap_trigger_and_not():
    p = {"max_gap_s": 60}
    r = _base_recon()
    r["efficiency"]["longest_villager_gap_s"] = 61
    assert detectors.long_vil_gap(r, p) is not None
    r["efficiency"]["longest_villager_gap_s"] = 60
    assert detectors.long_vil_gap(r, p) is None


def test_slow_feudal_trigger_and_not():
    p = {"generic_max_s": 720}
    r = _base_recon()
    r["ages"]["feudal_arrival_s"] = 721
    out = detectors.slow_feudal(r, p)
    assert out is not None and out.observed["basis"] == "generic"
    r["ages"]["feudal_arrival_s"] = 720
    assert detectors.slow_feudal(r, p) is None


def test_slow_castle_build_relative_band():
    r = _base_recon()
    r["ages"]["castle_arrival_s"] = 1000
    r["_build_target"] = {"castle": {"max_s": 950}}
    out = detectors.slow_castle(r, {"generic_max_s": 9999})
    assert out is not None and out.observed["basis"] == "build" and out.observed["target_s"] == 950


def test_slow_imperial_trigger_and_not():
    p = {"generic_max_s": 2160}
    r = _base_recon()
    r["ages"]["imperial_arrival_s"] = 2161
    assert detectors.slow_imperial(r, p) is not None
    r["ages"]["imperial_arrival_s"] = 2160
    assert detectors.slow_imperial(r, p) is None


def test_late_or_missing_eco_up_trigger_and_not():
    params = mistakes.load_library()["late-loom-or-eco-up"].detector.params
    r = _base_recon()
    # baseline: Wheelbarrow at 1100, castle 1000 + 240 deadline = 1240 -> on time. Not flagged.
    assert detectors.late_or_missing_eco_up(r, params) is None
    # make Wheelbarrow missing -> flagged (castle was reached)
    r["techs"]["eco"] = [e for e in r["techs"]["eco"] if e["name"] != "Wheelbarrow"]
    out = detectors.late_or_missing_eco_up(r, params)
    assert out is not None and "Wheelbarrow" in out.observed["missing"]


def test_late_eco_up_skips_when_age_unreached():
    params = {"deadlines": {"Wheelbarrow": {"after": "castle", "deadline_s": 240}}}
    r = _base_recon()
    r["ages"]["castle_arrival_s"] = None  # never reached Castle
    r["techs"]["eco"] = []
    assert detectors.late_or_missing_eco_up(r, params) is None  # honest: can't judge -> skip


def test_too_few_villagers_trigger_and_not():
    params = mistakes.load_library()["too-few-villagers"].detector.params
    r = _base_recon()
    # floor = min(20 + 2.2*30, 110) = min(86, 110) = 86. 85 trips, 86 doesn't.
    r["counts"]["villagers_produced"] = 85
    out = detectors.too_few_villagers(r, params)
    assert out is not None and out.observed["floor"] == 86
    r["counts"]["villagers_produced"] = 86
    assert detectors.too_few_villagers(r, params) is None


def test_too_few_villagers_never_fires_on_high_produced():
    params = mistakes.load_library()["too-few-villagers"].detector.params
    r = _base_recon()
    r["counts"]["villagers_produced"] = 126  # calibration game's upper-bound count
    assert detectors.too_few_villagers(r, params) is None


def test_villager_stall_late_trigger_and_not():
    params = mistakes.load_library()["villager-stall-late"].detector.params
    r = _base_recon()
    # baseline still makes a vil at t_s 1700 (after imp 1600) -> not stalled.
    assert detectors.villager_stall_late(r, params) is None
    # remove post-imp villager queue AND drop count below floor -> stalled.
    r["production"]["produced_units"] = [
        {"name": "Villager", "unit_id": 83, "amount": 1, "t_s": 50},
        {"name": "Knight", "unit_id": 38, "amount": 5, "t_s": 1700},
    ]
    r["counts"]["villagers_produced"] = 80
    out = detectors.villager_stall_late(r, params)
    assert out is not None and out.observed["villagers_produced"] == 80
    assert out.observed["vils_queued_after_imp"] == 0


def test_villager_stall_late_not_when_maxed():
    params = mistakes.load_library()["villager-stall-late"].detector.params
    r = _base_recon()
    r["production"]["produced_units"] = [{"name": "Villager", "unit_id": 83, "amount": 1, "t_s": 50}]
    r["counts"]["villagers_produced"] = 120  # above floor -> maxed, not a stall
    assert detectors.villager_stall_late(r, params) is None


def test_villager_stall_late_skips_if_no_imperial():
    params = mistakes.load_library()["villager-stall-late"].detector.params
    r = _base_recon()
    r["ages"]["imperial_arrival_s"] = None
    assert detectors.villager_stall_late(r, params) is None


def test_exposed_gold_trigger_and_not():
    params = mistakes.load_library()["exposed-gold"].detector.params
    r = _base_recon()
    # baseline: mining camp is safe -> not flagged.
    assert detectors.exposed_gold(r, params) is None
    # put a mining camp in the FRONT with no cover nearby -> flagged.
    r["spatial"]["me"]["eco_exposure"]["front"] = [{"name": "Mining Camp", "x": 40, "y": 35, "t_s": 900}]
    r["spatial"]["me"]["forward"] = []
    r["spatial"]["me"]["walls"] = []
    out = detectors.exposed_gold(r, params)
    assert out is not None and out.observed["exposed_mining_camps"][0]["name"] == "Mining Camp"


def test_exposed_gold_not_when_covered_or_unmeasurable():
    params = mistakes.load_library()["exposed-gold"].detector.params
    r = _base_recon()
    r["spatial"]["me"]["eco_exposure"]["front"] = [{"name": "Mining Camp", "x": 40, "y": 35, "t_s": 900}]
    r["spatial"]["me"]["forward"] = [{"name": "Tower", "x": 42, "y": 36}]  # within cover_dist 12
    r["spatial"]["me"]["walls"] = []
    assert detectors.exposed_gold(r, params) is None  # covered
    # unmeasurable exposure (axis_len None) -> never guesses
    r["spatial"]["me"]["eco_exposure"] = {"front": [], "safe": [], "axis_len": None}
    assert detectors.exposed_gold(r, params) is None


def test_got_housed_trigger_and_not():
    params = mistakes.load_library()["got-housed"].detector.params
    r = _base_recon()
    assert detectors.got_housed(r, params) is None  # ceiling 120 >= 100
    r["population"]["me"]["housed_pop_ceiling"] = 60
    out = detectors.got_housed(r, params)
    assert out is not None and out.observed["housed_pop_ceiling"] == 60


def test_no_map_presence_trigger_and_not():
    params = mistakes.load_library()["no-map-presence"].detector.params
    r = _base_recon()
    assert detectors.no_map_presence(r, params) is None  # has forward + away fights
    r["spatial"]["me"]["forward"] = []
    r["combat"]["me"]["engagements"] = [{"zone": "own_base", "n_commands": 3}]
    out = detectors.no_map_presence(r, params)
    assert out is not None and out.observed["away_engagements"] == 0


def test_leaky_or_late_walls_trigger_and_not():
    params = mistakes.load_library()["leaky-or-late-walls"].detector.params
    r = _base_recon()
    # pressure at home but plenty of walls (3) -> not flagged.
    r["combat"]["me"]["engagements"] = [{"zone": "own_base", "n_commands": 4}]
    assert detectors.leaky_or_late_walls(r, params) is None
    # under pressure with too few walls -> flagged.
    r["spatial"]["me"]["walls"] = [{"x": 1, "y": 1, "x_end": 2, "y_end": 1, "name": "Palisade Wall", "t_s": 700}]
    out = detectors.leaky_or_late_walls(r, params)
    assert out is not None and out.observed["wall_segments"] == 1


def test_walled_too_early_trigger_and_not():
    params = mistakes.load_library()["walled-too-early"].detector.params
    r = _base_recon()
    # baseline walls at t_s 700-720, feudal 600 -> they're AFTER feudal -> not early.
    assert detectors.walled_too_early(r, params) is None
    # many dark-age walls before feudal -> flagged.
    r["spatial"]["me"]["walls"] = [
        {"x": i, "y": 1, "x_end": i + 1, "y_end": 1, "name": "Palisade Wall", "t_s": 200} for i in range(5)
    ]
    out = detectors.walled_too_early(r, params)
    assert out is not None and out.observed["early_wall_segments"] == 5


# ----------------------------------------------------------------- pass-level behaviour
def test_needs_2_never_in_output():
    r = _base_recon()
    # even with economy fields present, disabled detectors must not appear.
    r["economy"] = {"stockpile_estimate": {"wood": 1000}, "vils_per_resource": {"wood": 50}}
    flagged_ids = {f.id for f in mistakes.detect_mistakes(r)}
    assert flagged_ids.isdisjoint(NEEDS_2)


def test_missing_input_degrades_not_raises():
    r = _base_recon()
    del r["efficiency"]["tc_idle_s"]  # idle-tc input gone
    # idle-tc skipped, but the rest of the pass runs without raising.
    r["efficiency"]["longest_villager_gap_s"] = 200  # trip long-vil-gap to prove pass still runs
    flagged = mistakes.detect_mistakes(r)
    ids = {f.id for f in flagged}
    assert "idle-tc" not in ids
    assert "long-vil-gap" in ids


def test_determinism_identical_lists():
    r = _base_recon()
    r["efficiency"]["tc_idle_s"] = 300
    r["efficiency"]["longest_villager_gap_s"] = 120
    a = [f.to_dict() for f in mistakes.detect_mistakes(r)]
    b = [f.to_dict() for f in mistakes.detect_mistakes(copy.deepcopy(r))]
    assert a == b
    assert json.dumps(a)  # JSON-serializable


def test_flagged_sorted_by_severity_then_magnitude():
    r = _base_recon()
    r["efficiency"]["tc_idle_s"] = 300  # idle-tc high severity
    r["population"]["me"]["housed_pop_ceiling"] = 40  # got-housed medium
    flagged = mistakes.detect_mistakes(r)
    ranks = [detect._SEVERITY_RANK[f.severity] for f in flagged]
    assert ranks == sorted(ranks, reverse=True)


def test_build_target_arg_routes_to_slow_age():
    r = _base_recon()
    r["ages"]["castle_arrival_s"] = 1000
    flagged = mistakes.detect_mistakes(r, build_target={"castle": {"max_s": 900}})
    sc = next((f for f in flagged if f.id == "slow-castle"), None)
    assert sc is not None and sc.observed["basis"] == "build"
    # original recon dict not mutated by the build_target injection
    assert "_build_target" not in r


# -------------------------------------------------------------- calibration golden (real)
@requires_rec
def test_calibration_game_golden():
    from aoe2coach.parser import parse_rec
    from aoe2coach.reconstruct import reconstruct

    recon = reconstruct(parse_rec(REC_PATH, RELIC_PROFILE_ID)).to_dict()
    flagged = mistakes.detect_mistakes(recon)
    ids = {f.id for f in flagged}

    # Should surface late ages (20:55 Castle / 40:23 Imperial are late vs generic bands).
    assert "slow-castle" in ids or "slow-imperial" in ids
    # An idle/low-APM-driven flag consistent with very low effective APM.
    assert "idle-tc" in ids or "long-vil-gap" in ids

    # Must NOT false-positive: too-few-villagers (126 produced is an upper bound), no needs-#2.
    assert "too-few-villagers" not in ids
    assert ids.isdisjoint(NEEDS_2)

    # honesty tags + serializable
    for f in flagged:
        assert f.confidence_tier in ("exact", "heuristic")  # needs-#2 never fires
    json.dumps([f.to_dict() for f in flagged])


@requires_rec2
def test_second_rec_runs_clean():
    from aoe2coach.parser import parse_rec
    from aoe2coach.reconstruct import reconstruct

    recon = reconstruct(parse_rec(REC2_PATH, RELIC_PROFILE_ID)).to_dict()
    flagged = mistakes.detect_mistakes(recon)
    ids = {f.id for f in flagged}
    assert ids.isdisjoint(NEEDS_2)
    json.dumps([f.to_dict() for f in flagged])
