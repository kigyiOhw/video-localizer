"""字幕添加 REST 端点。

POST /api/subtitle/add      — 添加软字幕（JSON / HTML）。
GET  /api/subtitle/download — 下载输出文件。
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from fastapi import APIRouter, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response

from web.api.utils import _is_within_directory

logger = logging.getLogger("video_localizer.api.subtitle")

router = APIRouter(prefix="/api/subtitle")


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


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
    from web.api.utils import _resolve_allowed_path
    return _resolve_allowed_path(
        file_path,
        [settings.paths.media_input, settings.paths.temp_dir],
    )


# ---------------------------------------------------------------------------
# 端点
# ---------------------------------------------------------------------------


@router.post("/add", response_model=None)
async def subtitle_add_post(
    request: Request,
    video_path: str | None = Form(None),
    video_file: UploadFile | None = None,
    subtitle_path: str | None = Form(None),
    subtitle_file: UploadFile | None = None,
    language: str | None = Form(None),
    container: str | None = Form("mkv"),
):
    """POST /api/subtitle/add — 给视频添加外部字幕文件作为软字幕轨道。

    支持 4 种输入组合:
      - video_path + subtitle_path: 双方均为服务器路径
      - video_path + subtitle_file: 视频在服务器 + 上传字幕
      - video_file + subtitle_path: 上传视频 + 字幕在服务器
      - video_file + subtitle_file: 双方均上传
    """
    settings = _get_settings()
    templates = _get_templates(request)

    # 校验必填参数
    if not language or not language.strip():
        return _error_response(request, templates, "请选择或输入语言代码（如 eng, jpn, zho）。", 400)

    if not video_path and not video_file:
        return _error_response(request, templates, "请提供视频路径或上传视频文件。", 400)

    if not subtitle_path and not subtitle_file:
        return _error_response(request, templates, "请提供字幕路径或上传字幕文件。", 400)

    container = (container or "mkv").lower()
    if container not in ("mkv", "mp4"):
        return _error_response(request, templates, f"不支持的容器格式: {container}（应为 mkv / mp4）", 400)

    # 临时文件列表（用于清理）
    temp_files: list[Path] = []

    try:
        # 解析视频路径
        if video_file:
            temp_dir = settings.paths.temp_dir
            temp_dir.mkdir(parents=True, exist_ok=True)
            safe_name = Path(video_file.filename or "uploaded_video").name
            resolved_video = temp_dir / f"upload_{safe_name}"
            with open(resolved_video, "wb") as f:
                shutil.copyfileobj(video_file.file, f)
            temp_files.append(resolved_video)
            logger.info("视频已上传: %s → %s", video_file.filename, resolved_video)
        else:
            resolved_video = _resolve_path(video_path.strip())  # type: ignore[arg-type]

        # 解析字幕路径
        if subtitle_file:
            temp_dir = settings.paths.temp_dir
            temp_dir.mkdir(parents=True, exist_ok=True)
            safe_name = Path(subtitle_file.filename or "uploaded_subtitle").name
            resolved_subtitle = temp_dir / f"upload_{safe_name}"
            with open(resolved_subtitle, "wb") as f:
                shutil.copyfileobj(subtitle_file.file, f)
            temp_files.append(resolved_subtitle)
            logger.info("字幕已上传: %s → %s", subtitle_file.filename, resolved_subtitle)
        else:
            resolved_subtitle = _resolve_path(subtitle_path.strip())  # type: ignore[arg-type]

        # 生成输出路径
        output_path = settings.paths.media_output / f"{resolved_video.stem}_subtitled.{container}"

        logger.info(
            "添加字幕请求: 视频=%s, 字幕=%s, 语言=%s, 容器=%s",
            resolved_video.name, resolved_subtitle.name, language, container,
        )

        from processing.core.mux import MuxError, add_subtitle

        result = add_subtitle(
            video_path=resolved_video,
            subtitle_path=resolved_subtitle,
            language=language.strip(),
            output_path=output_path,
            container=container,
            ffmpeg_path=settings.ffmpeg.executable,
            timeout=120,
            overwrite=True,
        )

    except MuxError as e:
        logger.warning("添加字幕失败: %s", e)
        return _error_response(request, templates, str(e), 422)
    except ValueError as e:
        logger.warning("添加字幕路径非法: %s", e)
        return _error_response(request, templates, str(e), 403)
    except Exception as e:
        logger.warning("添加字幕出错: %s", e, exc_info=True)
        return _error_response(request, templates, f"内部错误: {e}", 500)
    finally:
        # 清理上传的临时文件
        for f in temp_files:
            try:
                f.unlink(missing_ok=True)
            except OSError:
                logger.debug("清理临时文件失败: %s", f, exc_info=True)

    # 构建下载 URL
    download_url = f"/api/subtitle/download?path={result.output_path.as_posix()}"

    resp_data = {
        "input_video": str(result.input_video),
        "output_path": str(result.output_path),
        "output_size": result.output_size,
        "subtitle_count": result.subtitle_count,
        "added_track_index": result.added_track_index,
        "language": result.language,
        "download_url": download_url,
    }

    is_htmx = request.headers.get("hx-request", "").lower() == "true"
    if is_htmx:
        return templates.TemplateResponse(request, "subtitle_results.html", {
            "error": None,
            "result": resp_data,
        })

    return JSONResponse(content={"success": True, "result": resp_data})


@router.get("/add", response_model=None)
async def subtitle_add_get(
    request: Request,
    video_path: str | None = Query(None),
    subtitle_path: str | None = Query(None),
    language: str | None = Query(None),
    container: str | None = Query("mkv"),
):
    """GET /api/subtitle/add — JSON-only 便捷接口。"""
    settings = _get_settings()
    templates = _get_templates(request)

    if not video_path or not video_path.strip():
        return JSONResponse(
            content={"success": False, "error": "请提供 ?video_path= 参数。"},
            status_code=400,
        )
    if not subtitle_path or not subtitle_path.strip():
        return JSONResponse(
            content={"success": False, "error": "请提供 ?subtitle_path= 参数。"},
            status_code=400,
        )
    if not language or not language.strip():
        return JSONResponse(
            content={"success": False, "error": "请提供 ?language= 参数。"},
            status_code=400,
        )

    container = (container or "mkv").lower()
    if container not in ("mkv", "mp4"):
        return JSONResponse(
            content={"success": False, "error": f"不支持的容器格式: {container}"},
            status_code=400,
        )

    try:
        resolved_video = _resolve_path(video_path.strip())
        resolved_subtitle = _resolve_path(subtitle_path.strip())
    except ValueError as e:
        logger.warning("添加字幕路径非法: %s", e)
        return JSONResponse(
            content={"success": False, "error": str(e)},
            status_code=403,
        )

    output_path = settings.paths.media_output / f"{resolved_video.stem}_subtitled.{container}"

    try:
        from processing.core.mux import MuxError, add_subtitle

        result = add_subtitle(
            video_path=resolved_video,
            subtitle_path=resolved_subtitle,
            language=language.strip(),
            output_path=output_path,
            container=container,
            ffmpeg_path=settings.ffmpeg.executable,
            timeout=120,
            overwrite=True,
        )
    except MuxError as e:
        return JSONResponse(
            content={"success": False, "error": str(e)},
            status_code=422,
        )
    except Exception as e:
        return JSONResponse(
            content={"success": False, "error": f"内部错误: {e}"},
            status_code=500,
        )

    download_url = f"/api/subtitle/download?path={result.output_path.as_posix()}"
    return JSONResponse(content={
        "success": True,
        "result": {
            "input_video": str(result.input_video),
            "output_path": str(result.output_path),
            "output_size": result.output_size,
            "subtitle_count": result.subtitle_count,
            "added_track_index": result.added_track_index,
            "language": result.language,
            "download_url": download_url,
        },
    })


@router.get("/download", response_model=None)
async def subtitle_download(
    request: Request,
    path: str = Query(..., description="要下载的输出文件路径"),
):
    """GET /api/subtitle/download?path= — 下载封装后的输出文件。

    安全检查：仅允许下载 media_output 目录下的文件。
    """
    settings = _get_settings()
    templates = _get_templates(request)

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


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------


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
            "subtitle_results.html",
            {"error": message, "result": None},
            status_code=status_code,
        )
    return JSONResponse(
        content={"success": False, "error": message},
        status_code=status_code,
    )
