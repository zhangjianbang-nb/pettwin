"""摄像头感知层（v0.2）：视频源抽象 + 活动量计算。

设计原则：
- 活动量 = 帧间运动像素占比（0-100 归一化），可跨摄像头比较、可解释、零模型依赖
- MOG2 背景减除（cv2 自带）为主；YOLO 宠物检测为可选增强（is_yolo_available()）
- VideoSource 统一抽象：视频文件 / RTSP / USB 摄像头 / App 推帧
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import numpy as np


@dataclass
class ActivitySample:
    ts: float            # 采样结束时间
    seconds: float       # 覆盖时长
    activity: float      # 0-100（运动像素占比均值）
    peak: float          # 窗口内峰值
    frames: int


class MotionSensor:
    """MOG2 背景减除运动量估计。固定机位假设（背景稳定）。"""

    def __init__(self, history: int = 300, var_threshold: float = 25.0,
                 downscale: int = 320, min_blob_area: int = 80):
        self.downscale = downscale
        self.min_blob_area = min_blob_area
        self._sub = None
        self._prev_shape = None

    def _ensure_sub(self, shape):
        import cv2
        if self._sub is None or self._prev_shape != shape:
            import cv2
            self._sub = cv2.createBackgroundSubtractorMOG2(
                history=300, varThreshold=25, detectShadows=False)
            self._prev_shape = shape

    def frame_activity(self, frame_bgr: np.ndarray) -> float:
        """单帧运动占比 0-100。首帧只建模背景返回 0。"""
        import cv2
        h, w = frame_bgr.shape[:2]
        scale = self.downscale / max(h, w)
        small = cv2.resize(frame_bgr, (int(w * scale), int(h * scale))) if scale < 1 else frame_bgr
        self._ensure_sub(small.shape)
        fg = self._sub.apply(small)
        # 形态学去噪 + 面积过滤（忽略噪点）
        fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN,
                              cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
        n = int((fg > 0).sum())
        # 过滤小连通域（简化：总像素低于阈值视为 0）
        if n < self.min_blob_area:
            return 0.0
        return min(100.0, 100.0 * n / fg.size)

    def reset(self):
        self._sub = None
        self._prev_shape = None


class PetDetector:
    """YOLO 可选增强：识别猫/狗并只统计宠物区域（人走过不误计）。

    未装 ultralytics 时 is_yolo_available()=False，调用方回退 MotionSensor。
    """

    # COCO 类 id：15=cat 16=dog
    PET_CLASSES = (15, 16)

    def __init__(self, model_name: str = "yolov8n.pt", conf: float = 0.35):
        self.model_name = model_name
        self.conf = conf
        self._model = None
        self._lock = threading.Lock()

    def is_yolo_available(self) -> bool:
        try:
            import ultralytics  # noqa: F401
            return True
        except ImportError:
            return False

    def _get_model(self):
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from ultralytics import YOLO
                    self._model = YOLO(self.model_name)
        return self._model

    def detect(self, frame_bgr: np.ndarray) -> list[tuple[int, int, int, int]]:
        """返回宠物 bbox 列表 [(x1,y1,x2,y2)]。"""
        if not self.is_yolo_available():
            return []
        res = self._get_model().predict(frame_bgr, verbose=False,
                                        conf=self.conf, classes=list(self.PET_CLASSES))
        boxes = []
        for r in res:
            for b in r.boxes:
                x1, y1, x2, y2 = [int(v) for v in b.xyxy[0].tolist()]
                boxes.append((x1, y1, x2, y2))
        return boxes


def activity_from_video(path: str, sensor: MotionSensor | None = None,
                        detector: PetDetector | None = None,
                        max_seconds: float = 600.0,
                        sample_fps: float = 2.0) -> list[ActivitySample]:
    """视频文件 → 每秒活动量样本（写入 behavior_log 前的中间产物）。

    sample_fps：每秒采样帧数（2 足够估计活动量，全帧率没必要）。
    """
    import cv2
    sensor = sensor or MotionSensor()
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise ValueError(f"cannot open video {path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, int(fps / sample_fps))
    per_sec: dict[int, list[float]] = {}
    t0 = time.time()
    i = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        sec = int(i / fps)
        if sec > max_seconds:
            break
        if detector and detector.is_yolo_available() and i % (step * 4) == 0:
            # 检测到宠物才计活动量（YOLO 增强模式：排除人走动误计）
            boxes = detector.detect(frame)
            if not boxes:
                i += 1
                continue
        a = sensor.frame_activity(frame)
        per_sec.setdefault(sec, []).append(a)
        i += 1
    cap.release()
    out = []
    for sec in sorted(per_sec):
        vals = per_sec[sec]
        out.append(ActivitySample(
            ts=t0 + sec, seconds=1.0,
            activity=sum(vals) / len(vals), peak=max(vals), frames=len(vals)))
    return out
