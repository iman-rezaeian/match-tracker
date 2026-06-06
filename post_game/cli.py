"""CLI entry point: `python -m post_game.cli --game-id ... --field-name ...`"""

from __future__ import annotations

import json
import logging

import typer
from rich.console import Console
from rich.logging import RichHandler

from . import pipeline

app = typer.Typer(add_completion=False, help="Post-game analytics pipeline.")
console = Console()


@app.command()
def run(
    game_id: str = typer.Option(..., "--game-id", help="Firestore game document id."),
    field_name: str = typer.Option(None, "--field-name", help="(Legacy) Name of a calibrated field. If omitted, uses the per-game calibration stored on the game doc."),
    tv_view: bool = typer.Option(False, "--tv-view", help="Also render a full-game broadcast view + auto-highlight reel."),
    max_play_s: float = typer.Option(None, "--max-play-s", help="SMOKE TEST: process only N seconds centered on the middle of each half. Use ~120 for a fast end-to-end accuracy check."),
    smoke_window: list[str] = typer.Option(None, "--smoke-window", help="Explicit smoke window 'a-b' in source-video seconds. Pass multiple times for multiple windows. Overrides --max-play-s."),
    debug_frames_every_s: float = typer.Option(None, "--debug-frames-every-s", help="Save annotated preview frames every N video seconds to outputs/<game>/debug_frames/. Eyeball detection quality mid-run."),
    skip_clips: bool = typer.Option(True, "--skip-clips/--with-clips", help="Skip per-event highlight clip rendering. Default ON: the PWA uses tv_reel + broadcast_events to seek to any event, so per-event clips are only needed for downloadable montages (e.g. end-of-season). Pass --with-clips to render them."),
    skip_upload: bool = typer.Option(False, "--skip-upload", help="Skip R2 uploads of clips / TV reel / highlights. Files stay local under outputs/<game>/."),
    reuse_tv_reel: bool = typer.Option(False, "--reuse-tv-reel", help="Reuse an already-rendered outputs/<game>/tv_view/tv_reel.mp4 instead of re-rendering it (the multi-hour part). Implies --tv-view. Use to recover a run that died after the reel rendered but before uploads/analytics. Auto-highlights still render fresh."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Run the Tier A pipeline on a single finished game."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        handlers=[RichHandler(console=console, rich_tracebacks=True, show_path=False)],
    )
    # --reuse-tv-reel only makes sense with the TV-view stage; turn it on.
    if reuse_tv_reel:
        tv_view = True
    analytics = pipeline.run(
        game_id=game_id,
        field_name=field_name,
        tv_view=tv_view,
        max_play_s=max_play_s,
        debug_frames_every_s=debug_frames_every_s,
        skip_clips=skip_clips,
        skip_upload=skip_upload,
        smoke_windows=[tuple(map(float, w.split("-"))) for w in smoke_window] if smoke_window else None,
        reuse_tv_reel=reuse_tv_reel,
    )
    console.print_json(json.dumps({
        "game_id": game_id,
        "players_analyzed": len(analytics.get("player_stats", [])),
        "clips": analytics.get("clip_count", 0),
        "gk_events": len(analytics.get("gk_positions", [])),
        "tv_reel": (analytics.get("tv_reel") or {}).get("r2_url") or None,
        "auto_highlights": (analytics.get("auto_highlights") or {}).get("r2_url") or None,
    }))


@app.command()
def ball_gate(
    game_id: str = typer.Option(..., "--game-id"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Step 6.5: measure ball-detection hit rate on one game and print the gate verdict."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        handlers=[RichHandler(console=console, rich_tracebacks=True, show_path=False)],
    )
    from . import ball, config, firestore_io, pipeline
    from .video import open_video

    game = firestore_io.get_game(game_id)
    if not game.video_url:
        raise typer.BadParameter(f"Game {game_id} has no videoUrl.")
    video_path = pipeline._ensure_local_video(game.video_url, game_id)
    meta = open_video(str(video_path))

    console.print(f"[bold]Running phase-0 ball detector on {game_id} ({meta['duration_s']:.0f}s)...[/bold]")
    ball_df = ball.detect_ball(str(video_path), sample_rate=config.SAMPLE_RATE)
    report = ball.hit_rate_report(ball_df, total_frames=meta["total_frames"], fps=meta["fps"])

    out_md = config.OUTPUTS_DIR / game_id / "ball_detection_report.md"
    ball.write_report_md(report, game_id=game_id, out_path=out_md)

    console.print_json(json.dumps({
        "hit_rate_pct": round(report.hit_rate * 100, 1),
        "frames_with_ball": report.frames_with_ball,
        "sampled_frames": report.total_sampled_frames,
        "verdict": report.verdict,
        "report_md": str(out_md),
    }))


@app.command("set-video")
def set_video(
    game_id: str = typer.Option(..., "--game-id"),
    path: str = typer.Option(..., "--path", help="Absolute path to a local stitched MP4 (will be saved as file:// in Firestore)."),
) -> None:
    """Attach a local MP4 to a game by writing videoUrl=file:///... to Firestore."""
    from pathlib import Path as _P
    from . import firestore_io
    p = _P(path).expanduser()
    if not p.exists() or not p.is_file():
        raise typer.BadParameter(f"File does not exist: {p}")
    firestore_io.set_video_url(game_id, str(p))
    console.print_json(json.dumps({"game_id": game_id, "videoUrl": f"file://{p.resolve()}"}))


@app.command("delete-analytics")
def delete_analytics_cmd(
    game_id: str = typer.Option(..., "--game-id"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    keep_local: bool = typer.Option(False, "--keep-local", help="Don't delete the local outputs/<game_id>/ folder."),
) -> None:
    """Delete a game's analytics so the pipeline can be re-run cleanly.

    Wipes:
      - teams/main/games/<id>/analytics/*  (all version docs)
      - teams/main/games/<id>/clips/*      (per-event clip metadata)
      - public broadcast fields on the game doc (videoHighlightsUrl,
        videoFullGameUrl, broadcastEvents, ...) so the public page stops
        offering 'Watch Highlights' / 'Full Game' until the next run.
      - the local post_game/outputs/<id>/ folder (unless --keep-local).

    Does NOT touch: videoUrl, calibration, video offsets, the coach's
    events / score, or any R2 objects (those expire / can be overwritten
    on the next pipeline run).
    """
    import shutil
    from . import config, firestore_io

    if not yes:
        confirm = typer.confirm(
            f"Wipe analytics for game {game_id}? This is reversible only by re-running the pipeline.",
            default=False,
        )
        if not confirm:
            console.print("[yellow]Aborted.[/yellow]")
            raise typer.Exit(code=1)

    summary = firestore_io.delete_analytics(game_id)

    local_removed = False
    out_dir = config.OUTPUTS_DIR / game_id
    if not keep_local and out_dir.exists():
        shutil.rmtree(out_dir)
        local_removed = True

    console.print_json(json.dumps({
        "game_id": game_id,
        **summary,
        "local_outputs_removed": local_removed,
        "local_outputs_path": str(out_dir),
    }))


def _parse_offset(s: str) -> float:
    """Accept HH:MM:SS, MM:SS, or raw seconds (float)."""
    s = s.strip()
    if ":" in s:
        parts = [float(p) for p in s.split(":")]
        if len(parts) == 3:
            h, m, sec = parts
        elif len(parts) == 2:
            h, m, sec = 0.0, parts[0], parts[1]
        else:
            raise typer.BadParameter(f"Bad time format: {s}")
        return h * 3600 + m * 60 + sec
    return float(s)


@app.command("set-offset")
def set_offset(
    game_id: str = typer.Option(..., "--game-id"),
    kickoff: str = typer.Option(..., "--kickoff",
                                help="1st-half kickoff position in the source video. HH:MM:SS or raw seconds."),
) -> None:
    """Record where in the recording the 1st-half kickoff whistle occurs.

    The pipeline uses this + Firestore wallclock deltas to trim warmup,
    halftime, and post-game from the source video before analysis.
    """
    from . import firestore_io
    seconds = _parse_offset(kickoff)
    if seconds < 0:
        raise typer.BadParameter("Offset must be non-negative.")
    firestore_io.set_video_offset_h1_kickoff_s(game_id, seconds)
    console.print_json(json.dumps({"game_id": game_id, "videoOffsetH1KickoffS": seconds}))


@app.command("calibrate")
def calibrate(
    game_id: str = typer.Option(..., "--game-id"),
    at: float = typer.Option(60.0, "--at", help="Seconds into the video to grab the calibration frame from."),
    length: float = typer.Option(50.0, "--length", help="Field length (m)."),
    width: float  = typer.Option(35.0, "--width",  help="Field width (m)."),
    goal_width: float = typer.Option(4.88, "--goal-width", help="Goal mouth width (m). 4.88 = 16ft U10."),
    cam_height: float = typer.Option(5.0, "--cam-height",
        help="Camera height above pitch (m). Used as initial guess for the sphere fit; refined automatically. The X5-on-16ft-pole mount is ~5.0 m and changes <0.5m game to game."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Open the multi-point sphere-projection calibration tool in your browser.

    Click 13 reference landmarks on a frame from the game (4 corners, the
    halfway-line endpoints, the field center, all 4 goal posts, the 2 goal
    mid-points). The tool fits a 2D similarity + camera pitch/roll on top
    of the sphere-projection ray-trace and writes the result to Firestore
    under games/<game_id>.calibration. The pipeline then auto-detects the
    sphere model and uses it for all pixel↔field math.

    Run this once per game (camera height is stable across games, so the
    default 5.0m initial guess is fine — the optimizer refines it).
    """
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        handlers=[RichHandler(console=console, rich_tracebacks=True, show_path=False)],
    )
    from . import calibrate_flat
    payload = calibrate_flat.calibrate_flat(
        game_id, at_seconds=at,
        field_length_m=length, field_width_m=width,
        goal_width_m=goal_width, camera_height_m=cam_height,
    )
    gs = payload.get("ground_similarity", {}) if payload else {}
    console.print_json(json.dumps({
        "game_id": game_id,
        "calibration_saved": bool(payload),
        "reference_points": len(payload.get("reference_points", [])) if payload else 0,
        "rms_m": gs.get("rms_m"),
        "rms_weighted_m": gs.get("rms_weighted_m"),
    }))


@app.command("list")
def list_games(
    limit: int = typer.Option(15, "--limit", "-n", help="How many recent games to show."),
    only_unprocessed: bool = typer.Option(False, "--unprocessed", help="Only games with video but no analytics yet."),
) -> None:
    """List recent games so you can find a game-id to pass to `run`."""
    logging.basicConfig(level=logging.WARNING, format="%(message)s",
                        handlers=[RichHandler(console=console, show_path=False)])
    from rich.table import Table
    from . import firestore_io

    rows = firestore_io.list_recent_games_snapshots(limit=max(limit, 5))
    if only_unprocessed:
        rows = [r for r in rows if r["has_video"] and not r["has_analytics"]]
    rows = rows[:limit]
    if not rows:
        console.print("[yellow]No games found.[/yellow]")
        return
    t = Table(show_header=True, header_style="bold")
    t.add_column("#", justify="right")
    t.add_column("game-id", style="cyan")
    t.add_column("date")
    t.add_column("opponent")
    t.add_column("score", justify="right")
    t.add_column("status")
    t.add_column("video")
    t.add_column("calib")
    t.add_column("done")
    for i, r in enumerate(rows, 1):
        t.add_row(
            str(i),
            r["id"],
            r["date"] or "—",
            r["opponent"] or "—",
            f'{r["our_score"]}-{r["opp_score"]}',
            r["status"] or "—",
            "✓" if r["has_video"] else "—",
            "✓" if r["has_calibration"] else "—",
            "✓" if r["has_analytics"] else "—",
        )
    console.print(t)


if __name__ == "__main__":
    app()
