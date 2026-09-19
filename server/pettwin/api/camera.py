"""摄像头接入 API（v0.2）：视频/单帧 ingest → 活动量 → behavior_log。

写入 kind='activity', source='camera'——v0.1 的基线/异常/周报自动生效。
常驻源（RTSP/USB）用后台线程周期采样；简化版 v0.2 先做 ingest + 常驻源管理。
"""
from __future__ import annotations

import threading
import time

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from pettwin.perception.camera import MotionSensor, PetDetector, activity_from_video


class CameraSource(BaseModel):
    pet_id: str
    kind: str  # rtsp | usb
    url: str = ""     # rtsp://... 或 usb 索引字符串
    interval_s: float = 60.0   # 采样间隔（每 interval_s 采 window_s 秒）
    window_s: float = 5.0


class CameraHub:
    """常驻视频源管理（每源一个采样线程）。"""

    def __init__(self, tracker):
        self.tracker = tracker
        self.sources: dict[str, dict] = {}   # pet_id -> {stop_event, thread, cfg}
        self.sensor = MotionSensor()
        self.detector = PetDetector()

    def start_source(self, cfg: CameraSource):
        if cfg.pet_id in self.sources:
            raise ValueError("source already running for this pet")
        stop = threading.Event()
        th = threading.Thread(target=self._loop, args=(cfg, stop), daemon=True,
                              name=f"cam-{cfg.pet_id}")
        self.sources[cfg.pet_id] = {"stop": stop, "thread": th, "cfg": cfg}
        th.start()

    def stop_source(self, pet_id: str) -> bool:
        s = self.sources.pop(pet_id, None)
        if not s:
            return False
        s["stop"].set()
        s["thread"].join(timeout=5)
        return True

    def status(self) -> list[dict]:
        return [{"pet_id": pid, "kind": s["cfg"].kind, "url": s["cfg"].url,
                 "interval_s": s["cfg"].interval_s, "alive": s["thread"].is_alive()}
                for pid, s in self.sources.items()]

    def _loop(self, cfg: CameraSource, stop: threading.Event):
        import cv2
        cap = cv2.VideoCapture(0 if cfg.kind == "usb" and not cfg.url else
                               (int(cfg.url) if cfg.kind == "usb" else cfg.url))
        if not cap.isOpened():
            self.sources.pop(cfg.pet_id, None)
            return
        fps = cap.get(5) or 30.0
        n_frames = max(1, int(cfg.window_s * min(fps, 5.0)))  # 最多 5fps 采样
        while not stop.is_set():
            vals = []
            for _i in range(n_frames):
                if stop.is_set():
                    break
                ok, frame = cap.read()
                if not ok:
                    time.sleep(0.5)
                    continue
                vals.append(self.sensor.frame_activity(frame))
                time.sleep(1.0 / min(fps, 5.0))
            if vals:
                activity = sum(vals) / len(vals)
                try:
                    self.tracker.log(cfg.pet_id, "activity", round(activity, 2),
                                     source="camera", note=f"{cfg.kind} window")
                except Exception:
                    pass
            # 等下一采样窗口
            stop.wait(max(1.0, cfg.interval_s - cfg.window_s))
        cap.release()


def make_camera_router(hub: CameraHub) -> APIRouter:
    router = APIRouter()

    @router.post("/v1/camera/ingest_video")
    async def ingest_video(pet_id: str = "default", video: UploadFile = File(...)):
        """上传视频片段 → 每秒活动量 → behavior_log(source=camera)。"""
        import tempfile, os
        suffix = os.path.splitext(video.filename or "v.mp4")[1] or ".mp4"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            f.write(await video.read())
            tmp = f.name
        try:
            samples = activity_from_video(tmp, sensor=hub.sensor, detector=hub.detector)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        finally:
            os.unlink(tmp)
        pts = [(s.ts, round(s.activity, 2)) for s in samples]
        hub.tracker.bulk_log(pet_id, "activity", pts, source="camera")
        return {"samples": len(pts),
                "mean_activity": round(sum(p[1] for p in pts) / len(pts)) if pts else 0}

    @router.post("/v1/camera/ingest_frame")
    async def ingest_frame(pet_id: str = "default", image: UploadFile = File(...)):
        """App 推单帧（自管采样节奏）。返回该帧活动量并落库。"""
        import numpy as np
        data = await image.read()
        arr = np.frombuffer(data, dtype=np.uint8)
        import cv2
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            raise HTTPException(422, "invalid image")
        a = round(hub.sensor.frame_activity(img))
        hub.tracker.log(pet_id, "activity", round(a, 2), source="camera", note="frame")
        return {"activity": round(a)}

    @router.post("/v1/camera/source")
    async def start_source(cfg: CameraSource):
        try:
            hub.start_source(cfg)
        except ValueError as e:
            raise HTTPException(409, str(e)) from e
        return {"ok": True}

    @router.delete("/v1/camera/source/{pet_id}")
    async def stop_source(pet_id: str):
        if not hub.stop_source(pet_id):
            raise HTTPException(404, "no running source")
        return {"ok": True}

    @router.get("/v1/camera/sources")
    async def list_sources():
        return hub.status()

    return router
