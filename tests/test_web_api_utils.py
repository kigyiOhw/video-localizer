"""web.api.utils 共享工具测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from web.api.utils import _is_within_directory


class TestIsWithinDirectory:
    """_is_within_directory 路径安全测试。"""

    def test_file_inside_directory(self, tmp_path: Path) -> None:
        """目录内文件返回 True。"""
        base = tmp_path / "output"
        base.mkdir()
        target = base / "movie.mkv"
        target.write_text("fake")
        assert _is_within_directory(target, base) is True

    def test_directory_itself(self, tmp_path: Path) -> None:
        """target 等于 directory 返回 True。"""
        base = tmp_path / "output"
        base.mkdir()
        assert _is_within_directory(base, base) is True

    def test_subdirectory(self, tmp_path: Path) -> None:
        """子目录返回 True。"""
        base = tmp_path / "output"
        sub = base / "nested"
        sub.mkdir(parents=True)
        assert _is_within_directory(sub, base) is True

    def test_parent_directory(self, tmp_path: Path) -> None:
        """上级目录返回 False。"""
        base = tmp_path / "output"
        base.mkdir()
        secret = tmp_path / "secret.txt"
        secret.write_text("secret")
        assert _is_within_directory(secret, base) is False

    def test_similar_prefix(self, tmp_path: Path) -> None:
        """字符串前缀相似但不是子目录时返回 False。"""
        base = tmp_path / "output"
        base.mkdir()
        similar = tmp_path / "output_extra"
        similar.mkdir()
        target = similar / "file.txt"
        target.write_text("x")
        assert _is_within_directory(target, base) is False

    def test_nonexistent_target(self, tmp_path: Path) -> None:
        """不存在的目标路径仍可通过 .. 解析判断。"""
        base = tmp_path / "output"
        base.mkdir()
        target = base / ".." / "secret.txt"
        assert _is_within_directory(target, base) is False

    def test_relative_path_target(self, tmp_path: Path) -> None:
        """相对路径目标解析后判断。"""
        base = tmp_path / "output"
        base.mkdir()
        # 切换当前工作目录到 tmp_path，使相对路径可解析
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            assert _is_within_directory(Path("output/movie.mkv"), Path("output")) is True
            assert _is_within_directory(Path("secret.txt"), Path("output")) is False
        finally:
            os.chdir(old_cwd)
