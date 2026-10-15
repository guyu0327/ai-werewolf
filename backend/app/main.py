from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .engine.store import Store
from .events import GameHub
from .llm.client import LlmClient
from .runner import GameRunner
from .routes import games as games_routes
from .routes import settings as settings_routes
from .routes import stream as stream_routes
from .routes import voice as voice_routes

if getattr(sys, "frozen", False):
    # PyInstaller 产物在 resources/server/server.exe：前端产物与数据目录按安装目录相对定位
    PROJECT_ROOT = Path(sys.executable).resolve().parent.parent
else:
    PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _data_dir() -> Path:
    env = os.environ.get("AT_WEREWOLF_DATA", "").strip()
    return Path(env) if env else PROJECT_ROOT / "data"


def create_app() -> FastAPI:
    app = FastAPI(title="AI Werewolf", docs_url=None, redoc_url=None)
    store = Store(_data_dir())
    llm = LlmClient()
    hub = GameHub()
    runner = GameRunner(store, hub, llm)
    app.state.store = store
    app.state.llm = llm
    app.state.hub = hub
    app.state.runner = runner

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    token = os.environ.get("AT_WEREWOLF_TOKEN", "").strip()

    @app.middleware("http")
    async def auth(request, call_next):
        if token and request.url.path.startswith("/api") and request.url.path != "/api/health":
            if request.headers.get("X-AT-Token") != token:
                return JSONResponse({"message": "未授权访问"}, status_code=401)
        return await call_next(request)

    @app.middleware("http")
    async def no_cache_html(request, call_next):
        # index.html 每次使用前必须重验证：静态托管默认无 Cache-Control，Chromium 启发式缓存
        # 会让桌面端在重新构建后仍加载旧 bundle（hash 资源名随旧 html 锁死）
        response = await call_next(request)
        if response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    app.include_router(games_routes.router)
    app.include_router(settings_routes.router)
    app.include_router(stream_routes.router)
    app.include_router(voice_routes.router)

    dist = PROJECT_ROOT / "web" / "dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="web")

    @app.on_event("startup")
    async def on_startup():
        print("[werewolf] 后端已启动", file=sys.stderr)

    return app


app = create_app()


def run() -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("AT_WEREWOLF_PORT", "8321")), log_level="warning")


if __name__ == "__main__":
    run()
