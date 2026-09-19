"""v0.3 风格向量测试：behavior_log → 3D 分身动画参数映射。"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pettwin.avatar.style import compute_style_vector
from pettwin.behavior.metrics import BehaviorTracker
from pettwin.memory.store import MemoryStore


def _seed(tracker, pid, now, *, hourly_val=30, recent_6h=180, recent_1h=30,
          sleep=0, eat=0, meow=0):
    """14 天小时级基线（近 3h 留白）+ 近 6h 六个点 + 事件点。"""
    for d in range(14):
        for h in range(24):
            ts = now - (d + 1) * 86400 + h * 3600 + 600
            if ts > now - 3 * 3600:  # 近 3h 交给 recent 段
                continue
            tracker.log(pid, "activity", hourly_val, ts=ts)
    for i in range(6):
        tracker.log(pid, "activity", recent_6h / 6, ts=now - (5 - i) * 3600 - 600)
    if recent_1h:
        tracker.log(pid, "activity", recent_1h, ts=now - 1800)
    if sleep:
        tracker.log(pid, "sleep", sleep, ts=now - 3600 - 600)
    if eat:
        tracker.log(pid, "eat", eat, ts=now - 3600 - 600)
    if meow:
        tracker.log(pid, "meow", meow, ts=now - 3600 - 600)


def test_style_empty_returns_none(tmp_path):
    s = MemoryStore(tmp_path / "s.db")
    bt = BehaviorTracker(s)
    assert compute_style_vector(bt, "p1") is None


def test_style_active_cat(tmp_path):
    s = MemoryStore(tmp_path / "s.db")
    bt = BehaviorTracker(s)
    now = time.time()
    _seed(bt, "p1", now)
    v = compute_style_vector(bt, "p1", now=now)
    assert v is not None
    assert v["state"] in ("walk", "gallop")
    assert v["energy"] > 0.4
    assert 0.0 <= v["mood"] <= 1.0
    assert 0.0 <= v["gait"] <= 2.0
    assert 0.0 <= v["tail"] <= 2.0
    assert v["anim"] in ("Idle", "Walk", "Gallop", "Eating", "Idle_2_HeadLow", "Idle_2")


def test_style_lethargic_cat(tmp_path):
    s = MemoryStore(tmp_path / "s.db")
    bt = BehaviorTracker(s)
    now = time.time()
    _seed(bt, "p1", now, recent_6h=20, recent_1h=0)
    v = compute_style_vector(bt, "p1", now=now)
    assert v["energy"] < 0.4
    assert v["mood"] <= 0.25
    assert v["gait"] < 1.0


def test_style_eating(tmp_path):
    s = MemoryStore(tmp_path / "s.db")
    bt = BehaviorTracker(s)
    now = time.time()
    _seed(bt, "p1", now, recent_6h=24, recent_1h=1, eat=25)
    v = compute_style_vector(bt, "p1", now=now)
    assert v["state"] == "eat"
    assert v["anim"] == "Eating"


def test_style_sleeping(tmp_path):
    s = MemoryStore(tmp_path / "s.db")
    bt = BehaviorTracker(s)
    now = time.time()
    _seed(bt, "p1", now, recent_6h=10, recent_1h=0, sleep=120)
    v = compute_style_vector(bt, "p1", now=now)
    assert v["state"] == "sleep"
    assert v["anim"] == "Idle_2_HeadLow"


def test_style_via_api(tmp_path):
    import os
    from fastapi.testclient import TestClient
    import pettwin.config as cfg
    from pettwin.main import create_app

    os.environ["PETTWIN_DATA_DIR"] = str(tmp_path)
    cfg._settings = None  # 重置单例, 让 env 生效
    app = create_app()
    client = TestClient(app)
    r = client.get("/v1/avatar/p1/style")
    assert r.status_code == 200
    body = r.json()
    assert body["pet_id"] == "p1"
    assert body["style"] is None  # 无日志 → None

    bt = BehaviorTracker(app.state.store)
    _seed(bt, "p1", time.time())
    r2 = client.get("/v1/avatar/p1/style")
    assert r2.json()["style"] is not None
