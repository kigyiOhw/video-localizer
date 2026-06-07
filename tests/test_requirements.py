"""硬件检测与最低配置测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from config.requirements import (
    check_minimum_requirements,
    detect_system_info,
    select_profile,
)


class TestDetectSystemInfo:
    def test_returns_required_keys(self) -> None:
        info = detect_system_info()
        for key in ("cpu_count", "ram_gb", "vram_gb", "has_cuda", "cuda_version"):
            assert key in info, f"缺少键: {key}"

    def test_cpu_count_positive(self) -> None:
        info = detect_system_info()
        assert info["cpu_count"] >= 1

    def test_ram_gb_positive(self) -> None:
        info = detect_system_info()
        assert info["ram_gb"] > 0

    def test_vram_gb_not_negative(self) -> None:
        info = detect_system_info()
        assert info["vram_gb"] >= 0


class TestCheckMinimumRequirements:
    def test_returns_list(self, temp_settings_file: Path) -> None:
        from config import Settings
        s = Settings.load(temp_settings_file)
        warnings = check_minimum_requirements(s)
        assert isinstance(warnings, list)

    def test_no_crash(self, temp_settings_file: Path) -> None:
        """最低配置检查不应抛出异常。"""
        from config import Settings
        s = Settings.load(temp_settings_file)
        # 即使路径不存在也不应崩溃
        warnings = check_minimum_requirements(s)
        # 只是检查，不强制 pass/fail
        assert isinstance(warnings, list)


class TestSelectProfileBoundaries:
    """验证配置档阈值边界。"""

    def test_boundary_16gb(self) -> None:
        assert select_profile(16.0) == "gpu_ultra"
        assert select_profile(15.9) == "gpu_high"

    def test_boundary_8gb(self) -> None:
        assert select_profile(8.0) == "gpu_high"
        assert select_profile(7.9) == "gpu_medium"

    def test_boundary_4gb(self) -> None:
        assert select_profile(4.0) == "gpu_medium"
        assert select_profile(3.9) == "gpu_low"

    def test_boundary_2gb(self) -> None:
        assert select_profile(2.0) == "gpu_low"
        assert select_profile(1.9) == "cpu"
