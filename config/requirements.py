"""硬件检测与配置档自动选择。

启动时自动检测 CPU / GPU / VRAM / RAM，匹配 5 档硬件配置档，
覆盖 settings.yaml 中 asr / tts / translate 的默认值。
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any

import psutil

logger = logging.getLogger("video_localizer.config")


# ---------------------------------------------------------------------------
# 硬件检测
# ---------------------------------------------------------------------------


def detect_system_info() -> dict[str, Any]:
    """检测系统硬件信息。

    Returns:
        dict with keys: cpu_count, ram_gb, vram_gb, has_cuda, cuda_version.
    """
    info: dict[str, Any] = {
        "cpu_count": _detect_cpu(),
        "ram_gb": _detect_ram_gb(),
        "vram_gb": 0.0,
        "has_cuda": False,
        "cuda_version": None,
    }

    # GPU / CUDA 检测
    try:
        cuda_available, cuda_version, vram_gb = _detect_gpu()
        info["has_cuda"] = cuda_available
        info["cuda_version"] = cuda_version
        info["vram_gb"] = vram_gb
    except Exception:
        logger.debug("GPU 检测失败，回退到 CPU 模式", exc_info=True)

    logger.info(
        "硬件检测完成: CPU=%d核 RAM=%.1fGB VRAM=%.1fGB CUDA=%s",
        info["cpu_count"], info["ram_gb"], info["vram_gb"], info["has_cuda"],
    )
    return info


def _detect_cpu() -> int:
    """检测 CPU 逻辑核心数。"""
    count = psutil.cpu_count(logical=True) or 1
    logger.debug("CPU 核心数: %d", count)
    return count


def _detect_ram_gb() -> float:
    """检测系统内存 (GB)。"""
    total = psutil.virtual_memory().total
    gb = round(total / (1024 ** 3), 1)
    logger.debug("系统内存: %.1f GB", gb)
    return gb


def _detect_gpu() -> tuple[bool, str | None, float]:
    """检测 NVIDIA GPU 及 VRAM（多 GPU 时汇总总显存）。

    Returns:
        (has_cuda, cuda_version_str, vram_gb).
    """
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi is None:
        logger.debug("nvidia-smi 未找到，假定无 GPU")
        return False, None, 0.0

    try:
        result = subprocess.run(
            [nvidia_smi, "--query-gpu=memory.total,driver_version",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            logger.debug("nvidia-smi 返回非零: %s", result.stderr.strip())
            return False, None, 0.0

        # 汇总所有 GPU 的显存
        total_vram_mb = 0.0
        driver_ver = None
        for line in result.stdout.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            parts = line.split(",")
            if len(parts) < 2:
                continue
            total_vram_mb += float(parts[0].strip())
            if driver_ver is None:
                driver_ver = parts[1].strip()

        if driver_ver is None:
            logger.debug("nvidia-smi 输出格式异常: %s", result.stdout.strip())
            return False, None, 0.0

        vram_gb = round(total_vram_mb / 1024.0, 1)
        gpu_count = result.stdout.strip().count("\n") + 1
        logger.debug("检测到 %d GPU: 总VRAM=%.1fGB driver=%s", gpu_count, vram_gb, driver_ver)
        return True, driver_ver, vram_gb

    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError) as e:
        logger.debug("nvidia-smi 调用失败: %s", e)
        return False, None, 0.0


# ---------------------------------------------------------------------------
# 配置档选择
# ---------------------------------------------------------------------------

# VRAM 阈值 (GB) → 配置档名
_VRAM_THRESHOLDS: list[tuple[float, str]] = [
    (16.0, "gpu_ultra"),
    (8.0,  "gpu_high"),
    (4.0,  "gpu_medium"),
    (2.0,  "gpu_low"),
]


def select_profile(vram_gb: float) -> str:
    """根据 VRAM 选择配置档。

    Args:
        vram_gb: GPU 显存大小 (GB)，0 表示无 GPU。

    Returns:
        配置档名称: "gpu_ultra" | "gpu_high" | "gpu_medium" | "gpu_low" | "cpu"
    """
    for threshold, profile_name in _VRAM_THRESHOLDS:
        if vram_gb >= threshold:
            logger.info("VRAM %.1fGB >= %.0fGB → 选择配置档 '%s'", vram_gb, threshold, profile_name)
            return profile_name

    logger.info("VRAM %.1fGB 或无可用的 GPU → 选择配置档 'cpu'", vram_gb)
    return "cpu"


def auto_configure(settings: "Settings", info: dict[str, Any] | None = None) -> str:  # noqa: F821
    """自动检测硬件并应用对应配置档。

    Args:
        settings: Settings 实例（会被原地修改）。
        info: 可选，预先检测的系统信息（避免重复检测）。

    Returns:
        选中的配置档名称。
    """
    if info is None:
        info = detect_system_info()
    profile_name = select_profile(info["vram_gb"])
    settings.apply_profile(profile_name)
    return profile_name


# ---------------------------------------------------------------------------
# 最低配置检查
# ---------------------------------------------------------------------------


def check_minimum_requirements(
    settings: "Settings",  # noqa: F821
    info: dict[str, Any] | None = None,
) -> list[str]:
    """检查是否满足最低运行要求。

    Args:
        settings: Settings 实例（读取 requirements 段阈值）。
        info: 可选，预先检测的系统信息（避免重复检测 RAM/Disk）。

    Returns:
        失败项列表（空列表表示一切正常）。每一项是一条人类可读的错误描述。
    """
    failures: list[str] = []
    req = settings.requirements

    if info is None:
        info = detect_system_info()

    # 内存检查
    ram_gb = info.get("ram_gb", _detect_ram_gb())
    if ram_gb < req.min_ram_gb:
        failures.append(f"内存不足: {ram_gb}GB < {req.min_ram_gb}GB (最低要求)")

    # 磁盘空间检查
    temp_dir = Path(settings.paths.temp_dir)
    check_dir = temp_dir if temp_dir.exists() else temp_dir.parent
    try:
        usage = shutil.disk_usage(check_dir)
        free_gb = usage.free / (1024 ** 3)
        if free_gb < req.min_disk_free_gb:
            failures.append(
                f"磁盘空间不足: {free_gb:.1f}GB < {req.min_disk_free_gb}GB (最低要求)"
            )
        logger.debug("磁盘可用空间: %.1f GB (%s)", free_gb, check_dir)
    except Exception:
        logger.debug("无法检测磁盘空间: %s", check_dir, exc_info=True)

    # 必需工具检查
    for tool in req.required_tools:
        if shutil.which(tool) is None:
            failures.append(f"缺少必需工具: {tool}（请安装后重试）")

    if failures:
        for f in failures:
            logger.error("✗ %s", f)
    else:
        logger.info("最低配置检查通过")

    return failures


# ---------------------------------------------------------------------------
# 快速导入钩子
# ---------------------------------------------------------------------------

def __getattr__(name: str):
    """延迟导入 Settings 以避免循环引用。"""
    if name == "Settings":
        from . import Settings
        return Settings
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
