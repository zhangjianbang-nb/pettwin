"""叫声语义引擎（v0.4）：wav/meow 音频 → 声学特征 → 场景语义分类。

零重依赖路线：numpy + 标准库 wave 解码；特征 = 时长/能量/过零率/基频(自相关)/频谱质心。
分类两层：
  1. 规则引擎（可解释，默认）：时长×基频×重复模式 → hunger/greeting/distress/playful/other
  2. 基线自学习：为每只宠物存历史特征，新叫声与自身历史分布比 z-score，
     显著异常的叫声标 anomalous（健康预警输入，联动 InsightEngine）

设计取舍：不做黑盒神经网络分类（数据少易过拟合且不可解释）；
YAMNet/预训练模型留可选增强位（is_model_available()）。
"""
from __future__ import annotations

import io
import math
import time
import wave
from dataclasses import dataclass, asdict

import numpy as np

MEOW_KINDS = ("hunger", "greeting", "distress", "playful", "other")


@dataclass
class MeowFeatures:
    duration_s: float      # 叫声时长
    energy: float          # RMS 能量 (0-1 归一)
    zcr: float             # 过零率（嘶哑/气声高，饱满低）
    f0_hz: float           # 基频（猫 400-800Hz 典型，痛苦尖锐更高）
    centroid_hz: float     # 频谱质心（尖锐度）
    repeats: int = 1       # 连续叫声次数（2s 窗内）

    def to_dict(self) -> dict:
        d = asdict(self)
        for k, v in d.items():
            if isinstance(v, float):
                d[k] = round(v, 4)
        return d


@dataclass
class MeowLabel:
    kind: str              # MEOW_KINDS 之一
    confidence: float      # 0-1（规则引擎为规则强度）
    note: str              # 人话解释
    anomalous: bool = False
    z: float | None = None


# ---------- 解码 ----------

def decode_wav(data: bytes) -> tuple[np.ndarray, int]:
    """wav bytes → (mono float32 [-1,1], sample_rate)。支持标准 PCM。"""
    with wave.open(io.BytesIO(data), "rb") as w:
        sr = w.getframerate()
        nch = w.getnchannels()
        sw = w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if sw == 2:
        x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif sw == 4:
        x = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
    elif sw == 1:
        x = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    else:
        raise ValueError(f"unsupported sample width {sw}")
    if nch > 1:
        x = x.reshape(-1, nch).mean(axis=1)
    return x.astype(np.float32), sr


def decode_any(data: bytes) -> tuple[np.ndarray, int]:
    """wav 优先；非 wav（裸 mp3/ogg）抛错——App 端统一转 wav 上传。"""
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return decode_wav(data)
    raise ValueError("only PCM wav supported (App 端转码后上传)")


# ---------- 特征 ----------

def extract_features(data: bytes, repeats: int = 1) -> MeowFeatures:
    x, sr = decode_any(data)
    if len(x) < sr // 20:  # <50ms 视为噪声
        raise ValueError("audio too short")
    # 去直流
    x = x - x.mean()
    duration = len(x) / sr
    rms = math.sqrt(float(np.mean(x * x)))
    energy = min(1.0, rms * 4.0)  # 猫叫 RMS 通常 0.05-0.25
    # 过零率
    signs = x[1:] * x[:-1] < 0
    zcr = float(np.mean(signs.astype(np.float32)))
    # 基频: 归一化自相关在 250-1200Hz 窗内找峰
    f0 = _estimate_f0(x, sr, lo_hz=250, hi_hz=1200)
    # 频谱质心（rFFT 幅度加权）
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    freqs = np.fft.rfftfreq(len(x), 1.0 / sr)
    total = float(spec.sum()) or 1.0
    centroid = float(np.dot(freqs, spec) / total)
    return MeowFeatures(duration_s=duration, energy=energy, zcr=zcr,
                        f0_hz=f0, centroid_hz=centroid, repeats=max(1, int(repeats)))


def _estimate_f0(x: np.ndarray, sr: int, lo_hz: float, hi_hz: float) -> float:
    lag_lo = max(1, int(sr / hi_hz))
    lag_hi = min(len(x) - 1, int(sr / lo_hz))
    if lag_hi <= lag_lo:
        return 0.0
    seg = x[: min(len(x), sr)]  # 最多 1s
    seg = seg / (np.linalg.norm(seg) + 1e-9)
    best_lag, best_val = 0, -1.0
    for lag in range(lag_lo, lag_hi + 1):
        v = float(np.dot(seg[:-lag], seg[lag:]))
        if v > best_val:
            best_val, best_lag = v, lag
    if best_lag == 0 or best_val < 0.3:  # 无明显周期性
        return 0.0
    return round(sr / best_lag, 1)


# ---------- 规则分类 ----------

def classify(f: MeowFeatures) -> MeowLabel:
    """规则引擎。优先级：distress > hunger > greeting > playful > other。

    依据（猫行为学常识，可解释）：
    - distress: 长叫(>1.2s) + 高基频(>650Hz) 或高质心 → 痛/怕
    - hunger:   短促重复(repeats>=2) + 中高基频 + 饭点前后 → 讨食（重复由调用方给）
    - greeting: 中短叫(0.3-0.9s) + 基频正常(400-600) + 单次 → 见人打招呼
    - playful:  短促(<=0.4s) + 高能量 + 低 zcr → 玩耍邀请
    """
    if f.f0_hz <= 0:
        return MeowLabel("other", 0.3, "无清晰基频（环境音/摩擦声）")

    if f.duration_s > 1.2 and (f.f0_hz > 650 or f.centroid_hz > 1800):
        return MeowLabel("distress", 0.75, f"长叫 {f.duration_s:.1f}s 且基频 {f.f0_hz:.0f}Hz 偏高——痛/怕信号")
    if f.duration_s > 1.5:
        return MeowLabel("distress", 0.55, f"超长叫 {f.duration_s:.1f}s")

    if f.repeats >= 2 and 0.2 < f.duration_s < 0.9 and f.f0_hz > 450:
        return MeowLabel("hunger", 0.7, f"{f.repeats} 连叫 + 基频 {f.f0_hz:.0f}Hz——典型讨食")

    if 0.3 <= f.duration_s <= 0.9 and 380 <= f.f0_hz <= 620 and f.repeats == 1:
        return MeowLabel("greeting", 0.65, "标准短喵——打招呼/求关注")

    if f.duration_s <= 0.4 and f.energy > 0.5 and f.zcr < 0.15:
        return MeowLabel("playful", 0.6, "短促饱满——玩耍邀请")

    return MeowLabel("other", 0.35,
                     f"时长 {f.duration_s:.2f}s / 基频 {f.f0_hz:.0f}Hz / 重复 {f.repeats}")


# ---------- 基线自学习 ----------

class MeowBaseline:
    """每只宠物的叫声特征库（z-score 自比）。存 store.kv：key=meowbase:{pet_id}。

    特征向量 = [duration_s, energy, zcr, f0_hz, centroid_hz]（repeats 不入向量）。
    """

    DIM = 5
    MAX_SAMPLES = 200

    def __init__(self, store):
        self.store = store

    def _load(self, pet_id: str) -> list[list[float]]:
        row = self.store.conn.execute(
            "SELECT data FROM kv_store WHERE key=?", (f"meowbase:{pet_id}",)).fetchone()
        if not row:
            return []
        import json
        return json.loads(row["data"])

    def _save(self, pet_id: str, samples: list[list[float]]):
        import json
        self.store.conn.execute(
            "INSERT OR REPLACE INTO kv_store (key, data) VALUES (?, ?)",
            (f"meowbase:{pet_id}", json.dumps(samples[-self.MAX_SAMPLES:])))
        self.store.conn.commit()

    def compare(self, pet_id: str, f: MeowFeatures) -> tuple[bool, float | None]:
        """与自身历史比：|z|>=2.5 视为异常叫声。样本 <8 时不判。"""
        samples = self._load(pet_id)
        if len(samples) < 8:
            return False, None
        arr = np.asarray(samples, dtype=np.float32)
        vec = np.asarray([f.duration_s, f.energy, f.zcr, f.f0_hz, f.centroid_hz],
                         dtype=np.float32)
        mean, std = arr.mean(axis=0), arr.std(axis=0)
        std = np.maximum(std, np.maximum(np.abs(mean) * 0.1, 1e-3))
        z = float(np.max(np.abs((vec - mean) / std)))
        return z >= 2.5, round(z, 2)

    def observe(self, pet_id: str, f: MeowFeatures):
        samples = self._load(pet_id)
        samples.append([f.duration_s, f.energy, f.zcr, f.f0_hz, f.centroid_hz])
        self._save(pet_id, samples)


class MeowEngine:
    """叫声语义门面：分类 + 基线自学习 + 历史落库（kv_store）。

    历史记录存 kv_store key=meowhist:{pet_id}（最近 100 条），
    供"它/它 日/周报引用（例：本周讨食叫比上周多 40%）。
    """

    def __init__(self, store):
        self.store = store
        self.baseline = MeowBaseline(store)

    def analyze(self, pet_id: str, audio: bytes, repeats: int = 1,
                ts: float | None = None) -> dict:
        f = extract_features(audio, repeats=repeats)
        label = classify(f)
        anomalous, z = self.baseline.compare(pet_id, f)
        if anomalous:
            label.anomalous = True
            label.z = z
        self.baseline.observe(pet_id, f)
        ts = ts or time.time()
        self._append_history(pet_id, {
            "ts": ts,
            "kind": label.kind,
            "confidence": label.confidence,
            "note": label.note,
            "anomalous": anomalous,
            "features": f.to_dict(),
        })
        return {
            "kind": label.kind,
            "confidence": label.confidence,
            "note": label.note,
            "anomalous": anomalous,
            "z": z,
            "features": f.to_dict(),
        }

    def history(self, pet_id: str, limit: int = 20) -> list[dict]:
        import json
        row = self.store.conn.execute(
            "SELECT data FROM kv_store WHERE key=?", (f"meowhist:{pet_id}",)).fetchone()
        if not row:
            return []
        hist = json.loads(row["data"])
        return hist[-limit:][::-1]

    def kind_counts(self, pet_id: str, days: int = 7) -> dict:
        """近 N 天叫声语义分布（周报输入）。"""
        import json
        lo = time.time() - days * 86400
        row = self.store.conn.execute(
            "SELECT data FROM kv_store WHERE key=?", (f"meowhist:{pet_id}",)).fetchone()
        if not row:
            return {}
        hist = [h for h in json.loads(row["data"]) if h["ts"] >= lo]
        counts: dict = {}
        for h in hist:
            counts[h["kind"]] = counts.get(h["kind"], 0) + 1
        return counts

    def _append_history(self, pet_id: str, entry: dict):
        import json
        row = self.store.conn.execute(
            "SELECT data FROM kv_store WHERE key=?", (f"meowhist:{pet_id}",)).fetchone()
        hist = json.loads(row["data"]) if row else []
        hist.append(entry)
        self.store.conn.execute(
            "INSERT OR REPLACE INTO kv_store (key, data) VALUES (?, ?)",
            (f"meowhist:{pet_id}", json.dumps(hist[-100:])))
        self.store.conn.commit()
