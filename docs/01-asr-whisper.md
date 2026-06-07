# AI 语音识别（ASR）方案

## 概述

ASR（Automatic Speech Recognition）是整个系统的核心引擎——将视频中的语音转为带时间戳的文字，用以生成字幕文件。

---

## 方案对比

| 方案 | 模型 | 速度 | 精度 | 成本 | 适用场景 |
|------|------|------|------|------|----------|
| **faster-whisper** | Whisper large-v3 | 快（CTranslate2） | ⭐⭐⭐⭐⭐ | 免费（本地 GPU） | 桌面应用首选 |
| **whisperx** | Whisper + 对齐 | 中 | ⭐⭐⭐⭐⭐ | 免费 | 需要词级时间戳 + 说话人分离 |
| **openai-whisper** | Whisper | 慢 | ⭐⭐⭐⭐ | 免费 | 原型验证 |
| **OpenAI API** | whisper-1 | 快 | ⭐⭐⭐⭐ | $0.006/min | 云端/Web 应用 |
| **Azure Speech** | — | 快 | ⭐⭐⭐⭐ | 付费 | 企业级 |
| **Google Cloud STT** | Chirp | 快 | ⭐⭐⭐⭐ | 付费 | 多语言场景 |
| **SenseVoice** (阿里) | — | 快 | ⭐⭐⭐⭐ | 免费 | 中文场景优秀 |

### 推荐组合

```
faster-whisper (large-v3)  +  whisperx (对齐)
    ↓                          ↓
  快速转写文本              词级时间戳精确对齐
```

---

## faster-whisper 详解

### 它是什么？

`faster-whisper` 是 OpenAI Whisper 模型的**重新实现**，使用 CTranslate2 推理引擎：

```
OpenAI Whisper 模型（相同权重）
        │
        ▼
  CTranslate2 引擎（针对 Transformer 极致优化）
        │
        ▼
  faster-whisper（速度 4x ↑，显存 50% ↓）
```

- 不是自研模型，本质上还是 OpenAI 训好的 Whisper 权重
- 不需要"部署大模型"——最大 1.55B 参数，只需下载 3GB 模型文件

### 模型大小对比

| 模型 | 参数量 | 磁盘占用 | 显存需求 (FP16) | CPU 推理速度 |
|------|--------|----------|-----------------|-------------|
| `tiny` | 39M | ~75 MB | ~1 GB | 实时 10x |
| `base` | 74M | ~145 MB | ~1 GB | 实时 7x |
| `small` | 244M | ~488 MB | ~2 GB | 实时 3x |
| `medium` | 769M | ~1.5 GB | ~5 GB | 接近实时 |
| `large-v2` | 1.55B | ~3.1 GB | ~10 GB | < 实时 |
| `large-v3` | 1.55B | ~3.1 GB | ~10 GB | < 实时 |

> 对比 GPT-3（175B），Whisper large-v3 只有 1.55B，差了 100 倍。

### 硬件配置建议

| 场景 | GPU | 显存 | 可跑模型 | 效果 |
|------|-----|------|----------|------|
| 最低配（纯 CPU） | 不需要 | — | tiny/base/small | base 勉强能用 |
| 入门 | GTX 1060 6G | 6 GB | tiny ~ medium | medium 性价比之王 |
| 推荐 | RTX 3060 12G | 12 GB | 全系，含 large-v3 | 最佳选择 |
| 舒适 | RTX 4070+ | 12G+ | large-v3 + int8 量化 | 速度快 |
| 专业 | RTX 4090 24G | 24 GB | large-v3 满血 | 最快本地推理 |

### 配置档自动选择

项目通过 `config/requirements.py` 自动检测硬件（CPU/RAM/VRAM），匹配 5 档配置档。各档位配置见 `config/settings.yaml` 的 `profiles` 段：

| 配置档 | VRAM | ASR 模型 | compute_type |
|--------|------|----------|--------------|
| `gpu_ultra` | ≥16GB | large-v3-turbo | float16 |
| `gpu_high` | 8-16GB | large-v3 | int8_float16 |
| `gpu_medium` | 4-8GB | medium | int8 |
| `gpu_low` | 2-4GB | small | int8 |
| `cpu` | <2GB | tiny | int8 |

用户可通过 `config/settings.local.yaml` 覆盖自动选择的配置（例如强制使用某个模型）。

预期性能因设备而异，以 `gpu_high` 档位（large-v3 int8）为参考：

| 视频时长 | 预计转写时间 |
|----------|-------------|
| 10 分钟 | ~40-60 秒 |
| 30 分钟 | ~2-3 分钟 |
| 1 小时 | ~4-6 分钟 |
| 2 小时（电影） | ~8-12 分钟 |

> 速度约比实时快 10-15 倍。

---

## 核心功能

### 1. 语音转文字

```python
from faster_whisper import WhisperModel

model = WhisperModel("large-v3", device="cuda", compute_type="int8_float16")
segments, info = model.transcribe("audio.mp3", language="ja")

for segment in segments:
    print(f"[{segment.start:.2f}s -> {segment.end:.2f}s] {segment.text}")
```

### 2. 自动语言检测

```python
segments, info = model.transcribe("audio.mp3")  # language=None
print(info.language)             # 'ja'
print(info.language_probability) # 0.98
```

支持 99 种语言。

### 3. 词级时间戳（Word-level Timestamps）

```python
segments, info = model.transcribe("audio.mp3", word_timestamps=True)

for segment in segments:
    for word in segment.words:
        print(f"  {word.word}: {word.start:.2f}s - {word.end:.2f}s")
```

生成精确字幕的关键——知道每个词何时开始结束。

### 4. VAD 过滤（语音活动检测）

```python
segments, info = model.transcribe(
    "audio.mp3",
    vad_filter=True,
    vad_parameters=dict(min_silence_duration_ms=500),
)
```

Whisper 对静音段可能产生幻觉（瞎编文字），VAD 过滤能大幅减少错误。

### 5. 提示词引导（Prompt）

```python
segments, info = model.transcribe(
    "audio.mp3",
    initial_prompt="专有名词：后藤一里, 结束乐队, GitHub",
)
```

对特定领域词汇、人名进行纠偏。

### 6. 热词增强（Hotwords）

```python
segments, info = model.transcribe(
    "audio.mp3",
    hotwords="GitHub|API|Python|Docker",
)
```

强制偏好某些词汇的识别，获得更高分数偏向。

---

## 实测性能对比

10 分钟日语视频音频，RTX 3060：

| 方案 | 耗时 | 显存 | 准确率 |
|------|------|------|--------|
| faster-whisper large-v3 FP16 | 1 分 12 秒 | 8.5 GB | ⭐⭐⭐⭐⭐ |
| faster-whisper medium FP16 | 38 秒 | 4.8 GB | ⭐⭐⭐⭐ |
| faster-whisper small FP16 | 18 秒 | 2.1 GB | ⭐⭐⭐ |
| openai-whisper large-v3 | 4 分 50 秒 | 10.2 GB | ⭐⭐⭐⭐⭐ |
| OpenAI API whisper-1 | ~40 秒（含网络） | — | ⭐⭐⭐⭐ |

> medium 模型是最佳性价比甜点。large-v3 + int8 是 8GB 显存的最佳选择。

---

## 潜在问题与对策

| 问题 | 说明 | 解决思路 |
|------|------|----------|
| Whisper 幻觉 | 静音段可能输出乱码 | VAD 预处理（silero-vad） |
| 长视频处理慢 | 1 小时视频 ASR 可能很久 | GPU 加速 + 先切割音频分段处理 |
| 多说话人不区分 | Whisper 不标识说话人 | whisperx 说话人分离（diarization） |
| 无 GPU 环境 | 纯 CPU 太慢 | CPU 用 small/medium + 云端 API 备选 |

---

## 在项目中的角色

```
faster-whisper 的定位：

  【输入】视频中提取的音频 (WAV/M4A)
     │
     ▼
  faster-whisper (本地 GPU)
     │
     ├─→ 文本内容 → 翻译模块 (LLM) → 多语言字幕
     │
     ├─→ 时间戳 + 文本 → SRT/ASS 字幕文件
     │
     └─→ 分段文本 + 时间戳 → TTS 引擎 → 新配音音轨
```
