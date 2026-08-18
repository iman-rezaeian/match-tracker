# Per-player metrics — every path measured, and the decision

**Written 2026-08-09.** Author: a session that started from the coach's proposal to
replace inferred identity with seeded following, and ended by measuring every remaining
route to per-player numbers. Companion to `METRIC_ACCURACY_ROADMAP.md` (the tier list this
provides the measured backing for) and `ACCURACY_AUDIT.md`.

**STATUS: the per-player DISTANCE question is closed. Position metrics are achievable
today; distance/sprint metrics need a wearable.** Everything below is measured on Game 1
`mrhvbvwi1gjpn` against the clean re-track (opponent filter OFF), unless stated.

---

## 1. The decision, up front

| approach | coach time | coverage of player-time | verdict |
|---|---|---|---|
| automatic identity | 0 | ~20% | broken |
| checkpoint seeding (every 2 min) | 15 min | 35% | weak |
| fragment verification | 1 h | 52–62% | poor trade |
| click-and-fix (retag while it plays) | **~1,900 clicks/game** | 75% | **unusable** |
| wearables / GPS pods | 0 | ~100% | works, costs money |

424 player-minutes exist per game (8 on-field × 53 min).

**Position-based metrics are SAMPLES** — average position, territory, width/depth, time in
each third, positional drift between halves. Catch a player 200 times at random moments and
these are accurate even at 35% coverage. Sampling error, no systematic bias. **These are
achievable now.**

**Distance, sprints and high-intensity work are INTEGRALS.** At partial coverage they are
biased LOW, and biased worst on the hardest workers, because tracking drops out during
congested play. Report as "measured over N% of his time" and normalise per observed minute
when comparing players — never as a total.

---

## 2. Why the verification routes cap out

The intuition behind fragment verification is sound: the tracker sees the players, it just
cannot connect them, and a human can. **The blocker is that the fragments themselves are
already mixed.**

Teleport oracle (a jump inside one track id faster than 7 m/s — a child cannot run it):

| fragment length | share with ≥1 teleport | median teleports |
|---|---|---|
| 10–20 s | 76% | 3 |
| 30–60 s | 95% | 7 |
| 60–120 s | 96% | 10 |
| 120 s+ | **100%** | 18 |

These are real body-swaps, not projection noise: **3,052 jumps move >2 m within a single
0.1 s frame**, across 1,123 tracks, against a normal step of 0.118 m.

So confirming a long fragment would launder swaps into coach-authorised data — worse than
not verifying, because it carries the coach's authority.

**Cutting every fragment at its internal teleports** yields clean-by-construction
sub-fragments: **9,906 of them, median life 1.1 s**, 649 min total. That is the honest unit
of clean signal. Judging them longest-first (our players only):

| coach time @3 s/tap | fragments | coverage |
|---|---|---|
| 10 min | 200 | 28% |
| 15 min | 300 | 35% |
| 30 min | 600 | 49% |
| 1 h | 1,200 | 62% |
| 4 h (all) | 4,953 | **77% — hard ceiling** |

Diminishing steeply: the first 10 minutes buys 28%, the second hour ~8 more points. The
unreachable 23% is player-time with no usable track at all.

---

## 3. Click-and-fix, measured

The coach's refinement — play the video with everyone boxed and labelled, click to correct
when a label is wrong. Simulated by replaying the follower and correcting it whenever the
box is visibly wrong for `grace` seconds. **Per player followed; multiply by 8 to watch a
team:**

| reaction time | clicks/min (8 players) | clicks per 50-min game | on-target |
|---|---|---|---|
| instant | ~226 | 11,000 | 91% |
| **1 s** | **~39** | **~1,900** | **75%** |
| 3 s | ~24 | ~1,200 | 68% |
| 5 s | ~18 | ~900 | 63% |

It also degrades as the coach slows down. ~1,900 clicks for 75% coverage is not a workflow.

---

## 4. Root cause: fragmentation, not identity

The clean-cache scene is healthy — **17 bodies/frame full-game (18 in a 15-min window),
5.7 s median track lifespan, pitch thirds even.** Detection is fine (99.5% of frames hold
≥14 raw bodies). What fails is association, and it fails below the level any identity
scheme can repair:

- clean (teleport-free) fragments live **1.1 s** median
- per 4-second labeling clip on the clean cache: **7%** ≥90% tracked, **63% MIXED**, median
  tracked fraction **32%**
- a follower cannot follow a body that was never tracked

Four tracker knobs are measured inert (thresholds, buffer, heading, appearance — see
`stage2` notes and the sweep). **Swapping trackers is a lateral move, not a generational
one:** BoT-SORT is 2022 and boxmot's alternatives (ByteTrack 2022; OC-SORT, StrongSORT,
DeepOCSORT, HybridSORT all 2023) are the same Kalman + IoU + appearance family.
`TRACKER_TYPE` at `config.py:134` is a dead knob (declared, referenced nowhere), so the
swap is cheap and worth trying — but expect a modest change, not a fix. A genuine
alternative would be a different paradigm (transformer / end-to-end MOT), not installed and
unevidenced here.

---

## 5. What was built (branch `feat/stint-following`, unmerged)

The coach's seed-and-follow idea, implemented and parked **on fragmentation, not on its own
merits.** Resume if median track lifespan moves materially off 5.7 s.

- `post_game/stint_follow.py` (+ 22 tests) — joint follower, all 12 squad members advance
  per frame under mutual exclusion, bench ineligible. Every rejection is a **declared gap**,
  never a guess.
- `tracking/stint_follow_probe.py`, `stint_follow_eval.py` — survival probe and a runner
  over real coach-log stints.
- `tracking/stint_label_render.py` / `stint_label_app.py` / `stint_label_score.py` — clip
  renderer, Streamlit labeling app, scorer.
- `tracking/composition_sampler.py` / `composition_app.py` — "one child or several?"
  sampler. Built and smoke-tested, never run on a real sample.
- `tracking/kit_vote_audit.py` — the tool that found the opponent-filter bug.

**Results worth keeping even though the approach is parked:**

- **Zero silent swaps** across every measurement — every swap declared a gap first. Loud
  failure should be a REQUIREMENT of any future architecture, not a nice-to-have.
- **Lone-candidate plausibility gate**: a sole candidate must be *plausible*, not merely
  unopposed. Halved the swap rate (0.098 → 0.057/min). All five diagnosed swaps had exactly
  ONE candidate in reach — there was never a tiebreak to win.
- **Re-acquire radius 0.75 m**, optimal at both 48-second and 8-minute framings.
  Correctness against held-back ids: 85% within 1 m, 56% at 1–2 m, 38% past 4 m. Anything
  above ~0.7 m is inert at 10 Hz (the reach gate already binds tighter).
- **`heading_penalty` is inert here too** — independent confirmation of the sweep's finding,
  by a different route: with a candidate set of size 1 there is nothing to disambiguate.

---

## 6. Practical notes for whoever builds the next labeling tool

- **Still crops are not labelable.** Median detection box is 77 px; 63% are under 100 px.
  Identity comes from watching MOVEMENT, so the unit must be a clip, not a crop strip.
- **The referee is a fourth category.** He belongs to neither team, so a two-team colour
  vote has no correct answer for him, and he roams the pitch so no touchline test excludes
  him. Every "7v7 = 14 bodies" figure is really 15.
- **A clip can be MIXED** — lost for part, tracking for part (63% are). One verdict per clip
  is then ill-posed; record the tracked fraction and say "NO TRACK **this frame**".
- **Ask composition, not identity**, when the question is chain purity: "one child or
  several?" is answerable on a player nobody can name. Asking "who is this?" produced 26
  `__cant_tell__` of 30 and a retired headline number.
- OpenCV's `mp4v` writes MPEG-4 Part 2, which **no browser plays**. Transcode to
  H.264 + yuv420p + faststart.

---

## 7. Coach decisions recorded

- **Wants per-player numbers, but not via a wearable.** Honest answer: position metrics yes,
  distance no.
- **Design for a full half (25 min), not the 8-minute median stint** — in tournaments he
  rotates less and a player can stay on throughout. Downscaling later is easy; the reverse
  is not.
- **Voice memos failed because they were attempted DURING the game**, not because voice is
  the wrong medium. Post-game voice is untested and is the **recommended next investment** —
  it is identity-independent, so it is trustworthy regardless of tracking state, and the
  pipeline already exists (transcribe → extract → confirm-queue). Caveat: voice does not
  localise, so per-player metrics still need a pointer as well as a name.
- Rejected number-not-on-roster as a team signal: numbers overlap between teams and read
  coverage is ~30%. It stays a per-detection cannot-link tiebreaker.
