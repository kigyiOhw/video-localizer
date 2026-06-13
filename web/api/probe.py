"""流探测 REST 端点。

POST /api/probe — 文件上传或路径参数，支持 HTMX 和 JSON 两种响应。
GET  /api/probe — 通过 query 参数探测。
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

from web.api.utils import _resolve_allowed_path

logger = logging.getLogger("video_localizer.api.probe")
router = APIRouter(prefix="/api/probe")


def _get_templates(request: Request):
    """延迟导入避免循环引用。"""
    from app import templates
    return templates


def _get_settings():
    """延迟导入避免循环引用。"""
    from app import settings
    return settings


def _resolve_path(file_path: str) -> Path:
    """将输入的路径字符串解析为 Path 对象并校验允许范围。

    允许访问 media_input 与 temp_dir 目录。
    """
    settings = _get_settings()
    return _resolve_allowed_path(
        file_path,
        [settings.paths.media_input, settings.paths.temp_dir],
    )


async def _handle_probe(file_path: str | None, request: Request) -> JSONResponse | HTMLResponse:
    """统一的探测处理逻辑。

    Args:
        file_path: 要探测的文件路径。
        request: FastAPI Request 对象（用于检测 HTMX 头）。

    Returns:
        JSON（普通请求）或 HTML fragment（HTMX 请求）。
    """
    from processing.core.probe import (
        ProbeError,
        ProbeResult,
        _format_duration,
        _format_size,
        _safe_filename,
        probe_file,
    )

    settings = _get_settings()
    templates = _get_templates(request)

    try:
        path = _resolve_path(file_path)  # type: ignore[arg-type]
    except ValueError as e:
        logger.warning("探测请求路径非法: %s", e)
        is_htmx = request.headers.get("hx-request", "").lower() == "true"
        if is_htmx:
            return templates.TemplateResponse(request, "probe_results.html", {
                "error": str(e),
                "result": None,
                "format_duration": _format_duration,
                "format_size": _format_size,
                "safe_filename": _safe_filename,
            })
        return JSONResponse(
            content={"success": False, "error": str(e)},
            status_code=403,
        )

    logger.info("探测请求: %s", path)

    try:
        result: ProbeResult = probe_file(
            path,
            ffprobe_path=settings.ffmpeg.ffprobe_executable,
        )
    except ProbeError as e:
        logger.warning("探测失败: %s", e)
        is_htmx = request.headers.get("hx-request", "").lower() == "true"
        if is_htmx:
            return templates.TemplateResponse(request, "probe_results.html", {
                "error": str(e),
                "result": None,
                "format_duration": _format_duration,
                "format_size": _format_size,
                "safe_filename": _safe_filename,
            })
        return JSONResponse(
            content={"success": False, "error": str(e)},
            status_code=422,
        )

    # 序列化结果
    data = {
        "success": True,
        "format": {
            "filename": result.format.filename,
            "format_name": result.format.format_name,
            "format_long": result.format.format_long,
            "size_bytes": result.format.size_bytes,
            "duration": result.format.duration,
            "bitrate": result.format.bitrate,
        },
        "video_streams": [
            {
                "index": v.index, "codec": v.codec, "codec_long": v.codec_long,
                "language": v.language, "width": v.width, "height": v.height,
                "pix_fmt": v.pix_fmt, "bitrate": v.bitrate, "fps": v.fps,
                "fps_float": v.fps_float, "duration": v.duration, "bit_depth": v.bit_depth,
                "disposition": v.disposition, "tags": v.tags,
            }
            for v in result.video_streams
        ],
        "audio_streams": [
            {
                "index": a.index, "codec": a.codec, "codec_long": a.codec_long,
                "language": a.language, "sample_rate": a.sample_rate,
                "channels": a.channels, "channel_layout": a.channel_layout,
                "bitrate": a.bitrate, "duration": a.duration,
                "disposition": a.disposition, "tags": a.tags,
            }
            for a in result.audio_streams
        ],
        "subtitle_streams": [
            {
                "index": s.index, "codec": s.codec, "codec_long": s.codec_long,
                "language": s.language, "duration": s.duration,
                "disposition": s.disposition, "tags": s.tags,
            }
            for s in result.subtitle_streams
        ],
    }

    is_htmx = request.headers.get("hx-request", "").lower() == "true"
    if is_htmx:
        logger.debug("返回 HTML fragment")
        return templates.TemplateResponse(request, "probe_results.html", {
            "error": None,
            "result": result,
            "format_duration": _format_duration,
            "format_size": _format_size,
            "safe_filename": _safe_filename,
        })

    logger.debug("返回 JSON")
    return JSONResponse(content=data)


@router.post("/", response_model=None)
async def probe_post(
    request: Request,
    file: UploadFile | None = None,
    file_path: str | None = Form(None),
):
    """POST /api/probe — 上传文件或指定路径进行探测。

    - 如果提供 file（UploadFile），先保存到临时目录再探测
    - 如果提供 file_path（Form），直接探测该路径
    - HX-Request 头为 true 时返回 HTML fragment
    """
    if file and file.filename:
        settings = _get_settings()
        temp_dir = settings.paths.temp_dir
        temp_dir.mkdir(parents=True, exist_ok=True)
        safe_name = Path(file.filename).name
        dest = temp_dir / safe_name
        logger.info("保存上传文件: %s → %s", file.filename, dest)
        content = await file.read()
        dest.write_bytes(content)
        return await _handle_probe(str(dest), request)

    if file_path and file_path.strip():
        return await _handle_probe(file_path.strip(), request)

    # 缺少输入
    logger.warning("POST /api/probe 缺少输入")
    is_htmx = request.headers.get("hx-request", "").lower() == "true"
    if is_htmx:
        templates = _get_templates(request)
        return templates.TemplateResponse(request, "probe_results.html", {
            "error": "请提供文件或指定文件路径。",
            "result": None,
            "format_duration": None,
            "format_size": None,
            "safe_filename": None,
        })
    return JSONResponse(
        content={"success": False, "error": "请提供 file 或 file_path。"},
        status_code=400,
    )


@router.get("/", response_model=None)
async def probe_get(
    request: Request,
    file_path: str | None = None,
):
    """GET /api/probe?file_path= — 通过查询参数探测文件。

    始终返回 JSON（GET 请求不检测 HTMX 头）。
    """
    if not file_path or not file_path.strip():
        return JSONResponse(
            content={"success": False, "error": "请提供 ?file_path= 参数。"},
            status_code=400,
        )

    # GET 请求始终返回 JSON（不检测 HTMX）
    from processing.core.probe import ProbeError, probe_file

    settings = _get_settings()
    try:
        path = _resolve_path(file_path.strip())
    except ValueError as e:
        logger.warning("GET 探测请求路径非法: %s", e)
        return JSONResponse(
            content={"success": False, "error": str(e)},
            status_code=403,
        )
    logger.info("GET 探测请求: %s", path)

    try:
        result = probe_file(path, ffprobe_path=settings.ffmpeg.ffprobe_executable)
    except ProbeError as e:
        logger.warning("探测失败: %s", e)
        return JSONResponse(
            content={"success": False, "error": str(e)},
            status_code=422,
        )

    return JSONResponse(content={
        "success": True,
        "format": {
            "filename": result.format.filename,
            "format_name": result.format.format_name,
            "format_long": result.format.format_long,
            "size_bytes": result.format.size_bytes,
            "duration": result.format.duration,
            "bitrate": result.format.bitrate,
        },
        "video_streams": len(result.video_streams),
        "audio_streams": len(result.audio_streams),
        "subtitle_streams": len(result.subtitle_streams),
        "total_streams": (
            len(result.video_streams)
            + len(result.audio_streams)
            + len(result.subtitle_streams)
        ),
    })
