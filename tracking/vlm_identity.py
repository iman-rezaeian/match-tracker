#!/usr/bin/env python3
"""VLM jersey-number identity — read each stitched tracklet's jersey number with
a VLM and emit per-tracklet identity SUGGESTION DRAFTS the coach confirms in the
PWA. Drafts are NEVER auto-applied: on coach accept they flow into
`identityOverrides` via the existing FIX-IDS saveOverrides path (see
PLAYER_ID_CORRECTION_UI). This is the individuating signal coach-log anchors
lack on same-kit U10 outfield players ([[phase-a-coach-log-outfield-dead]]).

Measured ceiling (2026-08-04, Opus via the OAuth-bearer path): precision 0.79 on
reads, ~0.9 with a confidence gate; coverage is gated on CROP QUALITY, so we
render number-optimized crops and VOTE across a tracklet's frames.

Mirrors the voice->draft pipeline (a standalone `tracking/` tool, not a pipeline
stage): decoupled, re-runnable, no VLM cost coupled into every analysis run.
Reconstructs stitched tracklets from cached tracks exactly as eval_stitch_assign,
reuses the VLM machinery (`_call`/`_read_number`/`_render_crops`/`_SCHEMA`) from
vlm_number_probe, and writes drafts via firestore_io.write_identity_drafts.

Auth (Opus needs the OAuth-bearer path; the corp raw key is Haiku-only):
    brew install anthropics/tap/ant   # once
    ant auth login                    # once, SSO browser flow (user)
    export ANTHROPIC_OAUTH_TOKEN="$(ant auth print-credentials --access-token)"
    unset ANTHROPIC_API_KEY
Run:
    set -a; source .env; set +a
    .venv-post-game/bin/python -m tracking.vlm_identity --game-id mri01pvelv46d --model claude-opus-4-8 --dry-run
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Callable, Optional

import numpy as np

# Reuse the probe's VLM machinery verbatim (dual-auth _call, vision _read_number,
# number-optimized _render_crops, the 0=no-number _SCHEMA). Keep the probe intact.
from tracking.vlm_number_probe import _read_number, _render_crops

DRAFT_SOURCE = "vlm_draft"


# --------------------------------------------------------------------------
# Pure logic (no network / video / firestore) — unit-tested in isolation.
# --------------------------------------------------------------------------
def build_number_map(roster) -> tuple[dict[str, int], dict[int, str], set[int]]:
    """From the roster, return (player_id->number, number->player_id, dup_numbers).

    The Firestore field is `number` (get_roster maps it to `.jersey_number`,
    Optional[int]). A number owned by >1 dressed player can't disambiguate — we
    collect those into `dup_numbers` so the caller refuses to draft them rather
    than silently last-wins."""
    num_of: dict[str, int] = {}
    holders: dict[int, list[str]] = {}
    for r in roster:
        jn = getattr(r, "jersey_number", None)
        if jn is None:
            continue
        try:
            jn = int(jn)
        except (TypeError, ValueError):
            continue
        num_of[r.id] = jn
        holders.setdefault(jn, []).append(r.id)
    player_of_num = {n: ids[0] for n, ids in holders.items() if len(ids) == 1}
    dup_numbers = {n for n, ids in holders.items() if len(ids) > 1}
    return num_of, player_of_num, dup_numbers


def vote_number(reads: list[dict], min_conf: float) -> tuple[Optional[int], float, int]:
    """Vote a jersey number across a tracklet's per-crop-batch VLM reads.

    Each read is {"number": int (0 = none), "confidence": float, ...}. Count only
    reads whose confidence clears `min_conf` and whose number is a real digit
    (not the 0 sentinel). The winning number is the most-voted; its confidence is
    the MAX confidence among its supporting reads (a tracklet's single clearest
    legible frame is the trustworthy signal). Returns (number|None, confidence,
    n_supporting_votes)."""
    votes: dict[int, list[float]] = {}
    for r in reads or []:
        n = r.get("number")
        c = float(r.get("confidence") or 0.0)
        if not n or c < min_conf:
            continue
        votes.setdefault(int(n), []).append(c)
    if not votes:
        return None, 0.0, 0
    # most votes, tie-broken by highest single confidence
    best = max(votes.items(), key=lambda kv: (len(kv[1]), max(kv[1])))
    num, confs = best
    return num, float(max(confs)), len(confs)


def make_draft(tracklet_id: int, number: Optional[int], confidence: float,
               player_of_num: dict[int, str], dup_numbers: set[int],
               valid_ids: Optional[set[str]], reasoning: str,
               current_player_id: Optional[str], minutes: float) -> Optional[dict]:
    """Build one identityDrafts item for a tracklet, or None if it shouldn't draft.

    None when: no number voted; the number is a duplicate (ambiguous roster); the
    number maps to no roster player; or the mapped player isn't in the logged
    squad. The `id` is deterministic per tracklet so a re-run REPLACES rather than
    duplicates."""
    if not number:
        return None
    if number in dup_numbers:
        return None
    pid = player_of_num.get(int(number))
    if not pid:
        return None
    if valid_ids is not None and pid not in valid_ids:
        return None
    return {
        "id": f"vid_{int(tracklet_id)}",
        "trackletId": int(tracklet_id),
        "suggestedPlayerId": pid,
        "jerseyNumber": int(number),
        "confidence": round(float(confidence), 2),
        "reasoning": (reasoning or "")[:120],
        "currentPlayerId": current_player_id,
        "minutes": round(float(minutes), 1),
        "source": DRAFT_SOURCE,
    }


def _tallest_rows(sub, k: int):
    """The k tallest (closest -> most legible) detections of a tracklet's rows."""
    s = sub.copy()
    s["_h"] = s["y2_eq"] - s["y1_eq"]
    return s[s["_h"] > 0].nlargest(k, "_h")


# --------------------------------------------------------------------------
# Orchestration (I/O). Kept thin so the pure logic above carries the tests.
# --------------------------------------------------------------------------
def read_tracklet_number(video: str, sub, tmp: Path, tracklet_id: int,
                         roster_numbers: list[int], model: str, crops: int,
                         min_conf: float, batches: int,
                         read_fn: Callable = _read_number,
                         render_fn: Callable = _render_crops) -> tuple[Optional[int], float, int, str]:
    """Render number-optimized crops for one tracklet and vote a number across
    `batches` independent VLM reads. Returns (number|None, confidence, votes,
    reasoning). read_fn/render_fn are injectable for tests."""
    tall = _tallest_rows(sub, crops * batches)
    if tall.empty:
        return None, 0.0, 0, "no-crops"
    imgs = render_fn(video, tall, crops * batches, tmp, tracklet_id)
    if not imgs:
        return None, 0.0, 0, "no-crops"
    # split the rendered crops into `batches` groups, one VLM read each -> vote
    groups = [imgs[i::batches] for i in range(batches)]
    reads = [read_fn(g, roster_numbers, model) for g in groups if g]
    num, conf, votes = vote_number(reads, min_conf)
    reasoning = ""
    for r in reads:
        if int(r.get("number") or 0) == (num or -1):
            reasoning = r.get("reasoning") or ""
            break
    return num, conf, votes, reasoning


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game-id", required=True)
    ap.add_argument("--model", default="claude-opus-4-8")
    ap.add_argument("--min-conf", type=float, default=0.5,
                    help="drop VLM reads below this confidence (default 0.5)")
    ap.add_argument("--crops", type=int, default=6, help="crops per read batch")
    ap.add_argument("--batches", type=int, default=2, help="independent reads to vote per tracklet")
    ap.add_argument("--min-tracklet-min", type=float, default=0.3,
                    help="skip tracklets shorter than this (not worth naming)")
    ap.add_argument("--max-tracklets", type=int, default=60,
                    help="only VLM-read the N longest eligible tracklets (cost cap; "
                         "tiny fragments read nothing). 0 = no cap")
    ap.add_argument("--min-onfield-frac", type=float, default=0.5,
                    help="skip tracklets spending less than this fraction inside the "
                         "field box — filters near-touchline sideline adults (coaches/refs)")
    ap.add_argument("--stitch-mode", choices=["greedy", "global"], default="global")
    ap.add_argument("--dry-run", action="store_true",
                    help="print reads + the draft set, but write NOTHING to Firestore")
    args = ap.parse_args()
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")

    import tempfile
    import pandas as pd
    from post_game import config, firestore_io
    from post_game.calibration import FieldProjector
    from post_game.pipeline import _our_color
    from post_game.reid_stitch import stitch_tracklets
    from post_game.team_classifier import classify_tracks

    if not (os.environ.get("ANTHROPIC_OAUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")):
        raise SystemExit("Set ANTHROPIC_OAUTH_TOKEN (ant auth) for Opus, or ANTHROPIC_API_KEY.")

    game = firestore_io.get_game(args.game_id)
    roster = firestore_io.get_roster()
    name_of = {r.id: f"#{getattr(r,'jersey_number','?')} {r.name}" for r in roster}
    num_of, player_of_num, dup_numbers = build_number_map(roster)
    roster_numbers = sorted(player_of_num) + sorted(dup_numbers)
    valid_ids = set(game.squad) if getattr(game, "squad", None) else None
    if dup_numbers:
        print(f"⚠ duplicate jersey numbers on roster (won't draft): {sorted(dup_numbers)}")

    cal = firestore_io.get_game_calibration(args.game_id)
    if cal is None:
        raise SystemExit("No calibration — can't reconstruct tracklets.")
    ckpt = config.OUTPUTS_DIR / args.game_id
    tracks_df = pd.read_parquet(ckpt / "tracks_raw.parquet")

    # jersey medians for classify (same reduction eval_stitch_assign uses)
    jersey: dict[int, list] = {}
    with np.load(ckpt / "jersey_samples.npz", allow_pickle=True) as nz:
        for k in nz.files:
            s = list(nz[k])
            if s:
                stacked = np.vstack([np.asarray(x, dtype=np.float32) for x in s])
                jersey[int(k)] = list(s)  # raw samples: stitch color-guard needs them
    jersey_med = {tid: [np.median(np.vstack([np.asarray(x, np.float32) for x in s]), axis=0)]
                  for tid, s in jersey.items() if s}

    proj = FieldProjector(cal)
    xy = proj.pixel_to_field_batch(tracks_df[["foot_x_eq", "foot_y_eq"]].to_numpy())
    tracks_df["x_m"], tracks_df["y_m"] = xy[:, 0], xy[:, 1]

    team_of_track = classify_tracks(
        tracks_df, jersey_med, our_home_color_hex=_our_color(game),
        opp_color_hex=game.away_color, ref_color_hex=game.ref_color)
    tracklet_of_track = stitch_tracklets(
        tracks_df, team_of_track, track_embeddings={}, track_jersey_samples=jersey,
        mode=args.stitch_mode, our_color_hex=_our_color(game), opp_color_hex=game.away_color)

    # group frames by stitched tracklet, our-team only
    our = {int(t) for t, tm in team_of_track.items() if tm == 0}
    tracks_df["tracklet"] = tracks_df["track_id"].map(
        lambda t: tracklet_of_track.get(int(t), int(t)))
    our_tl = tracks_df[tracks_df["track_id"].isin(our)]
    dt = 0.1
    dts = tracks_df.sort_values(["track_id", "time_s"]).groupby("track_id")["time_s"].diff().dropna()
    if len(dts[dts > 0]):
        dt = float(dts[dts > 0].median())

    # current per-tracklet identity from the persisted analytics doc (before/after)
    current_of_tl: dict[int, tuple[Optional[str], float]] = {}
    adoc = firestore_io.read_analytics(args.game_id) or {}
    for rec in adoc.get("tracklets", []) or []:
        current_of_tl[int(rec.get("tracklet_id"))] = (rec.get("player_id"),
                                                       float(rec.get("minutes") or 0.0))

    video = (game.video_url or "").replace("file://", "")
    have_video = bool(video) and Path(video).exists()
    if not have_video:
        print(f"⚠ raw video not on disk ({video!r}) — cannot render crops; no drafts.")
    tmp = Path(tempfile.mkdtemp(prefix="vlmid_"))

    print(f"game {args.game_id}: {our_tl['tracklet'].nunique()} our tracklets "
          f"(model={args.model}, min_conf={args.min_conf}, crops={args.crops}x{args.batches})\n")

    drafts: list[dict] = []
    L, W = cal.length_m, cal.width_m
    # Per-tracklet on-field fraction (foot inside the field box, +1.5 m tol).
    # Near-camera sideline adults (coaches/refs) sit at y≈W and mostly outside;
    # this drops them before we spend a VLM call reading "no number, adult".
    on = ((our_tl["x_m"] >= -1.5) & (our_tl["x_m"] <= L + 1.5)
          & (our_tl["y_m"] >= -1.5) & (our_tl["y_m"] <= W + 1.5))
    onfield_frac = our_tl.assign(_on=on).groupby("tracklet")["_on"].mean()
    # name the longest ELIGIBLE tracklets first, capped for cost
    tl_minutes = (our_tl.groupby("tracklet").size() * dt / 60.0).sort_values(ascending=False)
    eligible = [int(tl) for tl, mins in tl_minutes.items()
                if mins >= args.min_tracklet_min
                and float(onfield_frac.get(tl, 0.0)) >= args.min_onfield_frac]
    if args.max_tracklets > 0:
        eligible = eligible[: args.max_tracklets]
    print(f"  VLM-reading {len(eligible)} eligible tracklets "
          f"(>= {args.min_tracklet_min}min, on-field >= {args.min_onfield_frac})\n")
    for tl in eligible:
        mins = float(tl_minutes[tl])
        sub = our_tl[our_tl["tracklet"] == tl]
        cur_pid, cur_min = current_of_tl.get(tl, (None, float(mins)))
        num = conf = votes = None
        reasoning = "no-video"
        if have_video:
            num, conf, votes, reasoning = read_tracklet_number(
                video, sub, tmp, tl, roster_numbers, args.model,
                args.crops, args.min_conf, args.batches)
        draft = make_draft(tl, num, conf or 0.0, player_of_num, dup_numbers,
                            valid_ids, reasoning, cur_pid, cur_min)
        pred = name_of.get(draft["suggestedPlayerId"], "—") if draft else "—"
        cur = name_of.get(cur_pid, "—") if cur_pid else "—"
        print(f"  tl{tl:<6} {mins:4.1f}min  read#={str(num):<5} c={conf or 0:.2f} v={votes} "
              f"-> {pred:<20} (was {cur}) {'DRAFT' if draft else '—'}")
        if draft:
            drafts.append(draft)

    print(f"\n=== {len(drafts)} identity drafts (of {len(eligible)} VLM-read tracklets) ===")
    for d in sorted(drafts, key=lambda x: -x["minutes"]):
        print(f"  tl{d['trackletId']:<6} -> {name_of.get(d['suggestedPlayerId'],'?'):<20} "
              f"c={d['confidence']} ({d['minutes']}min)")

    if args.dry_run:
        print("\n[dry-run] NOT writing to Firestore.")
        return
    firestore_io.write_identity_drafts(args.game_id, drafts)
    print(f"\nwrote {len(drafts)} drafts to game.identityDrafts")


if __name__ == "__main__":
    main()
