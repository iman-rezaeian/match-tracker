"""Follow named players through a stint, instead of naming tracks afterwards.

The problem this replaces
-------------------------
Stage 2 detects every body and the identity stage works out afterwards which
body is which child. On same-kit U10s that fails structurally: 4269 track ids
for ~15 players on mrhvbvwi1gjpn (on-pitch +-1.5 m, full game; 5057 raw),
6.0 s median lifespan, ~80% of tracked time unresolved. Detection is NOT the
weak link — 99.5% of frames hold >=14 bodies — so the loss is entirely in
ASSOCIATION.

(An earlier version of this docstring also cited "87% chain impurity". That
figure is RETIRED — see tracking/composition_sampler.py. It was 26/30
`__cant_tell__` labels, i.e. the share the coach could not NAME, misread as the
share holding more than one child. Nothing in that data asserts any tracklet
held two children, so chain impurity is UNRESOLVED, not established. The case
for this module rests on lifespan and unresolved time, which are measured.)

This module deletes the association problem rather than tuning it. The coach
names a player once, at the moment they walk on, and the follower carries that
name forward until they walk off. There is no id space to collapse and no
tracklet chain to keep pure, because identity is an input rather than an output.

The unit is a STINT, not a player
---------------------------------
Nobody plays a whole match. Measured on both Jul-12 games: 12-player squads,
7 on the field, 28 SUB events collapsing into 9-12 batches, **35 stints per
game at a median of 8.0-8.1 minutes**. So the job is not "follow a child for 50
minutes" but "follow a child for 8 minutes, 35 times" — and a stint is bounded
at both ends by the coach log, which `identity._onfield_intervals` already
reconstructs.

Two properties that fall out of the stint model for free:

  * **The roster is exact.** Both games had 0 squad players without a
    reconstructed interval, so at any instant the log names precisely who is on.
    Bench players are ineligible for on-field detections, which is the
    constraint that suppresses drift onto a team-mate.
  * **Sub-outs are drift detectors.** A stint still tracking after its scheduled
    end is wrong by arithmetic — 35 free correctness checks per game, needing no
    labels at all.

Declare, do not guess
---------------------
The follower emits a GAP whenever it cannot justify an attachment: nothing in
reach, two candidates too close to separate, or a sole candidate that does not
fit the prediction. A gap is a review item for the coach and an excluded window
in the metrics — never an interpolated number.

This is deliberate. Measured in `tracking/stint_follow_probe`, every observed
swap declared a gap first and none slid across silently. A declared gap costs
one tap; a silent swap corrupts a whole shift and looks perfectly fine
afterwards, which is how the current pipeline's errors stayed hidden for months.

Why the cost function is this small
-----------------------------------
Distance to a constant-velocity prediction, a physical reach gate, and the
direction-of-travel prior. Appearance is deliberately absent: on identical kits
it is INERT (identical results with it on and off, 0.063 cosine margin, 53%
correct on unambiguous continuations against 76% for heading). Adding it costs
runtime and buys nothing.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Optional

import numpy as np

from .tracking import heading_penalty

# --- Tuning, all measured on mrhvbvwi1gjpn -------------------------------
# A generous U10 sprint; the measured step is 0.08 m per 0.1 s. Anything past
# this is not a child running, so it bounds what a target can reach in dt.
MAX_STEP_MPS = 7.0
# Two candidates closer than this in cost are not separable — declare instead
# of picking. This is the crossing/contest case, where a wrong pick poisons the
# rest of the stint.
AMBIG_MARGIN_M = 0.5
# A SOLE candidate must still be plausible, not merely unopposed. Every swap
# measured in the probe (5 of 5) had exactly one candidate in reach: three were
# detection holes where the true body was missing and someone else sat nearby,
# two were prediction overshoot at sprint speed. Gating lone candidates halved
# the swap rate (0.098 -> 0.057 per minute) and lifted projected stint survival
# from 45% to 64%. 0.35 m is the knee — 0.25 m catches no further swaps and only
# adds gaps.
#
# NOTE: at 10 Hz the reach gate is already MAX_STEP_MPS * 0.1 = 0.7 m, so any
# value above ~0.7 is INERT. Do not "tune" it upward and conclude it does
# nothing; it cannot.
LONE_GATE_M = 0.35
# Longest observation hole to coast through on the velocity estimate. Beyond
# this the prediction is too stale to extrapolate and the target is LOST.
MAX_COAST_S = 1.0
# A lost target searches around its last known position with a reach that grows
# with time, capped here so a long absence does not open the gate to the whole
# pitch. 3 s at 7 m/s is ~21 m, already generous for a U10 field.
REACQUIRE_CAP_S = 3.0
# How far from its last known position a lost target may re-acquire.
#
# Re-acquisition is where identity is most easily lost: there is no live
# prediction, so the only evidence is "a body turned up near where they
# vanished". Unguarded it caused 16 of 27 swaps on Game 1. Correctness against
# held-back ids falls off sharply with the size of the jump:
#
#     jump      n     correct
#     0-1 m    137      85%
#     1-2 m     25      56%
#     2-4 m     17      47%
#     4-8 m     34      38%
#
# Past ~1 m it is close to a coin flip, so anything further is not evidence —
# it is a guess wearing a plausible distance. A declared gap costs one tap; a
# wrong re-acquire silently rewrites the remainder of the stint.
#
# Swept end-to-end on Game 1 (53 seeds), 0.75 m is the knee:
#
#     radius   swaps   rate/min   coverage*
#     0.00 †       8      0.592         7%
#     0.50         9      0.202        86%
#     0.75         9      0.191        86%   <- best
#     1.00        11      0.231        87%
#     2.00        14      0.303        87%
#     4.00        17      0.373        88%
#     († re-acquisition disabled: a lost target stays lost forever, which is
#        the deadlock this whole path exists to fix — note the 7% coverage.)
#
# * READ THIS BEFORE QUOTING THE COVERAGE COLUMN. Those percentages are against
#   48-second pseudo-truth segments, and coverage is attached/asked — so a short
#   question flatters it. Holding the seeds fixed and only extending how long
#   the follower is ASKED to continue:
#
#       asked          median coverage   median actually followed
#       segment only         91%                76 s
#       + 60 s               73%               132 s
#       + 300 s              53%               303 s
#       + 600 s              49%               339 s
#
#   The follower does keep extending; it just does not keep pace with the
#   denominator. A follow sustains roughly 300-340 s before it stops making
#   progress, which against a real 8-minute stint is ~60%, not 86%.
#
#   Re-swept at an 8-minute ask, 0.75 m is STILL the minimum (0.377/min, vs
#   0.469 at 0.50 and 0.435 at 1.00), so the setting is robust to the framing
#   even though the headline rate is not: 0.377/min at a realistic duration
#   against 0.191 on 48-second segments.
REACQUIRE_RADIUS_M = 0.75
# Two targets cannot stand in the same square metre. Used to reject a joint
# assignment that puts two named children on top of each other.
MIN_SEPARATION_M = 0.5
# Weight on the direction-of-travel prior, in metres of equivalent cost.
HEADING_W = 1.0


@dataclass
class Seed:
    """A coach's one-time answer: this body, at this moment, is this player."""
    player_id: str
    t0: float
    xy: tuple[float, float]
    # Stint end from the coach log. The follower stops here; if it is still
    # confidently attached past this point, that is the drift signal.
    t_end: float = float("inf")


@dataclass
class Target:
    """One player being followed through one stint."""
    player_id: str
    x: float
    y: float
    vx: float = 0.0
    vy: float = 0.0
    # Clock of the last frame this target was ADVANCED through, attach or not.
    # Distinct from `t_attach` on purpose: the prediction must extrapolate from
    # the last time we actually SAW the player, but the coast budget has to be
    # measured against wall-clock or a single gap freezes the target forever.
    t_last: float = 0.0
    # Clock of the last successful attachment; anchors the prediction.
    t_attach: float = 0.0
    t_end: float = float("inf")
    alive: bool = True
    # Frames where we could not justify an attachment. These become coach
    # review items and are EXCLUDED from metrics rather than interpolated.
    gaps: list[float] = field(default_factory=list)
    samples: list[tuple[float, float, float]] = field(default_factory=list)

    def predict(self, t: float) -> tuple[float, float]:
        """Extrapolate from the last SIGHTING, not the last frame stepped."""
        dt = t - self.t_attach
        return self.x + self.vx * dt, self.y + self.vy * dt


def _assign(costs: np.ndarray, unmatched_cost: float) -> dict[int, int]:
    """Globally optimal target -> detection assignment under exclusion.

    Greedy nearest-first is wrong here and the failure is not hypothetical: two
    targets converging on one body both want it, and whichever is processed
    first takes it, leaving the other to grab a neighbour it should have
    refused. That is precisely the silent-swap mechanism this module exists to
    prevent, so the assignment has to be solved jointly.

    Padding with a dummy column per target priced at `unmatched_cost` lets the
    solver leave a target UNMATCHED when every real option is implausible,
    rather than forcing an attachment.
    """
    from scipy.optimize import linear_sum_assignment

    n_t, n_d = costs.shape
    if n_t == 0 or n_d == 0:
        return {}
    pad = np.full((n_t, n_t), unmatched_cost, dtype=float)
    full = np.hstack([costs, pad])
    rows, cols = linear_sum_assignment(full)
    return {int(r): int(c) for r, c in zip(rows, cols)
            if c < n_d and np.isfinite(costs[r, c])}


def step(targets: list[Target], dets: np.ndarray, t: float,
         max_step_mps: float = MAX_STEP_MPS,
         ambig_margin_m: float = AMBIG_MARGIN_M,
         lone_gate_m: Optional[float] = LONE_GATE_M,
         heading_w: float = HEADING_W,
         max_coast_s: float = MAX_COAST_S,
         min_separation_m: float = MIN_SEPARATION_M,
         reacquire_cap_s: float = REACQUIRE_CAP_S,
         reacquire_radius_m: float = REACQUIRE_RADIUS_M) -> None:
    """Advance every live target one frame, in place, under mutual exclusion.

    `dets` is (N, 3): x_m, y_m, box_h. Detections carry no identity — that is
    the point. One detection serves at most one target.
    """
    live = [tg for tg in targets if tg.alive and tg.t_last <= t <= tg.t_end]
    if not live:
        return
    if dets is None or not len(dets):
        for tg in live:
            tg.gaps.append(t)
            tg.t_last = t
        return

    n_t, n_d = len(live), len(dets)
    costs = np.full((n_t, n_d), np.inf, dtype=float)
    for i, tg in enumerate(live):
        # Measured from the last SIGHTING. Using the last frame stepped would
        # make dt 0.1 s forever, so a target that lost its body would keep a
        # tight reach gate around a stale position and never recover.
        dt = t - tg.t_attach
        if dt <= 0:
            continue
        if dt <= max_coast_s:
            # Coasting: extrapolate and allow anything physically reachable.
            qx, qy = tg.predict(t)
            ok = np.hypot(dets[:, 0] - qx, dets[:, 1] - qy) <= max_step_mps * dt
        else:
            # LOST. Beyond the coast budget the velocity estimate is worthless,
            # so predicting forward would chase a ghost across the pitch. Fall
            # back to the last KNOWN position with a reach that grows in time.
            #
            # There must be a path back. The first version simply refused any
            # target past the coast budget, which made a >1 s occlusion
            # permanently fatal: on real Game 1 data a target attached 28 times
            # and then declared 2576 consecutive gaps, for 7% coverage. A lost
            # player has not left the planet — they reappear a few metres away
            # a moment later, and re-acquiring them is the difference between a
            # usable stint and a dead one.
            qx, qy = tg.x, tg.y
            ok = (np.hypot(dets[:, 0] - qx, dets[:, 1] - qy)
                  <= min(reacquire_radius_m, max_step_mps * min(dt, reacquire_cap_s)))
        d = np.hypot(dets[:, 0] - qx, dets[:, 1] - qy)
        for j in np.where(ok)[0]:
            c = float(d[j])
            if heading_w > 0.0 and (tg.vx or tg.vy):
                c += heading_w * heading_penalty(
                    (tg.x, tg.y), (tg.vx, tg.vy), (dets[j, 0], dets[j, 1]),
                    box_h=float(dets[j, 2]))
            costs[i, j] = c

    # Unmatched must be priced above any acceptable match but below a bad one,
    # so the solver prefers a plausible body and abstains otherwise.
    unmatched = max_step_mps * max_coast_s + heading_w + 1.0
    match = _assign(costs, unmatched)

    for i, tg in enumerate(live):
        j = match.get(i)
        if j is None:
            tg.gaps.append(t)
            tg.t_last = t
            continue
        row = costs[i]
        finite = np.where(np.isfinite(row))[0]
        # Ambiguity is judged over what this target could have taken, before
        # exclusion removed options — otherwise a contested crossing looks
        # unambiguous simply because a rival already claimed the alternative.
        if len(finite) > 1:
            srt = np.sort(row[finite])
            if (srt[1] - srt[0]) < ambig_margin_m:
                tg.gaps.append(t)
                tg.t_last = t
                continue
        if (t - tg.t_attach) > max_coast_s:
            # RE-ACQUIRING: the radius cap in the reach stage is the guard.
            pass
        elif lone_gate_m is not None and len(finite) <= 1:
            # The SOLE-candidate case, and only while the prediction is live. A
            # LOST target has no trustworthy prediction to sit near, so applying
            # this gate there would reject every re-acquisition and reinstate
            # the deadlock.
            qx, qy = tg.predict(t)
            if math.hypot(dets[j, 0] - qx, dets[j, 1] - qy) > lone_gate_m:
                tg.gaps.append(t)
                tg.t_last = t
                continue
        nx, ny = float(dets[j, 0]), float(dets[j, 1])
        # Two named children cannot occupy the same square metre. If the
        # solver produced that, both claims are suspect — declare for this one
        # rather than silently keeping a physically impossible pair.
        clash = any(
            math.hypot(nx - o.x, ny - o.y) < min_separation_m
            for o in live if o is not tg and o.t_attach == t)
        if clash:
            tg.gaps.append(t)
            tg.t_last = t
            continue
        dt = t - tg.t_attach
        if dt > max_coast_s:
            # Re-acquired after being lost. The displacement spans an unobserved
            # hole, so dividing it by dt would report a speed the child never
            # ran and poison the next prediction. Start the velocity estimate
            # afresh; the gap is already recorded and excluded from metrics.
            tg.vx = tg.vy = 0.0
        elif dt > 0:
            tg.vx, tg.vy = (nx - tg.x) / dt, (ny - tg.y) / dt
        tg.x, tg.y = nx, ny
        tg.t_last = tg.t_attach = t
        tg.samples.append((t, nx, ny))


def follow_stints(frames: Iterable[tuple[float, np.ndarray]],
                  seeds: list[Seed], **kw) -> list[Target]:
    """Run every seeded stint jointly across a frame stream.

    `frames` yields (time_s, dets) with dets (N,3) of x_m, y_m, box_h —
    id-stripped by construction, because identity comes from the seed.
    """
    by_t0: dict[float, list[Seed]] = {}
    for s in seeds:
        by_t0.setdefault(s.t0, []).append(s)
    pending = sorted(by_t0)
    targets: list[Target] = []
    live: list[Target] = []
    pi = 0

    for t, dets in frames:
        while pi < len(pending) and pending[pi] <= t:
            for s in by_t0[pending[pi]]:
                tg = Target(player_id=s.player_id, x=s.xy[0], y=s.xy[1],
                            t_last=t, t_attach=t, t_end=s.t_end)
                tg.samples.append((t, s.xy[0], s.xy[1]))
                targets.append(tg)
                live.append(tg)
            pi += 1
        for tg in live:
            if tg.alive and t > tg.t_end:
                tg.alive = False
        live = [tg for tg in live if tg.alive]
        if live:
            step(live, dets, t, **kw)
    return targets


def coverage(tg: Target) -> float:
    """Fraction of the stint actually observed, not interpolated.

    Reported ALONGSIDE every metric. A low distance total on 40% coverage means
    "we only saw 40% of this child", not "this child did not run" — conflating
    those is how the current pipeline produced confidently wrong per-player
    numbers.
    """
    n = len(tg.samples) + len(tg.gaps)
    return len(tg.samples) / n if n else 0.0


def distance_m(tg: Target) -> float:
    """Distance over OBSERVED samples only.

    Gaps are skipped rather than bridged: interpolating across a hole invents
    metres the child may not have run, and the coach asked for accurate
    per-player numbers over complete-looking ones. Always read with
    `coverage()`.
    """
    if len(tg.samples) < 2:
        return 0.0
    a = np.array([(x, y) for _, x, y in tg.samples], dtype=float)
    return float(np.hypot(*np.diff(a, axis=0).T).sum())


def drift_check(tg: Target, tol_s: float = 5.0) -> bool:
    """True if this stint was still attached past its logged sub-out.

    The free correctness check: the coach's log says the child left the field,
    so a follower still confidently tracking someone is on the wrong body. 35
    of these per game, needing no labels. Tolerance absorbs the coach's tap
    latency, measured at a 13-15 s median but bounded here to the stint end
    itself since seeds are placed from the video, not the tap.
    """
    if not tg.samples or not math.isfinite(tg.t_end):
        return False
    return tg.samples[-1][0] > tg.t_end + tol_s
