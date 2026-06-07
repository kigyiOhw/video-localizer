"""FFprobe 媒体文件流探测模块。

通过 subprocess 调用 ffprobe，解析 JSON 输出为结构化 dataclass。
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("video_localizer.probe")


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------


class ProbeError(Exception):
    """探测媒体文件时发生错误。"""


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------


@dataclass
class StreamBase:
    """流基类，包含所有流类型共有的字段。"""

    index: int
    codec: str  # codec_name, e.g. "h264", "aac", "subrip"
    codec_long: str | None  # codec_long_name
    codec_type: str  # "video" | "audio" | "subtitle"
    language: str | None  # ISO 639-2, e.g. "jpn", "eng"
    disposition: dict[str, int] = field(default_factory=dict)
    tags: dict[str, str] = field(default_factory=dict)


@dataclass
class VideoStream(StreamBase):
    """视频流。"""

    width: int = 0
    height: int = 0
    pix_fmt: str = ""
    bitrate: int | None = None
    fps: str = ""  # avg_frame_rate, e.g. "24000/1001"
    fps_float: float | None = None
    duration: float | None = None
    bit_depth: int = 8


@dataclass
class AudioStream(StreamBase):
    """音频流。"""

    sample_rate: int = 0
    channels: int = 0
    channel_layout: str = ""
    bitrate: int | None = None
    duration: float | None = None


@dataclass
class SubtitleStream(StreamBase):
    """字幕流。"""

    duration: float | None = None


@dataclass
class FormatInfo:
    """容器格式信息。"""

    filename: str
    format_name: str  # e.g. "mp4", "matroska"
    format_long: str | None = None
    size_bytes: int = 0
    duration: float | None = None
    bitrate: int | None = None


@dataclass
class ProbeResult:
    """探测结果，聚合所有流和格式信息。"""

    format: FormatInfo
    video_streams: list[VideoStream]
    audio_streams: list[AudioStream]
    subtitle_streams: list[SubtitleStream]


# ---------------------------------------------------------------------------
# 公共接口
# ---------------------------------------------------------------------------


def probe_file(
    path: Path,
    ffprobe_path: str = "ffprobe",
    timeout: int = 30,
) -> ProbeResult:
    """探测媒体文件，返回结构化流信息。

    Args:
        path: 媒体文件路径。
        ffprobe_path: ffprobe 可执行文件路径或名称。
        timeout: 子进程超时秒数。

    Returns:
        ProbeResult 包含格式信息和所有流。

    Raises:
        ProbeError: 文件不存在、ffprobe 未找到、超时、或返回非零。
    """
    if not path.exists():
        raise ProbeError(f"文件不存在: {path}")
    if not path.is_file():
        raise ProbeError(f"路径不是文件: {path}")

    cmd = [
        ffprobe_path,
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]

    logger.info("执行 ffprobe: %s", " ".join(cmd))
    logger.debug("探测文件: %s (超时: %ds)", path, timeout)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        raise ProbeError(f"ffprobe 未找到: {ffprobe_path}。请确认 FFmpeg 已安装并在 PATH 中。")
    except subprocess.TimeoutExpired:
        raise ProbeError(f"探测超时 ({timeout}s): {path}。文件可能过大或损坏。")

    if result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else "无错误输出"
        raise ProbeError(f"ffprobe 返回非零 ({result.returncode}): {stderr}")

    try:
        raw = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise ProbeError(f"ffprobe 输出不是有效 JSON: {e}")

    return parse_ffprobe_output(raw)


def parse_ffprobe_output(raw: dict[str, Any]) -> ProbeResult:
    """解析 ffprobe JSON 输出为 ProbeResult。

    Args:
        raw: ffprobe -print_format json 输出的解析结果。

    Returns:
        结构化的 ProbeResult。
    """
    fmt_raw = raw.get("format", {})
    format_info = FormatInfo(
        filename=fmt_raw.get("filename", ""),
        format_name=fmt_raw.get("format_name", ""),
        format_long=fmt_raw.get("format_long_name"),
        size_bytes=int(fmt_raw.get("size", 0)),
        duration=_parse_float(fmt_raw.get("duration")),
        bitrate=_parse_int(fmt_raw.get("bit_rate")),
    )

    video_streams: list[VideoStream] = []
    audio_streams: list[AudioStream] = []
    subtitle_streams: list[SubtitleStream] = []

    for s in raw.get("streams", []):
        codec_type = s.get("codec_type", "")
        base = _parse_stream_base(s)

        if codec_type == "video":
            video_streams.append(_parse_video_stream(s, base))
        elif codec_type == "audio":
            audio_streams.append(_parse_audio_stream(s, base))
        elif codec_type == "subtitle":
            subtitle_streams.append(_parse_subtitle_stream(s, base))
        else:
            logger.debug("跳过未知流类型: codec_type=%s, index=%d", codec_type, base.index)

    logger.info(
        "探测结果: 格式=%s, 视频流=%d, 音频流=%d, 字幕流=%d, 时长=%s",
        format_info.format_name,
        len(video_streams),
        len(audio_streams),
        len(subtitle_streams),
        _format_duration(format_info.duration) if format_info.duration else "未知",
    )

    return ProbeResult(
        format=format_info,
        video_streams=video_streams,
        audio_streams=audio_streams,
        subtitle_streams=subtitle_streams,
    )


# ---------------------------------------------------------------------------
# 内部解析辅助
# ---------------------------------------------------------------------------


def _parse_stream_base(s: dict[str, Any]) -> StreamBase:
    """从 ffprobe stream 字典提取 StreamBase 公共字段。"""
    disposition = s.get("disposition", {})
    tags = s.get("tags", {})
    return StreamBase(
        index=s.get("index", 0),
        codec=s.get("codec_name", ""),
        codec_long=s.get("codec_long_name"),
        codec_type=s.get("codec_type", ""),
        language=_normalize_language(tags),
        disposition=disposition if isinstance(disposition, dict) else {},
        tags=tags if isinstance(tags, dict) else {},
    )


def _parse_video_stream(s: dict[str, Any], base: StreamBase) -> VideoStream:
    """从 ffprobe stream 字典构建 VideoStream。"""
    fps_str = s.get("avg_frame_rate", "")
    return VideoStream(
        index=base.index,
        codec=base.codec,
        codec_long=base.codec_long,
        codec_type=base.codec_type,
        language=base.language,
        disposition=base.disposition,
        tags=base.tags,
        width=int(s.get("width", 0)),
        height=int(s.get("height", 0)),
        pix_fmt=s.get("pix_fmt", ""),
        bitrate=_parse_int(s.get("bit_rate")),
        fps=fps_str,
        fps_float=_parse_fps(fps_str),
        duration=_parse_float(s.get("duration")),
        bit_depth=int(s.get("bits_per_raw_sample", s.get("bits_per_coded_sample", 8)) or 8),
    )


def _parse_audio_stream(s: dict[str, Any], base: StreamBase) -> AudioStream:
    """从 ffprobe stream 字典构建 AudioStream。"""
    return AudioStream(
        index=base.index,
        codec=base.codec,
        codec_long=base.codec_long,
        codec_type=base.codec_type,
        language=base.language,
        disposition=base.disposition,
        tags=base.tags,
        sample_rate=int(s.get("sample_rate", 0)),
        channels=int(s.get("channels", 0)),
        channel_layout=s.get("channel_layout", ""),
        bitrate=_parse_int(s.get("bit_rate")),
        duration=_parse_float(s.get("duration")),
    )


def _parse_subtitle_stream(s: dict[str, Any], base: StreamBase) -> SubtitleStream:
    """从 ffprobe stream 字典构建 SubtitleStream。"""
    return SubtitleStream(
        index=base.index,
        codec=base.codec,
        codec_long=base.codec_long,
        codec_type=base.codec_type,
        language=base.language,
        disposition=base.disposition,
        tags=base.tags,
        duration=_parse_float(s.get("duration")),
    )


def _normalize_language(tags: dict[str, Any]) -> str | None:
    """从 tags 中提取并规范化语言代码。

    ffprobe 可能将语言放在 tags.language 或使用 BCP-47 标签 (如 'en-US')。
    这里转换为 ISO 639-2 三字母代码。
    """
    lang = tags.get("language", "")
    if not lang:
        return None
    # BCP-47 → ISO 639-2 (常见映射)
    bcp47_to_iso = {
        "zh": "zho", "zh-CN": "zho", "zh-TW": "zho",
        "en": "eng", "en-US": "eng", "en-GB": "eng",
        "ja": "jpn", "ja-JP": "jpn",
        "ko": "kor", "ko-KR": "kor",
        "fr": "fra", "fr-FR": "fra",
        "de": "deu", "de-DE": "deu",
        "es": "spa", "es-ES": "spa",
        "pt": "por", "pt-BR": "por",
        "ru": "rus", "ru-RU": "rus",
        "ar": "ara", "ar-SA": "ara",
        "hi": "hin", "hi-IN": "hin",
        "it": "ita", "it-IT": "ita",
        "th": "tha", "th-TH": "tha",
        "vi": "vie", "vi-VN": "vie",
    }
    return bcp47_to_iso.get(lang, lang)  # 未知则保持原样


# ---------------------------------------------------------------------------
# 数值解析辅助
# ---------------------------------------------------------------------------


def _parse_float(value: Any) -> float | None:
    """安全解析浮点数。"""
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _parse_int(value: Any) -> int | None:
    """安全解析整数。"""
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _parse_fps(fps_str: str) -> float | None:
    """解析帧率字符串，支持分数格式如 "24000/1001" → 23.976。

    Args:
        fps_str: 帧率字符串，如 "24000/1001" 或 "30"。

    Returns:
        浮点帧率，解析失败返回 None。
    """
    if not fps_str or fps_str in ("0/0", "unknown"):
        return None
    if "/" in fps_str:
        parts = fps_str.split("/", 1)
        num = _parse_float(parts[0])
        den = _parse_float(parts[1])
        if num is not None and den is not None and den != 0:
            return round(num / den, 3)
        return None
    return _parse_float(fps_str)


# ---------------------------------------------------------------------------
# 格式化辅助（用于模板展示）
# ---------------------------------------------------------------------------


def _format_duration(seconds: float | None) -> str:
    """格式化时长为 HH:MM:SS 或 MM:SS 格式。

    Args:
        seconds: 秒数。

    Returns:
        格式化后的时长字符串。
    """
    if seconds is None:
        return "未知"
    total = int(seconds)
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _format_size(size_bytes: int) -> str:
    """格式化文件大小为人类可读格式。

    Args:
        size_bytes: 字节数。

    Returns:
        格式化后的大小字符串。
    """
    if size_bytes <= 0:
        return "未知"
    if size_bytes >= 1073741824:
        return f"{size_bytes / 1073741824:.2f} GB"
    if size_bytes >= 1048576:
        return f"{size_bytes / 1048576:.1f} MB"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.0f} KB"
    return f"{size_bytes} B"


def _safe_filename(name: str) -> str:
    """截取安全的文件名用于展示（过长时截断）。

    Args:
        name: 原始文件名。

    Returns:
        截断后安全的文件名。
    """
    if len(name) > 60:
        return name[:27] + "..." + name[-30:]
    return name
