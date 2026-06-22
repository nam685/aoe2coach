"""Build-order reference library (sub-project #3, Part A).

Progressive disclosure (Nam's requirement): `load_one(build_id)` reads ONE YAML on demand — the
coach (#4) loads only the candidate file(s) into context, never the whole library. `load_library()`
reads them all (for the classifier and the schema-validation test) and caches the result.

Pure data access: the only I/O is reading the package-data YAML files under `data/`.
"""

from __future__ import annotations

import functools
from pathlib import Path

import yaml

from ._schema import BuildOrder, SchemaError, validate

_DATA_DIR = Path(__file__).parent / "data"


def _data_path(build_id: str) -> Path:
    # build_id is a slug == filename stem; guard against path traversal.
    if "/" in build_id or ".." in build_id or build_id.startswith("_"):
        raise ValueError(f"invalid build_id: {build_id!r}")
    return _DATA_DIR / f"{build_id}.yaml"


def load_one(build_id: str) -> dict:
    """Read a single build's full reference dict on demand (progressive disclosure, Part C.2).

    Returns the raw dict (full schema: identity, steps, age_targets, eco_split, whats_next,
    signature) — exactly what #4's agent reads to fact-check. Raises FileNotFoundError if the slug
    has no file, SchemaError if the file is malformed.
    """
    path = _data_path(build_id)
    if not path.exists():
        raise FileNotFoundError(f"no build-order reference for id {build_id!r} ({path})")
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    validate(raw)  # raises SchemaError if malformed
    return raw


def build_ids() -> list[str]:
    """Sorted list of available build ids (filename stems, excluding _index/_-prefixed)."""
    return sorted(p.stem for p in _DATA_DIR.glob("*.yaml") if not p.name.startswith("_"))


@functools.lru_cache(maxsize=1)
def load_library() -> dict[str, BuildOrder]:
    """Load + validate every build file into {build_id: BuildOrder}. Cached (loaded once).

    Used by the classifier (which needs every signature) and the schema-validation test. For #4's
    on-demand single-file reads, use load_one() instead.
    """
    out: dict[str, BuildOrder] = {}
    for bid in build_ids():
        with _data_path(bid).open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
        bo = validate(raw)
        if bo.id != bid:
            raise SchemaError(f"file {bid}.yaml declares id {bo.id!r} (id must equal filename stem)")
        out[bid] = bo
    return out


__all__ = ["BuildOrder", "SchemaError", "validate", "load_one", "load_library", "build_ids"]
