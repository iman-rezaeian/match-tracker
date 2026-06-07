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
    # Per-game coach identity corrections, written by the PWA IdentityFixView:
    # { "<tracklet_id>": "<player_id>" | None }. A player_id force-assigns that
    # stitched tracklet to that roster player (status="coach", confidence=1.0);
    # None drops the tracklet (not our team / spectator). Coach overrides always
    # win over the auto-assignment. Applied in identity_assign.assign_identities_v2.
    # NOTE: tracklet ids are stable only while the Stage-2 track cache is
    # unchanged — a full re-track regenerates them and invalidates overrides.
    identity_overrides: dict = field(default_factory=dict)


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
        identity_overrides={str(k): v for k, v in (d.get("identityOverrides") or {}).items()},
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
            "has_analytics": has_analytics,
            "started_at": int(d.get("startedAt", 0)),
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


def write_analytics(game_id: str, analytics: dict[str, Any]) -> None:
    _team_doc().collection("games").document(game_id).collection("analytics").document(
        config.ANALYTICS_DOC_VERSION
    ).set(analytics)


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


def set_video_url(game_id: str, url: str) -> None:
    """Set `videoUrl` on the game doc. Accepts file:// for local Mac files,
    https:// for R2 / hosted videos, or a bare path (will be normalized to file://)."""
    if not url.startswith(("file://", "http://", "https://")):
        url = "file://" + str(Path(url).expanduser().resolve())
    _team_doc().collection("games").document(game_id).set(
        {"videoUrl": url}, merge=True
    )


def set_video_offset_h1_kickoff_s(game_id: str, offset_s: float) -> None:
    """Persist the seconds-into-source-video of the 1st-half kickoff whistle."""
    _team_doc().collection("games").document(game_id).set(
        {"videoOffsetH1KickoffS": float(offset_s)}, merge=True
    )


def set_video_offset_h2_kickoff_s(game_id: str, offset_s: float) -> None:
    """Persist a manual override for the 2nd-half kickoff (source-video seconds).

    When > 0, overrides the wallclock-derived H2 start in `half_windows()`
    and `period_clock_to_video_time_factory()`. Set to 0 to fall back to the
    auto-derived value.
    """
    _team_doc().collection("games").document(game_id).set(
        {"videoOffsetH2KickoffS": float(offset_s)}, merge=True
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
