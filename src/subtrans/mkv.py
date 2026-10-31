from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .errors import FatalError, SubtransError

Kind = Literal["text", "pgs", "vobsub"]

ENGLISH = {"eng", "en"}
PORTUGUESE = {"por", "pt"}
KIND_RANK = {"text": 0, "pgs": 1, "vobsub": 2}


@dataclass
class Track:
    id: int
    codec: str
    language: str
    name: str
    kind: Kind
    forced: bool = False

    @property
    def is_bitmap(self) -> bool:
        return self.kind != "text"

    @property
    def ext(self) -> str:
        if self.kind == "pgs":
            return "sup"
        if self.kind == "vobsub":
            return "sub"
        if any(k in self.codec for k in ("ASS", "SSA", "SubStation")):
            return "ass"
        return "srt"

    def __str__(self) -> str:
        forced = " forced" if self.forced else ""
        return f"[{self.id}] {self.kind:6} {self.codec:20} lang={self.language:4}{forced} name={self.name!r}"


def _classify(codec: str) -> Kind | None:
    if codec in ("SubRip/SRT", "SubStationAlpha") or any(k in codec for k in ("UTF8", "ASS", "SSA")):
        return "text"
    if "PGS" in codec:
        return "pgs"
    if "VOBSUB" in codec.upper():
        return "vobsub"
    return None


def _run(args: list[str]) -> str:
    if shutil.which(args[0]) is None:
        raise FatalError(f"`{args[0]}` not found. Install MKVToolNix.")
    result = subprocess.run(args, capture_output=True, text=True)
    # MKVToolNix exits 1 for warnings only; 2 is an error.
    if result.returncode >= 2:
        raise SubtransError(f"{args[0]} failed: {(result.stderr or result.stdout).strip()}")
    return result.stdout


def inspect(path: Path) -> list[Track]:
    # An absolute path keeps a name such as "-x.mkv" or "@x.mkv" from reading as an option.
    data = json.loads(_run(["mkvmerge", "-J", str(path.absolute())]))
    tracks: list[Track] = []
    for t in data.get("tracks", []):
        if t.get("type") != "subtitles":
            continue
        props = t.get("properties", {})
        codec = props.get("codec_id") or t.get("codec", "")
        kind = _classify(codec)
        if kind is None:
            continue
        tracks.append(
            Track(
                id=t["id"],
                codec=codec,
                language=props.get("language", "und"),
                name=props.get("track_name", ""),
                kind=kind,
                forced=bool(props.get("forced_track")),
            )
        )
    return tracks


def pick_source(tracks: list[Track], kinds: set[str]) -> tuple[Track | None, str]:
    """Return the English track to translate, or None and the reason to skip."""
    if any(t.language in PORTUGUESE and not t.forced for t in tracks):
        return None, "has a Portuguese subtitle track"
    # A forced track holds only the foreign-language lines, not the full dialogue.
    english = [t for t in tracks if t.language in ENGLISH and not t.forced]
    if not english and all(t.language == "und" for t in tracks):
        # A rip with no language tags at all is almost always English.
        english = [t for t in tracks if not t.forced]
    if not english:
        return None, "no full English subtitle track"
    usable = [t for t in english if t.kind in kinds]
    if not usable:
        found = ", ".join(sorted({t.kind for t in english}))
        return None, f"no English track this host can read (found: {found})"
    return min(usable, key=lambda t: (KIND_RANK[t.kind], t.id)), ""


def extract(path: Path, track: Track, out_path: Path) -> Path:
    """Extract the track and return the file to parse or OCR next."""
    if track.kind == "vobsub":
        # mkvextract writes <base>.idx and <base>.sub for VobSub.
        base = out_path.with_suffix("")
        _run(["mkvextract", "tracks", str(path.absolute()), f"{track.id}:{base}"])
        idx = base.with_suffix(".idx")
        if not idx.exists():
            raise SubtransError(f"mkvextract did not produce {idx}")
        return idx
    _run(["mkvextract", "tracks", str(path.absolute()), f"{track.id}:{out_path}"])
    return out_path
