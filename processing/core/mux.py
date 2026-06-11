"""FFmpeg 封装模块。

通过 subprocess 调用 ffmpeg，以 -c copy（流拷贝）模式给视频添加外部字幕轨。
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("video_localizer.mux")


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------


class MuxError(Exception):
    """封装失败（文件不存在、ffmpeg 未找到、流映射错误等）。"""


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------


@dataclass
class MuxResult:
    """单次封装结果。"""

    input_video: Path        # 输入视频路径
    output_path: Path        # 输出文件路径
    output_size: int         # 输出文件大小（字节）
    subtitle_count: int      # 输出文件的字幕轨总数
    added_track_index: int   # 新添加的字幕轨索引（全局 stream index）
    language: str            # 设置的语言代码，e.g. "eng"


# ---------------------------------------------------------------------------
# 公共接口
# ---------------------------------------------------------------------------


def add_subtitle(
    video_path: Path,
    subtitle_path: Path,
    language: str,
    output_path: Path | None = None,
    container: str = "mkv",
    ffmpeg_path: str = "ffmpeg",
    timeout: int = 120,
    overwrite: bool = False,
) -> MuxResult:
    """给视频添加外部字幕文件作为软字幕轨道。

    Args:
        video_path: 输入视频文件。
        subtitle_path: 外部字幕文件（.srt / .ass / .vtt）。
        language: ISO 639-2 三字母语言代码，e.g. "eng", "jpn", "zho"。
        output_path: 输出文件路径。None 则自动生成为 {stem}_subtitled.{container}。
        container: 输出容器格式 ("mkv" | "mp4")。
        ffmpeg_path: ffmpeg 可执行文件路径或名称。
        timeout: 子进程超时秒数。
        overwrite: 是否覆盖已存在的输出文件。

    Returns:
        MuxResult 含输出路径、大小和新字幕轨索引。

    Raises:
        MuxError: 输入/输出/ffmpeg 问题。
    """
    # 验证输入
    if not video_path.exists():
        raise MuxError(f"视频文件不存在: {video_path}")
    if not video_path.is_file():
        raise MuxError(f"视频路径不是文件: {video_path}")
    if not subtitle_path.exists():
        raise MuxError(f"字幕文件不存在: {subtitle_path}")
    if not subtitle_path.is_file():
        raise MuxError(f"字幕路径不是文件: {subtitle_path}")
    if not language or not language.strip():
        raise MuxError("语言代码不能为空")

    container = container.lower()
    if container not in ("mkv", "mp4"):
        raise MuxError(f"不支持的容器格式: {container}（应为 mkv / mp4）")

    # 验证字幕格式
    _validate_subtitle_format(subtitle_path)

    # 计算已有字幕轨数量，确定新轨的 metadata 索引
    existing_sub_count = _count_subtitle_streams(video_path, ffmpeg_path)

    # 自动生成输出路径
    if output_path is None:
        output_parent = video_path.parent / "output"
        output_parent.mkdir(parents=True, exist_ok=True)
        output_path = output_parent / f"{video_path.stem}_subtitled.{container}"
    else:
        output_path = Path(output_path)

    # 检查输出冲突
    if output_path.exists() and not overwrite:
        raise MuxError(
            f"输出文件已存在: {output_path}。请使用 overwrite=True 覆盖或更改输出路径。"
        )

    # 确保输出目录存在
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 构建并执行 ffmpeg 命令
    cmd = _build_add_subtitle_args(
        video_path=video_path,
        subtitle_path=subtitle_path,
        output_path=output_path,
        language=language,
        container=container,
        ffmpeg_path=ffmpeg_path,
        overwrite=overwrite,
    )

    logger.info("执行 ffmpeg 添加字幕: %s", " ".join(cmd))
    logger.debug(
        "给 %s 添加字幕轨 (语言=%s, 容器=%s, 已有字幕轨=%d) → %s",
        video_path.name, language, container, existing_sub_count, output_path,
    )

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        raise MuxError(f"ffmpeg 未找到: {ffmpeg_path}。请确认 FFmpeg 已安装并在 PATH 中。")
    except subprocess.TimeoutExpired:
        raise MuxError(f"添加字幕超时 ({timeout}s): {video_path.name}。文件可能过大。")

    if result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else "无错误输出"
        raise MuxError(f"ffmpeg 返回非零 ({result.returncode}): {stderr}")

    # 验证输出
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise MuxError(f"封装后输出文件为空或不存在: {output_path}")

    output_size = output_path.stat().st_size
    # 新轨的全局索引 = 原视频总流数（因为新字幕轨追加到末尾）
    added_track_index = existing_sub_count

    logger.info(
        "字幕添加完成: %s + %s → %s（字幕轨: %d 个, 新增索引: %d）",
        video_path.name, subtitle_path.name, output_path.name,
        existing_sub_count + 1, added_track_index,
    )

    return MuxResult(
        input_video=video_path,
        output_path=output_path,
        output_size=output_size,
        subtitle_count=existing_sub_count + 1,
        added_track_index=added_track_index,
        language=language.strip(),
    )


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------


def _build_add_subtitle_args(
    video_path: Path,
    subtitle_path: Path,
    output_path: Path,
    language: str,
    container: str,
    ffmpeg_path: str,
    overwrite: bool,
) -> list[str]:
    """构建 ffmpeg 添加字幕的命令参数。

    MKV:
      ffmpeg [-y/-n] -i <video> -i <sub> -c copy -map 0 -map 1
             -metadata:s:s:0 language=<lang> <output>.mkv

    MP4:
      ffmpeg [-y/-n] -i <video> -i <sub> -c copy -c:s mov_text
             -map 0 -map 1 -metadata:s:s:0 language=<lang> <output>.mp4
    """
    cmd = [ffmpeg_path]
    cmd.append("-y" if overwrite else "-n")

    cmd += [
        "-i", str(video_path),
        "-i", str(subtitle_path),
        "-c", "copy",
    ]

    if container == "mp4":
        cmd += ["-c:s", "mov_text"]

    cmd += [
        "-map", "0",
        "-map", "1",
        "-metadata:s:s:0", f"language={language}",
        str(output_path),
    ]

    return cmd


def _validate_subtitle_format(path: Path) -> str:
    """验证字幕文件格式并返回 codec 名称。

    支持的格式:
      .srt → subrip
      .ass / .ssa → ass
      .vtt → webvtt

    Raises:
        MuxError: 不支持的字幕格式。
    """
    ext = path.suffix.lower()
    mapping = {
        ".srt": "subrip",
        ".ass": "ass",
        ".ssa": "ass",
        ".vtt": "webvtt",
    }
    codec = mapping.get(ext)
    if codec is None:
        raise MuxError(
            f"不支持的字幕格式: {ext}（支持: {', '.join(sorted(mapping.keys()))}）"
        )
    return codec


def _count_subtitle_streams(video_path: Path, ffprobe_path: str) -> int:
    """探测视频中已有的字幕轨数量。

    复用 Stage 2 probe_file 获取字幕流列表。
    """
    from processing.core.probe import ProbeError, probe_file

    try:
        result = probe_file(video_path, ffprobe_path=ffprobe_path, timeout=30)
    except ProbeError:
        # 探测失败时假定无字幕轨
        logger.debug("探测视频流信息失败，假定字幕轨数为 0", exc_info=True)
        return 0

    return len(result.subtitle_streams)


def _format_size(size_bytes: int) -> str:
    """格式化文件大小为人类可读格式。"""
    if size_bytes <= 0:
        return "0 B"
    if size_bytes >= 1073741824:
        return f"{size_bytes / 1073741824:.2f} GB"
    if size_bytes >= 1048576:
        return f"{size_bytes / 1048576:.1f} MB"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.0f} KB"
    return f"{size_bytes} B"
