"""Spatial reconstruction from BUILD / WALL ops + header start positions.

All coords are AoE2 map tile units (0..map_dimension, typically 0..120). Every function guards
missing / zero / non-numeric coords and never raises. Pure functions over
`ops: list[(clock_ms, action_type, data)]`.

Honesty: these are *placements* (where things were built), an exact, command-derived fact — NOT
live state. A razed building still appears (deaths aren't logged). The assembler labels them so.
"""

from mgz.fast import Action

from . import const

# A military building is "forward" if it sits farther than this many tiles from the player's own
# base centroid. Calibrated loosely on Arabia (map_dim 120, bases ~75 tiles apart); a barracks in
# your own base is ~5-15 tiles from centroid, a proxy/forward building 30+. Tunable.
FORWARD_DIST = 30.0


def _coord(v):
    """Return a float coord or None for missing/non-numeric values. Zeroes are kept (valid tile)."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    return None


def _xy(data):
    """Extract a (x, y) tuple of floats from an op's data, or None if either is missing."""
    x = _coord(data.get("x"))
    y = _coord(data.get("y"))
    if x is None or y is None:
        return None
    return (x, y)


def buildings(ops, player):
    """All BUILD placements for `player`, in command (time) order.

    Each entry: {"name", "building_id", "x", "y", "t_s"}. Ops with missing coords are skipped
    (they can't be placed on the map), but never raise.
    """
    out = []
    for t, action_type, data in ops:
        if action_type != Action.BUILD or data.get("player_id") != player:
            continue
        xy = _xy(data)
        if xy is None:
            continue
        out.append(
            {
                "name": const.building_name(data.get("building_id")),
                "building_id": data.get("building_id"),
                "x": xy[0],
                "y": xy[1],
                "t_s": t // 1000,
            }
        )
    return out


def walls(ops, player):
    """All WALL segments for `player`. Each: {"x", "y", "x_end", "y_end", "name", "t_s"}.

    Skips segments missing any of the four endpoints. Never raises.
    """
    out = []
    for t, action_type, data in ops:
        if action_type != Action.WALL or data.get("player_id") != player:
            continue
        x = _coord(data.get("x"))
        y = _coord(data.get("y"))
        x_end = _coord(data.get("x_end"))
        y_end = _coord(data.get("y_end"))
        if None in (x, y, x_end, y_end):
            continue
        out.append(
            {
                "x": x,
                "y": y,
                "x_end": x_end,
                "y_end": y_end,
                "name": const.building_name(data.get("building_id")),
                "t_s": t // 1000,
            }
        )
    return out


def base_centroid(ops, player, blds=None):
    """Centroid (mean x, mean y) of the player's NON-military, NON-wall buildings.

    Eco/economic buildings (TC, houses, farms, mills, camps) define "home"; military buildings
    can be forward and would drag the centroid toward the front. Returns {"x", "y"} or None when
    the player placed no usable eco building (e.g. all coords missing). `blds` may be passed to
    avoid recomputing buildings(ops, player).
    """
    blds = buildings(ops, player) if blds is None else blds
    eco = [b for b in blds if b["name"] not in const.MILITARY_BUILDINGS]
    base = eco if eco else blds  # fall back to all buildings if the player built no eco building
    if not base:
        return None
    n = len(base)
    return {"x": sum(b["x"] for b in base) / n, "y": sum(b["y"] for b in base) / n}


def _dist(ax, ay, bx, by):
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5


def forward_buildings(ops, player, centroid=None, blds=None, threshold=FORWARD_DIST):
    """Military buildings placed farther than `threshold` tiles from the player's own centroid.

    These are proxy / forward-aggression structures. Returns a list shaped like buildings() with an
    extra "dist" field. Empty if no centroid (can't measure) or none qualify.
    """
    blds = buildings(ops, player) if blds is None else blds
    centroid = base_centroid(ops, player, blds=blds) if centroid is None else centroid
    if centroid is None:
        return []
    out = []
    for b in blds:
        if b["name"] not in const.MILITARY_BUILDINGS:
            continue
        d = _dist(b["x"], b["y"], centroid["x"], centroid["y"])
        if d > threshold:
            out.append({**b, "dist": round(d, 1)})
    return out


def start_position(header_player):
    """Starting position {"x","y"} for a header player dict, or None. header["players"][n] carries
    a "position" dict {x, y} (the player's starting TC). Used as the opponent-base reference."""
    if not isinstance(header_player, dict):
        return None
    pos = header_player.get("position")
    if not isinstance(pos, dict):
        return None
    x = _coord(pos.get("x"))
    y = _coord(pos.get("y"))
    if x is None or y is None:
        return None
    return {"x": x, "y": y}


def eco_exposure(my_centroid, opp_centroid, blds):
    """Classify each economic building as "front" or "safe" along the me->opp axis.

    Project each eco building onto the unit vector from my base toward the opponent's base. A
    building whose projection is past the MIDPOINT (closer to the opponent than to me along the
    axis) is "front"; otherwise "safe". This is the honest, opponent-relative exposure signal
    downstream #5 needs. Returns:
      {"front": [building...], "safe": [building...], "axis_len": float}
    Degrades gracefully: if either centroid is missing, everything is "safe" and axis_len is None
    (we can't measure exposure without both bases).
    """
    eco = [b for b in blds if b["name"] not in const.MILITARY_BUILDINGS]
    if my_centroid is None or opp_centroid is None:
        return {"front": [], "safe": list(eco), "axis_len": None}
    mx, my = my_centroid["x"], my_centroid["y"]
    ox, oy = opp_centroid["x"], opp_centroid["y"]
    dx, dy = ox - mx, oy - my
    axis_len = (dx * dx + dy * dy) ** 0.5
    if axis_len == 0:
        # Bases coincide (degenerate); cannot define an axis.
        return {"front": [], "safe": list(eco), "axis_len": 0.0}
    front, safe = [], []
    half = axis_len / 2.0
    for b in eco:
        # Signed projection of (building - me) onto the me->opp unit axis, in tiles.
        proj = ((b["x"] - mx) * dx + (b["y"] - my) * dy) / axis_len
        item = {**b, "axis_proj": round(proj, 1)}
        (front if proj > half else safe).append(item)
    return {"front": front, "safe": safe, "axis_len": round(axis_len, 1)}
