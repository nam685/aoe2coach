"""Tests for the #1 villager-production simulation (production.py).

Synthetic ops are (t_ms, Action, data) tuples faithful to mgz.fast.parse_action shapes. The model
simulates per-TC villager pops (a single Town Center pops one villager every ~25s), so the live
villager count is bounded by (#TCs)·(t / train_time) + starting — NEVER the cumulative queued sum
(which over-counts because players queue ahead of what one TC can physically produce).
"""

import os

import pytest
from mgz.fast import Action

from aoe2coach import production

REC_PATH = "/home/namle685/projects/aoe2coach-analysis/game.aoe2record"
REC2_PATH = "/home/namle685/projects/aoe2coach-analysis/game2.aoe2record"
RELIC_PROFILE_ID = 14697894

requires_rec = pytest.mark.skipif(not os.path.exists(REC_PATH), reason="calibration rec not present")
requires_rec2 = pytest.mark.skipif(not os.path.exists(REC2_PATH), reason="second rec not present")


def _vil(t_ms, player=1, amount=1):
    return (t_ms, Action.DE_QUEUE, {"player_id": player, "unit_id": 83, "amount": amount})


def _tc_build(t_ms, player=1, x=10.0, y=10.0):
    # 621 = DE alternate Town Center id (the one real recs emit).
    return (t_ms, Action.BUILD, {"player_id": player, "building_id": 621, "x": x, "y": y})


# --------------------------------------------------------------------- train_time_at
def test_train_time_default_is_25s():
    assert production.train_time_at("Britons", 0, {}) == pytest.approx(25.0)
    assert production.train_time_at(None, 999_000, {}) == pytest.approx(25.0)


def test_train_time_persians_faster_by_age():
    # Persian TC works +5/10/15/20% faster in Dark/Feudal/Castle/Imperial.
    ages = {"feudal_arrival_s": 600, "castle_arrival_s": 1200, "imperial_arrival_s": 2000}
    assert production.train_time_at("Persians", 0, ages) == pytest.approx(25.0 / 1.05)  # dark
    assert production.train_time_at("Persians", 600, ages) == pytest.approx(25.0 / 1.10)  # feudal
    assert production.train_time_at("Persians", 1200, ages) == pytest.approx(25.0 / 1.15)  # castle
    assert production.train_time_at("Persians", 2000, ages) == pytest.approx(25.0 / 1.20)  # imperial


# --------------------------------------------------------------------- simulate_villagers
def test_single_tc_serializes_queued_pops():
    # 5 villagers queued instantly at t=0; ONE TC can only pop them one-per-25s, not all at once.
    ops = [_vil(0) for _ in range(5)]
    sim = production.simulate_villagers(ops, player=1, civ="Britons", ages={}, tc_build_ids={621})
    # pop times: 25, 50, 75, 100, 125 s
    assert [round(p) for p in sim.pop_times_s] == [25, 50, 75, 100, 125]
    assert sim.starting == 3


def test_villagers_present_never_exceeds_single_tc_ceiling():
    # Queue 20 villagers all at t=0 with ONE TC. At t=100s only 4 can have popped (+3 start = 7).
    ops = [_vil(0) for _ in range(20)]
    sim = production.simulate_villagers(ops, player=1, civ="Britons", ages={}, tc_build_ids={621})
    assert sim.villagers_present(100) == 3 + 4  # floor(100/25)=4
    # ceiling: starting + n_tc * (t / train_time)
    assert sim.villagers_present(100) <= 3 + 1 * (100 / 25) + 1


def test_extra_tc_doubles_pop_rate_after_build():
    # 1 TC from t=0; a 2nd TC built at t=0 too -> two parallel production lines.
    ops = [_tc_build(0)] + [_vil(0) for _ in range(6)]
    sim = production.simulate_villagers(ops, player=1, civ="Britons", ages={}, tc_build_ids={621})
    # two TCs each pop one per 25s: pops at 25,25,50,50,75,75
    assert sorted(round(p) for p in sim.pop_times_s) == [25, 25, 50, 50, 75, 75]
    assert sim.villagers_present(50) == 3 + 4


def test_built_tc_only_produces_after_its_build_time():
    # 2nd TC built at t=100s; villagers queued at t=0 must pop on the FIRST tc until the 2nd exists.
    ops = [_tc_build(100_000)] + [_vil(0) for _ in range(3)]
    sim = production.simulate_villagers(ops, player=1, civ="Britons", ages={}, tc_build_ids={621})
    # only TC#1 available until 100s: pops at 25, 50, 75
    assert [round(p) for p in sim.pop_times_s] == [25, 50, 75]


def test_present_curve_is_monotonic_nondecreasing():
    ops = [_vil(i * 25_000) for i in range(10)]
    sim = production.simulate_villagers(ops, player=1, civ="Britons", ages={}, tc_build_ids={621})
    prev = 0
    for t in range(0, 300, 10):
        v = sim.villagers_present(t)
        assert v >= prev
        prev = v


# --------------------------------------------------------------------- real rec sanity
@requires_rec
def test_real_rec_vils_at_feudal_click_is_physically_possible():
    from aoe2coach.parser import parse_rec
    from aoe2coach.reconstruct import reconstruct

    rec = parse_rec(REC_PATH, RELIC_PROFILE_ID)
    r = reconstruct(rec).to_dict()
    vfc = r["production"]["vils_at_feudal_click"]
    # Was 26 (the queued over-count). One TC by feudal cannot have produced more than ~17 (+3 start).
    assert 18 <= vfc <= 21, f"vils_at_feudal_click={vfc} not in physically-possible 18-21 band"


@requires_rec
def test_real_rec_villager_curve_never_exceeds_production_ceiling():
    from aoe2coach.parser import parse_rec

    rec = parse_rec(REC_PATH, RELIC_PROFILE_ID)
    me = rec.me["number"]
    from aoe2coach.reconstruct import reconstruct

    r = reconstruct(rec).to_dict()
    ages = r["ages"]
    sim = production.simulate_villagers(
        rec.ops, player=me, civ=rec.me["civ_name"], ages=ages, tc_build_ids=production.TC_BUILDING_IDS
    )
    # sample the curve; at every t, present <= starting + n_active_tc * (t / min_train_time) + slack
    for t in range(60, rec.duration_ms // 1000, 120):
        present = sim.villagers_present(t)
        ntc = sim.n_tcs_active(t)
        ceiling = sim.starting + ntc * (t / production.MIN_TRAIN_TIME_S) + 1
        assert present <= ceiling, f"t={t}: present {present} > ceiling {ceiling} (ntc={ntc})"
