# 字幕翻译方案

## 概述

ASR 只能产出一个语言的文本。要生成多语言字幕，需要翻译模块。

---

## 翻译方案对比

| 方案 | 质量 | 速度 | 成本 | 隐私 | 适用场景 |
|------|------|------|------|------|----------|
| **LLM API** (GPT-4o/Claude/DeepSeek) | ⭐⭐⭐⭐⭐ | 快 | 按 token 付费 | ❌ 云端 | 最高质量翻译 |
| **本地 LLM** (Qwen/Llama + Ollama) | ⭐⭐⭐⭐ | 中 | 免费 | ✅ 完全本地 | 隐私敏感场景 |
| **Google Translate API** | ⭐⭐⭐⭐ | 快 | $20/百万字符 | ❌ 云端 | 多语言覆盖广 |
| **DeepL API** | ⭐⭐⭐⭐⭐ | 快 | 付费 | ❌ 云端 | 欧亚语言间最佳 |
| **Argos Translate** | ⭐⭐⭐ | — | 免费离线 | ✅ 完全本地 | 备选方案 |
| **Whisper 直接多语言** | ⭐⭐⭐⭐ | 快 | 免费 | ✅ 本地 | 语音直出多语言 |

---

## 策略建议

```
优先：Whisper 直接识别 → 如果视频本身是多语种混说的
其次：LLM 翻译（DeepSeek/GPT-4o-mini）→ 性价比最优
兜底：本地 Ollama 模型 → 纯离线场景
```

---

## LLM 翻译的优势

相比传统翻译 API，LLM 翻译有以下优势：

1. **上下文理解**：能根据前后对话调整翻译，而不是逐句独立翻译
2. **术语一致性**：通过 system prompt 指定专有名词翻译规则
3. **语气保持**：能保留原文的情感色彩和说话风格
4. **字幕特定优化**：可指定"生成适合字幕长度的翻译"（控制字数）

### 示例 Prompt

```
你是一个字幕翻译专家。请将以下日语字幕翻译成中文。

要求：
1. 每行不超过 20 个字（字幕显示限制）
2. 保持口语化，不要书面语
3. 专有名词：後藤ひとり → 后藤一里（保持统一）
4. 敬语适当简化为中文习惯表达

输入：
[0:00 - 0:04] 私、人と話すの苦手で…
[0:04 - 0:08] でも、バンドは好きなんです

输出：
[0:00 - 0:04] 我不太擅长跟人说话...
[0:04 - 0:08] 但是，我很喜欢乐队
```

---

## 本地 LLM 方案（Ollama）

CPU 推理 7-14B 模型可行（需 16GB+ RAM，具体取决于硬件配置档）：

```bash
# 安装 Ollama
# 拉取翻译用模型（推荐 Qwen 系列，中英日优秀）
ollama pull qwen2.5:7b      # ~4GB，最快
ollama pull qwen2.5:14b     # ~8GB，质量更好
```

```python
import ollama

response = ollama.chat(
    model="qwen2.5:14b",
    messages=[{
        "role": "system",
        "content": "你是字幕翻译专家..."
    }, {
        "role": "user",
        "content": "请翻译以下字幕：..."
    }]
)
```

---

## 多语言字幕生成流程

```
提取音频
   │
   ▼
faster-whisper (ASR)
   │
   ├─→ 中文 SRT（原文）
   │
   ▼
翻译模块 (LLM API 或 Ollama)
   │
   ├─→ 英文 SRT
   ├─→ 日文 SRT
   ├─→ 韩文 SRT
   └─→ ...
   │
   ▼
FFmpeg 封装
   │
   └─→ MKV（含 5 条字幕轨 + metadata 语言标记）
```

---

## 字幕格式转换

不同播放器支持的格式不同，需要互转：

| 格式 | 特点 | 支持容器 |
|------|------|----------|
| SRT | 最通用，纯文本 + 时间 | MKV, MP4 |
| ASS/SSA | 支持样式、位置、特效 | MKV |
| WebVTT | Web 标准，HTML5 播放器 | WebM, MKV |
| MOV text | Apple 格式 | MP4 |

### 转换工具

```bash
# ffmpeg 内置转换
ffmpeg -i subtitle.srt subtitle.ass
ffmpeg -i subtitle.ass subtitle.srt
```
