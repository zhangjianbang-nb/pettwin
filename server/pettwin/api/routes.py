"""PetTwin REST API。"""
from __future__ import annotations

import base64

import numpy as np
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field


class PetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    species: str = "cat"
    breed: str = ""
    birthday: str = ""


class BehaviorIn(BaseModel):
    pet_id: str
    kind: str = Field(pattern="^(activity|eat|drink|sleep|litter|meow|weight)$")
    value: float
    ts: float | None = None
    source: str = "manual"
    note: str = ""


class BehaviorBulk(BaseModel):
    pet_id: str
    kind: str
    points: list[tuple[float, float]]


class EventIn(BaseModel):
    pet_id: str
    kind: str = Field(pattern="^(health|diet|behavior|groom|misc)$")
    content: str = Field(min_length=1, max_length=500)
    importance: float = 0.6


class ScheduleIn(BaseModel):
    pet_id: str
    kind: str = Field(pattern="^(vaccine|deworm|groom|birthday|custom)$")
    due_at: float
    label: str
    repeat_days: int | None = None


def make_router(*, profiles, tracker, diary, insights, meows=None, social=None) -> APIRouter:
    router = APIRouter()

    # ---------- 档案 ----------

    @router.post("/v1/pets")
    async def create_pet(req: PetCreate, photo_b64: str | None = None):
        img = _decode_image(photo_b64)
        pet_id = profiles.create_pet(req.name, req.species, req.breed, req.birthday, img)
        return {"pet_id": pet_id}

    @router.get("/v1/pets")
    async def list_pets():
        return profiles.list_pets()

    @router.get("/v1/pets/{pet_id}")
    async def get_pet(pet_id: str):
        prof = profiles.get_profile(pet_id)
        if not prof:
            raise HTTPException(404, "pet not found")
        return prof

    @router.post("/v1/pets/{pet_id}/photo")
    async def add_photo(pet_id: str, image: UploadFile = File(...)):
        img = _decode_image_bytes(await image.read())
        try:
            m = profiles.register(pet_id, img)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        return {"samples_updated": True, "is_new": m.is_new}

    @router.post("/v1/identify")
    async def identify(image: UploadFile = File(...)):
        img = _decode_image_bytes(await image.read())
        try:
            m = profiles.identify(img)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        return {"pet_id": m.pet_id, "name": m.name,
                "similarity": m.similarity, "is_new": m.is_new}

    @router.delete("/v1/pets/{pet_id}")
    async def forget_pet(pet_id: str):
        profiles.forget_pet(pet_id)
        return {"ok": True}

    # ---------- 行为 ----------

    @router.post("/v1/behavior")
    async def log_behavior(req: BehaviorIn):
        rid = tracker.log(req.pet_id, req.kind, req.value, req.ts, req.source, req.note)
        return {"id": rid}

    @router.post("/v1/behavior/bulk")
    async def bulk_behavior(req: BehaviorBulk):
        tracker.bulk_log(req.pet_id, req.kind, req.points)
        return {"ok": True, "n": len(req.points)}

    @router.get("/v1/behavior/{pet_id}/anomaly")
    async def anomaly(pet_id: str, kind: str = "activity"):
        a = tracker.anomaly_score(pet_id, kind)
        return a or {"kind": kind, "anomaly": False, "reason": "insufficient baseline"}

    @router.get("/v1/behavior/{pet_id}/rhythm")
    async def rhythm(pet_id: str, kind: str = "activity", days: int = 28):
        return {"hourly": tracker.hourly_profile(pet_id, kind, days)}

    @router.get("/v1/behavior/{pet_id}/wow")
    async def week_over_week(pet_id: str, kind: str = "activity"):
        return tracker.week_over_week(pet_id, kind) or {"kind": kind, "reason": "insufficient data"}

    # ---------- 事件与记忆 ----------

    @router.post("/v1/events")
    async def record_event(req: EventIn):
        e = diary.record_event(req.pet_id, req.kind, req.content, req.importance)
        return {"id": e.id, "tags": e.tags}

    @router.get("/v1/events/{pet_id}")
    async def search_events(pet_id: str, q: str = "", limit: int = 8):
        if q:
            hits = diary.related_events(pet_id, q, top_k=limit)
            return [{"content": h[0].content, "importance": h[0].importance,
                     "created_at": h[0].created_at, "score": h[1]} for h in hits]
        rows = diary.store.list_memories(pet_id, layer="episodic", limit=limit)
        return [{"content": r.content, "importance": r.importance,
                 "created_at": r.created_at} for r in rows]

    # ---------- 日程 ----------

    @router.post("/v1/schedule")
    async def add_schedule(req: ScheduleIn):
        insights.store.conn.execute(
            "INSERT INTO schedule (pet_id,kind,due_at,repeat_days,label) VALUES (?,?,?,?,?)",
            (req.pet_id, req.kind, req.due_at, req.repeat_days, req.label))
        insights.store.conn.commit()
        return {"ok": True}

    # ---------- 洞察与周报 ----------

    @router.get("/v1/insights/{pet_id}")
    async def daily_insights(pet_id: str):
        out = insights.daily_digest(pet_id)
        return [{"level": i.level, "title": i.title, "detail": i.detail,
                 "score": i.score} for i in out]

    @router.get("/v1/report/{pet_id}")
    async def weekly_report(pet_id: str):
        name = profiles.get_name(pet_id) or "它"
        return diary.weekly_report(pet_id, name)

    # ---------- 3D 分身（v0.3） ----------

    @router.get("/v1/avatar/{pet_id}/style")
    async def avatar_style(pet_id: str):
        """行为权重向量：behavior_log → 桌面 3D 分身动画参数。
        数据不足返回 {"style": None}，前端落到默认猫性格。"""
        from pettwin.avatar.style import compute_style_vector
        vec = compute_style_vector(tracker, pet_id)
        return {"pet_id": pet_id, "style": vec}

    # ---------- 叫声语义（v0.4） ----------

    @router.post("/v1/meow/{pet_id}")
    async def analyze_meow(pet_id: str, audio: UploadFile = File(...),
                           repeats: int = Form(1)):
        """上传叫声 wav → 声学特征 → 场景语义（hunger/greeting/distress/playful/other）。
        与自身基线比 z-score，显著异常标 anomalous（健康预警输入）。"""
        data = await audio.read()
        try:
            return meows.analyze(pet_id, data, repeats=repeats)
        except ValueError as e:
            raise HTTPException(422, str(e))

    @router.get("/v1/meow/{pet_id}/history")
    async def meow_history(pet_id: str, limit: int = 20):
        return {"history": meows.history(pet_id, limit=limit)}

    @router.get("/v1/meow/{pet_id}/counts")
    async def meow_counts(pet_id: str, days: int = 7):
        return {"counts": meows.kind_counts(pet_id, days=days)}

    # ---------- 多宠社交（v0.4） ----------

    @router.post("/v1/social/{pet_a}/{pet_b}/interaction")
    async def add_interaction(pet_a: str, pet_b: str, kind: str = Form(...),
                              note: str = Form("")):
        """记录互动事件（play/groom/fight/share_spot/other）。"""
        try:
            iid = social.record_interaction(pet_a, pet_b, kind, note=note)
        except ValueError as e:
            raise HTTPException(422, str(e))
        return {"id": iid}

    @router.get("/v1/social/{pet_a}/{pet_b}")
    async def social_view(pet_a: str, pet_b: str, days: int = 7):
        """陪伴分（0-1）+ 共处分钟 + 近 7 天互动事件。任一方无摄像头数据 → None。"""
        return social.companionship(pet_a, pet_b, days=days) or {
            "pet_a": pet_a, "pet_b": pet_b, "reason": "insufficient camera data"}

    @router.get("/v1/health")
    async def health():
        return {"status": "ok", "dino": profiles.engine.is_available()}

    return router


def _decode_image(b64: str | None):
    if not b64:
        return None
    return _decode_image_bytes(base64.b64decode(b64))


def _decode_image_bytes(data: bytes):
    import cv2
    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(422, "invalid image")
    return img
