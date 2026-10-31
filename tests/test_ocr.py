from pathlib import Path

import pytest

from subtrans import ocr
from subtrans.errors import SubtransError


@pytest.mark.parametrize(
    ("mkv_lang", "tesseract"),
    [("eng", "eng"), ("en", "eng"), ("und", "eng"), ("fre", "fra"), ("fra", "fra"), ("ger", "deu"), ("chi", "chi_sim")],
)
def test_tesseract_lang(mkv_lang: str, tesseract: str) -> None:
    assert ocr._tesseract_lang(mkv_lang) == tesseract


def test_unknown_language_is_an_error() -> None:
    with pytest.raises(SubtransError):
        ocr._language("zzz")


def test_music_notes(tmp_path: Path) -> None:
    srt = tmp_path / "a.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:02,000\n# Singing in the rain #\n#\nRoom # 5\n", encoding="utf-8")
    ocr._fix_music_notes(srt)
    assert srt.read_text(encoding="utf-8") == "1\n00:00:01,000 --> 00:00:02,000\n♪ Singing in the rain ♪\n♪\nRoom # 5\n"
