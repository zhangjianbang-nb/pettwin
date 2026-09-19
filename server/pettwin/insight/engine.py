"""主动洞察引擎（Insight）：异常检测 → 警报；回访；日程提醒；每日上限防打扰。

复用 SoulSync proactive 的"门控+上限+反馈"哲学，宠物域触发源：
- anomaly：活动量/饮食 z-score 异常 → 最高优先级
- schedule：疫苗/驱虫到期、生日
- revisit：N 天前有"待观察"事件（换粮/就医），回访适应情况
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from pettwin.behavior.metrics import BehaviorTracker
from pettwin.config import get_settings
from pettwin.memory.store import MemoryStore


@dataclass
class Insight:
    level: str          # alert | schedule | revisit | report
    title: str
    detail: str
    score: float        # 优先级 0-1
    pet_id: str
    data: dict = field(default_factory=dict)


class InsightEngine:
    def __init__(self, store: MemoryStore, tracker: BehaviorTracker):
        self.store = store
        self.tracker = tracker
        self.store.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS insight_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pet_id TEXT NOT NULL,
                ts REAL NOT NULL,
                level TEXT NOT NULL,
                title TEXT NOT NULL
            );
            """)

    # ---------- 触发源 ----------

    def check_anomalies(self, pet_id: str, kinds=("activity", "eat", "drink")) -> list[Insight]:
        out = []
        s = get_settings()
        for kind in kinds:
            a = self.tracker.anomaly_score(pet_id, kind)
            if a and a["anomaly"]:
                direction = "偏低" if a["z"] < 0 else "偏高"
                level = "alert" if a["z"] < -2.0 else "notice"
                out.append(Insight(
                    level=level,
                    title=f"{kind} 异常{direction}",
                    detail=(f"最近值 {a['value']:.1f}，基线均值 {a['baseline_mean']:.1f}"
                            f"（z={a['z']:.1f}，n={a['n_baseline']}）。"
                            + ("活动骤降常提示不适，建议观察精神/食欲，必要时就医。"
                               if kind == "activity" and a["z"] < 0 else "")),
                    score=min(1.0, 0.7 + abs(a["z"]) / 10),
                    pet_id=pet_id, data=a))
        return out

    def check_schedule(self, pet_id: str, horizon_days: float = 7.0) -> list[Insight]:
        now = time.time()
        rows = self.store.conn.execute(
            "SELECT id, kind, due_at, label FROM schedule "
            "WHERE pet_id=? AND done_at IS NULL AND due_at<=? ORDER BY due_at",
            (pet_id, now + horizon_days * 86400)).fetchall()
        out = []
        for r in rows:
            overdue = r["due_at"] < now
            out.append(Insight(
                level="schedule",
                title=("已到期：" if overdue else "即将到期：") + r["label"],
                detail=f"类型 {r['kind']}，到期时间 {time.strftime('%m-%d %H:%M', time.localtime(r['due_at']))}",
                score=0.9 if overdue else 0.6,
                pet_id=pet_id, data={"schedule_id": r["id"]}))
        return out

    def check_revisits(self, pet_id: str, within_days: float = 7.0) -> list[Insight]:
        """N 天前记录的"待观察"事件 → 回访。"""
        s = get_settings()
        now = time.time()
        cutoff = now - within_days * 86400
        rows = self.store.conn.execute(
            "SELECT content, created_at FROM memories WHERE user_id=? AND layer='episodic' "
            "AND (tags LIKE '%observe%') AND created_at>=? AND created_at<=?",
            (pet_id, cutoff, now - s.revisit_min_gap_hours * 3600)).fetchall()
        out = []
        for r in rows:
            days_ago = (now - r["created_at"]) / 86400
            out.append(Insight(
                level="revisit",
                title=f"回访：{r['content'][:40]}",
                detail=f"{days_ago:.0f} 天前记录。情况有好转吗？",
                score=0.5,
                pet_id=pet_id, data={"event": r["content"]}))
        return out

    # ---------- 汇合（门控+每日上限） ----------

    def daily_digest(self, pet_id: str, now: float | None = None) -> list[Insight]:
        """生成今日洞察清单（按优先排序；受每日上限约束）。"""
        s = get_settings()
        now = now or time.time()
        if not s.proactive_enabled:
            return []
        today_start = now - (now % 86400)
        sent = self.store.conn.execute(
            "SELECT COUNT(*) n FROM insight_log WHERE pet_id=? AND ts>=?",
            (pet_id, today_start)).fetchone()["n"]
        cap = s.daily_report_cap
        budget = max(0, cap - sent)

        candidates = (self.check_anomalies(pet_id) + self.check_schedule(pet_id)
                       + self.check_revisits(pet_id))
        # 去重：同一（level,title）24h 内只发一次——异常未恢复时不再重复刷屏
        fresh = []
        for c in candidates:
            dup = self.store.conn.execute(
                "SELECT 1 FROM insight_log WHERE pet_id=? AND level=? AND title=? AND ts>?",
                (pet_id, c.level, c.title, now - 86400)).fetchone()
            if not dup:
                fresh.append(c)
        fresh.sort(key=i_score, reverse=True)
        chosen = fresh[:budget]
        for c in chosen:
            self.store.conn.execute(
                "INSERT INTO insight_log (pet_id, ts, level, title) VALUES (?,?,?,?)",
                (pet_id, now, c.level, c.title))
        self.store.conn.commit()
        return chosen


def i_score(i: Insight) -> float:
    return i.score
