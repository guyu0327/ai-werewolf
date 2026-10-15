"""端到端冒烟：对真实运行中的后端（uvicorn）跑一整局 6 人局。

AI 预设指向本地 mock LLM（scripts/mock_llm.py，需先启动），验证成功路径；
真人动作用脚本自动提交。加 --god 跑上帝观战局（全员 AI，无真人输入）。
用法：python scripts/mock_llm.py &  python scripts/smoke_play.py [--god] [base_url]
"""
from __future__ import annotations

import asyncio
import json
import sys

import httpx

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ARGS = [a for a in sys.argv[1:] if a not in ("--god", "--sheriff")]
GOD = "--god" in sys.argv
SHERIFF = "--sheriff" in sys.argv
BASE = ARGS[0] if ARGS else "http://127.0.0.1:8321"


async def main() -> None:
    async with httpx.AsyncClient(base_url=BASE, timeout=60) as c:
        r = await c.get("/api/health")
        r.raise_for_status()
        print("health:", r.json())

        presets = [
            {
                "id": "p1",
                "name": "mock",
                "base_url": "http://127.0.0.1:9100/v1",
                "api_key": "x",
                "model": "mock-model",
                "speech_temperature": 0.9,
                "decision_temperature": 0.3,
            }
        ]
        personas = [
            {"id": f"a{i}", "name": f"AI{i}", "avatar": "🦊", "persona": "冷静的逻辑流玩家", "preset_id": "p1"}
            for i in range(1, 6 if not GOD else 7)
        ]
        r = await c.put("/api/settings", json={"presets": presets, "personas": personas, "boards": []})
        r.raise_for_status()

        if GOD:
            r = await c.post(
                "/api/games",
                json={"board_id": "six", "persona_ids": [p["id"] for p in personas], "mode": "god", "sheriff": SHERIFF},
            )
        else:
            r = await c.post(
                "/api/games",
                json={
                    "board_id": "six",
                    "persona_ids": [p["id"] for p in personas],
                    "human_name": "冒烟玩家",
                    "human_avatar": "🙂",
                    "sheriff": SHERIFF,
                },
            )
        r.raise_for_status()
        state = r.json()
        gid = state["id"]
        human_seat = state["human_seat"]
        print(f"game={gid} mode={state.get('mode')} human_seat={human_seat}")
        if GOD:
            assert human_seat is None, "观战局不应有真人座位"
            assert state.get("mode") == "god"

        r = await c.post(f"/api/games/{gid}/start")
        r.raise_for_status()

        deltas = 0
        vote_casts = 0
        thinking_events = 0
        thinking_starts = 0
        thinking_deltas = 0
        buffer = ""
        finished = False
        async with c.stream("GET", f"/api/games/{gid}/stream?view=auto") as resp:
            async for chunk in resp.aiter_text():
                buffer += chunk
                while "\n\n" in buffer:
                    block, buffer = buffer.split("\n\n", 1)
                    event, data = parse_block(block)
                    if event == "snapshot":
                        print("snapshot: day", data["state"]["day"], "events", len(data["events"]))
                        # 竞态保护：await_input 可能在流连上前发出（如真人开局即狼），快照 pending 需补交
                        if data.get("pending") and not finished:
                            await try_submit(c, gid, data["pending"])
                    elif event == "phase_change":
                        print(f"phase -> {data['phase']} (day {data['day']})")
                    elif event == "await_input":
                        await try_submit(c, gid, data)
                    elif event == "speech_delta":
                        deltas += 1
                    elif event == "vote_cast":
                        vote_casts += 1
                        print(f"  [vote_cast] {data.get('seat')}号 -> {data.get('target')}号")
                    elif event == "thinking":
                        thinking_events += 1
                        print(f"  [thinking] {data.get('seat')}号 ({len(str(data.get('text')))} 字)")
                    elif event == "thinking_start":
                        thinking_starts += 1
                    elif event == "thinking_delta":
                        thinking_deltas += 1
                    elif event in ("speech", "last_words"):
                        who = data.get("actor")
                        print(f"  [{event}] {who}号: {str(data.get('text'))[:40]}")
                    elif event == "vote_result":
                        print("  [vote]", data.get("tie") and "平票" or f"放逐 {data.get('exiled')}号({data.get('role')})")
                    elif event == "night_settle":
                        print("  [night]", str(data.get("text"))[:50])
                    elif event == "game_over":
                        print("game_over:", data.get("text"))
                        finished = True
                    elif event == "error":
                        print("server error:", data)
                    if finished:
                        break
                if finished:
                    break

        r = await c.get(f"/api/games/{gid}/review")
        r.raise_for_status()
        review = r.json()
        roles = {p["seat"]: p["role"] for p in review["players"]}
        print("review roles:", roles)
        print(f"winner={review['winner']} events={len(review['events'])} speech_deltas={deltas}")
        assert finished, "游戏没有走到 game_over"
        assert review["winner"] in ("wolf", "good", "draw")
        assert vote_casts > 0, "没有收到逐个亮票事件"
        if GOD:
            assert thinking_events > 0, "观战局应收到思考过程事件"
        else:
            assert thinking_events == 0, "玩家视角不应收到任何思考过程事件（泄漏）"
            assert thinking_deltas == 0, "玩家视角不应收到思考内容增量（泄漏）"
            assert thinking_starts > 0, "玩家视角应收到思考占位信号（正在思考动画）"
        # 复盘事件应与 SSE 快照同构：payload 字段展平在顶层（否则前端渲染 undefined）
        host_lines = [e for e in review["events"] if e["type"] == "host_line"]
        assert host_lines and all(e.get("text") for e in host_lines), "复盘 host_line 缺顶层 text（事件未展平）"
        vote_results = [e for e in review["events"] if e["type"] == "vote_result"]
        assert vote_results and all("votes" in e for e in vote_results), "复盘 vote_result 缺顶层 votes（事件未展平）"
        think_in_review = [e for e in review["events"] if e["type"] == "thinking"]
        assert think_in_review, "复盘应包含思考过程（双模式均可见）"
        if GOD:
            assert review["mode"] == "god", "复盘 mode 应为 god"
            assert all(p.get("role") for p in review["players"]), "观战局身份应全公开"
        if SHERIFF:
            etypes = {e["type"] for e in review["events"]}
            assert "sheriff_candidates" in etypes and "sheriff_result" in etypes, "警长竞选事件缺失"
            result = next(e for e in review["events"] if e["type"] == "sheriff_result")
            if not result.get("destroyed"):
                assert review.get("sheriff_seat"), "当选后复盘应带 sheriff_seat"
                assert "speech_order" in etypes, "当选警长后应决定发言顺序"
        print(f"vote_casts={vote_casts} thinking_events={thinking_events} thinking_starts={thinking_starts} thinking_deltas={thinking_deltas}")
        print("SMOKE OK")


def parse_block(block: str) -> tuple[str, dict]:
    event = "message"
    lines: list[str] = []
    for line in block.split("\n"):
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            lines.append(line[5:].strip())
    return event, (json.loads("\n".join(lines)) if lines else {})


async def try_submit(c: "httpx.AsyncClient", gid: str, info: dict) -> None:
    """提交动作；「当前没有等待你的操作」= 已被快照补交过，静默忽略。"""
    rr = await c.post(f"/api/games/{gid}/action", json=auto_action(info, 0))
    if rr.status_code == 200:
        return
    if "当前没有等待你的操作" in rr.text:
        return
    print("action rejected:", rr.status_code, rr.text)


def auto_action(info: dict, human_seat: int) -> dict:
    kind, allowed = info["kind"], info.get("allowed") or []
    payload: dict = {"kind": kind}
    if kind in ("speech", "last_words"):
        payload["text"] = "我是好人，昨晚信息不多，先听大家的。"
    elif kind == "witch_save":
        payload["use"] = False
    elif kind == "witch_poison":
        payload["use"] = False
    elif kind == "hunter":
        payload["shoot"] = False
    elif kind == "sheriff_run":
        payload["run"] = True
    elif kind == "speech_order":
        payload["direction"] = "left"
    elif kind == "badge_pass":
        payload["destroy"] = False
        if allowed:
            payload["target"] = allowed[0]
    elif allowed:
        payload["target"] = allowed[0]
    return payload


if __name__ == "__main__":
    asyncio.run(main())
