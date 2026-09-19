"""PetTwin 服务端入口：uvicorn pettwin.main:app --port 8801"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from fastapi.middleware.cors import CORSMiddleware

from pettwin.api.camera import CameraHub, make_camera_router
from pettwin.api.routes import make_router
from pettwin.behavior.metrics import BehaviorTracker
from pettwin.behavior.social import SocialGraph
from pettwin.perception.meow import MeowEngine
from pettwin.config import get_settings
from pettwin.insight.diary import PetDiary
from pettwin.insight.engine import InsightEngine
from pettwin.memory.store import MemoryStore
from pettwin.perception.pet_id import PetIdentityEngine
from pettwin.perception.pet_profile import PetProfileManager


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="PetTwin", version="0.3.0",
                  description="Behavioral memory twin for real pets")
    # 桌面分身页可与 server 分离部署, GET 端点开放 CORS（只读, 无 cookie 凭证）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["*"],
    )
    store = MemoryStore(s.db_path, embed_dim=s.embed_dim)
    app.state.store = store

    engine = PetIdentityEngine(store)
    profiles = PetProfileManager(store, engine)
    tracker = BehaviorTracker(store)
    diary = PetDiary(store, tracker)
    insights = InsightEngine(store, tracker)
    meows = MeowEngine(store)
    social = SocialGraph(store, tracker)
    app.state.profiles = profiles
    app.state.meows = meows
    app.state.social = social

    app.include_router(make_router(profiles=profiles, tracker=tracker,
                                   diary=diary, insights=insights,
                                   meows=meows, social=social))
    app.state.camera_hub = CameraHub(tracker)
    app.include_router(make_camera_router(app.state.camera_hub))

    # 静态资产：/static/desktop/*（3D 分身页）——App iframe 与远程桌面访问入口
    desktop_dir = Path(__file__).resolve().parents[2] / "desktop"
    if desktop_dir.is_dir():
        app.mount("/static/desktop", StaticFiles(directory=desktop_dir, html=True), name="desktop")

    @app.on_event("shutdown")
    def _close():
        store.close()

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    s = get_settings()
    uvicorn.run(app, host=s.server_host, port=s.server_port)
