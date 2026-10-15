"""本地 mock LLM（OpenAI 兼容 /v1/chat/completions），用于整局冒烟的成功路径。

- stream=true：按 OpenAI 流式格式分块返回一段发言
- 其他：按提示词中的任务指令返回对应结构化 JSON 决策
用法：python scripts/mock_llm.py [port]   （默认 9100）
"""
from __future__ import annotations

import json
import re
import sys

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI()

SPEECH = "大家好，我昨晚睡得很香，今天先听大家怎么说。"

DECISION_PREFIXES = (
    "请投票放逐一人",
    "狼队夜聊",
    "请选择今晚查验",
    "请选择今晚守护",
    "请决定是否使用解药",
    "请决定是否使用毒药",
    "你出局了",
    "警长竞选开始",
    "请投票选出警长",
    "你出局了，警徽在你手里",
    "你当选警长",
)


def task_of(body: dict) -> str:
    msgs = body.get("messages", [])
    user_text = str(msgs[-1].get("content", "")) if msgs else ""
    return user_text.split("【你的任务】")[-1]


def decide(body: dict) -> str:
    user_text = str((body.get("messages") or [{}])[-1].get("content", ""))
    task = task_of(body)
    m = re.search(r"存活玩家：([^【\n。]*)", user_text)
    alive = [int(s) for s in re.findall(r"(\d+)号", m.group(1))] if m else []
    m = re.search(r"【可[^】]*】[^【]*?(\d+)号", user_text)
    target = int(m.group(1)) if m else (alive[0] if alive else 1)
    if alive and target not in alive:
        target = alive[0]  # 提示词无目标清单或目标已出局时，改打首个存活者（暂停机制不再兜底非法目标）
    sys_text = str((body.get("messages") or [{}])[0].get("content", ""))
    ms = re.search(r"你的座位是 (\d+)号", sys_text)
    my_seat = int(ms.group(1)) if ms else 1
    if task.startswith("请一次性设计"):
        m2 = re.search(r"(\d+)\s*个", task)
        n = max(1, min(3, int(m2.group(1)) if m2 else 1))
        personas = [
            {
                "name": f"随机侠{i}",
                "persona": (
                    "你是个爱说冷笑话的老江湖，说话慢悠悠，喜欢用反问收尾。"
                    "找狼时专记谁的话前后对不上，投票又稳又狠，口头禅是'急的往往不是好人'。"
                ),
            }
            for i in range(1, n + 1)
        ]
        return json.dumps({"personas": personas}, ensure_ascii=False)
    if task.startswith("请设计一个狼人杀"):
        return json.dumps(
            {
                "name": "笑面虎",
                "persona": (
                    "你是个永远笑眯眯的老江湖，说话慢悠悠的，喜欢用反问收尾：'你们觉得呢？'。"
                    "你从不第一个跳身份，前两天只会眯着眼听别人发言，专记谁的话前后对不上。"
                    "轮到你需要伪装时，你能把自己摘得一干二净；找狼时你的票永远投得又稳又狠。"
                    "你的口头禅是'急的往往不是好人'。"
                ),
            },
            ensure_ascii=False,
        )
    if task.startswith("请投票放逐一人"):
        return json.dumps({"target": target, "reason": "直觉，你发言像狼"}, ensure_ascii=False)
    if '"explode"' in task:  # 自爆权衡（白天发言 / 警上竞选 / 查杀触发共用此字段名）
        return json.dumps({"explode": False}, ensure_ascii=False)
    if task.startswith("狼队夜聊"):
        return json.dumps({"target": target, "reason": "刀他试探身份"}, ensure_ascii=False)
    if task.startswith("请选择今晚查验"):
        return json.dumps({"target": target}, ensure_ascii=False)
    if task.startswith("请决定是否使用解药"):
        return json.dumps({"use": False}, ensure_ascii=False)
    if task.startswith("请决定是否使用毒药"):
        return json.dumps({"use": False, "target": 0}, ensure_ascii=False)
    if task.startswith("你出局了"):
        return json.dumps({"shoot": False, "target": 0}, ensure_ascii=False)
    if task.startswith("警长竞选开始"):
        return json.dumps({"run": my_seat % 2 == 1}, ensure_ascii=False)
    if task.startswith("请投票选出警长"):
        return json.dumps({"target": target}, ensure_ascii=False)
    if task.startswith("你出局了，警徽在你手里"):
        return json.dumps({"destroy": False, "target": target}, ensure_ascii=False)
    if task.startswith("你当选警长"):
        return json.dumps({"direction": "left" if my_seat % 2 == 1 else "right"}, ensure_ascii=False)
    return json.dumps({"target": target, "reason": "直觉"}, ensure_ascii=False)


@app.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    model = body.get("model", "mock")
    if body.get("stream"):
        is_decision = task_of(body).startswith(DECISION_PREFIXES)

        async def gen():
            # 决策请求：模拟思考型模型，先吐 reasoning_content 再吐 content JSON
            if is_decision:
                answer = decide(body)
                for piece in ("先梳理信息，", "再权衡各座位身份，", "给出结论。"):
                    chunk = {
                        "id": "mock", "object": "chat.completion.chunk", "created": 1, "model": model,
                        "choices": [{"index": 0, "delta": {"reasoning_content": piece}, "finish_reason": None}],
                    }
                    yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
                for piece in (answer[:10], answer[10:]):
                    chunk = {
                        "id": "mock", "object": "chat.completion.chunk", "created": 1, "model": model,
                        "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}],
                    }
                    yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
            else:
                # 发言请求也模拟思考型模型：先吐 reasoning 再吐正文
                for piece in ("组织一下发言思路，", "简短表态即可。"):
                    chunk = {
                        "id": "mock", "object": "chat.completion.chunk", "created": 1, "model": model,
                        "choices": [{"index": 0, "delta": {"reasoning_content": piece}, "finish_reason": None}],
                    }
                    yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
                for piece in (SPEECH[:6], SPEECH[6:12], SPEECH[12:]):
                    chunk = {
                        "id": "mock", "object": "chat.completion.chunk", "created": 1, "model": model,
                        "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}],
                    }
                    yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    return JSONResponse(
        {
            "id": "mock", "object": "chat.completion", "created": 1, "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": decide(body)}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 9100, log_level="warning")
