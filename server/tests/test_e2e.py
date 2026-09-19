"""PetTwin e2e：建宠→注册照片→识别→行为→异常→事件→洞察→周报 全旅程。"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pytest
from httpx import ASGITransport, AsyncClient

from pettwin import config
from pettwin.main import create_app


def cat_image(seed: int) -> bytes:
    import cv2
    rng = np.random.RandomState(seed)
    img = rng.randint(0, 255, (120, 120, 3), dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("PETTWIN_DATA_DIR", str(tmp_path / "data"))
    config._settings = None
    return create_app()


@pytest.mark.asyncio
async def test_full_journey(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        # 1) 建两只宠 + 各录一张照
        r = await c.post("/v1/pets", json={"name": "咪咪", "species": "cat", "birthday": "2023-05-01"})
        pid1 = r.json()["pet_id"]
        r = await c.post("/v1/pets", json={"name": "旺财", "species": "dog"})
        pid2 = r.json()["pet_id"]
        await c.post(f"/v1/pets/{pid1}/photo", files={"image": ("a.jpg", cat_image(1))})
        await c.post(f"/v1/pets/{pid2}/photo", files={"image": ("b.jpg", cat_image(2))})

        # 2) 识别：A 照应命中 pid1
        r = await c.post("/v1/identify", files={"image": ("a.jpg", cat_image(1))})
        body = r.json()
        assert body["pet_id"] == pid1 and body["name"] == "咪咪", body

        # 3) 行为：14 天正常 + 今日骤降
        now = time.time()
        pts = [(now - d * 86400, 100.0) for d in range(13, 0, -1)]
        await c.post("/v1/behavior/bulk", json={"pet_id": pid1, "kind": "activity", "points": pts})
        await c.post("/v1/behavior", json={"pet_id": pid1, "kind": "activity", "value": 30})
        r = await c.get(f"/v1/behavior/{pid1}/anomaly?kind=activity")
        body = r.json()
        assert body["anomaly"] is True and body["z"] < 0, body

        # 4) 事件：换粮（带 observe 标签）→ 相关检索
        await c.post("/v1/events", json={"pet_id": pid1, "kind": "diet",
                                          "content": "换了新猫粮，它第一天不太爱吃", "importance": 0.7})
        r = await c.get(f"/v1/events/{pid1}?q=猫粮")
        hits = r.json()
        assert hits and "猫粮" in hits[0]["content"]

        # 5) 日程：疫苗过期
        await c.post("/v1/schedule", json={"pet_id": pid1, "kind": "vaccine",
                                            "due_at": now - 86400, "label": "年度疫苗"})

        # 6) 洞察：异常+日程都应出现（回访被 24h 门槛挡住，因为事件刚记录）
        r = await c.get(f"/v1/insights/{pid1}")
        insights = r.json()
        levels = [i["level"] for i in insights]
        assert "alert" in levels and "schedule" in levels, insights

        # 7) 周报
        r = await c.get(f"/v1/report/{pid1}")
        rep = r.json()
        assert rep["narrative"] and "咪咪" in rep["narrative"]
        assert rep["events"]

        # 8) 遗忘
        await c.delete(f"/v1/pets/{pid2}")
        r = await c.get("/v1/pets")
        assert all(p["pet_id"] != pid2 for p in r.json())


@pytest.mark.asyncio
async def test_behavior_validation(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/v1/behavior", json={"pet_id": "x", "kind": "invalid", "value": 1})
        assert r.status_code == 422  # kind 枚举校验
