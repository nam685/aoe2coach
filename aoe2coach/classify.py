"""Deterministic build-order classifier (sub-project #3, Part B).

Pure functions over a `Reconstruction` dict (from #1's `reconstruct(rec).to_dict()`). NO LLM, no
network, no clock, no randomness. Reads only EXACT fields #1 emits (B.2) and the YAML signatures
(loaded once via `buildorders.load_library()`). Emits 1-3 ranked candidates with matched/missed
signals; degrades to `unknown=True` + closest-N rather than ever forcing a wrong label (B.3).

Determinism (B.4): same Reconstruction -> same ClassificationResult; ties break by build_id asc.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from . import const
from .buildorders import BuildOrder, load_library

# --- Scoring weights (B.3). Module constants, documented, tunable. Sum to 1.0. -------------------
W_UNIT = 0.30  # first military unit in signature's acceptable set
W_DEF = 0.20  # defining_units all present (partial credit)
W_BLD = 0.15  # first military buildings presence
W_FEUD = 0.20  # feudal arrival distance-into-band
W_VILS = 0.10  # vils_at_feudal_click distance-into-band
W_CIV = 0.05  # my_civ in recommended_civs (soft nudge only)
# A build with NO defining_units is a generic catch-all (pure scouts, generic FC). It still earns
# defining credit, but LESS than a sibling that commits to a defining unit AND shows it — so when
# the observed army contains a sibling's defining unit (an Archer for scouts-into-archers, a Knight
# for knight-rush), the committed build outranks the generic. Sibling-disambiguation (Part D).
W_DEF_GENERIC = 0.6  # fraction of W_DEF awarded to an empty-defining build

# --- Thresholds (B.3). First guesses; calibration-pending (Open Decision 5). ---------------------
MIN_THRESHOLD = 0.35  # below this for the top candidate -> unknown / off-meta
CONFIDENT_THRESHOLD = 0.70  # top must clear this AND lead #2 by GAP to be is_confident
CONFIDENT_GAP = 0.15

# Castle-age cutoff used for the age_path fingerprint: a Castle reached this early with NO feudal
# military reads as fast_castle (A.3 / B.2 "Castle < ~17:00").
FAST_CASTLE_S = 17 * 60


@dataclass
class Candidate:
    build_id: str
    name: str
    confidence: float
    matched_signals: list[str] = field(default_factory=list)
    missed_signals: list[str] = field(default_factory=list)


@dataclass
class ClassificationResult:
    candidates: list[Candidate] = field(default_factory=list)
    is_confident: bool = False
    unknown: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


# --------------------------------------------------------------------------- snapshot from recon
@dataclass
class _Snapshot:
    feudal_arrival_s: int | None
    castle_arrival_s: int | None
    imperial_arrival_s: int | None
    first_military_building: str | None
    first_military_building_s: int | None
    first_military_unit: str | None  # normalised class
    first_military_unit_s: int | None
    produced_unit_classes: set[str]  # normalised classes of all non-villager units produced
    vils_at_feudal_click: int | None
    early_buildings: set[str]  # building names placed before castle arrival (or all, if no castle)
    my_civ: str | None
    age_path: str


def _first_military_unit(recon: dict) -> tuple[str | None, int | None]:
    """First NON-villager produced unit, normalised to its class, with its time."""
    units = recon.get("production", {}).get("produced_units", []) or []
    best = None
    for u in units:
        if u.get("unit_id") == const.VILLAGER_ID:
            continue
        if best is None or u["t_s"] < best["t_s"] or (u["t_s"] == best["t_s"] and u["name"] < best["name"]):
            best = u
    if best is None:
        return None, None
    return const.unit_class(best["name"]), best["t_s"]


def _produced_classes(recon: dict) -> set[str]:
    units = recon.get("production", {}).get("produced_units", []) or []
    return {const.unit_class(u["name"]) for u in units if u.get("unit_id") != const.VILLAGER_ID}


def _early_buildings(recon: dict, castle_arrival_s: int | None) -> set[str]:
    """Building names placed early. Cutoff = castle arrival (else all observed buildings).

    Used for negative evidence (excludes_buildings): a Castle/Krepost/Donjon before castle age
    contradicts a feudal rush.
    """
    blds = recon.get("spatial", {}).get("me", {}).get("buildings", []) or []
    cutoff = castle_arrival_s if castle_arrival_s is not None else None
    out = set()
    for b in blds:
        if cutoff is None or b.get("t_s") is None or b["t_s"] <= cutoff:
            out.add(b["name"])
    return out


def _age_path(snap_castle, first_mil_bld_s, first_mil_unit_s) -> str:
    """Derive the age_path fingerprint from arrivals + first feudal military (B.2).

    feudal_rush  : feudal military (building or unit) committed before Castle arrival.
    fast_castle  : no feudal military and Castle reached fast (< ~17:00).
    boom         : no feudal military, slow Castle (>= ~17:00) -> deliberate eco.
    fast_imperial: Imperial handled implicitly by fast_castle/boom for v1 (no early military).
    drush_fc     : a feudal-rush signature with a fast castle; we surface feudal_rush here and let
                   the drush_fc build still match via signature (it shares the early Barracks/militia
                   fingerprint). We do NOT hard-gate drush_fc out of a feudal_rush observation.
    """
    has_feudal_military = (first_mil_bld_s is not None and (snap_castle is None or first_mil_bld_s < snap_castle)) or (
        first_mil_unit_s is not None and (snap_castle is None or first_mil_unit_s < snap_castle)
    )
    if has_feudal_military:
        return "feudal_rush"
    if snap_castle is not None and snap_castle < FAST_CASTLE_S:
        return "fast_castle"
    return "boom"


def _build_snapshot(recon: dict) -> _Snapshot:
    ages = recon.get("ages", {})
    feudal = ages.get("feudal_arrival_s")
    castle = ages.get("castle_arrival_s")
    imperial = ages.get("imperial_arrival_s")

    prod = recon.get("production", {})
    ms = prod.get("milestones", {})
    fmb = ms.get("first_military_building") or {}
    fmb_name = fmb.get("name") if isinstance(fmb, dict) else None
    fmb_s = ms.get("first_military_building_s")
    fmu_class, fmu_s = _first_military_unit(recon)

    return _Snapshot(
        feudal_arrival_s=feudal,
        castle_arrival_s=castle,
        imperial_arrival_s=imperial,
        first_military_building=fmb_name,
        first_military_building_s=fmb_s,
        first_military_unit=fmu_class,
        first_military_unit_s=fmu_s,
        produced_unit_classes=_produced_classes(recon),
        vils_at_feudal_click=prod.get("vils_at_feudal_click"),
        early_buildings=_early_buildings(recon, castle),
        my_civ=recon.get("meta", {}).get("my_civ"),
        age_path=_age_path(castle, fmb_s, fmu_s),
    )


# --------------------------------------------------------------------------- soft scoring helpers
def _band_score(value, band) -> float:
    """1.0 inside [lo,hi]; linear falloff to 0 over one extra band-width outside (A.4). None->0."""
    if value is None or band is None:
        return 0.0
    lo, hi = band
    if lo <= value <= hi:
        return 1.0
    width = max(hi - lo, 1)
    dist = (lo - value) if value < lo else (value - hi)
    return max(0.0, 1.0 - dist / width)


# Which age_paths are mutually incompatible (hard pre-narrowing, B.3 step 2). An observed
# fast_castle/boom (no feudal military) drops every feudal-pressure build, and vice versa.
_FEUDAL_PRESSURE = {"feudal_rush", "drush_fc"}
_NO_FEUDAL_MILITARY = {"fast_castle", "boom", "fast_imperial"}


def _age_path_compatible(observed: str, build_path: str) -> bool:
    if observed in _FEUDAL_PRESSURE and build_path in _NO_FEUDAL_MILITARY:
        return False
    if observed in _NO_FEUDAL_MILITARY and build_path in _FEUDAL_PRESSURE:
        return False
    return True


def _score_build(snap: _Snapshot, bo: BuildOrder) -> tuple[float, list[str], list[str]]:
    """Weighted soft score of one build against the snapshot. Returns (score, matched, missed)."""
    sig = bo.signature
    matched: list[str] = []
    missed: list[str] = []
    score = 0.0

    # w_unit: first military unit in the acceptable set (classes already normalised both sides).
    accept_units = {const.unit_class(u) for u in sig.first_military_unit}
    unit_matched = snap.first_military_unit is not None and snap.first_military_unit in accept_units
    if unit_matched:
        score += W_UNIT
        matched.append(f"first unit={snap.first_military_unit}")
    else:
        observed = snap.first_military_unit or "none"
        missed.append(f"first unit {observed} not in {sorted(accept_units)}")

    # w_bld: first military building in the build's expected set (computed before w_def, since the
    # generic-defining credit is gated on the build being structurally on-track).
    expected_blds = set(sig.first_military_buildings)
    bld_observed = snap.first_military_building is not None
    bld_matched = bld_observed and snap.first_military_building in expected_blds
    if bld_matched:
        score += W_BLD
        matched.append(f"first mil building={snap.first_military_building}")
    elif expected_blds and bld_observed:
        missed.append(f"first mil building {snap.first_military_building} not in {sorted(expected_blds)}")

    # w_def: defining_units all present (partial credit by fraction present).
    defining = {const.unit_class(u) for u in sig.defining_units}
    if defining:
        present = defining & snap.produced_unit_classes
        frac = len(present) / len(defining)
        score += W_DEF * frac
        if frac >= 1.0:
            matched.append(f"defining units present: {sorted(defining)}")
        else:
            missed.append(f"missing defining units: {sorted(defining - present)}")
    else:
        # Generic catch-all (pure scouts, generic FC): earn partial defining credit ONLY when the
        # build is structurally on-track — its first military unit OR building matched, or none has
        # been observed yet (early FC). A mismatched building (tower rush vs a Stable build) earns
        # nothing here, so an off-meta game degrades to unknown instead of clearing MIN on timing.
        on_track = unit_matched or bld_matched or not (bld_observed or snap.first_military_unit)
        score += W_DEF * W_DEF_GENERIC if on_track else 0.0

    # w_feud: feudal arrival distance-into-band.
    fs = _band_score(snap.feudal_arrival_s, sig.feudal_arrival_band_s)
    score += W_FEUD * fs
    if sig.feudal_arrival_band_s is not None and snap.feudal_arrival_s is not None:
        lo, hi = sig.feudal_arrival_band_s
        label = _fmt_mmss(snap.feudal_arrival_s)
        if fs >= 1.0:
            matched.append(f"feudal {label} in band [{_fmt_mmss(lo)}-{_fmt_mmss(hi)}]")
        elif fs > 0:
            matched.append(f"feudal {label} near band [{_fmt_mmss(lo)}-{_fmt_mmss(hi)}]")
        else:
            missed.append(f"feudal {label} outside band [{_fmt_mmss(lo)}-{_fmt_mmss(hi)}]")

    # w_vils: vils_at_feudal_click distance-into-band.
    vfc = sig.vils_at_feudal_click
    if vfc is not None and snap.vils_at_feudal_click is not None:
        vs = _band_score(snap.vils_at_feudal_click, vfc["band"])
        score += W_VILS * vs
        if vs >= 1.0:
            matched.append(f"vils@feudal-click={snap.vils_at_feudal_click} (target {vfc['target']})")
        else:
            missed.append(f"vils@feudal-click={snap.vils_at_feudal_click} off target {vfc['target']}")

    # w_civ: soft nudge only.
    if snap.my_civ and bo.recommended_civs and snap.my_civ in bo.recommended_civs:
        score += W_CIV
        matched.append(f"civ {snap.my_civ} recommended")

    return score, matched, missed


def _fmt_mmss(s) -> str:
    if s is None:
        return "?"
    return f"{int(s) // 60}:{int(s) % 60:02d}"


# --------------------------------------------------------------------------- the public entrypoint
def classify(recon: dict, library: dict[str, BuildOrder] | None = None) -> ClassificationResult:
    """Deterministically classify a Reconstruction into 1-3 ranked Candidates (B.3).

    `recon` is the dict form of #1's Reconstruction (`reconstruct(rec).to_dict()`). `library`
    overrides the loaded YAML library (for tests); defaults to `buildorders.load_library()`.
    """
    lib = library if library is not None else load_library()
    snap = _build_snapshot(recon)
    notes: list[str] = []

    # Step 2: hard pre-filter on NEGATIVE evidence only.
    survivors: list[BuildOrder] = []
    for bo in sorted(lib.values(), key=lambda b: b.id):
        excluded = set(bo.signature.excludes_buildings) & snap.early_buildings
        if excluded:
            continue
        if not _age_path_compatible(snap.age_path, bo.signature.age_path):
            continue
        survivors.append(bo)

    # Step 3: soft weighted scoring over survivors.
    scored: list[tuple[float, BuildOrder, list[str], list[str]]] = []
    for bo in survivors:
        score, matched, missed = _score_build(snap, bo)
        scored.append((score, bo, matched, missed))
    # Rank: confidence desc, then build_id asc (determinism, B.4).
    scored.sort(key=lambda t: (-t[0], t[1].id))

    def _mk(entry) -> Candidate:
        score, bo, matched, missed = entry
        return Candidate(
            build_id=bo.id,
            name=bo.name,
            confidence=round(score, 4),
            matched_signals=matched,
            missed_signals=missed,
        )

    above = [e for e in scored if e[0] >= MIN_THRESHOLD]

    if not above:
        # Step 5a: nothing clears MIN -> off-meta, return closest-N with an honest note.
        closest = [_mk(e) for e in scored[:3]]
        notes.append(_offmeta_note(snap, scored))
        return ClassificationResult(candidates=closest, is_confident=False, unknown=True, notes=notes)

    top3 = [_mk(e) for e in above[:3]]
    top = above[0][0]
    second = above[1][0] if len(above) > 1 else 0.0
    is_confident = top >= CONFIDENT_THRESHOLD and (top - second) >= CONFIDENT_GAP

    if not is_confident:
        notes.append(
            f"low-confidence: top={top3[0].build_id} {top:.2f}"
            + (f", #2={top3[1].build_id} {second:.2f}" if len(top3) > 1 else "")
            + " — coach must verify between candidates"
        )
    return ClassificationResult(candidates=top3, is_confident=is_confident, unknown=False, notes=notes)


def _offmeta_note(snap: _Snapshot, scored) -> str:
    closest = scored[0][1].id if scored else "none"
    bits = [f"off-meta: no build cleared {MIN_THRESHOLD:.2f}", f"observed age_path={snap.age_path}"]
    if snap.feudal_arrival_s is not None:
        bits.append(f"feudal {_fmt_mmss(snap.feudal_arrival_s)}")
    if snap.castle_arrival_s is not None:
        bits.append(f"castle {_fmt_mmss(snap.castle_arrival_s)}")
    if snap.first_military_unit:
        bits.append(f"first unit {snap.first_military_unit}")
    bits.append(f"closest={closest}")
    return "; ".join(bits)
