"""Web API 共享工具函数。"""

from __future__ import annotations

from pathlib import Path


def _is_within_directory(target: Path, directory: Path) -> bool:
    """判断 target 是否位于 directory 内部（含等于 directory 自身）。

    使用 Path.is_relative_to 进行真正的路径比较，避免字符串前缀拼接在
    Windows 上的分隔符问题以及 /foo 与 /foobar 之类的误判。
    """
    try:
        target_resolved = target.resolve()
        dir_resolved = directory.resolve()
        return target_resolved.is_relative_to(dir_resolved)
    except (ValueError, OSError):
        return False
