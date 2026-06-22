"""Pillow renderer: a geometry.MapLayout -> a strategic-map PNG (ME blue / OPP red) + a legend.

Engagement-TRIGGERED: `render_maps` writes one OVERALL layout plus one SNAPSHOT per detected
engagement (the map as it stood when that aggressive-command activity happened), so the coach can
see how the front developed. All drawing is here; geometry.py stays pure and image-free.

HONESTY: bases / forward buildings / walls / aggressive-command activity / direction arrows only —
operational macro, never tactical micro or casualty claims.
"""

import math
import os

from PIL import Image, ImageDraw

from . import geometry

# --- palette (military-map flavor) ---
BG = (18, 22, 28)
GRID = (38, 44, 52)
FG = (210, 216, 224)
MUTED = (120, 130, 142)
ME = (66, 135, 245)  # blue
ME_DIM = (40, 70, 120)
OPP = (232, 72, 72)  # red
OPP_DIM = (120, 44, 44)
ENGAGE = (240, 196, 64)  # amber — aggressive-command activity
FORWARD = (180, 120, 245)  # violet — forward/proxy buildings


def _side_colors(side):
    if side == "me":
        return ME, ME_DIM
    if side == "opp":
        return OPP, OPP_DIM
    return MUTED, MUTED


def _grid(draw, lay):
    """Light tile grid every ~20 game tiles, for spatial reference."""
    step_tiles = 20
    n = max(1, int(lay.map_dim // step_tiles))
    for i in range(n + 1):
        g = i * step_tiles
        x0, y0 = geometry.project_point(g, 0, lay.map_dim, lay.img_size, lay.margin)
        x1, y1 = geometry.project_point(g, lay.map_dim, lay.map_dim, lay.img_size, lay.margin)
        draw.line([x0, y0, x1, y1], fill=GRID, width=1)
        hx0, hy0 = geometry.project_point(0, g, lay.map_dim, lay.img_size, lay.margin)
        hx1, hy1 = geometry.project_point(lay.map_dim, g, lay.map_dim, lay.img_size, lay.margin)
        draw.line([hx0, hy0, hx1, hy1], fill=GRID, width=1)


def _dot(draw, m, r, fill, outline=None):
    draw.ellipse([m.px - r, m.py - r, m.px + r, m.py + r], fill=fill, outline=outline, width=2 if outline else 1)


def _arrow(draw, a, color):
    """Draw a tail->head line with a small arrowhead at the head."""
    draw.line([a.x0, a.y0, a.x1, a.y1], fill=color, width=2)
    ang = math.atan2(a.y1 - a.y0, a.x1 - a.x0)
    head = 10
    spread = math.radians(26)
    for s in (+1, -1):
        hx = a.x1 - head * math.cos(ang + s * spread)
        hy = a.y1 - head * math.sin(ang + s * spread)
        draw.line([a.x1, a.y1, hx, hy], fill=color, width=2)


def _legend(draw, lay):
    """Small key in the top-left corner."""
    items = [
        (ME, "ME (blue)"),
        (OPP, "OPP (red)"),
        (FORWARD, "forward bldg"),
        (ENGAGE, "engagement"),
    ]
    x, y = lay.margin + 4, lay.margin + 4
    box = 9
    for color, label in items:
        draw.rectangle([x, y, x + box, y + box], fill=color)
        draw.text((x + box + 6, y - 1), label, fill=FG)
        y += box + 6


def render_layout(lay, out_path, title=""):
    """Render a single MapLayout to a PNG at `out_path` (size = lay.img_size square)."""
    img = Image.new("RGB", (lay.img_size, lay.img_size), BG)
    draw = ImageDraw.Draw(img)

    # playable-area border + grid
    m, s = lay.margin, lay.img_size
    draw.rectangle([m, m, s - m, s - m], outline=GRID, width=1)
    _grid(draw, lay)

    # walls (thin lines, dim side color)
    for w in lay.me_walls:
        draw.line(list(w.points), fill=ME_DIM, width=3)
    for w in lay.opp_walls:
        draw.line(list(w.points), fill=OPP_DIM, width=3)

    # buildings (small dim dots)
    for b in lay.me_buildings:
        _dot(draw, b, 2, ME_DIM)
    for b in lay.opp_buildings:
        _dot(draw, b, 2, OPP_DIM)

    # direction arrows (drawn under bases/engagements)
    for a in lay.arrows:
        _arrow(draw, a, ME if a.side == "me" else OPP)

    # forward buildings (highlighted)
    for f in lay.forward_buildings:
        _dot(draw, f, 5, FORWARD, outline=FG)

    # engagement markers (amber, sized by command volume), zone label
    for e in lay.engagements:
        n = e.n_commands or 1
        r = 6 + min(10, int(n))
        _dot(draw, e, r, ENGAGE, outline=FG)
        if e.zone:
            draw.text((e.px + r + 2, e.py - 6), e.zone, fill=ENGAGE)

    # bases (large bright dots, labeled)
    for base, label in ((lay.me_base, "ME"), (lay.opp_base, "OPP")):
        if base is None:
            continue
        bright, _ = _side_colors(base.side)
        _dot(draw, base, 9, bright, outline=FG)
        draw.text((base.px + 12, base.py - 6), label, fill=bright)

    _legend(draw, lay)

    # title + subtitle
    head = title or f"{lay.map_name} — strategic map"
    if lay.at_s is not None:
        head = f"{head}  @ {lay.at_s // 60}:{lay.at_s % 60:02d}"
    draw.text((m, 6), head, fill=FG)
    draw.text((s - 250, s - 16), "operational macro only — not unit micro", fill=MUTED)

    img.save(out_path, "PNG")
    return out_path


def render_maps(reconstruction, out_dir, prefix="map", img_size=600, margin=30):
    """Render the OVERALL layout + one SNAPSHOT per detected engagement. Returns the PNG paths.

    `reconstruction` is the dict from `reconstruct(rec).to_dict()`. Files:
      <prefix>_overall.png, then <prefix>_eng01.png, <prefix>_eng02.png, ... (chronological).
    """
    os.makedirs(out_dir, exist_ok=True)
    paths = []

    overall = geometry.layout(reconstruction, img_size=img_size, margin=margin)
    overall_path = os.path.join(out_dir, f"{prefix}_overall.png")
    render_layout(overall, overall_path, title=f"{overall.map_name} — overall")
    paths.append(overall_path)

    engs = (reconstruction.get("combat", {}) or {}).get("me", {}).get("engagements", []) or []
    engs = sorted(engs, key=lambda e: e.get("start_s") or 0)
    for i, e in enumerate(engs, start=1):
        snap = geometry.layout(reconstruction, img_size=img_size, margin=margin, at_s=e.get("start_s"))
        p = os.path.join(out_dir, f"{prefix}_eng{i:02d}.png")
        render_layout(snap, p, title=f"{snap.map_name} — engagement {i} ({e.get('zone', '')})")
        paths.append(p)

    return paths
