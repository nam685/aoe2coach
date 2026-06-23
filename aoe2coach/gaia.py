"""Starting GAIA object table -> resource classification (sub-project #2 foundation).

Pure helpers over `header["players"][0]["objects"]` (surfaced by #1 as `ParsedRec.gaia_objects`):
~4,560 starting map objects, each {class_id, object_id, instance_id, position, index}. #2 joins
GATHER_POINT.target_type (== gaia object_id) and ORDER.target_id (== gaia instance_id) against this
table to classify an assignment target as wood/food/gold/stone.

Honesty (verified empirically on game.aoe2record + game2.aoe2record):
- class_id 20 (object_id 1902): trees -> WOOD. Unambiguous.
- class_id 70: forageable wildlife (sheep/boar/deer/llama: 305, 822, 1963, 285, 812, ...) -> FOOD.
- class_id 10 is MIXED. Only a small curated set are genuine gatherable resources:
    66  -> gold mine  -> GOLD
    102 -> stone mine -> STONE
    1053/1059 -> forage/berry bush -> FOOD
  The high-count class-10 object_ids (1358/1348/2567/2570/1063/1349/1248/1359/348 ...) are map
  DECORATION/terrain clutter spread across the whole map center, NOT resources — verified by their
  position spread and counts (hundreds each, blanketing the centre). They resolve to None: a villager
  right-clicked onto terrain is a MOVE, not a gather assignment, and counting it would fabricate
  signal. class_id 30 (relics) is not eco -> None.

Every function never raises on unknown/missing input — returns None / {} so a future patch that
changes ids degrades gracefully (no fabricated resource).
"""

# Curated class-10 object_id -> resource (genuine gatherables only; everything else class-10 -> None).
_CLASS10_GOLD = {66}
_CLASS10_STONE = {102}
_CLASS10_FOOD = {1053, 1059}  # forage / berry bush variants


def gaia_objects(gaia_list):
    """Index the starting GAIA object list by instance_id -> object dict.

    `gaia_list` is `ParsedRec.gaia_objects` (header["players"][0]["objects"]). Returns {} for
    None/empty/non-list input. ORDER.target_id joins against this (target_id == instance_id).
    """
    if not isinstance(gaia_list, list):
        return {}
    out = {}
    for o in gaia_list:
        if isinstance(o, dict) and "instance_id" in o:
            out[o["instance_id"]] = o
    return out


def by_object_id(gaia_list):
    """Index the GAIA object list by object_id -> object dict (first wins).

    GATHER_POINT.target_type joins against this (target_type == object_id). Returns {} on bad input.
    """
    if not isinstance(gaia_list, list):
        return {}
    out = {}
    for o in gaia_list:
        if isinstance(o, dict) and "object_id" in o:
            out.setdefault(o["object_id"], o)
    return out


def resource_points(gaia_list):
    """List of (resource, x, y) for every GAIA object that classifies to a resource.

    Used by the #2 economy model to resolve a gather-point x/y to the nearest resource (villagers are
    gather-pointed to a Lumber/Mining camp near trees/mines, not re-clicked onto the resource itself).
    Skips objects with no position. Returns [] on bad input; never raises.
    """
    if not isinstance(gaia_list, list):
        return []
    out = []
    for o in gaia_list:
        if not isinstance(o, dict):
            continue
        rc = resource_class(o)
        if rc is None:
            continue
        pos = o.get("position")
        if isinstance(pos, dict) and "x" in pos and "y" in pos:
            out.append((rc, pos["x"], pos["y"]))
    return out


def nearest_resource(resource_points_list, x, y, classes=None):
    """Nearest resource to (x, y) among `resource_points_list` (from resource_points()).

    Returns (resource, distance). `classes`, if given, restricts to those resource families (e.g.
    {"gold","stone"} for a Mining Camp). Returns (None, inf) if no candidate. Never raises.
    """
    best = None
    best_d2 = float("inf")
    for rc, ox, oy in resource_points_list:
        if classes is not None and rc not in classes:
            continue
        d2 = (ox - x) ** 2 + (oy - y) ** 2
        if d2 < best_d2:
            best_d2 = d2
            best = rc
    return best, (best_d2**0.5 if best is not None else float("inf"))


def resource_class(gaia_obj):
    """Classify a GAIA object to "food"|"wood"|"gold"|"stone" or None (not a gatherable resource).

    Never raises: None/empty/unknown -> None. Class 20 -> wood; class 70 -> food; class 10 only via
    the curated id sets (gold/stone/berry); everything else (decoration, relics, buildings) -> None.
    """
    if not isinstance(gaia_obj, dict):
        return None
    cid = gaia_obj.get("class_id")
    oid = gaia_obj.get("object_id")
    if cid == 20:
        return "wood"
    if cid == 70:
        return "food"
    if cid == 10:
        if oid in _CLASS10_GOLD:
            return "gold"
        if oid in _CLASS10_STONE:
            return "stone"
        if oid in _CLASS10_FOOD:
            return "food"
    return None
