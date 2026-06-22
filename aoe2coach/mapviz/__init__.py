"""Sub-project #7: strategic map rendering.

Turns the #1 Reconstruction's spatial + combat facts into a simple military-style strategic map
PNG that becomes COACH INPUT — the LLM can't visualize building layouts or fights from text alone.

HONESTY BOUNDARY: this renders OPERATIONAL macro only — base locations, forward buildings, walls,
where aggressive-command activity happened, and scout/attack direction arrows. It is NOT tactical
unit micro and claims no casualties (replays log no deaths).

Convention everywhere: ME = blue, OPP = red.

- `geometry` — pure (no image I/O), fully unit-testable: projects game coords to image coords and
  lays out bases / buildings / walls / engagement markers / direction arrows.
- `render` — Pillow renderer that turns a geometry.MapLayout into a PNG, plus a legend.
"""

from . import geometry, render

__all__ = ["geometry", "render"]
