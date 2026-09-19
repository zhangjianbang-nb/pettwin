"""v0.5 姿势分析测试。"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from pettwin.memory.store import MemoryStore
from pettwin.perception.pose import BBox, PoseWatcher, classify_pose

T0 = 1000.0
TS = [T0 + i * 0.5 for i in range(6)]


def test_lying():
    boxes = [BBox(100, 200, 280, 300)] * 6  # ratio 1.8
    ev = classify_pose(boxes, TS)
    assert any(e.kind == "lying" for e in ev)


def test_stretched():
    boxes = [BBox(50, 220, 350, 260)]  # ratio 7.5
    ev = classify_pose(boxes, TS)
    assert any(e.kind == "stretched" for e in ev)


def test_on_object_zone():
    boxes = [BBox(100, 200, 280, 300)]
    ev = classify_pose(boxes, TS, zones={"keyboard": (90, 190, 290, 310)})
    hit = [e for e in ev if e.kind == "on_object"]
    assert hit and hit[0].zone == "keyboard"


def test_sitting():
    boxes = [BBox(100, 150, 200, 250)]  # ratio 1.0 稳定
    ev = classify_pose(boxes, TS)
    assert any(e.kind == "sitting" for e in ev)


def test_jumping():
    seq = [BBox(100, 100, 200, 200), BBox(100, 105, 200, 205),
           BBox(100, 260, 200, 360), BBox(100, 265, 200, 365)]
    ev = classify_pose(seq, [T0 + i * 0.3 for i in range(4)])
    assert any(e.kind == "jumping" for e in ev)


def test_moving():
    boxes = [BBox(50 + i * 60, 150, 150 + i * 60, 250) for i in range(5)]
    ev = classify_pose(boxes, [T0 + i * 0.4 for i in range(5)])
    assert any(e.kind == "moving" for e in ev)


def test_watcher_dedup():
    w = PoseWatcher(zones={"keyboard": (90, 190, 290, 310)})
    out = []
    for i in range(6):
        out += w.push(BBox(100, 200, 280, 300), ts=T0 + i * 0.5)
    assert any(e.kind == "on_object" for e in out)
    # 3 分钟内不重发
    out2 = []
    for i in range(6):
        out2 += w.push(BBox(100, 200, 280, 300), ts=T0 + 10 + i * 0.5)
    assert not any(e.kind == "on_object" for e in out2)


def test_pose_api(tmp_path):
    import os
    import pettwin.config as cfg
    from fastapi.testclient import TestClient
    from pettwin.main import create_app

    os.environ["PETTWIN_DATA_DIR"] = str(tmp_path)
    cfg._settings = None
    app = create_app()
    client = TestClient(app)

    body = {
        "pet_id": "p1",
        "bboxes": [{"x1": 100, "y1": 200, "x2": 280, "y2": 300}] * 6,
        "ts": time.time(),
        "zones": {"keyboard": [90, 190, 290, 310]},
    }
    r = client.post("/v1/pose/p1", json=body)
    assert r.status_code == 200
    events = r.json()["events"]
    assert any(e["kind"] == "on_object" for e in events)

    # behavior_log 有 pose 记录
    r2 = client.get("/v1/pose/p1/recent")
    counts = r2.json()["counts"]
    assert any(k.startswith("on_object") for k in counts)

    # episodic 事件已记（observe 标签链路）
    r3 = client.get("/v1/events/p1")
    assert any("[behavior] [pose:on_object]" in e["content"] for e in r3.json())
