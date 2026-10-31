from __future__ import annotations

import json
import tempfile
from pathlib import Path

import httpx
import pysubs2
from rich.console import Console
from rich.markup import escape
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
)

from . import mkv, ocr
from . import subs as subsmod
from .checkpoint import Checkpoint
from .errors import SubtransError
from .mkv import Track
from .translate import TranslationError, translate_batch

TARGET_TAG = "pt-BR"
DEFAULT_BATCH_SIZE = 40
MANUAL_MARKER = "«MANUAL» "
# Above this share of refused cues the model, not the content, is at fault.
MAX_MANUAL_SHARE = 0.1


def output_path(video: Path, track: Track, limit: int | None = None) -> Path:
    # OCR drops styling, so bitmap tracks always become .srt.
    ext = "srt" if track.is_bitmap else track.ext
    # A sample run gets its own name, so nothing mistakes it for a full translation.
    tag = f"sample{limit}.{TARGET_TAG}" if limit else TARGET_TAG
    return video.with_suffix(f".{tag}.{ext}")


def load_cues(video: Path, track: Track, console: Console) -> tuple[pysubs2.SSAFile, list[pysubs2.SSAEvent]]:
    with tempfile.TemporaryDirectory() as tmp:
        console.print("[cyan]Extracting subtitle track...[/cyan]")
        produced = mkv.extract(video, track, Path(tmp) / f"track.{track.ext}")
        if track.kind == "pgs":
            console.print("[cyan]OCR (PGS via pgsrip)...[/cyan]")
            produced = ocr.ocr_pgs(produced, track.language)
        elif track.kind == "vobsub":
            console.print("[cyan]OCR (VobSub via vobsub2srt)...[/cyan]")
            produced = ocr.ocr_vobsub(produced, track.language)
        ssa = subsmod.load(produced)
    return ssa, subsmod.dialog_events(ssa)


def _translate_with_bisect(
    client: httpx.Client,
    model: str,
    cues: list[tuple[int, str]],
    cp: Checkpoint,
    progress: Progress,
    task: TaskID,
) -> None:
    try:
        result = translate_batch(client, model, cues)
    except TranslationError as e:
        if len(cues) == 1:
            cid, text = cues[0]
            progress.console.print(f"[red]Cue {cid} flagged manual:[/red] {escape(str(e))}")
            cp.mark_manual(cid, text, str(e))
            progress.update(task, advance=1)
            return
        progress.console.print(f"[yellow]Batch of {len(cues)} failed, bisecting:[/yellow] {escape(str(e))}")
        mid = len(cues) // 2
        _translate_with_bisect(client, model, cues[:mid], cp, progress, task)
        _translate_with_bisect(client, model, cues[mid:], cp, progress, task)
        return
    cp.update(result)
    progress.update(task, advance=len(cues))


def translate_file(
    video: Path,
    track: Track,
    client: httpx.Client,
    model: str,
    console: Console,
    out: Path | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    limit: int | None = None,
    force: bool = False,
) -> Path:
    """Translate one track of one MKV and return the written subtitle path."""
    out_path = out or output_path(video, track, limit)
    progress_path = out_path.with_name(out_path.name + ".progress.json")
    if out_path.exists() and not progress_path.exists() and not force:
        raise SubtransError(f"{out_path} already exists. Pass --force to overwrite it.")
    subsmod.format_for(out_path)
    console.print(f"[green]Track:[/green] {escape(str(track))}")
    console.print(f"[green]Output:[/green] {escape(str(out_path))}")

    ssa, events = load_cues(video, track, console)
    if limit is not None:
        events = events[:limit]
        ssa.events = events
    console.print(f"[green]Cues:[/green] {len(events)}  [green]Model:[/green] {escape(model)}")

    cp = Checkpoint(progress_path, total_cues=len(events))
    if cp.translated:
        console.print(f"[yellow]Resuming from checkpoint: {len(cp.translated)} cues already done.[/yellow]")
    # Cues with no visible text (styling only) stay as they are.
    text_ids = [i for i, e in enumerate(events) if e.plaintext.strip()]
    if not text_ids:
        raise SubtransError("No cue has text. Check the OCR output.")
    pending = [(i, events[i].text) for i in text_ids if not cp.has(i)]

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Translating", total=len(pending))
        for i in range(0, len(pending), batch_size):
            _translate_with_bisect(client, model, pending[i : i + batch_size], cp, progress, task)

    if len(cp.manual) > max(5, len(text_ids) * MAX_MANUAL_SHARE):
        raise SubtransError(
            f"The model refused {len(cp.manual)} of {len(text_ids)} cues, so no subtitle was written. "
            f"Try another --model, or delete {progress_path.name} to retry these cues."
        )

    manual_ids = {m["id"] for m in cp.manual}
    for i, ev in enumerate(events):
        if i in manual_ids:
            ev.text = f"{MANUAL_MARKER}{ev.text}"
        elif i in cp.translated:
            ev.text = cp.translated[i]
    subsmod.save(ssa, out_path)

    manual_path = out_path.with_name(out_path.name + ".manual.json")
    manual_path.unlink(missing_ok=True)
    if cp.manual:
        entries = [
            {
                "id": m["id"],
                "start_ms": events[m["id"]].start,
                "end_ms": events[m["id"]].end,
                "original": m["original"],
                "error": m["error"],
            }
            for m in cp.manual
        ]
        manual_path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
        console.print(f"[yellow]{len(entries)} cues need manual translation:[/yellow] {escape(str(manual_path))}")

    cp.cleanup()
    console.print(f"[bold green]Done:[/bold green] {escape(str(out_path))}")
    return out_path
