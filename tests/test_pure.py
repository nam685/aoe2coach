import json
from unittest.mock import MagicMock, patch

from mgz.fast import Action

from aoe2coach import const
from aoe2coach.coach import BENCHMARKS, COACH_SYSTEM, CoachOutput, build_coach_prompt, coach, parse_opening
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
    # Every event line (excluding the header and trailing APM line) is role- and tag-tagged.
    for ln in lines[1:-1]:
        parts = ln.split(" ")
        assert parts[1] in {"ME", "OPP"}
        assert parts[2] in {"AGE_UP", "BUILD", "TECH", "TRAIN"}
    assert "ME BUILD Archery Range" in log
    assert "OPP BUILD Stable" in log
    assert "OPP TRAIN Scout Cavalry" in log  # first OPP military unit surfaces
    assert log.rstrip().endswith("APM total_actions=6")


def test_benchmarks_sliced_verbatim():
    # The 6 benchmark rows must be present and the slice must be a substring of COACH_SYSTEM.
    assert BENCHMARKS.startswith("AoE2 1v1 benchmark uptimes")
    assert BENCHMARKS in COACH_SYSTEM
    for row in ["Scouts opening", "Archers opening", "Drush", "Fast Castle", "Tower Rush"]:
        assert row in BENCHMARKS
    assert "Key metrics" not in BENCHMARKS  # end anchor excluded


def test_build_coach_prompt_structure_unchanged():
    metrics = {
        "feudal_uptime_s": 715,
        "castle_uptime_s": None,
        "imperial_uptime_s": None,
        "apm": 80,
        "villager_count": 25,
        "army": [{"name": "Archer", "amount": 12}],
        "eco_tech_timings": [{"name": "Double-Bit Axe", "t_s": 610}],
    }
    p = build_coach_prompt("00:00 APM total_actions=6", metrics)
    assert p.startswith(COACH_SYSTEM)
    assert "=== METRICS SUMMARY ===" in p
    assert "=== SALIENT LOG ===" in p
    assert p.rstrip().endswith("Now write the coach report.")


def test_parse_opening():
    assert parse_opening("OPENING: Scouts\n\nbody") == "Scouts"
    assert parse_opening("no tag here") == ""


def test_coach_mocks_subprocess_and_extracts_fields():
    fake = json.dumps(
        {"result": "OPENING: Archers\n\nGood archer opening.", "model": "claude-sonnet-4-5", "is_error": False}
    )
    with patch("aoe2coach.coach.subprocess.run") as run:
        run.return_value = MagicMock(returncode=0, stdout=fake, stderr="")
        out = coach({"apm": 80}, "00:00 APM total_actions=6", model="sonnet")
    assert isinstance(out, CoachOutput)
    assert out.raw_text == "OPENING: Archers\n\nGood archer opening."
    assert out.opening_tag == "Archers"
    assert out.model_used == "claude-sonnet-4-5"
    # subprocess invoked with the exact CLI contract
    args = run.call_args.args[0]
    assert args[:2] == ["claude", "-p"] and "--output-format" in args and "json" in args


def test_analyze_replay_data_contract():
    import aoe2coach.entrypoint as ep

    fake_rec = MagicMock()
    fake_rec.ops = []
    fake_rec.duration_ms = 900_000
    fake_rec.me = {"number": 1, "civ_name": "Franks", "profile_id": 42}
    fake_rec.opponent = {"number": 2, "civ_name": "Mayans"}
    fake_rec.my_result = "win"

    with (
        patch.object(ep, "parse_rec", return_value=fake_rec),
        patch.object(
            ep,
            "build_timeline",
            return_value={
                "uptimes": {"feudal": None, "castle": None, "imperial": None},
                "units": [],
                "eco_techs": [],
                "action_count": 0,
            },
        ),
        patch.object(ep, "compute_metrics", return_value={"apm": 50, "army": [], "eco_tech_timings": []}),
        patch.object(ep, "render_dual_log", return_value="# ME = you\n00:00 APM total_actions=0"),
        patch.object(
            ep,
            "coach",
            return_value=CoachOutput(
                raw_text="OPENING: Scouts\n\nx", opening_tag="Scouts", model_used="claude-sonnet-4-5"
            ),
        ),
    ):
        # use_v2=False exercises the legacy single-shot contract (v2 adds facts_json/coach_tier
        # additively — covered in tests/test_coach_v2.py against the full bundle).
        row = ep.analyze_replay("/fake.aoe2record", 42, elo_band="mid", use_v2=False)

    assert set(row) == {
        "match_id",
        "metrics_json",
        "salient_log",
        "game_result",
        "coach_output",
        "opening",
        "civ",
        "elo_band",
    }
    assert row["game_result"] == "win"
    assert row["opening"] == "Scouts"
    assert row["civ"] == "Franks"
    assert row["elo_band"] == "mid"
    assert json.loads(row["metrics_json"])["apm"] == 50
    assert all(isinstance(v, str) for v in row.values())
