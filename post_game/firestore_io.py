"""Firestore + R2 I/O for the post_game pipeline.

Uses the synchronous `google-cloud-firestore` client. Reads from
`teams/main/games/<gameId>`, `teams/main` (roster), and `teams/main/fields/<name>`.
Writes back to `teams/main/games/<gameId>/analytics/<version>` and
`teams/main/games/<gameId>/clips/<eventId>`.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from . import config

log = logging.getLogger(__name__)


# --- Data classes --------------------------------------------------------

@dataclass
class CoachEvent:
    id: str
    type: str
    player_id: Optional[str]
    period: int
    elapsed: int
    at: int
    extras: dict = field(default_factory=dict)


@dataclass
class PausePeriod:
    started_at: int
    ended_at: Optional[int]


@dataclass
class GameDoc:
    id: str
    date: str
    opponent: str
    is_home: bool
    half_length_min: int
    period: int
    status: str
    started_at: int
    ended_at: Optional[int]
    elapsed_at_pause: int
    segment_started_at: Optional[int]
    pause_periods: list[PausePeriod]
    events: list[CoachEvent]
    squad: list[str]
    starting_lineup: list[str]
    gk_player_id: Optional[str]
    gk_changes: list[dict]
    our_score: int
    opp_score: int
    video_url: Optional[str]
    home_color: Optional[str]
    away_color: Optional[str]
    # Optional referee kit color (hex). When set AND distinct from both kits,
    # the team classifier uses a supervised 3-anchor split (ours/opp/ref) so the
    # on-pitch referee is excluded instead of mislabeled as our team.
    ref_color: Optional[str]
    field_name: Optional[str]
    # Seconds from the start of the source video to the 1st-half kickoff
    # whistle. Used to trim out pre-game warmup, halftime, and post-game tail.
    # Halftime + 2nd-half kickoff + final-whistle positions in video are
    # derived from this offset + wallclock deltas in `pause_periods` /
    # `ended_at`.
    video_offset_h1_kickoff_s: float = 0.0
    # Optional manual override for the 2nd-half kickoff in source-video
    # seconds. When set (> 0), takes precedence over the wallclock-derived
    # H2 start. Use this when the "start 2nd half" button was pressed late
    # (sub chaos, distracted coach, etc.).
    video_offset_h2_kickoff_s: float = 0.0
    # Coach explicitly confirmed each half's kickoff position in the calibration
    # step. An UNconfirmed kickoff (default) means the offset may be a silent 0
    # that shifts every player's on-field window — the Run-Analysis gate blocks
    # until BOTH are confirmed. H2 may be confirmed with the auto-derived start.
    video_offset_h1_confirmed: bool = False
    video_offset_h2_confirmed: bool = False
    # Match format: "7v7" (Canadian festivals/tournaments) or "9v9" (US
    # tournaments, from the 2026-27 season). Sets how many bodies the pipeline
    # should expect on the pitch. Every game predating the field is 7v7, so an
    # absent value means 7v7 rather than unknown.
    game_format: str = "7v7"
    # Per-game coach identity corrections, written by the PWA IdentityFixView:
    # { "<tracklet_id>": "<player_id>" | None }. A player_id force-assigns that
    # stitched tracklet to that roster player (status="coach", confidence=1.0);
    # None drops the tracklet (not our team / spectator). Coach overrides always
    # win over the auto-assignment. Applied in identity_assign.assign_identities_v2.
    # NOTE: tracklet ids are stable only while the Stage-2 track cache is
    # unchanged — a full re-track regenerates them and invalidates overrides.
    identity_overrides: dict = field(default_factory=dict)
    # Reserved for a FUTURE manual coach override of the camera on-field
    # correction ({player_id: {onS, offS} in video s}). v1 derives corrections
    # in-pipeline from identity_overrides + tracklet spans (sub_correct.py) and
    # writes nothing here; parsed read-only so the field round-trips if set.
    identity_sub_corrections: dict = field(default_factory=dict)


@dataclass
class RosterPlayer:
    id: str
    name: str
    jersey_number: Optional[int]
    photo_url: Optional[str]


@dataclass
class FieldCalibration:
    name: str
    length_m: float
    width_m: float
    src_points_px: list[tuple[float, float]]
    dst_points_m: list[tuple[float, float]]
    homography: list[list[float]]
    video_frame_size: tuple[int, int]
    # Sphere model params (preferred). None if calibration was saved with the
    # legacy planar-homography flow only. Populated from `ground_similarity`
    # + `camera_height_m` + `camera_pitch_deg` + `camera_roll_deg`.
    sphere: Optional[dict] = None


# --- Client --------------------------------------------------------------

@lru_cache(maxsize=1)
def _client():
    from google.cloud import firestore
    return firestore.Client(project=config.FIRESTORE_PROJECT_ID)


def _team_doc():
    return _client().document(config.FIRESTORE_TEAM_DOC)


# --- Reads ---------------------------------------------------------------

def get_game(game_id: str) -> GameDoc:
    snap = _team_doc().collection("games").document(game_id).get()
    if not snap.exists:
        raise RuntimeError(f"Game {game_id} not found in Firestore.")
    d = snap.to_dict() or {}
    events = [
        CoachEvent(
            id=str(e.get("id", "")),
            type=str(e.get("type", "")),
            player_id=e.get("playerId"),
            period=int(e.get("period", 1)),
            elapsed=int(e.get("elapsed", 0)),
            at=int(e.get("at", 0)),
            extras={k: v for k, v in e.items() if k not in {"id", "type", "playerId", "period", "elapsed", "at"}},
        )
        for e in d.get("events", []) or []
    ]
    pauses = [
        PausePeriod(started_at=int(p.get("startedAt", 0)), ended_at=p.get("endedAt"))
        for p in d.get("pausePeriods", []) or []
    ]
    return GameDoc(
        id=game_id,
        date=str(d.get("date", "")),
        opponent=str(d.get("opponent", "")),
        is_home=bool(d.get("isHome", True)),
        half_length_min=int(d.get("halfLengthMin", 25)),
        period=int(d.get("period", 1)),
        status=str(d.get("status", "finished")),
        started_at=int(d.get("startedAt", 0)),
        ended_at=d.get("endedAt"),
        elapsed_at_pause=int(d.get("elapsedAtPause", 0)),
        segment_started_at=d.get("segmentStartedAt"),
        pause_periods=pauses,
        events=events,
        squad=list(d.get("squad", []) or []),
        starting_lineup=list(d.get("startingLineup", []) or []),
        gk_player_id=d.get("gkPlayerId"),
        gk_changes=list(d.get("gkChanges", []) or []),
        our_score=int(d.get("ourScore", 0)),
        opp_score=int(d.get("oppScore", 0)),
        video_url=d.get("videoUrl"),
        home_color=d.get("homeColor"),
        away_color=d.get("awayColor"),
        ref_color=d.get("refColor"),
        field_name=d.get("fieldName"),
        video_offset_h1_kickoff_s=float(d.get("videoOffsetH1KickoffS", 0.0) or 0.0),
        video_offset_h2_kickoff_s=float(d.get("videoOffsetH2KickoffS", 0.0) or 0.0),
        video_offset_h1_confirmed=bool(d.get("videoOffsetH1Confirmed", False)),
        video_offset_h2_confirmed=bool(d.get("videoOffsetH2Confirmed", False)),
        identity_overrides={str(k): v for k, v in (d.get("identityOverrides") or {}).items()},
        identity_sub_corrections={str(k): v for k, v in (d.get("identitySubCorrections") or {}).items()},
        game_format=str(d.get("format") or "7v7"),
    )


def list_recent_games_snapshots(limit: int = 25) -> list[dict]:
    """Return lightweight summaries of recent games, newest first.

    Each dict has: id, date, opponent, our_score, opp_score, status,
    has_video, has_calibration, has_analytics, started_at.
    """
    coll = _team_doc().collection("games")
    try:
        from google.cloud.firestore import Query  # type: ignore
        q = coll.order_by("startedAt", direction=Query.DESCENDING).limit(limit)
        docs = list(q.stream())
    except Exception:
        docs = list(coll.limit(limit).stream())
    out: list[dict] = []
    for snap in docs:
        d = snap.to_dict() or {}
        has_analytics = False
        try:
            asub = list(coll.document(snap.id).collection("analytics").limit(1).stream())
            has_analytics = len(asub) > 0
        except Exception:
            pass
        out.append({
            "id": snap.id,
            "date": d.get("date", ""),
            "opponent": d.get("opponent", ""),
            "our_score": int(d.get("ourScore", 0)),
            "opp_score": int(d.get("oppScore", 0)),
            "status": d.get("status", ""),
            "has_video": bool(d.get("videoUrl")),
            "has_calibration": bool(d.get("calibration")),
            "has_video_offset": d.get("videoOffsetH1KickoffS") is not None,
            "video_offset_h1_kickoff_s": float(d.get("videoOffsetH1KickoffS") or 0.0),
            "video_offset_h2_kickoff_s": float(d.get("videoOffsetH2KickoffS") or 0.0),
            "video_offset_h1_confirmed": bool(d.get("videoOffsetH1Confirmed", False)),
            "video_offset_h2_confirmed": bool(d.get("videoOffsetH2Confirmed", False)),
            "has_analytics": has_analytics,
            "started_at": int(d.get("startedAt", 0)),
            # The calibration UI reads this to place its 2nd-half preview seek.
            # It was absent from this projection, so the UI silently fell back
            # to 30 while the real default is 25 — landing the preview ~5 min
            # past kickoff. Default mirrors GameDoc's.
            "half_length_min": int(d.get("halfLengthMin", 25)),
        })
    out.sort(key=lambda r: r["started_at"], reverse=True)
    return out


def get_roster() -> list[RosterPlayer]:
    snap = _team_doc().get()
    if not snap.exists:
        return []
    data = snap.to_dict() or {}
    out = []
    for r in data.get("roster", []) or []:
        out.append(
            RosterPlayer(
                id=str(r.get("id", "")),
                name=str(r.get("name", "")),
                jersey_number=r.get("number"),
                photo_url=r.get("photo"),
            )
        )
    return out


def get_field(field_name: str) -> Optional[FieldCalibration]:
    snap = _team_doc().collection("fields").document(field_name).get()
    if not snap.exists:
        return None
    d = snap.to_dict() or {}
    return _calibration_from_dict(d, default_name=field_name)


def get_game_calibration(game_id: str) -> Optional[FieldCalibration]:
    """Read the per-game calibration written by the FieldCalibrationModal."""
    snap = _team_doc().collection("games").document(game_id).get()
    if not snap.exists:
        return None
    d = (snap.to_dict() or {}).get("calibration")
    if not d:
        return None
    return _calibration_from_dict(d, default_name=game_id)


def _calibration_from_dict(d: dict, default_name: str) -> FieldCalibration:
    """Accept both legacy (nested-array) and new (flat) schemas."""
    # Source/destination points: either list-of-lists (legacy) or {p0,p1,p2,p3} objects.
    def _pts(value):
        if isinstance(value, dict):
            out = []
            for k in ("p0", "p1", "p2", "p3"):
                p = value.get(k) or {}
                out.append((float(p.get("x", 0.0)), float(p.get("y", 0.0))))
            return out
        return [tuple(p) for p in (value or [])]

    src = _pts(d.get("src_points_px"))
    dst = _pts(d.get("dst_points_m"))

    # Homography: prefer flat 9-element, fall back to 3x3 list.
    Hflat = d.get("homography_flat")
    if Hflat and len(Hflat) == 9:
        H = [[float(Hflat[0]), float(Hflat[1]), float(Hflat[2])],
             [float(Hflat[3]), float(Hflat[4]), float(Hflat[5])],
             [float(Hflat[6]), float(Hflat[7]), float(Hflat[8])]]
    else:
        H = [list(row) for row in d.get("homography", [])]

    # Frame size: split fields (new) or 2-tuple (legacy).
    if "video_frame_w" in d or "video_frame_h" in d:
        size = (int(d.get("video_frame_w", 0)), int(d.get("video_frame_h", 0)))
    else:
        size = tuple(d.get("video_frame_size", (0, 0)))

    # Sphere model (preferred). Requires ground_similarity + a frame size.
    sphere = None
    gs = d.get("ground_similarity")
    if gs and size[0] and size[1]:
        try:
            sphere = {
                "a":  float(gs["a"]),  "b":  float(gs["b"]),
                "tx": float(gs["tx"]), "ty": float(gs["ty"]),
                "cam_h_m":   float(d.get("camera_height_m", 5.0)),
                "pitch_deg": float(d.get("camera_pitch_deg", 0.0)),
                "roll_deg":  float(d.get("camera_roll_deg",  0.0)),
                "eq_w": int(size[0]),
                "eq_h": int(size[1]),
                "rms_m": float(gs.get("rms_m", 0.0)),
            }
        except (KeyError, TypeError, ValueError):
            sphere = None

    return FieldCalibration(
        name=str(d.get("name", default_name)),
        length_m=float(d.get("length_m", 50.0)),
        width_m=float(d.get("width_m", 35.0)),
        src_points_px=src,
        dst_points_m=dst,
        homography=H,
        video_frame_size=size,
        sphere=sphere,
    )


# --- Writes --------------------------------------------------------------

def save_field(field_cal: FieldCalibration) -> None:
    _team_doc().collection("fields").document(field_cal.name).set(
        {
            "name": field_cal.name,
            "length_m": field_cal.length_m,
            "width_m": field_cal.width_m,
            "src_points_px": [list(p) for p in field_cal.src_points_px],
            "dst_points_m": [list(p) for p in field_cal.dst_points_m],
            "homography": field_cal.homography,
            "video_frame_size": list(field_cal.video_frame_size),
        }
    )


# --- Per-field SCALE anchor (accuracy: map-measured touchline length) ---------
# Absolute scale can't be recovered from a single grazing camera (goal size
# varies between U10 fields, so nothing in-scene has a known length). The coach
# reads the touchline length off a satellite map ONCE per field; it's stored
# here keyed by a short field label and reused for every future game on that
# field. See CALIBRATION_SCALE_PLAN.md + calibration_solve.solve_sphere_scaled.

def list_field_scales() -> list[dict]:
    """All stored per-field scale anchors: [{field_key, length_m, width_m, source}].

    `width_m` is the solver-recovered field width (None on older entries that
    predate width persistence); it feeds the calibration-QC consistency check.
    """
    out = []
    for snap in _team_doc().collection("fields").stream():
        d = snap.to_dict() or {}
        if d.get("map_length_m") is not None:
            w = d.get("map_width_m")
            out.append({
                "field_key": snap.id,
                "length_m": float(d["map_length_m"]),
                "width_m": float(w) if w is not None else None,
                "source": d.get("map_source", ""),
            })
    return sorted(out, key=lambda r: r["field_key"])


def get_field_scale(field_key: str) -> Optional[dict]:
    """The stored map-measured length (+ solved width, if recorded) for a field."""
    snap = _team_doc().collection("fields").document(field_key).get()
    if not snap.exists:
        return None
    d = snap.to_dict() or {}
    if d.get("map_length_m") is None:
        return None
    w = d.get("map_width_m")
    return {"field_key": field_key, "length_m": float(d["map_length_m"]),
            "width_m": float(w) if w is not None else None,
            "source": d.get("map_source", "")}


def save_field_scale(field_key: str, length_m: float, source: str = "",
                     width_m: Optional[float] = None) -> None:
    """Persist the map-measured length (+ solved width) for a field (merge, so it
    coexists with any legacy FieldCalibration on the same doc).

    `source` is only written when non-empty, so a specific note the coach set
    earlier (e.g. "goal-to-goal (coach)") is never clobbered by a generic
    default on a later save.
    """
    payload: dict[str, Any] = {"map_length_m": float(length_m)}
    if source:
        payload["map_source"] = source
    if width_m is not None:
        payload["map_width_m"] = float(width_m)
    _team_doc().collection("fields").document(field_key).set(payload, merge=True)


def set_game_field(game_id: str, field_key: str) -> None:
    """Link a game to the field it was played on (so its scale is reusable)."""
    _team_doc().collection("games").document(game_id).set(
        {"fieldName": field_key}, merge=True
    )


# Keys the pipeline does NOT produce and must therefore never destroy on a full
# write. `click_stats` is the coach's own hand-tagged positions -- ~10 minutes of
# his labour per game, published by tracking/click_publish.py, and the ONLY
# trustworthy per-player positional source in the app. A full re-render wiped it
# on the Caboto game (2026-08-15) simply because `set()` replaces the document.
_PRESERVE_ON_FULL_WRITE = ("click_stats",)


def write_analytics(game_id: str, analytics: dict[str, Any]) -> None:
    """Replace the analytics doc, preserving keys the pipeline never writes.

    `set()` without merge is deliberate for everything the pipeline DOES own -- a
    stale key from a previous schema should disappear rather than linger. But keys
    owned by another producer have to be carried across explicitly, or a re-render
    silently destroys them.
    """
    ref = (_team_doc().collection("games").document(game_id)
           .collection("analytics").document(config.ANALYTICS_DOC_VERSION))
    payload = dict(analytics)
    try:
        prev = ref.get()
        old = prev.to_dict() if prev.exists else None
    except Exception:  # a read failure must not block the write
        old = None
    if old:
        for key in _PRESERVE_ON_FULL_WRITE:
            if key not in payload and old.get(key) is not None:
                payload[key] = old[key]
    ref.set(payload)


def write_analytics_merge(game_id: str, fields: dict[str, Any]) -> None:
    """MERGE fields into the analytics doc — keys NOT provided are preserved.
    Used by the stats-only refresh to update identity-dependent analytics
    (player_stats, formation, tracklets, ...) without touching the reel / audio /
    broadcast-index fields."""
    _team_doc().collection("games").document(game_id).collection("analytics").document(
        config.ANALYTICS_DOC_VERSION
    ).set(fields, merge=True)


def read_analytics(game_id: str) -> Optional[dict]:
    """Read the current analytics doc (or None)."""
    snap = (_team_doc().collection("games").document(game_id)
            .collection("analytics").document(config.ANALYTICS_DOC_VERSION).get())
    return snap.to_dict() if snap.exists else None


# Keys the season view actually reads. Everything else in the analytics doc is
# either film-room detail or per-track debug data.
_SUMMARY_KEYS = ("player_stats", "field_tilt", "generated_at_ms",
                 "team_shape_filter")
# Per-player fields the squad table and its sparklines use. `heatmap_grid` is
# deliberately excluded: 96 floats per player per game, and the season view has
# never drawn a heatmap.
_SUMMARY_PLAYER_KEYS = ("player_id", "minutes_played", "pct_defensive_third",
                        "pct_middle_third", "pct_attacking_third")


def write_analytics_summary(game_id: str, analytics: dict[str, Any]) -> None:
    """Write a SMALL companion doc for the season view to fan out over.

    The season view fetches one analytics doc per finished game in a single
    Promise.all. The full docs run 420-970 KB each -- ~5 MB across seven games,
    of which ~3.4 MB is `identity_assignments`, a per-track array it never reads
    (measured: it touches `player_stats` and nothing else). That download and the
    main-thread JSON parse are what made the view open to a black screen on a
    phone, and it gets worse with every game played.

    So the fan-out target becomes this projection instead, which is ~2% of the
    size and grows only with the roster. Derived from the payload just written,
    so it cannot drift from the doc it summarises.

    Also carries `click_stats.players[].n_clicks` -- not for numbers, only so the
    squad table can mark which games are tagged and therefore which per-player
    positions exist at all.
    """
    out: dict[str, Any] = {k: analytics[k] for k in _SUMMARY_KEYS if k in analytics}
    if isinstance(analytics.get("player_stats"), list):
        out["player_stats"] = [
            {k: s[k] for k in _SUMMARY_PLAYER_KEYS if k in s}
            for s in analytics["player_stats"] if isinstance(s, dict)
        ]
    cs = analytics.get("click_stats")
    if isinstance(cs, dict):
        out["click_stats"] = {
            "n_clicks": cs.get("n_clicks"),
            "n_frames": cs.get("n_frames"),
            "median_pos_err_m": cs.get("median_pos_err_m"),
            # Load-bearing for any cross-game pooling: when the keeper's median
            # sits mid-pitch the orientation resolver REFUSES rather than guess,
            # and this game's depth figures are then in an undefined frame.
            # Averaging an unoriented game into a season figure mirrors half of
            # its contribution. Callers must exclude `oriented: false` games.
            "oriented": cs.get("oriented"),
            "players": [{"player_id": p.get("player_id"),
                         "n_clicks": p.get("n_clicks"),
                         "avg_depth_m": p.get("avg_depth_m"),
                         "pct_defensive_third": p.get("pct_defensive_third"),
                         "pct_middle_third": p.get("pct_middle_third"),
                         "pct_attacking_third": p.get("pct_attacking_third")}
                        for p in (cs.get("players") or []) if isinstance(p, dict)],
        }
    (_team_doc().collection("games").document(game_id)
     .collection("analytics").document(config.ANALYTICS_SUMMARY_DOC).set(out))


def collect_prior_player_top_speeds(exclude_game_id: str | None = None) -> dict[str, list[float]]:
    """{player_id: [top_speed_ms per prior game]} from every other game's
    analytics doc. Feeds the personalized sprint threshold (plan 4.5)."""
    out: dict[str, list[float]] = {}
    games = _team_doc().collection("games")
    for snap in games.stream():
        if exclude_game_id and snap.id == exclude_game_id:
            continue
        try:
            a = games.document(snap.id).collection("analytics").document(
                config.ANALYTICS_DOC_VERSION).get()
        except Exception:
            continue
        if not a.exists:
            continue
        for ps in (a.to_dict() or {}).get("player_stats", []) or []:
            pid = ps.get("player_id")
            ts = ps.get("top_speed_ms")
            if pid and isinstance(ts, (int, float)) and ts > 0:
                out.setdefault(str(pid), []).append(float(ts))
    return out


# Public broadcast fields set on the game doc by `set_public_reels` after a
# pipeline run. Keep this list in sync with the keys written below in
# `set_public_reels` so `delete_analytics` clears every one of them.
_PUBLIC_REEL_FIELDS = (
    "videoHighlightsUrl",
    "videoHighlightsDurationS",
    "videoFullGameUrl",
    "videoFullGameDurationS",
    "broadcastEvents",
    "broadcastHomeName",
    "broadcastAwayName",
    "broadcastHomeColor",
    "broadcastAwayColor",
)


def delete_analytics(game_id: str) -> dict[str, int]:
    """Wipe everything the pipeline writes for a game so it can be re-run.

    Deletes:
      - all docs in teams/main/games/<id>/analytics/   (per-version subdocs)
      - all docs in teams/main/games/<id>/clips/       (per-event clip meta)
      - public broadcast fields on the game doc (so PublicHomePage stops
        offering the highlight / TV-reel buttons until next run)

    Does NOT touch: videoUrl, calibration, video offsets, or the game's
    own events / score — only post-game-analytics artefacts.

    Returns a small {analytics_docs, clip_docs, public_fields_cleared}
    counter so the caller can show the user what happened.
    """
    from google.cloud.firestore import DELETE_FIELD  # type: ignore

    game_ref = _team_doc().collection("games").document(game_id)

    analytics_count = 0
    for snap in game_ref.collection("analytics").stream():
        snap.reference.delete()
        analytics_count += 1

    clip_count = 0
    for snap in game_ref.collection("clips").stream():
        snap.reference.delete()
        clip_count += 1

    # The on-demand public broadcast doc (events index lives here now).
    try:
        game_ref.collection("public").document("broadcast").delete()
    except Exception as e:
        log.debug("public/broadcast delete skipped for %s: %s", game_id, e)

    # Strip the public broadcast fields. update() with DELETE_FIELD on a
    # missing key is a no-op, so we don't need to read first.
    field_count = 0
    try:
        game_ref.update({k: DELETE_FIELD for k in _PUBLIC_REEL_FIELDS})
        field_count = len(_PUBLIC_REEL_FIELDS)
    except Exception as e:
        log.warning("Could not clear public broadcast fields on %s: %s", game_id, e)

    return {
        "analytics_docs": analytics_count,
        "clip_docs": clip_count,
        "public_fields_cleared": field_count,
    }


def write_game_calibration(game_id: str, calibration: dict[str, Any]) -> None:
    """Merge the per-game calibration onto the game doc. Schema must match
    what the PWA's FieldCalibrationModal writes — see _calibration_from_dict."""
    _team_doc().collection("games").document(game_id).set(
        {"calibration": calibration}, merge=True
    )


def write_clip_metadata(game_id: str, event_id: str, meta: dict[str, Any]) -> None:
    _team_doc().collection("games").document(game_id).collection("clips").document(event_id).set(meta)


def write_voice_drafts(game_id: str, drafts: list[dict]) -> None:
    """Write voice-extracted draft events to the game doc's `voiceDrafts` field.

    The PWA confirm queue reads these; the coach one-click accepts a draft into
    `game.events` (source='voice-confirmed') or dismisses it. Additive and
    reversible — a new sibling field to `voiceSegments`; does NOT touch
    `events`/scores/stats. Overwrites the field (re-running extraction replaces
    the draft set rather than appending duplicates)."""
    _team_doc().collection("games").document(game_id).set(
        {"voiceDrafts": drafts}, merge=True)


def write_identity_drafts(game_id: str, drafts: list[dict]) -> None:
    """Write VLM jersey-number identity suggestions to `game.identityDrafts`.

    Each draft maps a stitched tracklet to a suggested roster player (read off
    the jersey number by the VLM). The PWA FIX-IDS view surfaces these as
    per-tracklet Accept suggestions; on accept the coach's choice flows into
    `identityOverrides` via the existing saveOverrides path and is applied on the
    next pipeline re-run. These drafts are SUGGESTIONS ONLY — never auto-applied.

    Additive and reversible: a new sibling field on the game doc; does NOT touch
    `events`/scores/stats/`identityOverrides`. Overwrites the field (a re-run
    replaces the draft set — the deterministic per-tracklet `id` prevents dupes)."""
    _team_doc().collection("games").document(game_id).set(
        {"identityDrafts": drafts}, merge=True)


def set_public_reels(game_id: str, fields: dict[str, Any]) -> None:
    """Merge public-safe broadcast-reel fields onto the game doc.

    Why this lives on the game doc (not the analytics subcollection):
    parents/spectators need the video URLs + the per-event overlay index
    to render the public 'Watch Highlights' button + on-screen scorebug,
    but they must NOT be able to read the rest of the analytics doc
    (per-player stats, GK positioning, identity confidences, etc.).
    Firestore rules then lock down the analytics/ subcollection to coaches.

    Expected keys (all optional, only what's present is written):
      videoHighlightsUrl, videoHighlightsDurationS,
      videoFullGameUrl,   videoFullGameDurationS,
      broadcastEvents (list[dict] — first-name + jersey# only),
      broadcastHomeName, broadcastAwayName,
      broadcastHomeColor, broadcastAwayColor.
    """
    if not fields:
        return
    _team_doc().collection("games").document(game_id).set(fields, merge=True)


def set_public_broadcast_events(game_id: str, events: list) -> None:
    """Write the per-event overlay index to games/<id>/public/broadcast.

    Moved OFF the game doc (2026-06-13): broadcastEvents is ~100 KB/analyzed
    game and the dugout list + public scoreboard pull every game doc on load,
    but only need this index when a reel actually opens. Parking it in a
    public-readable subcollection doc (fetched on demand) keeps the list lean
    as the season grows. Requires the games/<id>/public/{doc} public-read
    rule (see firestore.rules)."""
    (_team_doc().collection("games").document(game_id)
        .collection("public").document("broadcast")
        .set({"events": events}))


def clear_legacy_broadcast_events(game_id: str) -> None:
    """Delete the obese on-doc `broadcastEvents` field (now in public/broadcast)
    so re-run game docs shrink. No-op when the field is already absent."""
    from google.cloud.firestore import DELETE_FIELD  # type: ignore
    try:
        _team_doc().collection("games").document(game_id).update(
            {"broadcastEvents": DELETE_FIELD})
    except Exception as e:
        log.debug("clear_legacy_broadcast_events skipped for %s: %s", game_id, e)


def set_video_url(game_id: str, url: str) -> None:
    """Set `videoUrl` on the game doc. Accepts file:// for local Mac files,
    https:// for R2 / hosted videos, or a bare path (will be normalized to file://)."""
    if not url.startswith(("file://", "http://", "https://")):
        url = "file://" + str(Path(url).expanduser().resolve())
    _team_doc().collection("games").document(game_id).set(
        {"videoUrl": url}, merge=True
    )


def set_video_offset_h1_kickoff_s(game_id: str, offset_s: float,
                                  confirmed: bool = True) -> None:
    """Persist the seconds-into-source-video of the 1st-half kickoff whistle.

    `confirmed` marks that the coach deliberately set/verified it (default True —
    a coach saving from the UI is confirming). The Run-Analysis gate requires
    confirmation so a silent default-0 offset can't shift every on-field window."""
    _team_doc().collection("games").document(game_id).set(
        {"videoOffsetH1KickoffS": float(offset_s),
         "videoOffsetH1Confirmed": bool(confirmed)}, merge=True
    )


def set_video_offset_h2_kickoff_s(game_id: str, offset_s: float,
                                  confirmed: bool = True) -> None:
    """Persist a manual override for the 2nd-half kickoff (source-video seconds).

    When > 0, overrides the wallclock-derived H2 start in `half_windows()`
    and `period_clock_to_video_time_factory()`. Set to 0 to fall back to the
    auto-derived value. `confirmed` records that the coach verified the H2 start
    (either by entering a timestamp or accepting the auto-derived one) — the
    Run-Analysis gate requires it.
    """
    _team_doc().collection("games").document(game_id).set(
        {"videoOffsetH2KickoffS": float(offset_s),
         "videoOffsetH2Confirmed": bool(confirmed)}, merge=True
    )


# --- R2 ------------------------------------------------------------------

@lru_cache(maxsize=1)
def _r2_client():
    import os
    import boto3
    # Corp VPNs MITM TLS with a self-signed root, which botocore's bundled
    # certifi store doesn't trust -> uploads die with CERTIFICATE_VERIFY_FAILED.
    # Honour the same CA-bundle env vars as download_video() so a combined
    # macOS-keychain + certifi bundle can be pointed at via .env.
    ca = (os.environ.get("AWS_CA_BUNDLE")
          or os.environ.get("REQUESTS_CA_BUNDLE")
          or os.environ.get("SSL_CERT_FILE"))
    return boto3.client(
        "s3",
        endpoint_url=config.R2_ENDPOINT,
        aws_access_key_id=os.environ.get("R2_ACCESS_KEY_ID"),
        aws_secret_access_key=os.environ.get("R2_SECRET_ACCESS_KEY"),
        region_name="auto",
        verify=ca or None,
    )


def upload_clip(local_path: str, key: str) -> str:
    _r2_client().upload_file(local_path, config.R2_BUCKET, key, ExtraArgs={"ContentType": "video/mp4"})
    base = config.R2_PUBLIC_BASE.rstrip("/")
    return f"{base}/{key}" if base else f"r2://{config.R2_BUCKET}/{key}"


def upload_image(local_path: str, key: str, content_type: str = "image/jpeg") -> str:
    """Upload a still image (e.g. per-tracklet thumbnail) to R2 and return its
    public URL. Same bucket/base as `upload_clip` but image content type."""
    _r2_client().upload_file(local_path, config.R2_BUCKET, key, ExtraArgs={"ContentType": content_type})
    base = config.R2_PUBLIC_BASE.rstrip("/")
    return f"{base}/{key}" if base else f"r2://{config.R2_BUCKET}/{key}"


def download_video(url: str, dest: Path) -> Path:
    """Download an https URL (R2 public) to local disk if not already cached."""
    import ssl
    import urllib.request
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    log.info("Downloading video %s -> %s", url, dest)
    # Honour corp CA bundles (REQUESTS_CA_BUNDLE / SSL_CERT_FILE) when set —
    # corp VPNs MITM TLS with a self-signed root.
    ca = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
    ctx = ssl.create_default_context(cafile=ca) if ca else None
    with urllib.request.urlopen(url, context=ctx) as resp, open(dest, "wb") as f:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    return dest
