# 安装与部署指南

## 硬件需求

### 最低配置
- CPU：任意 x86_64（纯 CPU 推理，用 small 模型）
- 内存：8 GB
- 存储：10 GB 可用（模型 + 依赖）
- 系统：Windows 10+ / Linux / macOS

### 推荐配置
- CPU：i7 / Ryzen 7 或更高
- GPU：NVIDIA RTX 3060+（6GB+ 显存）
- 内存：16 GB+
- 存储：20 GB 可用

### 硬件配置档自动适配

启动时 `config/requirements.py` 自动检测 CPU/RAM/VRAM，匹配 5 档配置档（参见 `config/settings.yaml` 的 `profiles` 段）。用户可通过 `config/settings.local.yaml` 覆盖自动选择的配置。

---

## 依赖清单

### 系统依赖

| 软件 | 版本 | 大小 | 用途 |
|------|------|------|------|
| Python | 3.11+ | ~100 MB | 运行环境 |
| FFmpeg | 8.1.1+ | ~100 MB | 音视频处理 |
| CUDA Driver | ≥ 525 | 已安装 | GPU 加速 |

> 本项目不需要安装完整的 CUDA Toolkit，ctranslate2 已内置 CUDA 运行时。

### Python 包

| 包 | 大小 | 用途 |
|---|------|------|
| `faster-whisper` | ~1.2 GB（含 ctranslate2） | ASR 语音识别 |
| `ffmpeg-python` | ~1 MB | FFmpeg Python 封装 |
| `pydub` | ~5 MB | 音频处理 |
| `pysrt` | ~100 KB | SRT 字幕解析 |
| `silero-vad` | ~5 MB | 语音活动检测 |

### AI 模型文件

| 模型 | 大小 | 用途 |
|------|------|------|
| faster-whisper large-v3 | ~3 GB | 语音转文字 |

**总计：约 4.6 GB**

---

## 安装步骤

### 1. 环境准备

```powershell
# 确保 Python 已安装且在 PATH 中
python --version    # 应显示 Python 3.11+

# 确保 FFmpeg 已安装
ffmpeg -version     # 应显示版本号
```

### 2. 配置环境变量

复制 `.env.example` 为 `.env`（gitignored），填入本地路径：

```bash
# .env
MEDIA_ROOT=/path/to/your/media
MODELS_ROOT=/path/to/your/models
```

HF_HOME 等变量在 `docker-compose.yml` 中已设置，指向 `/models/huggingface`。直接运行时在终端 set 即可。

### 3. 创建目录结构

```bash
# 克隆项目
git clone <repo-url> video-localizer
cd video-localizer

# 素材目录（路径可自定义，与 .env 中 MEDIA_ROOT 对应）
mkdir -p /path/to/media/input /path/to/media/output /path/to/media/temp

# 模型目录（与 .env 中 MODELS_ROOT 对应）
mkdir -p /path/to/models/huggingface
```

### 4. 安装 Python 依赖

```powershell
# 核心：ASR 引擎
pip install faster-whisper

# 基础工具
pip install ffmpeg-python pydub pysrt

# 可选：VAD 语音检测
pip install silero-vad
```

### 5. 验证安装

创建测试脚本 `test_env.py`：

```python
"""测试环境是否安装正确"""
import sys
print(f"Python: {sys.version}")

# 1. FFmpeg
import subprocess
result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True)
print(f"FFmpeg: {result.stdout.split(chr(10))[0]}")

# 2. 配置加载
from config import Settings
settings = Settings.load()
print(f"配置加载成功，配置档: {settings.selected_profile}")

# 3. 硬件检测
from config.requirements import detect_system_info
info = detect_system_info()
print(f"CPU={info['cpu_count']}核 RAM={info['ram_gb']}GB VRAM={info['vram_gb']}GB")

# 4. 核心依赖
import pydub
import pysrt
import ffmpeg
print("✅ 所有核心依赖就绪")
```

运行：
```powershell
python test_env.py
```

---

## FFmpeg 安装方式

### 方式一：winget（Windows 11 自带）

```powershell
winget install --id Gyan.FFmpeg.Essentials
```

### 方式二：手动下载

1. 访问 https://www.gyan.dev/ffmpeg/builds/
2. 下载 `ffmpeg-release-essentials.zip`
3. 解压到任意目录
4. 将 `bin` 目录加入系统环境变量 PATH

### 方式三：Scoop

```powershell
scoop install ffmpeg
```

---

## 模型下载

首次运行 `faster-whisper` 时会自动从 HuggingFace 下载模型：

```python
from faster_whisper import WhisperModel

# 首次运行自动下载到 HF_HOME 目录（由 docker-compose.yml 映射到 /models/huggingface）
# 模型和参数由 config/settings.yaml 的配置档自动选定
model = WhisperModel("large-v3", device="cuda", compute_type="int8_float16")
```

下载进度会显示在控制台。模型大小约 3 GB，下载时间取决于网络速度。

---

## 路径规划

Docker 容器内路径固定，宿主机路径通过 `.env` 灵活配置：

```
容器内:                        宿主机 (通过 .env 配置):
/app            ←─── 项目代码根目录（bind mount）
/media          ←─── ${MEDIA_ROOT}
  ├── input/    ←─── 待处理视频
  ├── output/   ←─── 输出视频
  └── temp/     ←─── 临时文件
/models         ←─── ${MODELS_ROOT} (只读)
  └── huggingface/  ←─── HF_HOME，模型缓存
```

直接运行时路径由 `config/settings.local.yaml` 的 `paths` 段配置。

---

## 快速自检清单

| 检查项 | 命令 | 预期结果 |
|--------|------|----------|
| Python 版本 | `python --version` | 3.14+ |
| pip 版本 | `pip --version` | 最新版 |
| FFmpeg | `ffmpeg -version` | 显示版本 |
| GPU 驱动 | `nvidia-smi` | 显示 GPU 型号和驱动版本 |
| 配置文件 | `cat config/settings.yaml` | 显示默认配置 |
| 测试 | `python -m pytest tests/ -v` | 全部通过 |
| 服务启动 | `docker compose up -d` | `localhost:8000` 可访问 |
