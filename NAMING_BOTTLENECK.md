# The naming bottleneck — measured state, 2026-08-05

**One-line problem: the software finds the players and then fails to name them.**
7 are on the field; 3.55 get named. Detection is healthy, measurement precision is
near-perfect, and essentially the entire per-player accuracy loss is attribution.

Read this before re-opening per-player accuracy. Several long-standing conclusions
in `ACCURACY_AUDIT.md` and the tracking memories are **retracted below** — check
the retraction list before trusting an older doc.

---

## Verified measurements (trust these)

All on W8 `mri01pvelv46d` (Caboto, 2026-07-12), the best ground-truth game: 145
coach hand-labels **and** video still on disk at
`~/Movies/stompers/VID_20260712_Game2.mp4` (7680×3840 equirect, 29.97 fps, 80 GB).

### The instrument: a real precision ceiling
| test | result | what it means |
|---|---|---|
| same minutes, two independent samplings (odd vs even frames) | **r = 0.984**, 1.9% typical disagreement | the measurement chain is near-perfect |
| per-player, first half vs second half | **r = 0.004** | per-player output is noise |
| within one track, first half vs second | r = 0.755 | mixes real effort change; NOT a ceiling |

**Use r = 0.98 as the target and per-player half-to-half r as the score.** Any
identity change gets measured this way. Coverage is not a valid score — the VLM
run raised named coverage while r stayed flat (0.004 → −0.027).

> **⚠ 2026-08-05 amendment — the headline r is UNDERPOWERED on one game.**
> The scorer now exists (`tracking/half_split_r.py`) and its odd/even control
> reproduces the ceiling exactly (**+0.980** on W8, +0.994 / +0.991 on two other
> games), so the instrument is sound. But per-player half-to-half on a SINGLE
> game rests on n≈6–9 players, and at that n it is not a usable score:
>
> | | |
> |---|---|
> | W8 baseline, single game | r = **+0.381**, bootstrap 95% CI **[−0.71, +0.95]** |
> | leave-one-out range on those same 7 players | **−0.206 … +0.832** |
> | changing only the tracked-time floor (60 s → 0 s, adds 1 player) | **+0.381 → −0.242** |
> | pooled over 3 games (within-game z-scored, n=22) | r = **+0.329**, CI **[−0.06, +0.65]** |
>
> The doc's original **+0.004 is inside the noise band of +0.381** — they are the
> same measurement at different filter settings, not a contradiction. Dropping
> one player moves r by more than a full correlation unit, so **never report a
> single-game r**. Pool across games (z-scored within game) and quote the CI;
> even pooled, only changes of roughly ≥0.3 are resolvable.

### The naming gap
| | |
|---|---|
| bodies detected on the pitch, per second | **17.0** (14 players + ref + adults) |
| ≥7 of our tracks on the pitch | **97.7%** of play |
| substantial tracks (≥30 s) per second | **6.3** ≈ the 7 target |
| **actually named** | **3.55 of 7** |
| **all 7 named simultaneously** | **2.65%** of play |
| 3 or fewer named | 50.1% of play |
| `unknown` share of our non-opponent time | **76%** (12,191 track-min discarded vs 3,824 named) |

`ID_CONFIDENCE_STATS_MIN = 0.35` (`config.py:258`) is the gate that dumps a
tracklet into `unknown`.

### Fragmentation cause: detector confidence on small targets
| depth band | median bbox | fragments per tracked-min |
|---|---|---|
| 0–8 m (far) | 48 px | **5.8** |
| 16–24 m | 83 px | 2.6 |

Confidence at track death **0.58** vs 0.741 typical. Occlusion is NOT the cause:
0% of deaths have another player within 1.5 m; nearest neighbour at death is
3.37 m vs a 4.27 m baseline.

### Misattribution, independently confirmed by the coach's own log
- **30.0%** of attributed detections fall outside the player's own on-field window.
- Only **9.5%** of those are within 15 s of a sub; **72.3%** are >60 s away
  (median 126 s) — so these are not coach mis-taps, they are wrong-child tracks.
- Control that proves the test: the **keeper has 0 out-of-window detections** (he
  never subs off, so no bench window exists for a wrong track to land in).
- The log says 7.0 on average; tracking finds 3.4. Tracking finds MORE than the
  log allows in only **0.8%** of seconds → the off-window filter is
  **not** deleting legitimate players. It is a correct drop.

### Geometry (measured, and it is NOT the limiter)
Camera sits ~4 m behind the near touchline, so the near half of frame is empty
spectator space and **62.7% of detections are in the far band**. Depth resolution
collapses 7.2× (37.5 px/m near → **5.2 px/m** far; 1 px = 0.19 m there).

**But repeatability is HIGHEST in the far band (r = 0.927 vs 0.254 near).** The
depth noise is survivable. See retractions.

---

## Retracted claims (do NOT act on these)

1. **"0.755 is the ceiling."** Bad test — it compared *different* minutes, so it
   folded in genuine changes in how hard a kid was running. Real ceiling 0.98.
2. **"Geometry is the physical ceiling; per-player is impossible on this rig."**
   Refuted by the far-band result above. I derived a worst-case pixel-jitter bound
   (0.19 m → 2.7 m/s → "4,850 m of fiction") and mistook the bound for reality;
   measured per-step depth motion is 0.086 m.
3. **"A second camera is required."** It would triple depth resolution and fix
   nothing, because depth is not the limiter. Coach has ruled it out anyway.
4. **"Stop investing in per-player stats."** Rested on (1) and (2).
5. **"100% of stitching is wrong."** Denominator error (co-present instants, not
   tracklets). Real figure was 17.6% of multi-track tracklets.
6. **"The stitch overlap bug doesn't exist in current code."** Also wrong — see
   the open thread below.
7. **ICC = 0.128 across 7 games.** Invalid: ~45 commits touched
   `stats.py`/`identity_assign.py`/`pipeline.py` between those games, so it
   measured algorithm churn, not player variation.
8. **"The green kit has no legible number."** Wrong — judged from one
   front-facing crop. The VLM read numbers off green shirts' backs at 0.90–0.98.

---

## Open thread: stitch conflicts I could not reproduce

The analytics doc written by the 2026-08-05 run (fresh stitch, 1,955 our-fragments
→ 596 our-tracklets) contains **129 conflicting pairs across 95 tracklets** — one
identity in two places, >1.5 m apart. Example: tl1528 holds track 1747
(569.8–576.3 s) and 1752 (573.0–2360.2 s).

Six attempts to reproduce this outside the pipeline all gave **zero** conflicts
(2,243 / 2,797 / 6,383 tracklets depending on how `team_of_track` and gap-split
were reconstructed). A candidate fix — a component-wide overlap guard, since
`_overlaps_in_time` is applied only to the pair being joined and overlap is not
transitive — was a **byte-for-byte no-op** on every input reproducible from
outside, and was reverted.

**Do not chase this from outside the pipeline.** Instrument *inside* the run:
log every `union()` in `post_game/reid_stitch.py` with both fragments' sample
times and the conflict-check verdict, run the real pipeline, and read the trace.
Relevant: `pipeline.py:490-499` gap-splits at `SPLIT_GAP_S=1.0` before stitching,
which removes the interior holes the overlap test depends on.

---

## Shipped this session (on `dev` + `beta`, pushed; `main` untouched)

| commit | what |
|---|---|
| `1f9bb35` | hide work rate below 25% coverage — kills a bogus 83 m/min keeper reading (his avg speed was 4.6 km/h). 9 of 12 W8 players keep a number, 46–88 m/min |
| `a4ce0c1` | calibrate reports real quality, not the never-written `rms_weighted_m`. Surfaced that the B1 tilt solve works: RMS **1.691 → 0.647 m**, worst point `corner_FL` 1.069 m |
| `f1d14db` | keeper tracklets emit `status="gk"` instead of `"auto"` (all 13 of W8's keeper tracks were laundered as `auto`) + 4 tests |

**Uncommitted:** `tracking/vlm_number_probe.py` adds `VLM_RELAXED_TLS=1`. The corp
proxy (`Rocket LLC / focsecurefw-ssl-decrypt`) mints a leaf for api.anthropic.com
with **no Authority Key Identifier**, which Python 3.13's `VERIFY_X509_STRICT`
rejects. The new path clears only that flag — full chain + hostname verification
retained — unlike the pre-existing `VLM_INSECURE_TLS=1` (`verify=False`).
`corp_ca.pem` is generated locally and must NOT be committed.

## VLM jersey identity: works, too small to matter
16 drafts, 10 players, confidence 0.60–0.98, reading numbers off green shirts'
backs. Cross-checked against coach labels: **6 agree, 7 conflict, 3 new.** Four
conflicts are tracklets the coach marked `__opp__`/`__other__` where the VLM sees
a green kit with a legible number — tl191 verified by eye from the video, both
players ARE in green, so the coach label is wrong there. But drafts cover only
**~10% of on-field time**, and applying them moved r 0.004 → −0.027.
**Verdict: a label spell-checker, not a metrics fix. Keep `VLM_IDENTITY` off.**

## Lever 2, part 1: tightening the on-field gate — MEASURED, DOES NOT WORK

The assigner was never a pure "assign freely, then filter". It already consults
the log during assignment, but with `ONFIELD_TOLERANCE_S = 240.0` — a **four
minute** tolerance (`identity_assign.py` per-window candidate gate + per-tracklet
vote filter). At U10 sub intervals that leaves nearly every squad member eligible
in nearly every window, so the log barely constrains the decision where it is
made; it then bites at exact boundaries afterwards in `stats._drop_offwindow`.

Made the tolerance configurable (`config.ID_ONFIELD_TOLERANCE_S`, default 240.0 =
verified byte-for-byte no-op) and swept it on 3 games:

| tolerance | pooled r (n) | 95% CI | W8 off-window drop rate |
|---|---|---|---|
| 240 s (today) | **+0.329** (22) | [−0.06, +0.65] | 34.4% |
| 120 s | +0.069 (25) | [−0.34, +0.48] | — |
| 60 s | −0.040 (28) | [−0.41, +0.36] | 13.1% |
| 30 s | −0.114 (28) | [−0.46, +0.32] | — |
| 10 s | +0.102 (29) | [−0.30, +0.51] | 11.0% |

**The mechanism engaged and the outcome did not improve.** Off-window violations
fell 34.4% → 11.0%, exactly as intended — the log now binds during assignment —
yet r did not rise at any setting, and every tightened value sits at or below
baseline. More players get named (n rises 22 → 29) while r stays flat: the same
completeness-without-correctness trap as the VLM run.

**Interpretation:** the wrong-child attributions are *not* mostly bench-window
leakage that a tighter temporal gate can catch. Making the log bind changes WHICH
wrong child gets picked, not WHETHER one does — consistent with the doc's own
finding that 72.3% of bad detections are >60 s from any sub. The binding
constraint is that within a legal on-field set of 7 same-kit children, nothing in
the current signal distinguishes them.

Kept as a no-op default knob (the sweep is reproducible); **not shipped as a
behaviour change**.

## The tracklet is not the object we think it is (2026-08-05, measured)

Probing the sub-boundary idea (`tracking/sub_straddle_probe.py`) turned up
something bigger than the thing being probed.

**Tracklets are mostly welded across halftime.** On 3 games, **71–92% of our
tracked time** sits in tracklets whose span crosses the halftime break. The
median substantial (≥60 s) tracklet spans ~31 minutes of wallclock while holding
only ~100 s of detections — a **4–7% duty cycle**.

The cause is NOT the stitcher. `STITCH_MAX_GAP_S = 10.0` refuses links beyond
10 s, but single **BoT-SORT `track_id`s** already span the break by themselves —
e.g. W8 track 1327 runs 429 s → 2283 s with exactly one 1759 s gap and nothing
else; track 222 (80→1759 s, one 1348 s gap); track 3487 (1156→3194 s, one 1791 s
gap). Five of W8's six largest tracklets are welded across halftime this way.

`GAP_SPLIT_ENABLED` — the existing stage-3.5 fix for precisely this — **defaults
to OFF** (`config.py`, `os.environ.get(...) == "1"`), and neither `eval_identity`
nor the scorer invoked it.

Consequence for the sub-log idea: 87–95% of tracked time is in tracklets that
straddle a SUB, and ~86–100% of substantial ones do. Assignment is
tracklet-global, so one player label per tracklet is wrong for part of nearly
every tracklet. But naive splitting at every boundary yields 96% sub-30 s crumbs
(median piece 0.0 s) — the pieces are too small to name.

### Turning gap-split on: looks like a win, is a regression

| comparison | baseline | gap-split 30 s |
|---|---|---|
| pooled **headline** (2 games) | +0.396 (n=16) | **+0.572** (n=12) |
| pooled on the **SAME 11 player-halves** | **+0.733** | **+0.370** |

Delta on common players **−0.363, 95% CI [−0.734, −0.026] — excludes zero, a
resolvable REGRESSION**, and it also fails to name 5 players the baseline named.
The headline "improvement" was pure composition: it dropped exactly the
hard-to-name children (Qian, Yaacoub, Sharma, Zaidan) and the correlation over
the easy remainder looked better.

**This is the single most important methodological lesson so far: never compare
headline r between runs.** Two runs name different players. Use
`tracking/half_split_compare.py`, which intersects the rosters and bootstraps the
difference. Composition effects here were LARGER than the effect being measured
and pointed the opposite way.

(Only 2 games could be tested — `mpyo67cl4uflh` and others had their multi-GB
`jersey_samples.npz` pruned, and re-splitting needs it for team classification.)

## Ranked levers
1. **Name the tracks we already have** — 6.3 available vs 3.55 named. The whole game.
2. ~~**Use the SUB log as a hard 7-slot constraint per second**~~ — the temporal
   half of this is now MEASURED DEAD (above). What remains untested is the
   *structural* half: splitting tracklets at sub boundaries so one tracklet can't
   span two children, and per-second slot feasibility. Note the caveat that still
   applies — forcing 7 names hits 7/7 by construction, so only **r** decides.
3. **Size-aware detection threshold** so 48 px far players aren't dropped at
   conf 0.58. Attacks fragment creation rather than repairing it.
4. Stitch-conflict trace (open thread above).
5. Jersey VLM — real but ~10% coverage; review assist only.

**Measured dead:** second camera, taller mast (1.4×), corner mount (worse), higher
frame rate, appearance/Re-ID on same-kit kids, cleat/sock markers, referee-colour
input, coach-log outfield anchors (~0.3 precision), bulk hand-labelling (57 labels
→ 22.3% coverage at 19.3% purity, curve flat after ~20).

## Kit note

> **⚠ 2026-08-05 correction: most of the corpus is ALREADY black-kit.** Checked
> `_our_color` on all six cached games: **4 of 6 are all-black** (`#0a0a0a`) —
> mqcf9axlvtuyt, mqcjsjugchb2i, mpyo67cl4uflh, and W7 mrhvbvwi1gjpn (vs a GREEN
> opponent, `#28bb40`, i.e. the inverse of W8). Only W8 and mq01kuce2i81r are
> green. So "everything measured is from a green game" is wrong.
>
> Reassuringly, **repeatability does not separate by kit** — the black games sit
> at both ends (−0.141 and +0.408) — so kit colour is not the confound; that
> scatter is the underpowered-metric noise documented above.
>
> Worth knowing about the coach's colour picker: the hex is only ever an
> **anchor**, never the classifier. With no ref colour logged (true for all six
> games) `classify_tracks` runs KMeans over *measured* jersey HSV and the coarse
> hex only decides **which cluster is "us"**. The measured pixels do the
> separating, so a coarse pick is fine — but it also cannot rescue a game whose
> jersey samples were pruned.

Two kit sets: **all green** and **all black**. The original numbers below are
from a GREEN game. On a black-kit game the colour separation that team classification and the
VLM both rely on will degrade badly — players, refs and coaches all dark. Height
is the backup signal there: on-pitch p90 1.43 m vs an off-pitch adult tail at
1.69 m. Adults are near-sideline (median y=28.9 m of 30.3) vs players mid-pitch
(16–17 m), but do NOT filter on that — it costs real players on the touchline.
