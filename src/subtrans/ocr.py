from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from babelfish import Language, LanguageConvertError, LanguageReverseError

from .errors import SubtransError

TESSDATA_DIRS = (
    "/usr/share/tesseract-ocr/5/tessdata",
    "/usr/share/tesseract-ocr/4.00/tessdata",
    "/usr/share/tessdata",
    "/opt/homebrew/share/tessdata",
    "/usr/local/share/tessdata",
)

# Tesseract reads the ♪ glyph as "#". Real "#" at the edge of a subtitle line is rare.
_MUSIC_NOTE = re.compile(r"^#(?= |$)|(?<= )#$", re.MULTILINE)


def _language(mkv_lang: str) -> Language:
    """Parse an MKV language code. "und" (undefined) is read as English."""
    if not mkv_lang or mkv_lang == "und":
        return Language("eng")
    try:
        if len(mkv_lang) == 2:
            return Language.fromalpha2(mkv_lang)
        return Language.fromalpha3b(mkv_lang)
    except LanguageReverseError:
        pass
    try:
        # Some muxers write the terminological code ("fra") where MKV expects "fre".
        return Language(mkv_lang)
    except ValueError as e:
        raise SubtransError(f"Unknown subtitle language code {mkv_lang!r}") from e


def _tesseract_lang(mkv_lang: str) -> str:
    code = _language(mkv_lang).alpha3
    return "chi_sim" if code == "zho" else code


def _pgsrip() -> str | None:
    # Next to the interpreter first, so a venv works when it is not on PATH.
    local = Path(sys.executable).with_name("pgsrip")
    return str(local) if local.exists() else shutil.which("pgsrip")


def available_kinds() -> set[str]:
    kinds = {"text"}
    if _pgsrip() and shutil.which("tesseract"):
        kinds.add("pgs")
    if shutil.which("vobsub2srt"):
        kinds.add("vobsub")
    return kinds


def _run(cmd: list[str], env: dict[str, str] | None = None) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if result.returncode != 0:
        raise SubtransError(f"{Path(cmd[0]).name} failed: {(result.stderr or result.stdout).strip()}")


def _fix_music_notes(srt: Path) -> None:
    text = srt.read_text(encoding="utf-8")
    fixed = _MUSIC_NOTE.sub("♪", text)
    if fixed != text:
        srt.write_text(fixed, encoding="utf-8")


def ocr_pgs(sup_path: Path, lang: str) -> Path:
    """OCR a .sup file with pgsrip and return the produced .srt."""
    pgsrip = _pgsrip()
    if pgsrip is None or shutil.which("tesseract") is None:
        raise SubtransError("PGS OCR needs pgsrip (a project dependency) and tesseract.")
    language = _language(lang)
    try:
        ietf = language.alpha2
    except LanguageConvertError as e:
        raise SubtransError(f"pgsrip has no two-letter code for {lang!r}") from e
    # pgsrip ignores a .sup whose stem has no language tag.
    tagged = sup_path.with_name(f"{sup_path.stem}.{ietf}{sup_path.suffix}")
    shutil.copy2(sup_path, tagged)
    env = os.environ.copy()
    if "TESSDATA_PREFIX" not in env:
        # Without it, pgsrip logs "Tesseract data: None" and produces empty output.
        tessdata = next((d for d in TESSDATA_DIRS if Path(d).is_dir()), None)
        if tessdata:
            env["TESSDATA_PREFIX"] = tessdata
    _run([pgsrip, "-l", ietf, "-f", str(tagged)], env=env)
    produced = sorted(tagged.parent.glob(f"{tagged.stem}*.srt"))
    if not produced:
        raise SubtransError(f"pgsrip produced no .srt for {tagged.name}")
    _fix_music_notes(produced[0])
    return produced[0]


def ocr_vobsub(idx_path: Path, lang: str) -> Path:
    """OCR a VobSub .idx (with its .sub sibling) with vobsub2srt and return the .srt."""
    if shutil.which("vobsub2srt") is None:
        raise SubtransError("VobSub OCR needs vobsub2srt: build it from https://github.com/ruediger/VobSub2SRT")
    base = idx_path.with_suffix("")
    _run(["vobsub2srt", "--tesseract-lang", _tesseract_lang(lang), str(base)])
    srt = base.with_suffix(".srt")
    if not srt.exists():
        raise SubtransError(f"vobsub2srt produced no .srt for {idx_path.name}")
    _fix_music_notes(srt)
    return srt
