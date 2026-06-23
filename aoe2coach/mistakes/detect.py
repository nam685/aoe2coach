"""The detector pass: `detect_mistakes(recon) -> list[Flagged]`.

Pure & deterministic — same Reconstruction -> same list, same order. No randomness, no clock, no
network. This is what lets #4 trust the output as linter-style facts: the pass decides which
mistakes are *present*; the coach only narrates the flagged ones (retrieving each entry on demand
via load_one).

HONESTY: a detector is skipped (never run) if any input path it declares is absent/None in the
Reconstruction — so a missing #1 field or absent #2 economy data drops the check rather than
guessing. `needs-#2` entries are `fn: disabled` and never run at all.
"""

from dataclasses import asdict, dataclass, field

from . import detectors
from .detectors import Detection  # re-export for the package API

_SEVERITY_RANK = {"high": 3, "medium": 2, "low": 1}


@dataclass
class Flagged:
    """One row of the pass output. JSON-serializable; the stable contract #4 consumes."""

    id: str
    name: str
    severity: str
    confidence_tier: str  # exact | heuristic | needs-#2  -> coach hedging
    observed: dict = field(default_factory=dict)
    magnitude: float = 0.0
    reference_path: str = ""  # "mistakes/data/<id>.yaml" — #4 retrieves on demand

    def to_dict(self):
        return asdict(self)


def _get_path(recon, dotted):
    """Read a dotted path from the recon dict. Returns (found: bool, value). `found` is False if any
    segment is missing; a present-but-None leaf returns (True, None) so callers can treat None as
    'absent' uniformly via the value check below."""
    cur = recon
    for seg in dotted.split("."):
        if not isinstance(cur, dict) or seg not in cur:
            return (False, None)
        cur = cur[seg]
    return (True, cur)


def _inputs_present(recon, inputs):
    """True only if every declared input path exists AND is not None (honest skip otherwise)."""
    for path in inputs:
        found, value = _get_path(recon, path)
        if not found or value is None:
            return False
    return True


def detect_mistakes(recon, library=None, build_target=None, economy=None):
    """Run every enabled detector over `recon` and return the sorted Flagged list.

    Args:
      recon: #1's Reconstruction.to_dict() (or same-shape dict).
      library: optional pre-loaded {id: Rubric}; defaults to load_library().
      build_target: optional matched-build age targets (from #3) for build-relative uptime flags;
        injected under recon['_build_target'] for the slow-age detectors (B.6). The recon is not
        mutated — a shallow copy carries the injection.
      economy: optional #2 estimate_economy(...) dict. When given, it is injected under
        recon['economy'] so the floating-resources / over-collecting detectors can read the
        worker-allocation + spending shares. Absent -> those detectors honestly skip (input missing).

    Returns: list[Flagged], sorted by (severity desc, magnitude desc, id asc). Possibly empty —
    "no detectable mistakes" is a valid, honest answer.
    """
    if library is None:
        from . import load_library

        library = load_library()

    # Accept either #1's Reconstruction object or its dict form. Without this, passing the object
    # silently fires ZERO detectors (every dotted-path lookup misses) -- a dangerous false "clean".
    if not isinstance(recon, dict):
        recon = recon.to_dict()

    recon_view = recon
    if build_target is not None or economy is not None:
        recon_view = dict(recon)
        if build_target is not None:
            recon_view["_build_target"] = build_target
        if economy is not None:
            recon_view["economy"] = economy if isinstance(economy, dict) else economy.to_dict()

    flagged = []
    for rubric in library.values():
        if rubric.disabled:
            continue  # needs-#2 / reference-only — never runs
        if not _inputs_present(recon_view, rubric.detector.inputs):
            continue  # honest skip: a required field is absent/None
        fn = getattr(detectors, rubric.detector.fn, None)
        if fn is None:
            continue  # defensive; load_library() already validated this resolves
        detection = fn(recon_view, rubric.detector.params)
        if detection is None:
            continue
        flagged.append(
            Flagged(
                id=rubric.id,
                name=rubric.name,
                severity=rubric.severity,
                confidence_tier=rubric.confidence_tier,
                observed=detection.observed,
                magnitude=round(float(detection.magnitude), 4),
                reference_path=f"mistakes/data/{rubric.id}.yaml",
            )
        )

    flagged.sort(key=lambda f: (-_SEVERITY_RANK.get(f.severity, 0), -f.magnitude, f.id))
    return flagged


__all__ = ["Detection", "Flagged", "detect_mistakes"]
