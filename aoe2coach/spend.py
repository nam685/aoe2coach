"""NEAR-EXACT resource SPENDING model (sub-project #2, the trustworthy half of the floating signal).

A replay logs the COMMANDS that spend resources — BUILD (a building placed), DE_QUEUE (a unit queued)
and RESEARCH (a tech started). Their costs are FIXED, published game data, so summing them gives a
near-exact "resources spent" timeline WITHOUT any of the estimation error that plagues the
gather/collected model (which has to guess villager-per-resource and effective rates).

NEAR-EXACT, with one honest caveat: this counts the cost at the moment of the COMMAND and ignores
CANCELS (a queued unit later cancelled, or a building deleted before completion, refunds resources we
still count as spent). Cancels are rare in practice and not separable from the command log, so the
spending total is a slight OVER-count of net spend — acceptable, and labeled `estimate` downstream.
Civ cost discounts (e.g. cheaper techs) are NOT modeled — the base dat costs are used. Unknown ids
contribute nothing (never fabricate a cost).

This is the SPENDING side of the floating heuristic: compare the worker-allocation SHARE on resource R
(gathering INTENT, from econ.py) against R's share of SPENDING (here). A sustained excess of intent
over spend on R = "floating R". We never report absolute bank totals — only these two SHARES.

Pure functions over `ops`. No DB/network/IO. Never raises on missing fields.
"""

from mgz.fast import Action

_RESOURCES = ("food", "wood", "gold", "stone")

# --- Unit costs (unit_id -> {resource: amount}). Base AoE2 DE dat costs for the units const names.
# Only the trainable line-base + common uniques we surface; elite/upgrade variants share their base
# cost. Source: aoe2techtree community data. Missing ids resolve to {} (no fabricated cost).
UNIT_COSTS = {
    83: {"food": 50},  # Villager
    13: {"wood": 75},  # Fishing Ship
    4: {"wood": 25, "gold": 45},  # Archer
    24: {"wood": 25, "gold": 45},  # Crossbowman
    492: {"wood": 25, "gold": 45},  # Arbalester
    7: {"food": 25, "wood": 35},  # Skirmisher
    6: {"food": 25, "wood": 35},  # Elite Skirmisher
    39: {"wood": 40, "gold": 60},  # Cavalry Archer
    474: {"wood": 40, "gold": 60},  # Heavy Cavalry Archer
    5: {"food": 45, "gold": 50},  # Hand Cannoneer
    74: {"food": 60, "gold": 20},  # Militia
    75: {"food": 60, "gold": 20},  # Man-at-Arms
    77: {"food": 60, "gold": 20},  # Long Swordsman
    473: {"food": 60, "gold": 20},  # Two-Handed Swordsman
    567: {"food": 60, "gold": 20},  # Champion
    93: {"food": 35, "wood": 25},  # Spearman
    358: {"food": 35, "wood": 25},  # Pikeman
    359: {"food": 35, "wood": 25},  # Halberdier
    448: {"food": 80},  # Scout Cavalry
    546: {"food": 80},  # Light Cavalry
    441: {"food": 80},  # Hussar
    38: {"food": 60, "gold": 75},  # Knight
    283: {"food": 60, "gold": 75},  # Cavalier
    569: {"food": 60, "gold": 75},  # Paladin
    329: {"food": 55, "gold": 60},  # Camel Rider
    330: {"food": 55, "gold": 60},  # Heavy Camel Rider
    207: {"food": 55, "gold": 60},  # Imperial Camel Rider
    751: {"food": 25, "gold": 20},  # Eagle Scout
    753: {"food": 25, "gold": 20},  # Eagle Warrior
    752: {"food": 25, "gold": 20},  # Elite Eagle Warrior
    125: {"gold": 100},  # Monk
    280: {"wood": 160, "gold": 135},  # Mangonel
    550: {"wood": 160, "gold": 135},  # Onager
    588: {"wood": 160, "gold": 135},  # Siege Onager
    279: {"wood": 75, "gold": 75},  # Scorpion
    542: {"wood": 75, "gold": 75},  # Heavy Scorpion
    36: {"wood": 225, "gold": 225},  # Bombard Cannon
    1258: {"wood": 160},  # Battering Ram
    422: {"wood": 160},  # Capped Ram
    548: {"wood": 160},  # Siege Ram
    42: {"wood": 200, "gold": 200},  # Trebuchet
    331: {"wood": 200, "gold": 200},  # Trebuchet (packed)
    440: {"food": 65, "gold": 20},  # Petard
}

# --- Building costs (building_id -> {resource: amount}). Base DE dat. A Farm is wood; a TC is
# wood+stone; etc. Reseeded farms each cost again (so a farm-heavy late game spends real wood).
BUILDING_COSTS = {
    70: {"wood": 25},  # House
    68: {"wood": 100},  # Mill
    50: {"wood": 60},  # Farm
    562: {"wood": 100},  # Lumber Camp
    584: {"wood": 100},  # Mining Camp
    109: {"wood": 275, "stone": 100},  # Town Center
    71: {"wood": 275, "stone": 100},  # Town Center (alt)
    621: {"wood": 275, "stone": 100},  # Town Center (DE)
    2556: {"wood": 275, "stone": 100},  # Settlement (TC-like)
    12: {"wood": 175},  # Barracks
    87: {"wood": 175},  # Archery Range
    101: {"wood": 175},  # Stable
    49: {"wood": 200},  # Siege Workshop
    103: {"wood": 150},  # Blacksmith
    84: {"wood": 175},  # Market
    104: {"wood": 175},  # Monastery
    209: {"wood": 200},  # University
    82: {"stone": 650},  # Castle
    1251: {"stone": 350, "wood": 0},  # Krepost
    45: {"wood": 150},  # Dock
    199: {"wood": 100},  # Fish Trap
    72: {"wood": 5},  # Palisade Wall
    490: {"wood": 5},  # Palisade Gate
    792: {"wood": 5},  # Palisade Gate
    117: {"stone": 5},  # Stone Wall
    155: {"stone": 5},  # Fortified Wall
    487: {"stone": 30},  # Gate
    79: {"wood": 25, "stone": 125},  # Watch Tower
    234: {"wood": 25, "stone": 125},  # Guard Tower
    235: {"wood": 25, "stone": 125},  # Keep
    236: {"stone": 100, "gold": 100},  # Bombard Tower
    598: {"wood": 25, "stone": 0},  # Outpost
    276: {"wood": 1000, "stone": 1000, "gold": 1000},  # Wonder
}

# --- Tech costs (technology_id -> {resource: amount}). Eco + military + university + age-ups we name
# in const. Base DE dat (food/gold heavy). Age-ups dominate mid-game gold/food spend, so they matter
# for the floating signal. Source: aoe2techtree community data.
TECH_COSTS = {
    # Age-ups
    101: {"food": 500},  # Feudal Age
    102: {"food": 800, "gold": 200},  # Castle Age
    103: {"food": 1000, "gold": 800},  # Imperial Age
    # Eco
    22: {"gold": 50},  # Loom
    14: {"food": 75},  # Horse Collar
    13: {"food": 125, "wood": 125},  # Heavy Plow
    12: {"food": 250, "wood": 250},  # Crop Rotation
    202: {"food": 100},  # Double-Bit Axe
    203: {"food": 150, "wood": 100},  # Bow Saw
    221: {"food": 300, "wood": 200},  # Two-Man Saw
    55: {"food": 100},  # Gold Mining
    182: {"food": 200, "gold": 150},  # Gold Shaft Mining
    278: {"food": 100},  # Stone Mining
    279: {"food": 200, "gold": 150},  # Stone Shaft Mining
    213: {"food": 175, "wood": 50},  # Wheelbarrow
    249: {"food": 300, "wood": 200},  # Hand Cart
    8: {"food": 200},  # Town Watch
    280: {"food": 300},  # Town Patrol
    39: {"food": 150, "gold": 150},  # Husbandry
    48: {"gold": 200},  # Caravan
    23: {"gold": 200},  # Coinage
    17: {"gold": 300},  # Banking
    65: {"food": 100, "wood": 100},  # Gillnets
    # Military (blacksmith + unit-line upgrades) — gold-heavy mid/late spend
    199: {"food": 100},  # Fletching
    200: {"food": 100, "gold": 100},  # Bodkin Arrow
    201: {"food": 175, "gold": 150},  # Bracer
    67: {"food": 150},  # Forging
    68: {"food": 220, "gold": 120},  # Iron Casting
    75: {"food": 275, "gold": 225},  # Blast Furnace
    211: {"food": 100},  # Padded Archer Armor
    212: {"food": 150, "gold": 150},  # Leather Archer Armor
    219: {"food": 250, "gold": 250},  # Ring Archer Armor
    74: {"food": 100},  # Scale Mail Armor
    76: {"food": 200, "gold": 100},  # Chain Mail Armor
    77: {"food": 300, "gold": 150},  # Plate Mail Armor
    81: {"food": 150},  # Scale Barding Armor
    82: {"food": 250, "gold": 150},  # Chain Barding Armor
    80: {"food": 350, "gold": 200},  # Plate Barding Armor
    435: {"food": 150, "gold": 100},  # Bloodlines
    315: {"food": 100},  # Squires
    716: {"food": 150, "gold": 50},  # Arson
    222: {"food": 100},  # Man-at-Arms
    207: {"food": 100, "gold": 40},  # Long Swordsman
    217: {"food": 300, "gold": 100},  # Two-Handed Swordsman
    264: {"food": 750, "gold": 350},  # Champion
    197: {"food": 215, "gold": 90},  # Pikeman
    429: {"food": 300, "gold": 600},  # Halberdier
    254: {"food": 200, "gold": 100},  # Eagle Warrior
    384: {"food": 800, "gold": 500},  # Elite Eagle Warrior
    98: {"food": 100, "gold": 50},  # Crossbowman
    100: {"food": 300, "gold": 150},  # Arbalester
    218: {"food": 230},  # Elite Skirmisher
    715: {"food": 500, "gold": 100},  # Imperial Skirmisher
    209: {"food": 600, "gold": 600},  # Heavy Cavalry Archer
    231: {"food": 300, "gold": 250},  # Thumb Ring
    437: {"food": 200, "gold": 200},  # Parthian Tactics
    420: {"food": 150},  # Light Cavalry
    428: {"food": 500, "gold": 600},  # Hussar
    265: {"food": 300, "gold": 300},  # Cavalier
    241: {"food": 325, "gold": 360},  # Heavy Camel Rider
    255: {"food": 300},  # Capped Ram
    257: {"food": 800, "gold": 200},  # Onager
    320: {"food": 1000, "gold": 300},  # Siege Ram
    321: {"food": 1450, "gold": 600},  # Siege Onager
    322: {"food": 1000, "wood": 1100},  # Heavy Scorpion
    408: {"food": 400, "wood": 400},  # Hoardings
    63: {"food": 150, "gold": 150},  # Conscription
    # University
    93: {"food": 300, "gold": 100},  # Ballistics
    47: {"food": 150, "stone": 100},  # Masonry
    194: {"food": 300, "stone": 200},  # Architecture
    50: {"food": 200, "stone": 100},  # Fortified Wall
    64: {"food": 200, "stone": 100},  # Murder Holes
    140: {"food": 100, "gold": 50},  # Guard Tower
    51: {"food": 500, "gold": 350},  # Keep
    377: {"food": 300, "wood": 200},  # Treadmill Crane
    380: {"food": 350, "gold": 100},  # Heated Shot
    379: {"food": 300, "gold": 200},  # Chemistry
    607: {"food": 800, "gold": 400},  # Bombard Tower
    608: {"food": 500, "gold": 600},  # Siege Engineers
}


def unit_cost(unit_id):
    """Cost dict for a unit_id, or {} if unknown (never fabricate)."""
    return dict(UNIT_COSTS.get(unit_id, {}))


def building_cost(building_id):
    """Cost dict for a building_id, or {} if unknown."""
    return dict(BUILDING_COSTS.get(building_id, {}))


def tech_cost(tech_id):
    """Cost dict for a technology_id, or {} if unknown."""
    return dict(TECH_COSTS.get(tech_id, {}))


def _accumulate(totals, cost, mult=1):
    for r, amt in cost.items():
        if r in _RESOURCES and amt:
            totals[r] += amt * mult


def spent_by_resource(ops, player, *, start_s=None, end_s=None):
    """Near-exact resources SPENT by `player` over `ops`, optionally limited to [start_s, end_s].

    Sums BUILD (building cost), DE_QUEUE (unit cost × amount) and RESEARCH (tech cost) commands. Ignores
    cancels (a slight over-count). Unknown ids contribute nothing. Returns {resource: int} over all four
    resources (zeros included). Pure; never raises on missing fields.
    """
    totals = dict.fromkeys(_RESOURCES, 0.0)
    for t, action_type, data in ops:
        if data.get("player_id") != player:
            continue
        t_s = t // 1000
        if start_s is not None and t_s < start_s:
            continue
        if end_s is not None and t_s > end_s:
            continue
        if action_type == Action.BUILD:
            _accumulate(totals, building_cost(data.get("building_id")))
        elif action_type == Action.DE_QUEUE:
            amount = int(data.get("amount", 1) or 1)
            _accumulate(totals, unit_cost(data.get("unit_id")), mult=amount)
        elif action_type == Action.RESEARCH:
            _accumulate(totals, tech_cost(data.get("technology_id")))
    return {r: int(round(v)) for r, v in totals.items()}


def spent_in_window(ops, player, start_s, end_s):
    """Resources spent by `player` in the time window [start_s, end_s] (seconds). Convenience wrapper."""
    return spent_by_resource(ops, player, start_s=start_s, end_s=end_s)


def spend_share(totals):
    """Per-resource SHARE of spending (fractions summing to 1), or {} if nothing was spent.

    Never fabricates a share when totals are all-zero (returns {} — the honest no-signal answer)."""
    grand = sum(totals.get(r, 0) for r in _RESOURCES)
    if grand <= 0:
        return {}
    return {r: totals.get(r, 0) / grand for r in _RESOURCES}


__all__ = [
    "UNIT_COSTS",
    "BUILDING_COSTS",
    "TECH_COSTS",
    "unit_cost",
    "building_cost",
    "tech_cost",
    "spent_by_resource",
    "spent_in_window",
    "spend_share",
]
