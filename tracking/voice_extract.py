#!/usr/bin/env python3
"""Phase 3.6 payoff — LLM event EXTRACTION from cleaned game narration.

Pipeline so far: voice_probe (Whisper transcript) → voice_clean (drop the coach's
on-field instructions, keep play-by-play) → THIS: turn the cleaned narration into
structured DRAFT events {videoTimeS, type, player, confidence} for the confirm
queue. Never auto-commits — everything is a draft the coach confirms.

Two things the raw narration needs and this does:
  * DEDUP excited repetition — the coach yells "Goal! Goal! Goal!" across a dozen
    Whisper segments for ONE goal; collapse to a single event at its onset.
  * ROSTER match — the coach narrates by FIRST NAME; map to a player_id, tolerating
    Whisper phonetics ("Golland"→Garland). Ambiguous first names (Ben Adam/Hahn,
    Liam Gibala/Garland) resolve to null (coach disambiguates in the queue).

Timestamps are the narration's own (audio) seconds. Aligning to videoTimeS is a
downstream concern — trivial for in-PWA recordings (each carries videoTimeS), and
anchored by the spoken "kickoff" markers for phone-memo audio.

Runs on Haiku through the corp gateway (Opus is gateway-blocked); reuses the
SDK-or-raw-HTTPS + corp-TLS pattern. Read-only; no Firestore writes.
Run: set -a; source .env; set +a
     .venv-post-game/bin/python -m tracking.voice_extract \
         --annotated tracking/outputs/voice_clean/game2_amherstburg.annotated.json \
         --label game2_amherstburg --model claude-haiku-4-5
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

# Opus, matching voice_clean.py — extraction is the harder half of the pair. It has
# to decide whether "he's got it, turns, oh lovely ball" is one KEY_PASS or three
# events, collapse the coach's excited repeats, and infer WHICH child from a first
# name heard over crowd noise. Haiku was the default here (the outlier: voice_clean
# already ran Opus) and under-extracts exactly the PROCESS events the score needs
# most — duels, gates, turnovers — which are the quietly-narrated ones.
#
# Reachable via the `ant` OAuth bearer (see `_call`); a raw corp ANTHROPIC_API_KEY is
# entitlement-limited to Haiku and 429s instantly on Opus, so this default REQUIRES
# the OAuth path. Override with --model for a cheap dry run.
MODEL = "claude-opus-5"
OUT_DIR = Path(__file__).resolve().parent / "outputs" / "voice_clean"

# Coach event vocabulary. MUST be a subset of EVENT_TYPES in soccer_team_app.jsx:
# the confirm queue looks each draft's type up there and silently drops the
# Accept when it misses, leaving an un-actionable row in the queue forever.
# CORNER and OFFSIDE were extracted here but have never existed in the app, and
# SUB needs both the off and on player, which narration doesn't reliably give —
# so none of the three could ever be accepted. Removed rather than added to the
# app: a draft the coach cannot act on is worse than no draft.
#
# ⚠ WHY THE DISCRETIONARY TYPES WERE ADDED (2026-08-15). The original list held
# only the events the coach already taps reliably during a game, so voice added
# volume but fixed nothing. Measured over 12 games, his live taps split in two:
#
#   outcome events   GOAL/ASSIST/SHOT/SAVE   logged every game, stable
#   process events   DUEL_*/GATES/TURNOVER/  ZERO in 8 of 12 games; the DEF share
#                    HOLDS_BALL/KEY_PASS/    of action events fell 60% -> 3%
#                    BLOCK/CLEAR
#
# Those process events are what the DEF and DEC pillars run on, and their absence
# is why the season score had to be coverage-weighted (see pwa_score
# .PILLAR_EVENT_TYPES). Voice POST-game is the only realistic way to recover them:
# the coach is watching, not coaching, so he can narrate what he could not tap.
# CLEAR and KICK_OUT are included as the defensive pair narration does produce
# ("cleared it", "hoofed it away"). GIVE_GO is NOT: it needs a partner player,
# which narration rarely states.
EVENT_TYPES = [
    # outcome (already tapped well — kept so voice can enrich/confirm)
    "GOAL", "ASSIST", "SHOT_ON", "SHOT_OFF", "SAVE", "PEN_AWARDED",
    "FOUL_BY", "FOUL_ON", "OPP_GOAL",
    # discretionary / process — the ones live tapping loses
    "BALL_WIN", "DUEL_WIN", "DUEL_LOSE", "BLOCK", "CLEAR", "KICK_OUT",
    "KEY_PASS", "GATES", "TURNOVER", "HOLDS_BALL",
]

_SCHEMA = {
    "type": "object",
    "properties": {
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "t": {"type": "number", "description": "onset time (s) of the FIRST segment for this event"},
                    "type": {"type": "string", "enum": EVENT_TYPES},
                    "player_first_name": {"type": "string", "description": "first name as narrated, or '' if none/opponent"},
                    "confidence": {"type": "number", "description": "0..1"},
                    "quote": {"type": "string", "description": "the narration line(s), <=120 chars"},
                },
                "required": ["t", "type", "player_first_name", "confidence", "quote"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["events"],
    "additionalProperties": False,
}


_ANT_BEARER_CACHE: dict[str, object] = {}


def _ant_bearer() -> str | None:
    """OAuth bearer minted from the `ant` CLI when no ANTHROPIC_OAUTH_TOKEN is set.

    Lets narration extraction run hands-free with no manual `export` and no API key
    in .env, as long as the user has run `ant auth login` once. Minted FRESH at call
    time so a long run doesn't hit an expired token. Cached per process; returns None
    if `ant` is absent or not logged in. Lifted verbatim from vlm_number_probe, which
    already solved this — see the auth-channel note in `_call`.
    """
    if "tok" in _ANT_BEARER_CACHE:
        return _ANT_BEARER_CACHE["tok"]  # type: ignore[return-value]
    import shutil
    import subprocess
    tok = None
    if shutil.which("ant"):
        try:
            r = subprocess.run(["ant", "auth", "print-credentials", "--access-token"],
                               capture_output=True, text=True, timeout=20)
            t = (r.stdout or "").strip()
            if r.returncode == 0 and t.startswith("sk-ant-"):
                tok = t
        except Exception:
            tok = None
    _ANT_BEARER_CACHE["tok"] = tok
    return tok


def _relaxed_session(ca_bundle: str):
    """A requests Session verifying against `ca_bundle` with X509_STRICT cleared.

    Everything else about verification stays ON: the signature chain is still
    validated to the corp root and the hostname is still checked. The single
    relaxation is RFC-5280 strictness about a missing Authority Key Identifier on
    the corp proxy's minted leaf — the one thing that makes an otherwise-good chain
    fail under Python 3.13 / OpenSSL 3 with CERTIFICATE_VERIFY_FAILED. Deliberately
    much narrower than the VLM_INSECURE_TLS=1 (verify=False) escape hatch, which
    disables verification wholesale. Same helper as vlm_number_probe.
    """
    import ssl

    import requests
    from requests.adapters import HTTPAdapter

    ctx = ssl.create_default_context(cafile=ca_bundle)
    ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT      # the ONLY thing relaxed
    ctx.check_hostname = True                         # hostname still enforced
    ctx.verify_mode = ssl.CERT_REQUIRED               # cert still required

    class _Adapter(HTTPAdapter):
        def init_poolmanager(self, *a, **kw):
            kw["ssl_context"] = ctx
            return super().init_poolmanager(*a, **kw)

    s = requests.Session()
    s.mount("https://", _Adapter())
    return s


def _call(payload: dict) -> str:
    """SDK when importable, else raw HTTPS (corp route). 429/5xx backoff + corp TLS."""
    try:
        import anthropic
        resp = anthropic.Anthropic().messages.create(**payload)
        return next((b.text for b in resp.content if b.type == "text"), "{}")
    except ImportError:
        pass
    import requests
    # Two auth channels, same as vlm_number_probe: an OAuth BEARER (minted by
    # `ant auth print-credentials --access-token`) OR a raw x-api-key. On the Rocket
    # corp account the raw key is entitlement-limited to Haiku — an instant 429 on
    # Opus/Sonnet — while the OAuth token (same SSO that grants Claude Code its Opus)
    # reaches the bigger models. Prefer the bearer; it needs the oauth beta header.
    # This is why extraction no longer needs ANTHROPIC_API_KEY in .env at all.
    oauth = os.environ.get("ANTHROPIC_OAUTH_TOKEN") or _ant_bearer()
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not oauth and not key:
        raise SystemExit(
            "No Anthropic auth: run `ant auth login` (preferred — reaches Opus), "
            "or set ANTHROPIC_OAUTH_TOKEN / ANTHROPIC_API_KEY.")
    if oauth:
        headers = {"authorization": f"Bearer {oauth}",
                   "anthropic-beta": "oauth-2025-04-20",
                   "anthropic-version": "2023-06-01",
                   "content-type": "application/json"}
    else:
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01",
                   "content-type": "application/json"}
    base = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/")
    ca = (os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("AWS_CA_BUNDLE")
          or os.environ.get("SSL_CERT_FILE"))
    verify = ca or True
    poster, kw = requests.post, {"verify": verify}
    if os.environ.get("VLM_INSECURE_TLS") == "1":
        kw = {"verify": False}
        import urllib3
        urllib3.disable_warnings()
    elif ca:
        # Corp VPN: keep the chain + hostname checks, drop only X509_STRICT.
        poster, kw = _relaxed_session(ca).post, {}
    for attempt in range(5):
        r = poster(f"{base}/v1/messages", headers=headers,
                   json=payload, timeout=120, **kw)
        if r.status_code in (429, 500, 502, 503, 529) and attempt < 4:
            time.sleep(min(float(r.headers.get("retry-after", 2 ** attempt)), 30))
            continue
        r.raise_for_status()
        blocks = r.json().get("content", [])
        return next((b["text"] for b in blocks if b.get("type") == "text"), "{}")
    raise SystemExit("API failed after retries.")


def _extract(lines: list[str], roster_desc: str, model: str) -> list[dict]:
    system = (
        "You extract soccer match events from a coach's timestamped play-by-play "
        "narration of his OWN youth team. Each line is '[t] text'.\n"
        "Rules:\n"
        "- Emit ONE event per real occurrence. The coach repeats himself when "
        "excited ('Goal! Goal! Goal!' across many lines = ONE goal); collapse "
        "repeats within ~20s of the same type into a single event at the FIRST "
        "line's time.\n"
        "- Extract EVERY distinct real match event in the lines you are given (see "
        "the type enum). Do not stop early. Ignore pep-talk, warmups, and vague "
        "commentary ('trying to move up').\n"
        "- Whisper mangles words. EVENT phonetics: 'Padalti'/'Penalty shot'→"
        "PEN_AWARDED (NOT a player name). Do not turn a mangled event word into a "
        "player. 'corner'/'offside' are NOT in the enum — skip them entirely.\n"
        "- PROCESS events matter as much as goals. Map the coach's ordinary "
        "phrasing:\n"
        "    won it / won the ball / nicked it / stole it / intercepted   -> BALL_WIN\n"
        "    won the 1v1 / beat him / held him off / shrugged him off     -> DUEL_WIN\n"
        "    lost it / got beaten / dispossessed / muscled off the ball   -> DUEL_LOSE\n"
        "    blocked / got a foot in / charged it down                    -> BLOCK\n"
        "    cleared it / headed it clear / got it out                    -> CLEAR\n"
        "    hoofed it / booted it away / just kicked it out              -> KICK_OUT\n"
        "    great ball / lovely pass / played him in / through ball      -> KEY_PASS\n"
        "    split them / through the gap / between the two              -> GATES\n"
        "    gave it away / turned it over / bad pass / straight to them  -> TURNOVER\n"
        "    held it too long / should have released / dwelt on it        -> HOLDS_BALL\n"
        "  These are judgements the coach states out loud while watching back; take "
        "them at face value. If he says a name with one of these, attach it.\n"
        "- Do NOT invent process events from neutral commentary. 'He's got the "
        "ball', 'we're pushing up', 'good shape' are NOT events. Only emit when the "
        "coach describes a completed action or makes an explicit judgement.\n"
        "- player_first_name: the first name the coach used for the player who did "
        "it; '' if none stated or it's the opponent (use OPP_GOAL for opponent "
        "goals). Normalize obvious name phonetics to a plausible roster first name "
        "('Golland'→Liam Garland). The keeper who makes SAVEs is usually the same "
        "player all game.\n"
        "- confidence: how sure you are the event happened and the player is right.\n"
        f"Roster (first name / number): {roster_desc}"
    )
    # Chunk the narration so the model covers the WHOLE game (a single 271-line
    # shot anchors on the dense opening and stops). Overlap a couple lines so an
    # event spanning a chunk edge isn't lost; cross-chunk dedup cleans the overlap.
    CHUNK, OVERLAP = 70, 3
    out: list[dict] = []
    dropped: list[tuple[int, int]] = []
    i = 0
    while i < len(lines):
        window = lines[i:i + CHUNK]
        payload = {
            "model": model,
            "max_tokens": 4000,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": "Extract events:\n" + "\n".join(window)}],
            "output_config": {"format": {"type": "json_schema", "schema": _SCHEMA}},
        }
        try:
            out.extend(json.loads(_call(payload)).get("events", []))
        except (json.JSONDecodeError, TypeError) as e:
            # One bad chunk is ~CHUNK lines of narration — a quarter of a short
            # game. Swallowing it silently reported a plausible-looking total
            # with a hole in it, so say which window was lost.
            dropped.append((i, min(i + CHUNK, len(lines))))
            print(f"  !! chunk lines {i}-{min(i + CHUNK, len(lines))} dropped "
                  f"({type(e).__name__}: {e})", file=sys.stderr)
        i += CHUNK - OVERLAP
    if dropped:
        lost = sum(b - a for a, b in dropped)
        print(f"  !! WARNING: {len(dropped)} chunk(s) failed to parse — "
              f"~{lost} of {len(lines)} narration lines were NOT scanned for events",
              file=sys.stderr)
    return out


def _match_player(name: str, first_to_ids: dict[str, list[str]]) -> tuple[str | None, bool]:
    """(player_id | None, ambiguous). Exact-ish first-name match; None if no/dup."""
    if not name:
        return None, False
    key = re.sub(r"[^a-z]", "", name.lower())
    # exact first-name key
    for fn, ids in first_to_ids.items():
        if re.sub(r"[^a-z]", "", fn.lower()) == key:
            return (ids[0], False) if len(ids) == 1 else (None, True)
    # phonetic-ish: startswith / contains (Golland↔Garland share 'g..l..l')
    cands = [fn for fn in first_to_ids if key[:3] and (fn.lower().startswith(key[:3]) or key.startswith(fn.lower()[:3]))]
    uniq = {i for fn in cands for i in first_to_ids[fn]}
    if len(uniq) == 1:
        return next(iter(uniq)), False
    return None, len(uniq) > 1


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--annotated", required=True, help="voice_clean .annotated.json")
    ap.add_argument("--label", required=True)
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--game-id", help="optional: validate against live-logged events")
    args = ap.parse_args()
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from post_game import firestore_io

    roster = firestore_io.get_roster()
    first_to_ids: dict[str, list[str]] = {}
    name_of = {}
    for p in roster:
        fn = p.name.split()[0]
        first_to_ids.setdefault(fn, []).append(p.id)
        name_of[p.id] = f"#{getattr(p, 'jersey_number', '?')} {p.name}"
    roster_desc = ", ".join(f"{p.name.split()[0]}(#{getattr(p,'jersey_number','?')})" for p in roster)

    data = json.loads(Path(args.annotated).read_text())
    segs = data.get("segments", data if isinstance(data, list) else [])
    # keep only narration segments with text
    narr = [s for s in segs if (s.get("label") == "narration") and (s.get("text") or "").strip()]
    lines = [f"[{float(s.get('t', s.get('start', 0))):.0f}] {s['text'].strip()}" for s in narr]
    print(f"{args.label}: {len(narr)} narration segments -> extracting with {args.model}")

    raw = _extract(lines, roster_desc, args.model)
    events = []
    for e in raw:
        pid, ambig = _match_player(e.get("player_first_name", ""), first_to_ids)
        events.append({
            "videoTimeS": round(float(e.get("t", 0)), 1),
            "type": e.get("type"),
            "player_id": pid,
            "player_first_name": e.get("player_first_name", ""),
            "ambiguous_name": ambig,
            "confidence": round(float(e.get("confidence", 0)), 2),
            "quote": (e.get("quote") or "")[:120],
            "source": "voice_draft",
        })
    events.sort(key=lambda x: x["videoTimeS"])

    # Cross-chunk / repetition dedup: same type within 20s = one occurrence
    # (keep the higher-confidence, and its player if the winner lacked one).
    deduped: list[dict] = []
    for e in events:
        prev = next((d for d in reversed(deduped)
                     if d["type"] == e["type"] and e["videoTimeS"] - d["videoTimeS"] <= 20), None)
        if prev is None:
            deduped.append(e)
        else:
            if not prev["player_id"] and e["player_id"]:
                prev["player_id"], prev["player_first_name"] = e["player_id"], e["player_first_name"]
            prev["confidence"] = max(prev["confidence"], e["confidence"])
    events = deduped

    from collections import Counter
    print(f"\nextracted {len(events)} draft events:", dict(Counter(e["type"] for e in events)))
    for e in events:
        who = name_of.get(e["player_id"], e["player_first_name"] or "—")
        flag = " [AMBIG]" if e["ambiguous_name"] else (" [no-match]" if e["player_first_name"] and not e["player_id"] else "")
        print(f"  {e['videoTimeS']:7.0f}s  {e['type']:<12} {who:<20}{flag}  c={e['confidence']}  “{e['quote'][:50]}”")

    if args.game_id:
        g = firestore_io.get_game(args.game_id)
        live = Counter((ev.type or "").upper() for ev in (g.events or [])
                       if (ev.type or "").upper() in EVENT_TYPES)
        got = Counter(e["type"] for e in events)
        print(f"\n=== vs live log ({args.game_id}) — event-type counts ===")
        for t in sorted(set(live) | set(got)):
            print(f"  {t:<12} live={live.get(t,0):<3} voice={got.get(t,0)}")

    out = OUT_DIR / f"{args.label}.events.json"
    out.write_text(json.dumps({"label": args.label, "model": args.model,
                               "n_narration": len(narr), "events": events}, indent=2))
    print(f"\nwritten: {out}")


if __name__ == "__main__":
    main()
