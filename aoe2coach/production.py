"""Villager production SIMULATION (#1 foundation).

A replay logs villager DE_QUEUE *orders* (queued), NOT pops. Players queue ahead, so the cumulative
queued count badly OVER-counts live villagers: a single Town Center pops one villager every ~25s, so
1 TC cannot have produced 23 villagers by 7:24 (it can produce ~17, +3 starting = ~20). This module
simulates the physical pop timeline.

MODEL (pure, command-derived + a fixed engine constant):
  - TC set = {start TC active from t=0} ∪ {built TCs, each active from its BUILD time}. A 2nd+ TC can
    only be built in Castle Age, so before Castle there is exactly the 1 starting TC.
  - Each villager DE_QUEUE (in timestamp order) is assigned to the earliest-free TC; the villager pops
    at max(queue_time, tc_free_at) + train_time(age at pop); that TC's free-at advances to the pop.
  - train_time defaults to 25s; PERSIANS train faster (TC works +5/10/15/20% faster in
    Dark/Feudal/Castle/Imperial → 25 / that multiplier, by the age at pop time).

`villagers_present(t)` = starting_villagers(civ) + count(pop_time ≤ t). The result is bounded by
starting + (#active TCs)·(t / min_train_time): the production ceiling that the old queued count broke.

Pure: no DB/network/IO. Never raises on missing fields.
"""

from dataclasses import dataclass, field

from mgz.fast import Action

from . import const

# Town Center building ids (const.BUILDING_NAMES entries named "Town Center"). 621 is the id real DE
# recs emit; 71/109 are alternates kept for completeness.
TC_BUILDING_IDS = frozenset(bid for bid, name in const.BUILDING_NAMES.items() if name == "Town Center")

# Base villager train time (seconds) at standard 1.0x game speed.
BASE_TRAIN_TIME_S = 25.0

# The fastest any TC can train a villager (Persian Imperial = 25 / 1.20). Used as the denominator of
# the production-ceiling sanity bound so the bound is never violated by a legitimately-fast civ.
MIN_TRAIN_TIME_S = BASE_TRAIN_TIME_S / 1.20

# Persian TC work-rate bonus by age index (0=Dark,1=Feudal,2=Castle,3=Imperial).
_PERSIAN_TC_SPEED = {0: 1.05, 1: 1.10, 2: 1.15, 3: 1.20}


def _age_index_at(t_s, ages):
    """Age index (0=Dark,1=Feudal,2=Castle,3=Imperial) at time t_s, from ARRIVAL times in `ages`.

    `ages` is the Reconstruction `ages` dict (feudal/castle/imperial `_arrival_s`); missing -> Dark.
    """
    idx = 0
    fa = ages.get("feudal_arrival_s")
    ca = ages.get("castle_arrival_s")
    ia = ages.get("imperial_arrival_s")
    if fa is not None and t_s >= fa:
        idx = 1
    if ca is not None and t_s >= ca:
        idx = 2
    if ia is not None and t_s >= ia:
        idx = 3
    return idx


def train_time_at(civ, t_s, ages):
    """Villager train time (seconds) for `civ` at time `t_s`. 25s default; Persians divide by their
    age-dependent TC speed bonus (faster). `ages` is the Reconstruction ages dict; missing -> Dark."""
    if civ == "Persians":
        mult = _PERSIAN_TC_SPEED[_age_index_at(t_s, ages or {})]
        return BASE_TRAIN_TIME_S / mult
    return BASE_TRAIN_TIME_S


@dataclass
class VillagerSim:
    """Result of simulate_villagers: the physical villager-pop timeline.

    pop_times_s        sorted list of villager pop times (seconds), one per queued villager.
    starting           pre-placed starting villagers (civ-dependent; never queued).
    tc_active_times_s  sorted list of TC activation times (seconds); the start TC is 0.
    """

    pop_times_s: list = field(default_factory=list)
    starting: int = const.STARTING_VILLAGERS_DEFAULT
    tc_active_times_s: list = field(default_factory=list)

    def villagers_present(self, t_s):
        """Live villager count at time t_s = starting + #pops at or before t_s (an estimate of live;
        deaths are not logged, so this is an upper bound on survivors but a faithful PRODUCED count)."""
        # pop_times_s is sorted; count entries <= t_s.
        lo, hi = 0, len(self.pop_times_s)
        while lo < hi:
            mid = (lo + hi) // 2
            if self.pop_times_s[mid] <= t_s:
                lo = mid + 1
            else:
                hi = mid
        return self.starting + lo

    def villagers_produced(self):
        """Total villagers ever produced = starting + all pops by game end."""
        return self.starting + len(self.pop_times_s)

    def n_tcs_active(self, t_s):
        """Number of Town Centers active (producing) at time t_s."""
        return sum(1 for a in self.tc_active_times_s if a <= t_s)

    def curve(self, step_s=30, end_s=None):
        """Villager-present time series: [{"t_s", "villagers"}] sampled every step_s up to end_s
        (defaults to the last pop time). The starting villagers anchor t=0."""
        if end_s is None:
            end_s = int(self.pop_times_s[-1]) if self.pop_times_s else 0
        out = []
        t = 0
        while t <= end_s:
            out.append({"t_s": t, "villagers": self.villagers_present(t)})
            t += step_s
        return out


def simulate_villagers(ops, player, civ, ages, tc_build_ids=TC_BUILDING_IDS):
    """Simulate the physical villager-pop timeline for `player`.

    See module docstring for the model. `civ` drives train time (Persian bonus); `ages` is the
    Reconstruction ages dict (arrival times) used for the age-at-pop train time. `tc_build_ids` is the
    set of Town Center building ids (BUILD ops with these ids add a TC at their build time).

    Returns a VillagerSim. Pure; never raises on missing fields.
    """
    starting = const.starting_villagers(civ)

    # TC activation times (seconds): start TC at 0, plus each built TC at its BUILD time.
    tc_active = [0]
    vil_queue_ms = []
    for t, action_type, data in ops:
        if data.get("player_id") != player:
            continue
        if action_type == Action.BUILD and data.get("building_id") in tc_build_ids:
            tc_active.append(t // 1000)
        elif action_type == Action.DE_QUEUE and data.get("unit_id") == const.VILLAGER_ID:
            amount = int(data.get("amount", 1) or 1)
            vil_queue_ms.extend([t] * amount)
    tc_active.sort()
    vil_queue_ms.sort()

    # Per-TC "free-at" timeline (seconds). A TC cannot pop a villager before it is active, so its
    # initial free-at is its activation time.
    free_at = list(tc_active)  # one entry per TC, in seconds
    pop_times = []
    for qt_ms in vil_queue_ms:
        qt = qt_ms / 1000.0
        # Assign to the TC that can finish this villager earliest. A TC built AFTER the queue time can
        # still be the producer (the player re-queues / the order carries over) — its effective start
        # is max(free_at, activation). free_at already includes activation as its floor.
        best_i = 0
        best_avail = max(free_at[0], qt)
        for i in range(1, len(free_at)):
            avail = max(free_at[i], qt)
            if avail < best_avail:
                best_avail = avail
                best_i = i
        tt = train_time_at(civ, int(best_avail), ages or {})
        pop = best_avail + tt
        pop_times.append(pop)
        free_at[best_i] = pop
    pop_times.sort()
    return VillagerSim(pop_times_s=pop_times, starting=starting, tc_active_times_s=tc_active)
