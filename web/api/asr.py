"""ASR 语音识别 REST 端点。

POST /api/asr/transcribe        — 同步转写（JSON / HTML）。
POST /api/asr/transcribe/stream — SSE 流式转写（实时进度 + 逐片段推送）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

logger = logging.getLogger("video_localizer.api.asr")

router = APIRouter(prefix="/api/asr")

# ---------------------------------------------------------------------------
# 引擎单例
# ---------------------------------------------------------------------------

_engine = None


def _get_engine():
    """延迟创建 ASR 引擎单例（首次请求时加载模型）。"""
    global _engine
    if _engine is None:
        from engines.asr.whisper_local import WhisperLocalEngine

        # 延迟导入避免循环引用
        from app import settings
        cfg = settings.asr
        logger.info(
            "初始化 ASR 引擎: engine=%s, model=%s, device=%s, compute=%s",
            cfg.engine, cfg.model_size, cfg.device, cfg.compute_type,
        )
        _engine = WhisperLocalEngine(
            model_size=cfg.model_size,
            device=cfg.device,
            compute_type=cfg.compute_type,
            beam_size=cfg.beam_size,
            vad_filter=cfg.vad_filter,
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


def _extract_audio(input_path: Path, stream_index: int = 0) -> Path:
    """从媒体文件提取音轨到临时目录。

    Args:
        input_path: 媒体文件路径。
        stream_index: 音频流索引（0=第一个音轨）。

    Returns:
        提取后的音频文件路径。
    """
    from processing.core.extract import extract_stream

    settings = _get_settings()
    temp_dir = settings.paths.temp_dir
    temp_dir.mkdir(parents=True, exist_ok=True)

    result = extract_stream(
        input_path=input_path,
        output_dir=temp_dir,
        stream_index=stream_index,
        stream_type="audio",
        ffmpeg_path=settings.ffmpeg.executable,
        ffprobe_path=settings.ffmpeg.ffprobe_executable,
        overwrite=True,
    )
    return result.output_path


# ---------------------------------------------------------------------------
# 端点
# ---------------------------------------------------------------------------


@router.post("/transcribe", response_model=None)
async def asr_transcribe_post(
    request: Request,
    file_path: str | None = Form(None),
    language: str | None = Form(None),
    model_size: str | None = Form(None),
):
    """POST /api/asr/transcribe — 转写媒体文件的语音内容。

    - file_path: 媒体文件路径（必填）
    - language: ISO 639-1 语言代码，留空则自动检测
    - model_size: 覆盖默认模型大小（可选）
    """
    if not file_path or not file_path.strip():
        return _asr_error(request, "请提供 file_path 参数。", 400)

    settings = _get_settings()
    templates = _get_templates(request)
    path = _resolve_path(file_path.strip())

    logger.info("ASR 转写请求: %s (语言=%s)", path, language or "auto")

    try:
        result = await _run_asr(request, path, language, model_size)
    except Exception as e:
        logger.warning("ASR 转写失败: %s", e, exc_info=True)
        return _asr_error(request, str(e), 422)

    # 响应
    is_htmx = request.headers.get("hx-request", "").lower() == "true"
    if is_htmx:
        return templates.TemplateResponse(request, "asr_results.html", {
            "error": None,
            "result": result,
        })

    return JSONResponse(content={"success": True, **result})


@router.get("/transcribe", response_model=None)
async def asr_transcribe_get(
    request: Request,
    file_path: str | None = None,
    language: str | None = None,
):
    """GET /api/asr/transcribe?file_path= — 转写（JSON 响应）。"""
    if not file_path or not file_path.strip():
        return JSONResponse(
            content={"success": False, "error": "请提供 ?file_path= 参数。"},
            status_code=400,
        )

    path = _resolve_path(file_path.strip())
    logger.info("ASR 转写请求 (GET): %s", path)

    try:
        result = await _run_asr(request, path, language)
    except Exception as e:
        logger.warning("ASR 转写失败: %s", e, exc_info=True)
        return JSONResponse(
            content={"success": False, "error": str(e)},
            status_code=422,
        )

    return JSONResponse(content={"success": True, **result})


# ---------------------------------------------------------------------------
# SSE 流式转写
# ---------------------------------------------------------------------------


@router.post("/transcribe/stream", response_model=None)
async def asr_transcribe_stream(
    request: Request,
    file_path: str | None = Form(None),
    language: str | None = Form(None),
):
    """POST /api/asr/transcribe/stream — SSE 流式转写，实时推送进度和片段。

    事件类型:
      status   — 阶段状态（probe / extract / transcribe）
      segment  — 单个转写片段
      progress — 进度心跳（每 0.3s）
      done     — 完成（含 SRT 和统计）
      error    — 错误
    """
    if not file_path or not file_path.strip():
        return _asr_error(request, "请提供 file_path 参数。", 400)

    path = _resolve_path(file_path.strip())
    lang_param = language if language and language != "auto" else None

    return StreamingResponse(
        _sse_generator(request, path, lang_param),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _sse_generator(request: Request, input_path: Path, language: str | None):
    """SSE 事件异步生成器。

    用 asyncio.Queue 桥接同步转写线程和异步 SSE 流：
    - 后台线程：跑 faster-whisper，每个片段 put 到队列
    - 主协程：从队列取 → yield SSE 事件
    """
    started = time.monotonic()
    queue: asyncio.Queue = asyncio.Queue()
    settings = _get_settings()

    logger.info("SSE 转写开始: %s (语言=%s)", input_path.name, language or "auto")

    # ── 阶段 1: 探测 + 提取 ──
    yield _sse("status", {"phase": "probe", "message": "正在探测媒体文件..."})

    from processing.core.probe import ProbeError, probe_file

    try:
        probe = await asyncio.to_thread(
            probe_file, input_path,
            ffprobe_path=settings.ffmpeg.ffprobe_executable, timeout=30,
        )
    except ProbeError as e:
        yield _sse("error", {"message": str(e)})
        return

    if not probe.audio_streams:
        yield _sse("error", {"message": "文件中没有音频流，无法转写。"})
        return

    audio_stream = probe.audio_streams[0]

    yield _sse("status", {"phase": "extract", "message": "正在提取音轨..."})

    from processing.core.extract import ExtractError

    try:
        audio_path = await asyncio.to_thread(
            _extract_audio, input_path, stream_index=0,
        )
    except (ExtractError, ValueError) as e:
        yield _sse("error", {"message": f"音频提取失败: {e}"})
        return

    yield _sse("status", {"phase": "load", "message": "正在加载 Whisper 模型到 GPU..."})
    engine = _get_engine()

    # 模型加载必须在主线程完成（CUDA 上下文初始化在子线程中会死锁）
    t0 = time.monotonic()
    model = engine._get_model()
    t1 = time.monotonic()
    logger.info("模型加载耗时 %.1fs", t1 - t0)

    # ── 阶段 2: 后台转写 ──
    yield _sse("status", {
        "phase": "transcribe",
        "message": f"模型就绪 (%.1fs)，开始转写... (模型: {engine._model_size}, 设备: {engine._device})" % (t1 - t0),
    })

    def _run_transcribe():
        """在后台线程中跑转写，每个片段放入队列。"""
        try:
            seg_iter, info = model.transcribe(
                str(audio_path),
                language=language,
                beam_size=engine._beam_size,
                vad_filter=engine._vad_filter,
                vad_parameters=dict(
                    min_silence_duration_ms=500, threshold=0.5,
                ) if engine._vad_filter else None,
            )
            logger.info("开始推理，等待首个片段...")
            seg_count = 0
            for seg in seg_iter:
                seg_count += 1
                if seg_count == 1:
                    t_first = time.monotonic()
                    logger.info("首个片段到达 (%.1fs): [%.1f-%.1f] %s", t_first - t1, seg.start, seg.end, seg.text[:80])
                text = seg.text.strip()
                if not text:
                    continue
                queue.put_nowait(("segment", {
                    "start": round(seg.start, 3),
                    "end": round(seg.end, 3),
                    "text": text,
                    "confidence": round(getattr(seg, "avg_logprob", 0.0), 3),
                }))
            logger.info("转写完成: %d 有效片段, 语言=%s (%.2f)", seg_count, info.language, info.language_probability)
            queue.put_nowait(("done", {
                "language": info.language,
                "language_probability": round(info.language_probability, 3),
            }))
        except Exception as e:
            logger.warning("后台转写线程异常: %s", e, exc_info=True)
            queue.put_nowait(("error", {"message": str(e)}))
        finally:
            # 清理临时音频
            try:
                audio_path.unlink(missing_ok=True)
            except OSError:
                pass

    # 启动后台线程
    loop = asyncio.get_event_loop()
    task = loop.run_in_executor(None, _run_transcribe)

    # ── 阶段 3: 实时推送 ──
    all_segments: list[dict] = []
    done_info = {}

    while True:
        try:
            event_type, data = await asyncio.wait_for(queue.get(), timeout=0.3)
        except asyncio.TimeoutError:
            # 心跳：推送进度（加载期间也发送，避免前端卡住）
            elapsed = time.monotonic() - started
            if all_segments:
                audio_dur = audio_stream.duration or 1
                last_end = all_segments[-1]["end"]
                progress_pct = min(99, round(last_end / audio_dur * 100)) if audio_dur else 0
                speed = round(last_end / elapsed, 1) if elapsed > 0 else 0
                yield _sse("progress", {
                    "segments": len(all_segments),
                    "elapsed": round(elapsed, 1),
                    "progress_pct": progress_pct,
                    "speed": f"{speed}x",
                })
            else:
                # 尚无片段：模型仍在加载中，发送 0% 心跳
                yield _sse("progress", {
                    "segments": 0,
                    "elapsed": round(elapsed, 1),
                    "progress_pct": 0,
                    "speed": "加载中...",
                })
            continue

        if event_type == "segment":
            all_segments.append(data)
            elapsed = time.monotonic() - started
            audio_dur = audio_stream.duration or 1
            last_end = data["end"]
            progress_pct = min(99, round(last_end / audio_dur * 100)) if audio_dur else 0
            speed = round(last_end / elapsed, 1) if elapsed > 0 else 0
            yield _sse("segment", {
                **data,
                "index": len(all_segments),
                "elapsed": round(elapsed, 1),
                "progress_pct": progress_pct,
                "speed": f"{speed}x",
            })

        elif event_type == "status":
            yield _sse("status", data)

        elif event_type == "done":
            done_info = data
            break

        elif event_type == "error":
            yield _sse("error", data)
            return

    # ── 阶段 4: 完成 ──
    from engines.asr.engine import segments_to_srt

    elapsed = round(time.monotonic() - started, 1)
    srt_text = segments_to_srt([
        type("ASRSegment", (), {"start": s["start"], "end": s["end"], "text": s["text"]})
        for s in all_segments
    ])

    yield _sse("done", {
        "language": done_info.get("language", "unknown"),
        "duration": audio_stream.duration,
        "total_segments": len(all_segments),
        "elapsed": elapsed,
        "speed": f"{round((audio_stream.duration or 1) / elapsed, 1)}x",
        "model": engine._model_size,
        "device": engine._device,
        "segments": all_segments,
        "srt": srt_text,
    })


def _sse(event: str, data: dict) -> str:
    """构建一条 SSE 消息。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# ---------------------------------------------------------------------------
# 核心转写逻辑（同步版本）
# ---------------------------------------------------------------------------


async def _run_asr(
    request: Request,
    input_path: Path,
    language: str | None,
    model_size_override: str | None = None,
) -> dict:
    """执行完整的 ASR 流水线：提取音轨 → 转写 → 生成 SRT。

    Args:
        request: FastAPI Request。
        input_path: 媒体文件路径。
        language: 源语言（None=自动检测）。
        model_size_override: 覆盖配置中的模型大小。

    Returns:
        结果字典。
    """
    from engines.asr.engine import segments_to_srt

    started = time.monotonic()

    # 0) 探测文件
    from processing.core.probe import probe_file

    settings = _get_settings()
    probe = probe_file(input_path, ffprobe_path=settings.ffmpeg.ffprobe_executable, timeout=30)

    if not probe.audio_streams:
        raise ValueError("文件中没有音频流，无法转写。")

    # 1) 提取第一个音轨
    from processing.core.extract import ExtractError

    audio_index = 0  # 默认提取第一条音轨
    try:
        audio_path = _extract_audio(input_path, stream_index=audio_index)
        logger.info("音频提取完成: %s", audio_path)
    except ExtractError as e:
        raise ValueError(f"音频提取失败: {e}")

    # 2) 转写
    engine = _get_engine()
    lang_param = language if language and language != "auto" else None

    segments = engine.transcribe(audio_path, language=lang_param)
    detected_lang = lang_param or "unknown"

    # 获取实际检测到的语言
    if not lang_param and segments:
        # 从 faster-whisper 获取检测结果（在 transcribe 中已记录）
        pass

    # 3) 生成 SRT
    srt_text = segments_to_srt(segments)

    # 4) 清理临时音频
    try:
        audio_path.unlink(missing_ok=True)
    except OSError:
        pass

    elapsed = round(time.monotonic() - started, 1)

    audio_stream = probe.audio_streams[audio_index]
    lang_label = detected_lang or lang_param or "auto"

    logger.info(
        "ASR 完成: %d 片段, %s, 耗时 %.1fs",
        len(segments), lang_label, elapsed,
    )

    return {
        "language": lang_label,
        "duration": audio_stream.duration,
        "segments": [
            {
                "start": s.start,
                "end": s.end,
                "text": s.text,
                "confidence": s.confidence,
            }
            for s in segments
        ],
        "srt": srt_text,
        "stats": {
            "model": engine._model_size,
            "device": engine._device,
            "compute_type": engine._compute_type,
            "elapsed_seconds": elapsed,
            "segment_count": len(segments),
            "audio_stream_index": audio_index,
        },
    }


# ---------------------------------------------------------------------------
# 保存 SRT 到输出目录
# ---------------------------------------------------------------------------

from pydantic import BaseModel


class SaveSRTRequest(BaseModel):
    srt_content: str
    file_name: str
    video_path: str = ""


@router.post("/save")
async def asr_save_srt(body: SaveSRTRequest):
    """POST /api/asr/save — 保存 SRT 字幕到输出目录。"""
    settings = _get_settings()
    output_dir = settings.paths.media_output
    output_dir.mkdir(parents=True, exist_ok=True)

    file_name = body.file_name
    # 防止路径穿越
    file_name = Path(file_name).name
    if not file_name.endswith(".srt"):
        file_name += ".srt"

    output_path = output_dir / file_name
    try:
        output_path.write_text(body.srt_content, encoding="utf-8")
        logger.info("SRT 已保存: %s (%d 字节)", output_path, len(body.srt_content))
        return JSONResponse(content={
            "success": True,
            "file_path": str(output_path),
        })
    except OSError as e:
        logger.warning("保存 SRT 失败: %s", e)
        return JSONResponse(
            content={"success": False, "error": str(e)},
            status_code=500,
        )


# ---------------------------------------------------------------------------
# 错误响应
# ---------------------------------------------------------------------------


def _asr_error(request: Request, message: str, status_code: int) -> JSONResponse | HTMLResponse:
    """统一的 ASR 错误响应。"""
    is_htmx = request.headers.get("hx-request", "").lower() == "true"
    if is_htmx:
        templates = _get_templates(request)
        return templates.TemplateResponse(request, "asr_results.html", {
            "error": message,
            "result": None,
        })
    return JSONResponse(
        content={"success": False, "error": message},
        status_code=status_code,
    )
