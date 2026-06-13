"""音频轨管理 REST 端点。

POST /api/audio/add     — 追加外部音频轨
POST /api/audio/replace — 替换音频轨
POST /api/audio/remove  — 移除音频轨
POST /api/audio/mute    — 静音音频轨
POST /api/audio/sync    — 调整音频同步
POST /api/audio/speed   — 调整音频速度
GET  /api/audio/download — 下载输出文件
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from web.api.utils import _is_within_directory, _resolve_allowed_path

logger = logging.getLogger("video_localizer.api.audio")

router = APIRouter(prefix="/api/audio")


def _get_templates(request: Request):
    """延迟导入避免循环引用。"""
    from app import templates
    return templates


def _get_settings():
    """延迟导入避免循环引用。"""
    from app import settings
    return settings


@router.post("/add", response_model=None)
async def audio_add_post(
    request: Request,
    file_path: str | None = Form(None),
    audio_path: str | None = Form(None),
    language: str | None = Form("und"),
    set_default: str | None = Form("false"),
    container: str | None = Form("mkv"),
):
    """POST /api/audio/add — 追加外部音频轨。"""
    settings = _get_settings()
    templates = _get_templates(request)

    if not file_path or not file_path.strip():
        return _error_response(request, templates, "请提供 file_path 参数。", 400)
    if not audio_path or not audio_path.strip():
        return _error_response(request, templates, "请提供 audio_path 参数。", 400)

    container = (container or "mkv").lower()
    if container not in ("mkv", "mp4"):
        return _error_response(
            request, templates,
            f"不支持的容器格式: {container}（应为 mkv / mp4）", 400,
        )

    try:
        resolved_video = _resolve_allowed_path(
            file_path.strip(),
            [settings.paths.media_input, settings.paths.temp_dir],
        )
        resolved_audio = _resolve_allowed_path(
            audio_path.strip(),
            [settings.paths.media_input, settings.paths.temp_dir],
        )
    except ValueError as e:
        logger.warning("追加音频轨请求路径非法: %s", e)
        return _error_response(request, templates, str(e), 403)

    output_path = settings.paths.media_output / f"{resolved_video.stem}_added_audio.{container}"

    set_default_bool = set_default.strip().lower() in ("true", "on", "1", "yes")

    logger.info(
        "追加音频轨请求: 视频=%s, 音频=%s, 语言=%s, 默认=%s, 容器=%s",
        resolved_video.name, resolved_audio.name, language, set_default_bool, container,
    )

    from processing.core.audio import AudioTrackError, add_audio_track

    try:
        result = add_audio_track(
            video_path=resolved_video,
            audio_path=resolved_audio,
            language=language.strip() or "und",
            set_default=set_default_bool,
            output_path=output_path,
            container=container,
            ffmpeg_path=settings.ffmpeg.executable,
            timeout=120,
            overwrite=True,
        )
    except AudioTrackError as e:
        logger.warning("追加音频轨失败: %s", e)
        return _error_response(request, templates, str(e), 422)
    except Exception as e:
        logger.warning("追加音频轨出错: %s", e, exc_info=True)
        return _error_response(request, templates, f"内部错误: {e}", 500)

    return _success_response(request, templates, result)


@router.post("/replace", response_model=None)
async def audio_replace_post(
    request: Request,
    file_path: str | None = Form(None),
    audio_path: str | None = Form(None),
    audio_index: int | None = Form(None),
    language: str | None = Form("und"),
    container: str | None = Form("mkv"),
):
    """POST /api/audio/replace — 替换指定音频轨。"""
    settings = _get_settings()
    templates = _get_templates(request)

    if not file_path or not file_path.strip():
        return _error_response(request, templates, "请提供 file_path 参数。", 400)
    if not audio_path or not audio_path.strip():
        return _error_response(request, templates, "请提供 audio_path 参数。", 400)
    if audio_index is None:
        return _error_response(request, templates, "请提供 audio_index 参数。", 400)

    container = (container or "mkv").lower()
    if container not in ("mkv", "mp4"):
        return _error_response(
            request, templates,
            f"不支持的容器格式: {container}（应为 mkv / mp4）", 400,
        )

    try:
        resolved_video = _resolve_allowed_path(
            file_path.strip(),
            [settings.paths.media_input, settings.paths.temp_dir],
        )
        resolved_audio = _resolve_allowed_path(
            audio_path.strip(),
            [settings.paths.media_input, settings.paths.temp_dir],
        )
    except ValueError as e:
        logger.warning("替换音频轨请求路径非法: %s", e)
        return _error_response(request, templates, str(e), 403)

    output_path = settings.paths.media_output / f"{resolved_video.stem}_replaced_audio_{audio_index}.{container}"

    logger.info(
        "替换音频轨请求: 视频=%s, 音频=%s, 索引=%d, 语言=%s, 容器=%s",
        resolved_video.name, resolved_audio.name, audio_index, language, container,
    )

    from processing.core.audio import AudioTrackError, replace_audio_track

    try:
        result = replace_audio_track(
            video_path=resolved_video,
            audio_path=resolved_audio,
            audio_index=audio_index,
            language=language.strip() or "und",
            output_path=output_path,
            container=container,
            ffmpeg_path=settings.ffmpeg.executable,
            ffprobe_path=settings.ffmpeg.ffprobe_executable,
            timeout=120,
            overwrite=True,
        )
    except AudioTrackError as e:
        logger.warning("替换音频轨失败: %s", e)
        return _error_response(request, templates, str(e), 422)
    except Exception as e:
        logger.warning("替换音频轨出错: %s", e, exc_info=True)
        return _error_response(request, templates, f"内部错误: {e}", 500)

    return _success_response(request, templates, result)


@router.post("/remove", response_model=None)
async def audio_remove_post(
    request: Request,
    file_path: str | None = Form(None),
    audio_index: int | None = Form(None),
    container: str | None = Form("mkv"),
):
    """POST /api/audio/remove — 移除指定音频轨。"""
    settings = _get_settings()
    templates = _get_templates(request)

    if not file_path or not file_path.strip():
        return _error_response(request, templates, "请提供 file_path 参数。", 400)
    if audio_index is None:
        return _error_response(request, templates, "请提供 audio_index 参数。", 400)

    container = (container or "mkv").lower()
    if container not in ("mkv", "mp4"):
        return _error_response(
            request, templates,
            f"不支持的容器格式: {container}（应为 mkv / mp4）", 400,
        )

    try:
        resolved_video = _resolve_allowed_path(
            file_path.strip(),
            [settings.paths.media_input, settings.paths.temp_dir],
        )
    except ValueError as e:
        logger.warning("移除音频轨请求路径非法: %s", e)
        return _error_response(request, templates, str(e), 403)

    output_path = settings.paths.media_output / f"{resolved_video.stem}_removed_audio_{audio_index}.{container}"

    logger.info(
        "移除音频轨请求: 视频=%s, 索引=%d, 容器=%s",
        resolved_video.name, audio_index, container,
    )

    from processing.core.audio import AudioTrackError, remove_audio_track

    try:
        result = remove_audio_track(
            video_path=resolved_video,
            audio_index=audio_index,
            output_path=output_path,
            container=container,
            ffmpeg_path=settings.ffmpeg.executable,
            ffprobe_path=settings.ffmpeg.ffprobe_executable,
            timeout=120,
            overwrite=True,
        )
    except AudioTrackError as e:
        logger.warning("移除音频轨失败: %s", e)
        return _error_response(request, templates, str(e), 422)
    except Exception as e:
        logger.warning("移除音频轨出错: %s", e, exc_info=True)
        return _error_response(request, templates, f"内部错误: {e}", 500)

    return _success_response(request, templates, result)


@router.post("/mute", response_model=None)
async def audio_mute_post(
    request: Request,
    file_path: str | None = Form(None),
    audio_index: int | None = Form(None),
    container: str | None = Form("mkv"),
):
    """POST /api/audio/mute — 静音音频轨（audio_index 为空则静音全部）。"""
    settings = _get_settings()
    templates = _get_templates(request)

    if not file_path or not file_path.strip():
        return _error_response(request, templates, "请提供 file_path 参数。", 400)

    container = (container or "mkv").lower()
    if container not in ("mkv", "mp4"):
        return _error_response(
            request, templates,
            f"不支持的容器格式: {container}（应为 mkv / mp4）", 400,
        )

    try:
        resolved_video = _resolve_allowed_path(
            file_path.strip(),
            [settings.paths.media_input, settings.paths.temp_dir],
        )
    except ValueError as e:
        logger.warning("静音音频轨请求路径非法: %s", e)
        return _error_response(request, templates, str(e), 403)

    suffix = f"_{audio_index}" if audio_index is not None else ""
    output_path = settings.paths.media_output / f"{resolved_video.stem}_muted_audio{suffix}.{container}"

    logger.info(
        "静音音频轨请求: 视频=%s, 索引=%s, 容器=%s",
        resolved_video.name, audio_index, container,
    )

    from processing.core.audio import AudioTrackError, mute_audio_track

    try:
        result = mute_audio_track(
            video_path=resolved_video,
            audio_index=audio_index,
            output_path=output_path,
            container=container,
            ffmpeg_path=settings.ffmpeg.executable,
            ffprobe_path=settings.ffmpeg.ffprobe_executable,
            timeout=120,
            overwrite=True,
        )
    except AudioTrackError as e:
        logger.warning("静音音频轨失败: %s", e)
        return _error_response(request, templates, str(e), 422)
    except Exception as e:
        logger.warning("静音音频轨出错: %s", e, exc_info=True)
        return _error_response(request, templates, f"内部错误: {e}", 500)

    return _success_response(request, templates, result)


@router.post("/sync", response_model=None)
async def audio_sync_post(
    request: Request,
    file_path: str | None = Form(None),
    offset_seconds: float | None = Form(None),
    audio_index: int | None = Form(None),
    container: str | None = Form("mkv"),
):
    """POST /api/audio/sync — 调整音频同步偏移。"""
    settings = _get_settings()
    templates = _get_templates(request)

    if not file_path or not file_path.strip():
        return _error_response(request, templates, "请提供 file_path 参数。", 400)
    if offset_seconds is None:
        return _error_response(request, templates, "请提供 offset_seconds 参数。", 400)

    container = (container or "mkv").lower()
    if container not in ("mkv", "mp4"):
        return _error_response(
            request, templates,
            f"不支持的容器格式: {container}（应为 mkv / mp4）", 400,
        )

    try:
        resolved_video = _resolve_allowed_path(
            file_path.strip(),
            [settings.paths.media_input, settings.paths.temp_dir],
        )
    except ValueError as e:
        logger.warning("调整同步请求路径非法: %s", e)
        return _error_response(request, templates, str(e), 403)

    suffix = f"_{audio_index}" if audio_index is not None else ""
    offset_str = f"{offset_seconds:+.1f}"
    output_path = settings.paths.media_output / f"{resolved_video.stem}_synced_audio{suffix}_{offset_str}.{container}"

    logger.info(
        "调整同步请求: 视频=%s, 偏移=%.1fs, 索引=%s, 容器=%s",
        resolved_video.name, offset_seconds, audio_index, container,
    )

    from processing.core.audio import AudioTrackError, adjust_audio_sync

    try:
        result = adjust_audio_sync(
            video_path=resolved_video,
            offset_seconds=offset_seconds,
            audio_index=audio_index,
            output_path=output_path,
            container=container,
            ffmpeg_path=settings.ffmpeg.executable,
            ffprobe_path=settings.ffmpeg.ffprobe_executable,
            timeout=120,
            overwrite=True,
        )
    except AudioTrackError as e:
        logger.warning("调整同步失败: %s", e)
        return _error_response(request, templates, str(e), 422)
    except Exception as e:
        logger.warning("调整同步出错: %s", e, exc_info=True)
        return _error_response(request, templates, f"内部错误: {e}", 500)

    return _success_response(request, templates, result)


@router.post("/speed", response_model=None)
async def audio_speed_post(
    request: Request,
    file_path: str | None = Form(None),
    speed_ratio: float | None = Form(None),
    audio_index: int | None = Form(None),
    container: str | None = Form("mkv"),
):
    """POST /api/audio/speed — 调整音频速度。"""
    settings = _get_settings()
    templates = _get_templates(request)

    if not file_path or not file_path.strip():
        return _error_response(request, templates, "请提供 file_path 参数。", 400)
    if speed_ratio is None:
        return _error_response(request, templates, "请提供 speed_ratio 参数。", 400)

    container = (container or "mkv").lower()
    if container not in ("mkv", "mp4"):
        return _error_response(
            request, templates,
            f"不支持的容器格式: {container}（应为 mkv / mp4）", 400,
        )

    try:
        resolved_video = _resolve_allowed_path(
            file_path.strip(),
            [settings.paths.media_input, settings.paths.temp_dir],
        )
    except ValueError as e:
        logger.warning("调整速度请求路径非法: %s", e)
        return _error_response(request, templates, str(e), 403)

    suffix = f"_{audio_index}" if audio_index is not None else ""
    ratio_str = str(speed_ratio)
    output_path = settings.paths.media_output / f"{resolved_video.stem}_speed_{ratio_str}_audio{suffix}.{container}"

    logger.info(
        "调整速度请求: 视频=%s, 速度=%s, 索引=%s, 容器=%s",
        resolved_video.name, ratio_str, audio_index, container,
    )

    from processing.core.audio import AudioTrackError, adjust_audio_speed

    try:
        result = adjust_audio_speed(
            video_path=resolved_video,
            speed_ratio=speed_ratio,
            audio_index=audio_index,
            output_path=output_path,
            container=container,
            ffmpeg_path=settings.ffmpeg.executable,
            ffprobe_path=settings.ffmpeg.ffprobe_executable,
            timeout=120,
            overwrite=True,
        )
    except AudioTrackError as e:
        logger.warning("调整速度失败: %s", e)
        return _error_response(request, templates, str(e), 422)
    except Exception as e:
        logger.warning("调整速度出错: %s", e, exc_info=True)
        return _error_response(request, templates, f"内部错误: {e}", 500)

    return _success_response(request, templates, result)


@router.get("/download", response_model=None)
async def audio_download(
    request: Request,
    path: str = Query(..., description="要下载的输出文件路径"),
):
    """GET /api/audio/download?path= — 下载音频处理后的输出文件。

    安全检查：仅允许下载 media_output 目录下的文件。
    """
    settings = _get_settings()

    target = Path(path)
    if not target.is_absolute():
        target = settings.paths.media_output / target

    if not _is_within_directory(target, settings.paths.media_output):
        logger.warning("拒绝下载请求（路径穿越）: %s", path)
        return JSONResponse(
            content={"success": False, "error": "禁止访问该路径。"},
            status_code=403,
        )

    if not target.exists() or not target.is_file():
        logger.warning("下载文件不存在: %s", target)
        return JSONResponse(
            content={"success": False, "error": "文件不存在。"},
            status_code=404,
        )

    logger.info("下载文件: %s", target.name)
    return FileResponse(
        path=str(target),
        filename=target.name,
        media_type="application/octet-stream",
    )


def _success_response(
    request: Request,
    templates,
    result,
) -> Response | JSONResponse:
    """根据请求类型返回成功 HTML 或 JSON。"""
    download_url = f"/api/audio/download?path={result.output_path.as_posix()}"

    resp_data = {
        "input_video": str(result.input_video),
        "output_path": str(result.output_path),
        "output_size": result.output_size,
        "operation": result.operation,
        "audio_index": result.audio_index,
        "download_url": download_url,
        **result.extra,
    }

    is_htmx = request.headers.get("hx-request", "").lower() == "true"
    if is_htmx:
        return templates.TemplateResponse(request, "audio_results.html", {
            "error": None,
            "result": resp_data,
        })

    return JSONResponse(content={"success": True, "result": resp_data})


def _error_response(
    request: Request,
    templates,
    message: str,
    status_code: int,
) -> Response | JSONResponse:
    """根据请求类型返回错误 HTML 或 JSON。"""
    is_htmx = request.headers.get("hx-request", "").lower() == "true"
    if is_htmx:
        return templates.TemplateResponse(
            request,
            "audio_results.html",
            {"error": message, "result": None},
            status_code=status_code,
        )
    return JSONResponse(
        content={"success": False, "error": message},
        status_code=status_code,
    )
