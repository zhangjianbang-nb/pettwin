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
