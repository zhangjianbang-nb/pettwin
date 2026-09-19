"""宠物档案（Pet Profile）：多宠家庭的注册/识别/画像。

identities 表复用 SoulSync schema（modality='pet_face'）；
profile 表按 pet_id 存硬事实（生日/品种/绝育/疫苗）。
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

import numpy as np

from pettwin.config import get_settings
from pettwin.memory.store import MemoryStore
from pettwin.perception.pet_id import PetIdentityEngine


@dataclass
class PetMatch:
    pet_id: str | None
    name: str | None
    similarity: float
    is_new: bool


class PetProfileManager:
    def __init__(self, store: MemoryStore, engine: PetIdentityEngine):
        self.store = store
        self.engine = engine

    # ---------- 注册 ----------

    def register(self, pet_id: str, image_bgr: np.ndarray) -> PetMatch:
        """为指定宠物录入一张识别照（多次采样均值合并）。"""
        vec = self.engine.embed_image(image_bgr)
        if vec is None:
            raise ValueError("no usable image")
        arr = np.asarray(vec, dtype=np.float32)
        row = self.store.conn.execute(
            "SELECT data, samples FROM identities WHERE user_id=? AND modality='pet_face' AND label=?",
            (pet_id, "primary")).fetchone()
        if row:
            stored = np.frombuffer(row["data"], dtype=np.float32)
            n = row["samples"]
            merged = stored * n + arr
            merged = merged / (n + 1)
            merged = merged / (np.linalg.norm(merged) + 1e-9)
            self.store.conn.execute(
                "UPDATE identities SET data=?, samples=? WHERE user_id=? AND modality='pet_face' AND label=?",
                (merged.tobytes(), n + 1, pet_id, "primary"))
            self.store.conn.commit()
            return PetMatch(pet_id, self.get_name(pet_id), 1.0, is_new=False)
        self.store.conn.execute(
            "INSERT INTO identities (user_id,modality,label,dim,data,samples,created_at)"
            " VALUES (?,'pet_face','primary',?,?,1,?)",
            (pet_id, len(vec), arr.tobytes(), time.time()))
        self.store.conn.commit()
        return PetMatch(pet_id, self.get_name(pet_id), 1.0, is_new=True)

    # ---------- 识别 ----------

    def identify(self, image_bgr: np.ndarray) -> PetMatch:
        """照片里是哪只？→ 全库比对。低于阈值返回 is_new。"""
        vec = self.engine.embed_image(image_bgr)
        if vec is None:
            raise ValueError("no usable image")
        rows = self.store.conn.execute(
            "SELECT user_id, data FROM identities WHERE modality='pet_face'").fetchall()
        best, best_sim = None, -1.0
        for r in rows:
            stored = np.frombuffer(r["data"], dtype=np.float32)
            if len(stored) != len(vec):
                continue
            sim = float(np.dot(stored, np.asarray(vec, dtype=np.float32)))
            if sim > best_sim:
                best, best_sim = r["user_id"], sim
        if best is None or best_sim < get_settings().pet_sim_threshold:
            return PetMatch(None, None, max(best_sim, 0.0), is_new=True)
        return PetMatch(best, self.get_name(best), best_sim, is_new=False)

    # ---------- 档案 ----------

    def create_pet(self, name: str, species: str = "cat", breed: str = "",
                   birthday: str = "", photo_bgr: np.ndarray | None = None) -> str:
        pet_id = uuid.uuid4().hex[:12]
        self.store.set_profile(pet_id, "name", name)
        self.store.set_profile(pet_id, "species", species)
        if breed:
            self.store.set_profile(pet_id, "breed", breed)
        if birthday:
            self.store.set_profile(pet_id, "birthday", birthday)
        if photo_bgr is not None:
            self.register(pet_id, photo_bgr)
        return pet_id

    def get_name(self, pet_id: str) -> str | None:
        v = self.store.get_profile(pet_id).get("name")
        return v

    def get_profile(self, pet_id: str) -> dict:
        return self.store.get_profile(pet_id)

    def list_pets(self) -> list[dict]:
        rows = self.store.conn.execute(
            "SELECT DISTINCT user_id FROM identities WHERE modality='pet_face'").fetchall()
        out = []
        for r in rows:
            pid = r["user_id"]
            prof = self.store.get_profile(pid)
            if prof:
                out.append({"pet_id": pid, **prof})
        return out

    def forget_pet(self, pet_id: str):
        """被遗忘权（宠物数据同样高敏）。"""
        self.store.forget_user(pet_id)
