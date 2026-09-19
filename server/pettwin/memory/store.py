"""记忆存储层：SQLite + 向量检索。

四层记忆（SURVEY.md §6.1）：
  L1 working  — 当前会话窗口（内存态，由 agent 持有，不落库）
  L2 episodic — 情景事件条目（向量库 + 时间索引）
  L3 semantic — 语义摘要/习惯/模式（向量 + 结构化）
  L4 profile  — 用户画像硬事实（键值式 persona，KG 风格）
另有：identities（人脸/声纹嵌入）、identity_bindings、proactive_log、insights。
"""
from __future__ import annotations

import json
import math
import sqlite3
import struct
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import sqlite_vec
    _HAS_VEC = True
except ImportError:
    _HAS_VEC = False


def pack_f32(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def unpack_f32(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / na / nb


@dataclass
class MemoryEntry:
    id: str | None
    user_id: str
    layer: str            # episodic | semantic | insight
    content: str
    importance: float     # 0-1（LLM 自评 1-10 归一化，SURVEY [S1]）
    emotion: str | None = None
    valence: float | None = None
    created_at: float = field(default_factory=time.time)
    last_accessed: float = field(default_factory=time.time)
    access_count: int = 0
    decay: float = 1.0
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "user_id": self.user_id, "layer": self.layer,
            "content": self.content, "importance": self.importance,
            "emotion": self.emotion, "valence": self.valence,
            "created_at": self.created_at, "last_accessed": self.last_accessed,
            "access_count": self.access_count, "decay": self.decay,
            "tags": self.tags,
        }


SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    layer TEXT NOT NULL,
    content TEXT NOT NULL,
    importance REAL NOT NULL DEFAULT 0.5,
    emotion TEXT,
    valence REAL,
    created_at REAL NOT NULL,
    last_accessed REAL NOT NULL,
    access_count INTEGER NOT NULL DEFAULT 0,
    decay REAL NOT NULL DEFAULT 1.0,
    tags TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_mem_user_layer ON memories(user_id, layer);
CREATE INDEX IF NOT EXISTS idx_mem_created ON memories(user_id, created_at);

CREATE TABLE IF NOT EXISTS vectors (
    memory_id TEXT PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
    dim INTEGER NOT NULL,
    data BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS profile (
    user_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_at REAL NOT NULL,
    sensitive INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, key)
);

CREATE TABLE IF NOT EXISTS identities (
    user_id TEXT NOT NULL,
    modality TEXT NOT NULL,
    label TEXT NOT NULL,
    dim INTEGER NOT NULL,
    data BLOB NOT NULL,
    samples INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    PRIMARY KEY (user_id, modality, label)
);

CREATE TABLE IF NOT EXISTS identity_bindings (
    user_id TEXT NOT NULL,
    identity_id TEXT NOT NULL,
    name TEXT,
    confirmed INTEGER NOT NULL DEFAULT 0,
    cooccurrences INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL,
    PRIMARY KEY (user_id, identity_id)
);

CREATE TABLE IF NOT EXISTS proactive_log (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    ts REAL NOT NULL,
    trigger TEXT NOT NULL,
    score REAL NOT NULL,
    message TEXT NOT NULL,
    user_reaction TEXT
);

CREATE TABLE IF NOT EXISTS bindings_cross (
    user_id TEXT NOT NULL,
    face_id TEXT NOT NULL,
    voice_id TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, face_id, voice_id)
);

CREATE TABLE IF NOT EXISTS insights (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at REAL NOT NULL,
    source_event_count INTEGER NOT NULL DEFAULT 0
);
"""


class MemoryStore:
    """SQLite 持久化 + 余弦向量检索。"""

    def __init__(self, db_path: Path | str, embed_dim: int = 1024):
        self.db_path = str(db_path)
        self.embed_dim = embed_dim
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        if _HAS_VEC:
            sqlite_vec.load(self.conn)
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.close()

    # ---------- 写入 ----------

    def add_memory(self, entry: MemoryEntry, embedding: list[float] | None = None) -> MemoryEntry:
        if entry.id is None:
            entry.id = uuid.uuid4().hex[:16]
        self.conn.execute(
            "INSERT INTO memories (id,user_id,layer,content,importance,emotion,valence,"
            "created_at,last_accessed,access_count,decay,tags) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (entry.id, entry.user_id, entry.layer, entry.content, entry.importance,
             entry.emotion, entry.valence, entry.created_at, entry.last_accessed,
             entry.access_count, entry.decay, json.dumps(entry.tags, ensure_ascii=False)))
        if embedding:
            self.conn.execute(
                "INSERT OR REPLACE INTO vectors (memory_id,dim,data) VALUES (?,?,?)",
                (entry.id, len(embedding), pack_f32(embedding)))
        self.conn.commit()
        return entry

    def update_memory(self, memory_id: str, content: str | None = None,
                      embedding: list[float] | None = None, **fields):
        sets, vals = [], []
        if content is not None:
            sets.append("content=?"); vals.append(content)
        for k in ("importance", "emotion", "valence", "decay"):
            if k in fields:
                sets.append(f"{k}=?"); vals.append(fields[k])
        if sets:
            vals.append(memory_id)
            self.conn.execute(f"UPDATE memories SET {','.join(sets)} WHERE id=?", vals)
        if embedding:
            self.conn.execute(
                "INSERT OR REPLACE INTO vectors (memory_id,dim,data) VALUES (?,?,?)",
                (memory_id, len(embedding), pack_f32(embedding)))
        self.conn.commit()

    def delete_memory(self, memory_id: str):
        self.conn.execute("DELETE FROM vectors WHERE memory_id=?", (memory_id,))
        self.conn.execute("DELETE FROM memories WHERE id=?", (memory_id,))
        self.conn.commit()

    def list_memories(self, user_id: str, layer: str | None = None,
                      limit: int = 200) -> list[MemoryEntry]:
        if layer:
            rows = self.conn.execute(
                "SELECT * FROM memories WHERE user_id=? AND layer=? ORDER BY created_at DESC LIMIT ?",
                (user_id, layer, limit)).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM memories WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
                (user_id, limit)).fetchall()
        return [self._row_to_entry(r) for r in rows]

    # ---------- 画像（L4，键值 persona [S9]，KG 式硬事实 [S11]）----------

    def set_profile(self, user_id: str, key: str, value: str, sensitive: bool = False):
        self.conn.execute(
            "INSERT INTO profile (user_id,key,value,updated_at,sensitive) VALUES (?,?,?,?,?) "
            "ON CONFLICT(user_id,key) DO UPDATE SET value=excluded.value,"
            "updated_at=excluded.updated_at,sensitive=excluded.sensitive",
            (user_id, key, value, time.time(), int(sensitive)))
        self.conn.commit()

    def get_profile(self, user_id: str, include_sensitive: bool = False) -> dict[str, str]:
        if include_sensitive:
            rows = self.conn.execute(
                "SELECT key,value FROM profile WHERE user_id=?", (user_id,)).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT key,value FROM profile WHERE user_id=? AND sensitive=0", (user_id,)).fetchall()
        return {r["key"]: r["value"] for r in rows}

    def delete_profile_key(self, user_id: str, key: str) -> bool:
        """被遗忘权 [S49]。"""
        cur = self.conn.execute("DELETE FROM profile WHERE user_id=? AND key=?", (user_id, key))
        self.conn.commit()
        return cur.rowcount > 0

    def forget_user(self, user_id: str):
        """完全遗忘（GDPR 级）。"""
        self.conn.execute(
            "DELETE FROM vectors WHERE memory_id IN "
            "(SELECT id FROM memories WHERE user_id=?)", (user_id,))
        for table in ("memories", "profile", "identities", "bindings_cross",
                      "identity_bindings", "proactive_log", "insights"):
            self.conn.execute(f"DELETE FROM {table} WHERE user_id=?", (user_id,))
        self.conn.commit()

    # ---------- 检索（三维打分 [S1] + 情绪加权 [S34]）----------

    def search(self, user_id: str, query_embedding: list[float], top_k: int = 8,
               now: float | None = None, half_life_hours: float = 72.0,
               emotion_boost: float = 0.15) -> list[tuple[MemoryEntry, float]]:
        now = now or time.time()
        rows = self.conn.execute(
            "SELECT * FROM memories WHERE user_id=? AND layer IN ('episodic','semantic','insight')",
            (user_id,)).fetchall()
        if not rows:
            return []
        scored = []
        for row in rows:
            entry = self._row_to_entry(row)
            sim = self._similarity(row["id"], query_embedding)
            if sim <= 0.01:
                continue
            score = self._score(entry, sim, now, half_life_hours, emotion_boost)
            scored.append((entry, score))
        scored.sort(key=lambda t: t[1], reverse=True)
        top = scored[:top_k]
        if top:
            self._touch_access([e.id for e, _s in top])
        return top

    def _score(self, entry: MemoryEntry, relevance: float, now: float,
               half_life_hours: float, emotion_boost: float) -> float:
        hours = max(0.0, (now - entry.created_at) / 3600.0)
        recency = 0.5 ** (hours / max(half_life_hours, 0.1))
        importance = min(1.0, max(0.05, entry.importance)) * entry.decay
        bonus = emotion_boost if (entry.emotion and entry.emotion != "neutral") else 0.0
        return relevance * (0.6 + 0.4 * recency) * (0.5 + importance) + bonus

    def _similarity(self, memory_id: str, query_embedding: list[float]) -> float:
        if not query_embedding:
            return 0.0
        row = self.conn.execute(
            "SELECT data FROM vectors WHERE memory_id=?", (memory_id,)).fetchone()
        if not row:
            return 0.0
        vec = unpack_f32(row["data"])
        return cosine(vec, query_embedding)

    def _row_to_entry(self, row: sqlite3.Row) -> MemoryEntry:
        return MemoryEntry(
            id=row["id"], user_id=row["user_id"], layer=row["layer"],
            content=row["content"], importance=row["importance"],
            emotion=row["emotion"], valence=row["valence"],
            created_at=row["created_at"], last_accessed=row["last_accessed"],
            access_count=row["access_count"], decay=row["decay"],
            tags=json.loads(row["tags"] or "[]"))

    def _touch_access(self, ids: list[str]):
        if not ids:
            return
        now = time.time()
        marks = ",".join("?" for _i in ids)
        self.conn.execute(
            f"UPDATE memories SET access_count=access_count+1, last_accessed=? "
            f"WHERE id IN ({marks})", [now] + ids)
        self.conn.commit()

    # ---------- 遗忘曲线（[S3] MemoryBank：重要性高/访问多衰减慢）----------

    def apply_decay(self, user_id: str, half_life_days: float = 14.0):
        now = time.time()
        rows = self.conn.execute(
            "SELECT id, created_at, importance, access_count FROM memories WHERE user_id=?",
            (user_id,)).fetchall()
        updates = []
        for r in rows:
            age_days = (now - r["created_at"]) / 86400.0
            resistance = 0.5 + 0.5 * min(1.0, r["importance"]) + min(0.5, r["access_count"] / 20.0)
            effective_half_life = half_life_days * resistance
            retention = max(0.05, 0.5 ** (age_days / max(effective_half_life, 0.5)))
            updates.append((retention, r["id"]))
        self.conn.executemany("UPDATE memories SET decay=? WHERE id=?", updates)
        self.conn.commit()

    def consolidate_candidates(self, user_id: str, older_than_hours: float,
                               limit: int = 50) -> list[MemoryEntry]:
        """取待巩固的旧情景记忆（夜间批处理 [S15] 输入）。"""
        cutoff = time.time() - older_than_hours * 3600
        rows = self.conn.execute(
            "SELECT * FROM memories WHERE user_id=? AND layer='episodic' AND created_at<? "
            "ORDER BY created_at ASC LIMIT ?",
            (user_id, cutoff, limit)).fetchall()
        return [self._row_to_entry(r) for r in rows]
