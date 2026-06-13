"""轨道默认设置 REST 端点。

POST /api/track/default  — 切换默认轨道（JSON / HTML）。
GET  /api/track/download — 下载输出文件。
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from web.api.utils import _is_within_directory, _resolve_allowed_path

logger = logging.getLogger("video_localizer.api.track")

router = APIRouter(prefix="/api/track")


def _get_templates(request: Request):
    """延迟导入避免循环引用。"""
    from app import templates
    return templates


def _get_settings():
    """延迟导入避免循环引用。"""
    from app import settings
    return settings


@router.post("/default", response_model=None)
async def track_default_post(
    request: Request,
    file_path: str | None = Form(None),
    stream_type: str | None = Form(None),
    stream_index: int | None = Form(None),
    container: str | None = Form("mkv"),
):
    """POST /api/track/default — 切换指定类型轨道的默认标记。

    参数:
      - file_path: 媒体文件路径（media_input 内）
      - stream_type: video | audio | subtitle
      - stream_index: 同类流中的序号（0-based）
      - container: 输出容器，默认 mkv
    """
    settings = _get_settings()
    templates = _get_templates(request)

    # 校验必填参数
    if not file_path or not file_path.strip():
        return _error_response(request, templates, "请提供 file_path 参数。", 400)

    if not stream_type or not stream_type.strip():
        return _error_response(request, templates, "请提供 stream_type 参数。", 400)

    if stream_index is None:
        return _error_response(request, templates, "请提供 stream_index 参数。", 400)

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
        logger.warning("切换默认轨道请求路径非法: %s", e)
        return _error_response(request, templates, str(e), 403)

    output_path = settings.paths.media_output / (
        f"{resolved_video.stem}_default_{stream_type.strip()}_{stream_index}.{container}"
    )

    logger.info(
        "切换默认轨道请求: 视频=%s, 类型=%s, 索引=%d, 容器=%s",
        resolved_video.name, stream_type, stream_index, container,
    )

    from processing.core.mux import MuxError, switch_default_track

    try:
        result = switch_default_track(
            video_path=resolved_video,
            stream_type=stream_type.strip(),
            stream_index=stream_index,
            output_path=output_path,
            container=container,
            ffmpeg_path=settings.ffmpeg.executable,
            ffprobe_path=settings.ffmpeg.ffprobe_executable,
            timeout=120,
            overwrite=True,
        )
    except MuxError as e:
        logger.warning("切换默认轨道失败: %s", e)
        return _error_response(request, templates, str(e), 422)
    except Exception as e:
        logger.warning("切换默认轨道出错: %s", e, exc_info=True)
        return _error_response(request, templates, f"内部错误: {e}", 500)

    download_url = f"/api/track/download?path={result.output_path.as_posix()}"

    resp_data = {
        "input_video": str(result.input_video),
        "output_path": str(result.output_path),
        "output_size": result.output_size,
        "stream_type": result.stream_type,
        "stream_index": result.stream_index,
        "changed_tracks": result.changed_tracks,
        "download_url": download_url,
    }

    is_htmx = request.headers.get("hx-request", "").lower() == "true"
    if is_htmx:
        return templates.TemplateResponse(request, "track_results.html", {
            "error": None,
            "result": resp_data,
        })

    return JSONResponse(content={"success": True, "result": resp_data})


@router.get("/download", response_model=None)
async def track_download(
    request: Request,
    path: str = Query(..., description="要下载的输出文件路径"),
):
    """GET /api/track/download?path= — 下载切换默认轨道后的输出文件。

    安全检查：仅允许下载 media_output 目录下的文件。
    """
    settings = _get_settings()

    target = Path(path)
    if not target.is_absolute():
        target = settings.paths.media_output / target

    # 路径穿越防护
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
            "track_results.html",
            {"error": message, "result": None},
            status_code=status_code,
        )
    return JSONResponse(
        content={"success": False, "error": message},
        status_code=status_code,
    )
