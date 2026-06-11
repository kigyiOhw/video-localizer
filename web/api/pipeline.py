"""端到端流水线 REST 端点。

POST /api/pipeline/run        — 同步全流程（JSON / HTML）。
POST /api/pipeline/run/stream — SSE 流式全流程（实时进度推送）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

logger = logging.getLogger("video_localizer.api.pipeline")

router = APIRouter(prefix="/api/pipeline")


# ---------------------------------------------------------------------------
# 引擎获取（复用已有单例）
# ---------------------------------------------------------------------------


def _get_asr_engine():
    """获取 ASR 引擎单例。"""
    from web.api.asr import _get_engine
    return _get_engine()


def _get_translate_engine():
    """获取翻译引擎单例。"""
    from web.api.translate import _get_engine
    return _get_engine()


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _get_templates(request: Request):
    from app import templates
    return templates


def _get_settings():
    from app import settings
    return settings


def _resolve_path(file_path: str) -> Path:
    p = Path(file_path)
    if p.is_absolute():
        return p
    return _get_settings().paths.media_input / p


def _sse(event: str, data: dict) -> str:
    """构建一条 SSE 消息。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# ---------------------------------------------------------------------------
# 端点
# ---------------------------------------------------------------------------


@router.post("/run", response_model=None)
async def pipeline_run(
    request: Request,
    video_path: str | None = Form(None),
    target_language: str | None = Form(None),
    source_language: str | None = Form(None),
):
    """POST /api/pipeline/run — 同步全流程：视频 → ASR → 翻译 → 封装。

    返回 JSON（默认）或 HTML 片段（HX-Request 头）。
    """
    templates = _get_templates(request)
    settings = _get_settings()

    if not video_path or not video_path.strip():
        return _pipeline_error(request, templates, "请提供视频文件路径。", 400)

    target = target_language or settings.translate.target_language
    source = source_language or settings.translate.source_language or ""

    path = _resolve_path(video_path.strip())

    logger.info(
        "流水线请求: %s → %s (源语言=%s)",
        path.name, target, source or "auto",
    )

    try:
        asr_engine = _get_asr_engine()
        translate_engine = _get_translate_engine()
    except ValueError as e:
        return _pipeline_error(request, templates, str(e), 422)

    from processing.pipeline.full_pipeline import PipelineError, run_full_pipeline

    try:
        result = await asyncio.to_thread(
            run_full_pipeline,
            video_path=path,
            target_language=target,
            asr_engine=asr_engine,
            translate_engine=translate_engine,
            source_language=source,
            ffmpeg_path=settings.ffmpeg.executable,
            ffprobe_path=settings.ffmpeg.ffprobe_executable,
            overwrite=True,
        )
    except PipelineError as e:
        logger.warning("流水线失败: %s", e)
        return _pipeline_error(request, templates, str(e), 422)
    except Exception as e:
        logger.warning("流水线出错: %s", e, exc_info=True)
        return _pipeline_error(request, templates, f"内部错误: {e}", 500)

    # 响应
    resp_data = {
        "output_path": str(result.output_path),
        "output_size": result.output_size,
        "source_language": result.source_language,
        "target_language": result.target_language,
        "asr_elapsed": result.asr_elapsed,
        "translate_elapsed": result.translate_elapsed,
        "total_elapsed": result.total_elapsed,
        "total_segments": len(result.translated_segments),
        "srt_original": result.srt_original,
        "srt_translated": result.srt_translated,
        "segments": result.translated_segments,
        "download_url": f"/api/subtitle/download?path={result.output_path.as_posix()}",
    }

    is_htmx = request.headers.get("hx-request", "").lower() == "true"
    if is_htmx:
        return templates.TemplateResponse(request, "pipeline_results.html", {
            "error": None,
            "result": resp_data,
        })

    return JSONResponse(content={"success": True, **resp_data})


@router.post("/run/stream", response_model=None)
async def pipeline_run_stream(
    request: Request,
    video_path: str | None = Form(None),
    target_language: str | None = Form(None),
    source_language: str | None = Form(None),
):
    """POST /api/pipeline/run/stream — SSE 流式全流程。

    事件类型:
      status     — 阶段状态（probe / extract / asr / translate / mux）
      segment    — ASR 转写片段
      translated — 翻译批次结果
      progress   — 进度心跳
      done       — 完成（含结果和下载链接）
      error      — 错误
    """
    if not video_path or not video_path.strip():
        return StreamingResponse(
            _single_error_sse("请提供视频文件路径。"),
            media_type="text/event-stream",
        )

    settings = _get_settings()
    target = target_language or settings.translate.target_language
    source = source_language or settings.translate.source_language or ""
    path = _resolve_path(video_path.strip())

    return StreamingResponse(
        _sse_generator(request, path, target, source),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# SSE 生成器
# ---------------------------------------------------------------------------


async def _sse_generator(
    request: Request,
    video_path: Path,
    target_language: str,
    source_language: str,
):
    """SSE 事件异步生成器。

    用 asyncio.Queue 桥接同步管线生成器和异步 SSE 流。
    """
    started = time.monotonic()
    queue: asyncio.Queue = asyncio.Queue()

    # 获取引擎
    try:
        asr_engine = _get_asr_engine()
        translate_engine = _get_translate_engine()
    except ValueError as e:
        yield _sse("error", {"message": str(e)})
        return

    # 尝试获取底层 WhisperModel 用于逐片段推送
    asr_model = getattr(asr_engine, '_model', None)

    settings = _get_settings()

    from processing.pipeline.full_pipeline import run_full_pipeline_stream

    def _run_pipeline():
        """后台线程：跑同步管线生成器，事件放入队列。"""
        try:
            for evt in run_full_pipeline_stream(
                video_path=video_path,
                target_language=target_language,
                asr_engine=asr_engine,
                translate_engine=translate_engine,
                asr_model=asr_model,
                source_language=source_language,
                ffmpeg_path=settings.ffmpeg.executable,
                ffprobe_path=settings.ffmpeg.ffprobe_executable,
                overwrite=True,
            ):
                queue.put_nowait(evt)
        except Exception as e:
            logger.warning("后台管线线程异常: %s", e, exc_info=True)
            queue.put_nowait({"event": "error", "data": {"message": str(e)}})

    loop = asyncio.get_event_loop()
    task = loop.run_in_executor(None, _run_pipeline)

    # 事件路由
    current_phase = "probe"

    while True:
        try:
            evt = await asyncio.wait_for(queue.get(), timeout=0.3)
        except asyncio.TimeoutError:
            elapsed = time.monotonic() - started
            yield _sse("progress", {
                "phase": current_phase,
                "elapsed": round(elapsed, 1),
            })
            continue

        event_type = evt["event"]
        data = evt["data"]

        if event_type == "status":
            if "phase" in data:
                current_phase = data["phase"]
            yield _sse("status", data)

        elif event_type == "segment":
            elapsed = time.monotonic() - started
            yield _sse("segment", {**data, "elapsed": round(elapsed, 1)})

        elif event_type == "translated":
            yield _sse("translated", data)

        elif event_type == "done":
            elapsed = round(time.monotonic() - started, 1)
            yield _sse("done", {**data, "total_elapsed": elapsed})
            return

        elif event_type == "error":
            yield _sse("error", data)
            return


# ---------------------------------------------------------------------------
# 错误响应
# ---------------------------------------------------------------------------


def _pipeline_error(
    request: Request,
    templates,
    message: str,
    status_code: int,
) -> JSONResponse | HTMLResponse:
    """统一的错误响应（支持 HTMX）。"""
    is_htmx = request.headers.get("hx-request", "").lower() == "true"
    if is_htmx and templates:
        return templates.TemplateResponse(request, "pipeline_results.html", {
            "error": message,
            "result": None,
        })
    return JSONResponse(
        content={"success": False, "error": message},
        status_code=status_code,
    )


async def _single_error_sse(message: str):
    """返回单条 SSE 错误事件的生成器。"""
    yield _sse("error", {"message": message})
