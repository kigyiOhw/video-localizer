"""FFmpeg 流提取模块。

通过 subprocess 调用 ffmpeg，以 -c copy（流拷贝）模式提取单个流。
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("video_localizer.extract")


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------


class ExtractError(Exception):
    """提取流时发生错误。"""


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------


@dataclass
class ExtractResult:
    """单次提取结果。"""

    stream_index: int
    stream_type: str       # "video" | "audio" | "subtitle"
    codec: str             # 原始 codec，e.g. "aac", "subrip"
    output_path: Path      # 输出文件路径
    output_size: int       # 输出文件大小（字节）
    duration: float | None # 秒


# ---------------------------------------------------------------------------
# 公共接口
# ---------------------------------------------------------------------------


def extract_stream(
    input_path: Path,
    output_dir: Path,
    stream_index: int,
    stream_type: str = "audio",
    output_ext: str | None = None,
    ffmpeg_path: str = "ffmpeg",
    ffprobe_path: str = "ffprobe",
    timeout: int = 120,
    overwrite: bool = False,
) -> ExtractResult:
    """从媒体文件中提取单个流（流拷贝，无重编码）。

    Args:
        input_path: 输入媒体文件。
        output_dir: 输出目录。
        stream_index: 要提取的流索引（0-based，在同类流中的序号）。
        stream_type: 流类型 ("video" | "audio" | "subtitle")。
        output_ext: 输出扩展名（不含点），不传则根据 codec 自动推断。
        ffmpeg_path: ffmpeg 可执行文件路径或名称。
        ffprobe_path: ffprobe 可执行文件路径或名称。
        timeout: 子进程超时秒数。
        overwrite: 是否覆盖已存在的输出文件。

    Returns:
        ExtractResult 含输出路径和大小。

    Raises:
        ExtractError: 输入/输出/ffmpeg 问题。
    """
    if not input_path.exists():
        raise ExtractError(f"文件不存在: {input_path}")
    if not input_path.is_file():
        raise ExtractError(f"路径不是文件: {input_path}")
    if stream_type not in ("video", "audio", "subtitle"):
        raise ExtractError(f"不支持的流类型: {stream_type}（应为 video / audio / subtitle）")

    # 探测流的 codec 和时长（只 probe 一次）
    codec, duration = _detect_codec(input_path, ffprobe_path, stream_index, stream_type)

    # 确定输出扩展名
    ext = output_ext or _suggest_extension(codec, stream_type)
    stem = input_path.stem
    output_path = output_dir / f"{stem}_{stream_type}_{stream_index}.{ext}"

    # 检查输出冲突
    if output_path.exists() and not overwrite:
        raise ExtractError(
            f"输出文件已存在: {output_path}。请使用 overwrite=True 覆盖或更改输出路径。"
        )

    # 确保输出目录存在
    output_dir.mkdir(parents=True, exist_ok=True)

    # 构建并执行 ffmpeg 命令
    cmd = _build_ffmpeg_args(input_path, output_path, stream_index, stream_type, ffmpeg_path, overwrite)

    logger.info("执行 ffmpeg 提取: %s", " ".join(cmd))
    logger.debug(
        "提取 %s 流 #%d (codec=%s) → %s",
        stream_type, stream_index, codec, output_path,
    )

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        raise ExtractError(f"ffmpeg 未找到: {ffmpeg_path}。请确认 FFmpeg 已安装并在 PATH 中。")
    except subprocess.TimeoutExpired:
        raise ExtractError(f"提取超时 ({timeout}s): {input_path}。文件可能过大。")

    if result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else "无错误输出"
        raise ExtractError(f"ffmpeg 返回非零 ({result.returncode}): {stderr}")

    # 验证输出
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise ExtractError(f"提取后输出文件为空或不存在: {output_path}")

    output_size = output_path.stat().st_size

    logger.info(
        "提取完成: %s #%d → %s (%s)",
        stream_type, stream_index, output_path.name, _format_size(output_size),
    )

    return ExtractResult(
        stream_index=stream_index,
        stream_type=stream_type,
        codec=codec,
        output_path=output_path,
        output_size=output_size,
        duration=duration,
    )


def extract_multiple(
    input_path: Path,
    output_dir: Path,
    streams: list[dict[str, Any]],
    ffmpeg_path: str = "ffmpeg",
    ffprobe_path: str = "ffprobe",
    timeout: int = 300,
    overwrite: bool = False,
) -> list[ExtractResult]:
    """批量提取多个流。单个失败不影响后续提取。

    Args:
        input_path: 输入媒体文件。
        output_dir: 输出目录。
        streams: 流描述列表，每项为:
            {"index": int, "type": "audio"|"video"|"subtitle", "ext": str | None}
        ffmpeg_path: ffmpeg 可执行文件路径或名称。
        ffprobe_path: ffprobe 可执行文件路径或名称。
        timeout: 总超时秒数。
        overwrite: 是否覆盖已存在的输出文件。

    Returns:
        ExtractResult 列表（成功项；失败的记录到日志）。
    """
    if not streams:
        raise ExtractError("流列表为空")

    results: list[ExtractResult] = []
    errors: list[str] = []

    for s in streams:
        idx = s.get("index", 0)
        stype = s.get("type", "audio")
        ext = s.get("ext") or s.get("output_ext")
        try:
            r = extract_stream(
                input_path=input_path,
                output_dir=output_dir,
                stream_index=idx,
                stream_type=stype,
                output_ext=ext,
                ffmpeg_path=ffmpeg_path,
                ffprobe_path=ffprobe_path,
                timeout=timeout,
                overwrite=overwrite,
            )
            results.append(r)
        except ExtractError as e:
            logger.warning("提取失败 [%s #%d]: %s", stype, idx, e)
            errors.append(f"{stype} #{idx}: {e}")

    if not results and errors:
        raise ExtractError(f"所有流提取失败: {'; '.join(errors)}")

    if errors:
        logger.warning("部分提取失败: %d/%d 成功", len(results), len(results) + len(errors))

    return results


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------


def _build_ffmpeg_args(
    input_path: Path,
    output_path: Path,
    stream_index: int,
    stream_type: str,
    ffmpeg_path: str,
    overwrite: bool,
) -> list[str]:
    """构建 ffmpeg 命令参数。

    - 视频: -map 0:v:<index> -c:v copy -an -sn
    - 音频: -map 0:a:<index> -c:a copy -vn -sn
    - 字幕: -map 0:s:<index> -c:s copy -vn -an
    """
    type_map = {"video": "v", "audio": "a", "subtitle": "s"}
    prefix = type_map[stream_type]

    cmd = [ffmpeg_path]
    if overwrite:
        cmd.append("-y")
    else:
        cmd.append("-n")

    cmd += [
        "-i", str(input_path),
        "-map", f"0:{prefix}:{stream_index}",
    ]

    # 只保留目标流类型，丢弃其他
    if stream_type == "video":
        cmd += ["-c:v", "copy", "-an", "-sn"]
    elif stream_type == "audio":
        cmd += ["-c:a", "copy", "-vn", "-sn"]
    elif stream_type == "subtitle":
        cmd += ["-c:s", "copy", "-vn", "-an"]

    cmd.append(str(output_path))
    return cmd


def _detect_codec(
    input_path: Path,
    ffprobe_path: str,
    stream_index: int,
    stream_type: str,
) -> tuple[str, float | None]:
    """探测单个流的 codec 和时长（复用同一次 probe_file 调用）。

    Args:
        input_path: 媒体文件路径。
        ffprobe_path: ffprobe 可执行文件路径。
        stream_index: 同类流中的序号。
        stream_type: 流类型。

    Returns:
        (codec, duration) 元组。

    Raises:
        ExtractError: 流不存在或探测失败。
    """
    from processing.core.probe import ProbeError, probe_file

    try:
        result = probe_file(input_path, ffprobe_path=ffprobe_path, timeout=30)
    except ProbeError as e:
        raise ExtractError(f"探测失败: {e}")

    streams = {
        "video": result.video_streams,
        "audio": result.audio_streams,
        "subtitle": result.subtitle_streams,
    }[stream_type]

    if stream_index < 0 or stream_index >= len(streams):
        type_names = {"video": "视频", "audio": "音频", "subtitle": "字幕"}
        raise ExtractError(
            f"{type_names.get(stream_type, stream_type)}流 #{stream_index} 不存在 "
            f"（共 {len(streams)} 个）"
        )

    stream = streams[stream_index]
    return stream.codec, stream.duration


def _suggest_extension(codec: str, stream_type: str) -> str:
    """根据 codec 推荐输出文件扩展名。

    Args:
        codec: FFmpeg codec 名称（codec_name）。
        stream_type: "video" | "audio" | "subtitle"。

    Returns:
        推荐扩展名（不含点）。
    """
    # 视频
    if stream_type == "video":
        video_map = {
            "h264": "mkv",
            "h265": "mkv",
            "hevc": "mkv",
            "vp9": "webm",
            "av1": "mkv",
            "mpeg4": "mp4",
            "mpeg2video": "mpg",
            "theora": "ogv",
        }
        return video_map.get(codec, "mkv")

    # 音频
    if stream_type == "audio":
        audio_map = {
            "aac": "m4a",
            "mp3": "mp3",
            "opus": "opus",
            "flac": "flac",
            "vorbis": "ogg",
            "ac3": "ac3",
            "eac3": "eac3",
            "dts": "dts",
            "truehd": "thd",
            "wmav2": "wma",
            "alac": "m4a",
        }
        # PCM 类 → wav
        if codec.startswith("pcm_"):
            return "wav"
        return audio_map.get(codec, "mka")

    # 字幕
    if stream_type == "subtitle":
        sub_map = {
            "subrip": "srt",
            "ass": "ass",
            "ssa": "ass",
            "webvtt": "vtt",
            "mov_text": "srt",
            "dvd_subtitle": "sub",
            "hdmv_pgs_subtitle": "sup",
            "subrip_comment": "srt",
        }
        return sub_map.get(codec, "srt")

    return "bin"


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
