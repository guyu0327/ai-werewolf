"""本地 JSON 持久化：settings.json + games/{id}.json，写入采用 tmp+rename 原子替换。"""
from __future__ import annotations

import json
import os
from pathlib import Path

from ..models import Game, Settings

DEFAULT_SETTINGS = Settings()


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.games_dir = self.root / "games"
        self.games_dir.mkdir(parents=True, exist_ok=True)
        self._settings_file = self.root / "settings.json"

    # ---------- settings ----------

    def load_settings(self) -> Settings:
        try:
            data = json.loads(self._settings_file.read_text("utf-8"))
            return Settings.model_validate(data)
        except FileNotFoundError:
            return DEFAULT_SETTINGS.model_copy(deep=True)
        except Exception:
            # 配置损坏时不覆盖用户文件，先按空配置运行
            return DEFAULT_SETTINGS.model_copy(deep=True)

    def save_settings(self, settings: Settings) -> None:
        self._atomic_write(self._settings_file, settings.model_dump_json(indent=2))

    # ---------- games ----------

    def _game_file(self, game_id: str) -> Path:
        safe = "".join(c for c in game_id if c.isalnum() or c in "-_")
        return self.games_dir / f"{safe}.json"

    def save_game(self, game: Game) -> None:
        self._atomic_write(self._game_file(game.id), game.model_dump_json())

    def get_game(self, game_id: str) -> Game | None:
        f = self._game_file(game_id)
        try:
            return Game.model_validate(json.loads(f.read_text("utf-8")))
        except FileNotFoundError:
            return None

    def delete_game(self, game_id: str) -> bool:
        f = self._game_file(game_id)
        if f.exists():
            f.unlink()
            return True
        return False

    def list_game_meta(self) -> list[dict]:
        metas = []
        for f in self.games_dir.glob("*.json"):
            try:
                g = Game.model_validate(json.loads(f.read_text("utf-8")))
            except Exception:
                continue
            metas.append(
                {
                    "id": g.id,
                    "created_at": g.created_at,
                    "board_name": g.board.name,
                    "day": g.day,
                    "phase": g.phase.value,
                    "mode": g.mode,
                    "winner": g.winner,
                    "end_reason": g.end_reason,
                    "players": [
                        {"seat": p.seat, "name": p.name, "avatar": p.avatar, "is_human": p.is_human, "alive": p.alive}
                        for p in sorted(g.players, key=lambda x: x.seat)
                    ],
                }
            )
        metas.sort(key=lambda m: m["created_at"], reverse=True)
        return metas

    # ---------- io ----------

    def _atomic_write(self, file: Path, content: str) -> None:
        tmp = file.with_suffix(file.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, file)
