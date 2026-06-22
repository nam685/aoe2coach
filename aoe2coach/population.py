"""Population *capacity* reconstruction from pop-providing BUILD ops.

HONESTY: a replay logs commands, not state. We can know how much pop HOUSING a player BUILT
(exact, from House/TC/Castle BUILD ops), but NOT how many units were alive (deaths aren't logged).
So this module emits the *housed pop ceiling* the player constructed over time — an exact,
command-derived capacity curve — and never a live population. It is the denominator the
`*_produced` counts sit under, and it is clamped to the game POP_CAP (200).

Pure functions over ops; never raises on missing coords/ids.
"""

from mgz.fast import Action

from . import const


def pop_buildings(ops, player):
    """Pop-providing BUILD events for `player`, in time order.

    Each: {"name", "pop", "t_s"} where pop = const.POP_PER_BUILDING[name]. Only buildings that
    provide housing (House, Town Center, Castle) are included; production-only buildings give 0
    and are skipped.
    """
    out = []
    for t, action_type, data in ops:
        if action_type != Action.BUILD or data.get("player_id") != player:
            continue
        name = const.building_name(data.get("building_id"))
        pop = const.POP_PER_BUILDING.get(name)
        if pop:
            out.append({"name": name, "pop": pop, "t_s": t // 1000})
    return out


def housed_pop_ceiling(ops, player):
    """Total pop housing this player BUILT, clamped to POP_CAP.

    NOTE: this is a built-capacity ceiling, NOT live pop and NOT live housing (a destroyed house
    still counts here — we can't see deaths). The starting Town Center's pop is NOT in the command
    stream (it's placed pre-game in the header), so this counts only BUILT housing; the assembler
    documents that caveat. Returns an int in [0, POP_CAP].
    """
    total = sum(b["pop"] for b in pop_buildings(ops, player))
    return min(total, const.POP_CAP)


def pop_ceiling_steps(ops, player):
    """Cumulative housed-pop ceiling as a step function: [{"t_s", "ceiling"}], clamped to POP_CAP.

    One step per pop-providing BUILD, in time order; `ceiling` is the running clamped total. Useful
    for #5's capacity overlay. Empty if the player built no housing.
    """
    steps = []
    running = 0
    for b in pop_buildings(ops, player):
        running = min(running + b["pop"], const.POP_CAP)
        steps.append({"t_s": b["t_s"], "ceiling": running})
    return steps
