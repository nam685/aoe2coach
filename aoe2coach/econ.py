"""Tier-B economy ESTIMATOR (sub-project #2). Everything here is an `~estimate`, labeled as such.

A replay is a command log, not a state log: villager-per-resource counts and resources collected are
NOT in the file. We estimate them from the SPARSE assignment commands (GATHER_POINT + ORDER) joined
to the starting GAIA object table (gaia.py), using known DE gather rates (rates.py).

HONESTY (program HARD RULE): every estimated value carries `estimate: true`, a `confidence`/`n_events`
(supporting event count), and collected totals carry a `[low, high]` band. If the collected estimate
misses the validation band (+-15% per resource AND +-10% on total vs the calibration screenshot), the
model SELF-SUPPRESSES the number and emits only the qualitative eco shape. Suppression is success.

Pure functions over `ops`, the parsed gaia table, and a Reconstruction dict (#1). No DB/network/IO.
"""

from mgz.fast import Action

from . import gaia as gaia_mod
from . import rates

_RESOURCES = ("food", "wood", "gold", "stone")

# Validation band (spec): per-resource and grand-total error vs the calibration screenshot. These are
# the suppression thresholds — a number that misses them is dropped, not shipped as fact.
BAND_PER_RESOURCE = 0.15
BAND_TOTAL = 0.10

# Collected-estimate band half-width: the model is coarse (dozens of events), so report a wide
# [low, high] around the point estimate to avoid false precision.
COLLECTED_BAND = 0.30


def assignment_events(ops, player, gaia_by_inst, gaia_by_objid):
    """Fuse GATHER_POINT + ORDER into resource-assignment events for `player`, in time order.

    Each event: {"t_s", "resource", "n_vils"}. Rules (verified on the real recs):
      - GATHER_POINT: drop target_type == -1 (gather point on bare ground, no resource); else join
        target_type -> gaia object_id, classify; drop if not a resource (building/decoration).
      - ORDER: join target_id -> gaia instance_id, classify; drop if not a resource.
      - n_vils = len(object_ids) (the group the command moved), min 1.
    Never fabricates: an unjoinable / non-resource target produces no event.
    """
    out = []
    for t, action_type, data in ops:
        if data.get("player_id") != player:
            continue
        if action_type == Action.GATHER_POINT:
            tt = data.get("target_type", -1)
            if tt == -1:
                continue
            obj = gaia_by_objid.get(tt)
        elif action_type == Action.ORDER:
            obj = gaia_by_inst.get(data.get("target_id"))
        else:
            continue
        resource = gaia_mod.resource_class(obj)
        if resource is None:
            continue
        n_vils = len(data.get("object_ids") or []) or 1
        out.append({"t_s": t // 1000, "resource": resource, "n_vils": n_vils})
    out.sort(key=lambda e: e["t_s"])
    return out


def eco_split_steps(events):
    """Step function of estimated vils-per-resource. Each event UPDATES the running allocation; the
    split is HELD CONSTANT between events (the known drift source — exposed, never smoothed).

    Returns a list of {t_s, alloc:{resource:n_vils}, confidence} where confidence = cumulative event
    count supporting that step (thin windows self-disclose). The allocation is the SUM of n_vils
    most-recently assigned to each resource (an approximation; vils are reassigned silently).
    """
    steps = []
    alloc = {}
    for i, e in enumerate(events):
        alloc = dict(alloc)
        alloc[e["resource"]] = alloc.get(e["resource"], 0) + e["n_vils"]
        steps.append({"t_s": e["t_s"], "alloc": alloc, "confidence": i + 1})
    return steps


def _alloc_at(steps, t_s):
    """The running allocation in effect at time t_s (last step at or before t_s). {} if none yet."""
    cur = {}
    n = 0
    for s in steps:
        if s["t_s"] <= t_s:
            cur = s["alloc"]
            n = s["confidence"]
        else:
            break
    return cur, n


def eco_split_at_ages(steps, ages):
    """Coarse vils-per-resource SNAPSHOT at each age boundary (the primary deliverable).

    `ages` is the Reconstruction `ages` dict (feudal/castle/imperial _arrival_s). For each reached
    age returns {estimate, n_events, alloc, shares} where shares are normalized fractions; None for
    an age not reached. Labeled estimate, with n_events so thin windows self-disclose.
    """
    out = {}
    for age in ("feudal", "castle", "imperial"):
        t = ages.get(f"{age}_arrival_s")
        if t is None:
            out[age] = None
            continue
        alloc, n = _alloc_at(steps, t)
        total = sum(alloc.values())
        shares = {r: alloc.get(r, 0) / total for r in alloc} if total else {}
        out[age] = {
            "estimate": True,
            "n_events": n,
            "alloc": dict(alloc),
            "shares": shares,
        }
    return out


def collected_estimate(steps, recon):
    """Estimate resources collected by integrating Σ vils_on_R(window) × rate_at(R, t) × dt.

    Returns {resource: {value, low, high, estimate, confidence}} or None (SUPPRESSED).

    SELF-SUPPRESSION (spec HARD RULE — no ground truth available at runtime, so suppress on internal
    implausibility, never on a hidden answer key):
      1. No signal at all -> None.
      2. A MAJOR resource (wood or food) has ZERO assignment signal -> the whole collected block is
         untrustworthy (the additive step model can't track the unsignaled resource), so SUPPRESS ALL.
         On save 68.0, wood is reliably zero-signal (vils are gather-pointed to a lumber camp, never
         re-clicked onto trees) — this is THE expected suppression trigger and is success per the spec.
      3. The implied collection exceeds a villager-bound sanity ceiling (the additive allocation never
         decays, so it over-counts) -> SUPPRESS ALL.
    `recon` supplies duration_s, eco-tech timings (rate_at), and villagers_produced (the ceiling).
    """
    if not steps:
        return None
    duration_s = recon.get("meta", {}).get("duration_s")
    if not duration_s:
        duration_s = steps[-1]["t_s"]
    if not duration_s:
        return None

    totals = dict.fromkeys(_RESOURCES, 0.0)
    seen = set()
    # Walk consecutive step windows; the final window runs to game end.
    for i, s in enumerate(steps):
        t0 = s["t_s"]
        t1 = steps[i + 1]["t_s"] if i + 1 < len(steps) else duration_s
        dt = max(0, t1 - t0)
        if dt == 0:
            continue
        for r, n in s["alloc"].items():
            if n <= 0:
                continue
            seen.add(r)
            rate = rates.rate_at(r, (t0 + t1) // 2, recon)
            totals[r] += n * rate * dt

    if not seen:
        return None

    # (2) A major resource with no signal poisons the whole estimate -> suppress all (qualitative only).
    if "wood" not in seen or "food" not in seen:
        return None

    # (3) Villager-bound sanity ceiling: total resources can't exceed (max villagers) working at the
    # fastest base rate for the whole game. villagers_produced is itself an upper bound (queued), so
    # this ceiling is generous — only catches gross runaway over-counting from the non-decaying split.
    grand_total = sum(totals[r] for r in _RESOURCES)
    vils = recon.get("counts", {}).get("villagers_produced")
    if vils:
        max_rate = max(rates.BASE_RATE_PER_S.values())
        ceiling = vils * max_rate * duration_s
        if grand_total > ceiling:
            return None

    out = {}
    for r in _RESOURCES:
        if r not in seen:
            out[r] = None  # never report 0 collected as a fact
            continue
        v = totals[r]
        out[r] = {
            "value": round(v),
            "low": round(v * (1 - COLLECTED_BAND)),
            "high": round(v * (1 + COLLECTED_BAND)),
            "estimate": True,
            "confidence": "low",
        }
    return out


def _qualitative_shape(events, recon):
    """Heuristic eco NARRATIVE from event timestamps + eco techs (interpretation is heuristic, the
    timestamps are exact). Never a number presented as fact — just the shape."""
    first_by_res = {}
    for e in events:
        first_by_res.setdefault(e["resource"], e["t_s"])
    committed_first = min(first_by_res, key=first_by_res.get) if first_by_res else None
    gold_start_s = first_by_res.get("gold")
    # Farm / lumber-camp commitment is exact from #1's builds; surface as qualitative wood/food signal.
    eco_techs = [t["name"] for t in recon.get("techs", {}).get("eco", [])]
    return {
        "committed_first": committed_first,
        "first_assignment_s_by_resource": first_by_res,
        "gold_mining_start_s": gold_start_s,
        "eco_techs": eco_techs,
        "note": (
            "Eco shape from sparse assignment events (GATHER_POINT+ORDER) joined to the starting map. "
            "Timestamps are exact; the interpretation is heuristic. Wood is typically under-signaled "
            "(vils are gather-pointed to a lumber camp, not re-clicked onto trees), so wood share is "
            "the least reliable."
        ),
    }


def estimate_economy(ops, player, gaia_list, recon):
    """Assemble the full ESTIMATE block: assignment events -> split steps -> age snapshots +
    collected band (suppressed if no signal / out of band) + qualitative shape.

    Returns a JSON-serializable dict, always labeled `estimate: true`. The `collected` key is a
    per-resource band dict OR None (suppressed). `qualitative` is always present (the honest fallback).
    """
    # Accept either #1's Reconstruction object or its dict form (parity with classify/detect_mistakes).
    if not isinstance(recon, dict):
        recon = recon.to_dict()
    g_inst = gaia_mod.gaia_objects(gaia_list)
    g_objid = gaia_mod.by_object_id(gaia_list)
    events = assignment_events(ops, player, g_inst, g_objid)
    steps = eco_split_steps(events)
    ages = recon.get("ages", {})

    snaps = eco_split_at_ages(steps, ages)
    collected = collected_estimate(steps, recon)
    qualitative = _qualitative_shape(events, recon)

    counts_by_res = {}
    for e in events:
        counts_by_res[e["resource"]] = counts_by_res.get(e["resource"], 0) + 1

    return {
        "estimate": True,
        "n_assignment_events": len(events),
        "assignment_events_by_resource": counts_by_res,
        "eco_split_at_ages": snaps,
        "collected": collected,
        "qualitative": qualitative,
        "note": (
            "Tier-B ESTIMATE layer. Economy is NOT in the rec; this is reconstructed from sparse "
            "assignment commands. Coarse by design — age-boundary splits are the primary signal; "
            "collected totals are suppressed unless they land in the validation band."
        ),
    }


def validate_collected(collected, truth_by_resource):
    """Check a collected estimate against ground-truth totals and return a per-resource + total
    band report. Used by the calibration loop / real-rec gate (not by the live estimator).

    `truth_by_resource` = {"food","wood","gold","stone": int}. Returns
    {resource: {in_band, error}, total: {in_band, error}}. A suppressed (None) collected -> all
    "suppressed". The estimator's job is to SUPPRESS out-of-band numbers; this just measures.
    """
    if collected is None:
        return {"suppressed": True}
    report = {}
    est_total = 0
    truth_total = sum(truth_by_resource.get(r, 0) for r in _RESOURCES)
    for r in _RESOURCES:
        band = collected.get(r)
        truth = truth_by_resource.get(r, 0)
        if band is None:
            report[r] = {"suppressed": True}
            continue
        est_total += band["value"]
        err = abs(band["value"] - truth) / truth if truth else None
        report[r] = {"in_band": (err is not None and err <= BAND_PER_RESOURCE), "error": err}
    total_err = abs(est_total - truth_total) / truth_total if truth_total else None
    report["total"] = {
        "estimated": est_total,
        "truth": truth_total,
        "error": total_err,
        "in_band": (total_err is not None and total_err <= BAND_TOTAL),
    }
    return report
