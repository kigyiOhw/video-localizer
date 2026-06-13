"""流提取 REST 端点。

POST /api/extract — 提取单个流或批量提取，支持 HTMX 和 JSON 两种响应。
GET  /api/extract/download — 下载提取的文件。
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from web.api.utils import _is_within_directory, _resolve_allowed_path

logger = logging.getLogger("video_localizer.api.extract")

router = APIRouter(prefix="/api/extract")


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


def _parse_stream_spec(raw: str) -> dict[str, str | int | None]:
    """解析流选择器字符串。

    格式: "type:index" → {"type": "audio", "index": 0}
          "type:index:ext" → {"type": "subtitle", "index": 1, "ext": "srt"}
    """
    parts = raw.strip().split(":")
    result: dict[str, str | int | None] = {"type": "audio", "index": 0, "ext": None}
    if len(parts) >= 2:
        result["type"] = parts[0]
        result["index"] = int(parts[1])
    if len(parts) >= 3:
        result["ext"] = parts[2]
    return result


@router.post("/", response_model=None)
async def extract_post(
    request: Request,
    file_path: str | None = Form(None),
    stream_index: int | None = Form(None),
    stream_type: str | None = Form(None),
    output_ext: str | None = Form(None),
    streams: list[str] | None = Form(None),
):
    """POST /api/extract — 提取媒体流。

    单流提取:
      - file_path: 媒体文件路径
      - stream_index: 同类流中的序号
      - stream_type: "video" | "audio" | "subtitle"
      - output_ext: 可选，输出文件扩展名

    批量提取:
      - file_path: 媒体文件路径
      - streams: ["audio:0", "subtitle:0:srt", ...]

    HX-Request 头为 true 时返回 HTML fragment。
    """
    from processing.core.extract import (
        ExtractError,
        ExtractResult,
        extract_multiple,
        extract_stream,
        _format_size,
    )
    from processing.core.probe import (
        _format_duration,
        probe_file,
        ProbeError,
    )

    settings = _get_settings()
    templates = _get_templates(request)

    if not file_path or not file_path.strip():
        return _extract_error(request, "请提供 file_path 参数。", 400)

    try:
        path = _resolve_path(file_path.strip())
    except ValueError as e:
        logger.warning("提取请求路径非法: %s", e)
        return _extract_error(request, str(e), 403)

    # 批量提取
    if streams and len(streams) > 0:
        specs = [_parse_stream_spec(s) for s in streams]
        logger.info("批量提取请求: %s, %d 个流", path, len(specs))
        try:
            results = extract_multiple(
                input_path=path,
                output_dir=settings.paths.media_output,
                streams=specs,
                ffmpeg_path=settings.ffmpeg.executable,
                ffprobe_path=settings.ffmpeg.ffprobe_executable,
            )
        except ExtractError as e:
            logger.warning("批量提取失败: %s", e)
            return _extract_error(request, str(e), 422)

        return _extract_success(request, results, path, templates)

    # 单流提取
    if stream_index is not None and stream_type:
        logger.info("提取请求: %s → %s #%d", path, stream_type, stream_index)
        try:
            result = extract_stream(
                input_path=path,
                output_dir=settings.paths.media_output,
                stream_index=stream_index,
                stream_type=stream_type,
                output_ext=output_ext,
                ffmpeg_path=settings.ffmpeg.executable,
                ffprobe_path=settings.ffmpeg.ffprobe_executable,
            )
        except ExtractError as e:
            logger.warning("提取失败: %s", e)
            error_msg = str(e)
            # 判断错误类型
            if "不存在" in error_msg:
                return _extract_error(request, error_msg, 404)
            if "已存在" in error_msg:
                return _extract_error(request, error_msg, 409)
            if "未找到" in error_msg and "ffmpeg" in error_msg.lower():
                return _extract_error(request, error_msg, 422)
            return _extract_error(request, error_msg, 422)

        return _extract_success(request, [result], path, templates)

    # 缺少必填参数
    logger.warning("POST /api/extract 缺少流选择参数")
    return _extract_error(
        request,
        "请提供 stream_index + stream_type，或 streams 数组。",
        400,
    )


def _extract_error(
    request: Request,
    message: str,
    status_code: int,
) -> JSONResponse | HTMLResponse:
    """统一的错误响应。"""
    is_htmx = request.headers.get("hx-request", "").lower() == "true"
    if is_htmx:
        templates = _get_templates(request)
        return templates.TemplateResponse(request, "extract_results.html", {
            "error": message,
            "results": None,
            "input_path": None,
        })
    return JSONResponse(
        content={"success": False, "error": message},
        status_code=status_code,
    )


def _extract_success(
    request: Request,
    results: list,
    input_path: Path,
    templates,
) -> JSONResponse | HTMLResponse:
    """统一的成功响应。"""
    from processing.core.extract import _format_size

    is_htmx = request.headers.get("hx-request", "").lower() == "true"
    if is_htmx:
        return templates.TemplateResponse(request, "extract_results.html", {
            "error": None,
            "results": results,
            "input_path": str(input_path),
            "format_size": _format_size,
        })

    return JSONResponse(content={
        "success": True,
        "count": len(results),
        "results": [
            {
                "stream_index": r.stream_index,
                "stream_type": r.stream_type,
                "codec": r.codec,
                "output_path": str(r.output_path),
                "output_size": r.output_size,
                "duration": r.duration,
                "download_url": f"/api/extract/download?path={r.output_path}",
            }
            for r in results
        ],
    })


@router.get("/download", response_model=None)
async def extract_download(
    path: str = Query(..., description="要下载的文件路径"),
):
    """GET /api/extract/download?path= — 下载提取的文件。

    包含路径穿越防护：只允许下载 media_output 目录下的文件。
    """
    settings = _get_settings()
    target = Path(path)

    # 检查路径是否在允许的输出目录内
    media_output = settings.paths.media_output.resolve()
    if not _is_within_directory(target, media_output):
        logger.warning("拒绝下载请求（路径穿越）: %s", path)
        return JSONResponse(
            content={"success": False, "error": "不允许访问该路径。"},
            status_code=403,
        )

    if not target.exists() or not target.is_file():
        logger.warning("下载文件不存在: %s", target)
        return JSONResponse(
            content={"success": False, "error": f"文件不存在: {path}"},
            status_code=404,
        )

    logger.info("下载文件: %s (%s)", target.name, _format_size_dl(target.stat().st_size))
    download_name = target.name
    return FileResponse(
        path=str(target),
        filename=download_name,
        media_type="application/octet-stream",
    )


def _format_size_dl(size_bytes: int) -> str:
    """用于日志的文件大小格式化。"""
    if size_bytes >= 1048576:
        return f"{size_bytes / 1048576:.1f} MB"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.0f} KB"
    return f"{size_bytes} B"
