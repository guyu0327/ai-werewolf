"""语音输入：渲染端 PCM 经本端中继到讯飞听写（流式版）。

FastAPI 的 HTTP 中间件不拦截 WebSocket scope，token 由端点自查（env 为空时跳过，
与 HTTP 中间件一致）。凭证只在后端解密使用，永不下发渲染进程。
"""
from __future__ import annotations

import asyncio
import json
import os

from fastapi import APIRouter, Request, WebSocket
from starlette.websockets import WebSocketDisconnect

from ..voice.iat import DAILY_LIMIT, MAX_CONNECTION_SECS, IatSession, build_ws_url, voice_credentials
from ..voice.credentials import VoiceConfigError
from ..voice.usage import VoiceUsage

router = APIRouter(tags=["voice"])


@router.get("/api/voice/usage")
async def voice_usage(request: Request):
    usage = VoiceUsage(request.app.state.store.root)
    return {"used": usage.used(), "limit": DAILY_LIMIT}


@router.websocket("/ws/voice")
async def voice_iat(ws: WebSocket, token: str = ""):
    expected = os.environ.get("AT_WEREWOLF_TOKEN", "").strip()
    if expected and token != expected:
        await ws.close(code=4401)
        return

    await ws.accept()
    usage = VoiceUsage(ws.app.state.store.root)
    if usage.used() >= DAILY_LIMIT:
        await ws.send_json({"type": "error", "message": f"今日语音识别免费次数已用完（{DAILY_LIMIT} 次/日）"})
        await ws.close(code=4429)
        return

    try:
        app_id, api_key, api_secret = voice_credentials()
        url = build_ws_url(api_key, api_secret)
    except VoiceConfigError as exc:
        await ws.send_json({"type": "error", "message": str(exc)})
        await ws.close(code=1011)
        return

    session = IatSession(url, app_id)
    try:
        await session.connect()
    except Exception as exc:
        await ws.send_json({"type": "error", "message": f"无法连接语音服务：{exc}"})
        await ws.close(code=1011)
        return

    async def upstream() -> None:
        # 客户端 → 讯飞：二进制帧喂音频，{"type":"stop"} 收尾
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                await session.finish()
                return
            if msg.get("bytes"):
                await session.feed(msg["bytes"])
            elif msg.get("text"):
                try:
                    if json.loads(msg["text"]).get("type") == "stop":
                        await session.finish()
                        return
                except ValueError:
                    continue

    async def downstream() -> None:
        # 讯飞 → 客户端：partial 增量、最终帧后收口
        deadline = asyncio.get_running_loop().time() + MAX_CONNECTION_SECS
        while not session.done:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                await session.finish()  # 60s 音频上限到点，主动收尾等最终帧
                remaining = 8.0
            try:
                await asyncio.wait_for(session.recv(), timeout=remaining)
            except asyncio.TimeoutError:
                return
            if session.done:
                return
            try:
                await ws.send_json({"type": "partial", "text": session.text})
            except Exception:
                return

    up = asyncio.create_task(upstream())
    down = asyncio.create_task(downstream())
    try:
        await down
    except Exception:
        pass
    up.cancel()
    try:
        await up
    except (asyncio.CancelledError, Exception):
        pass

    if session.error is not None:
        try:
            await ws.send_json({"type": "error", "message": str(session.error)})
        except Exception:
            pass
        await ws.close(code=1011)
        return

    if session.got_final:
        usage.increment()
    try:
        await ws.send_json({"type": "final", "text": session.text})
        await ws.close(code=1000)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
