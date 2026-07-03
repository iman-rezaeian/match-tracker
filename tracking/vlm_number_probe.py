#!/usr/bin/env python3
"""Phase B validation probe — can a VLM individuate players by reading jersey
numbers where coach-log anchors can't?

Phase A established coach-log anchors don't individuate OUTFIELD players
(sub 0.32, event 0.18-0.26 precision vs GT on two 8K games; only the keeper is
reliable). For a same-kit roster the one individuating signal a VLM can extract
is the JERSEY NUMBER. This probe measures exactly that, cheaply, by reusing the
crop strips the coach ALREADY labeled for the blind GT set:

  for each GT-labeled tracklet:  tl_<id>.jpg  ->  claude-opus-4-8  ->  {number}
  map number -> roster player -> compare to the GT true player.

Reports COVERAGE (fraction where a number was read) and PRECISION (of those,
fraction mapping to the correct player). Gate: if precision on read numbers is
not >> 0.3 (the coach-log outfield ceiling), Phase B doesn't clear the bar.

Read-only; no pipeline rerun. Needs ANTHROPIC_API_KEY + api.anthropic.com.
Run: set -a; source .env; set +a
     .venv-post-game/bin/python -m tracking.vlm_number_probe --game-id mqcf9axlvtuyt --n 50
"""
from __future__ import annotations

import argparse
import base64
import csv
import json
import os
from pathlib import Path

# Default to Haiku 4.5: it supports vision + structured output, is the cost lever
# for a whole-game batch, and (unlike Opus on some key tiers) isn't rate-capped.
# Override with --model claude-opus-4-8 in an environment with Opus access.
MODEL = "claude-haiku-4-5"
LABELS_ROOT = Path(__file__).resolve().parent / "labels"

# Union `type` (["integer","null"]) is rejected by structured outputs → use 0 as
# the "no legible number" sentinel (0 is never a valid jersey number).
_SCHEMA = {
    "type": "object",
    "properties": {
        "number": {"type": "integer",
                   "description": "Jersey number if legible on the shirt, else 0"},
        "confidence": {"type": "number", "description": "0..1 legibility confidence"},
        "reasoning": {"type": "string", "description": "<=15 words"},
    },
    "required": ["number", "confidence", "reasoning"],
    "additionalProperties": False,
}


def _read_number(imgs: list[str], roster_numbers: list[int], model: str) -> dict:
    """One VLM call: read the jersey number from a tracklet's crop(s)."""
    nums = ", ".join(str(n) for n in sorted(roster_numbers))
    system = (
        "You read jersey numbers off youth soccer players from cropped frames. "
        "The images show the SAME player in a dark kit across a few moments. "
        f"Valid squad numbers: {nums}. Report a number ONLY if you can actually "
        "read the digit(s) on the shirt (front or back) — do NOT guess from "
        "build/hair/position. If no digit is legible in any crop, return 0. "
        "Prefer 0 over a low-confidence guess."
    )
    content = [{"type": "image", "source": {"type": "base64",
                "media_type": "image/jpeg", "data": b}} for b in imgs]
    content.append({"type": "text", "text": "Read this player's jersey number."})
    payload = {
        "model": model,
        "max_tokens": 200,
        "system": [{"type": "text", "text": system,
                    "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": content}],
        "output_config": {"format": {"type": "json_schema", "schema": _SCHEMA}},
    }
    try:
        return json.loads(_call(payload))
    except (json.JSONDecodeError, TypeError):
        return {"number": 0, "confidence": 0.0, "reasoning": "parse-fail"}
    except RuntimeError as e:
        return {"number": 0, "confidence": 0.0, "reasoning": f"api-error {e}"[:60]}


def _render_crops(video: str, sub, k: int, tmp: Path, tl: int) -> list[str]:
    """Render the k tallest (closest → most legible) detections of a tracklet as
    tight, upscaled crops straight from the equirect frame. Returns base64 JPEGs.
    Number-optimized: small pad, 6× lanczos upscale — unlike the 4-panel GT strip."""
    import shutil, subprocess
    ff = shutil.which("ffmpeg")
    if not ff or not Path(video).exists():
        return []
    s = sub.copy()
    s["h"] = s["y2_eq"] - s["y1_eq"]
    s = s[s["h"] > 0].nlargest(k, "h")
    out: list[str] = []
    for i, (_, row) in enumerate(s.iterrows()):
        h = float(row["y2_eq"] - row["y1_eq"]); pad = h * 0.25
        cx = max(0, int(row["x1_eq"] - pad)); cy = max(0, int(row["y1_eq"] - pad))
        cw = int((row["x2_eq"] - row["x1_eq"]) + 2 * pad); ch = int(h + 2 * pad)
        dst = tmp / f"{tl}_{i}.jpg"
        try:
            subprocess.run(
                [ff, "-nostdin", "-loglevel", "error", "-ss", f"{float(row['time_s'])}",
                 "-i", video, "-vf", f"crop={cw}:{ch}:{cx}:{cy},scale=iw*6:ih*6:flags=lanczos",
                 "-frames:v", "1", "-q:v", "2", str(dst)], check=True, timeout=90)
            out.append(base64.standard_b64encode(dst.read_bytes()).decode())
        except Exception:
            continue
    return out


def _call(payload: dict, _tries: int = 5) -> str:
    """SDK when importable, else raw HTTPS (corp-VPN route) — as voice_clean.
    Retries transient 429/5xx with exponential backoff."""
    import time
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
    # Corp VPN does TLS interception → trust the corp CA bundle if provided
    # (same var R2 uploads use). Falls back to default trust store off-VPN.
    verify = (os.environ.get("REQUESTS_CA_BUNDLE")
              or os.environ.get("AWS_CA_BUNDLE") or True)
    if os.environ.get("VLM_INSECURE_TLS") == "1":
        verify = False  # opt-in escape for corp CA chains OpenSSL 3 rejects
        import urllib3
        urllib3.disable_warnings()
    base = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/")
    for attempt in range(_tries):
        r = requests.post(
            f"{base}/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json=payload, timeout=120, verify=verify)
        if r.status_code in (429, 500, 502, 503, 529) and attempt < _tries - 1:
            wait = float(r.headers.get("retry-after", 2 ** attempt))
            time.sleep(min(wait, 30))
            continue
        if r.status_code >= 400:
            raise RuntimeError(f"{r.status_code}: {r.text[:180]}")
        blocks = r.json().get("content", [])
        return next((b["text"] for b in blocks if b.get("type") == "text"), "{}")
    raise RuntimeError(f"API failed after {_tries} attempts (last {r.status_code}).")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--game-id", required=True)
    ap.add_argument("--n", type=int, default=50, help="max labeled tracklets to probe")
    ap.add_argument("--model", default=MODEL, help=f"VLM model id (default {MODEL})")
    ap.add_argument("--render", action="store_true",
                    help="Render fresh number-optimized crops from the 8K video "
                         "(coherent-parquet bboxes) instead of the GT label strips.")
    ap.add_argument("--crops", type=int, default=3, help="crops/tracklet in --render mode")
    args = ap.parse_args()

    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from post_game import firestore_io

    roster = firestore_io.get_roster()
    num_of = {}   # player_id -> jersey number
    player_of_num = {}
    for r in roster:
        jn = getattr(r, "jersey_number", None)
        if jn is not None:
            num_of[r.id] = int(jn)
            player_of_num[int(jn)] = r.id
    name_of = {r.id: f"#{getattr(r,'jersey_number','?')} {r.name}" for r in roster}
    roster_numbers = sorted(player_of_num)

    gt_dir = LABELS_ROOT / f"{args.game_id}_player_gt"
    gt_csv = gt_dir / "gt.csv"
    labeled = []
    for row in csv.DictReader(open(gt_csv)):
        if (row.get("label") or "").strip() == "player" and row.get("true_player_id"):
            img = gt_dir / (row.get("image") or f"tl_{row['tracklet_id']}.jpg")
            if img.exists():
                labeled.append((int(row["tracklet_id"]), row["true_player_id"],
                                float(row.get("minutes") or 0.0), img))
    # Probe the highest-coverage tracklets first (most worth naming).
    labeled.sort(key=lambda x: -x[2])
    sample = labeled[: args.n]
    src = f"rendered {args.crops} 8K crops/tracklet" if args.render else "GT label strips"
    print(f"probing {len(sample)}/{len(labeled)} labeled tracklets with {args.model} ({src})\n")

    # --render: fresh number-optimized crops from the 8K video.
    tmp = grp = video = None
    if args.render:
        import pandas as pd, tempfile
        cp = Path(__file__).resolve().parent / "outputs" / "identity_eval" / f"{args.game_id}.stage4.coherent.parquet"
        cdf = pd.read_parquet(cp)
        grp = {int(t): sub for t, sub in cdf.groupby("track_id")}
        video = (firestore_io.get_game(args.game_id).video_url or "").replace("file://", "")
        tmp = Path(tempfile.mkdtemp(prefix="vlmnum_"))
        print(f"  video: {video}\n")

    read = correct = 0
    read_min = correct_min = tot_min = 0.0
    rows = []
    for tl, true_pid, mins, img in sample:
        tot_min += mins
        if args.render:
            imgs = _render_crops(video, grp[tl], args.crops, tmp, tl) if tl in grp else []
        else:
            imgs = [base64.standard_b64encode(img.read_bytes()).decode()]
        res = _read_number(imgs, roster_numbers, args.model) if imgs else {"number": 0, "confidence": 0.0, "reasoning": "no-crops"}
        _n = res.get("number")
        num = int(_n) if _n not in (None, 0) else None  # 0 = "no legible number"
        pred_pid = player_of_num.get(num) if num is not None else None
        ok = pred_pid == true_pid
        if num is not None:
            read += 1; read_min += mins
            if ok:
                correct += 1; correct_min += mins
        rows.append((tl, true_pid, num, pred_pid, ok, mins,
                     round(float(res.get("confidence") or 0), 2)))
        mark = "OK" if ok else ("--" if num is None else "WRONG")
        print(f"  tl{tl:<6} gt={name_of.get(true_pid,true_pid):<20} "
              f"read#={str(num):<5} -> {name_of.get(pred_pid,'—') if pred_pid else '—':<20} "
              f"{mark:<6} {mins:.1f}min c={rows[-1][6]}")

    n = len(sample)
    print(f"\n=== VLM jersey-number probe · {args.game_id} ===")
    print(f"coverage: {read}/{n} tracklets read a number ({100*read/max(n,1):.0f}%)  "
          f"| {read_min:.1f}/{tot_min:.1f} min ({100*read_min/max(tot_min,1e-9):.0f}%)")
    print(f"precision | on read numbers: count={correct}/{read}="
          f"{correct/max(read,1):.2f}  time-wt={correct_min/max(read_min,1e-9):.2f}")
    print(f"end-to-end (correct / all sampled): count={correct}/{n}={correct/max(n,1):.2f}  "
          f"time-wt={correct_min/max(tot_min,1e-9):.2f}")
    print("(coach-log outfield ceiling was ~0.3 precision; keeper ~0.8-1.0)")

    out = gt_dir / "vlm_number_probe.json"
    out.write_text(json.dumps({
        "game_id": args.game_id, "model": args.model, "n": n,
        "coverage_count": read, "coverage_min": round(read_min, 1),
        "precision_on_read_count": round(correct / max(read, 1), 3),
        "precision_on_read_timewt": round(correct_min / max(read_min, 1e-9), 3),
        "rows": [{"tracklet": t, "true": p, "read_number": num, "pred": pp,
                  "ok": ok, "minutes": m, "confidence": c}
                 for (t, p, num, pp, ok, m, c) in rows],
    }, indent=2))
    print(f"\nwritten: {out}")


if __name__ == "__main__":
    main()
