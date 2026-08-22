# Voice notes — design

**Status:** Part 1 (push-to-talk recorder) and Part 2's Fix A + Fix B (source-keyed
draft ids, per-source merge in `write_voice_drafts`) shipped 2026-08-22. Fix C
(cross-source corroboration), the repetition guard, and Part 3's provenance UI
are still open — as is the unmeasured post-game yield question below.
**Date:** 2026-08-20
**Supersedes:** the auto-start full-game recorder (`POST_GAME_NARRATION_GUIDE.md` stays valid for post-game narration)

## The decision

Replace the in-game full-game voice recorder with a **push-to-talk note recorder**,
and make the extraction pipeline merge **two** voice sources — live notes and
post-game narration — instead of one.

## Why: the measurement

Ran `voice_union` on the real Jun 21 Win City game (`mqnxdtyven0g2`) against the
existing Opus extraction of its 50-minute live recording. Alignment was exact
(segment boundaries 1439s / 2886s match the live clock to the second).

    voice events: 41  |  live events: 139
    NEW drafts:   28
    DUP (already tapped, dropped): 13
    ENRICH: 0

The 28 looks good until you decompose it: **16 of 28 are KEY_PASS**, and they are
mostly one stock phrase repeated — "Nice serve by Malvere" seven times (a
mistranscription of Maverick), "Nice serve by Issa" four times. Strip KEY_PASS
and 12 events remain: KICK_OUT 3, TURNOVER 3, SHOT_OFF 2, BLOCK 1, BALL_WIN 1,
FOUL_BY 1, DUEL_LOSE 1. Only 6 of those carry a confident player; 3 have none.

Two conclusions:

1. **The pipeline is sound.** Alignment, ±30s dedup, confidence calibration and
   the deliberate refusal to guess between Ben Adam / Ben Hahn all work. 13
   correctly-dropped dups include 5 BALL_WINs — so the raw extraction's
   "BALL_WIN 6" was mostly re-stating live taps, not new data.
2. **The capture mode is wrong.** 50 minutes of audio yielded ~12 new events
   because the coach is coaching, not narrating. This confirms his own read: he
   barely gets a chance to talk during a game.

So: stop recording whole games live. Record short notes instead, and get volume
from post-game narration where there is time to speak.

## Consequence for what a note IS

Extraction quality on sparse live speech is not good enough to auto-generate
scoring events worth trusting — 16 near-identical KEY_PASS drafts would be a
chore to dismiss, and each is worth +4 ATK / +3 DEC if accepted.

**A note is primarily a note.** Its transcript is the deliverable: attached to a
moment, readable on the timeline, searchable at season review. Extraction still
runs, but drafts are a bonus rather than the point. A note like "Issa's been
marking their 9 all half" is not an event and must not be forced into one.

## Part 1 — Push-to-talk recorder (app)

Replaces the auto-start take with N short clips. Each clip is an independent
segment carrying its own `startedAt`, which is the same wall-clock anchor the
event taps use — so alignment gets *simpler*, not harder. No cumulative boundary
arithmetic, no "say kickoff" cue, no drift.

**Keep:** `_voiceMime`, `_voiceUpload`, the IndexedDB chunk cache, the R2 path,
and the coach-only `games/<id>/voice/segments` doc with its `arrayUnion` append —
that is already additive per segment and needs no change.

**Drop:** kickoff auto-start (`game.autoRecord` + `pendingMicRef` stream
stashing), the clock-driven `startSegment`/`stopSegment` imperative handle called
from the half-time / 2nd-half / full-time handlers, the screen wake-lock, the
iOS-hiccup auto-restart, and the tap-to-mute / long-press-to-stop gesture. All of
that exists to survive a 25-minute continuous take. A 15-second clip needs none
of it.

**Add:** a mic button in the live game view. Press and hold to record, release to
stop and upload. Retain the mic permission across notes so only the first press
prompts. Show a duration counter while held and an upload indicator after.

**Note doc.** Each note appends to the same coach-only segments list, plus the
fields extraction needs:

    { startedAt, url, durationS, mime, kind: 'note', period, elapsed }

`period` and `elapsed` come from the live clock at press time. This is what makes
a note self-anchoring: the pipeline never has to derive its position.

**Migration.** `game.autoRecord` stays readable so existing games keep working;
new games stop setting it. Do not delete the old segments — the Win City audio is
the only measurement instrument for this whole design.

## Part 2 — Two-source merge (pipeline)

Today this is **broken for two sources**, in two distinct ways.

**Bug A — colliding ids.** The draft id is `vd_{period}_{elapsed}_{type}`. A live
note and a post-game narration of the same moment produce the *same* id, so the
second silently replaces the first.

**Bug B — destructive write.** `write_voice_drafts` does
`set({"voiceDrafts": drafts}, merge=True)`, which replaces the whole array.
Narrating post-game therefore **deletes** the live notes' drafts.

Both must be fixed before a second source exists, or the second capture path
destroys the first's output.

**Fix A — source in the id:** `vd_{src}_{period}_{elapsed}_{type}` where `src` is
`live` or `post`. Preserves the re-run-replaces-itself property per source while
letting both coexist.

**Fix B — merge per source:** `write_voice_drafts(game_id, drafts, source)` keeps
drafts whose `source` differs and replaces only the named source's. Re-running
post-game extraction must never touch live notes.

**Fix C — cross-source corroboration.** The union currently matches voice only
against live *taps*. It must also match voice against voice, because the coach
will often mention the same memorable moment both live and while watching back.
That is not duplication to discard — it is two independent observations.

Same-type, same-period, within ±30s:

| Case | Handling |
|---|---|
| Both sources agree on the player | ONE draft, `corroborated: true`, confidence boosted, both quotes retained |
| One has no player, the other does | The one with a player wins (existing ENRICH logic) |
| They name DIFFERENT players | Surface BOTH, flagged `conflict: true` |

The conflict case is the valuable one: it is exactly how the Ben Adam / Ben Hahn
and Liam Gibala / Liam Garland ambiguities get resolved by the coach's eye.

**Precedence.** When the two sources conflict irreconcilably, **post-game wins.**
A live note is a thinner observation — the coach is coaching, with no pause
button. This must be explicit in the code, not an accident of which ran last.

**Repetition guard.** The existing ~20s collapse does not catch the observed
failure: seven "nice serve" KEY_PASSes spread minutes apart. Cap any single
(type, player) pair per source per half at a configurable N (start at 3) and
report what was dropped. Do not silently truncate — a hidden cap reads as
"captured everything".

## Part 3 — Confirm queue (app)

The queue already exists and handles accept/dismiss, player assignment and
`voice-confirmed` stamping. It needs to show provenance:

- **Source badge** per draft: live note vs post-game
- **Both quotes** when `corroborated`, with the boosted confidence
- **Conflict pairs adjacent**, so the coach picks between two named players in
  one interaction rather than meeting them ten cards apart
- **Note-only clips** — a clip that yielded no event still surfaces its
  transcript, and can be kept as a note rather than dismissed. This is the part
  that makes notes worth recording even when extraction finds nothing.

## Open question the measurement did NOT answer

Nobody has yet narrated a game post-game end to end and counted what lands in the
queue. `POST_GAME_NARRATION_GUIDE.md` flags this and it is still true: the 41-event
Win City extraction is from a **live** recording. Post-game yield is unmeasured.

Sequence the work so that stays cheap to learn: Part 2 (merge semantics) is
load-bearing and testable offline against the existing `.events.json` files, so
build it first. Part 1 and Part 3 are only worth their effort if post-game
narration proves out.
