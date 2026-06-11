"""翻译 REST 端点。

POST /api/translate        — 同步翻译（JSON / HTML）。
POST /api/translate/stream — SSE 流式翻译（逐批推送）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

logger = logging.getLogger("video_localizer.api.translate")

router = APIRouter(prefix="/api/translate")

# ---------------------------------------------------------------------------
# 引擎单例
# ---------------------------------------------------------------------------

_engine = None


def _get_engine():
    """延迟创建翻译引擎单例（首次请求时实例化）。

    根据 settings.translate.engine 分发到具体实现：
    - "llm"       → LLMTranslateEngine（远程 API）
    - "llm_local" → LLMLocalTranslateEngine（本地 Ollama）
    - "none"      → 抛出 ValueError
    """
    global _engine
    if _engine is None:
        from engines.translate.llm import LLMTranslateEngine
        from engines.translate.llm_local import LLMLocalTranslateEngine

        from app import settings
        cfg = settings.translate

        if cfg.engine == "llm":
            api_key = os.getenv("OPENAI_API_KEY", "")
            if not api_key:
                logger.warning(
                    "翻译引擎设为 'llm' 但未找到 OPENAI_API_KEY 环境变量。"
                    "API 调用可能会返回 401。"
                )
            _engine = LLMTranslateEngine(
                api_base=cfg.api_base,
                api_key=api_key,
                model=cfg.model,
                temperature=cfg.temperature,
                batch_size=cfg.batch_size,
            )
        elif cfg.engine == "llm_local":
            _engine = LLMLocalTranslateEngine(
                api_base=cfg.ollama_base,
                model=cfg.ollama_model,
                temperature=cfg.temperature,
                batch_size=cfg.batch_size,
            )
        else:
            raise ValueError(
                f"翻译引擎未配置或已禁用 (engine={cfg.engine})。"
                "请在 config/settings.local.yaml 中将 translate.engine 设为 'llm' 或 'llm_local'，"
                "或将硬件配置档升级到 gpu_medium 以上。"
            )

        logger.info(
            "初始化翻译引擎: engine=%s, model=%s, batch_size=%d",
            cfg.engine,
            cfg.model if cfg.engine == "llm" else cfg.ollama_model,
            cfg.batch_size,
        )
    return _engine


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


@router.post("", response_model=None)
async def translate_post(
    request: Request,
    srt_text: str | None = Form(None),
    srt_path: str | None = Form(None),
    target_language: str | None = Form(None),
    source_language: str | None = Form(None),
):
    """POST /api/translate — 同步翻译 SRT 字幕。

    输入方式（二选一）：
    - srt_text: 直接粘贴 SRT 文本
    - srt_path: SRT 文件路径

    返回 JSON（默认）或 HTML 片段（HX-Request 头）。
    """
    templates = _get_templates(request)
    settings = _get_settings()

    # 校验输入
    if not srt_text and not srt_path:
        return _translate_error(request, templates, "请提供 SRT 文本或文件路径。", 400)

    target = target_language or settings.translate.target_language
    source = source_language or settings.translate.source_language or ""

    try:
        result = await _run_translate(request, srt_text, srt_path, target, source)
    except ValueError as e:
        logger.warning("翻译失败: %s", e)
        return _translate_error(request, templates, str(e), 422)
    except Exception as e:
        logger.warning("翻译出错: %s", e, exc_info=True)
        return _translate_error(request, templates, f"内部错误: {e}", 500)

    is_htmx = request.headers.get("hx-request", "").lower() == "true"
    if is_htmx:
        return templates.TemplateResponse(request, "translate_results.html", {
            "error": None,
            "result": result,
        })

    return JSONResponse(content={"success": True, **result})


@router.post("/stream", response_model=None)
async def translate_stream(
    request: Request,
    srt_text: str | None = Form(None),
    srt_path: str | None = Form(None),
    target_language: str | None = Form(None),
    source_language: str | None = Form(None),
):
    """POST /api/translate/stream — SSE 流式翻译。

    事件类型:
      status     — 阶段状态（prepare / translate）
      translated — 单个翻译片段（含原文 + 译文）
      progress   — 进度心跳
      done       — 完成（含 SRT 和统计）
      error      — 错误
    """
    if not srt_text and not srt_path:
        return _translate_error(request, None, "请提供 SRT 文本或文件路径。", 400)

    settings = _get_settings()
    target = target_language or settings.translate.target_language
    source = source_language or settings.translate.source_language or ""

    return StreamingResponse(
        _sse_generator(request, srt_text, srt_path, target, source),
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
    srt_text: str | None,
    srt_path: str | None,
    target_language: str,
    source_language: str,
):
    """SSE 事件异步生成器。

    用 asyncio.Queue 桥接同步翻译线程和异步 SSE 流。
    """
    started = time.monotonic()
    queue: asyncio.Queue = asyncio.Queue()

    # ── 阶段 1: 解析 SRT ──
    yield _sse("status", {"phase": "prepare", "message": "正在解析 SRT 字幕..."})

    from engines.translate.engine import srt_to_segments, translated_segments_to_srt as to_srt

    try:
        if srt_path:
            path = _resolve_path(srt_path.strip())
            if not path.exists():
                yield _sse("error", {"message": f"SRT 文件不存在: {path}"})
                return
            srt_content = path.read_text(encoding="utf-8")
        else:
            srt_content = srt_text  # type: ignore[assignment]

        segments = srt_to_segments(srt_content)
    except ValueError as e:
        yield _sse("error", {"message": str(e)})
        return

    if not segments:
        yield _sse("error", {"message": "SRT 中未找到任何字幕条目。"})
        return

    total = len(segments)
    yield _sse("status", {
        "phase": "prepare",
        "message": f"解析完成: {total} 条字幕，正在加载翻译引擎...",
    })

    # ── 阶段 2: 获取引擎 ──
    try:
        engine = _get_engine()
    except ValueError as e:
        yield _sse("error", {"message": str(e)})
        return

    settings = _get_settings()
    engine_label = settings.translate.engine
    model_label = (
        settings.translate.model if engine_label == "llm"
        else settings.translate.ollama_model
    )

    yield _sse("status", {
        "phase": "translate",
        "message": f"开始翻译... (引擎: {engine_label}, 模型: {model_label})",
    })

    # ── 阶段 3: 后台翻译 ──
    def _run_translate():
        """在后台线程中跑翻译，每批结果放入队列。"""
        try:
            all_translated: list = []
            for batch_idx, batch in enumerate(engine.translate_stream(
                segments, target_language, source_language,
            ), 1):
                all_translated.extend(batch)
                queue.put_nowait(("batch", {
                    "batch_index": batch_idx,
                    "segments": [
                        {
                            "start": s.start,
                            "end": s.end,
                            "source_text": s.source_text,
                            "translated_text": s.translated_text,
                        }
                        for s in batch
                    ],
                }))
            queue.put_nowait(("done", {
                "segments": [
                    {
                        "start": s.start,
                        "end": s.end,
                        "source_text": s.source_text,
                        "translated_text": s.translated_text,
                    }
                    for s in all_translated
                ],
                "srt": to_srt(all_translated),
            }))
        except Exception as e:
            logger.warning("后台翻译线程异常: %s", e, exc_info=True)
            queue.put_nowait(("error", {"message": str(e)}))

    loop = asyncio.get_event_loop()
    task = loop.run_in_executor(None, _run_translate)

    # ── 阶段 4: 实时推送 ──
    completed = 0
    batch_count = 0

    while True:
        try:
            event_type, data = await asyncio.wait_for(queue.get(), timeout=0.3)
        except asyncio.TimeoutError:
            elapsed = time.monotonic() - started
            pct = min(99, round(completed / total * 100)) if total else 0
            yield _sse("progress", {
                "segments_completed": completed,
                "total_segments": total,
                "batch": batch_count,
                "progress_pct": pct,
                "elapsed": round(elapsed, 1),
            })
            continue

        if event_type == "batch":
            batch_count = data["batch_index"]
            for seg in data["segments"]:
                completed += 1
                yield _sse("translated", {
                    **seg,
                    "index": completed,
                    "batch": batch_count,
                })

        elif event_type == "done":
            elapsed = round(time.monotonic() - started, 1)
            yield _sse("done", {
                "total_segments": completed,
                "elapsed": elapsed,
                "engine": engine_label,
                "model": model_label,
                "segments": data["segments"],
                "srt": data["srt"],
            })
            return

        elif event_type == "error":
            yield _sse("error", data)
            return


# ---------------------------------------------------------------------------
# 核心翻译逻辑（同步版本）
# ---------------------------------------------------------------------------


async def _run_translate(
    request: Request,
    srt_text: str | None,
    srt_path: str | None,
    target_language: str,
    source_language: str,
) -> dict:
    """执行完整翻译流程：解析 SRT → 翻译 → 生成译文 SRT。

    Returns:
        结果字典。
    """
    from engines.translate.engine import srt_to_segments, translated_segments_to_srt as to_srt

    started = time.monotonic()

    # 解析 SRT
    if srt_path:
        path = _resolve_path(srt_path.strip())
        if not path.exists():
            raise ValueError(f"SRT 文件不存在: {path}")
        srt_content = path.read_text(encoding="utf-8")
    else:
        srt_content = srt_text  # type: ignore[assignment]

    segments = srt_to_segments(srt_content)
    if not segments:
        raise ValueError("SRT 中未找到任何字幕条目。")

    # 翻译
    engine = _get_engine()
    translated = engine.translate(segments, target_language, source_language)

    # 生成译文 SRT
    srt_output = to_srt(translated)

    elapsed = round(time.monotonic() - started, 1)
    settings = _get_settings()
    engine_label = settings.translate.engine
    model_label = (
        settings.translate.model if engine_label == "llm"
        else settings.translate.ollama_model
    )

    logger.info("翻译完成: %d 条 → %s, 耗时 %.1fs", len(translated), target_language, elapsed)

    return {
        "source_count": len(segments),
        "translated_count": len(translated),
        "target_language": target_language,
        "elapsed": elapsed,
        "engine": engine_label,
        "model": model_label,
        "segments": [
            {
                "start": s.start,
                "end": s.end,
                "source_text": s.source_text,
                "translated_text": s.translated_text,
            }
            for s in translated
        ],
        "srt": srt_output,
    }


# ---------------------------------------------------------------------------
# 错误响应
# ---------------------------------------------------------------------------


def _translate_error(
    request: Request,
    templates,
    message: str,
    status_code: int,
) -> JSONResponse | HTMLResponse:
    """统一的翻译错误响应。"""
    is_htmx = request.headers.get("hx-request", "").lower() == "true" if request else False
    if is_htmx and templates:
        return templates.TemplateResponse(request, "translate_results.html", {
            "error": message,
            "result": None,
        })
    return JSONResponse(
        content={"success": False, "error": message},
        status_code=status_code,
    )
