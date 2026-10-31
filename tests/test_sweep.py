import os
from pathlib import Path

import pytest
import typer

from subtrans import sweep
from subtrans.mkv import Track
from subtrans.translate import TransientError

from .helpers import track


def _old(path: Path) -> None:
    os.utime(path, (0, 0))


def test_sidecar_detected(tmp_path: Path) -> None:
    video = tmp_path / "Movie (2008).mkv"
    video.touch()
    assert not sweep.has_pt_sidecar(video)
    (tmp_path / "Movie (2008).pt-BR.srt").touch()
    assert sweep.has_pt_sidecar(video)


def test_sidecar_other_tags_and_case(tmp_path: Path) -> None:
    video = tmp_path / "Show S01E01.mkv"
    video.touch()
    (tmp_path / "Show S01E01.POR.ass").touch()
    assert sweep.has_pt_sidecar(video)


def test_forced_sidecar_does_not_count(tmp_path: Path) -> None:
    video = tmp_path / "Movie.mkv"
    video.touch()
    (tmp_path / "Movie.pt-BR.forced.srt").touch()
    assert not sweep.has_pt_sidecar(video)


def test_forced_in_the_title_is_not_a_forced_sidecar(tmp_path: Path) -> None:
    video = tmp_path / "The.Forced.March.2020.mkv"
    video.touch()
    (tmp_path / "The.Forced.March.2020.pt-BR.srt").touch()
    assert sweep.has_pt_sidecar(video)


def test_tool_files_are_not_sidecars(tmp_path: Path) -> None:
    video = tmp_path / "Movie.mkv"
    video.touch()
    for name in (
        "Movie.pt-BR.srt.progress.json",
        "Movie.pt-BR.srt.tmp",
        "Movie.sample10.pt-BR.srt",
        "Movie.en.srt",
        "Movie Extras.pt-BR.srt",
    ):
        (tmp_path / name).touch()
    assert not sweep.has_pt_sidecar(video)


def test_find_videos_skips_hidden_and_directories(tmp_path: Path) -> None:
    (tmp_path / "A").mkdir()
    (tmp_path / "A" / "a.MKV").touch()
    (tmp_path / "A" / "._a.mkv").touch()
    (tmp_path / ".Trash").mkdir()
    (tmp_path / ".Trash" / "b.mkv").touch()
    (tmp_path / "dir.mkv").mkdir()
    assert [p.name for p in sweep.find_videos([tmp_path])] == ["a.MKV"]


def _library(tmp_path: Path, names: list[str]) -> None:
    for name in names:
        (tmp_path / name).mkdir()
        video = tmp_path / name / f"{name}.mkv"
        video.touch()
        _old(video)


def test_vanished_directory_does_not_abort_sweep(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _library(tmp_path, ["A", "B"])
    inspected: list[str] = []

    def fake_sidecar(video: Path) -> bool:
        if video.stem == "A":
            raise FileNotFoundError(video.parent)
        return False

    def fake_inspect(video: Path) -> list[Track]:
        inspected.append(video.stem)
        return [track(1, "eng")]

    monkeypatch.setattr(sweep, "has_pt_sidecar", fake_sidecar)
    monkeypatch.setattr(sweep.mkv, "inspect", fake_inspect)
    with pytest.raises(typer.Exit) as exc:
        sweep.main(roots=[tmp_path], model="m", dry_run=True)
    assert exc.value.exit_code == 1
    assert inspected == ["B"]


def test_recent_file_waits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "new.mkv").touch()
    monkeypatch.setattr(sweep.mkv, "inspect", lambda video: pytest.fail("inspected a file still being written"))
    sweep.main(roots=[tmp_path], model="m", dry_run=True)


def test_provider_outage_stops_the_sweep(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _library(tmp_path, ["A", "B"])
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setattr(sweep, "preflight", lambda client, model: None)
    monkeypatch.setattr(sweep.mkv, "inspect", lambda video: [track(1, "eng")])
    attempted: list[str] = []

    def outage(video: Path, *args, **kwargs) -> Path:
        attempted.append(video.stem)
        raise TransientError("OpenRouter 503")

    monkeypatch.setattr(sweep, "translate_file", outage)
    with pytest.raises(typer.Exit) as exc:
        sweep.main(roots=[tmp_path], model="m", dry_run=False)
    assert exc.value.exit_code == 2
    assert attempted == ["A"]


def test_missing_key_stops_before_any_work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _library(tmp_path, ["A"])
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(sweep.mkv, "inspect", lambda video: pytest.fail("inspected without a key"))
    with pytest.raises(typer.Exit) as exc:
        sweep.main(roots=[tmp_path], model="m", dry_run=False)
    assert exc.value.exit_code == 2
