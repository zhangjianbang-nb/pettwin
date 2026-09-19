"""PetTwin 配置。环境变量优先；记忆引擎参数沿用 SoulSync 验证值。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _int_env(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


def _float_env(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


@dataclass
class Settings:
    llm_base_url: str = field(
        default_factory=lambda: os.environ.get("PETTWIN_LLM_BASE_URL", ""))
    llm_api_key: str = field(
        default_factory=lambda: os.environ.get("PETTWIN_LLM_API_KEY", "EMPTY"))
    llm_model: str = field(
        default_factory=lambda: os.environ.get("PETTWIN_LLM_MODEL", "glm-5.3-flash"))

    data_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("PETTWIN_DATA_DIR", "~/.pettwin")).expanduser())
    db_path: Path = field(default=None)

    # Embedding（不配则哈希降级）
    embed_base_url: str = field(
        default_factory=lambda: os.environ.get("PETTWIN_EMBED_BASE_URL", ""))
    embed_model: str = field(
        default_factory=lambda: os.environ.get("PETTWIN_EMBED_MODEL", "bge-m3"))
    embed_fallback_hash: bool = field(
        default_factory=lambda: os.environ.get("PETTWIN_EMBED_FALLBACK", "1") == "1")

    # 记忆引擎（SoulSync 验证值）
    memory_importance_gate: float = field(
        default_factory=lambda: _float_env("PETTWIN_MEM_IMPORTANCE_GATE", 0.45))
    memory_recall_top_k: int = field(
        default_factory=lambda: _int_env("PETTWIN_MEM_TOPK", 8))
    reflection_interval_minutes: int = field(
        default_factory=lambda: _int_env("PETTWIN_REFLECT_MIN", 240))
    reflection_min_events: int = field(
        default_factory=lambda: _int_env("PETTWIN_REFLECT_MIN_EVENTS", 6))

    # 个体识别
    pet_sim_threshold: float = field(
        default_factory=lambda: _float_env("PETTWIN_PET_SIM", 0.82))
    embed_dim: int = field(default_factory=lambda: _int_env("PETTWIN_EMBED_DIM", 256))

    # 行为基线与异常
    baseline_window_days: int = field(
        default_factory=lambda: _int_env("PETTWIN_BASELINE_DAYS", 14))
    anomaly_z: float = field(default_factory=lambda: _float_env("PETTWIN_ANOMALY_Z", 2.0))
    anomaly_min_points: int = field(
        default_factory=lambda: _int_env("PETTWIN_ANOMALY_MIN_POINTS", 5))

    # 主动报告
    proactive_enabled: bool = field(
        default_factory=lambda: os.environ.get("PETTWIN_PROACTIVE", "1") == "1")
    revisit_min_gap_hours: int = field(
        default_factory=lambda: _int_env("PETTWIN_REVISIT_GAP_H", 24))
    daily_report_cap: int = field(
        default_factory=lambda: _int_env("PETTWIN_DAILY_CAP", 3))

    server_host: str = field(
        default_factory=lambda: os.environ.get("PETTWIN_HOST", "0.0.0.0"))
    server_port: int = field(default_factory=lambda: _int_env("PETTWIN_PORT", 8801))

    def __post_init__(self):
        if self.db_path is None:
            self.db_path = self.data_dir / "pettwin.db"
        self.data_dir.mkdir(parents=True, exist_ok=True)


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
