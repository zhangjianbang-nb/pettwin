"""宠物个体识别（Pet Identity）：照片 → embedding → 余弦识别哪只猫/狗。

模型策略：
- 可选依赖 torch+torchvision (DINOv2 ViT-S/14)：精度高，多宠区分可靠
- 缺依赖时降级为确定性视觉哈希（pHash 风格）——同一宠物近照可聚，仅够 v0.1 测试

嵌入统一 256 维（DINOv2 384 维 PCA 截断 / 哈希直接 256），余弦阈值 pet_sim_threshold。
"""
from __future__ import annotations

import threading

import numpy as np

from pettwin.config import get_settings
from pettwin.memory.store import MemoryStore


class PetIdentityEngine:
    def __init__(self, store: MemoryStore):
        self.store = store
        s = get_settings()
        self.sim_threshold = s.pet_sim_threshold
        self._model = None
        self._lock = threading.Lock()

    def is_available(self) -> bool:
        try:
            import torch  # noqa: F401
            import torchvision  # noqa: F401
            return True
        except ImportError:
            return False

    def _get_model(self):
        if self._model is None:
            with self._lock:
                if self._model is None:
                    import torch
                    self._model = torch.hub.load(
                        "facebookresearch/dinov2", "dinov2_vits14")
                    self._model.eval()
        return self._model

    def embed_image(self, image_bgr: np.ndarray) -> list[float] | None:
        """BGR uint8 → 256 维归一化嵌入；无法解码/无内容返回 None。"""
        if image_bgr is None or image_bgr.size == 0:
            return None
        if self.is_available():
            return self._embed_dino(image_bgr)
        return self._embed_hash(image_bgr)

    def _embed_dino(self, image_bgr: np.ndarray) -> list[float] | None:
        try:
            import cv2
            import torch
            from torchvision import transforms
            img = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
            tf = transforms.Compose([
                transforms.ToPILImage(),
                transforms.Resize((224, 224)),
                transforms.ToTensor(),
                transforms.Normalize(mean=[0.485, 0.456, 0.406],
                                     std=[0.229, 0.224, 0.225]),
            ])
            x = tf(img).unsqueeze(0)
            with torch.no_grad():
                feat = self._get_model()(x)
            vec = feat.squeeze().numpy().astype(np.float32)
            vec = vec / (np.linalg.norm(vec) + 1e-9)
            # DINOv2 vits=384 维 → 取前 256 维（PCA 替代，v0.1 够用）
            return vec[:256].tolist()
        except Exception:
            return self._embed_hash(image_bgr)

    def _embed_hash(self, image_bgr: np.ndarray) -> list[float]:
        """8x8 均值哈希 × 灰度梯度 → 256 维（降级模式）。"""
        import cv2
        gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, (16, 16)).astype(np.float32)
        v = (small - small.mean()).flatten()
        norm = float(np.linalg.norm(v)) + 1e-9
        return (v / norm).tolist()
