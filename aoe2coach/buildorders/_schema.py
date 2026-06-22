"""Schema + validation for the Hera build-order reference files (sub-project #3, Part A.2).

Pure: the only I/O is reading the YAML files (done in __init__.py); this module just defines the
dataclasses and a `validate()` that checks a loaded dict against the contract. The classifier reads
ONLY the `signature` block; the rest (steps / age_targets / eco_split / whats_next) is reference data
for sub-project #4 to read on demand.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Allowed age_path fingerprints (B.2). A build's signature.age_path must be one of these.
AGE_PATHS = {"feudal_rush", "fast_castle", "drush_fc", "fast_imperial", "boom"}

# Required top-level keys for a build file.
_REQUIRED_TOP = ("id", "name", "source", "family", "signature")
# Required keys inside `signature`.
_REQUIRED_SIG = (
    "first_military_buildings",
    "first_military_unit",
    "defining_units",
    "feudal_arrival_band_s",
    "vils_at_feudal_click",
    "excludes_buildings",
    "age_path",
)


class SchemaError(ValueError):
    """Raised when a build-order YAML violates the schema (Part D validation test)."""


@dataclass
class Signature:
    """The machine-checkable block — the ONLY part the deterministic classifier reads (A.2)."""

    first_military_buildings: list[str] = field(default_factory=list)
    first_military_unit: list[str] = field(default_factory=list)
    defining_units: list[str] = field(default_factory=list)
    feudal_arrival_band_s: list[int] | None = None
    castle_arrival_band_s: list[int] | None = None
    vils_at_feudal_click: dict | None = None
    excludes_buildings: list[str] = field(default_factory=list)
    age_path: str = "feudal_rush"


@dataclass
class BuildOrder:
    """One Hera build. `raw` keeps the full dict (#4 reads steps/age_targets/eco_split/whats_next)."""

    id: str
    name: str
    family: str
    age_path: str
    signature: Signature
    recommended_civs: list[str] = field(default_factory=list)
    source: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)


def _check_band(band, where):
    """A band is [lo, hi] with lo <= hi, or None (the build doesn't commit to that timing)."""
    if band is None:
        return
    if not (isinstance(band, list | tuple) and len(band) == 2):
        raise SchemaError(f"{where}: band must be [lo, hi], got {band!r}")
    lo, hi = band
    if not (isinstance(lo, int | float) and isinstance(hi, int | float)):
        raise SchemaError(f"{where}: band bounds must be numbers, got {band!r}")
    if lo > hi:
        raise SchemaError(f"{where}: band lo > hi ({lo} > {hi})")


def validate(d: dict) -> BuildOrder:
    """Validate a loaded YAML dict and return a BuildOrder. Raises SchemaError on any violation.

    Checks: required keys, band ordering (lo<=hi), age_path enum, signature unit/building lists are
    non-empty lists of strings, vils_at_feudal_click shape. Does NOT resolve unit names against
    const here (that is done by the library-load test in Part D, which has const available).
    """
    if not isinstance(d, dict):
        raise SchemaError(f"build file must be a mapping, got {type(d).__name__}")
    for k in _REQUIRED_TOP:
        if k not in d:
            raise SchemaError(f"missing required key: {k!r}")

    bid = d["id"]
    if not isinstance(bid, str) or not bid:
        raise SchemaError(f"id must be a non-empty string, got {bid!r}")

    sig = d["signature"]
    if not isinstance(sig, dict):
        raise SchemaError(f"{bid}: signature must be a mapping")
    for k in _REQUIRED_SIG:
        if k not in sig:
            raise SchemaError(f"{bid}: signature missing key: {k!r}")

    age_path = sig["age_path"]
    if age_path not in AGE_PATHS:
        raise SchemaError(f"{bid}: age_path {age_path!r} not in {sorted(AGE_PATHS)}")

    for lk in ("first_military_buildings", "first_military_unit", "defining_units", "excludes_buildings"):
        v = sig[lk]
        if not isinstance(v, list):
            raise SchemaError(f"{bid}: signature.{lk} must be a list, got {type(v).__name__}")
        if not all(isinstance(x, str) for x in v):
            raise SchemaError(f"{bid}: signature.{lk} must be a list of strings, got {v!r}")
    # first_military_unit must be a non-empty acceptable set.
    if not sig["first_military_unit"]:
        raise SchemaError(f"{bid}: signature.first_military_unit must be non-empty")

    _check_band(sig["feudal_arrival_band_s"], f"{bid}: signature.feudal_arrival_band_s")
    _check_band(sig.get("castle_arrival_band_s"), f"{bid}: signature.castle_arrival_band_s")
    if sig["feudal_arrival_band_s"] is None and age_path in ("feudal_rush", "drush_fc"):
        raise SchemaError(f"{bid}: feudal-pressure builds must pin a feudal_arrival_band_s")

    vfc = sig["vils_at_feudal_click"]
    if vfc is not None:
        if not isinstance(vfc, dict) or "target" not in vfc or "band" not in vfc:
            raise SchemaError(f"{bid}: signature.vils_at_feudal_click must be {{target, band}}")
        _check_band(vfc["band"], f"{bid}: signature.vils_at_feudal_click.band")

    signature = Signature(
        first_military_buildings=list(sig["first_military_buildings"]),
        first_military_unit=list(sig["first_military_unit"]),
        defining_units=list(sig["defining_units"]),
        feudal_arrival_band_s=sig["feudal_arrival_band_s"],
        castle_arrival_band_s=sig.get("castle_arrival_band_s"),
        vils_at_feudal_click=vfc,
        excludes_buildings=list(sig["excludes_buildings"]),
        age_path=age_path,
    )
    return BuildOrder(
        id=bid,
        name=d["name"],
        family=d["family"],
        age_path=age_path,
        signature=signature,
        recommended_civs=list(d.get("recommended_civs", [])),
        source=d.get("source", {}),
        raw=d,
    )
