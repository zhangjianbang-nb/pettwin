"""v0.4 叫声语义 + 多宠社交测试。"""
import io
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pytest

from pettwin.behavior.metrics import BehaviorTracker
from pettwin.behavior.social import SocialGraph
from pettwin.memory.store import MemoryStore
from pettwin.perception.meow import MeowEngine, classify, extract_features


def _synth(freq: float, dur: float, sr: int = 16000, noise: float = 0.01) -> bytes:
    """合成猫叫：基频+谐波+弯调+衰减包络。"""
    t = np.arange(int(dur * sr)) / sr
    f_t = freq * (1 + 0.15 * np.exp(-t * 8))
    phase = 2 * np.pi * np.cumsum(f_t) / sr
    x = 0.6 * np.sin(phase) + 0.25 * np.sin(2 * phase) + 0.1 * np.sin(3 * phase)
    env = np.exp(-3 * t / dur) * (t < dur * 0.9)
    x = x * env + noise * np.random.randn(len(x))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((x.astype(np.float32) * 32767).astype(np.int16).tobytes())
    return buf.getvalue()


@pytest.fixture
def store(tmp_path):
    s = MemoryStore(tmp_path / "t.db")
    yield s
    s.close()


# ---------- 特征与分类 ----------

def test_features_shape(store):
    np.random.seed(7)
    f = extract_features(_synth(500, 0.6))
    assert 0.5 < f.duration_s < 0.7
    assert 0 < f.energy <= 1.0
    assert 0 <= f.zcr <= 1.0
    assert 400 < f.f0_hz < 650  # 合成基频 500Hz
    assert f.centroid_hz > 0


def test_classify_four_kinds():
    np.random.seed(7)
    assert classify(extract_features(_synth(750, 1.5))).kind == "distress"
    assert classify(extract_features(_synth(500, 0.6))).kind == "greeting"
    assert classify(extract_features(_synth(520, 0.5), repeats=3)).kind == "hunger"
    assert classify(extract_features(_synth(600, 0.3))).kind == "playful"


def test_classify_rejects_no_f0():
    # 无周期性信号(纯噪声) → f0=0 → other
    np.random.seed(3)
    x = (np.random.randn(16000) * 0.1).astype(np.float32)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes((x * 32767).astype(np.int16).tobytes())
    f = extract_features(buf.getvalue())
    assert f.f0_hz == 0.0
    assert classify(f).kind == "other"


# ---------- 引擎（基线自学习 + 历史） ----------

def test_engine_history_and_baseline(store):
    np.random.seed(7)
    eng = MeowEngine(store)
    # 前 8 次"正常"叫声喂基线
    for _ in range(8):
        r = eng.analyze("p1", _synth(500, 0.6))
        assert r["kind"] == "greeting"
    # 异常叫声：超长超高基频 → distress + 与基线比显著异常
    r2 = eng.analyze("p1", _synth(900, 2.0))
    assert r2["kind"] == "distress"
    assert r2["anomalous"] is True
    assert r2["z"] is not None and r2["z"] >= 2.5

    hist = eng.history("p1")
    assert len(hist) == 9
    assert hist[0]["kind"] == "distress"  # 倒序最新在前

    counts = eng.kind_counts("p1", days=7)
    assert counts.get("greeting") == 8
    assert counts.get("distress") == 1


def test_engine_insufficient_baseline_no_anomaly(store):
    np.random.seed(7)
    eng = MeowEngine(store)
    for _ in range(3):  # 少于 8 条不判异常
        eng.analyze("p1", _synth(500, 0.6))
    r = eng.analyze("p1", _synth(900, 2.0))
    assert r["anomalous"] is False


# ---------- 社交 ----------

def test_social_companionship(store):
    tracker = BehaviorTracker(store)
    sg = SocialGraph(store, tracker)
    now = time.time()
    for i in range(100):
        tracker.log("a", "activity", 30, ts=now - (i + 1) * 60, source="camera")
    for i in range(80):
        tracker.log("b", "activity", 25, ts=now - (i + 1) * 60, source="camera")
    r = sg.companionship("a", "b", now=now)
    assert r["companionship"] > 0.7
    assert r["co_minutes"] == 80.0
    assert sg.companionship("a", "solo", now=now) is None


def test_social_interactions(store):
    tracker = BehaviorTracker(store)
    sg = SocialGraph(store, tracker)
    iid = sg.record_interaction("b", "a", "play", note="追逐")  # 倒序输入自动规范化
    assert iid > 0
    evs = sg.interactions("a", "b", days=7)
    assert len(evs) == 1 and evs[0]["kind"] == "play"
    with pytest.raises(ValueError):
        sg.record_interaction("a", "a", "play")
    with pytest.raises(ValueError):
        sg.record_interaction("a", "b", "hug")


# ---------- API ----------

def test_meow_social_api(tmp_path, store):
    import os
    import pettwin.config as cfg
    from fastapi.testclient import TestClient
    from pettwin.main import create_app

    os.environ["PETTWIN_DATA_DIR"] = str(tmp_path)
    cfg._settings = None
    app = create_app()
    client = TestClient(app)

    np.random.seed(7)
    wav = _synth(500, 0.6)
    r = client.post("/v1/meow/p1",
                   files={"audio": ("m.wav", wav, "audio/wav")},
                   data={"repeats": "1"})
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] in ("greeting", "other")

    r2 = client.get("/v1/meow/p1/history")
    assert r2.status_code == 200 and len(r2.json()["history"]) == 1

    r3 = client.post("/v1/meow/p1", files={"audio": ("x.wav", b"notawav", "audio/wav")})
    assert r3.status_code == 422

    r4 = client.post("/v1/social/pa/pb/interaction", data={"kind": "play", "note": "n"})
    assert r4.status_code == 200
    r5 = client.get("/v1/social/pa/pb")
    assert r5.status_code == 200
    # 无 camera 数据 → reason 分支
    assert "reason" in r5.json() or "companionship" in r5.json()
