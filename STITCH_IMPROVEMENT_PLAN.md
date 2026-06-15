# Stitching improvement plan — best practices, applied honestly

Companion to `METRICS_RELEVANCE_PLAN.md`. Focuses **specifically on the
tracklet-stitching layer** — what state-of-the-art MOT does that we don't, what
actually moves the needle in our setting (fixed 8K 360° camera, identical kits,
~12 players/team), and the precision/recall trade-offs that have already been
measured.

---

## TL;DR

Three honest framings up front:

1. **Pure geometric stitching has a measured ceiling.** Within-team spatio-
   temporal chaining caps at ~**814 clean chains** for 12 players (≈70:1
   fragmentation). We need ~16. The remaining 50× collapse is information
   geometry cannot recover — it requires identity signal.
2. **Same-kit appearance is dead.** OSNet embeddings between random *cross-team*
   tracks give cosine 0.62–0.75 — they can't even separate teams reliably, let
   alone teammates. Any plan that leans on "better Re-ID" is fighting physics.
3. **Best practices we haven't tried still leave real value on the table.**
   Co-segmentation cannot-link constraints, global min-cost flow,
   motion-extrapolated costs, and switch detection are field-standard and
   would meaningfully improve *precision* (which is currently the binding
   constraint on running stitching looser). They won't get us to 16, but they
   buy headroom for the identity layer to do its job.

So the plan: tighten the **precision** of stitching so it can safely run
**looser** (fewer false merges → more correct merges), then couple with
anchor-and-propagate identity. Stop chasing the count.

---

## Where we stand (measured)

From `METRICS_RELEVANCE_PLAN.md` + the eval harness:

| Stage | Belle River (8K) | Read |
|---|---|---|
| Detection | ~86% body coverage / frame | not the bottleneck |
| Raw tracker output | 2,887 fragments, median 14 s | severely fragmented |
| Gap-split (clean contiguous) | 5,765 sub-fragments | teleports removed |
| Current stitch (gap≤10 s, OSNet+HSV weighted) | 499 tracklets | only 3:1 merge |
| Pure ST within-team (gap≤5 s, dist-cap 12 m) | ~814 chains | geometry ceiling |
| Identity assignment (auto only) | 5–7.5 % named | the real chokepoint |
| Identity assignment (auto + coach FIX-IDS) | 23–25 % named | bounded by reviewer effort |
| **Ideal** | ~16 per game | unreachable from geometry alone |

**Mechanism of the loss.** Each player generates 50–150 raw fragments. Stitching
collapses easy short-gap continuations into a few dozen tracklets per player.
But the remaining fragmentation is *gap-limited* (long out-of-frame stretches)
or *crossing-limited* (two same-team players pass close together → tracker
swap), and same-kit appearance can't resolve either. Geometry alone hits the
~70:1 floor and stops.

**Where false merges hide.** The 499 → 814 jump under loose-gap settings looked
like a win until `tracking/stitch_review.py` crops surfaced cross-team merges
and 42 m "runs" in 4.9 s. The current 499 is not 499 *correct* tracklets either
— it contains teleport-zombies that the gap-split flag (off by default) would
break up. We're flying without a precision instrument.

---

## The fundamental constraint

Tracklet stitching at its core is solving:

> Given N tracklets with start/end (t, x, y) and noisy per-tracklet features,
> partition them so each part = one physical player.

Best practices solve this with three signal sources:

1. **Time / space continuity** (motion model).
2. **Appearance** (Re-ID embedding, color histogram).
3. **Hard constraints** (cannot-link from co-segmentation).

In our setting **(2) is empirically broken**, so (1) and (3) must carry it. The
plan is structured around getting maximum mileage from (1) + (3) and then
introducing identity priors as a **fourth signal source** (the real win).

---

## Best-practice catalog — rated for this setting

### Tier A: real leverage, not yet implemented

**A1. Co-segmentation hard cannot-link.**
Two tracklets active at overlapping times *cannot* be the same player. This is
a hard constraint, free precision. Our current code only checks pairwise
"successor starts after predecessor ends" — it does not enforce that two
tracklets within a *chain* never overlap. With min-cost flow or graph
partitioning, this is an ∞-cost edge between every concurrent pair.

> **Why it matters in same-kit setting**: appearance is dead, so the only way
> to safely run a looser gap is more aggressive *negative* evidence. Cannot-link
> rules out ~70 % of candidate merges that geometry alone allows.

**A2. Replace greedy chaining with global min-cost flow (Pirsiavash 2011 /
Zhang 2008 style).**
Today `reid_stitch.py` is greedy: each fragment picks its lowest-cost successor
in time order. Greedy locks in bad early decisions. Min-cost flow gives a
globally optimal partition under the same cost model (O(N² log N) for our N≈
1500 — milliseconds). Standard reference: `networkx.min_cost_flow` or `ortools`.

> **Why it matters**: when fragment A has two plausible successors B and C
> (common when two same-team players cross), greedy picks one and orphans the
> other. Global flow resolves the *joint* assignment.

**A3. Hierarchical multi-pass stitching.**
- **Pass 1**: gap ≤ 2 s, very tight (predicted-vs-observed < 1 m). High
  precision. Collapses easy continuations.
- **Pass 2**: on pass-1 tracklets, gap ≤ 5 s with motion-model cost.
- **Pass 3**: on pass-2 tracklets, gap ≤ 10 s with motion + co-segmentation.

Each pass operates on the *cleaner* output of the previous and can therefore
run looser without precision collapse. Field-standard in MOT (DeepSORT's
cascading matching, ByteTrack's two-stage assoc) — adapted to offline tracklet
stitching here.

> **Why it matters**: today we have one global gap of 10 s and one cost
> function. The same threshold is too loose for short gaps (allowing
> teleports) and too tight for long ones (missing legitimate re-acquisitions
> across occlusions). A staged approach Pareto-dominates.

**A4. Motion-extrapolated cost (not endpoint distance).**
Today's cost is `|p_b.start − p_a.end|`. Better: use the terminal velocity of
A (mean dx/dt, dy/dt over the last ~1 s) to predict `p_a(t_b.start)`, compare
to `p_b.start`. A player running left is unlikely to re-emerge to the right.
Even a simple constant-velocity model halves false merges on crossing players.

> **Why it matters**: most stitch errors today are direction-agnostic — A and
> B happen to be at endpoint-close positions but A was running away from B's
> start. Motion model adds the missing prior.

**A5. Cross-tracklet switch detection.**
Within a single tracker-output `track_id`, identity sometimes switches mid-
track (two players cross, tracker drifts to the other). Our `gap_split.py`
only splits on **time** gaps; it does not split on **identity drift**.
Detect a switch via:
- Sudden velocity reversal (> 90° in < 200 ms with no detection gap).
- Sudden appearance shift (HSV mean delta > threshold within a same-`track_id`
  window).
- Pose/height shift if pose features are available.

Then split at the detected boundary. This is "free" upstream cleanup.

> **Why it matters**: switches are why some tracklets read as "perfect identity
> coverage" but the player swaps mid-tracklet — the swap-polluted distance
> mode the stats fix only treats post-hoc. Better to split before assignment.

**A6. Soft costs + global optimizer (not hard gates).**
Today's pipeline is mostly *hard gates* (gap < 10 s OR reject; dist < cap OR
reject). Soft costs encode all evidence into one objective and let the global
optimizer trade off. Standard cost function:

```
cost(A, B) = w_gap·gap
           + w_geom·|predicted_p_a(t_b) − p_b.start|
           + w_motion·angle_between(v_a_term, v_b_init)
           + w_app·(1 − cos(emb_A, emb_B))         # if features informative
           + ∞ if overlap(A, B) > δ                # cannot-link
           + ∞ if needed_speed(A, B) > cap         # physical
```

> **Why it matters**: hard gates are brittle. A pair just inside the gap but
> with strong motion agreement is cheaper than a pair just outside the gap
> with no motion agreement — but today the second is rejected and the first
> accepted, with no comparison. Soft costs make stitching tunable from a
> single Pareto front.

---

### Tier B: best-practice, but capped in our setting

**B1. Kit-invariant appearance features.** Mask the torso region before
embedding; use head + arms + legs only. Worth a probe but the OSNet model
isn't trained for that crop distribution, so we'd need to fine-tune. ~2× cost
of A1–A6 for likely marginal lift — defer until A1–A6 are landed and we know
the residual error mode.

**B2. Pose / skeleton features (RTMPose).** Height (ankle-to-head keypoint
span), limb-length ratios. Invariant within a game. Requires running RTMPose
on the source video — a re-render pass. **The lever is real but the cost is
high**: only worth it if A1–A6 leave a residual that's clearly
height-distinguishable (a 4'9" GK vs a 5'2" striker, say). Mark for later.

**B3. Multi-frame appearance aggregation.** Instead of mean OSNet over the
tracklet, cluster embeddings into k=2–3 representatives (handles pose/lighting
shifts) and use best-of-best similarity. Easy to implement; will help margin-
ally given B1's verdict. Free piggyback when we touch the appearance code.

**B4. Better stitching algorithms widely cited (StrongSORT, BoT-SORT v2,
GHOST, Deep-EIoU).** These are online trackers — we already use BoT-SORT
upstream. The relevant *post-hoc* stitching pieces (AFLink, GSI from
StrongSORT) are essentially A2 + A4 with our notation; worth porting code
patterns from but not architectural replacements.

---

### Tier C: field-standard but doesn't fit

**C1. End-to-end learned re-association (transformer trackers, Tracktor++,
MOTRv3).** Trained on broadcast/surveillance footage. Equirect projection
domain gap; same-kit problem unchanged. Not where the marginal dollar lives.

**C2. SAM2-based tracking (SAMURAI, SAM2MOT).** Tracking layer, not stitching
— would replace boxmot. Documented in `EIGHT_K_RETEST.md` as a deferred
detection/occlusion lever, not stitching.

**C3. Graph neural networks for tracklet association (MPNTrack, etc.).** GNN
operating on the tracklet graph with anchors as labeled nodes is essentially
"anchor-and-propagate, learned." Same idea, harder to debug, needs training
data we don't have. Use rule-based propagation first (Phase 4 below).

---

## What stitching alone can NEVER do here (be honest with the user)

No amount of cleverness on (time, x, y, kit-color, kit-color) collapses
50–150 fragments-per-player to one. That collapse requires *which player is
this* signal. Three sources of that signal exist:

- **Action events** (GOAL/ASSIST/SAVE at a known clock time and place) —
  already partially used (~15 events / game, ~75 votes added in P1).
- **Board template** (position at a window-start time) — already used; the
  ambiguity within zones is the dominant residual.
- **Coach FIX-IDS overrides** — high quality but reviewer-bounded.

Stitching's job is to make these signals propagate **as far as possible**
along continuity chains. Every false merge poisons a chain, every missed
merge halves propagation reach. **Precision is therefore worth more than
recall** in stitching specifically. Run tight, let identity reach further.

---

## Phased implementation plan

### Phase 0 — Build the validation instrument (1–2 days, mandatory)

Without this we cannot tell whether any change is a real win or a count
illusion (the existing `eval_stitch_assign.py` measures NAMED-COVERAGE
end-to-end, not stitch precision in isolation).

- Hand-label **200 tracklet pairs** across the 4 games using
  `tracking/stitch_review.py`-style side-by-side crops. For each: same player
  / different / can't tell.
  - Sample stratified: 50 short-gap (< 3 s), 50 medium (3–10 s), 50 long
    (10–30 s), 50 cross-team (negative controls).
- Build `tracking/stitch_pr_eval.py` that takes a stitching config and prints
  precision / recall / F1 / area-under-PR on the labeled set.
- Land it before any algorithmic change. Anything that doesn't move PR-AUC is
  not real.

### Phase 1 — Co-segmentation + motion model + global optimizer (3–5 days)

These three together because they share the same code surface (replacing the
greedy loop in `reid_stitch.py`).

1. **Build the cost matrix** for all candidate (A, B) pairs (∞ where
   overlap > δ, ∞ where needed-speed > cap, soft costs otherwise).
2. **Add motion-extrapolated cost** — terminal velocity over the last ~1 s of
   A, predicted position at `t_b.start`, geom cost = predicted vs observed.
3. **Solve with min-cost flow** (`networkx.min_cost_flow` or `scipy` LP).
   Output is a partition, not chains, so the "successor used once" greedy
   constraint is enforced cleanly by the flow capacity.
4. **A/B against current greedy** on the Phase-0 labeled set. Ship only if
   PR-AUC improves with no precision regression at the operating point.

Expected: 5–15 pp precision lift at the same recall, enabling a wider gap
(15–20 s) without false merges. The count may *not* drop much — that's
expected; the win is fewer poisoned chains.

### Phase 2 — Hierarchical multi-pass + switch detection (2–3 days)

1. **Hierarchical**: wire `reid_stitch.stitch_tracklets` to run three passes
   with progressively looser gaps. Each pass is a Phase-1 min-cost flow on
   the current partition.
2. **Switch detection in `gap_split.py`**: add the velocity-reversal /
   appearance-shift split. Reuses the same scan loop as the existing time-
   gap split.
3. A/B on the Phase-0 labeled set. Hierarchy alone shouldn't move PR-AUC
   much (it's a runtime / convergence improvement), but switch detection
   should bump precision on the medium-gap stratum.

### Phase 3 — Iterative re-stitch with identity anchors (1 week, biggest win)

This is where stitching couples with identity. The current pipeline runs
**stitch → assign**, monodirectionally. Field practice in tracking is
**iterate**:

1. Round 1: Phase-1 stitching (tight, high-precision) → tracklets.
2. Round 1: anchor-and-propagate identity (per `METRICS_RELEVANCE_PLAN.md`
   Step 2 prototype) → named tracklets.
3. **Round 2 stitching: identity becomes a hard signal.** Two named
   tracklets with *different* players? Cannot-link. Two named tracklets with
   the *same* player but a 20 s gap that geometry alone would never bridge?
   Force-link.
4. Round 2: re-run anchor propagation on the longer chains.
5. Iterate until convergence (typically 2–3 rounds).

This is the same idea as EM / co-training, applied to the stitch ↔ assign
coupling. Expected to move NAMED-COVERAGE from ~23 % to 40–60 % without
adding any new signal — just letting the existing signals reinforce.

### Phase 4 — Pose / kit-invariant appearance (deferred, 1–2 weeks)

Only worth doing if Phase 3 leaves a residual error mode that's clearly
height- or shape-distinguishable. Probe first (RTMPose on 100 hand-picked
ambiguous pairs from Phase 0), measure separability, decide.

---

## Validation infrastructure (cross-cutting)

- `tracking/stitch_pr_eval.py` (Phase 0) — precision-recall on labeled pairs.
- `tracking/eval_stitch_assign.py` (exists) — end-to-end NAMED-COVERAGE %.
- `tracking/stitch_review.py` (exists) — side-by-side crops for spot checks.
- New: `tracking/stitch_ablation.py` — sweep gap × cost-weights × passes,
  emit PR-AUC + named-coverage matrix. Surface the Pareto front so config
  changes are evidence-driven.
- Lock in a regression check: any change that drops PR-AUC by > 2 pp at
  unchanged recall fails the eval.

---

## Risks and what would invalidate this plan

- **Phase 0 might surface that we're already at the precision ceiling.** If
  the current 499 tracklets are 95 %+ precise and the false-merge rate is
  already low, A1–A6 give diminishing returns and Phase 3 is the only real
  lever. *This would be a useful finding* — we'd stop investing in stitching
  algorithm and pour everything into identity.
- **The labeled set may be ambiguous in the hard cases.** Same-team players
  in the same zone with similar build are hard for humans too. Use "can't
  tell" as a third class; cost-weight algorithm choices by the unambiguous
  pairs.
- **Min-cost flow at our scale** (≈1500 ours / game) is fine on CPU, but the
  full cost matrix is dense — sparsify by max-gap pruning before solving.

---

## What's deliberately out of scope

- **Online tracker replacement** (BoT-SORT → ByteTrack/StrongSORT/BoT-SORT v2).
  The tracker isn't the bottleneck.
- **Re-running detection** on the source video. Detection is solved.
- **Cross-game Re-ID** (training a player-specific Re-ID on previous games).
  Plausible but deferred until we have ground truth labels at scale.
- **Ball tracking / ball-following reel.** Separate workstream (see
  `EIGHT_K_RETEST.md`).

---

## North-star metric

**NAMED-COVERAGE % on the eval harness, with PR-AUC of stitch as a guardrail
that prevents the named coverage being "cheap" (correct by chance).**

Today (Belle River, with coach overrides): 23 %.
Phase 1 target: 25–28 % (precision lift, not count).
Phase 3 target: 40–60 % (the real win — iterative stitch+assign coupling).
Asymptote with available signals: ~70 % (per the anchor-and-propagate
prototype; the residual is *irreducible* without pose/height/number).
