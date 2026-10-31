from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.markup import escape

from . import mkv, ocr
from .errors import SubtransError
from .pipeline import DEFAULT_BATCH_SIZE, load_cues, translate_file
from .translate import DEFAULT_MODEL, TransientError, get_api_key, make_client, preflight

app = typer.Typer(add_completion=False, pretty_exceptions_show_locals=False)
console = Console()


@app.command()
def main(
    file: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True, help="Input .mkv file")],
    model: Annotated[str, typer.Option("--model", "-m", help="OpenRouter model slug")] = DEFAULT_MODEL,
    track: Annotated[int | None, typer.Option("--track", "-t", help="Subtitle track id (skips the auto-pick)")] = None,
    list_tracks: Annotated[bool, typer.Option("--list", "-l", help="List subtitle tracks and exit")] = False,
    out: Annotated[
        Path | None, typer.Option("--out", "-o", help="Output file (default: <name>.pt-BR.<ext> next to the input)")
    ] = None,
    batch_size: Annotated[int, typer.Option("--batch-size", "-b", min=1, help="Cues per API request")] = (
        DEFAULT_BATCH_SIZE
    ),
    limit: Annotated[
        int | None, typer.Option("--limit", min=1, help="Translate only the first N cues, to a sample file")
    ] = None,
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing output file")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Extract and parse only, with no API calls")] = False,
) -> None:
    """Translate the subtitles of one MKV file to Brazilian Portuguese."""
    try:
        tracks = mkv.inspect(file)
        if list_tracks:
            for t in tracks:
                console.print(escape(str(t)))
            if not tracks:
                console.print("[yellow]No subtitle tracks found.[/yellow]")
            return

        if track is not None:
            chosen = next((t for t in tracks if t.id == track), None)
            if chosen is None:
                raise SubtransError(f"No subtitle track with id {track}. Run with --list.")
        else:
            chosen, reason = mkv.pick_source(tracks, ocr.available_kinds())
            if chosen is None:
                raise SubtransError(f"No track to auto-pick: {reason}. Pass --track to choose one.")

        if dry_run:
            _, events = load_cues(file, chosen, console)
            console.print(f"[green]Cues:[/green] {len(events[:limit])}")
            return

        with make_client(get_api_key()) as client:
            preflight(client, model)
            translate_file(
                file, chosen, client, model, console, out=out, batch_size=batch_size, limit=limit, force=force
            )
    except SubtransError as e:
        console.print(f"[red]Error:[/red] {escape(str(e))}")
        if isinstance(e, TransientError):
            console.print("[yellow]Progress is saved. Run the same command again to resume.[/yellow]")
        raise typer.Exit(1) from e
