"""多宠社交关系引擎（v0.4）：共处时段 + 互动事件 + 陪伴分。

数据来源：
  - behavior_log 里 kind=activity source=camera 的记录——两只宠物在同 60s 窗内都有
    活动记录 → 一次共处（co-presence）。
  - 显式互动事件（App 手记，或后续由视觉检测自动生成）。

关系视图（GET /v1/social/{pet_a}/{pet_b}）：
  - 共处分钟数（7/28 天）、互动事件数、陪伴分 0-1
  - 陪伴分 = 共处槽 / min(A 活动槽, B 活动槽)

表：social_interaction(id, pet_a, pet_b, kind, ts, note)——pet_a < pet_b 规范化排序。
"""
from __future__ import annotations

import time

INTERACTION_KINDS = ("play", "groom", "fight", "share_spot", "other")
CO_PRESENCE_WINDOW_S = 60  # 共处判定窗口（秒）


class SocialGraph:
    def __init__(self, store, tracker):
        self.store = store
        self.tracker = tracker
        self._ensure_schema()

    def _ensure_schema(self):
        self.store.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS social_interaction (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pet_a TEXT NOT NULL,
                pet_b TEXT NOT NULL,
                kind TEXT NOT NULL,          -- play|groom|fight|share_spot|other
                ts REAL NOT NULL,
                note TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_social ON social_interaction(pet_a, pet_b, ts);
            """
        )

    # ---------- 互动事件 ----------

    def record_interaction(self, pet_a: str, pet_b: str, kind: str,
                           ts: float | None = None, note: str = "") -> int:
        if kind not in INTERACTION_KINDS:
            raise ValueError(f"unknown interaction kind {kind}")
        if pet_a == pet_b:
            raise ValueError("pet_a == pet_b")
        a, b = sorted((pet_a, pet_b))
        ts = ts or time.time()
        cur = self.store.conn.execute(
            "INSERT INTO social_interaction (pet_a, pet_b, kind, ts, note) VALUES (?,?,?,?,?)",
            (a, b, kind, ts, note))
        self.store.conn.commit()
        return cur.lastrowid

    def interactions(self, pet_a: str, pet_b: str, days: int = 7,
                     kind: str | None = None) -> list[dict]:
        a, b = sorted((pet_a, pet_b))
        lo = time.time() - days * 86400
        q = ("SELECT kind, ts, note FROM social_interaction "
             "WHERE pet_a=? AND pet_b=? AND ts>=?")
        args: list = [a, b, lo]
        if kind:
            q += " AND kind=?"
            args.append(kind)
        q += " ORDER BY ts DESC"
        rows = self.store.conn.execute(q, args).fetchall()
        return [{"kind": r["kind"], "ts": r["ts"], "note": r["note"]} for r in rows]

    # ---------- 共处统计 ----------

    def _activity_slots(self, pet_id: str, days: int, now: float) -> set[int]:
        """该宠物有 camera 活动记录的 60s 槽位集合。"""
        lo = now - days * 86400
        rows = self.store.conn.execute(
            "SELECT ts FROM behavior_log WHERE pet_id=? AND kind='activity' "
            "AND source='camera' AND ts>=?", (pet_id, lo)).fetchall()
        return {int(r["ts"] // CO_PRESENCE_WINDOW_S) for r in rows}

    def companionship(self, pet_a: str, pet_b: str, days: int = 7,
                      now: float | None = None) -> dict | None:
        """陪伴分：共处槽 / min(A 活动槽, B 活动槽)，0-1。任一方无 camera 数据 → None。"""
        now = now or time.time()
        sa = self._activity_slots(pet_a, days, now)
        sb = self._activity_slots(pet_b, days, now)
        if not sa or not sb:
            return None
        both = sa & sb
        denom = min(len(sa), len(sb))
        score = len(both) / denom if denom else 0.0
        return {
            "days": days,
            "companionship": round(score, 3),
            "co_minutes": len(both) * CO_PRESENCE_WINDOW_S / 60.0,
            "active_minutes_a": len(sa) * CO_PRESENCE_WINDOW_S / 60.0,
            "active_minutes_b": len(sb) * CO_PRESENCE_WINDOW_S / 60.0,
            "interactions_7d": self.interactions(pet_a, pet_b, days=7),
        }
