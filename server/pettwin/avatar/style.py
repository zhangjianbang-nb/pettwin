"""行为权重向量（Style Vector）：behavior_log → 3D 分身动画参数。

这是 Behavioral Twin 的核心映射层——竞品做"长得像"，我们做"动得像"。
分身引擎（desktop/）消费这份向量驱动 PetRig 骨骼动画：

  energy   0-1  综合能量水平（近 6h 活动量 / 近期基线，叠加异常压低）
  gait     0-2  步频倍率 → AnimationAction.timeScale
  tail     0-2  尾巴活跃度 → 程序摆动幅度/频率
  bounce   0-2  弹性 → 身体浮动幅度
  mood     0-1  情绪（1 兴奋 / 0 低落）→ 尾巴高度、耳朵姿态
  state    str  当前状态机目标动画（idle/walk/gallop/eat/sleep/meow）
  version  int  向量代数（前端据此做平滑过渡，跳变则直接切换）

映射规则刻意保守可解释：不做黑盒回归，每条规则对应一条可读行为事实。
"""
from __future__ import annotations

import time

# 状态机: 由"睡眠/进食/活动量"三类日志 + 日节律联合决定
_ANIM_MAP = {
    "idle": "Idle",
    "walk": "Walk",
    "gallop": "Gallop",
    "eat": "Eating",
    "sleep": "Idle_2_HeadLow",
    "meow": "Idle_2",
}

# 日节律里活动量占比低于该分位视为"这个钟点它习惯趴着"
_SLEEP_QUANTILE = 0.35


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _last_hours(tracker, pet_id: str, kind: str, hours: float, now: float) -> float:
    rows = tracker.store.conn.execute(
        "SELECT SUM(value) s FROM behavior_log WHERE pet_id=? AND kind=? AND ts>=?",
        (pet_id, kind, now - hours * 3600)).fetchone()
    return rows["s"] or 0.0


def _baseline_daily(tracker, pet_id: str, kind: str, window_days: int, now: float) -> float | None:
    base = tracker.baseline(pet_id, kind, window_days, now=now, exclude_last_hours=6)
    if base is None:
        return None
    # 基线按"每小时均值"归一（behavior_log 的 activity 值域随来源浮动）
    return base[0]


def compute_style_vector(tracker, pet_id: str, *, now: float | None = None) -> dict | None:
    """从行为日志合成风格向量。数据不足时返回 None（前端落到默认猫性格）。"""
    now = now or time.time()
    # 无任何日志 → 没有个性可言
    n = tracker.store.conn.execute(
        "SELECT COUNT(*) c FROM behavior_log WHERE pet_id=? AND kind='activity'",
        (pet_id,)).fetchone()["c"]
    if n < 3:
        return None

    act_6h = _last_hours(tracker, pet_id, "activity", 6, now)
    act_1h = _last_hours(tracker, pet_id, "activity", 1, now)
    sleep_6h = _last_hours(tracker, pet_id, "sleep", 6, now)
    eat_6h = _last_hours(tracker, pet_id, "eat", 6, now)
    meow_6h = _last_hours(tracker, pet_id, "meow", 6, now)
    base_hourly = _baseline_daily(tracker, pet_id, "activity", 14, now)

    # ---- energy: 近 6h 活动 vs 基线时均值 ----
    base_6h = base_hourly or 20.0
    ratio = act_6h / (base_6h * 6.0)
    energy = _clamp(ratio ** 0.5, 0.0, 1.0)

    # ---- 异常压低: z-score 显著偏低 → 分身也蔫 ----
    anom = tracker.anomaly_score(pet_id, "activity", now=now)
    mood = _clamp(ratio ** 0.35, 0.15, 1.0)
    if anom and anom["z"] <= -2.0:
        mood = min(mood, 0.2)
        energy = min(energy, 0.25)

    # ---- 状态机 ----
    sleep_2h = _last_hours(tracker, pet_id, "sleep", 2, now)
    if sleep_2h > 0 and act_1h < base_6h * 0.15:
        state = "sleep"
    elif eat_6h > 0 and act_1h < base_6h * 0.3:
        state = "eat"
    elif energy > 0.75 and act_1h > base_6h * 0.8:
        state = "gallop"
    elif act_1h > base_6h * 0.35:
        state = "walk"
    elif meow_6h > 0:
        state = "meow"
    else:
        state = "idle"

    # ---- 日节律: 这个钟点它习惯趴着 → 桌宠也趴 ----
    hourly = tracker.hourly_profile(pet_id, "activity", days=14)
    hour = time.localtime(now).tm_hour
    sorted_h = sorted(hourly)
    # 只有节律分布有显著起伏(max/min>2)才启用, 避免均匀数据把所有钟点都判成闲时
    if len(hourly) == 24 and hourly[hour] > 0 and max(hourly) > min(hourly) * 2:
        rank = sorted_h.index(hourly[hour]) / 23.0  # 0=最闲
        if rank < _SLEEP_QUANTILE and state in ("idle", "walk"):
            state = "sleep"

    # ---- 风格分量 ----
    gait = 0.7 + energy * 0.9          # 累了步频降
    tail = 0.5 + mood * 1.2            # 低落尾巴低垂慢摆
    bounce = 0.5 + energy * 1.1
    return {
        "energy": round(energy, 3),
        "gait": round(gait, 3),
        "tail": round(tail, 3),
        "bounce": _clamp(bounce, 0.2, 1.8),
        "mood": _clamp(mood, 0.0, 1.0),
        "state": state,
        "anim": _ANIM_MAP[state],
        "hour": hour,
        "act_1h": _clamp(act_1h, 0.0, 1e9),
        "act_6h": _clamp(act_6h, 0.0, 1e9),
        "base_6h": _clamp(base_6h, 0.0, 1e9),
        "version": int(now // 60),  # 分钟级代数, 前端平滑跟随
    }
