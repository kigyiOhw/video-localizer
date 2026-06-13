"""音频轨管理模块。

通过 subprocess 调用 ffmpeg，实现音频轨的添加、替换、移除、静音、同步偏移和速度调整。
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("video_localizer.audio")


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------


class AudioTrackError(Exception):
    """音频轨操作失败（文件不存在、ffmpeg 错误、流索引无效等）。"""


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------


@dataclass
class AudioTrackResult:
    """音频轨操作结果。"""

    input_video: Path
    output_path: Path
    output_size: int
    operation: str
    audio_index: int | None
    extra: dict[str, Any]


# ---------------------------------------------------------------------------
# 公共接口
# ---------------------------------------------------------------------------


def add_audio_track(
    video_path: Path,
    audio_path: Path,
    language: str = "und",
    set_default: bool = False,
    output_path: Path | None = None,
    container: str = "mkv",
    ffmpeg_path: str = "ffmpeg",
    timeout: int = 120,
    overwrite: bool = False,
) -> AudioTrackResult:
    """给视频追加外部音频轨道。

    Args:
        video_path: 输入视频文件。
        audio_path: 外部音频文件。
        language: ISO 639-2 三字母语言代码，默认 und。
        set_default: 是否将新音轨设为默认。
        output_path: 输出文件路径。None 则自动生成为 {stem}_added_audio.{container}。
        container: 输出容器格式 ("mkv" | "mp4")。
        ffmpeg_path: ffmpeg 可执行文件路径或名称。
        timeout: 子进程超时秒数。
        overwrite: 是否覆盖已存在的输出文件。

    Returns:
        AudioTrackResult 含输出路径、大小和新音轨信息。

    Raises:
        AudioTrackError: 输入/输出/ffmpeg 问题。
    """
    _validate_input_files(video_path=video_path, audio_path=audio_path)
    container = _validate_container(container)

    audio_streams = _get_audio_streams(video_path, ffprobe_path="ffprobe")
    added_index = len(audio_streams)

    if output_path is None:
        output_parent = video_path.parent / "output"
        output_parent.mkdir(parents=True, exist_ok=True)
        output_path = output_parent / f"{video_path.stem}_added_audio.{container}"
    else:
        output_path = Path(output_path)

    _check_output_conflict(output_path, overwrite)

    cmd = _build_add_audio_args(
        video_path=video_path,
        audio_path=audio_path,
        output_path=output_path,
        language=language,
        existing_audio_count=added_index,
        set_default=set_default,
        ffmpeg_path=ffmpeg_path,
        overwrite=overwrite,
    )

    logger.info("执行 ffmpeg 追加音频轨: %s", " ".join(cmd))
    _run_ffmpeg(cmd, timeout, video_path.name, "追加音频轨")

    output_size = output_path.stat().st_size
    return AudioTrackResult(
        input_video=video_path,
        output_path=output_path,
        output_size=output_size,
        operation="add",
        audio_index=None,
        extra={"added_index": added_index, "language": language, "set_default": set_default},
    )


def replace_audio_track(
    video_path: Path,
    audio_path: Path,
    audio_index: int,
    language: str = "und",
    output_path: Path | None = None,
    container: str = "mkv",
    ffmpeg_path: str = "ffmpeg",
    ffprobe_path: str = "ffprobe",
    timeout: int = 120,
    overwrite: bool = False,
) -> AudioTrackResult:
    """替换指定音频轨道（移除原轨道，插入外部音频，其他流保留）。

    Args:
        video_path: 输入视频文件。
        audio_path: 外部音频文件。
        audio_index: 要替换的音频流同类序号（0-based）。
        language: ISO 639-2 三字母语言代码。
        output_path: 输出文件路径。None 则自动生成为 {stem}_replaced_audio_{index}.{container}。
        container: 输出容器格式 ("mkv" | "mp4")。
        ffmpeg_path: ffmpeg 可执行文件路径或名称。
        ffprobe_path: ffprobe 可执行文件路径或名称。
        timeout: 子进程超时秒数。
        overwrite: 是否覆盖已存在的输出文件。

    Returns:
        AudioTrackResult 含输出路径、大小和替换信息。

    Raises:
        AudioTrackError: 输入/输出/ffmpeg 问题或流索引无效。
    """
    _validate_input_files(video_path=video_path, audio_path=audio_path)
    container = _validate_container(container)

    probe = _probe_video(video_path, ffprobe_path)
    audio_streams = probe.audio_streams
    if audio_streams:
        _validate_audio_index(audio_streams, audio_index, "替换")

    if output_path is None:
        output_parent = video_path.parent / "output"
        output_parent.mkdir(parents=True, exist_ok=True)
        output_path = output_parent / f"{video_path.stem}_replaced_audio_{audio_index}.{container}"
    else:
        output_path = Path(output_path)

    _check_output_conflict(output_path, overwrite)

    cmd = _build_replace_audio_args(
        video_path=video_path,
        audio_path=audio_path,
        output_path=output_path,
        audio_index=audio_index,
        language=language,
        video_count=len(probe.video_streams),
        audio_count=len(audio_streams),
        subtitle_count=len(probe.subtitle_streams),
        ffmpeg_path=ffmpeg_path,
        overwrite=overwrite,
    )

    logger.info("执行 ffmpeg 替换音频轨: %s", " ".join(cmd))
    _run_ffmpeg(cmd, timeout, video_path.name, "替换音频轨")

    output_size = output_path.stat().st_size
    return AudioTrackResult(
        input_video=video_path,
        output_path=output_path,
        output_size=output_size,
        operation="replace",
        audio_index=audio_index,
        extra={"language": language},
    )


def remove_audio_track(
    video_path: Path,
    audio_index: int,
    output_path: Path | None = None,
    container: str = "mkv",
    ffmpeg_path: str = "ffmpeg",
    ffprobe_path: str = "ffprobe",
    timeout: int = 120,
    overwrite: bool = False,
) -> AudioTrackResult:
    """移除指定音频轨道，保留其他所有流。

    Args:
        video_path: 输入视频文件。
        audio_index: 要移除的音频流同类序号（0-based）。
        output_path: 输出文件路径。None 则自动生成为 {stem}_removed_audio_{index}.{container}。
        container: 输出容器格式 ("mkv" | "mp4")。
        ffmpeg_path: ffmpeg 可执行文件路径或名称。
        ffprobe_path: ffprobe 可执行文件路径或名称。
        timeout: 子进程超时秒数。
        overwrite: 是否覆盖已存在的输出文件。

    Returns:
        AudioTrackResult 含输出路径、大小和剩余音轨数。

    Raises:
        AudioTrackError: 输入/输出/ffmpeg 问题或流索引无效。
    """
    _validate_input_file(video_path, "视频")
    container = _validate_container(container)

    probe = _probe_video(video_path, ffprobe_path)
    audio_streams = probe.audio_streams
    _validate_audio_index(audio_streams, audio_index, "移除")

    if output_path is None:
        output_parent = video_path.parent / "output"
        output_parent.mkdir(parents=True, exist_ok=True)
        output_path = output_parent / f"{video_path.stem}_removed_audio_{audio_index}.{container}"
    else:
        output_path = Path(output_path)

    _check_output_conflict(output_path, overwrite)

    cmd = _build_remove_audio_args(
        video_path=video_path,
        output_path=output_path,
        audio_index=audio_index,
        video_count=len(probe.video_streams),
        audio_count=len(audio_streams),
        subtitle_count=len(probe.subtitle_streams),
        ffmpeg_path=ffmpeg_path,
        overwrite=overwrite,
    )

    logger.info("执行 ffmpeg 移除音频轨: %s", " ".join(cmd))
    _run_ffmpeg(cmd, timeout, video_path.name, "移除音频轨")

    output_size = output_path.stat().st_size
    remaining = len(audio_streams) - 1
    return AudioTrackResult(
        input_video=video_path,
        output_path=output_path,
        output_size=output_size,
        operation="remove",
        audio_index=audio_index,
        extra={"remaining": remaining},
    )


def mute_audio_track(
    video_path: Path,
    audio_index: int | None = None,
    output_path: Path | None = None,
    container: str = "mkv",
    ffmpeg_path: str = "ffmpeg",
    ffprobe_path: str = "ffprobe",
    timeout: int = 120,
    overwrite: bool = False,
) -> AudioTrackResult:
    """静音音频轨道（单轨或全部）。

    使用 volume=0 滤镜处理目标音频流，视频与其他流保持 copy。

    Args:
        video_path: 输入视频文件。
        audio_index: 要静音的音频流同类序号（0-based）。None 表示全部静音。
        output_path: 输出文件路径。None 则自动生成。
        container: 输出容器格式 ("mkv" | "mp4")。
        ffmpeg_path: ffmpeg 可执行文件路径或名称。
        ffprobe_path: ffprobe 可执行文件路径或名称。
        timeout: 子进程超时秒数。
        overwrite: 是否覆盖已存在的输出文件。

    Returns:
        AudioTrackResult 含输出路径、大小和静音信息。

    Raises:
        AudioTrackError: 输入/输出/ffmpeg 问题或流索引无效。
    """
    _validate_input_file(video_path, "视频")
    container = _validate_container(container)

    probe = _probe_video(video_path, ffprobe_path)
    audio_streams = probe.audio_streams
    if audio_index is not None:
        _validate_audio_index(audio_streams, audio_index, "静音")

    if output_path is None:
        output_parent = video_path.parent / "output"
        output_parent.mkdir(parents=True, exist_ok=True)
        suffix = f"_{audio_index}" if audio_index is not None else ""
        output_path = output_parent / f"{video_path.stem}_muted_audio{suffix}.{container}"
    else:
        output_path = Path(output_path)

    _check_output_conflict(output_path, overwrite)

    cmd = _build_mute_args(
        video_path=video_path,
        output_path=output_path,
        audio_index=audio_index,
        video_count=len(probe.video_streams),
        audio_count=len(audio_streams),
        subtitle_count=len(probe.subtitle_streams),
        ffmpeg_path=ffmpeg_path,
        overwrite=overwrite,
    )

    logger.info("执行 ffmpeg 静音音频轨: %s", " ".join(cmd))
    _run_ffmpeg(cmd, timeout, video_path.name, "静音音频轨")

    output_size = output_path.stat().st_size
    return AudioTrackResult(
        input_video=video_path,
        output_path=output_path,
        output_size=output_size,
        operation="mute",
        audio_index=audio_index,
        extra={},
    )


def adjust_audio_sync(
    video_path: Path,
    offset_seconds: float,
    audio_index: int | None = None,
    output_path: Path | None = None,
    container: str = "mkv",
    ffmpeg_path: str = "ffmpeg",
    ffprobe_path: str = "ffprobe",
    timeout: int = 120,
    overwrite: bool = False,
) -> AudioTrackResult:
    """调整音频同步偏移。

    正数表示音频延后（比画面晚出现），负数表示音频提前。
    使用双输入同文件，第二路用 -itsoffset 偏移。

    Args:
        video_path: 输入视频文件。
        offset_seconds: 偏移秒数。
        audio_index: 要调整的音频流同类序号（0-based）。None 表示全部音频。
        output_path: 输出文件路径。None 则自动生成。
        container: 输出容器格式 ("mkv" | "mp4")。
        ffmpeg_path: ffmpeg 可执行文件路径或名称。
        ffprobe_path: ffprobe 可执行文件路径或名称。
        timeout: 子进程超时秒数。
        overwrite: 是否覆盖已存在的输出文件。

    Returns:
        AudioTrackResult 含输出路径、大小和偏移信息。

    Raises:
        AudioTrackError: 输入/输出/ffmpeg 问题或流索引无效。
    """
    _validate_input_file(video_path, "视频")
    container = _validate_container(container)

    probe = _probe_video(video_path, ffprobe_path)
    audio_streams = probe.audio_streams
    if audio_index is not None:
        _validate_audio_index(audio_streams, audio_index, "同步")

    if output_path is None:
        output_parent = video_path.parent / "output"
        output_parent.mkdir(parents=True, exist_ok=True)
        suffix = f"_{audio_index}" if audio_index is not None else ""
        offset_str = f"{offset_seconds:+.1f}"
        output_path = output_parent / f"{video_path.stem}_synced_audio{suffix}_{offset_str}.{container}"
    else:
        output_path = Path(output_path)

    _check_output_conflict(output_path, overwrite)

    cmd = _build_sync_args(
        video_path=video_path,
        output_path=output_path,
        offset_seconds=offset_seconds,
        audio_index=audio_index,
        video_count=len(probe.video_streams),
        audio_count=len(audio_streams),
        subtitle_count=len(probe.subtitle_streams),
        ffmpeg_path=ffmpeg_path,
        overwrite=overwrite,
    )

    logger.info("执行 ffmpeg 调整音频同步: %s", " ".join(cmd))
    _run_ffmpeg(cmd, timeout, video_path.name, "调整音频同步")

    output_size = output_path.stat().st_size
    return AudioTrackResult(
        input_video=video_path,
        output_path=output_path,
        output_size=output_size,
        operation="sync",
        audio_index=audio_index,
        extra={"offset_seconds": offset_seconds},
    )


def adjust_audio_speed(
    video_path: Path,
    speed_ratio: float,
    audio_index: int | None = None,
    output_path: Path | None = None,
    container: str = "mkv",
    ffmpeg_path: str = "ffmpeg",
    ffprobe_path: str = "ffprobe",
    timeout: int = 120,
    overwrite: bool = False,
) -> AudioTrackResult:
    """调整音频速度（变速不变调）。

    使用 atempo 滤镜。单次范围 0.5–2.0，超出时自动链式使用多个 atempo。

    Args:
        video_path: 输入视频文件。
        speed_ratio: 速度比例，1.0 表示不变，1.05 表示加速 5%。
        audio_index: 要调整的音频流同类序号（0-based）。None 表示全部音频。
        output_path: 输出文件路径。None 则自动生成。
        container: 输出容器格式 ("mkv" | "mp4")。
        ffmpeg_path: ffmpeg 可执行文件路径或名称。
        ffprobe_path: ffprobe 可执行文件路径或名称。
        timeout: 子进程超时秒数。
        overwrite: 是否覆盖已存在的输出文件。

    Returns:
        AudioTrackResult 含输出路径、大小和速度信息。

    Raises:
        AudioTrackError: 输入/输出/ffmpeg 问题或流索引无效。
    """
    _validate_input_file(video_path, "视频")
    container = _validate_container(container)

    probe = _probe_video(video_path, ffprobe_path)
    audio_streams = probe.audio_streams
    if audio_index is not None:
        _validate_audio_index(audio_streams, audio_index, "变速")

    if output_path is None:
        output_parent = video_path.parent / "output"
        output_parent.mkdir(parents=True, exist_ok=True)
        suffix = f"_{audio_index}" if audio_index is not None else ""
        ratio_str = str(speed_ratio)
        output_path = output_parent / f"{video_path.stem}_speed_{ratio_str}_audio{suffix}.{container}"
    else:
        output_path = Path(output_path)

    _check_output_conflict(output_path, overwrite)

    cmd = _build_speed_args(
        video_path=video_path,
        output_path=output_path,
        speed_ratio=speed_ratio,
        audio_index=audio_index,
        video_count=len(probe.video_streams),
        audio_count=len(audio_streams),
        subtitle_count=len(probe.subtitle_streams),
        ffmpeg_path=ffmpeg_path,
        overwrite=overwrite,
    )

    logger.info("执行 ffmpeg 调整音频速度: %s", " ".join(cmd))
    _run_ffmpeg(cmd, timeout, video_path.name, "调整音频速度")

    output_size = output_path.stat().st_size
    return AudioTrackResult(
        input_video=video_path,
        output_path=output_path,
        output_size=output_size,
        operation="speed",
        audio_index=audio_index,
        extra={"speed_ratio": speed_ratio},
    )


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------


def _validate_input_file(path: Path, name: str) -> None:
    """校验单个输入文件存在且为文件。"""
    if not path.exists():
        raise AudioTrackError(f"{name}文件不存在: {path}")
    if not path.is_file():
        raise AudioTrackError(f"{name}路径不是文件: {path}")


def _validate_input_files(video_path: Path, audio_path: Path) -> None:
    """校验视频和音频输入文件。"""
    _validate_input_file(video_path, "视频")
    _validate_input_file(audio_path, "音频")


def _validate_container(container: str) -> str:
    """校验输出容器格式。"""
    container = container.lower()
    if container not in ("mkv", "mp4"):
        raise AudioTrackError(f"不支持的容器格式: {container}（应为 mkv / mp4）")
    return container


def _check_output_conflict(output_path: Path, overwrite: bool) -> None:
    """检查输出文件冲突并确保目录存在。"""
    if output_path.exists() and not overwrite:
        raise AudioTrackError(
            f"输出文件已存在: {output_path}。请使用 overwrite=True 覆盖或更改输出路径。"
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)


def _probe_video(video_path: Path, ffprobe_path: str) -> Any:
    """探测视频文件，返回 ProbeResult。"""
    from processing.core.probe import ProbeError, probe_file

    try:
        return probe_file(video_path, ffprobe_path=ffprobe_path, timeout=30)
    except ProbeError as e:
        raise AudioTrackError(f"探测失败: {e}")


def _get_audio_streams(video_path: Path, ffprobe_path: str = "ffprobe") -> list:
    """获取视频中的音频流列表。"""
    return _probe_video(video_path, ffprobe_path).audio_streams


def _validate_audio_index(audio_streams: list, audio_index: int, operation: str) -> None:
    """校验音频流索引有效性。"""
    if audio_index < 0 or audio_index >= len(audio_streams):
        raise AudioTrackError(
            f"音频流 #{audio_index} 不存在（共 {len(audio_streams)} 个），无法{operation}。"
        )


def _run_ffmpeg(cmd: list[str], timeout: int, filename: str, operation_name: str) -> None:
    """执行 ffmpeg 命令并处理错误。"""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        raise AudioTrackError(f"ffmpeg 未找到。请确认 FFmpeg 已安装并在 PATH 中。")
    except subprocess.TimeoutExpired:
        raise AudioTrackError(f"{operation_name}超时 ({timeout}s): {filename}。文件可能过大。")

    if result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else "无错误输出"
        raise AudioTrackError(f"ffmpeg 返回非零 ({result.returncode}): {stderr}")

    output_path = Path(cmd[-1])
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise AudioTrackError(f"处理后输出文件为空或不存在: {output_path}")


def _build_add_audio_args(
    video_path: Path,
    audio_path: Path,
    output_path: Path,
    language: str,
    existing_audio_count: int,
    set_default: bool,
    ffmpeg_path: str,
    overwrite: bool,
) -> list[str]:
    """构建 ffmpeg 追加音频轨命令参数。"""
    cmd = [ffmpeg_path]
    cmd.append("-y" if overwrite else "-n")
    cmd += [
        "-i", str(video_path),
        "-i", str(audio_path),
        "-map", "0",
        "-map", "1",
        "-c", "copy",
        f"-metadata:s:a:{existing_audio_count}", f"language={language}",
    ]
    if set_default:
        cmd += [f"-disposition:a:{existing_audio_count}", "default"]
    cmd.append(str(output_path))
    return cmd


def _build_replace_audio_args(
    video_path: Path,
    audio_path: Path,
    output_path: Path,
    audio_index: int,
    language: str,
    video_count: int,
    audio_count: int,
    subtitle_count: int,
    ffmpeg_path: str,
    overwrite: bool,
) -> list[str]:
    """构建 ffmpeg 替换音频轨命令参数。"""
    cmd = [ffmpeg_path]
    cmd.append("-y" if overwrite else "-n")
    cmd += [
        "-i", str(video_path),
        "-i", str(audio_path),
    ]

    # 视频流
    for _ in range(video_count):
        cmd += ["-map", "0:v"]

    # 替换点之前的音频流
    for i in range(audio_index):
        cmd += ["-map", f"0:a:{i}"]

    # 新音频流
    cmd += ["-map", "1:a"]

    # 替换点之后的音频流
    for i in range(audio_index + 1, audio_count):
        cmd += ["-map", f"0:a:{i}"]

    # 字幕流
    for _ in range(subtitle_count):
        cmd += ["-map", "0:s"]

    cmd += [
        "-c", "copy",
        f"-metadata:s:a:{audio_index}", f"language={language}",
    ]
    cmd.append(str(output_path))
    return cmd


def _build_remove_audio_args(
    video_path: Path,
    output_path: Path,
    audio_index: int,
    video_count: int,
    audio_count: int,
    subtitle_count: int,
    ffmpeg_path: str,
    overwrite: bool,
) -> list[str]:
    """构建 ffmpeg 移除音频轨命令参数。"""
    cmd = [ffmpeg_path]
    cmd.append("-y" if overwrite else "-n")
    cmd += ["-i", str(video_path)]

    for _ in range(video_count):
        cmd += ["-map", "0:v"]

    for i in range(audio_count):
        if i == audio_index:
            continue
        cmd += ["-map", f"0:a:{i}"]

    for _ in range(subtitle_count):
        cmd += ["-map", "0:s"]

    cmd += ["-c", "copy", str(output_path)]
    return cmd


def _build_mute_args(
    video_path: Path,
    output_path: Path,
    audio_index: int | None,
    video_count: int,
    audio_count: int,
    subtitle_count: int,
    ffmpeg_path: str,
    overwrite: bool,
) -> list[str]:
    """构建 ffmpeg 静音音频轨命令参数。"""
    cmd = [ffmpeg_path]
    cmd.append("-y" if overwrite else "-n")
    cmd += ["-i", str(video_path)]

    target_indexes = list(range(audio_count)) if audio_index is None else [audio_index]

    filters = []
    for i in target_indexes:
        filters.append(f"[0:a:{i}]volume=0[m{i}]")
    if filters:
        cmd += ["-filter_complex", ";".join(filters)]

    for _ in range(video_count):
        cmd += ["-map", "0:v"]

    for i in range(audio_count):
        if i in target_indexes:
            cmd += ["-map", f"[m{i}]"]
        else:
            cmd += ["-map", f"0:a:{i}"]

    for _ in range(subtitle_count):
        cmd += ["-map", "0:s"]

    cmd += [
        "-c:v", "copy",
        "-c:a", "aac",
        str(output_path),
    ]
    return cmd


def _build_sync_args(
    video_path: Path,
    output_path: Path,
    offset_seconds: float,
    audio_index: int | None,
    video_count: int,
    audio_count: int,
    subtitle_count: int,
    ffmpeg_path: str,
    overwrite: bool,
) -> list[str]:
    """构建 ffmpeg 音频同步偏移命令参数。"""
    cmd = [ffmpeg_path]
    cmd.append("-y" if overwrite else "-n")
    cmd += [
        "-i", str(video_path),
        "-itsoffset", str(offset_seconds),
        "-i", str(video_path),
    ]

    for _ in range(video_count):
        cmd += ["-map", "0:v"]

    if audio_index is None:
        # 所有音频从偏移后的第二路输入取
        for i in range(audio_count):
            cmd += ["-map", f"1:a:{i}"]
    else:
        # 目标音频从第二路输入取，其他从第一路取
        for i in range(audio_count):
            if i == audio_index:
                cmd += ["-map", f"1:a:{i}"]
            else:
                cmd += ["-map", f"0:a:{i}"]

    for _ in range(subtitle_count):
        cmd += ["-map", "0:s"]

    cmd += ["-c", "copy", str(output_path)]
    return cmd


def _build_speed_args(
    video_path: Path,
    output_path: Path,
    speed_ratio: float,
    audio_index: int | None,
    video_count: int,
    audio_count: int,
    subtitle_count: int,
    ffmpeg_path: str,
    overwrite: bool,
) -> list[str]:
    """构建 ffmpeg 音频速度调整命令参数。"""
    cmd = [ffmpeg_path]
    cmd.append("-y" if overwrite else "-n")
    cmd += ["-i", str(video_path)]

    target_indexes = list(range(audio_count)) if audio_index is None else [audio_index]
    atempo_filter = _chain_atempo(speed_ratio)

    filters = []
    for i in target_indexes:
        filters.append(f"[0:a:{i}]{atempo_filter}[s{i}]")
    if filters:
        cmd += ["-filter_complex", ";".join(filters)]

    for _ in range(video_count):
        cmd += ["-map", "0:v"]

    for i in range(audio_count):
        if i in target_indexes:
            cmd += ["-map", f"[s{i}]"]
        else:
            cmd += ["-map", f"0:a:{i}"]

    for _ in range(subtitle_count):
        cmd += ["-map", "0:s"]

    cmd += [
        "-c:v", "copy",
        "-c:a", "aac",
        str(output_path),
    ]
    return cmd


def _chain_atempo(ratio: float) -> str:
    """计算 atempo 滤镜链字符串。

    atempo 单次只接受 0.5–2.0，超出范围时链式使用多个 atempo。
    """
    if 0.5 <= ratio <= 2.0:
        return f"atempo={ratio}"
    if ratio > 2.0:
        return f"atempo=2.0,{_chain_atempo(ratio / 2.0)}"
    return f"atempo=0.5,{_chain_atempo(ratio / 0.5)}"


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
