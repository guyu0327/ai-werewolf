"""语音识别每日用量计数：data/voice_usage.json {"YYYY-MM-DD": n}，跨日自动重置。"""
from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path


class VoiceUsage:
    def __init__(self, root: Path):
        self._file = Path(root) / "voice_usage.json"

    def _load(self) -> dict:
        try:
            data = json.loads(self._file.read_text("utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def used(self, day: str | None = None) -> int:
        return int(self._load().get(day or date.today().isoformat(), 0))

    def increment(self) -> None:
        day = date.today().isoformat()
        data = self._load()
        data[day] = self.used(day) + 1
        tmp = self._file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, self._file)
