"""DE villager gather rates + economy-upgrade multipliers (sub-project #2, Tier-B estimate).

Pure lookup tables + `rate_at(resource, t_s, recon)`. Everything emitted by the economy model is an
`~estimate`; these rates are the calibration knobs. The base per-second work rates below are the
well-documented AoE2 DE villager work rates; the upgrade multipliers are the published bonuses.

SOURCES (web-confirmed at implementation time, 2026-06):
  - Base work rates (resources/second): community gather-rate tables — Wood 0.55, Gold 0.5175,
    Stone 0.5175, Forage(berries) 0.45, Hunt 0.4725, Farm ~0.42.
    https://forums.ageofempires.com/t/villager-resource-gather-rates/33158 ,
    https://www.aoe2database.com/gathering_rates/en
  - Wood: Double-Bit Axe +20%, Bow Saw +20%, Two-Man Saw +10%.
    https://liquipedia.net/ageofempires/Double-Bit_Axe (DBA +20% confirmed)
  - Gold: Gold Mining +15%, Gold Shaft Mining +15%.
    https://liquipedia.net/ageofempires/Gold_Mining (Gold Mining +15% confirmed)
  - Stone: Stone Mining +15%, Stone Shaft Mining +15% (mirror of gold line).
  - Wheelbarrow / Hand Cart: carry capacity + move speed. Modeled as a small *effective* throughput
    bump (less walking per trip), NOT the raw carry %, because the per-resource walk distance is
    unknown. Tunable calibration knob.
    https://ageofempires.fandom.com/wiki/Wheelbarrow_(Age_of_Empires_II)
  - Farm food upgrades (Horse Collar / Heavy Plow / Crop Rotation) raise the farm FOOD AMOUNT, not
    the per-second rate, so they are NOT rate multipliers here (the blended-food rate already folds
    in farm/hunt/berry; food collected is the least-trustworthy number per the spec).

These are pinned numbers; the calibration loop (against game.aoe2record) is allowed to tune them
ONCE, then subsequent games are held-out. Pure: no DB/network/IO.
"""

# Base per-second villager work rates by resource family. Food is a single blended rate (the
# assignment classifier only knows "food", not its source) — the spec flags food as least-trustworthy.
BASE_RATE_PER_S = {
    "wood": 0.55,
    "gold": 0.5175,
    "stone": 0.5175,
    # Blended food: between forage (0.45), hunt (0.4725) and farm (~0.42). Mid value, tunable.
    "food": 0.45,
}

# Economy upgrades that multiply a resource's gather RATE. tech name -> (resource, +fraction).
# Applied multiplicatively from the tech's timing onward (all timings are exact, from #1.techs).
RATE_UPGRADES = {
    "Double-Bit Axe": ("wood", 0.20),
    "Bow Saw": ("wood", 0.20),
    "Two-Man Saw": ("wood", 0.10),
    "Gold Mining": ("gold", 0.15),
    "Gold Shaft Mining": ("gold", 0.15),
    "Stone Mining": ("stone", 0.15),
    "Stone Shaft Mining": ("stone", 0.15),
}

# Wheelbarrow / Hand Cart apply to ALL resources (carry+move). Modeled as a modest effective
# throughput bump (not the raw carry %) — calibration knob. tech name -> +fraction (all resources).
CARRY_UPGRADES = {
    "Wheelbarrow": 0.05,
    "Hand Cart": 0.05,
}


def _eco_tech_times(recon):
    """{tech_name: t_s} for ME eco techs from a Reconstruction dict. Empty on missing keys."""
    techs = recon.get("techs", {}) if isinstance(recon, dict) else {}
    eco = techs.get("eco", []) or []
    out = {}
    for e in eco:
        name = e.get("name")
        t = e.get("t_s")
        if name is not None and t is not None and name not in out:
            out[name] = t
    return out


def rate_at(resource, t_s, recon):
    """Estimated per-second gather rate for `resource` at time `t_s`, given ME's eco-tech timings.

    Multiplies the base rate by every rate-upgrade and carry-upgrade already researched by t_s.
    Unknown resource -> 0.0 (never raises). `recon` is a Reconstruction dict (or anything with a
    .get-able "techs"->"eco" list of {name, t_s}); missing techs simply contribute no multiplier.
    """
    base = BASE_RATE_PER_S.get(resource)
    if base is None:
        return 0.0
    times = _eco_tech_times(recon)
    mult = 1.0
    for name, (res, frac) in RATE_UPGRADES.items():
        if res == resource and name in times and times[name] <= t_s:
            mult *= 1.0 + frac
    for name, frac in CARRY_UPGRADES.items():
        if name in times and times[name] <= t_s:
            mult *= 1.0 + frac
    return base * mult
