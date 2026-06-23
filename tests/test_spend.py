"""Tests for the #2 spending model (`spend.py`): near-exact resource SPENDING from BUILD +
DE_QUEUE + RESEARCH commands.

Honesty: spending is NEAR-EXACT (it ignores cancels — a queued unit later cancelled still counts as
intent), and unknown ids contribute nothing (never fabricate a cost). The spending breakdown is the
trustworthy half of the floating heuristic (gathering INTENT vs SPENDING).
"""

from mgz.fast import Action

from aoe2coach import spend


def test_cost_lookup_known_and_unknown():
    # a Villager (83) costs 50 food.
    assert spend.unit_cost(83) == {"food": 50}
    # an Archer (4) costs wood + gold.
    c = spend.unit_cost(4)
    assert c["wood"] == 25 and c["gold"] == 45
    # unknown unit id -> empty cost (never fabricate).
    assert spend.unit_cost(999999) == {}
    # House (70) costs wood.
    assert spend.building_cost(70) == {"wood": 25}
    # unknown building -> empty.
    assert spend.building_cost(424242) == {}
    # Loom (22) tech cost.
    assert spend.tech_cost(22)  # non-empty
    assert spend.tech_cost(999999) == {}


def test_spent_by_resource_sums_build_queue_research():
    ops = [
        (10_000, Action.BUILD, {"player_id": 1, "building_id": 70}),  # House: 25 wood
        (20_000, Action.DE_QUEUE, {"player_id": 1, "unit_id": 83, "amount": 4}),  # 4 vils: 200 food
        (30_000, Action.DE_QUEUE, {"player_id": 1, "unit_id": 4, "amount": 2}),  # 2 archers: 50w 90g
        (40_000, Action.RESEARCH, {"player_id": 1, "technology_id": 22}),  # Loom
        (50_000, Action.BUILD, {"player_id": 2, "building_id": 70}),  # opp -> ignored
    ]
    out = spend.spent_by_resource(ops, player=1)
    assert out["food"] >= 200  # 4 vils + loom-ish
    assert out["wood"] >= 25 + 50  # house + 2 archers
    assert out["gold"] >= 90  # 2 archers
    # opponent's house not counted
    assert out["wood"] < 200


def test_spent_in_window_filters_by_time():
    ops = [
        (10_000, Action.DE_QUEUE, {"player_id": 1, "unit_id": 83, "amount": 2}),  # 100 food @10s
        (120_000, Action.DE_QUEUE, {"player_id": 1, "unit_id": 83, "amount": 2}),  # 100 food @120s
        (300_000, Action.DE_QUEUE, {"player_id": 1, "unit_id": 83, "amount": 2}),  # 100 food @300s
    ]
    win = spend.spent_in_window(ops, player=1, start_s=60, end_s=240)
    assert win["food"] == 100  # only the @120s queue falls in [60, 240]


def test_spent_by_resource_handles_amount_default_and_missing_fields():
    ops = [
        (10_000, Action.DE_QUEUE, {"player_id": 1, "unit_id": 83}),  # no amount -> 1
        (20_000, Action.BUILD, {"player_id": 1}),  # no building_id -> skipped, no crash
        (30_000, Action.RESEARCH, {"player_id": 1}),  # no tech id -> skipped
    ]
    out = spend.spent_by_resource(ops, player=1)
    assert out["food"] == 50  # one villager


def test_spend_share_normalizes():
    sh = spend.spend_share({"food": 200, "wood": 100, "gold": 0, "stone": 0})
    assert abs(sum(sh.values()) - 1.0) < 1e-6
    assert sh["food"] > sh["wood"]
    # all-zero -> empty (no fabricated shares)
    assert spend.spend_share({"food": 0, "wood": 0, "gold": 0, "stone": 0}) == {}
