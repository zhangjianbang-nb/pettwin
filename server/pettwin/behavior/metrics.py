"""行为日志与模式统计（Behavioral Twin 的数据层）。

- 行为事件流（activity/meow/litter/sleep/eat…）按 pet_id 落库
- 滑动基线（7/14/28 天）：当前值 vs 基线均值/标准差 → z-score 异常分
- 日节律统计（hour-of-day 活动分布）——"它下午 4 点总犯困"的数据来源
- 周同比报告输入
"""
from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass

BEHAVIOR_TYPES = ("activity", "eat", "drink", "sleep", "litter", "meow", "weight")


class BehaviorTracker:
    def __init__(self, store):
        self.store = store
        self._ensure_schema()

    def _ensure_schema(self):
        self.store.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS behavior_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pet_id TEXT NOT NULL,
                kind TEXT NOT NULL,          -- activity|eat|drink|sleep|litter|meow|weight
                value REAL NOT NULL,         -- 数值（分钟/克/次数/千克）
                ts REAL NOT NULL,
                source TEXT NOT NULL DEFAULT 'manual',  -- manual|camera|collar|csv
                note TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_beh ON behavior_log(pet_id, kind, ts);
            CREATE TABLE IF NOT EXISTS schedule (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pet_id TEXT NOT NULL,
                kind TEXT NOT NULL,          -- vaccine|deworm|groom|birthday|custom
                due_at REAL NOT NULL,
                repeat_days INTEGER,         -- 周期性（驱虫 90 天等）；NULL=一次性
                label TEXT NOT NULL,
                done_at REAL
            );
            """)

    # ---------- 写入 ----------

    def log(self, pet_id: str, kind: str, value: float, ts: float | None = None,
            source: str = "manual", note: str = "") -> int:
        assert kind in BEHAVIOR_TYPES, f"unknown behavior kind {kind}"
        cur = self.store.conn.execute(
            "INSERT INTO behavior_log (pet_id,kind,value,ts,source,note) VALUES (?,?,?,?,?,?)",
            (pet_id, kind, float(value), ts if ts is not None else time.time(), source, note))
        self.store.conn.commit()
        return cur.lastrowid

    def bulk_log(self, pet_id: str, kind: str, points: list[tuple[float, float]],
                 source: str = "csv"):
        """批量导入 [(ts, value)]。"""
        rows = [(pet_id, kind, float(v), float(t), source, "") for t, v in points]
        self.store.conn.executemany(
            "INSERT INTO behavior_log (pet_id,kind,value,ts,source,note) VALUES (?,?,?,?,?,?)",
            rows)
        self.store.conn.commit()

    # ---------- 基线与异常 ----------

    def baseline(self, pet_id: str, kind: str, window_days: int,
                 now: float | None = None, exclude_last_hours: float = 0.0) -> tuple | None:
        """窗口内 (mean, std, n)。exclude_last_hours 用于"排除最近值算基线"。"""
        now = now or time.time()
        lo = now - window_days * 86400
        hi = now - exclude_last_hours * 3600
        rows = self.store.conn.execute(
            "SELECT value FROM behavior_log WHERE pet_id=? AND kind=? AND ts>=? AND ts<?",
            (pet_id, kind, lo, hi)).fetchall()
        vals = [r["value"] for r in rows]
        if len(vals) < 2:
            return None
        import statistics as st
        return (st.mean(vals), st.pstdev(vals) or 1e-6, len(vals))

    def anomaly_score(self, pet_id: str, kind: str, window_days: int | None = None,
                      now: float | None = None) -> dict | None:
        """最近一个值相对基线的 z-score。|z|>=anomaly_z 视为异常。"""
        s = __import__("pettwin.config", fromlist=["get_settings"]).get_settings()
        now = now or time.time()
        w = window_days or s.baseline_window_days
        last = self.store.conn.execute(
            "SELECT value, ts FROM behavior_log WHERE pet_id=? AND kind=? "
            "ORDER BY ts DESC LIMIT 1", (pet_id, kind)).fetchone()
        if not last:
            return None
        base = self.baseline(pet_id, kind, w, now=now, exclude_last_hours=6)
        if base is None or base[2] < s.anomaly_min_points:
            return None
        mean, std, n = base
        z = (last["value"] - mean) / std
        return {"kind": kind, "value": last["value"], "baseline_mean": round(mean),
                "z": round(z), "anomaly": abs(z) >= s.anomaly_z,
                "n_baseline": n, "ts": last["ts"]}

    # ---------- 日节律 ----------

    def hourly_profile(self, pet_id: str, kind: str, days: int = 28) -> list[float]:
        """24 小时活动分布（归一化）——"它下午 4 点总犯困"的依据。"""
        now = time.time()
        rows = self.store.conn.execute(
            "SELECT ts, value FROM behavior_log WHERE pet_id=? AND kind=? AND ts>=?",
            (pet_id, kind, now - days * 86400)).fetchall()
        buckets = [0.0] * 24
        for r in rows:
            hour = time.localtime(r["ts"]).tm_hour
            buckets[hour] += r["value"]
        total = sum(buckets) or 1.0
        return [b / total for b in buckets]

    # ---------- 周同比 ----------

    def week_over_week(self, pet_id: str, kind: str, now: float | None = None) -> dict | None:
        now = now or time.time()
        this_week = self.store.conn.execute(
            "SELECT SUM(value) s FROM behavior_log WHERE pet_id=? AND kind=? AND ts>=?",
            (pet_id, kind, now - 7 * 86400)).fetchone()["s"]
        last_week = self.store.conn.execute(
            "SELECT SUM(value) s FROM behavior_log WHERE pet_id=? AND kind=? "
            "AND ts>=? AND ts<?",
            (pet_id, kind, now - 14 * 86400, now - 7 * 86400)).fetchone()["s"]
        if this_week is None or not last_week:
            return None
        return {"kind": kind, "this": this_week, "last": last_week,
                "delta_pct": (this_week - last_week) / last_week * 100.0}
