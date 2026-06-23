"""Schema for a mistake rubric entry + validate(). Pure; the only I/O is reading the yaml files
loaded elsewhere (this module validates already-parsed dicts).

A rubric entry (one YAML in data/) is the user-facing knowledge for one common 1v1 mistake:
explanation, fix, a cited source, and a deterministic detector spec. Honesty tagging
(confidence_tier) rides on every entry and every Flagged row downstream so the coach (#4) can
hedge `heuristic` flags and never assert a `needs-#2` one (those never fire).
"""

from dataclasses import dataclass, field

TIERS = ("exact", "heuristic", "needs-#2")
SEVERITIES = ("low", "medium", "high")
DISABLED_FN = "disabled"

# Dotted paths into #1's Reconstruction.to_dict() that detectors are allowed to declare as inputs.
# Validated so an entry can't claim to read a field the core doesn't emit (spec Part D / E).
RECONSTRUCTION_PATHS = frozenset(
    {
        "meta.map",
        "meta.duration_s",
        "meta.my_civ",
        "meta.result",
        "ages.feudal_arrival_s",
        "ages.castle_arrival_s",
        "ages.imperial_arrival_s",
        "ages.feudal_click_s",
        "ages.castle_click_s",
        "ages.imperial_click_s",
        "techs.eco",
        "techs.military",
        "techs.university",
        "production.produced_units",
        "production.milestones",
        "production.vils_at_feudal_click",
        "counts.villagers_produced",
        "counts.army_produced",
        "spatial.me.base_centroid",
        "spatial.me.buildings",
        "spatial.me.forward",
        "spatial.me.walls",
        "spatial.me.eco_exposure",
        "spatial.opp.base_centroid",
        "population.me.housed_pop_ceiling",
        "population.me.pop_ceiling_steps",
        "combat.me.engagements",
        "efficiency.tc_idle_s",
        "efficiency.longest_villager_gap_s",
        "efficiency.villager_gaps_s",
        "efficiency.apm_total",
        # #2 economy fields. The floating signal is a HEURISTIC mid-game intent-vs-spend gap (NOT a
        # bank total): resource_balance.floating carries the flags, worker_allocation.mid_game_share
        # the gathering-intent shares. Injected into the recon view by detect_mistakes(economy=...).
        "economy.resource_balance.floating",
        "economy.worker_allocation.mid_game_share",
    }
)


@dataclass
class Detector:
    fn: str
    inputs: list = field(default_factory=list)
    params: dict = field(default_factory=dict)
    condition: str = ""


@dataclass
class Source:
    ref: str = ""
    detail: str = ""
    study: dict = field(default_factory=dict)  # {url, title}


@dataclass
class Rubric:
    id: str
    name: str
    explanation: str
    severity: str
    confidence_tier: str
    detector: Detector
    fix: str
    source: Source

    @property
    def disabled(self):
        return self.detector.fn == DISABLED_FN


class SchemaError(ValueError):
    """Raised by validate() when a rubric entry violates the schema."""


def _require(cond, msg):
    if not cond:
        raise SchemaError(msg)


def from_dict(raw, stem=None):
    """Build a Rubric from a parsed-YAML dict. `stem` is the filename stem for id-matching checks."""
    _require(isinstance(raw, dict), f"entry must be a mapping, got {type(raw).__name__}")
    det_raw = raw.get("detector") or {}
    src_raw = raw.get("source") or {}
    rubric = Rubric(
        id=raw.get("id", ""),
        name=raw.get("name", ""),
        explanation=raw.get("explanation", ""),
        severity=raw.get("severity", ""),
        confidence_tier=raw.get("confidence_tier", ""),
        detector=Detector(
            fn=det_raw.get("fn", ""),
            inputs=list(det_raw.get("inputs", []) or []),
            params=dict(det_raw.get("params", {}) or {}),
            condition=det_raw.get("condition", ""),
        ),
        fix=raw.get("fix", ""),
        source=Source(
            ref=src_raw.get("ref", ""),
            detail=src_raw.get("detail", ""),
            study=dict(src_raw.get("study", {}) or {}),
        ),
    )
    validate(rubric, stem=stem)
    return rubric


def validate(rubric, stem=None, detector_names=None, index_refs=None):
    """Enforce the spec's schema rules on a Rubric. Raises SchemaError on the first violation.

    `detector_names`: set of resolvable fn names in detectors.py (besides `disabled`); when given,
    `detector.fn` must be in it or equal `disabled`.
    `index_refs`: set of citation keys defined in _index.yaml; when given, `source.ref` must resolve.
    """
    _require(rubric.id, "id is required")
    _require(rubric.name, f"{rubric.id}: name is required")
    _require(rubric.explanation, f"{rubric.id}: explanation is required")
    _require(rubric.fix, f"{rubric.id}: fix is required")
    if stem is not None:
        _require(rubric.id == stem, f"id '{rubric.id}' must equal filename stem '{stem}'")
    _require(
        rubric.confidence_tier in TIERS,
        f"{rubric.id}: confidence_tier '{rubric.confidence_tier}' not in {TIERS}",
    )
    _require(rubric.severity in SEVERITIES, f"{rubric.id}: severity '{rubric.severity}' not in {SEVERITIES}")

    # needs-#2 entries MUST be reference-only (fn: disabled) so they can never run on absent data.
    if rubric.confidence_tier == "needs-#2":
        _require(
            rubric.disabled,
            f"{rubric.id}: needs-#2 entry must set detector.fn: disabled (reference-only)",
        )

    # detector.fn resolves (or is disabled); declared inputs must be real Reconstruction paths.
    if not rubric.disabled:
        if detector_names is not None:
            _require(
                rubric.detector.fn in detector_names,
                f"{rubric.id}: detector.fn '{rubric.detector.fn}' not found in detectors.py",
            )
        _require(rubric.detector.inputs, f"{rubric.id}: an enabled detector must declare inputs")
    for path in rubric.detector.inputs:
        _require(
            path in RECONSTRUCTION_PATHS,
            f"{rubric.id}: input path '{path}' is not a known Reconstruction field",
        )

    # source.ref must resolve in _index.yaml when an index is supplied.
    _require(rubric.source.ref, f"{rubric.id}: source.ref is required")
    if index_refs is not None:
        _require(
            rubric.source.ref in index_refs,
            f"{rubric.id}: source.ref '{rubric.source.ref}' not defined in _index.yaml",
        )
    # study material is user-facing — require at least a url.
    _require(rubric.source.study.get("url"), f"{rubric.id}: source.study.url is required (user-facing learn-more)")
    return True
