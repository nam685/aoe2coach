"""Engagement reconstruction (best-effort, spatial-zone-pinned).

HONESTY: a replay does NOT log combat — no deaths, no hit events. What it DOES log is where a
player issued *aggressive movement* commands (ATTACK_GROUND, DE_ATTACK_MOVE, PATROL, and army
MOVE/ORDER). We treat clusters of such commands as "engagement activity" and pin each to a map
ZONE relative to the two bases. This is an activity heatmap, NOT a casualty report. Every datum is
labeled as derived from command locations, never as "a battle happened / who won".

The pinned zone enum is EXACTLY one of: "own_base" | "center" | "opp_base" (downstream contract).

Pure functions over ops + base centroids; never raises.
"""

from mgz.fast import Action

# Actions that indicate aggressive / combat-intent positioning. ATTACK_GROUND, DE_ATTACK_MOVE and
# PATROL are unambiguous combat intent. MOVE/ORDER carry an army position but are also used for eco;
# they're included only when explicitly requested (see military_only) — the assembler passes the
# clear-intent set by default to avoid eco contamination.
CLEAR_INTENT_ACTIONS = {Action.ATTACK_GROUND, Action.DE_ATTACK_MOVE, Action.PATROL}

# Zone enum — the ONLY allowed zone values (downstream contract).
ZONE_OWN = "own_base"
ZONE_CENTER = "center"
ZONE_OPP = "opp_base"
ZONES = (ZONE_OWN, ZONE_CENTER, ZONE_OPP)


def _coord(v):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    return None


def _xy(data):
    x = _coord(data.get("x"))
    y = _coord(data.get("y"))
    if x is None or y is None:
        return None
    return (x, y)


def pin_zone(x, y, my_centroid, opp_centroid):
    """Pin a map point to exactly one of own_base | center | opp_base.

    Projects the point onto the me->opp axis and splits it into thirds: the first third (nearest
    me) is own_base, the middle third is center, the last third (nearest opp) is opp_base. Points
    projecting before my base clamp to own_base; past the opponent clamp to opp_base. Returns
    ZONE_CENTER when the axis can't be defined (missing/coincident centroids) — the honest neutral.
    """
    if my_centroid is None or opp_centroid is None:
        return ZONE_CENTER
    mx, my = my_centroid["x"], my_centroid["y"]
    ox, oy = opp_centroid["x"], opp_centroid["y"]
    dx, dy = ox - mx, oy - my
    axis_len = (dx * dx + dy * dy) ** 0.5
    if axis_len == 0:
        return ZONE_CENTER
    # Fractional position along the axis, 0.0 at my base, 1.0 at opp base.
    frac = ((x - mx) * dx + (y - my) * dy) / (axis_len * axis_len)
    if frac < 1.0 / 3.0:
        return ZONE_OWN
    if frac < 2.0 / 3.0:
        return ZONE_CENTER
    return ZONE_OPP


def engagements(ops, player, my_centroid, opp_centroid, gap_s=30, intent_actions=None):
    """Cluster a player's aggressive-intent commands into engagement activity, zone-pinned.

    Consecutive in-zone commands within `gap_s` seconds collapse into one engagement. Each
    engagement: {"zone", "start_s", "end_s", "x", "y", "n_commands"} where (x, y) is the mean
    command location and `zone` is one of ZONES.

    `intent_actions` defaults to CLEAR_INTENT_ACTIONS (ATTACK_GROUND / DE_ATTACK_MOVE / PATROL) to
    avoid eco-MOVE contamination. Returns [] when no such commands exist.
    """
    intent = CLEAR_INTENT_ACTIONS if intent_actions is None else set(intent_actions)
    events = []
    for t, action_type, data in ops:
        if action_type not in intent or data.get("player_id") != player:
            continue
        xy = _xy(data)
        if xy is None:
            continue
        zone = pin_zone(xy[0], xy[1], my_centroid, opp_centroid)
        events.append((t // 1000, zone, xy[0], xy[1]))
    events.sort(key=lambda e: e[0])

    out = []
    cur = None
    for s, zone, x, y in events:
        if cur is not None and zone == cur["zone"] and (s - cur["end_s"]) <= gap_s:
            cur["end_s"] = s
            cur["_xs"].append(x)
            cur["_ys"].append(y)
            cur["n_commands"] += 1
        else:
            if cur is not None:
                out.append(_finalize(cur))
            cur = {"zone": zone, "start_s": s, "end_s": s, "_xs": [x], "_ys": [y], "n_commands": 1}
    if cur is not None:
        out.append(_finalize(cur))
    return out


def _finalize(cur):
    n = len(cur["_xs"])
    return {
        "zone": cur["zone"],
        "start_s": cur["start_s"],
        "end_s": cur["end_s"],
        "x": round(sum(cur["_xs"]) / n, 1),
        "y": round(sum(cur["_ys"]) / n, 1),
        "n_commands": cur["n_commands"],
    }
