import io
import json
from pathlib import Path

import pysubs2
import pytest
from rich.console import Console

from subtrans import pipeline
from subtrans.errors import SubtransError
from subtrans.translate import TranslationError

from .helpers import track

QUIET = Console(file=io.StringIO())


def _subs(texts: list[str]) -> pysubs2.SSAFile:
    ssa = pysubs2.SSAFile()
    for i, text in enumerate(texts):
        ssa.events.append(pysubs2.SSAEvent(start=i * 1000, end=i * 1000 + 900, text=text))
    return ssa


@pytest.fixture
def video(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "Movie.mkv"
    path.touch()

    def fake_load(video: Path, track, console):
        ssa = _subs(["Hi.", "Bad cue.", "{\\an8}", "Bye."])
        return ssa, pipeline.subsmod.dialog_events(ssa)

    monkeypatch.setattr(pipeline, "load_cues", fake_load)
    return path


def test_output_path() -> None:
    v = Path("/lib/Movie (2008).mkv")
    assert pipeline.output_path(v, track(1, "eng")).name == "Movie (2008).pt-BR.srt"
    assert pipeline.output_path(v, track(1, "eng", "pgs")).name == "Movie (2008).pt-BR.srt"
    assert pipeline.output_path(v, track(1, "eng"), limit=10).name == "Movie (2008).sample10.pt-BR.srt"


def test_bisect_flags_only_the_bad_cue(video: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    requested: list[list[int]] = []

    def fake_batch(client, model, cues):
        requested.append([cid for cid, _ in cues])
        if any(text == "Bad cue." for _, text in cues):
            raise TranslationError("refused")
        return {cid: text.upper() for cid, text in cues}

    monkeypatch.setattr(pipeline, "translate_batch", fake_batch)
    out = pipeline.translate_file(video, track(1, "eng"), None, "m", QUIET)

    lines = [e.text for e in pysubs2.load(str(out))]
    assert lines == ["HI.", "«MANUAL» Bad cue.", "", "BYE."]
    assert all(2 not in batch for batch in requested), "a styling-only cue reached the model"
    manual = json.loads(out.with_name(out.name + ".manual.json").read_text(encoding="utf-8"))
    assert [m["original"] for m in manual] == ["Bad cue."]
    assert not out.with_name(out.name + ".progress.json").exists()


def test_resume_skips_done_cues(video: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    progress = video.with_name("Movie.pt-BR.srt.progress.json")
    progress.write_text(json.dumps({"total_cues": 4, "translated": {"0": "OI.", "1": "RUIM."}, "manual": []}))
    requested: list[int] = []

    def fake_batch(client, model, cues):
        requested.extend(cid for cid, _ in cues)
        return {cid: "TCHAU." for cid, _ in cues}

    monkeypatch.setattr(pipeline, "translate_batch", fake_batch)
    out = pipeline.translate_file(video, track(1, "eng"), None, "m", QUIET)
    assert requested == [3]
    assert [e.text for e in pysubs2.load(str(out))] == ["OI.", "RUIM.", "", "TCHAU."]


def test_refuses_to_overwrite_an_existing_subtitle(video: Path) -> None:
    video.with_name("Movie.pt-BR.srt").write_text("human translation", encoding="utf-8")
    with pytest.raises(SubtransError, match="--force"):
        pipeline.translate_file(video, track(1, "eng"), None, "m", QUIET)


def test_interrupted_save_leaves_no_sidecar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def crash(self, path: str, **kwargs) -> None:
        Path(path).write_text("1\n00:00:00,000 --> 00:00:01,000\nOl", encoding="utf-8")
        raise OSError("disk full")

    monkeypatch.setattr(pysubs2.SSAFile, "save", crash)
    out = tmp_path / "Movie.pt-BR.srt"
    with pytest.raises(OSError):
        pipeline.subsmod.save(_subs(["Olá"]), out)
    assert not out.exists()


def test_systematic_refusal_writes_no_sidecar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    video = tmp_path / "Movie.mkv"
    video.touch()
    ssa = _subs([f"Line {i}." for i in range(20)])
    monkeypatch.setattr(pipeline, "load_cues", lambda v, t, c: (ssa, pipeline.subsmod.dialog_events(ssa)))

    def refuse(client, model, cues):
        raise TranslationError("no text")

    monkeypatch.setattr(pipeline, "translate_batch", refuse)
    with pytest.raises(SubtransError, match="refused 20 of 20"):
        pipeline.translate_file(video, track(1, "eng"), None, "m", QUIET)
    assert not (tmp_path / "Movie.pt-BR.srt").exists()
    assert (tmp_path / "Movie.pt-BR.srt.progress.json").exists()


def test_blank_ocr_output_writes_no_sidecar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    video = tmp_path / "Movie.mkv"
    video.touch()
    ssa = _subs(["", "  "])
    monkeypatch.setattr(pipeline, "load_cues", lambda v, t, c: (ssa, pipeline.subsmod.dialog_events(ssa)))
    with pytest.raises(SubtransError, match="No cue has text"):
        pipeline.translate_file(video, track(1, "eng", "pgs"), None, "m", QUIET)
    assert not (tmp_path / "Movie.pt-BR.srt").exists()


def test_bad_out_extension_fails_before_any_work(video: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pipeline, "load_cues", lambda *a: pytest.fail("extracted before checking --out"))
    with pytest.raises(SubtransError, match="Output must end in"):
        pipeline.translate_file(video, track(1, "eng"), None, "m", QUIET, out=video.with_suffix(".txt"))


def test_stale_checkpoint_is_ignored(video: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    progress = video.with_name("Movie.pt-BR.srt.progress.json")
    progress.write_text(json.dumps({"total_cues": 3, "translated": {"0": "VELHO."}, "manual": []}))
    requested: list[int] = []

    def fake_batch(client, model, cues):
        requested.extend(cid for cid, _ in cues)
        return {cid: "NOVO." for cid, _ in cues}

    monkeypatch.setattr(pipeline, "translate_batch", fake_batch)
    pipeline.translate_file(video, track(1, "eng"), None, "m", QUIET)
    assert requested == [0, 1, 3]
