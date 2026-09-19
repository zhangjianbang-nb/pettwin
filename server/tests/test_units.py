"""单元测试：行为统计/洞察门控/个体识别降级。"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pytest

from pettwin.behavior.metrics import BehaviorTracker
from pettwin.insight.engine import InsightEngine
from pettwin.memory.store import MemoryStore, MemoryEntry
from pettwin.perception.pet_id import PetIdentityEngine
from pettwin.perception.pet_profile import PetProfileManager


@pytest.fixture
def store(tmp_path):
    s = MemoryStore(tmp_path / "t.db", embed_dim=256)
    yield s
    s.close()


def test_anomaly_directions(store):
    bt = BehaviorTracker(store)
    now = time.time()
    for d in range(13, 0, -1):
        bt.log("p1", "eat", 80, ts=now - d * 86400)
    bt.log("p1", "eat", 10, ts=now - 3600)
    low = bt.anomaly_score("p1", "eat")
    assert low["anomaly"] and low["z"] < 0
    bt.log("p1", "eat", 200, ts=now - 1800)
    high = bt.anomaly_score("p1", "eat")
    assert high["anomaly"] and high["z"] > 0


def test_anomaly_needs_baseline(store):
    bt = BehaviorTracker(store)
    bt.log("p2", "weight", 4.2)
    assert bt.anomaly_score("p2", "weight") is None  # 数据不足


def test_hourly_profile_normalizes(store):
    bt = BehaviorTracker(store)
    now = time.time()
    pts = [(now - h * 3600, 1.0) for h in range(0, 96)]
    bt.bulk_log("p3", "activity", pts)
    prof = bt.hourly_profile("p3", "activity")
    assert len(prof) == 24 and abs(sum(prof)) == pytest.approx(1.0)


def test_insight_daily_cap(store):
    bt = BehaviorTracker(store)
    ie = InsightEngine(store, bt)
    now = time.time()
    for d in range(13, 0, -1):
        bt.log("p4", "activity", 100, ts=now - d * 86400)
    bt.log("p4", "activity", 20, ts=now - 3600)
    first = ie.daily_digest("p4", now=now)
    assert first
    second = ie.daily_digest("p4", now=now + 30)
    assert second == []  # cap 已满


def test_observe_tag_triggers_revisit(store):
    bt = BehaviorTracker(store)
    ie = InsightEngine(store, bt)
    now = time.time()
    e = MemoryEntry(None, "p5", "episodic", "[diet] 换粮", 0.7, tags=["event", "diet", "observe"])
    e.created_at = now - 3 * 86400
    store.add_memory(e, [0.1] + [0.0] * 255)
    rv = ie.check_revisits("p5")
    assert rv and "换粮" in rv[0].title


def test_pet_identity_hash_separation(store):
    eng = PetIdentityEngine(store)
    rng = np.random.RandomState(3)
    a = rng.randint(0, 255, (100, 100, 3), dtype=np.uint8)
    b = rng.randint(0, 255, (100, 100, 3), dtype=np.uint8)
    va, vb = eng.embed_image(a), eng.embed_image(b)
    dot = float(np.dot(np.asarray(va), np.asarray(vb)))
    assert dot < 0.5  # 降级模式也要能区分不同内容
