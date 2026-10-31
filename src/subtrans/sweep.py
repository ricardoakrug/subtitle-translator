from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.markup import escape

from . import mkv, ocr
from .errors import FatalError
from .pipeline import TARGET_TAG, translate_file
from .translate import DEFAULT_MODEL, TransientError, get_api_key, make_client, preflight

# Lowercase sidecar tags that mean a full Portuguese subtitle already sits next to the video.
PT_SIDECAR_TAGS = (TARGET_TAG.lower(), "pt", "por", "pob")
SUB_EXTS = {".srt", ".ass", ".ssa"}
# A copy import is not atomic. Wait until the file has been still for this long.
MIN_AGE_SECONDS = 600

app = typer.Typer(add_completion=False, pretty_exceptions_show_locals=False)
console = Console()


def has_pt_sidecar(video: Path) -> bool:
    stem = video.stem.lower()
    prefixes = tuple(f"{stem}.{tag}." for tag in PT_SIDECAR_TAGS)
    for p in video.parent.iterdir():
        name = p.name.lower()
        # A forced sidecar holds only the foreign-language lines.
        if name.startswith(prefixes) and p.suffix.lower() in SUB_EXTS and ".forced." not in name[len(stem) :]:
            return True
    return False


def find_videos(roots: list[Path]) -> Iterator[Path]:
    for root in roots:
        for p in sorted(root.rglob("*")):
            # Hidden paths include macOS "._x.mkv" resource forks and trash folders.
            hidden = any(part.startswith(".") for part in p.relative_to(root).parts)
            if p.suffix.lower() == ".mkv" and not hidden and p.is_file():
                yield p


@app.command()
def main(
    roots: Annotated[list[Path], typer.Argument(exists=True, file_okay=False, help="Library directories to walk")],
    model: Annotated[str, typer.Option("--model", "-m", help="OpenRouter model slug")] = DEFAULT_MODEL,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="List the files to translate, with no API calls")] = False,
) -> None:
    """Translate each MKV under the given directories that has no Portuguese subtitle yet."""
    client = None
    try:
        if not dry_run:
            client = make_client(get_api_key())
            preflight(client, model)
        kinds = ocr.available_kinds()
        failed = 0
        for video in find_videos(roots):
            label = escape(str(video))
            try:
                if has_pt_sidecar(video):
                    continue
                if time.time() - video.stat().st_mtime < MIN_AGE_SECONDS:
                    console.print(f"[dim]Skip {label}: modified less than {MIN_AGE_SECONDS // 60} min ago[/dim]")
                    continue
                source, reason = mkv.pick_source(mkv.inspect(video), kinds)
                if source is None:
                    console.print(f"[dim]Skip {label}: {escape(reason)}[/dim]")
                    continue
                console.print(f"[bold]Translate {label}[/bold] (track {source.id})")
                if client is not None:
                    translate_file(video, source, client, model, console)
            except (FatalError, TransientError):
                raise
            except Exception as e:  # One malformed file must not stop the whole library.
                console.print(f"[red]Failed {label}:[/red] {escape(str(e))}")
                failed += 1
    except (FatalError, TransientError) as e:
        # A provider outage would fail every remaining file after its extraction and OCR.
        console.print(f"[red]Stopped:[/red] {escape(str(e))}")
        raise typer.Exit(2) from e
    finally:
        if client is not None:
            client.close()
    if failed:
        console.print(f"[red]{failed} file(s) failed. The next sweep retries them.[/red]")
        raise typer.Exit(1)
