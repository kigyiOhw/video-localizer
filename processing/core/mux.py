"""FFmpeg 封装模块。

通过 subprocess 调用 ffmpeg，以 -c copy（流拷贝）模式给视频添加外部字幕轨。
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
    added_track_index: int   # 新添加的字幕轨索引（字幕流中的序号）
    language: str            # 设置的语言代码，e.g. "eng"


@dataclass
class SwitchDefaultResult:
    """切换默认轨道结果。"""

    input_video: Path
    output_path: Path
    output_size: int
    stream_type: str
    stream_index: int
    changed_tracks: list[dict[str, Any]]


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
        existing_sub_count=existing_sub_count,
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
# 切换默认轨道
# ---------------------------------------------------------------------------


def switch_default_track(
    video_path: Path,
    stream_type: str,
    stream_index: int,
    output_path: Path | None = None,
    ffmpeg_path: str = "ffmpeg",
    ffprobe_path: str = "ffprobe",
    container: str = "mkv",
    timeout: int = 120,
    overwrite: bool = False,
) -> SwitchDefaultResult:
    """切换指定类型轨道的默认标记。

    将目标轨道设为 default，同类型其他轨道设为 none。其他类型轨道保持不变。

    Args:
        video_path: 输入视频文件。
        stream_type: 流类型 ("video" | "audio" | "subtitle"）。
        stream_index: 同类流中的序号（0-based）。
        output_path: 输出文件路径。None 则自动生成为
                     {stem}_default_<type>_<index>.{container}。
        ffmpeg_path: ffmpeg 可执行文件路径或名称。
        ffprobe_path: ffprobe 可执行文件路径或名称（用于校验流存在）。
        container: 输出容器格式 ("mkv" | "mp4"）。
        timeout: 子进程超时秒数。
        overwrite: 是否覆盖已存在的输出文件。

    Returns:
        SwitchDefaultResult 含输出路径、大小和变更的轨道信息。

    Raises:
        MuxError: 输入/输出/ffmpeg 问题或流索引无效。
    """
    # 验证输入
    if not video_path.exists():
        raise MuxError(f"视频文件不存在: {video_path}")
    if not video_path.is_file():
        raise MuxError(f"视频路径不是文件: {video_path}")

    stream_type = stream_type.strip().lower()
    if stream_type not in ("video", "audio", "subtitle"):
        raise MuxError(f"不支持的流类型: {stream_type}（应为 video / audio / subtitle）")

    container = container.lower()
    if container not in ("mkv", "mp4"):
        raise MuxError(f"不支持的容器格式: {container}（应为 mkv / mp4）")

    # 探测流信息并校验索引
    streams = _get_streams_of_type(video_path, stream_type, ffprobe_path)
    if stream_index < 0 or stream_index >= len(streams):
        type_names = {"video": "视频", "audio": "音频", "subtitle": "字幕"}
        raise MuxError(
            f"{type_names.get(stream_type, stream_type)}流 #{stream_index} 不存在 "
            f"（共 {len(streams)} 个）"
        )

    # 自动生成输出路径
    if output_path is None:
        output_parent = video_path.parent / "output"
        output_parent.mkdir(parents=True, exist_ok=True)
        output_path = output_parent / f"{video_path.stem}_default_{stream_type}_{stream_index}.{container}"
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
    cmd = _build_switch_default_args(
        video_path=video_path,
        output_path=output_path,
        stream_type=stream_type,
        stream_index=stream_index,
        stream_count=len(streams),
        ffmpeg_path=ffmpeg_path,
        overwrite=overwrite,
    )

    logger.info("执行 ffmpeg 切换默认轨道: %s", " ".join(cmd))
    logger.debug(
        "切换默认轨道: %s, 类型=%s, 索引=%d, 输出=%s",
        video_path.name, stream_type, stream_index, output_path,
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
        raise MuxError(f"切换默认轨道超时 ({timeout}s): {video_path.name}。文件可能过大。")

    if result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else "无错误输出"
        raise MuxError(f"ffmpeg 返回非零 ({result.returncode}): {stderr}")

    # 验证输出
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise MuxError(f"封装后输出文件为空或不存在: {output_path}")

    output_size = output_path.stat().st_size

    changed_tracks = [
        {
            "type": stream_type,
            "index": i,
            "disposition": "default" if i == stream_index else "none",
        }
        for i in range(len(streams))
    ]

    logger.info(
        "切换默认轨道完成: %s → %s（类型=%s, 索引=%d）",
        video_path.name, output_path.name, stream_type, stream_index,
    )

    return SwitchDefaultResult(
        input_video=video_path,
        output_path=output_path,
        output_size=output_size,
        stream_type=stream_type,
        stream_index=stream_index,
        changed_tracks=changed_tracks,
    )


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------


def _get_streams_of_type(
    video_path: Path,
    stream_type: str,
    ffprobe_path: str,
) -> list:
    """探测视频中指定类型的流列表。

    Args:
        video_path: 输入视频。
        stream_type: "video" | "audio" | "subtitle"。
        ffprobe_path: ffprobe 可执行文件。

    Returns:
        该类型的流对象列表。

    Raises:
        MuxError: 探测失败。
    """
    from processing.core.probe import ProbeError, probe_file

    try:
        result = probe_file(video_path, ffprobe_path=ffprobe_path, timeout=30)
    except ProbeError as e:
        raise MuxError(f"探测失败: {e}")

    return {
        "video": result.video_streams,
        "audio": result.audio_streams,
        "subtitle": result.subtitle_streams,
    }[stream_type]


def _build_switch_default_args(
    video_path: Path,
    output_path: Path,
    stream_type: str,
    stream_index: int,
    stream_count: int,
    ffmpeg_path: str,
    overwrite: bool,
) -> list[str]:
    """构建 ffmpeg 切换默认轨道的命令参数。

    Args:
        video_path: 输入视频。
        output_path: 输出视频。
        stream_type: "video" | "audio" | "subtitle"。
        stream_index: 目标同类流序号（0-based）。
        stream_count: 该类型流总数。
        ffmpeg_path: ffmpeg 可执行文件。
        overwrite: 是否覆盖输出。

    Returns:
        ffmpeg 命令参数列表。
    """
    type_selectors = {"video": "v", "audio": "a", "subtitle": "s"}
    selector = type_selectors[stream_type]

    cmd = [ffmpeg_path]
    cmd.append("-y" if overwrite else "-n")
    cmd += [
        "-i", str(video_path),
        "-map", "0",
        "-c", "copy",
    ]

    for i in range(stream_count):
        disposition = "default" if i == stream_index else "none"
        cmd += [f"-disposition:{selector}:{i}", disposition]

    cmd.append(str(output_path))
    return cmd


def _build_add_subtitle_args(
    video_path: Path,
    subtitle_path: Path,
    output_path: Path,
    language: str,
    container: str,
    existing_sub_count: int,
    ffmpeg_path: str,
    overwrite: bool,
) -> list[str]:
    """构建 ffmpeg 添加字幕的命令参数。

    MKV:
      ffmpeg [-y/-n] -i <video> -i <sub> -c copy -map 0 -map 1
             -metadata:s:s:<idx> language=<lang> <output>.mkv

    MP4:
      ffmpeg [-y/-n] -i <video> -i <sub> -c copy -c:s mov_text
             -map 0 -map 1 -metadata:s:s:<idx> language=<lang> <output>.mp4

    Args:
        existing_sub_count: 输入视频已有的字幕轨数量，用于定位新轨的 metadata 索引。
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
        "-metadata:s:s:{existing_sub_count}".format(existing_sub_count=existing_sub_count), f"language={language}",
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
