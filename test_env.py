"""测试环境是否安装正确"""
import sys
print(f"Python: {sys.version}")

# 1. FFmpeg
import subprocess
result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True)
print(f"FFmpeg: {result.stdout.split(chr(10))[0]}")

# 2. faster-whisper
from faster_whisper import WhisperModel
import torch
print(f"CUDA available: {torch.cuda.is_available()}")
print(f"CUDA version: {torch.version.cuda if torch.cuda.is_available() else 'N/A'}")

# 3. 检查 ctranslate2 CUDA（faster-whisper 实际使用）
import ctranslate2
cuda_count = ctranslate2.get_cuda_device_count()
print(f"ctranslate2 CUDA devices: {cuda_count}")
print(f"Supported compute types: {ctranslate2.get_supported_compute_types('cuda')}")

# 4. 模型加载测试（首次运行会自动下载 ~3GB 到 HF_HOME）
print("Loading faster-whisper large-v3 model...")
model = WhisperModel("large-v3", device="cuda", compute_type="int8_float16")
print("[OK] faster-whisper ready")

# 5. 其它包
import pydub
import ffmpeg
import pysrt
print("[OK] All dependencies ready")
