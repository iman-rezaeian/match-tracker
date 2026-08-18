# Jersey-number identity (VLM): settled decision

**Status: SHIPPED, default ON, coverage-limited. NOT a dead-end.**
Last verified 2026-08-05; default flipped to ON 2026-08-06 (`5aae148`), doc
corrected 2026-08-09. Read this before re-opening the question.

This document exists because the jersey-number question has been re-litigated
several times. The measurements below are done. Do not re-run them; extend them.

---

## The one-line answer

Reading jersey numbers with a VLM is **the only identity signal we have measured
that beats the coach-log ceiling.** It is built, tested, wired into the pipeline,
and has a coach-facing Accept chip in the PWA. Its limit is **coverage, not
correctness**, and that limit comes from **crop legibility, not from the model** —
so it works as a *per-tracklet draft assist*, never as full automation.

If someone (including me) says "jersey OCR is a dead-end," they are wrong, and
they are probably confusing it with one of the genuinely dead levers in
[`ACCURACY_AUDIT.md`](ACCURACY_AUDIT.md) / the tracking-accuracy memory
(field-space tracking, tight gate, frame rate, cleats, referee colour, sub-log
constraint, second camera — those *are* measured-dead).

## What was measured

Two GT games, blind ground truth, apples-to-apples on GT label strips:

| model | G1 `mqcf9axlvtuyt` | G2 `mqcjsjugchb2i` |
|---|---|---|
| Haiku 4.5 (2026-07) | 26% cov / 0.62 prec | 16% cov / 0.38 prec |
| **Opus 4.8 (2026-08-04)** | **28% cov / 0.79 prec** | 4% cov / 1.00 prec (2/2) |

Reference bar: coach-log outfield anchors cap at **~0.3** precision and
individuate only the GK ([`phase-a`](#) memory). Opus at **0.79** is a different
regime — that is why this shipped and coach-log identity did not.

Crop quality drives coverage, and it scales:

| crops fed to the VLM | coverage | precision |
|---|---|---|
| GT label strips (4-panel) | 10% | 0.40 |
| 3× number-optimized 8K crops | 20% | 0.50 |
| 6× number-optimized 8K crops | **35%** | 0.57 |

Two findings that shaped the build:
1. **Confidence gate.** Every wrong G1 read had conf ≤ 0.45; correct reads
   clustered 0.55–0.85. Gating at ~0.5 lifts precision 0.79 → ~0.9+.
2. **Multi-frame voting.** The same player reads consistently across its
   tracklets (Luca #20 ×2, Jason #17 ×3, Issa #11 ×2). Voting per tracklet lifts
   both coverage and precision.

## Why coverage stays ~28% (the real, permanent limit)

The number is **on the back of the shirt only**. For any given tracklet, the
player is frequently facing the wrong way, too far away, or occluded. This is
geometry, not a model deficiency — Opus reads G2's strips no better than Haiku
because those strips are *illegible to anything*. More capable models will not
fix it; better crops will, up to a ceiling well under 100%.

**Consequence:** jersey numbers can never be the sole identity source. They are a
high-precision, low-recall assist that names a *subset* of tracklets. That is
genuinely useful — a correct name on 28% of tracklets, at 0.9 precision,
propagated by stitching, beats the coach labelling everything by hand — but it
does not close the coverage gap on its own.

## What is built (all on `dev` + `beta`, none on `main`)

| commit | what |
|---|---|
| `f4d57c9` | `tracking/vlm_number_probe.py` — the validation probe |
| `b0d4bb1` | `tracking/vlm_identity.py` — drafts: render crops → vote number → dup-number guard → `write_identity_drafts` |
| `e8ef666` | PWA Accept chip in `IdentityFixView` (`soccer_team_app.jsx:9239`) |
| `d874985` | **pipeline wiring** — fixes the tracklet-ID mismatch (see below) |
| `3fa8ac6` | prune the FIX-IDS review list using the VLM's team read (free — reads already happen) |

Flag: `VLM_IDENTITY` (`post_game/config.py:422`), **default ON** since `5aae148`
(2026-08-06), which also fixed the CLI flag silently overriding config on every
run — it is now tri-state (`--vlm-identity` / `--no-vlm-identity`). Model default
`claude-opus-4-8`, min-conf `0.5`, max-tracklets `120`.
Tests: `post_game/test_vlm_identity.py` (14, VLM mocked) — green as of 2026-08-05.

Re-measured on a kit-vote-cleaned pool at the time of the flip: coach-log alone
names ~4.6% of tracked time, +VLM = **35%** (30 drafts, 26 at conf ≥ 0.8, ~9 min
on a ~2 h run), and it pruned 18 opponents the colour classifier missed.

Validated end-to-end on W8 `mri01pvelv46d`: 9 drafts naming 6 players, conf
0.55–0.97, with legible reasoning ("'15' visible on green shirt back").
`events` (104) and `identityOverrides` confirmed **untouched** by the live write.

## Two traps that already cost a day each

**1. Tracklet IDs are run-specific.** The standalone CLI reconstructs tracklets
itself, and those IDs had **zero overlap** with the published analytics doc's IDs
(they are union-find stitch roots). Chips silently never appeared. Drafts must be
generated **inside** the pipeline run (`pipeline.run(vlm_identity=True)`, keyed by
that run's `tracklet_of_track`). The standalone CLI is dry-run/coverage only and
now prints a warning. **Full-run only** — `--stats-only` never opens the video, so
there are no crops.

**2. Opus needs the OAuth-bearer path.** The corp raw `sk-ant-` API key is
**Haiku-only** — Opus and Sonnet return an instant headerless 429 (a tier block,
not a rate limit). "I have Opus in Claude Code" ≠ "the raw API key can call
Opus": different channels. Use the SSO channel:

```bash
ant auth login   # once, interactive SSO browser flow
export ANTHROPIC_OAUTH_TOKEN="$(ant auth print-credentials --access-token)"
unset ANTHROPIC_API_KEY
```

Token expires ~hourly; re-mint per run. `3c31858` auto-mints it in the run panel.

## To produce coach-actionable drafts

```bash
python -m post_game.cli run --game-id <g> --vlm-identity --skip-upload
```

Reuses cached tracks, opens the video for crops, regenerates the analytics doc so
IDs match, writes `identityDrafts`, skips reel/upload.

## Open items (small, none blocking)

1. **Promote `beta` → `main`** — beta is 10 commits ahead and carries all of this.
2. **Tune `--min-conf`** once the coach has seen real drafts in anger.
3. **Batches API** (50% cost saving) not built.
4. **True coverage on strong crops is still unmeasured** — it needs a game with
   *both* fresh video *and* blind GT, which does not exist (both GT games' videos
   were deleted; W8 has video but no GT). Either GT-label a game whose video is
   still on disk, or capture a new game with GT. This is the one number that
   would tell us whether coverage is 28% or ~35–40% in production.

## Known cost/noise

The tallest tracks are near-camera **sideline adults** (coaches/refs, black
polos). Opus correctly reads them as `number=0, "adult/coach"` → no draft, but
they burn API calls. The on-field-fraction and top-N caps mitigate without fully
excluding them, since they stand right at the touchline.
