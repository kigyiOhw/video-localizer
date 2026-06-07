"""测试环境是否安装正确"""
import sys
import subprocess

print(f"Python: {sys.version}")

# 1. FFmpeg
result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True)
print(f"FFmpeg: {result.stdout.split(chr(10))[0]}")

# 2. CUDA 检测 (通过 ctranslate2，faster-whisper 实际使用)
try:
    import ctranslate2
    cuda_count = ctranslate2.get_cuda_device_count()
    has_cuda = cuda_count > 0
    print(f"ctranslate2 CUDA devices: {cuda_count}")
    if has_cuda:
        print(f"Supported compute types: {ctranslate2.get_supported_compute_types('cuda')}")
except ImportError:
    has_cuda = False
    print("ctranslate2 not installed, assuming CPU-only")

# 3. faster-whisper 模型加载测试
from faster_whisper import WhisperModel

if has_cuda:
    device = "cuda"
    compute_type = "int8_float16"
else:
    device = "cpu"
    compute_type = "int8"

try:
    print(f"Loading faster-whisper large-v3 model (device={device}, compute={compute_type})...")
    model = WhisperModel("large-v3", device=device, compute_type=compute_type)
    print("[OK] faster-whisper ready")
except Exception as e:
    # 如果 large-v3 下载失败或显存不足，尝试 tiny 做烟雾测试
    print(f"large-v3 failed ({e}), trying tiny as smoke test...")
    model = WhisperModel("tiny", device=device, compute_type=compute_type)
    print("[OK] faster-whisper ready (tiny fallback)")

# 4. 其它包
import pydub       # noqa: E402
import ffmpeg      # noqa: E402
import pysrt       # noqa: E402
print("[OK] All dependencies ready")
