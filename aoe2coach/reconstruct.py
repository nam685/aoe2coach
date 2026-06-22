"""The reconstruction assembler: parsed rec -> Reconstruction (the #1 anchor contract).

`reconstruct(rec)` ties the pure extractor modules into ONE JSON-serializable dict that every
downstream sub-project consumes. HARD RULE (program-wide): this core emits only EXACT,
command-derived facts. The only estimate-adjacent values carry the word "produced" in their key
(cumulative queued counts — an upper bound on live counts, never presented as live).

`rec` is a ParsedRec (from parser.parse_rec). Everything here is pure over rec.ops + rec header
fields already surfaced on ParsedRec (gaia_objects, start_positions, map_dim). No DB/network.
"""

from collections import defaultdict
from dataclasses import asdict, dataclass, field

from . import combat, efficiency, population, spatial
from .metrics import production_milestones
from .timeline import AGE_RESEARCH_MS, build_timeline

_AGE_KEYS = ("feudal", "castle", "imperial")


@dataclass
class Reconstruction:
    meta: dict = field(default_factory=dict)
    ages: dict = field(default_factory=dict)
    techs: dict = field(default_factory=dict)
    production: dict = field(default_factory=dict)
    counts: dict = field(default_factory=dict)
    spatial: dict = field(default_factory=dict)
    population: dict = field(default_factory=dict)
    combat: dict = field(default_factory=dict)
    efficiency: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)


def _arrival_s(click_ms, age):
    if click_ms is None:
        return None
    return (click_ms + AGE_RESEARCH_MS[age]) // 1000


def _ages(tl_me):
    up = tl_me["uptimes"]
    out = {}
    for age in _AGE_KEYS:
        click_ms = up[age]
        out[f"{age}_click_s"] = click_ms // 1000 if click_ms is not None else None
        out[f"{age}_arrival_s"] = _arrival_s(click_ms, age)
    return out


def _techs(tl):
    return {
        "eco": [{"name": e["name"], "t_s": e["t"] // 1000} for e in tl["eco_techs"]],
        "military": [{"name": e["name"], "t_s": e["t"] // 1000} for e in tl["military_techs"]],
        "university": [{"name": e["name"], "t_s": e["t"] // 1000} for e in tl["university_techs"]],
    }


def _counts(tl_me):
    """`produced` counts (cumulative queued) — an upper bound on live counts. LABELED produced."""
    army = defaultdict(int)
    villagers = 0
    for u in tl_me["units"]:
        if u["unit_id"] == 83:  # const.VILLAGER_ID
            villagers += u["amount"]
        else:
            army[u["name"]] += u["amount"]
    return {
        "villagers_produced": villagers,
        "army_produced": [{"name": n, "amount": a} for n, a in sorted(army.items(), key=lambda x: -x[1])],
    }


def _vils_at_feudal_click(tl_me):
    """Villagers PRODUCED (queued) by the Feudal *click* time — #3's build-classifier key signal.

    Counts villager DE_QUEUE amounts at or before the feudal click ms. None if no feudal click.
    Labeled `produced` semantics (queued, upper bound), exposed as a named int for #3.
    """
    click_ms = tl_me["uptimes"]["feudal"]
    if click_ms is None:
        return None
    return sum(u["amount"] for u in tl_me["units"] if u["unit_id"] == 83 and u["t"] <= click_ms)


def reconstruct(rec):
    """Assemble the Reconstruction object from a ParsedRec. Returns a Reconstruction dataclass
    (call .to_dict() for the JSON-serializable dict the downstream specs consume)."""
    ops = rec.ops
    duration_ms = rec.duration_ms
    me_num = rec.me["number"] if rec.me else None
    opp_num = rec.opponent["number"] if rec.opponent else None

    tl_me = build_timeline(ops, me_num) if me_num is not None else build_timeline([], -1)
    tl_opp = build_timeline(ops, opp_num) if opp_num is not None else None

    # --- meta ---
    meta = {
        "map": rec.map_name,
        "map_dim": rec.map_dim,
        "duration_s": duration_ms // 1000,
        "my_civ": rec.me["civ_name"] if rec.me else None,
        "opp_civ": rec.opponent["civ_name"] if rec.opponent else None,
        "result": rec.my_result,
        "is_ranked": rec.is_ranked,
        "is_1v1": rec.is_1v1,
        "opp_rating": None,  # not in the header command stream; relic-sourced later (not this core)
    }

    # --- ages (ME) + opp ages key ---
    ages = _ages(tl_me)
    if tl_opp is not None:
        ages["opp"] = _ages(tl_opp)

    # --- techs (ME) + opp key ---
    techs = _techs(tl_me)
    if tl_opp is not None:
        techs["opp"] = _techs(tl_opp)

    # --- production + milestones (ME) ---
    milestones_me = production_milestones(tl_me)
    production = {
        "produced_units": [
            {"name": u["name"], "unit_id": u["unit_id"], "amount": u["amount"], "t_s": u["t"] // 1000}
            for u in tl_me["units"]
        ],
        "milestones": {
            "first_military_building": milestones_me["first_military_building"],
            "first_military_building_s": (
                milestones_me["first_military_building"]["t_s"] if milestones_me["first_military_building"] else None
            ),
            "first_military_unit_s": milestones_me["first_military_unit_s"],
            "first_siege_s": milestones_me["first_siege_s"],
            "first_treb_s": milestones_me["first_treb_s"],
            "first_unit_s": milestones_me["first_unit_s"],
        },
        # #3 build-classifier key signal: villagers produced by the Feudal CLICK.
        "vils_at_feudal_click": _vils_at_feudal_click(tl_me),
    }

    # --- counts (LABELED produced) ---
    counts = _counts(tl_me)

    # --- spatial (ME full + OPP centroid & key buildings) ---
    me_blds = spatial.buildings(ops, me_num) if me_num is not None else []
    me_centroid = spatial.base_centroid(ops, me_num, blds=me_blds) if me_num is not None else None
    opp_blds = spatial.buildings(ops, opp_num) if opp_num is not None else []
    opp_centroid = spatial.base_centroid(ops, opp_num, blds=opp_blds) if opp_num is not None else None
    # Prefer the header start position as the opponent-base reference when available (it's the true
    # start TC, independent of how many buildings we saw); fall back to the building centroid.
    opp_start = spatial.start_position({"position": rec.start_positions.get(opp_num)}) if opp_num is not None else None
    opp_base_ref = opp_start or opp_centroid

    spatial_block = {
        "me": {
            "base_centroid": me_centroid,
            "buildings": me_blds,
            "forward": (
                spatial.forward_buildings(ops, me_num, centroid=me_centroid, blds=me_blds) if me_num is not None else []
            ),
            "walls": spatial.walls(ops, me_num) if me_num is not None else [],
            "eco_exposure": spatial.eco_exposure(me_centroid, opp_base_ref, me_blds),
        },
        "opp": {
            "base_centroid": opp_base_ref,
            "buildings": opp_blds,
            "walls": spatial.walls(ops, opp_num) if opp_num is not None else [],
        },
    }

    # --- population (built housing ceiling; NOT live pop) ---
    pop_block = {
        "me": {
            "housed_pop_ceiling": (population.housed_pop_ceiling(ops, me_num) if me_num is not None else 0),
            "pop_ceiling_steps": (population.pop_ceiling_steps(ops, me_num) if me_num is not None else []),
        },
        "note": "built housing capacity (House/TC/Castle BUILDs), clamped to POP_CAP; NOT live pop (deaths unlogged); excludes the pre-placed starting TC.",
    }

    # --- combat (zone-pinned aggressive-command activity; NOT casualties) ---
    combat_block = {
        "me": {
            "engagements": (combat.engagements(ops, me_num, me_centroid, opp_base_ref) if me_num is not None else []),
        },
        "note": "activity from aggressive-intent commands (attack-ground/attack-move/patrol), zone-pinned own_base|center|opp_base; replays log no combat/deaths.",
    }

    # --- efficiency (ME real APM + idle) ---
    eff = (
        efficiency.tc_idle(ops, me_num)
        if me_num is not None
        else {"tc_idle_s": 0, "longest_villager_gap_s": 0, "villager_gaps_s": []}
    )
    apm = efficiency.apm_split(ops, me_num, duration_ms) if me_num is not None else {}
    efficiency_block = {
        "tc_idle_s": eff["tc_idle_s"],
        "longest_villager_gap_s": eff["longest_villager_gap_s"],
        "villager_gaps_s": eff["villager_gaps_s"],
        "apm_total": apm.get("apm_total"),
        "apm_eco": apm.get("apm_eco"),
        "apm_military": apm.get("apm_military"),
    }

    return Reconstruction(
        meta=meta,
        ages=ages,
        techs=techs,
        production=production,
        counts=counts,
        spatial=spatial_block,
        population=pop_block,
        combat=combat_block,
        efficiency=efficiency_block,
    )
