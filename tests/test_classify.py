"""Tests for sub-project #3: the build-order reference library + deterministic classifier.

Synthetic Reconstruction dicts are minimal hand-built versions of #1's `reconstruct(rec).to_dict()`
shape (only the fields B.2 consumes). Real-rec sanity checks run against the two analysis recs when
present.
"""

import os

import pytest

from aoe2coach import classify, const, reconstruct
from aoe2coach.buildorders import SchemaError, build_ids, load_library, load_one
from aoe2coach.parser import parse_rec

REC_PATH = "/home/namle685/projects/aoe2coach-analysis/game.aoe2record"
REC2_PATH = "/home/namle685/projects/aoe2coach-analysis/game2.aoe2record"
RELIC_PROFILE_ID = 14697894
requires_rec = pytest.mark.skipif(not os.path.exists(REC_PATH), reason="calibration rec not present")
requires_rec2 = pytest.mark.skipif(not os.path.exists(REC2_PATH), reason="second rec not present")


# --------------------------------------------------------------------------- recon builder helper
def make_recon(
    *,
    feudal_arrival_s=None,
    castle_arrival_s=None,
    imperial_arrival_s=None,
    first_mil_building=None,
    first_mil_building_s=None,
    units=None,  # list of (name, unit_id, t_s)
    vils_at_feudal_click=None,
    buildings=None,  # list of (name, t_s)
    my_civ=None,
):
    """Build a minimal Reconstruction dict with just the fields the classifier reads (B.2)."""
    units = units or []
    produced = [{"name": n, "unit_id": uid, "amount": 1, "t_s": t} for (n, uid, t) in units]
    blds = [{"name": n, "x": 0, "y": 0, "t_s": t} for (n, t) in (buildings or [])]
    fmb = {"name": first_mil_building, "t_s": first_mil_building_s} if first_mil_building else None
    return {
        "meta": {"my_civ": my_civ},
        "ages": {
            "feudal_arrival_s": feudal_arrival_s,
            "castle_arrival_s": castle_arrival_s,
            "imperial_arrival_s": imperial_arrival_s,
        },
        "production": {
            "produced_units": produced,
            "milestones": {
                "first_military_building": fmb,
                "first_military_building_s": first_mil_building_s,
            },
            "vils_at_feudal_click": vils_at_feudal_click,
        },
        "spatial": {"me": {"buildings": blds}},
    }


# --------------------------------------------------------------------------- A: library + schema
def test_library_loads_and_validates():
    lib = load_library()
    assert len(lib) >= 10  # the encode-first subset + generic FC
    # every build's signature unit/building names resolve via const (no "#id" leaks)
    known_units = set(const.UNIT_NAMES.values()) | set(const.UNIT_CLASS.values())
    known_blds = set(const.BUILDING_NAMES.values())
    for bo in lib.values():
        sig = bo.signature
        for u in sig.first_military_unit + sig.defining_units:
            assert u in known_units, f"{bo.id}: unit {u!r} not a known unit name/class"
        for b in sig.first_military_buildings + sig.excludes_buildings:
            assert b in known_blds, f"{bo.id}: building {b!r} not a known building name"


def test_build_ids_excludes_index():
    ids = build_ids()
    assert "_index" not in ids
    assert "scouts-into-archers" in ids


def test_load_one_progressive_disclosure():
    one = load_one("knight-rush")
    assert one["id"] == "knight-rush"
    assert "steps" in one and "whats_next" in one  # full reference, not just signature


def test_load_one_missing_raises():
    with pytest.raises(FileNotFoundError):
        load_one("no-such-build")


def test_load_one_rejects_traversal():
    with pytest.raises(ValueError):
        load_one("../secrets")


def test_validate_rejects_bad_band():
    bad = {
        "id": "x",
        "name": "X",
        "source": {},
        "family": "scouts",
        "signature": {
            "first_military_buildings": ["Stable"],
            "first_military_unit": ["Scout Cavalry"],
            "defining_units": [],
            "feudal_arrival_band_s": [600, 500],  # lo > hi
            "vils_at_feudal_click": {"target": 18, "band": [16, 20]},
            "excludes_buildings": [],
            "age_path": "feudal_rush",
        },
    }
    with pytest.raises(SchemaError):
        from aoe2coach.buildorders import validate

        validate(bad)


def test_validate_rejects_bad_age_path():
    bad = {
        "id": "x",
        "name": "X",
        "source": {},
        "family": "scouts",
        "signature": {
            "first_military_buildings": ["Stable"],
            "first_military_unit": ["Scout Cavalry"],
            "defining_units": [],
            "feudal_arrival_band_s": [500, 600],
            "vils_at_feudal_click": {"target": 18, "band": [16, 20]},
            "excludes_buildings": [],
            "age_path": "rush_to_wonder",  # not in enum
        },
    }
    with pytest.raises(SchemaError):
        from aoe2coach.buildorders import validate

        validate(bad)


# --------------------------------------------------------------------------- B: classifier textbook
def test_scout_rush_top_candidate():
    recon = make_recon(
        feudal_arrival_s=530,
        first_mil_building="Stable",
        first_mil_building_s=540,
        units=[("Scout Cavalry", 448, 560)],
        vils_at_feudal_click=18,
        buildings=[("Stable", 540)],
    )
    res = classify(recon)
    assert res.candidates[0].build_id == "scout-rush-1-stable"
    assert not res.unknown
    # pure scout must NOT be classified as scouts-into-archers (no Archer seen)
    assert res.candidates[0].build_id != "scouts-into-archers"


def test_scouts_into_archers_beats_pure_scouts():
    recon = make_recon(
        feudal_arrival_s=535,
        first_mil_building="Stable",
        first_mil_building_s=545,
        units=[("Scout Cavalry", 448, 560), ("Archer", 4, 650)],
        vils_at_feudal_click=18,
        buildings=[("Stable", 545), ("Archery Range", 620)],
    )
    res = classify(recon)
    assert res.candidates[0].build_id == "scouts-into-archers"
    ids = [c.build_id for c in res.candidates]
    # pure scout rush should rank below the archer-transition build
    assert ids.index("scouts-into-archers") < (ids.index("scout-rush-1-stable") if "scout-rush-1-stable" in ids else 99)


def test_straight_archers():
    recon = make_recon(
        feudal_arrival_s=555,
        first_mil_building="Archery Range",
        first_mil_building_s=560,
        units=[("Archer", 4, 580)],
        vils_at_feudal_click=19,
        buildings=[("Archery Range", 560)],
    )
    res = classify(recon)
    assert res.candidates[0].build_id == "archers-1-range"


def test_maa_rush():
    recon = make_recon(
        feudal_arrival_s=505,
        first_mil_building="Barracks",
        first_mil_building_s=480,
        units=[("Man-at-Arms", 75, 520)],
        vils_at_feudal_click=18,
        buildings=[("Barracks", 480)],
    )
    res = classify(recon)
    assert res.candidates[0].build_id in ("generic-maa-rush", "feudal-drush")
    assert res.candidates[0].build_id == "generic-maa-rush"


def test_knight_rush_fast_castle():
    recon = make_recon(
        feudal_arrival_s=600,
        castle_arrival_s=920,
        first_mil_building="Stable",
        first_mil_building_s=950,  # AFTER castle arrival -> no feudal military
        units=[("Knight", 38, 980)],
        vils_at_feudal_click=22,
        buildings=[("Stable", 950)],
    )
    res = classify(recon)
    assert res.candidates[0].build_id == "knight-rush"
    # fast_castle observed -> no feudal_rush build should survive the pre-filter
    ids = [c.build_id for c in res.candidates]
    assert "scout-rush-1-stable" not in ids
    assert "generic-maa-rush" not in ids


def test_fast_castle_generic_when_no_committed_unit():
    recon = make_recon(
        feudal_arrival_s=610,
        castle_arrival_s=960,
        vils_at_feudal_click=22,
        units=[],  # no military yet at classification time
        buildings=[],
    )
    res = classify(recon)
    ids = [c.build_id for c in res.candidates]
    assert "fast-castle-generic" in ids
    assert all(c.build_id not in ("scout-rush-1-stable", "generic-maa-rush") for c in res.candidates)


# --------------------------------------------------------------------------- civ normalisation
def test_unique_unit_normalised_to_class():
    # A Briton doing straight Longbowmen should still match archers via UNIT_CLASS.
    recon = make_recon(
        feudal_arrival_s=555,
        first_mil_building="Archery Range",
        first_mil_building_s=560,
        units=[("Longbowman", 8, 580)],
        vils_at_feudal_click=19,
        buildings=[("Archery Range", 560)],
        my_civ="Britons",
    )
    res = classify(recon)
    assert res.candidates[0].build_id == "archers-1-range"


# --------------------------------------------------------------------------- off-meta / unknown
def test_tower_rush_is_unknown():
    # Towers forward, no normal military building, weird timings -> off-meta.
    recon = make_recon(
        feudal_arrival_s=500,
        first_mil_building="Watch Tower",
        first_mil_building_s=510,
        units=[],
        vils_at_feudal_click=14,
        buildings=[("Watch Tower", 510), ("Watch Tower", 540)],
    )
    res = classify(recon)
    assert res.unknown is True
    assert res.candidates  # closest-N still returned
    assert any("off-meta" in n for n in res.notes)


def test_castle_drop_rules_out_feudal_rush():
    # An early Castle (before castle age) must rule out feudal-rush builds via excludes_buildings.
    recon = make_recon(
        feudal_arrival_s=530,
        first_mil_building="Stable",
        first_mil_building_s=540,
        units=[("Scout Cavalry", 448, 560)],
        vils_at_feudal_click=18,
        buildings=[("Stable", 540), ("Castle", 700)],  # Castle before castle age
    )
    res = classify(recon)
    ids = [c.build_id for c in res.candidates]
    assert "scout-rush-1-stable" not in ids  # excluded by the Castle


# --------------------------------------------------------------------------- determinism
def test_determinism():
    recon = make_recon(
        feudal_arrival_s=535,
        first_mil_building="Stable",
        first_mil_building_s=545,
        units=[("Scout Cavalry", 448, 560), ("Archer", 4, 650)],
        vils_at_feudal_click=18,
        buildings=[("Stable", 545), ("Archery Range", 620)],
    )
    r1 = classify(recon)
    r2 = classify(recon)
    assert r1.to_dict() == r2.to_dict()


def test_result_is_json_serializable():
    import json

    recon = make_recon(
        feudal_arrival_s=530,
        first_mil_building="Stable",
        first_mil_building_s=540,
        vils_at_feudal_click=18,
        buildings=[("Stable", 540)],
        units=[("Scout Cavalry", 448, 560)],
    )
    res = classify(recon)
    json.dumps(res.to_dict())  # must not raise


def test_candidates_capped_at_three():
    recon = make_recon(
        feudal_arrival_s=535,
        first_mil_building="Stable",
        first_mil_building_s=545,
        units=[("Scout Cavalry", 448, 560)],
        vils_at_feudal_click=18,
        buildings=[("Stable", 545)],
    )
    res = classify(recon)
    assert len(res.candidates) <= 3


# --------------------------------------------------------------------------- real-rec sanity
@requires_rec
def test_calibration_game_plausible():
    """game.aoe2record: Vietnamese 'nom', Feudal 9:34 / Castle 20:55, skirms+scouts+battle elephants.

    Plausibility + honesty (Part D): feudal-military-then-slow-Castle should surface scout/skirm
    feudal-rush candidates, NOT a fast-castle/knight build, and NOT one over-confident label.
    """
    recon = reconstruct(parse_rec(REC_PATH, RELIC_PROFILE_ID)).to_dict()
    res = classify(recon)
    ids = [c.build_id for c in res.candidates]
    # no fast-castle / knight build should be the top candidate for a feudal-military game
    assert res.candidates[0].build_id not in ("knight-rush", "fast-castle-generic")
    # not falsely over-confident on this partly off-meta game
    assert not (res.is_confident and res.candidates[0].confidence > 0.9)
    # at least one scout/skirm/archer feudal-rush family in candidates
    assert any(
        i
        in (
            "scouts-into-archers",
            "scout-rush-1-stable",
            "korean-spear-skirm",
            "maa-into-skirms",
            "archers-1-range",
            "scouts-into-cav-archers",
        )
        for i in ids
    )
