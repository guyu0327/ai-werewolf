"""语音输入模块：签名 URL 确定性、wpgs 合并、凭证往返、WS 中继集成、用量计数。"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import re
import threading
from datetime import date, datetime, timezone
from urllib.parse import parse_qs, urlparse

import pytest
import websockets
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.voice import credentials as cred_mod
from app.voice.credentials import VoiceConfigError
from app.voice.iat import build_ws_url, merge_wpgs
from app.voice.usage import VoiceUsage
from app.routes import voice as voice_routes


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AT_WEREWOLF_DATA", str(tmp_path))
    monkeypatch.setenv("AT_WEREWOLF_TOKEN", "test-token")
    from app.main import create_app

    return TestClient(create_app()), tmp_path


# ---------- 签名 URL ----------


def test_build_ws_url_deterministic():
    now = datetime(2026, 10, 14, 12, 34, 56, tzinfo=timezone.utc)
    url = build_ws_url("api_key_1", "api_secret_2", now=now)
    assert url.startswith("wss://iat-api.xfyun.cn/v2/iat?")
    q = parse_qs(urlparse(url).query)
    assert q["host"] == ["iat-api.xfyun.cn"]
    assert q["date"] == ["Wed, 14 Oct 2026 12:34:56 GMT"]
    auth = base64.b64decode(q["authorization"][0]).decode()
    assert 'api_key="api_key_1"' in auth
    assert 'algorithm="hmac-sha256"' in auth
    assert 'headers="host date request-line"' in auth
    sig = re.search(r'signature="([^"]+)"', auth).group(1)
    origin = "host: iat-api.xfyun.cn\ndate: Wed, 14 Oct 2026 12:34:56 GMT\nGET /v2/iat HTTP/1.1"
    expect = base64.b64encode(hmac.new(b"api_secret_2", origin.encode(), hashlib.sha256).digest()).decode()
    assert sig == expect


# ---------- wpgs 合并 ----------


def test_merge_wpgs():
    assert merge_wpgs("", "今天", "apd") == "今天"
    assert merge_wpgs("今天天气", "很好", "apd") == "今天天气很好"
    # rpl+rg：把 [2,6) 区间替换为修正段
    assert merge_wpgs("今天天气很好", "天气不错", "rpl", [2, 6]) == "今天天气不错"
    # 无 pgs 标记按普通增量追加
    assert merge_wpgs("abc", "d") == "abcd"


# ---------- 凭证往返 ----------


def test_credentials_roundtrip(monkeypatch):
    key = bytes(range(32))
    nonce = bytes(range(12))
    blob = AESGCM(key).encrypt(nonce, "A1\nK2\nS3".encode("utf-8"), None)
    monkeypatch.setattr(cred_mod, "_BLOB", blob.hex())
    monkeypatch.setattr(cred_mod, "_NONCE", nonce.hex())
    monkeypatch.setattr(cred_mod, "_KEY_PART1", key[:16].hex())

    assert cred_mod.load_credentials(key[16:]) == ("A1", "K2", "S3")

    # 后半密钥不对 → 解密失败
    with pytest.raises(VoiceConfigError):
        cred_mod.load_credentials(bytes(16))

    # 密文损坏 → 解密失败
    monkeypatch.setattr(cred_mod, "_BLOB", "00")
    with pytest.raises(VoiceConfigError):
        cred_mod.load_credentials(key[16:])


# ---------- 用量计数 ----------


def test_usage_cross_day(tmp_path):
    u = VoiceUsage(tmp_path)
    (tmp_path / "voice_usage.json").write_text(json.dumps({"2020-01-01": 500}), encoding="utf-8")
    assert u.used() == 0  # 历史日期不计入今天
    u.increment()
    u.increment()
    assert u.used() == 2
    assert json.loads((tmp_path / "voice_usage.json").read_text("utf-8"))[date.today().isoformat()] == 2


def test_usage_endpoint(client):
    tc, tmp = client
    (tmp / "voice_usage.json").write_text(json.dumps({date.today().isoformat(): 7}), encoding="utf-8")
    r = tc.get("/api/voice/usage", headers={"X-AT-Token": "test-token"})
    assert r.status_code == 200
    assert r.json() == {"used": 7, "limit": 500}


# ---------- 本地假讯飞 WS 中继 ----------


class FakeXfyun:
    """本地假讯飞服务：校验首帧参数，回放 apd → rpl 修正序列，status==2 收口。"""

    def __init__(self, mode: str = "ok"):
        self.mode = mode
        self.frames: list[dict] = []
        self.port: int | None = None
        self._ready = threading.Event()

    async def handler(self, ws):
        async for raw in ws:
            msg = json.loads(raw)
            self.frames.append(msg)
            if self.mode == "error":
                await ws.send(json.dumps({"code": 11200, "message": "license 打开失败"}))
                return
            if "common" in msg:
                await ws.send(json.dumps(_result(1, [("我觉得",), ("三号",)], pgs="apd")))
            elif msg.get("data", {}).get("status") == 2:
                # 最终帧前给一次 rpl 修正：把 [3,5) 区间（「三号」）改为「12号」
                await ws.send(json.dumps(_result(1, [("12号",)], pgs="rpl", rg=[3, 5])))
                await ws.send(json.dumps(_result(2, [], pgs="apd")))
                return

    def start(self):
        loop = asyncio.new_event_loop()

        async def run():
            server = await websockets.serve(self.handler, "127.0.0.1", 0)
            self.port = server.sockets[0].getsockname()[1]
            self._ready.set()
            async with server:
                await server.wait_closed()

        th = threading.Thread(target=lambda: loop.run_until_complete(run()), daemon=True)
        th.start()
        assert self._ready.wait(5)


def _result(status: int, words: tuple, pgs: str | None = None, rg: list | None = None) -> dict:
    result: dict = {"ws": [{"cw": [{"w": w}]} for group in words for w in group]}
    if pgs:
        result["pgs"] = pgs
    if rg:
        result["rg"] = rg
    return {"code": 0, "data": {"status": status, "result": result}}


@pytest.fixture()
def fake_xfyun():
    fake = FakeXfyun()
    fake.start()
    yield fake


def _patch_xfyun(monkeypatch, fake: FakeXfyun) -> None:
    monkeypatch.setattr(voice_routes, "build_ws_url", lambda *a, **k: f"ws://127.0.0.1:{fake.port}")
    monkeypatch.setattr(voice_routes, "voice_credentials", lambda: ("aid", "key", "sec"))


def test_relay_end_to_end(client, fake_xfyun, monkeypatch):
    tc, tmp = client
    _patch_xfyun(monkeypatch, fake_xfyun)

    with tc.websocket_connect("/ws/voice?token=test-token") as ws:
        ws.send_bytes(b"\x01\x00" * 640)  # 一帧 1280B PCM
        ws.send_text(json.dumps({"type": "stop"}))
        msgs = []
        while True:
            m = json.loads(ws.receive_text())
            msgs.append(m)
            if m["type"] in ("final", "error"):
                break

    assert msgs[-1] == {"type": "final", "text": "我觉得12号"}
    partials = [m for m in msgs if m["type"] == "partial"]
    assert partials and partials[0]["text"] == "我觉得三号"

    first = fake_xfyun.frames[0]
    assert first["common"]["app_id"] == "aid"
    assert first["business"]["engine_type"] == "sms16k"
    assert first["business"]["dwa"] == "wpgs"
    audio = [f for f in fake_xfyun.frames if f.get("data", {}).get("audio")]
    assert base64.b64decode(audio[0]["data"]["audio"]) == b"\x01\x00" * 640
    assert any(f["data"]["status"] == 2 for f in fake_xfyun.frames)
    assert VoiceUsage(tmp).used() == 1  # 成功会话才计数


def test_relay_xfyun_error_no_count(client, monkeypatch):
    fake = FakeXfyun(mode="error")
    fake.start()
    tc, tmp = client
    _patch_xfyun(monkeypatch, fake)

    with pytest.raises(WebSocketDisconnect) as ei:
        with tc.websocket_connect("/ws/voice?token=test-token") as ws:
            ws.send_bytes(b"\x01\x00" * 640)
            while True:
                m = json.loads(ws.receive_text())
                if m["type"] == "error":
                    assert "11200" in m["message"] or "license" in m["message"]
                    break
            ws.receive_text()  # 消费 close 帧 → 抛出
    assert ei.value.code == 1011
    assert VoiceUsage(tmp).used() == 0


# ---------- 端点守卫 ----------


def test_ws_bad_token_4401(client):
    tc, _ = client
    with pytest.raises(WebSocketDisconnect) as ei:
        with tc.websocket_connect("/ws/voice?token=wrong") as ws:
            ws.receive_text()
    assert ei.value.code == 4401


def test_ws_usage_limit_4429(client):
    tc, tmp = client
    (tmp / "voice_usage.json").write_text(json.dumps({date.today().isoformat(): 500}), encoding="utf-8")
    with pytest.raises(WebSocketDisconnect) as ei:
        with tc.websocket_connect("/ws/voice?token=test-token") as ws:
            m = json.loads(ws.receive_text())
            assert m["type"] == "error"
            ws.receive_text()
    assert ei.value.code == 4429


def test_ws_unconfigured_credentials(client, monkeypatch):
    """凭证缺失（VoiceConfigError）→ 报「语音服务未配置」而非崩溃。"""
    tc, tmp = client

    def _missing():
        raise VoiceConfigError("语音服务未配置")

    monkeypatch.setattr(voice_routes, "voice_credentials", _missing)
    with pytest.raises(WebSocketDisconnect) as ei:
        with tc.websocket_connect("/ws/voice?token=test-token") as ws:
            m = json.loads(ws.receive_text())
            assert m["type"] == "error"
            assert "未配置" in m["message"]
            ws.receive_text()  # 消费 close 帧 → 抛出
    assert ei.value.code == 1011
    assert VoiceUsage(tmp).used() == 0
