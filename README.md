# PetTwin 🐾

**给真实宠物的行为记忆分身 —— 它记得你家猫的习惯、规律和病史，并在该开口的时候提醒你。**

[![CI](https://github.com/zhangjianbang-nb/pettwin/actions/workflows/ci.yml/badge.svg)](../../actions)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

English | 中文

---

## Why PetTwin?

宠物 AI 产品都停在"长得像"（照片→卡通形象）或"实时状态"（摄像头映射）。
**没有人做行为层**：

> 普通 AI 猫：会卖萌。
> **PetTwin：知道"上次换粮"后它蔫了 3 天、本周活动量比上周低 18%、疫苗下周一到期——并会在合适的时机告诉你，而不是每次打开都复读。**

差异化 = **Behavior Memory**（四层记忆引擎的宠物域实例）+ **可解释异常检测**（滑动基线 z-score，无需训练）+ **克制的主动洞察**（每日上限 + 24h 去重 + 回访机制）。

## Features (v0.1)

| 模块 | 能力 |
|---|---|
| 🐱 **Pet Identity** | 照片注册 → DINOv2 个体识别（多宠家庭"这是哪只"）；无 torch 环境自动降级视觉哈希 |
| 🧠 **Event Memory** | 四层记忆引擎（工作/情景/语义/画像）：医疗、饮食、行为事件入库，重要性门控 + 艾宾浩斯遗忘 + 中文 bigram 检索 |
| 📈 **Behavior Analytics** | 行为日志（activity/eat/sleep/litter/meow/weight）→ 滑动基线 → **z-score 异常分**（活动骤降即警报）+ 24h 日节律 + 周同比 |
| 🔔 **Insight Engine** | 三类触发（异常警报 / 疫苗驱虫日程 / "换粮 3 天了，适应吗"回访）+ **每日上限与去重**（不刷屏） |
| 📰 **Weekly Report** | 行为统计 + 事件回顾 → LLM 自然语言周报（无 LLM 时模板兜底） |
| 🗑 **Right to be forgotten** | 一键删除某只宠物的全部数据 |

## Quick Start

```bash
cd server
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
# 个体识别增强（可选，推荐）：
pip install -e ".[dino]"

# 可选 LLM（周报自然语言；不配则模板周报）
export PETTWIN_LLM_BASE_URL="http://127.0.0.1:10450/v1"
export PETTWIN_LLM_MODEL="glm-5.3-flash"

uvicorn pettwin.main:app --port 8801
```

### 60 秒体验

```bash
# 注册你的猫
curl -X POST localhost:8801/v1/pets -H 'Content-Type: application/json' \
  -d '{"name": "咪咪", "species": "cat", "birthday": "2023-05-01"}'
# → {"pet_id": "ab12cd34..."}

# 记录 14 天活动量后，今天骤降：
curl -X POST localhost:8801/v1/behavior -H 'Content-Type: application/json' \
  -d '{"pet_id": "ab12..", "kind": "activity", "value": 30}'

# 问它今天该关注什么：
curl localhost:8801/v1/insights/ab12..
# → [{"level": "alert", "title": "activity 异常偏低", "detail": "最近值 30.0，基线均值 101.0（z=-16.0）..."},
#    {"level": "schedule", "title": "已到期：年度疫苗"}]
```

## Architecture

```
采集（手工事件/摄像头帧/CSV 活动量）
        │
PetTwin Server (FastAPI)
├── pet_identity   DINOv2 个体识别（哈希降级）
├── memory         SoulSync 四层记忆引擎（复用 563 行，实测 24/24）
├── behavior       滑动基线 / z-score 异常 / 日节律 / 周同比
├── insight        异常警报 + 日程 + 回访（每日上限+24h 去重）
└── report         LLM 周报（模板兜底）
        │
(V2) 行为权重向量 → PetRig 3D 桌面分身（参数化四足骨架，已有 cat-rig 原型）
```

## 桌面 3D 分身（v0.3）

```bash
# 1) 起 server
cd server && uvicorn pettwin.main:app --port 8797
# 2) 桌面页（任意静态服务指向 desktop/，或直接 file:// 亦需 server 地址参数）
cd desktop && python3 -m http.server 8798
# 3) 浏览器打开
open "http://localhost:8798/?pet=<pet_id>&api=http://localhost:8797&name=橘子&color=orange"
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `pet` | demo | pet_id（无该 id 或无行为数据 → 演示猫性格） |
| `api` | 同源 | PetTwin server 地址 |
| `poll` | 30 | 轮询秒数（最小 5） |
| `name` | pet_id | HUD 显示名 |
| `color` | orange | orange/black/gray/cow |

风格向量：`GET /v1/avatar/{pet_id}/style` 返回 `energy/gait/tail/bounce/mood/state/anim`——
近 6h 活动 vs 基线 → 能量与情绪；异常低活动 → 蔫；睡觉/进食日志 → 姿态切换；
日节律显示"这个钟点它习惯趴着" → 分身也趴。前端逐帧 lerp 平滑跟随，无跳变。

## Roadmap

- [x] v0.1 — 行为记忆服务端（识别/记忆/统计/洞察/周报）
- [x] v0.2 — 摄像头接入（活动量自动统计：MOG2 背景减除 + YOLO 可选增强，常驻源 RTSP/USB）
- [x] v0.3 — 桌面 3D 分身（行为权重向量驱动 PetRig：energy/gait/tail/bounce/mood → 动画混算+程序尾巴，30s 轮询 API）
- [x] v0.4 — 叫声语义（零依赖声学特征+规则分类+每宠基线自学习，异常叫声健康预警）+ 多宠社交（共处陪伴分+互动事件）
- [x] v0.5 — 姿势分析（bbox 几何 6 类动作 + zones 标注区：**趴键盘 → 桌宠也趴键盘**，DLC 关键点留增强位）
- [x] v0.6 — 安卓 App（WebView 壳：分身/摄像头推帧推 bbox/叫声识别/看板，APK 已构建）
- [ ] v0.7 — 通知推送 + 多宠摄像头自动分轨 + 颜色/花纹个性化（照片→毛色）
- [x] v0.5 — 姿势分析（bbox 几何 6 类动作 + zones 标注区：**趴键盘 → 桌宠也趴键盘**，DLC 关键点留增强位）
- [x] v0.6 — 安卓 App（WebView 壳：分身/摄像头推帧推 bbox/叫声识别/看板，APK 已构建）
- [ ] v0.7 — 通知推送 + 多宠摄像头自动分轨 + 颜色/花纹个性化（照片→毛色）

## 设计文档

- [docs/DESIGN.md](docs/DESIGN.md) — 与竞品的本质差异、三层资产、记忆分层映射
- 竞品与付费验证研究见调研报告（Behavioral Twin 定位，30 天验证方案）

## License

Apache-2.0
