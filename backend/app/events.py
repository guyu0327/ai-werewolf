"""SSE hub：每个游戏一个广播组，订阅者各持一个有界队列（队列中为 (event, data) 元组，由消费端格式化与过滤）。"""
from __future__ import annotations

import asyncio
import json


def sse_format(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


class GameHub:
    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue]] = {}

    def subscribe(self, game_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=512)
        self._subs.setdefault(game_id, set()).add(q)
        return q

    def unsubscribe(self, game_id: str, q: asyncio.Queue) -> None:
        group = self._subs.get(game_id)
        if group:
            group.discard(q)
            if not group:
                self._subs.pop(game_id, None)

    def publish(self, game_id: str, event: str, data: dict) -> None:
        for q in list(self._subs.get(game_id, ())):
            try:
                q.put_nowait((event, data))
            except asyncio.QueueFull:
                # 订阅端消费太慢：丢弃最旧的一条，保证后续事件（尤其 phase_change）能送达
                try:
                    q.get_nowait()
                    q.put_nowait((event, data))
                except Exception:
                    pass
