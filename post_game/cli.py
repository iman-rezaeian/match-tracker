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
    field_name: str = typer.Option(..., "--field-name", help="Name of a calibrated field."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Run the Tier A pipeline on a single finished game."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        handlers=[RichHandler(console=console, rich_tracebacks=True, show_path=False)],
    )
    analytics = pipeline.run(game_id=game_id, field_name=field_name)
    console.print_json(json.dumps({
        "game_id": game_id,
        "players_analyzed": len(analytics.get("player_stats", [])),
        "clips": analytics.get("clip_count", 0),
        "gk_events": len(analytics.get("gk_positions", [])),
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


if __name__ == "__main__":
    app()
