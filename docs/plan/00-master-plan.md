# Video-Localizer 总体实现计划

> 最后更新: 2026-06-07

## 1. 项目现状

| 维度 | 状态 |
|------|------|
| 运行环境 | Python 3.14 + FFmpeg 8.1.1 + Docker |
| Docker | 已安装，用于运行 Web 服务 |
| 依赖包 | 29 个包已安装 |
| ASR 模型 | faster-whisper large-v3 (~3GB) 已下载 |
| 源代码 | **零** — 所有模块目录为空 |
| 设计文档 | 6 份 (`docs/00-05`) 已完成 |
| Git | 已 `git init`，未提交 |

## 2. 部署架构

**默认 Docker，也支持直接运行。**

```
方式一 (默认): docker compose up -d
方式二:        python app.py  (需 Python 3.13+ + FFmpeg)

┌─ Docker Container (python:3.13-slim + FFmpeg) ─────┐
│  FastAPI (:8000) + Jinja2 + HTMX                     │
│  挂载: ./:/app, <media>:/media, <models>:/models     │
└──────────────────────────────────────────────────────┘
        │ GPU 任务 → HTTP host.docker.internal:9001
        ▼
┌─ 宿主机 Worker ─────────────────────────────────────┐
│  python worker.py (:9001)                            │
│  faster-whisper CUDA + Edge-TTS                      │
└──────────────────────────────────────────────────────┘

## 3. 前端方案

**Jinja2 + HTMX**：零构建、服务端渲染、htmx 一行 `<script>` 引入即可处理文件上传、进度轮询、结果下载。

| 功能 | 实现方式 |
|------|----------|
| 文件上传 | `<form>` multipart POST |
| ASR 进度 | htmx polling `/api/task/{id}` |
| 翻译对照 | htmx swap 局部刷新 |
| 结果下载 | `<a download>` + `/api/output/{filename}` |

## 4. 测试素材

放在 `E:\Media\input\`，按维度覆盖，不绑定具体文件名：

| 维度 | 用途 |
|------|------|
| 短素材 (~5-15min, H.264+AAC) | 日常快速验证 |
| 中等素材 (~15-30min) | ASR 耗时、翻译批处理 |
| 长素材 (~30-60min) | 压力测试、性能基线 |
| 无音轨素材 | 边界错误处理 |
| 非标准编码 (AV1/VP9/HEVC) | 编码兼容性验证 |
| 多音轨素材 | 音轨切换测试 |
| 内嵌字幕素材 | 字幕探测/提取测试 |

## 5. 开发阶段

```
Stage 1: Web 框架 + 配置 + Docker  → 页面可访问, 配置可加载
Stage 2: FFprobe 探头探测
Stage 3: 流提取
Stage 4: 添加软字幕
Stage 5: 切换默认轨道
Stage 6: 字幕格式转换 (SRT↔ASS↔WebVTT)
Stage 7: 音频轨管理 + 音画同步    ← 含 offset/speed 调整
Stage 8: 硬字幕烧录
Stage 9: ASR 引擎 (faster-whisper) + GPU Worker
Stage 10: 翻译引擎 (LLM API)
Stage 11: 字幕生成器
Stage 12: TTS 引擎 (Edge-TTS)
Stage 13: 音频对齐
Stage 14: 端到端流水线
```

### 依赖关系

```
Stage 1
  ↓
Stage 2 → Stage 3 → Stage 4 → Stage 5
                              ↓
Stage 6                      Stage 7 → Stage 8
                                          ↓
Stage 3 ──→ Stage 9 → Stage 10 → Stage 11
                                        ↓
                Stage 12 → Stage 13 → Stage 14
```

---

## 6. 各阶段概要

### Stage 1: Web 框架 + 配置 + Docker

**目标**: Docker 容器可启动，Web 首页可访问，配置可加载。

| 输出 | 说明 |
|------|------|
| `Dockerfile` | python:3.13-slim + FFmpeg + 项目依赖 |
| `docker-compose.yml` | web 服务 + volume 挂载 + `.env` 变量 |
| `.env.example` | 环境变量说明 (提交 Git) |
| `.env` | 个人环境变量 (gitignored) |
| `config/__init__.py` | Settings 数据类 + 两层配置加载 |
| `config/requirements.py` | 硬件检测 + 5 档配置档匹配 + 最低要求校验 |
| `config/settings.yaml` | 通用默认值 (提交 Git, 5 档 profiles: ultra/high/medium/low/cpu) |
| `config/settings.local.yaml` | 个人覆盖 (gitignored) |
| `app.py` | FastAPI + Jinja2 + 静态文件 |
| `templates/base.html` + `index.html` | 基础布局 + 首页 |
| `static/css/style.css` | 基础样式 |
| `requirements.txt` | 完整依赖清单 (含 fastapi, uvicorn, httpx 等) |

**验证**: `docker compose up -d` → `localhost:8000` 显示首页，`/docs` 显示 Swagger

---

### Stage 2-5: Phase 1 核心 FFmpeg 层

每个 Stage 遵循相同模式：

| 层 | 产出 |
|----|------|
| `core/` | 核心函数 (probe, extract, mux) |
| `api/` | REST 端点 (薄封装，调核心函数) |
| `templates/` | Web 页面 (表单 + 结果展示) |
| `tests/` | pytest 单元测试 |

### Stage 7: 音频轨管理 + 音画同步 ← 新增

**核心模块**: `core/mux.py` (追加), `core/sync.py` (新增)

```python
# core/sync.py
def adjust_audio_sync(video, track_index, offset_seconds, output)
    → 整体偏移 (正=延后, 负=提前), FFmpeg -itoffset

def adjust_audio_speed(video, track_index, speed_ratio, output)
    → 变速不变调 (1.05=加速5%), FFmpeg atempo
```

| 场景 | 方法 |
|------|------|
| 固定偏移（全程晚 0.5s） | `adjust_audio_sync(offset=-0.5)` |
| 速率不匹配（音频比视频长） | `adjust_audio_speed(ratio=1.05)` |
| 句子级漂移（配音场景） | Stage 13 逐段对齐 |

### Stage 9+: Phase 3 AI 集成

GPU Worker 进程 (`worker.py`) 与 Web 容器通过 HTTP 通信：

```
Web 容器 post /api/asr/transcribe
    → GPU Worker localhost:9001/transcribe
        → faster-whisper CUDA 推理
        → 返回片段 JSON
    → Web 容器返回给前端
```

Worker 只在 Stage 9 才创建，前 8 个 Stage 不涉及。

---

## 7. 目录结构

```
video-localizer/
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── worker.py                 # GPU Worker (Stage 9 起)
├── app.py                    # FastAPI 入口
├── __init__.py
│
├── api/                      # REST 路由 (薄层)
│   ├── __init__.py
│   ├── probe.py
│   ├── extract.py
│   ├── subtitle.py
│   ├── audio.py
│   ├── asr.py
│   ├── translate.py
│   ├── tts.py
│   └── pipeline.py
│
├── core/                     # FFmpeg 操作
│   ├── probe.py
│   ├── extract.py
│   ├── mux.py
│   ├── burn.py
│   └── sync.py               ← 新增
│
├── asr/                      # 语音识别
│   ├── engine.py
│   └── whisper_local.py
│
├── tts/                      # 语音合成
│   ├── engine.py
│   ├── edge_tts.py
│   └── align.py
│
├── translate/                # 翻译
│   ├── engine.py
│   └── llm.py
│
├── subtitle/                 # 字幕处理
│   ├── srt.py
│   ├── ass.py
│   ├── convert.py
│   └── generator.py
│
├── pipeline/                 # 端到端流程
│   ├── switch_track.py
│   ├── add_subtitle.py
│   └── add_dub.py
│
├── config/
│   ├── __init__.py
│   └── settings.yaml
│
├── templates/                # Jinja2 页面
│   ├── base.html
│   └── index.html  (+ 后续按 Stage 增加)
│
├── static/
│   └── css/style.css
│
└── tests/
    ├── conftest.py
    └── fixtures/
```

---

## 8. 测试策略

| 层级 | 工具 | 内容 |
|------|------|------|
| 单元测试 | pytest | core/asr/tts 模块，mock 外部依赖 |
| API 测试 | httpx + pytest | 每个端点：正常 / 边界 / 错误 |
| E2E | 手动 | Docker 容器内浏览器操作完整流程 |

---

## 9. 文档结构

```
docs/
├── 00-05 (已有设计文档)
└── plan/
    ├── 00-master-plan.md        # 本文档
    ├── 01-stage1-foundation.md
    ├── ...
    └── 14-stage14-pipeline.md
```

每份 Stage 文档: **设计目标 → 接口设计 → 实施要点 → 验证结果 → 踩坑记录**

---

## 10. 关键风险

| 风险 | 缓解 |
|------|------|
| python:3.14 基础镜像不存在 | 用 3.13-slim，差异小 (仅 `audioop` 问题，3.13 仍内置) |
| 容器内 FFmpeg 版本差异 | 固定 FFmpeg 版本安装，与宿主机解耦 |
| Windows 路径挂载 | Docker Desktop 自动转换，`E:\Media` → `/media` |
| GPU Worker 通信延迟 | 同宿主机 localhost，延迟可忽略 |
| 长视频 HTTP 超时 | 后台任务 + 轮询进度，不阻塞请求 |
| 其余同前版 | |

---

*下一步: Stage 1 实施*
