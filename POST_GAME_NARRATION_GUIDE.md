# Post-game narration — what to say so it becomes stats

Written 2026-08-15. Read `METRICS_INVENTORY.md` for what each metric is and how
much to trust it; this is only about **what to say into the mic** while you watch
a game back, and why those particular words.

---

## Why this exists

You tap while you coach, so what gets logged varies enormously. Measured across
your 12 finished games:

| | first 2 games | remaining 10 |
|---|---|---|
| process events logged | 102 | **41** |
| per game | 51.0 | **4.1** |

`DUEL_WIN` is **absent from 8 of 12 games**. The defensive share of all action
events fell from **60% to 3%** over the season. That is not you tapping badly —
it is you coaching, which is the job.

The season score has been coverage-weighted so those quiet games no longer
*punish* anyone (see `pwa_score.PILLAR_EVENT_TYPES`). But weighting can only stop
the dilution; it cannot invent the missing observations. Narrating post-game is
the only realistic way to actually recover them, because you are watching rather
than coaching.

## The one thing that changed vs your earlier attempts

Your three existing narrations were recorded **live**. Scanning them for process
language:

    game1_belleriver   BALL_WIN 11, DUEL_LOSE 1, KICK_OUT 1
    game_wincity       BALL_WIN 6,  DUEL_LOSE 1, BLOCK 1, CLEAR 1, KICK_OUT 1
    game2_amherstburg  KEY_PASS 1  — nothing else

So live narration produced almost only "won the ball". That is what you can say
in real time. Watching back, you can say the rest — and the rest is what the DEF
and DEC pillars need.

---

## What to say

Use ordinary football language. The extractor is prompted on these exact phrasings,
so you do not need a script — just say the thing you would say to another coach.
**Always say the player's first name.**

| Say something like | Becomes |
|---|---|
| "Khalid won it", "nicked it off him", "intercepted" | `BALL_WIN` |
| "Ben won the 1v1", "beat him", "held him off" | `DUEL_WIN` |
| "Arian lost it there", "got beaten", "muscled off the ball" | `DUEL_LOSE` |
| "Jaedyn blocked it", "got a foot in", "charged it down" | `BLOCK` |
| "cleared it", "headed it clear" | `CLEAR` |
| "just hoofed it", "booted it away" | `KICK_OUT` |
| "great ball from Luca", "played him in", "through ball" | `KEY_PASS` |
| "split them", "through the gap", "between the two" | `GATES` |
| "gave it away", "turned it over", "straight to them" | `TURNOVER` |
| "held it too long", "should have released it" | `HOLDS_BALL` |

Goals, assists, shots and saves are also extracted, but you already tap those
reliably — voice mostly **confirms** them, and the union step dedups anything
within ±30 s of a live event rather than double-counting it.

### What NOT to say

Neutral commentary is deliberately ignored, so don't worry about it:
*"he's got the ball"*, *"we're pushing up"*, *"good shape"*. The extractor is told
to emit an event only for a completed action or an explicit judgement. Pep talk
and warmup chatter are stripped by `voice_clean` before extraction.

---

## Practicalities

**Pause freely.** You are watching a recording; there is no clock pressure. That
is the entire advantage over live narration.

**Say a name every time.** A draft with no player still reaches the confirm queue,
but you then have to pick the player by hand — which is slower than saying it.
Ambiguous first names (Ben Adam / Ben Hahn, Liam Gibala / Liam Garland) resolve to
*null* on purpose rather than guessing; say a surname or number for those two
pairs.

**Repetition is fine.** Excited repeats within ~20 s of the same type collapse to
one event at the first mention.

**Nothing auto-commits.** Everything arrives as a draft in FILM ROOM → CONFIRM
QUEUE for you to accept or dismiss.

### Rough effort

At ~10 process events per half you would roughly double the season's entire
process-event corpus from a single game. You do not need to narrate every game —
a handful of well-narrated games lifts the coverage weight on those games, which
is what makes the pillars comparable.

---

## Running it

```bash
set -a; source .env; set +a
.venv-post-game/bin/python -m tracking.voice_probe   --audio <file> --label <label>
.venv-post-game/bin/python -m tracking.voice_clean   --label <label>
.venv-post-game/bin/python -m tracking.voice_extract --annotated tracking/outputs/voice_clean/<label>.annotated.json --label <label>
.venv-post-game/bin/python -m tracking.voice_union   --events tracking/outputs/voice_clean/<label>.events.json --game-id <gameId>
```

⚠ **Alignment.** The in-PWA recorder writes one segment per half, so timestamps
map to the game clock at the segment boundaries. A single phone-memo recording of
a whole game has no such anchor — `voice_union` does not handle that case. If you
record on your phone, **say "kickoff" at each half's kickoff** so there is a marker
to align to.

⚠ Needs a working `ANTHROPIC_API_KEY` / gateway route; extraction runs on Haiku
because Opus is gateway-blocked on the corp network.
