"""PetTwin 服务端入口：uvicorn pettwin.main:app --port 8801"""
from __future__ import annotations

from fastapi import FastAPI

from pettwin.api.routes import make_router
from pettwin.behavior.metrics import BehaviorTracker
from pettwin.config import get_settings
from pettwin.insight.diary import PetDiary
from pettwin.insight.engine import InsightEngine
from pettwin.memory.store import MemoryStore
from pettwin.perception.pet_id import PetIdentityEngine
from pettwin.perception.pet_profile import PetProfileManager


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="PetTwin", version="0.1.0",
                  description="Behavioral memory twin for real pets")
    store = MemoryStore(s.db_path, embed_dim=s.embed_dim)
    app.state.store = store

    engine = PetIdentityEngine(store)
    profiles = PetProfileManager(store, engine)
    tracker = BehaviorTracker(store)
    diary = PetDiary(store, tracker)
    insights = InsightEngine(store, tracker)
    app.state.profiles = profiles

    app.include_router(make_router(profiles=profiles, tracker=tracker,
                                   diary=diary, insights=insights))

    @app.on_event("shutdown")
    def _close():
        store.close()

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    s = get_settings()
    uvicorn.run(app, host=s.server_host, port=s.server_port)
