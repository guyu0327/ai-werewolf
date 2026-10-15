"""讯飞语音听写（流式版）v2：HMAC 签名、wpgs 动态修正合并、单连接会话中继。"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone
from urllib.parse import quote

import websockets

from .credentials import load_credentials

# 密钥后 16 字节，由 scripts/gen_xfyun_blob.py 生成（前半在 credentials.py）
_KEY_PART2 = bytes.fromhex("c7a8cd7b5ebd572217a3d34f6bb53db3")

IAT_HOST = "iat-api.xfyun.cn"
IAT_PATH = "/v2/iat"
AUDIO_FRAME_BYTES = 1280  # 40ms @ 16kHz L16 单声道
MAX_CONNECTION_SECS = 60  # 讯飞单连接音频上限
DAILY_LIMIT = 500  # 免费额度（服务量：一条连接 = 一次）

_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _rfc1123(now: datetime) -> str:
    # 不用 strftime：%a/%b 受本地化环境影响，签名字符集必须恒定
    return (
        f"{_WEEKDAYS[now.weekday()]}, {now.day:02d} {_MONTHS[now.month - 1]} "
        f"{now.year} {now.hour:02d}:{now.minute:02d}:{now.second:02d} GMT"
    )


def build_ws_url(api_key: str, api_secret: str, host: str = IAT_HOST, path: str = IAT_PATH, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    date = _rfc1123(now)
    origin = f"host: {host}\ndate: {date}\nGET {path} HTTP/1.1"
    signature = base64.b64encode(hmac.new(api_secret.encode(), origin.encode(), hashlib.sha256).digest()).decode()
    auth_origin = (
        f'api_key="{api_key}", algorithm="hmac-sha256", '
        f'headers="host date request-line", signature="{signature}"'
    )
    authorization = base64.b64encode(auth_origin.encode()).decode()
    # 手工 urlencode：quote 空格为 %20（ urlencode 会用 +，讯飞同样接受但保持一致性）
    return f"wss://{host}{path}?authorization={quote(authorization, safe='')}&date={quote(date, safe='')}&host={host}"


def merge_wpgs(text: str, seg: str, pgs: str | None = None, rg: list | None = None) -> str:
    """按动态修正协议合并一个结果段：apd 追加 / rpl+rg 替换 [rg[0], rg[1]) 区间 / 无标记直接追加。"""
    if pgs == "apd":
        return text + seg
    if pgs == "rpl" and rg and len(rg) == 2:
        return text[: rg[0]] + seg + text[rg[1] :]
    return text + seg


def voice_credentials() -> tuple[str, str, str]:
    return load_credentials(_KEY_PART2)


class IatSession:
    """一条讯飞听写连接。渲染端音频 feed 进来，recv() 吐合并后的全文；finish() 收尾。"""

    def __init__(self, url: str, app_id: str):
        self._url = url
        self._app_id = app_id
        self._ws = None
        self._send_lock = asyncio.Lock()
        self._buf = b""
        self.finished = False
        self.done = False  # 收到最终帧或连接关闭
        self.got_final = False  # 收到讯飞 status==2（额度消耗成立的判定）
        self.error: Exception | None = None
        self.text = ""

    async def connect(self) -> None:
        self._ws = await asyncio.wait_for(
            websockets.connect(self._url, open_timeout=10, max_size=4 * 1024 * 1024), timeout=12
        )
        await self._send(
            {
                "common": {"app_id": self._app_id},
                "business": {"engine_type": "sms16k", "dwa": "wpgs"},
                "data": {"status": 0, "format": "audio/L16;rate=16000", "encoding": "raw", "audio": ""},
            }
        )

    async def feed(self, pcm: bytes) -> None:
        if self.finished or self._ws is None:
            return
        self._buf += pcm
        while len(self._buf) >= AUDIO_FRAME_BYTES:
            chunk, self._buf = self._buf[:AUDIO_FRAME_BYTES], self._buf[AUDIO_FRAME_BYTES:]
            await self._send_audio(chunk, status=1)

    async def finish(self) -> None:
        if self.finished or self._ws is None:
            return
        self.finished = True
        rest, self._buf = self._buf, b""
        await self._send_audio(rest, status=2)

    async def recv(self) -> None:
        """等待下一条讯飞结果并合并进 text；结束时置 done。"""
        if self.done or self._ws is None:
            return
        try:
            msg = await self._ws.recv()
        except Exception:
            self.done = True
            return
        try:
            data = json.loads(msg)
        except (TypeError, ValueError):
            return
        code = data.get("code", 0)
        if code != 0:
            self.error = RuntimeError(str(data.get("message") or f"讯飞识别错误 {code}"))
            self.done = True
            await self.close()
            return
        payload = data.get("data") or {}
        result = payload.get("result") or {}
        seg = "".join(
            piece.get("w", "") for word in result.get("ws") or [] for piece in word.get("cw") or []
        )
        self.text = merge_wpgs(self.text, seg, result.get("pgs"), result.get("rg"))
        if payload.get("status") == 2:
            self.got_final = True
            self.done = True
            await self.close()

    async def close(self) -> None:
        self.done = True
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass

    async def _send(self, obj: dict) -> None:
        async with self._send_lock:
            await self._ws.send(json.dumps(obj))

    async def _send_audio(self, pcm: bytes, status: int) -> None:
        await self._send(
            {
                "data": {
                    "status": status,
                    "format": "audio/L16;rate=16000",
                    "encoding": "raw",
                    "audio": base64.b64encode(pcm).decode(),
                }
            }
        )
