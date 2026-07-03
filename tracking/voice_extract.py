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
import time
from pathlib import Path

MODEL = "claude-haiku-4-5"
OUT_DIR = Path(__file__).resolve().parent / "outputs" / "voice_clean"

# Coach event vocabulary (matches the live-log types the confirm queue expects).
EVENT_TYPES = [
    "GOAL", "ASSIST", "SHOT_ON", "SHOT_OFF", "SAVE", "CORNER", "PEN_AWARDED",
    "FOUL_BY", "FOUL_ON", "BALL_WIN", "OFFSIDE", "SUB", "OPP_GOAL",
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


def _call(payload: dict) -> str:
    """SDK when importable, else raw HTTPS (corp route). 429/5xx backoff + corp TLS."""
    try:
        import anthropic
        resp = anthropic.Anthropic().messages.create(**payload)
        return next((b.text for b in resp.content if b.type == "text"), "{}")
    except ImportError:
        pass
    import requests
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise SystemExit("ANTHROPIC_API_KEY not set.")
    base = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/")
    verify = (os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("AWS_CA_BUNDLE") or True)
    if os.environ.get("VLM_INSECURE_TLS") == "1":
        verify = False
        import urllib3
        urllib3.disable_warnings()
    for attempt in range(5):
        r = requests.post(f"{base}/v1/messages",
                          headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                                   "content-type": "application/json"},
                          json=payload, timeout=120, verify=verify)
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
        "PEN_AWARDED (NOT a player name), 'corner kick'→CORNER, 'offside'→OFFSIDE. "
        "Do not turn a mangled event word into a player.\n"
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
        except (json.JSONDecodeError, TypeError):
            pass
        i += CHUNK - OVERLAP
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
