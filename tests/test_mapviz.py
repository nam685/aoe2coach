"""Tests for sub-project #7: strategic map rendering.

geometry.py is pure (no image I/O) and gets exhaustive unit coverage. render.py is smoke-tested
only: it must produce a valid, non-empty PNG of the requested size. Real-rec sanity (writing
eyeballable PNGs) lives in a guarded test that skips when the calibration recs are absent.

Convention under test: ME = blue, OPP = red. AoE2 game origin is top-left, y grows downward, so the
projection is a straight uniform scale (no y-flip) into a padded image box.
"""

import os

import pytest

from aoe2coach.mapviz import geometry as geo

# --------------------------------------------------------------------------- project_point


def test_project_point_origin_and_extent():
    # map_dim 120, image 400, margin 20 => usable box is 360px wide, scale = 360/120 = 3 px/tile.
    img, margin, dim = 400, 20, 120
    # game (0,0) -> top-left of the box
    px, py = geo.project_point(0, 0, dim, img, margin)
    assert (px, py) == (20.0, 20.0)
    # game (120,120) -> bottom-right of the box
    px, py = geo.project_point(120, 120, dim, img, margin)
    assert (px, py) == (380.0, 380.0)
    # center
    px, py = geo.project_point(60, 60, dim, img, margin)
    assert (px, py) == (200.0, 200.0)


def test_project_point_no_y_flip():
    # AoE2 y grows downward like image y; a larger game-y must yield a larger image-y.
    _, py_low = geo.project_point(10, 10, 120, 400, 20)
    _, py_high = geo.project_point(10, 100, 120, 400, 20)
    assert py_high > py_low


def test_project_point_stays_in_bounds_for_extreme_coords():
    # Coords at/over the map edge clamp into the image box, never outside it.
    img, margin, dim = 300, 10, 120
    for gx, gy in [(-5, -5), (0, 0), (120, 120), (130, 130)]:
        px, py = geo.project_point(gx, gy, dim, img, margin)
        assert margin <= px <= img - margin
        assert margin <= py <= img - margin


def test_project_point_defaults_dim_when_missing():
    # A None/zero map_dim must not divide-by-zero; it falls back to DEFAULT_MAP_DIM.
    px, py = geo.project_point(0, 0, None, 400, 20)
    assert (px, py) == (20.0, 20.0)
    px2, py2 = geo.project_point(0, 0, 0, 400, 20)
    assert (px2, py2) == (20.0, 20.0)


def test_project_segment():
    seg = geo.project_segment(0, 0, 120, 120, 120, 400, 20)
    assert seg == (20.0, 20.0, 380.0, 380.0)


# --------------------------------------------------------------------------- layout


def _recon(engagements=None):
    """Minimal Reconstruction-shaped dict (what reconstruct().to_dict() yields), ME blue / OPP red."""
    return {
        "meta": {"map": "Arabia", "map_dim": 120, "duration_s": 1800},
        "spatial": {
            "me": {
                "base_centroid": {"x": 40.0, "y": 90.0},
                "buildings": [
                    {"name": "Town Center", "x": 40.0, "y": 90.0, "t_s": 0},
                    {"name": "House", "x": 42.0, "y": 92.0, "t_s": 30},
                    {"name": "Barracks", "x": 60.0, "y": 60.0, "t_s": 300},
                ],
                "forward": [{"name": "Barracks", "x": 60.0, "y": 60.0, "t_s": 300, "dist": 36.0}],
                "walls": [{"x": 30.0, "y": 80.0, "x_end": 50.0, "y_end": 80.0, "name": "Palisade Wall", "t_s": 100}],
            },
            "opp": {
                "base_centroid": {"x": 80.0, "y": 20.0},
                "buildings": [
                    {"name": "Town Center", "x": 80.0, "y": 20.0, "t_s": 0},
                    {"name": "Stable", "x": 82.0, "y": 22.0, "t_s": 200},
                ],
                "walls": [],
            },
        },
        "combat": {
            "me": {"engagements": engagements if engagements is not None else []},
        },
    }


def test_layout_basic_structure():
    lay = geo.layout(_recon(), img_size=400, margin=20)
    assert lay.img_size == 400
    # both bases present and colored by side
    assert lay.me_base is not None and lay.me_base.side == "me"
    assert lay.opp_base is not None and lay.opp_base.side == "opp"
    # buildings split by side, each carrying its side + projected px/py in-bounds
    assert len(lay.me_buildings) == 3
    assert len(lay.opp_buildings) == 2
    for b in lay.me_buildings + lay.opp_buildings:
        assert 20 <= b.px <= 380 and 20 <= b.py <= 380
    assert {b.side for b in lay.me_buildings} == {"me"}
    assert {b.side for b in lay.opp_buildings} == {"opp"}


def test_layout_forward_buildings_flagged():
    lay = geo.layout(_recon(), img_size=400, margin=20)
    assert len(lay.forward_buildings) == 1
    assert lay.forward_buildings[0].name == "Barracks"
    assert lay.forward_buildings[0].side == "me"


def test_layout_walls_projected():
    lay = geo.layout(_recon(), img_size=400, margin=20)
    assert len(lay.me_walls) == 1
    seg = lay.me_walls[0]
    # projected endpoints in-bounds, four floats
    assert len(seg.points) == 4
    for v in seg.points:
        assert 20 <= v <= 380


def test_layout_engagement_markers_and_zone():
    eng = [{"zone": "center", "start_s": 600, "end_s": 640, "x": 60.0, "y": 55.0, "n_commands": 5}]
    lay = geo.layout(_recon(eng), img_size=400, margin=20)
    assert len(lay.engagements) == 1
    m = lay.engagements[0]
    assert m.zone == "center"
    assert m.n_commands == 5
    assert 20 <= m.px <= 380 and 20 <= m.py <= 380


def test_layout_direction_arrows_from_my_base_to_engagements_and_forward():
    eng = [{"zone": "opp_base", "start_s": 600, "end_s": 640, "x": 78.0, "y": 22.0, "n_commands": 5}]
    lay = geo.layout(_recon(eng), img_size=400, margin=20)
    # An arrow from my base toward each engagement (attack direction) and each forward building.
    assert len(lay.arrows) >= 2
    for a in lay.arrows:
        # arrow tail is at my base, head at the target; all in-bounds
        assert (a.x0, a.y0) == (lay.me_base.px, lay.me_base.py)
        for v in (a.x0, a.y0, a.x1, a.y1):
            assert 20 <= v <= 380


def test_layout_handles_missing_bases_gracefully():
    r = _recon()
    r["spatial"]["me"]["base_centroid"] = None
    r["spatial"]["opp"]["base_centroid"] = None
    lay = geo.layout(r, img_size=400, margin=20)
    # No bases -> no base markers, no arrows, but buildings still project and nothing raises.
    assert lay.me_base is None and lay.opp_base is None
    assert lay.arrows == []
    assert len(lay.me_buildings) == 3


def test_layout_snapshot_filters_buildings_by_time():
    # A snapshot at t=120s shows only buildings placed by then (engagement-triggered framing).
    lay = geo.layout(_recon(), img_size=400, margin=20, at_s=120)
    names = {b.name for b in lay.me_buildings}
    assert "Town Center" in names and "House" in names
    assert "Barracks" not in names  # placed at t_s=300, after the snapshot
    assert lay.at_s == 120


# --------------------------------------------------------------------------- render (smoke)


def _read_png_size(path):
    """Return (width, height) from a PNG's IHDR without Pillow, to verify size independently."""
    import struct

    with open(path, "rb") as f:
        data = f.read(33)
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    w, h = struct.unpack(">II", data[16:24])
    return w, h


def test_render_layout_produces_valid_png(tmp_path):
    from aoe2coach.mapviz import render

    out = tmp_path / "map.png"
    eng = [{"zone": "center", "start_s": 600, "end_s": 640, "x": 60.0, "y": 55.0, "n_commands": 5}]
    lay = geo.layout(_recon(eng), img_size=480, margin=24)
    render.render_layout(lay, str(out), title="Arabia — overall")
    assert out.exists() and out.stat().st_size > 0
    assert _read_png_size(str(out)) == (480, 480)


def test_render_maps_returns_overall_plus_per_engagement(tmp_path):
    from aoe2coach.mapviz import render

    eng = [
        {"zone": "center", "start_s": 600, "end_s": 640, "x": 60.0, "y": 55.0, "n_commands": 5},
        {"zone": "opp_base", "start_s": 900, "end_s": 950, "x": 78.0, "y": 22.0, "n_commands": 8},
    ]
    paths = render.render_maps(_recon(eng), str(tmp_path), prefix="g")
    # overall layout + one snapshot per engagement
    assert len(paths) == 3
    for p in paths:
        assert os.path.exists(p) and os.path.getsize(p) > 0


# --------------------------------------------------------------------------- real-rec sanity

REC_PATH = "/home/namle685/projects/aoe2coach-analysis/game.aoe2record"
REC2_PATH = "/home/namle685/projects/aoe2coach-analysis/game2.aoe2record"
RELIC_PROFILE_ID = 14697894


@pytest.mark.skipif(not os.path.exists(REC_PATH), reason="calibration rec not present")
def test_real_rec_layout_in_bounds():
    from aoe2coach import parse_rec, reconstruct

    rec = reconstruct(parse_rec(REC_PATH, RELIC_PROFILE_ID)).to_dict()
    lay = geo.layout(rec, img_size=600, margin=30)
    assert lay.me_base is not None and lay.opp_base is not None
    assert lay.me_base.side == "me" and lay.opp_base.side == "opp"
    assert len(lay.me_buildings) > 0 and len(lay.opp_buildings) > 0
    for b in lay.me_buildings + lay.opp_buildings:
        assert 30 <= b.px <= 570 and 30 <= b.py <= 570
