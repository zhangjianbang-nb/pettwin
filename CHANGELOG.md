# Changelog

## [0.5.0] — 2026-09-19

### Added
- **姿势分析**（v0.5）——Magic Moment 精修：
  - `perception/pose.py`：bbox 几何序列 → 6 类标志动作
    lying（趴卧 宽高比≥1.5）/ stretched（伸懒腰 ≥2.2）/ sitting（端坐 0.8-1.3 稳定）/
    jumping（中心高度 1s 内突变 >1 身高）/ on_object（趴卧+命中标注区）/ moving（位移≥1 身位）
  - `PoseWatcher`：流式滑窗（2.5s/4 帧）+ 同动作 3 分钟去重
  - 分层设计：L1 零依赖 bbox 几何（App 框选/固定区/YOLO bbox 均可喂）；L2 预留
    DeepLabCut/YOLO-pose 关键点（规则引擎不变，精度更高）
  - `POST /v1/pose/{pet_id}`：bbox 窗口 + zones（keyboard/monitor/bed…）→ 动作事件；
    同时写 behavior_log(kind=pose) + episodic 事件（自动 observe 标签→回访钩子）
  - `GET /v1/pose/{pet_id}/recent`：近期动作统计（周报输入）
- **分身联动**：style 向量姿势覆盖——10 分钟内 on_object → 分身切趴下动画（Idle_2_HeadLow，
  bounce 降为 0.4）、jumping → Gallop 撒欢、stretched → Jump_ToIdle；桌面页 HUD 显示
  "🎯 on_object @ keyboard" 徽章
- BEHAVIOR_TYPES / API kind 校验扩展 pose

### Notes
- 测试 24 → 35（6 类动作判定/watcher 去重/API/姿势覆盖 3 场景）
- 端到端：bbox 窗口 → on_object:keyboard 事件 → diary observe 链路 → style 覆盖
  （state=sleep/anim=Idle_2_HeadLow/pose=on_object:keyboard）→ 桌面页徽章+趴下动画，零 console 错误

## [0.4.0] — 2026-09-19

### Added
- **叫声语义**（v0.4）：
  - `POST /v1/meow/{pet_id}`：wav 上传 → 声学特征（时长/能量/过零率/基频自相关/频谱质心）→ 场景语义
    分类：hunger（连叫讨食）/ greeting（打招呼）/ distress（长叫高基频=痛怕）/ playful / other
  - 零重依赖：numpy + 标准库 wave，无 librosa/torch；合成叫声 4/4 分类全对
  - **基线自学习**：每宠历史特征库（kv_store），新叫声与自身分布比 z-score ≥2.5 → anomalous
    （健康预警输入）；样本 <8 不判
  - `GET /v1/meow/{pet_id}/history` + `/counts`：叫声历史与语义分布（周报输入）
- **多宠社交**（v0.4）：
  - `SocialGraph`：60s 槽共处判定（两只都有 camera 活动记录）→ 陪伴分 0-1
    （共处槽 / min(A活动槽, B活动槽)）+ 共处分钟
  - 互动事件表 social_interaction（play/groom/fight/share_spot/other，pet 对规范化排序）
  - `POST /v1/social/{a}/{b}/interaction` + `GET /v1/social/{a}/{b}`
- `kv_store` 通用 KV 表；`MeowEngine` 门面（分类+基线+历史 100 条）

### Notes
- 测试 16 → 24（特征/分类/基线异常/历史/陪伴分/互动校验/API 全套）
- 端到端：真实 HTTP 上传合成 wav → distress + anomalous z=23.3 → 社交陪伴分 1.0 + 2 互动事件

## [0.3.0] — 2026-09-19

### Added
- **桌面 3D 分身**（v0.3）：
  - `GET /v1/avatar/{pet_id}/style`：行为权重向量端点——behavior_log → 3D 动画参数
  - `server/pettwin/avatar/style.py`：风格向量合成（energy/gait/tail/bounce/mood/state）
    - 近 6h 活动量 vs 基线 → energy（sqrt 压缩）+ mood
    - 异常 z-score ≤ -2 → 强制蔫（mood≤0.2, energy≤0.25）
    - 状态机：sleep/eat/gallop/walk/meow/idle（睡眠窗口 2h + 低活动门槛）
    - 日节律：该钟点活动占比位于最闲 35% 且分布显著起伏 → 趴下
  - `desktop/`：桌面分身 web 页（PetRig cat_rig_v3.glb，51 骨骼 / 12 动画）
    - 三层渲染：模型归一化 + Fur Shell 毛发壳（5 层蒙皮外推）+ 风格向量驱动
    - 30s 轮询 style 向量，逐帧 lerp 平滑跟随（无跳变）
    - 尾巴 8 节程序摆动 × mood、耳朵微动、随真实时间变光
    - URL 参数：pet/api/name/color(orange|black|gray|cow)/poll
    - `desktop/verify_e2e.cjs`：浏览器验收脚本（模型/动画数/毛发/API/姿态断言）

### Fixed
- `metrics.py` 残损 token：anomaly dict 里 `abs(z)` 误写为 `round(z)`（行为方向判断反转）
- 基线方差下限：无波动基线的 z-score 不再放大数万倍（下限=均值 10%）
- 日节律均匀数据误判"闲时"：分布 max/min>2 才启用
- 睡眠判定窗口 6h→2h，避免无睡眠日志的蔫猫被误判睡觉

### Notes
- 测试 10 → 16（风格向量 6 场景：空数据/活跃/蔫/吃饭/睡觉/API 端点）
- server 新增 CORS 中间件（桌面页可分离部署，只读开放）
- 端到端验收：headless 浏览器 mock 数据全链路（模型 12 动画 + 5 层毛发 + API live + 蔫猫吃食姿态 + 零 console 错误）

## [0.2.0] — 2026-09-19

### Added
- **摄像头接入**（v0.2）：
  - `POST /v1/camera/ingest_video`：上传视频片段 → 每秒活动量 → behavior_log
  - `POST /v1/camera/ingest_frame`：App 单帧推流（自管采样节奏）
  - `POST /v1/camera/source` + `DELETE /v1/camera/source/{pet_id}` + `GET /v1/camera/sources`：
    常驻源（RTSP/USB）后台采样线程管理
- **活动量定义**：MOG2 背景减除 运动像素占比（0-100），可跨摄像头比较、可解释、零模型依赖
- **YOLO 可选增强**：装 ultralytics 后自动只统计宠物区域（排除人走动误计）
- 活动量写入 kind=activity source=camera，v0.1 的基线/异常/周报**零改造联动**
- DeepLabCut 关键点（行为姿势分析）留 v0.3

### Notes
- 测试 8 → 10（合成视频 ingest 联动/坏视频 422）

## [0.1.0] — 2026-09-19

首个公开版本。

### Added
- Pet Identity：DINOv2 个体识别（torch 可选，哈希降级），多宠注册/识别/遗忘
- Event Memory：SoulSync 四层记忆引擎宠物域复用（重要性门控+遗忘曲线+中文 bigram 检索）
- Behavior：行为日志、滑动基线 z-score 异常分、24h 日节律、周同比
- Insight：异常警报/日程到期/回访三触发 + 每日上限 + 24h 去重
- Weekly Report：LLM 自然语言周报（模板兜底）
- 15 REST 端点，8 测试全绿（e2e 全旅程）
