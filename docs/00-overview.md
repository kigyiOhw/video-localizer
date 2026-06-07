# 项目概述

## 核心需求

给视频添加/修改字幕和音频。如果没有就加上（AI 实时生成），有则能切换指定语言。

| 需求 | 说明 |
|------|------|
| 添加字幕 | 视频原本无字幕轨道，通过 AI 语音识别自动生成并嵌入 |
| 切换字幕 | 视频已有多个字幕轨，切换默认显示的语言 |
| 添加音频 | 通过 AI TTS 生成新配音音轨并封装 |
| 切换音频 | 视频已有多个音轨，切换默认播放的语言 |

> **核心特点**：字幕和音频均由 AI 实时生成，不依赖现成文件。

---

## 字幕的两种存在形式

这是最基础的概念区分，决定技术路线：

### 软字幕（Soft Subs）
- 作为独立的字幕**轨道**（track）存在，可被播放器开启/关闭
- 封装在容器中（如 MKV、MP4），不烧录进画面
- 优点：可切换、可提取、不破坏画质
- 常见格式：SRT、ASS/SSA、WebVTT、MOV text

### 硬字幕（Hard Subs / Burn-in）
- 字幕直接渲染到视频画面上，成为图像的一部分
- 优点：任何播放器都能看到，适合短视频平台
- 缺点：无法关闭/切换，修改需重新编码

---

## 容器格式选择

| 容器 | 软字幕支持 | 多音轨 | 推荐度 |
|------|-----------|--------|--------|
| MKV | 完美（SRT/ASS/PGS） | 完美 | 首选 |
| MP4 | 仅 mov_text/tx3g | 支持 | 兼容性场景 |
| WebM | 仅 WebVTT | 一般 | 不推荐 |

> **建议**：输出优先选 MKV，兼容性需求选 MP4（需烧录硬字幕）。

---

## 三种实现方案对比

### 方案 A：FFmpeg 命令行封装（推荐）

调用 FFmpeg 命令行，通过参数控制字幕/音频流映射。

```bash
# 查看视频流信息
ffmpeg -i input.mp4

# 添加软字幕（不重新编码）
ffmpeg -i input.mp4 -i subtitle.srt -c copy -c:s mov_text \
  -map 0 -map 1 -metadata:s:s:0 language=eng output.mp4

# 切换默认字幕轨
ffmpeg -i input.mkv -c copy -disposition:s:0 none \
  -disposition:s:1 default output.mkv

# 添加音轨
ffmpeg -i input.mp4 -i audio.mp3 -c copy -map 0 -map 1 \
  -metadata:s:a:1 language=jpn output.mp4

# 烧录硬字幕（需重新编码）
ffmpeg -i input.mp4 -vf "subtitles=subtitle.srt" output.mp4
```

- **语言实现**：Python (`subprocess` + `ffmpeg-python`) 或 Node.js (`fluent-ffmpeg`)
- **优点**：功能最全、文档丰富、几乎支持所有格式
- **缺点**：命令行拼接脆弱、需要用户安装 FFmpeg

### 方案 B：FFmpeg C API / 绑定库

| 库 | 语言 | 特点 |
|---|---|---|
| `ffmpeg-python` | Python | 函数式 API，自动拼接参数 |
| `PyAV` | Python | 直接绑定 libav*，可逐帧操作 |
| `moviepy` | Python | 高层封装，内部用 FFmpeg |
| `fluent-ffmpeg` | Node.js | 链式 API |
| `ffmpeg.wasm` | JS/WASM | 浏览器端运行 |

### 方案 C：自己实现容器/编解码（不推荐）

直接解析 MP4/MKV 容器格式，操作 track atom/box。工作量巨大，需要深入理解 ISO BMFF、Matroska 等格式标准，基本不可行。

---

## 整体数据流

```
视频文件
   │
   ├─→ [提取音频] ──→ [ASR语音识别] ──→ 文本 + 时间戳 ──→ [生成字幕文件]
   │                                                          │
   │                                                          ├─→ 原文SRT
   │                                                          └─→ [翻译] → 多语SRT
   │
   ├─→ [提取字幕文本] ──→ [TTS语音合成] ──→ 音频 ──→ [对齐/变速]
   │                                                          │
   │                                                          └─→ 新音轨
   │
   └─→ [FFmpeg 封装] ──→ 输出视频（含多字幕轨 + 多音轨）
```

---

## 语言标记

使用 ISO 639-2 三字母代码，通过 metadata 设置：

```bash
-metadata:s:s:0 language=eng   # 字幕流0 → 英语
-metadata:s:a:0 language=jpn   # 音频流0 → 日语
```

---

## 不重新编码的快速模式

只要容器支持目标字幕/音频格式，就可以用 `-c copy` 避免重新编码，速度提升数百倍：

```bash
ffmpeg -i video.mp4 -i sub.srt -c copy -c:s mov_text output.mp4
```

> **注意**：MP4 只接受 `mov_text` 格式字幕，SRT 需先转换（或改用 MKV）。

---

## 技术栈建议

| 场景 | 推荐方案 |
|------|----------|
| 快速原型 / 脚本 | Python + `ffmpeg-python` + `srt` 库 |
| 桌面应用 | Python + PyQt/Tkinter + `PyAV`，打包 FFmpeg |
| Web 应用 | 后端 Python/Node + FFmpeg，前端上传下载 |
| 全自动字幕生成 | `faster-whisper` + `ffmpeg-python` + `pysrt` |
| 浏览器端处理 | `ffmpeg.wasm`（仅小文件，性能有限） |

---

## 开发路线

### Phase 1 — 最小可用
1. 用 FFprobe 探测视频的流信息
2. 实现"添加外部 SRT 字幕到视频"（软字幕，MKV 输出）
3. 实现"切换默认字幕语言"

### Phase 2 — 增强
4. 支持字幕格式转换（SRT ↔ ASS ↔ WebVTT）
5. 支持音频轨道的添加/切换
6. 支持烧录硬字幕选项

### Phase 3 — AI 集成
7. 集成 Whisper 自动语音识别生成字幕
8. 字幕翻译功能（调用翻译 API）
9. TTS 配音生成与音频对齐
