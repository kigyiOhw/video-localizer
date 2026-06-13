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


def _resolve_allowed_path(file_path: str, allowed_roots: list[Path]) -> Path:
    """解析路径并校验其位于允许的根目录之一内。

    相对路径基于第一个允许的根目录解析。绝对路径直接解析后校验。

    Args:
        file_path: 用户传入的路径字符串。
        allowed_roots: 允许访问的根目录列表，第一个用于相对路径基准。

    Returns:
        解析后的 Path。

    Raises:
        ValueError: 路径不在任何允许根目录内。
    """
    if not allowed_roots:
        raise ValueError("未配置允许访问的根目录")

    p = Path(file_path)
    if not p.is_absolute():
        p = allowed_roots[0] / p

    p = p.resolve()
    for root in allowed_roots:
        if _is_within_directory(p, root):
            return p

    raise ValueError(f"禁止访问该路径: {file_path}")
