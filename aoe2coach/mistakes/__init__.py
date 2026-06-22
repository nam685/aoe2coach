"""Coaching knowledge base: curated, versioned rubric entries for common 1v1 mistakes + a pure,
deterministic detector pass over #1's Reconstruction.

Public API:
  load_library() -> dict[str, Rubric]   # all entries, loaded once (cached)
  load_one(mistake_id) -> dict          # one entry's raw schema dict (progressive disclosure)
  load_index() -> dict                  # _index.yaml (ordered list + citation refs)
  detect_mistakes(recon, ...) -> list[Flagged]   # the linter-style pass (see detect.py)

Progressive disclosure (reused from #3's mechanism, parallel namespace): the coach (#4) reads only
the flagged entries via load_one(id) -> data/<id>.yaml, never the whole library.
"""

import functools
import os

import yaml

from . import detectors
from ._schema import from_dict
from .detect import Detection, Flagged, detect_mistakes

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
_INDEX_FILE = "_index.yaml"


def _read_yaml(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@functools.lru_cache(maxsize=1)
def load_index():
    """The _index.yaml: ordered entry list + citation `refs` map (citation key -> {url, title})."""
    return _read_yaml(os.path.join(_DATA_DIR, _INDEX_FILE))


def _detector_names():
    """Public detector fn names in detectors.py (anything not private), for schema validation."""
    return {n for n in dir(detectors) if not n.startswith("_") and callable(getattr(detectors, n))}


@functools.lru_cache(maxsize=1)
def load_library():
    """Load + validate every rubric YAML in data/ (except the index). Cached after first call.

    Returns {id: Rubric}. Validation resolves detector.fn against detectors.py and source.ref
    against _index.yaml — a malformed entry raises at load time, not at detect time.
    """
    index = load_index()
    index_refs = set((index.get("refs") or {}).keys())
    det_names = _detector_names()
    lib = {}
    for fname in sorted(os.listdir(_DATA_DIR)):
        if not fname.endswith(".yaml") or fname == _INDEX_FILE:
            continue
        stem = fname[: -len(".yaml")]
        raw = _read_yaml(os.path.join(_DATA_DIR, fname))
        rubric = from_dict(raw, stem=stem)
        # Re-validate with the live detector + index sets (from_dict validated structure only).
        from ._schema import validate

        validate(rubric, stem=stem, detector_names=det_names, index_refs=index_refs)
        lib[rubric.id] = rubric
    return lib


def load_one(mistake_id):
    """Raw schema dict for one entry (data/<id>.yaml). The progressive-disclosure unit #4 retrieves.

    Mirrors #3's buildorders.load_one(build_id) contract in a parallel namespace. Raises
    FileNotFoundError if the id has no file.
    """
    path = os.path.join(_DATA_DIR, f"{mistake_id}.yaml")
    if not os.path.exists(path):
        raise FileNotFoundError(f"no mistake rubric '{mistake_id}' at {path}")
    return _read_yaml(path)


__all__ = [
    "load_library",
    "load_one",
    "load_index",
    "detect_mistakes",
    "Detection",
    "Flagged",
]
