"""Tests for sub-project #4: Coach v2 (agentic, workspace, progressive disclosure).

ALL tests mock the `claude` subprocess — no real CLI, no network. The package stays pure +
offline-testable. The runner is injected (`runner=`) so nothing ever spawns a real binary.
"""

import json
import sys
import types
from pathlib import Path

import pytest

from aoe2coach.classify import Candidate, ClassificationResult
from aoe2coach.coach import (
    COACH_SYSTEM_V2,
    CoachOutput,
    build_coach_prompt_v2,
    coach,
    parse_opening,
    run_agentic_coach,
)
from aoe2coach.mistakes.detect import Flagged
from aoe2coach.workspace import build_candidates_md, build_workspace

# The `coach` function is re-exported into the `aoe2coach.coach` attribute path on the package,
# so reach the actual module object via sys.modules for monkeypatching run_claude_coach.
coach_mod = sys.modules["aoe2coach.coach"]


# --------------------------------------------------------------------------- fixtures / helpers
def make_recon_dict():
    """A minimal but realistic Reconstruction dict (the v2 facts.json payload)."""
    return {
        "meta": {"map": "Arabia", "duration_s": 1500, "my_civ": "Franks", "result": "win"},
        "ages": {"feudal_arrival_s": 574, "castle_arrival_s": 1100},
        "techs": {"eco": [{"name": "Loom", "t_s": 480}], "military": [], "university": []},
        "production": {
            "produced_units": [{"name": "Archer", "unit_id": 4, "amount": 6, "t_s": 600}],
            "milestones": {"first_military_building": {"name": "Archery Range"}, "first_siege_s": None},
            "vils_at_feudal_click": 18,
        },
        "counts": {"villagers_produced": 30, "army_produced": [{"name": "Archer", "amount": 6}]},
        "spatial": {"me": {"forward": []}, "opp": {}},
        "efficiency": {"tc_idle_s": 40, "longest_villager_gap_s": 25},
    }


def make_candidates():
    return ClassificationResult(
        candidates=[
            Candidate(build_id="archers-1-range", name="1-Range Archers", confidence=0.80, matched_signals=["feudal"]),
            Candidate(build_id="scouts-into-archers", name="Scouts into Archers", confidence=0.42),
        ],
        notes=["low-confidence: top=archers-1-range 0.80"],
    )


def make_flagged():
    return [
        Flagged(
            id="idle-tc",
            name="Idle Town Center",
            severity="high",
            confidence_tier="exact",
            observed={"tc_idle_s": 40},
            reference_path="mistakes/data/idle-tc.yaml",
        )
    ]


class FakeRun:
    """A subprocess.run stand-in. Records the call and returns a canned CompletedProcess."""

    def __init__(self, stdout="", returncode=0, stderr="", raise_exc=None):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr
        self.raise_exc = raise_exc
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append({"argv": argv, "kwargs": kwargs})
        if self.raise_exc is not None:
            raise self.raise_exc
        return types.SimpleNamespace(returncode=self.returncode, stdout=self.stdout, stderr=self.stderr)


def ok_json(result_text, model="claude-sonnet-4-6"):
    return json.dumps({"result": result_text, "model": model, "is_error": False, "num_turns": 3})


# --------------------------------------------------------------------------- workspace build
def test_build_workspace_writes_all_files():
    recon = make_recon_dict()
    with build_workspace(
        recon,
        make_candidates(),
        salient_log="00:10 ME BUILD Archery Range",
        economy={"estimate": True, "collected": None},
        mistakes=make_flagged(),
    ) as ws:
        ws = Path(ws)
        assert (ws / "facts.json").exists()
        assert (ws / "salient.log").exists()
        assert (ws / "candidates.md").exists()
        assert (ws / "mistakes.json").exists()
        assert (ws / "economy.json").exists()
        assert (ws / "map_legend.md").exists()
        assert (ws / "TASK.md").exists()
        assert (ws / "references").is_dir()
        # facts.json round-trips the reconstruction
        assert json.loads((ws / "facts.json").read_text()) == recon
        # references/ copied the build library (the candidate's file is present)
        assert (ws / "references" / "archers-1-range.yaml").exists()
        # mistakes.json round-trips the flagged list
        m = json.loads((ws / "mistakes.json").read_text())
        assert m[0]["id"] == "idle-tc"
        # candidates.md points at the reference path
        cmd = (ws / "candidates.md").read_text()
        assert "references/archers-1-range.yaml" in cmd
        # NO CLAUDE.md leaks into the workspace
        assert not (ws / "CLAUDE.md").exists()
        saved = ws
    # cleaned up on exit
    assert not Path(saved).exists()


def test_build_workspace_copies_references_not_symlink():
    with build_workspace(make_recon_dict()) as ws:
        ref = Path(ws) / "references" / "archers-1-range.yaml"
        assert ref.exists() and not ref.is_symlink()


def test_build_workspace_accepts_reconstruction_object():
    class FakeRecon:
        def to_dict(self):
            return make_recon_dict()

    with build_workspace(FakeRecon()) as ws:
        assert json.loads((Path(ws) / "facts.json").read_text())["meta"]["map"] == "Arabia"


def test_build_workspace_debug_leaves_dir():
    with build_workspace(make_recon_dict(), debug=True) as ws:
        saved = Path(ws)
    assert saved.exists()
    # caller cleans up
    import shutil

    shutil.rmtree(saved)


def test_build_workspace_map_pngs(tmp_path):
    overall = tmp_path / "o.png"
    eng = tmp_path / "e.png"
    overall.write_bytes(b"\x89PNG_overall")
    eng.write_bytes(b"\x89PNG_eng")
    with build_workspace(make_recon_dict(), map_pngs=[str(overall), str(eng)]) as ws:
        ws = Path(ws)
        assert (ws / "strategic_map.png").read_bytes() == b"\x89PNG_overall"
        assert (ws / "engagement_01.png").read_bytes() == b"\x89PNG_eng"
        assert "engagement_01.png" in (ws / "TASK.md").read_text()


def test_candidates_md_handles_empty():
    md = build_candidates_md(None)
    assert "Grep references/" in md


# --------------------------------------------------------------------------- system prompt content
def test_coach_system_v2_contract():
    s = COACH_SYSTEM_V2
    assert "WHAT HAPPENED" in s
    assert "ANALYSIS" in s
    # named markers
    for marker in ("first military building", "first siege", "first treb", "forward buildings", "HOW THE GAME ENDED"):
        assert marker in s
    # cite-the-reference + don't-invent rules
    assert "Cite every benchmark" in s
    assert "do NOT assert a number" in s
    # honesty: produced is not live
    assert "produced" in s and "NOT live" in s
    # judges by ARRIVAL
    assert "ARRIVAL" in s


def test_coach_system_v2_forbids_meta_preamble():
    """The final report must read as a seamless human-written coaching report — no AI self-reference,
    no tool/process narration, no meta-preamble. The prompt must explicitly forbid these."""
    s = COACH_SYSTEM_V2.lower()
    # explicitly names the forbidden meta-preamble patterns
    assert "now i have all the data" in s
    assert "here is the report" in s
    # forbids AI self-reference and process/tool narration
    assert "ai self-reference" in s
    assert "tool narration" in s
    # forbids citing the input filenames in the deliverable
    assert "never to the input filename" in s
    # the report must start at the section header, nothing before it
    assert 'the first characters of your output must be the "what happened"' in s
    # framed as a seamless human-written deliverable
    assert "seamless" in s and "human-written coaching report" in s


def test_task_md_points_at_inputs():
    with build_workspace(make_recon_dict()) as ws:
        task = (Path(ws) / "TASK.md").read_text()
    assert "facts.json" in task
    assert "references/" in task
    assert "strategic_map.png" in task
    assert "WHAT HAPPENED" in task


# --------------------------------------------------------------------------- invocation shape
def test_agentic_argv_shape():
    fake = FakeRun(stdout=ok_json("WHAT HAPPENED\n- Opening: archers\n\nANALYSIS\nx"))
    with build_workspace(make_recon_dict(), make_candidates()) as ws:
        run_agentic_coach(ws, model="sonnet", claude_bin="claude", runner=fake)
        argv = fake.calls[0]["argv"]
        kwargs = fake.calls[0]["kwargs"]
    assert "-p" in argv
    assert "--output-format" in argv and "json" in argv
    # read-only tool allowlist present
    assert "Read" in argv and "Grep" in argv and "Glob" in argv
    # Write/Edit/Bash NOT present
    assert "Write" not in argv and "Edit" not in argv and "Bash" not in argv
    # system prompt injected
    assert "--append-system-prompt" in argv
    # cwd is the workspace
    assert kwargs["cwd"] == str(ws)
    # no bare WebFetch when web disabled
    assert not any("WebFetch" in a for a in argv)


def test_agentic_argv_web_domain_scoped():
    fake = FakeRun(stdout=ok_json("WHAT HAPPENED\n- Opening: archers\n\nANALYSIS\nx"))
    with build_workspace(make_recon_dict()) as ws:
        run_agentic_coach(ws, web_domain="age-of-empires-2.fandom.com", runner=fake)
        argv = fake.calls[0]["argv"]
    webfetch = [a for a in argv if "WebFetch" in a]
    assert webfetch == ["WebFetch(domain:age-of-empires-2.fandom.com)"]


# --------------------------------------------------------------------------- output capture
def test_run_agentic_captures_result_and_model():
    fake = FakeRun(stdout=ok_json("WHAT HAPPENED\n- Opening: Archers\n\nANALYSIS\nx", model="claude-sonnet-4-6"))
    with build_workspace(make_recon_dict()) as ws:
        text, model = run_agentic_coach(ws, runner=fake)
    assert "WHAT HAPPENED" in text
    assert model == "claude-sonnet-4-6"


def test_run_agentic_model_from_modelusage():
    payload = json.dumps({"result": "WHAT HAPPENED\nx", "modelUsage": {"claude-haiku-4-5": {}}})
    fake = FakeRun(stdout=payload)
    with build_workspace(make_recon_dict()) as ws:
        _text, model = run_agentic_coach(ws, runner=fake)
    assert model == "claude-haiku-4-5"


# --------------------------------------------------------------------------- opening parsing
def test_parse_opening_from_v2_bullet():
    assert parse_opening("WHAT HAPPENED\n- Opening: Fast Castle\nfoo") == "Fast Castle"


def test_parse_opening_legacy_line():
    assert parse_opening("OPENING: Scouts\nbody") == "Scouts"


def test_parse_opening_none():
    assert parse_opening("no opening here") == ""


# --------------------------------------------------------------------------- run_agentic failure modes
def test_run_agentic_nonzero_exit_raises():
    fake = FakeRun(stdout="", returncode=2, stderr="boom")
    with build_workspace(make_recon_dict()) as ws:
        with pytest.raises(RuntimeError, match="exited 2"):
            run_agentic_coach(ws, runner=fake)


def test_run_agentic_non_json_raises():
    fake = FakeRun(stdout="not json")
    with build_workspace(make_recon_dict()) as ws:
        with pytest.raises(RuntimeError, match="non-JSON"):
            run_agentic_coach(ws, runner=fake)


def test_run_agentic_is_error_raises():
    fake = FakeRun(stdout=json.dumps({"result": "x", "is_error": True}))
    with build_workspace(make_recon_dict()) as ws:
        with pytest.raises(RuntimeError, match="is_error"):
            run_agentic_coach(ws, runner=fake)


def test_run_agentic_empty_result_raises():
    fake = FakeRun(stdout=json.dumps({"result": "", "is_error": False}))
    with build_workspace(make_recon_dict()) as ws:
        with pytest.raises(RuntimeError, match="missing 'result'"):
            run_agentic_coach(ws, runner=fake)


# --------------------------------------------------------------------------- coach() v2 + degradation
def test_coach_v2_agentic_happy_path():
    fake = FakeRun(stdout=ok_json("WHAT HAPPENED\n- Opening: Archers\n\nANALYSIS\njudged"))
    out = coach(
        metrics={},
        salient_log="log",
        reconstruction=make_recon_dict(),
        candidates=make_candidates(),
        mistakes=make_flagged(),
        runner=fake,
    )
    assert isinstance(out, CoachOutput)
    assert out.tier == "agentic"
    assert out.opening_tag == "Archers"
    assert "ANALYSIS" in out.raw_text


def test_coach_v2_falls_back_to_facts_only(monkeypatch):
    # Agentic run fails (non-zero exit); the single-shot facts-only fallback is a SECOND mocked call.
    agentic = FakeRun(stdout="", returncode=1, stderr="no tools")
    fallback_calls = {}

    def fake_run_claude(prompt, **_kw):
        fallback_calls["prompt"] = prompt
        return "WHAT HAPPENED\n- Opening: scouts\n\nANALYSIS\nfallback", "claude-sonnet-4-6"

    monkeypatch.setattr(coach_mod, "run_claude_coach", fake_run_claude)
    out = coach(
        metrics={},
        salient_log="log",
        reconstruction=make_recon_dict(),
        candidates=make_candidates(),
        runner=agentic,
    )
    assert out.tier == "facts-only"
    assert out.model_used.endswith("+facts-only")
    assert out.opening_tag == "scouts"
    # the fallback prompt embedded the facts block
    assert "FACTS" in fallback_calls["prompt"]


def test_coach_v1_path_unchanged_when_no_reconstruction(monkeypatch):
    def fake_run_claude(prompt, **_kw):
        # v1 path uses the legacy embedded-benchmark prompt (METRICS SUMMARY block), not the v2 facts block.
        assert "METRICS SUMMARY" in prompt
        assert "FACTS (facts.json)" not in prompt
        return "OPENING: Scouts\n\nbody", "claude-sonnet-4-6"

    monkeypatch.setattr(coach_mod, "run_claude_coach", fake_run_claude)
    out = coach(metrics={"feudal_uptime_s": 500}, salient_log="x")
    assert out.tier == "v1"
    assert out.opening_tag == "Scouts"


# --------------------------------------------------------------------------- entrypoint v2 contract
def test_analyze_replay_v2_adds_facts_json(monkeypatch):
    """analyze_replay(use_v2=True) adds facts_json + coach_tier and preserves the legacy columns."""
    import aoe2coach.entrypoint as ep

    fake = FakeRun(stdout=ok_json("WHAT HAPPENED\n- Opening: Archers\n\nANALYSIS\nx"))

    real_coach = ep.coach
    monkeypatch.setattr(ep, "coach", lambda *a, **k: real_coach(*a, **{**k, "runner": fake}))

    # game1 real rec if present; else skip (no synthetic full-bundle here).
    rec_path = "/home/namle685/projects/aoe2coach-analysis/game.aoe2record"
    import os

    if not os.path.exists(rec_path):
        pytest.skip("calibration rec not present")
    row = ep.analyze_replay(rec_path, 14697894, use_v2=True)
    assert "facts_json" in row
    assert "coach_tier" in row
    assert json.loads(row["facts_json"])["meta"]["map"]
    # legacy columns preserved
    for k in ("coach_output", "opening", "metrics_json", "salient_log", "game_result"):
        assert k in row
    assert row["coach_tier"] == "agentic"


# --------------------------------------------------------------------------- fallback prompt content
def test_build_coach_prompt_v2_embeds_facts():
    p = build_coach_prompt_v2(make_recon_dict(), "salient", candidates_md="cands")
    assert "Arabia" in p  # facts inlined
    assert "salient" in p
    assert "cands" in p
    assert "WHAT HAPPENED" in p  # carries the v2 contract
