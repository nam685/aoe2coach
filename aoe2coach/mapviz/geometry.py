"""Pure geometry for the strategic map — game coords -> image coords + scene layout.

NO image I/O lives here; everything is plain data so it is fully unit-testable. render.py consumes
the `MapLayout` this module produces and draws it with Pillow.

AoE2 map coordinates are tile units in [0, map_dim] with the origin at the TOP-LEFT and y growing
DOWNWARD — the same orientation as image pixels — so the projection is a straight uniform scale
into a padded square box (no y-flip). Coords are clamped into the box so a stray out-of-map command
never draws outside the image.

Convention: ME = blue, OPP = red (side is "me" / "opp" on every placed element).
"""

from dataclasses import dataclass, field

DEFAULT_MAP_DIM = 120  # Arabia-sized fallback when the rec doesn't surface a map dimension.


# --------------------------------------------------------------------------- projection


def _safe_dim(map_dim):
    """A usable, positive map dimension. Guards None / 0 / non-numeric -> DEFAULT_MAP_DIM."""
    if isinstance(map_dim, (int, float)) and not isinstance(map_dim, bool) and map_dim > 0:
        return float(map_dim)
    return float(DEFAULT_MAP_DIM)


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def project_point(x, y, map_dim, img_size, margin):
    """Project a game tile coord (x, y) to image pixels (px, py), clamped into the padded box.

    The usable box is [margin, img_size - margin] on both axes; game [0, map_dim] maps onto it with
    a single uniform scale. Returns floats. Origin top-left, no y-flip (AoE2 y already grows down).
    """
    dim = _safe_dim(map_dim)
    box = float(img_size) - 2.0 * float(margin)
    scale = box / dim
    px = float(margin) + float(x) * scale
    py = float(margin) + float(y) * scale
    lo, hi = float(margin), float(img_size) - float(margin)
    return (_clamp(px, lo, hi), _clamp(py, lo, hi))


def project_segment(x0, y0, x1, y1, map_dim, img_size, margin):
    """Project a wall segment's two endpoints; returns (px0, py0, px1, py1)."""
    a = project_point(x0, y0, map_dim, img_size, margin)
    b = project_point(x1, y1, map_dim, img_size, margin)
    return (a[0], a[1], b[0], b[1])


# --------------------------------------------------------------------------- scene elements


@dataclass
class Marker:
    """A placed point element (base / building / engagement)."""

    side: str  # "me" | "opp" | "neutral"
    name: str
    px: float
    py: float
    kind: str = "building"  # "base" | "building" | "forward" | "engagement"
    # engagement-only extras (None for other kinds):
    zone: str | None = None
    n_commands: int | None = None


@dataclass
class WallSeg:
    side: str
    points: tuple  # (px0, py0, px1, py1)


@dataclass
class Arrow:
    """A direction arrow (tail -> head), e.g. my base -> an engagement or forward building."""

    side: str
    x0: float
    y0: float
    x1: float
    y1: float
    label: str = ""


@dataclass
class MapLayout:
    img_size: int
    margin: int
    map_dim: int
    map_name: str
    at_s: int | None = None  # snapshot time (None = overall layout)
    me_base: Marker | None = None
    opp_base: Marker | None = None
    me_buildings: list = field(default_factory=list)
    opp_buildings: list = field(default_factory=list)
    forward_buildings: list = field(default_factory=list)
    me_walls: list = field(default_factory=list)
    opp_walls: list = field(default_factory=list)
    engagements: list = field(default_factory=list)
    arrows: list = field(default_factory=list)


def _centroid(side_block):
    c = side_block.get("base_centroid")
    if not isinstance(c, dict):
        return None
    x, y = c.get("x"), c.get("y")
    if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
        return None
    return (float(x), float(y))


def _visible(buildings, at_s):
    """Buildings placed at or before `at_s` (snapshot framing). All of them when at_s is None."""
    if at_s is None:
        return list(buildings)
    return [b for b in buildings if (b.get("t_s") or 0) <= at_s]


def layout(reconstruction, img_size=600, margin=30, at_s=None):
    """Lay out a Reconstruction dict into a `MapLayout` of projected, side-colored scene elements.

    `reconstruction` is the dict from `reconstruct(rec).to_dict()`. `at_s`, when given, frames a
    SNAPSHOT: only buildings placed by then and engagements active by then are included (used to
    render a map at each detected engagement). Degrades gracefully — missing bases simply drop the
    base markers and arrows; nothing raises.
    """
    meta = reconstruction.get("meta", {}) or {}
    sp = reconstruction.get("spatial", {}) or {}
    me_sp = sp.get("me", {}) or {}
    opp_sp = sp.get("opp", {}) or {}
    map_dim = meta.get("map_dim")
    map_name = meta.get("map") or ""

    def pp(x, y):
        return project_point(x, y, map_dim, img_size, margin)

    lay = MapLayout(
        img_size=img_size,
        margin=margin,
        map_dim=int(_safe_dim(map_dim)),
        map_name=map_name,
        at_s=at_s,
    )

    # --- bases ---
    me_c = _centroid(me_sp)
    opp_c = _centroid(opp_sp)
    if me_c is not None:
        px, py = pp(*me_c)
        lay.me_base = Marker(side="me", name="Base", px=px, py=py, kind="base")
    if opp_c is not None:
        px, py = pp(*opp_c)
        lay.opp_base = Marker(side="opp", name="Base", px=px, py=py, kind="base")

    # --- buildings (time-filtered for snapshots) ---
    for b in _visible(me_sp.get("buildings", []), at_s):
        px, py = pp(b.get("x", 0), b.get("y", 0))
        lay.me_buildings.append(Marker(side="me", name=b.get("name", "?"), px=px, py=py))
    for b in _visible(opp_sp.get("buildings", []), at_s):
        px, py = pp(b.get("x", 0), b.get("y", 0))
        lay.opp_buildings.append(Marker(side="opp", name=b.get("name", "?"), px=px, py=py))

    # --- forward buildings (ME only — the honest aggression signal #1 computes) ---
    for b in _visible(me_sp.get("forward", []), at_s):
        px, py = pp(b.get("x", 0), b.get("y", 0))
        lay.forward_buildings.append(Marker(side="me", name=b.get("name", "?"), px=px, py=py, kind="forward"))

    # --- walls ---
    for w in _visible(me_sp.get("walls", []), at_s):
        lay.me_walls.append(
            WallSeg(
                side="me",
                points=project_segment(
                    w.get("x", 0), w.get("y", 0), w.get("x_end", 0), w.get("y_end", 0), map_dim, img_size, margin
                ),
            )
        )
    for w in _visible(opp_sp.get("walls", []), at_s):
        lay.opp_walls.append(
            WallSeg(
                side="opp",
                points=project_segment(
                    w.get("x", 0), w.get("y", 0), w.get("x_end", 0), w.get("y_end", 0), map_dim, img_size, margin
                ),
            )
        )

    # --- engagements (ME aggressive-command activity, zone-pinned) ---
    engs = (reconstruction.get("combat", {}) or {}).get("me", {}).get("engagements", []) or []
    if at_s is not None:
        engs = [e for e in engs if (e.get("start_s") or 0) <= at_s]
    for e in engs:
        px, py = pp(e.get("x", 0), e.get("y", 0))
        lay.engagements.append(
            Marker(
                side="me",
                name="engagement",
                px=px,
                py=py,
                kind="engagement",
                zone=e.get("zone"),
                n_commands=e.get("n_commands"),
            )
        )

    # --- direction arrows from my base toward engagements (attack direction) + forward buildings.
    if lay.me_base is not None:
        bx, by = lay.me_base.px, lay.me_base.py
        for m in lay.engagements:
            lay.arrows.append(Arrow(side="me", x0=bx, y0=by, x1=m.px, y1=m.py, label=m.zone or ""))
        for f in lay.forward_buildings:
            lay.arrows.append(Arrow(side="me", x0=bx, y0=by, x1=f.px, y1=f.py, label="forward"))

    return lay
