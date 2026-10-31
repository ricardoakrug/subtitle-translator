from __future__ import annotations

from pathlib import Path

import pysubs2

from .errors import SubtransError

OUTPUT_FORMATS = {".srt": "srt", ".ass": "ass", ".ssa": "ssa", ".vtt": "vtt"}


def load(path: Path) -> pysubs2.SSAFile:
    try:
        return pysubs2.load(str(path))
    except pysubs2.exceptions.Pysubs2Error as e:
        raise SubtransError(f"Cannot parse the extracted subtitle: {e}") from e


def format_for(out_path: Path) -> str:
    fmt = OUTPUT_FORMATS.get(out_path.suffix.lower())
    if fmt is None:
        raise SubtransError(f"Output must end in {', '.join(OUTPUT_FORMATS)}, not {out_path.suffix!r}")
    return fmt


def dialog_events(subs: pysubs2.SSAFile) -> list[pysubs2.SSAEvent]:
    return [e for e in subs.events if e.type == "Dialogue"]


def save(subs: pysubs2.SSAFile, out_path: Path) -> None:
    fmt = format_for(out_path)
    # Write, then rename: a truncated sidecar would make the sweep skip the file for good.
    tmp = out_path.with_name(out_path.name + ".tmp")
    subs.save(str(tmp), format_=fmt, encoding="utf-8")
    tmp.replace(out_path)
