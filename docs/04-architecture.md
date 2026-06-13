# 系统架构设计

## 分层架构

```
┌─────────────────────────────────────────┐
│              用户界面层                  │
│   FastAPI + Jinja2 + HTMX  (Web UI)     │
├─────────────────────────────────────────┤
│              业务逻辑层                  │
│  ┌──────────┐ ┌──────────┐ ┌─────────┐ │
│  │ 流分析器  │ │ 字幕处理 │ │ 音频处理│ │
│  │ (探测流)  │ │(生成/转换)│ │(提取/替换)│ │
│  └──────────┘ └──────────┘ └─────────┘ │
│  ┌──────────┐ ┌──────────┐ ┌─────────┐ │
│  │ ASR 引擎  │ │ 翻译引擎 │ │ TTS 引擎│ │
│  └──────────┘ └──────────┘ └─────────┘ │
├─────────────────────────────────────────┤
│              FFmpeg 执行层               │
│     ffmpeg-python / PyAV / subprocess   │
├─────────────────────────────────────────┤
│              文件系统                    │
│       输入/输出视频、字幕、音频文件       │
└─────────────────────────────────────────┘
```

---

## 模块目录结构

```
video-localizer/
├── engines/                 # AI 引擎层（策略模式）
│   ├── asr/                 #   语音识别
│   │   ├── engine.py        #   抽象接口
│   │   └── whisper_local.py #   faster-whisper 本地实现
│   ├── tts/                 #   语音合成（Stage 12 实现）
│   │   └── engine.py        #   抽象接口
│   ├── translate/           #   翻译
│   │   ├── engine.py        #   抽象接口
│   │   ├── llm.py           #   LLM 翻译（OpenAI/DeepSeek/Ollama）
│   │   └── llm_local.py     #   本地 Ollama 封装
│   └── __init__.py
│
├── processing/              # 媒体处理层
│   ├── core/                #   核心视频处理
│   │   ├── probe.py         #   视频流探测（ffprobe）
│   │   ├── extract.py       #   提取音频/字幕/视频流
│   │   └── mux.py           #   封装（添加流、设置 metadata）
│   ├── subtitle/            #   字幕处理（Stage 6 实现）
│   ├── pipeline/            #   完整工作流
│   │   └── full_pipeline.py #   ASR → 翻译 → 封装端到端流程
│   └── __init__.py
│
├── web/                     # Web 表示层
│   ├── api/                 #   FastAPI 路由
│   ├── templates/           #   Jinja2 模板
│   ├── static/              #   CSS/JS 静态资源
│   └── __init__.py
│
├── config/                  # 配置（跨层引用）
│   ├── __init__.py          # Settings 数据类 + YAML 加载
│   ├── requirements.py      # 硬件检测与配置档选择
│   └── settings.yaml        # 全局配置
│
├── media/                   # 媒体文件 I/O（input / output / temp）
├── docs/                    # 项目文档
├── tests/                   # 测试
│
├── app.py                   # FastAPI 入口
└── worker.py                # GPU Worker（Stage 9 计划拆分）
```

> **说明**：目录树反映当前已实现文件。`worker.py` 与 `engines/tts/`、`processing/subtitle/`
> 等目录中的模块将在后续 Stage（TTS、硬字幕烧录、音频对齐等）中补齐。

---

## 典型用户流程

### 流程 1：自动生成字幕

```
用户拖入视频 → 选择"生成中文字幕"
  → [probe] 探测视频信息
  → [extract] 提取音频
  → [whisper_local] faster-whisper 转写（进度条显示）
  → [srt] 生成 SRT 文件
  → [mux] 封装为软字幕添加到视频（MKV 输出）
  → 预览播放
```

### 流程 2：生成多语言字幕

```
用户拖入视频 → 选择"中文 + 英文 + 日文字幕"
  → 提取音频
  → ASR 转写得到原文（日语）
  → [translate/llm] LLM 翻译为中文、英文
  → 分别生成 SRT → 设置 language metadata
  → 封装进视频 → 输出 MKV
```

### 流程 3：AI 配音

```
用户拖入视频 + 字幕文件 → 选择"生成日语配音"
  → 提取字幕文本 + 时间戳
  → （如需要）翻译为目标语言
  → 按时间片段调用 TTS 引擎逐句合成
  → [align] 音频对齐（rubberband 变速）
  → 合并音频片段
  → 封装为新的音轨 → 输出 MKV
```

---

## 配置设计

`config/settings.yaml`：

```yaml
# paths: 路径配置（容器内路径或通过 .env 变量覆盖）
paths:
  model_root: /models
  hf_cache: /models/huggingface
  media_input: /media/input
  media_output: /media/output
  temp_dir: /media/temp

# asr: 语音识别 — 实际值由硬件配置档自动覆盖
asr:
  engine: whisper_local
  model_size: medium              # tiny/base/small/medium/large-v3/large-v3-turbo
  device: cpu                     # cuda / cpu
  compute_type: int8              # float16 / int8_float16 / int8
  beam_size: 5
  vad_filter: true
  language: auto

# subtitle: 字幕默认值
subtitle:
  default_language: zho
  default_format: srt

# translate: 翻译引擎（默认 none；使用 /translate 前需启用）
translate:
  engine: none                    # llm / llm_local / none
  target_language: zho

# tts: 语音合成
tts:
  engine: edge_tts                # edge_tts / xtts

# ffmpeg: 可执行文件
ffmpeg:
  executable: ffmpeg
  ffprobe_executable: ffprobe

# profiles: 5 档硬件配置（启动时按 VRAM 自动选择）
profiles:
  gpu_ultra:     # VRAM ≥ 16GB
    asr: {model_size: large-v3-turbo, device: cuda, compute_type: float16}
    ...
  cpu:           # 无 GPU
    asr: {model_size: tiny, device: cpu, compute_type: int8}
    ...
```

---

## 关键设计决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 默认输出容器 | MKV | 最大兼容性（软字幕、多音轨） |
| ASR 引擎 | faster-whisper | 速度 4x、显存省半、离线免费 |
| 翻译引擎 | LLM API（DeepSeek） | 翻译质量最高，可理解上下文 |
| TTS 引擎 | Edge-TTS | 免费、高质量、零配置 |
| FFmpeg 调用 | subprocess | 比 ffmpeg-python 更可控，减少依赖 |
| Python 版本 | 3.14 | 已安装，ctranslate2 支持 cp314 |

---

## 部署布局

默认使用 Docker 部署，路径通过卷挂载映射到容器内：

```yaml
# docker-compose.yml
volumes:
  - ./:/app                     # 项目代码（热更新）
  - ${MEDIA_ROOT}:/media        # 媒体文件（input / output / temp）
  - ${MODELS_ROOT}:/models:ro   # AI 模型（只读）
```

宿主机路径通过 `.env`（gitignored）配置：
```bash
MEDIA_ROOT=/path/to/your/media
MODELS_ROOT=/path/to/your/models
```

容器内固定路径：`/app`（代码）、`/media`（素材）、`/models`（模型）。也可直接运行 `python app.py`（需 Python 3.14+ + FFmpeg）。
