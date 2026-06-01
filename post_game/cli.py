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
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Run the Tier A pipeline on a single finished game."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        handlers=[RichHandler(console=console, rich_tracebacks=True, show_path=False)],
    )
    analytics = pipeline.run(game_id=game_id, field_name=field_name, tv_view=tv_view)
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
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Open the local browser calibration tool (tap 4 field corners, SAVE)."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        handlers=[RichHandler(console=console, rich_tracebacks=True, show_path=False)],
    )
    from . import calibrate_local
    payload = calibrate_local.calibrate_in_browser(game_id)
    console.print_json(json.dumps({"game_id": game_id, "calibration_saved": bool(payload)}))


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
