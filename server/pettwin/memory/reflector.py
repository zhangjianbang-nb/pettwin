"""记忆反思器（M2）：

- 会话末总结：把本轮对话抽取为情景记忆条目（importance 门控）
- 夜间巩固：重放当日事件 → Mem0 式合并裁决 [S4] → 生成语义摘要
- 生成式反思：累计重要性超阈值 → 提炼高层洞察 [S1][S14]，洞察本身入记忆流
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from pettwin.config import get_settings
from pettwin.memory.store import MemoryStore, MemoryEntry
from pettwin.memory.embeddings import EmbeddingClient


@dataclass
class TurnRecord:
    """一轮对话（agent 编排器传入）。"""
    user_text: str
    assistant_text: str
    emotion: str | None = None
    valence: float | None = None
    ts: float | None = None


class Reflector:
    def __init__(self, store: MemoryStore, embedder: EmbeddingClient, llm):
        self.store = store
        self.embedder = embedder
        self.llm = llm  # agent.llm.LLMClient
        s = get_settings()
        self.importance_gate = s.memory_importance_gate
        self.reflect_interval = s.reflection_interval_minutes * 60
        self.reflect_min_events = s.reflection_min_events

    # ---------- 会话末：抽取情景记忆 ----------

    async def extract_episodic(self, user_id: str, turns: list[TurnRecord]) -> list[MemoryEntry]:
        """LLM 从对话中抽取值得长期记住的事件（写入门控 [S1][S3]）。"""
        if not turns:
            return []
        dialog = "\n".join(
            f"用户: {t.user_text}\n助手: {t.assistant_text}" for t in turns[-12:])
        prompt = (
            "从下面的对话中提取值得 AI 陪伴者长期记住的事实/事件/情绪。\n"
            "每条输出一行 JSON：{\"content\": 事实(30字内), \"importance\": 1-10, "
            "\"emotion\": happy|sad|angry|anxious|neutral, \"valence\": -1.0~1.0}\n"
            "只提取对未来对话有用的事（健康、家庭、工作、偏好、承诺、重要情绪事件）。"
            "寒暄天气等琐碎不记。没有值得记的输出 []。\n\n对话:\n" + dialog)
        try:
            raw = await self._llm_json(prompt)
        except Exception:
            raw = []
        entries = []
        for item in raw:
            importance = min(1.0, max(0.0, float(item.get("importance", 5)) / 10.0))
            if importance < self.importance_gate:
                continue  # 重要性门控：不每句都记 [S3]
            entry = MemoryEntry(
                id=uuid.uuid4().hex[:16], user_id=user_id, layer="episodic",
                content=str(item.get("content", ""))[:200],
                importance=importance,
                emotion=item.get("emotion"), valence=item.get("valence"),
                tags=["auto"])
            if not entry.content:
                continue
            vec = await self.embedder.embed(entry.content)
            self.store.add_memory(entry, vec)
            entries.append(entry)
        return entries

    # ---------- 夜间巩固：合并裁决 + 语义沉淀 [S4][S15] ----------

    async def consolidate(self, user_id: str) -> int:
        candidates = self.store.consolidate_candidates(user_id, older_than_hours=6, limit=40)
        if len(candidates) < 2:
            return 0
        lines = "\n".join(f"- {c.content}（{c.emotion or 'neutral'}）" for c in candidates)
        prompt = (
            "以下是一个 AI 陪伴者记录的关于某用户的情景记忆碎片。请合并去重：\n"
            "输出 JSON 数组，每项 {\"content\": 合并后的语义记忆(40字内), "
            "\"importance\": 1-10, \"source_ids\": [碎片编号]}\n"
            "相同主题的合并成一条更概括的语义记忆；矛盾的以新的为准。\n\n碎片:\n" + lines)
        try:
            merged = await self._llm_json(prompt)
        except Exception:
            return 0
        # 把碎片转为 semantic 层，删除已合并的碎片（有损重构 [S10]）
        id_map = {i + 1: c.id for i, c in enumerate(candidates)}
        n = 0
        for item in merged:
            src_ids = [id_map.get(i) for i in item.get("source_ids", []) if id_map.get(i)]
            content = item.get("content", "")[:200]
            if not content:
                continue
            vec = await self.embedder.embed(content)
            entry = MemoryEntry(
                id=uuid.uuid4().hex[:16], user_id=user_id, layer="semantic",
                content=content,
                importance=min(1.0, float(item.get("importance", 5)) / 10.0),
                tags=["consolidated"])
            self.store.add_memory(entry, vec)
            for sid in src_ids:
                self.store.delete_memory(sid)
            n += 1
        return n

    # ---------- 生成式反思：洞察提炼 [S1][S14] ----------

    async def reflect(self, user_id: str) -> str | None:
        """当近期情景记忆重要性累计超阈值，生成 1-3 条关系洞察，入 insight 层。"""
        recent = self.store.list_memories(user_id, layer="episodic", limit=30)
        if len(recent) < self.reflect_min_events:
            return None
        total_importance = sum(m.importance for m in recent)
        if total_importance < 3.0:
            return None
        lines = "\n".join(f"- {m.content}" for m in recent[:20])
        prompt = (
            "根据以下与用户相处的记录，提炼 1-3 条对未来陪伴有用的洞察"
            "（用户状态变化/偏好模式/关系进展/需要注意的点）。\n"
            "输出 JSON 数组 [{\"content\": 洞察(50字内), \"importance\": 1-10}]\n\n记录:\n" + lines)
        try:
            insights = await self._llm_json(prompt)
        except Exception:
            return None
        last = None
        for item in insights[:3]:
            content = item.get("content", "")[:200]
            if not content:
                continue
            vec = await self.embedder.embed(content)
            entry = MemoryEntry(
                id=uuid.uuid4().hex[:16], user_id=user_id, layer="insight",
                content=content,
                importance=min(1.0, float(item.get("importance", 7)) / 10.0),
                tags=["reflection"])
            self.store.add_memory(entry, vec)
            self.store.conn.execute(
                "INSERT OR REPLACE INTO insights (id,user_id,content,created_at,source_event_count)"
                " VALUES (?,?,?,?,?)",
                (uuid.uuid4().hex[:16], user_id, content, time.time(), len(recent)))
            self.store.conn.commit()
            last = content
        return last

    async def _llm_json(self, prompt: str) -> list:
        text = await self.llm.complete(prompt, temperature=0.2, max_tokens=600)
        import json as _json
        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end == -1:
            return []
        return _json.loads(text[start:end + 1])
