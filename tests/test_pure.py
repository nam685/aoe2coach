from mgz.fast import Action

from aoe2coach import const
from aoe2coach.metrics import compute_metrics
from aoe2coach.timeline import build_timeline, render_dual_log


def test_name_helpers():
    assert const.civ_name(8) == "Persians"
    assert const.civ_name(999) == "#999"
    assert const.unit_name(const.VILLAGER_ID) == "Villager"
    assert const.building_name(50) == "Farm"
    assert const.unit_name(128) == "Trade Cart"
    assert const.building_name(99999) == "#99999"


def _ops():
    # (t_ms, action_type, data) — shapes match mgz.fast.parse_action output.
    return [
        (585_000, Action.RESEARCH, {"player_id": 1, "technology_id": 101}),  # feudal click
        (610_000, Action.RESEARCH, {"player_id": 1, "technology_id": 202}),  # Double-Bit Axe
        (610_000, Action.BUILD, {"player_id": 1, "building_id": 87, "x": 30.0, "y": 30.0}),
        (620_000, Action.BUILD, {"player_id": 1, "building_id": 50, "x": 31.0, "y": 31.0}),
        (625_000, Action.DE_QUEUE, {"player_id": 1, "unit_id": 83, "amount": 1}),  # Villager
        (630_000, Action.DE_QUEUE, {"player_id": 1, "unit_id": 4, "amount": 2}),  # Archer x2
        (590_000, Action.RESEARCH, {"player_id": 2, "technology_id": 101}),  # OPP feudal
        (615_000, Action.BUILD, {"player_id": 2, "building_id": 101, "x": 90.0, "y": 90.0}),  # OPP Stable
        (620_000, Action.DE_QUEUE, {"player_id": 2, "unit_id": 448, "amount": 1}),  # OPP Scout
    ]


def test_build_timeline_uptimes_and_units():
    tl = build_timeline(_ops(), me_number=1)
    assert tl["uptimes"]["feudal"] == 585_000
    assert tl["eco_techs"] == [{"t": 610_000, "name": "Double-Bit Axe"}]
    assert tl["action_count"] == 6  # 6 ME ops
    names = {u["name"] for u in tl["units"]}
    assert "Villager" in names and "Archer" in names


def test_compute_metrics_feudal_arrival():
    tl = build_timeline(_ops(), me_number=1)
    m = compute_metrics(tl, duration_ms=900_000)
    # arrival = click 585s + 130s research = 715s
    assert m["feudal_uptime_s"] == 715
    assert any(a["name"] == "Archer" for a in m["army"])
    assert m["villager_count"] == 1


def test_render_dual_log_roles_and_format():
    log = render_dual_log(_ops(), me_number=1, opp_number=2, me_action_count=6)
    lines = log.splitlines()
    assert lines[0].startswith("# ME = you")
    for ln in lines[1:]:
        parts = ln.split(" ")
        if parts[1] in {"ME", "OPP"}:
            assert parts[1] in {"ME", "OPP"}
    assert "OPP BUILD Stable" in log
    assert log.rstrip().endswith("APM total_actions=6")
