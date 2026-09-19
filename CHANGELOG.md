# Changelog

## [0.2.0] — 2026-09-19

### Added
- **摄像头接入**（v0.2）：
  - `POST /v1/camera/ingest_video`：上传视频片段 → 每秒活动量 → behavior_log
  - `POST /v1/camera/ingest_frame`：App 单帧推流（自管采样节奏）
  - `POST /v1/camera/source` + `DELETE /v1/camera/source/{pet_id}` + `GET /v1/camera/sources`：
    常驻源（RTSP/USB）后台采样线程管理
- **活动量定义**：MOG2 背景减除运动像素占比（0-100），可跨摄像头比较、可解释、零模型依赖
- **YOLO 可选增强**：装 ultralytics 后自动只统计宠物区域（排除人走动误计）
- 活动量写入 kind=activity source=camera，v0.1 的基线/异常/周报**零改造联动**
- DeepLabCut 关键点（行为姿势分析）留 v0.3

### Notes
- 测试 8 → 10（合成视频 ingest 联动/坏视频 422）

# Changelog

## [0.1.0] — 2026-09-19

首个公开版本。

### Added
- Pet Identity：DINOv2 个体识别（torch 可选，哈希降级），多宠注册/识别/遗忘
- Event Memory：SoulSync 四层记忆引擎宠物域复用（重要性门控+遗忘曲线+中文 bigram 检索）
- Behavior：行为日志、滑动基线 z-score 异常分、24h 日节律、周同比
- Insight：异常警报/日程到期/回访三触发 + 每日上限 + 24h 去重
- Weekly Report：LLM 自然语言周报（模板兜底）
- 15 REST 端点，8 测试全绿（e2e 全旅程）
