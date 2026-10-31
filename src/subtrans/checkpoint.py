from __future__ import annotations

import json
from pathlib import Path


class Checkpoint:
    """Translated and manual cues of one output file, saved after each batch."""

    def __init__(self, path: Path, total_cues: int):
        self.path = path
        self.total_cues = total_cues
        self.translated: dict[int, str] = {}
        self.manual: list[dict] = []
        self._manual_ids: set[int] = set()
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        # A different cue count means the source track changed, so start over.
        if data.get("total_cues") != self.total_cues:
            return
        self.translated = {int(k): v for k, v in data.get("translated", {}).items()}
        self.manual = list(data.get("manual", []))
        self._manual_ids = {int(m["id"]) for m in self.manual}

    def save(self) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp")
        payload = {
            "total_cues": self.total_cues,
            "translated": {str(k): v for k, v in self.translated.items()},
            "manual": self.manual,
        }
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def update(self, batch: dict[int, str]) -> None:
        self.translated.update(batch)
        self.save()

    def mark_manual(self, cue_id: int, original: str, error: str) -> None:
        if cue_id in self._manual_ids:
            return
        self.manual.append({"id": cue_id, "original": original, "error": error})
        self._manual_ids.add(cue_id)
        self.save()

    def cleanup(self) -> None:
        self.path.unlink(missing_ok=True)

    def has(self, cue_id: int) -> bool:
        return cue_id in self.translated or cue_id in self._manual_ids
