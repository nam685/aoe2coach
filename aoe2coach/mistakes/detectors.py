"""Named pure detector functions referenced by rubric YAML `detector.fn`.

Each function: `fn(recon: dict, params: dict) -> Detection | None`.
- `recon` is #1's Reconstruction.to_dict() (or any dict with the same shape).
- `params` are the calibration knobs supplied by the YAML (thresholds as DATA, not code).
- Return `None` = not flagged; return a `Detection` = flagged.

HONESTY (program-wide): a detector fires ONLY when the underlying datum is present and exact (or a
confident heuristic). Never fire off absent / None data — return None instead. `produced` counts are
upper bounds, so the count-based detectors only flag when BELOW a floor, never when above.

These functions never raise on missing fields: they read defensively and bail to None.
"""

from dataclasses import dataclass, field


@dataclass
class Detection:
    """Returned by a detector when the mistake is present."""

    observed: dict = field(default_factory=dict)  # the actual numbers, e.g. {"tc_idle_s": 142, ...}
    magnitude: float = 0.0  # 0..1 how bad, for ranking


def _num(v):
    """A finite int/float (not bool, not None), else None."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _clamp01(x):
    return max(0.0, min(1.0, x))


def disabled(_recon, _params):  # noqa: ARG001
    """No-op for `needs-#2` reference-only entries. Always returns None (never fires)."""
    return None


# --------------------------------------------------------------------------- efficiency
def idle_tc(recon, params):
    """TC idle beyond a duration-scaled tolerance. Exact (efficiency.tc_idle_s is command-derived)."""
    idle = _num(recon.get("efficiency", {}).get("tc_idle_s"))
    duration = _num(recon.get("meta", {}).get("duration_s"))
    if idle is None or duration is None:
        return None
    base = float(params.get("base_tolerance_s", 25))
    per_min = float(params.get("per_minute_tolerance_s", 2.0))
    tolerance = base + per_min * (duration / 60.0)
    if idle <= tolerance:
        return None
    return Detection(
        observed={"tc_idle_s": round(idle), "tolerance_s": round(tolerance)},
        magnitude=_clamp01((idle - tolerance) / max(tolerance, 1.0)),
    )


def long_vil_gap(recon, params):
    """A single villager-production gap longer than threshold. Exact."""
    gap = _num(recon.get("efficiency", {}).get("longest_villager_gap_s"))
    if gap is None:
        return None
    threshold = float(params.get("max_gap_s", 60))
    if gap <= threshold:
        return None
    return Detection(
        observed={"longest_villager_gap_s": round(gap), "threshold_s": round(threshold)},
        magnitude=_clamp01((gap - threshold) / max(threshold, 1.0)),
    )


# ------------------------------------------------------------------------------ uptimes
def _slow_age(recon, params, age, build_target=None):
    """Shared logic: flag if `ages.<age>_arrival_s` exceeds a target band's upper edge.

    Prefers the build-relative target band (passed by #4 after #3 classifies) under
    recon['_build_target'][age]; falls back to the generic per-pop band in params. Honest: when no
    build is known this is a generic "late vs typical" flag, not "you missed Fast Castle's 8:50".
    """
    arrival = _num(recon.get("ages", {}).get(f"{age}_arrival_s"))
    if arrival is None:
        return None
    target = None
    bt = build_target if build_target is not None else recon.get("_build_target")
    if isinstance(bt, dict):
        band = bt.get(age) or bt.get(f"{age}_arrival_s")
        if isinstance(band, dict):
            target = _num(band.get("max_s")) or _num(band.get("arrival_s"))
        else:
            target = _num(band)
    basis = "build" if target is not None else "generic"
    if target is None:
        target = _num(params.get("generic_max_s"))
    if target is None:
        return None
    if arrival <= target:
        return None
    return Detection(
        observed={f"{age}_arrival_s": round(arrival), "target_s": round(target), "basis": basis},
        magnitude=_clamp01((arrival - target) / max(target, 1.0)),
    )


def slow_feudal(recon, params):
    return _slow_age(recon, params, "feudal")


def slow_castle(recon, params):
    return _slow_age(recon, params, "castle")


def slow_imperial(recon, params):
    return _slow_age(recon, params, "imperial")


# ---------------------------------------------------------------------------- eco techs
def late_or_missing_eco_up(recon, params):
    """Key eco upgrade late or missing vs an age-relative deadline. Exact (tech t_s is exact).

    params.deadlines: {tech_name: {after: feudal|castle|imperial, deadline_s}}. A tech is "late" if
    its t_s exceeds (age_arrival_s + deadline_s); "missing" if the relevant age was reached but the
    tech never appears. Only judges a tech once its gating age was actually reached (honest — we
    don't flag a missing Wheelbarrow if the player never hit Castle).
    """
    eco = recon.get("techs", {}).get("eco")
    ages = recon.get("ages", {})
    if not isinstance(eco, list):
        return None
    by_name = {}
    for e in eco:
        if isinstance(e, dict) and e.get("name"):
            by_name.setdefault(e["name"], _num(e.get("t_s")))
    deadlines = params.get("deadlines", {}) or {}
    late, missing = [], []
    for tech, spec in deadlines.items():
        if not isinstance(spec, dict):
            continue
        after = spec.get("after", "feudal")
        arrival = _num(ages.get(f"{after}_arrival_s"))
        if arrival is None:
            continue  # gating age never reached -> can't judge -> skip (honest)
        deadline = arrival + float(spec.get("deadline_s", 0))
        t = by_name.get(tech)
        if t is None:
            missing.append(tech)
        elif t > deadline:
            late.append({"tech": tech, "t_s": round(t), "deadline_s": round(deadline)})
    if not late and not missing:
        return None
    n_flagged = len(late) + len(missing)
    return Detection(
        observed={"late": late, "missing": missing},
        magnitude=_clamp01(n_flagged / max(len(deadlines), 1)),
    )


# ------------------------------------------------------------------------------ villagers
def too_few_villagers(recon, params):
    """Villager count BELOW a duration-scaled floor. Exact-direction only.

    `villagers_produced` is an upper bound (queued), so a HIGH value can't prove a mistake — we only
    flag when even the upper bound is below the floor, which is unambiguous. Never flags on a high
    count (the calibration game's 126 produced must NOT trip this).
    """
    produced = _num(recon.get("counts", {}).get("villagers_produced"))
    duration = _num(recon.get("meta", {}).get("duration_s"))
    if produced is None or duration is None:
        return None
    minutes = duration / 60.0
    per_min = float(params.get("vils_per_minute_floor", 2.2))
    base = float(params.get("base_floor", 20))
    cap = float(params.get("floor_cap", 110))
    floor = min(base + per_min * minutes, cap)
    if produced >= floor:
        return None
    return Detection(
        observed={"villagers_produced": round(produced), "floor": round(floor)},
        magnitude=_clamp01((floor - produced) / max(floor, 1.0)),
    )


def villager_stall_late(recon, params):
    """Villager production CEASED after Imperial AND the produced count is below a post-Imperial
    floor (stuck at a low vil count post-Imp). Nam-requested. Exact:

    - "ceased" = no villager DE_QUEUE with t_s after imperial_arrival_s (production *stopped* — an
      exact, command-derived fact: absence of a queue command).
    - AND villagers_produced (an upper bound) is still below a floor — so even the optimistic count
      says the eco plateaued low. Both conditions guard against false-firing on a maxed eco.
    """
    ages = recon.get("ages", {})
    imp = _num(ages.get("imperial_arrival_s"))
    if imp is None:
        return None  # never reached Imperial -> N/A
    units = recon.get("production", {}).get("produced_units")
    if not isinstance(units, list):
        return None
    grace = float(params.get("post_imp_grace_s", 0))
    vil_id = int(params.get("villager_unit_id", 83))
    post_imp_vils = [
        u
        for u in units
        if isinstance(u, dict) and u.get("unit_id") == vil_id and (_num(u.get("t_s")) or 0) > imp + grace
    ]
    if post_imp_vils:
        return None  # still queuing villagers after Imperial -> not stalled
    produced = _num(recon.get("counts", {}).get("villagers_produced"))
    floor = float(params.get("post_imp_vil_floor", 100))
    if produced is None or produced >= floor:
        return None  # high produced count -> a maxed eco, not a stall
    return Detection(
        observed={
            "villagers_produced": round(produced),
            "post_imp_vil_floor": round(floor),
            "imperial_arrival_s": round(imp),
            "vils_queued_after_imp": 0,
        },
        magnitude=_clamp01((floor - produced) / max(floor, 1.0)),
    )


# -------------------------------------------------------------------------------- spatial
def exposed_gold(recon, params):
    """Mining camp / eco building in the FRONT (exposed) zone with no nearby protection.
    Nam-requested. Heuristic (zone is opponent-relative geometry; protection proximity is inferred).

    Fires when a mining-camp/eco building falls in spatial.me.eco_exposure.front AND no forward
    military building or wall sits within `cover_dist` tiles of it. Reads the opponent-relative
    `eco_exposure` (front|safe) the core already computes — never guesses without both bases (the
    core returns axis_len=None and everything safe when it can't measure, so this won't fire).
    """
    spatial = recon.get("spatial", {}).get("me", {})
    exposure = spatial.get("eco_exposure")
    if not isinstance(exposure, dict):
        return None
    if exposure.get("axis_len") is None:
        return None  # core couldn't measure exposure (missing a base) -> don't guess
    front = exposure.get("front") or []
    if not isinstance(front, list) or not front:
        return None

    mining_names = set(params.get("mining_building_names", ["Mining Camp"]))
    cover_dist = float(params.get("cover_dist", 12.0))
    forward = spatial.get("forward") or []
    walls = spatial.get("walls") or []

    def _covered(bx, by):
        for f in forward:
            fx, fy = _num(f.get("x")), _num(f.get("y"))
            if fx is not None and fy is not None and ((fx - bx) ** 2 + (fy - by) ** 2) ** 0.5 <= cover_dist:
                return True
        for w in walls:
            for wx, wy in ((_num(w.get("x")), _num(w.get("y"))), (_num(w.get("x_end")), _num(w.get("y_end")))):
                if wx is not None and wy is not None and ((wx - bx) ** 2 + (wy - by) ** 2) ** 0.5 <= cover_dist:
                    return True
        return False

    exposed = []
    for b in front:
        if not isinstance(b, dict) or b.get("name") not in mining_names:
            continue
        bx, by = _num(b.get("x")), _num(b.get("y"))
        if bx is None or by is None:
            continue
        if not _covered(bx, by):
            exposed.append({"name": b["name"], "x": bx, "y": by, "t_s": b.get("t_s")})
    if not exposed:
        return None
    return Detection(
        observed={"exposed_mining_camps": exposed, "axis_len": exposure.get("axis_len")},
        magnitude=_clamp01(len(exposed) / 2.0),
    )


def got_housed(recon, params):
    """Built housing ceiling plateaus low during active production. Heuristic.

    Uses the built-housing ceiling (exact placements) but inferring that the player was *housed*
    (units blocked) from capacity alone is heuristic — deaths aren't logged, so a low ceiling late
    while still producing is suggestive, not proven. Flags when the final housed ceiling is below a
    threshold for the game length AND the player was still producing units late.
    """
    pop = recon.get("population", {}).get("me", {})
    ceiling = _num(pop.get("housed_pop_ceiling"))
    duration = _num(recon.get("meta", {}).get("duration_s"))
    if ceiling is None or duration is None:
        return None
    minutes = duration / 60.0
    min_minutes = float(params.get("min_game_minutes", 12))
    if minutes < min_minutes:
        return None  # too short to judge housing
    expected = float(params.get("expected_ceiling", 100))
    if ceiling >= expected:
        return None
    # still producing late? require army/villager production past a fraction of the game.
    units = recon.get("production", {}).get("produced_units") or []
    late_cut = duration * float(params.get("late_production_frac", 0.5))
    producing_late = any(isinstance(u, dict) and (_num(u.get("t_s")) or 0) > late_cut for u in units)
    if not producing_late:
        return None
    return Detection(
        observed={"housed_pop_ceiling": round(ceiling), "expected_ceiling": round(expected)},
        magnitude=_clamp01((expected - ceiling) / max(expected, 1.0)),
    )


def no_map_presence(recon, params):
    """No forward/expansion buildings and few fights outside own base. Heuristic.

    Flags when spatial.me.forward is empty AND engagements in center/opp zones are below a threshold.
    Heuristic: lack of forward buildings + few aggressive commands suggests passivity, but absence of
    a command isn't proof of passivity.
    """
    spatial = recon.get("spatial", {}).get("me", {})
    forward = spatial.get("forward")
    engagements = recon.get("combat", {}).get("me", {}).get("engagements")
    if forward is None or engagements is None:
        return None
    duration = _num(recon.get("meta", {}).get("duration_s"))
    if duration is None or duration / 60.0 < float(params.get("min_game_minutes", 12)):
        return None
    if forward:
        return None  # has forward presence
    away = [e for e in engagements if isinstance(e, dict) and e.get("zone") in ("center", "opp_base")]
    max_away = int(params.get("max_away_engagements", 1))
    if len(away) > max_away:
        return None
    return Detection(
        observed={"forward_buildings": 0, "away_engagements": len(away), "threshold": max_away},
        magnitude=_clamp01((max_away + 1 - len(away)) / float(max_away + 1)),
    )


def leaky_or_late_walls(recon, params):
    """Walls absent / very sparse while fights happen near own base. Heuristic.

    Flags when own-base engagements occurred (pressure) but wall segments are below a coverage
    threshold. Heuristic: wall *segment count* is a rough coverage proxy; gaps aren't directly
    visible.
    """
    spatial = recon.get("spatial", {}).get("me", {})
    walls = spatial.get("walls")
    engagements = recon.get("combat", {}).get("me", {}).get("engagements")
    if walls is None or engagements is None:
        return None
    own_pressure = sum(
        e.get("n_commands", 1) for e in engagements if isinstance(e, dict) and e.get("zone") == "own_base"
    )
    min_pressure = int(params.get("min_own_base_pressure", 2))
    if own_pressure < min_pressure:
        return None  # no real pressure at home -> not a walling mistake
    min_segments = int(params.get("min_wall_segments", 3))
    if len(walls) >= min_segments:
        return None
    return Detection(
        observed={"wall_segments": len(walls), "own_base_pressure": own_pressure, "min_segments": min_segments},
        magnitude=_clamp01((min_segments - len(walls)) / float(max(min_segments, 1))),
    )


def walled_too_early(recon, params):
    """Heavy dark-age walling before Feudal arrival. Heuristic.

    Flags when several wall segments were placed before feudal_arrival_s. Heuristic: early walls can
    be correct on closed maps; this is a "you walled in the dark age instead of building eco" signal,
    soft by nature.
    """
    spatial = recon.get("spatial", {}).get("me", {})
    walls = spatial.get("walls")
    feudal = _num(recon.get("ages", {}).get("feudal_arrival_s"))
    if walls is None or feudal is None:
        return None
    early = [w for w in walls if isinstance(w, dict) and (_num(w.get("t_s")) or 0) < feudal]
    min_early = int(params.get("min_early_segments", 4))
    if len(early) < min_early:
        return None
    return Detection(
        observed={"early_wall_segments": len(early), "feudal_arrival_s": round(feudal), "min_segments": min_early},
        magnitude=_clamp01(len(early) / float(max(min_early * 2, 1))),
    )
