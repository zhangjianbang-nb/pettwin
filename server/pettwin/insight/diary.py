"""事件记忆接入 + LLM 周报生成。

事件 → 四层记忆引擎（episodic 入库，observe 标签触发回访）；
周报 → 行为统计（周同比+异常）+ 近期事件 → LLM 生成自然语言（无 LLM 时模板兜底）。
"""
from __future__ import annotations

import time
import uuid

from pettwin.behavior.metrics import BehaviorTracker
from pettwin.config import get_settings
from pettwin.memory.embeddings import EmbeddingClient
from pettwin.memory.reflector import Reflector
from pettwin.memory.store import MemoryEntry, MemoryStore

OBSERVE_KINDS = {"health", "diet", "behavior"}  # 这些类别的事件默认带 observe 标签


class PetDiary:
    def __init__(self, store: MemoryStore, tracker: BehaviorTracker):
        self.store = store
        self.tracker = tracker
        self.embedder = EmbeddingClient()
        self.reflector = Reflector(store, self.embedder, _NullLLM())

    def record_event(self, pet_id: str, kind: str, content: str,
                     importance: float = 0.6) -> MemoryEntry:
        """主人记录事件（打疫苗/换粮/吐毛球…）→ episodic 记忆。"""
        tags = ["event", kind]
        if kind in OBSERVE_KINDS:
            tags.append("observe")  # 回访引擎的钩子
        entry = MemoryEntry(
            id=uuid.uuid4().hex[:16], user_id=pet_id, layer="episodic",
            content=f"[{kind}] {content}", importance=min(1.0, max(0.0, importance)),
            tags=tags)
        vec = self.embedder.hash_embed(content)
        self.store.add_memory(entry, vec)
        return entry

    def related_events(self, pet_id: str, query: str, top_k: int | None = None) -> list:
        s = get_settings()
        vec = self.embedder.hash_embed(query)
        hits = self.store.search(pet_id, vec, top_k=top_k or s.memory_recall_top_k)
        return hits

    def weekly_report(self, pet_id: str, pet_name: str = "它") -> dict:
        """周报：行为统计 + 事件回顾 →（可选 LLM）自然语言。"""
        now = time.time()
        stats = {}
        for kind in ("activity", "eat", "sleep", "litter"):
            wow = self.tracker.week_over_week(pet_id, kind, now=now)
            if wow:
                stats[kind] = wow
        events = self.store.list_memories(pet_id, layer="episodic", limit=15)
        event_lines = [e.content for e in events if e.created_at >= now - 7 * 86400]
        anomalies = [i for i in (
            self.tracker.anomaly_score(pet_id, k) for k in ("activity", "eat", "drink"))
            if i and i["anomaly"]]

        report = {
            "pet_id": pet_id, "generated_at": now,
            "stats": stats, "events": event_lines, "anomalies": anomalies,
            "narrative": None,
        }
        llm_text = self._llm_narrative(pet_name, stats, event_lines, anomalies)
        report["narrative"] = llm_text or self._template_narrative(pet_name, stats, event_lines)
        return report

    def _llm_narrative(self, name, stats, events, anomalies) -> str | None:
        s = get_settings()
        if not s.llm_base_url:
            return None
        try:
            import httpx
            stat_txt = "；".join(
                f"{k}本周{v['this']:.0f}（上周{v['last']:.0f}，{v['delta_pct']:+.0f}%）"
                for k, v in stats.items())
            ev_txt = "；".join(events[:6]) or "无"
            an_txt = "；".join(f"{a['kind']} z={a['z']:.1f}" for a in anomalies) or "无异常"
            prompt = (
                f"你是宠物健康助手。{name}本周行为统计：{stat_txt}。异常：{an_txt}。"
                f"本周记录的事件：{ev_txt}。请写 3-4 句中文周报：先讲行为变化，"
                "再联系事件给出温和建议。不说教，不诊断疾病。")
            r = httpx.post(
                f"{s.llm_base_url.rstrip('/')}/chat/completions",
                json={"model": s.llm_model, "messages": [{"role": "user", "content": prompt}],
                      "max_tokens": 300, "temperature": 0.6},
                headers={"Authorization": f"Bearer {s.llm_api_key}"}, timeout=60)
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        except Exception:
            return None

    def _template_narrative(self, name, stats, events) -> str:
        parts = []
        for k, v in stats.items():
            direction = "上升" if v["delta_pct"] > 0 else "下降"
            parts.append(f"{name}本周{k}比上周{direction} {abs(v['delta_pct']):.0f}%")
        if events:
            parts.append(f"本周记录了 {len(events)} 件事：{'；'.join(events[:3])}")
        return "。".join(parts) + "。" if parts else f"{name}本周数据还不多，继续记录。"


class _NullLLM:
    """reflector 需要的 LLM 接口占位（宠物域周报走 PetDiary 自带逻辑）。"""

    async def complete(self, prompt, **kw):
        return "[]"
