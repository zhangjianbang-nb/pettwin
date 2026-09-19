"""姿势分析（v0.5）：bbox 几何序列 → 标志动作判定。

关键点来源分层（与 v0.2 摄像头同哲学：零依赖可跑，模型可选增强）：
  L1（默认）：YOLO bbox 几何——宽高比、中心高度、面积随时间的变化率。
     没装 ultralytics 时由调用方传入手工 bbox（App 框选/固定区域）。
  L2（预留）：DeepLabCut/YOLO-pose 关键点——接口已留 ` keypoints ` 参数，
     有真关键点时判定精度更高，规则引擎不变。

可判定动作（Magic Moment 素材）：
  lying      趴卧   bbox 宽>高×1.5 且持续
  stretched  伸懒腰 宽>高×2.2（前伸趴姿）或面积缓增+宽高比峰值
  sitting    端坐   接近方形(0.8-1.3) 且底部位置稳定
  jumping    跳跃   中心 y 在 0.5s 内突变 > bbox 高
  on_object  趴物件 趴卧 + 位置落在标注区（键盘/显示器/床——App 传 zones）
  moving     移动   中心 x 位移显著

输出 PoseEvent(kind, ts, confidence, zone, note)——写入 behavior_log(kind=pose_*)
+ events（behavior 类，自动 observe 标签→回访钩子）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, asdict

POSE_KINDS = ("lying", "stretched", "sitting", "jumping", "on_object", "moving")


@dataclass
class BBox:
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def w(self) -> float:
        return self.x2 - self.x1

    @property
    def h(self) -> float:
        return self.y2 - self.y1

    @property
    def cx(self) -> float:
        return (self.x1 + self.x2) / 2

    @property
    def cy(self) -> float:
        return (self.y1 + self.y2) / 2

    @property
    def area(self) -> float:
        return self.w * self.h

    def overlap_ratio(self, zone: tuple[float, float, float, float]) -> float:
        """与标注区 (x1,y1,x2,y2) 的交并比（对 bbox 自身面积归一）。"""
        zx1, zy1, zx2, zy2 = zone
        ix1, iy1 = max(self.x1, zx1), max(self.y1, zy1)
        ix2, iy2 = min(self.x2, zx2), min(self.y2, zy2)
        if ix2 <= ix1 or iy2 <= iy1:
            return 0.0
        inter = (ix2 - ix1) * (iy2 - iy1)
        return inter / self.area if self.area else 0.0


@dataclass
class PoseEvent:
    kind: str
    ts: float
    confidence: float
    zone: str = ""
    note: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["confidence"] = round(d["confidence"], 3)
        return d


def _frame_feats(b: BBox) -> dict:
    return {"ratio": b.w / max(b.h, 1e-6), "cx": b.cx, "cy": b.cy,
            "area": b.area, "w": b.w, "h": b.h}


def classify_pose(bboxes: list[BBox], timestamps: list[float],
                  zones: dict[str, tuple[float, float, float, float]] | None = None
                  ) -> list[PoseEvent]:
    """bbox 时间序列 → 动作事件列表。

    bboxes/timestamps：同一宠物连续采样（camera hub 2fps 或 App 推帧）。
    zones：标注区名→坐标（"keyboard"/"monitor"/"bed"…），命中且趴卧 → on_object。
    """
    zones = zones or {}
    if not bboxes:
        return []
    events: list[PoseEvent] = []
    n = len(bboxes)

    # ---- jumping: 相邻帧中心 y 突变 ----
    for i in range(1, n):
        dt = max(timestamps[i] - timestamps[i - 1], 1e-3)
        drop = bboxes[i].cy - bboxes[i - 1].cy
        ref_h = max(bboxes[i].h, bboxes[i - 1].h, 1e-6)
        if abs(drop) / ref_h > 1.0 and dt < 1.5:
            events.append(PoseEvent("jumping", timestamps[i], 0.7,
                                    note=f"中心高度突变 {abs(drop) / ref_h:.1f} 倍身高"))

    # ---- 静态姿势：取窗口内中位 bbox 判 ratio ----
    feats = [_frame_feats(b) for b in bboxes]
    ratio = sorted(f["ratio"] for f in feats)[n // 2]
    t_mid = timestamps[n // 2]

    # 移动量：中心 x 总位移 / 平均宽
    travel = abs(bboxes[-1].cx - bboxes[0].cx) / max(bboxes[0].w, 1e-6)

    if ratio >= 2.2:
        events.append(PoseEvent("stretched", t_mid, 0.65,
                                note=f"宽高比 {ratio:.1f}——伸懒腰/前趴"))
    elif ratio >= 1.5:
        zone_hit = _zone_hit(bboxes[n // 2], zones)
        if zone_hit:
            events.append(PoseEvent("on_object", t_mid, 0.7, zone=zone_hit,
                                   note=f"趴在 {zone_hit} 上（ratio {ratio:.1f}）"))
        else:
            events.append(PoseEvent("lying", t_mid, 0.7,
                                    note=f"趴卧（宽高比 {ratio:.1f}）"))
    elif 0.8 <= ratio <= 1.3 and travel < 0.3:
        events.append(PoseEvent("sitting", t_mid, 0.6, note="端坐"))
    elif travel >= 1.0:
        events.append(PoseEvent("moving", t_mid, 0.6,
                                note=f"位移 {travel:.1f} 个身位"))

    return events


def _zone_hit(b: BBox, zones: dict[str, tuple]) -> str:
    best, best_ov = "", 0.0
    for name, z in zones.items():
        ov = b.overlap_ratio(z)
        if ov > best_ov:
            best, best_ov = name, ov
    return best if best_ov >= 0.4 else ""


class PoseWatcher:
    """连续帧流 → 去重动作事件（同 kind 3 分钟内不重发）。

    用法：camera hub 每采到 bbox 就 push()，内部滑窗 2s 出判定；
    App 单帧模式由调用方自己攒 4-6 帧后调 analyze_window()。
    """

    def __init__(self, zones: dict | None = None, dedup_s: float = 180.0):
        self.zones = zones or {}
        self.dedup_s = dedup_s
        self._buf: list[tuple[float, BBox]] = []
        self._last_sent: dict[str, float] = {}

    def push(self, bbox: BBox, ts: float | None = None) -> list[PoseEvent]:
        """喂一帧 bbox；窗口满（>=4 帧 且 >=1.5s）触发一次判定并清窗。"""
        ts = ts or time.time()
        self._buf.append((ts, bbox))
        # 窗口裁剪：只留最近 2.5s
        while len(self._buf) > 1 and ts - self._buf[0][0] > 2.5:
            self._buf.pop(0)
        if len(self._buf) < 4:
            return []
        span = self._buf[-1][0] - self._buf[0][0]
        if span < 1.5:
            return []
        events = self.analyze_window([b for _, b in self._buf],
                                     [t for t, _ in self._buf])
        self._buf = self._buf[-1:]  # 保留最后一帧做衔接
        return self._dedup(events)

    def analyze_window(self, bboxes, timestamps) -> list[PoseEvent]:
        return classify_pose(bboxes, timestamps, zones=self.zones)

    def _dedup(self, events: list[PoseEvent]) -> list[PoseEvent]:
        out = []
        for e in events:
            key = f"{e.kind}:{e.zone}"
            last = self._last_sent.get(key, 0.0)
            if e.ts - last < self.dedup_s:
                continue
            self._last_sent[key] = e.ts
            out.append(e)
        return out
