"""v0.2 摄像头接入测试：合成视频 ingest → behavior_log → 异常检测联动。"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2
import numpy as np
import pytest
from httpx import ASGITransport, AsyncClient

from pettwin import config
from pettwin.main import create_app


def make_video(path: str, motion_secs: tuple[float, float] = (2.0, 4.0)):
    W, H, FPS, SEC = 320, 240, 10, 6
    vw = cv2.VideoWriter(path, cv2.VideoWriter_fourcc("m", "p", "4", "v"), FPS, (W, H))
    bg = np.full((H, W, 3), 90, dtype=np.uint8)
    for f in range(FPS * SEC):
        frame = bg.copy()
        t = f / FPS
        if motion_secs[0] <= t < motion_secs[1]:
            x = int((t - motion_secs[0]) / (motion_secs[1] - motion_secs[0]) * (W - 80))
            cv2.rectangle(frame, (x, 90), (x + 80, 170), (40, 200, 220), -1)
        frame = frame + np.random.RandomState(f).randint(0, 6, frame.shape, dtype=np.uint8)
        vw.write(frame)
    vw.release()


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("PETTWIN_DATA_DIR", str(tmp_path / "data"))
    config._settings = None
    return create_app()


@pytest.mark.asyncio
async def test_ingest_video_and_anomaly_link(app, tmp_path):
    vid = str(tmp_path / "clip.mp4")
    make_video(str(vid))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        # 1) ingest → 落库
        with open(vid, "rb") as f:
            r = await c.post("/v1/camera/ingest_video",
                             params={"pet_id": "cam1"}, files={"video": ("c.mp4", f)})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["samples"] >= 4, body

        # 2) 静止帧活动量接近 0
        ok, frame_img = cv2.imencode(".jpg", np.full((240, 320, 3), 90, dtype=np.uint8))
        r = await c.post("/v1/camera/ingest_frame", params={"pet_id": "cam1"},
                          files={"image": ("f.jpg", frame_img.tobytes())})
        a = r.json()["activity"]
        assert a < 5.0, f"static frame should be low, got {a}"

        # 3) 异常链路打通：历史高活动 + 一次静止 → 不一定告警（数据少），但 API 应可用
        r = await c.get("/v1/behavior/cam1/anomaly?kind=activity")
        assert r.status_code == 200

        # 4) 常驻源管理（不实际开 RTSP，只验证 status/stop 流程）
        r = await c.get("/v1/camera/sources")
        assert r.status_code == 200
        r = await c.delete("/v1/camera/source/cam_none")
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_ingest_rejects_bad_video(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/v1/camera/ingest_video", params={"pet_id": "x"},
                          files={"video": ("bad.mp4", b"not-a-video")})
        assert r.status_code == 422
