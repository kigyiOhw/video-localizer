"""端到端流水线：视频 → ASR → 翻译 → 封装字幕。

提供同步和流式两种版本。流式版本逐阶段 yield 事件 dict，供 SSE 层消费。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from engines.asr.engine import ASREngine, ASRSegment, segments_to_srt
from engines.translate.engine import (
    TranslateEngine,
    TranslateSegment,
    translated_segments_to_srt,
)

logger = logging.getLogger("video_localizer.pipeline")


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------


class PipelineError(Exception):
    """流水线执行失败。"""


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------


@dataclass
class PipelineResult:
    """全流程结果。"""

    video_path: Path
    output_path: Path                # 最终 MKV 文件
    output_size: int                 # 输出文件大小（字节）
    srt_original: str                # ASR 产出的原文 SRT 内容
    srt_translated: str              # 翻译后的 SRT 内容
    srt_original_path: Path | None = None  # 原文 SRT 文件路径（暂存）
    srt_translated_path: Path | None = None  # 译文 SRT 文件路径（暂存）
    asr_segments: list[dict[str, Any]] = field(default_factory=list)
    translated_segments: list[dict[str, Any]] = field(default_factory=list)
    source_language: str = ""        # 检测到的源语言
    target_language: str = ""        # 目标语言
    asr_elapsed: float = 0.0
    translate_elapsed: float = 0.0
    total_elapsed: float = 0.0


# ---------------------------------------------------------------------------
# 同步流水线
# ---------------------------------------------------------------------------


def run_full_pipeline(
    video_path: Path,
    target_language: str,
    asr_engine: ASREngine,
    translate_engine: TranslateEngine,
    source_language: str = "",
    output_dir: Path | None = None,
    temp_dir: Path | None = None,
    ffmpeg_path: str = "ffmpeg",
    ffprobe_path: str = "ffprobe",
    container: str = "mkv",
    overwrite: bool = True,
) -> PipelineResult:
    """端到端流水线：视频 → ASR → 翻译 → 封装字幕。

    步骤：
    1. probe 探测媒体文件
    2. extract 提取第一个音轨到临时文件
    3. asr_engine.transcribe() 语音识别
    4. segments_to_srt() 生成原文 SRT
    5. ASRSegment → TranslateSegment 转换
    6. translate_engine.translate() 翻译
    7. translated_segments_to_srt() 生成译文 SRT
    8. 保存译文 SRT 到临时文件
    9. add_subtitle() 封装到 MKV
    10. 清理临时文件

    Args:
        video_path: 输入视频文件路径。
        target_language: 目标语言（LLM prompt 用名称，如 "Chinese"）。
        asr_engine: ASR 引擎实例。
        translate_engine: 翻译引擎实例。
        source_language: 源语言（空=自动检测）。
        output_dir: 输出目录，None 则用 video_path 同级的 output/。
        ffmpeg_path: ffmpeg 可执行文件路径。
        ffprobe_path: ffprobe 可执行文件路径。
        container: 输出容器格式 ("mkv" | "mp4")。
        overwrite: 是否覆盖已存在的输出文件。

    Returns:
        PipelineResult 含输出路径、SRT 内容、统计信息。

    Raises:
        PipelineError: 任何阶段失败。
    """
    started = time.monotonic()
    temp_files: list[Path] = []

    # ── 验证输入 ──
    if not video_path.exists():
        raise PipelineError(f"视频文件不存在: {video_path}")
    if not video_path.is_file():
        raise PipelineError(f"视频路径不是文件: {video_path}")

    container = container.lower()
    if container not in ("mkv", "mp4"):
        raise PipelineError(f"不支持的容器格式: {container}")

    # ── 步骤 1: 探测 ──
    logger.info("阶段 1/4: 探测 %s", video_path.name)
    from processing.core.probe import ProbeError, probe_file

    try:
        probe = probe_file(video_path, ffprobe_path=ffprobe_path, timeout=30)
    except ProbeError as e:
        raise PipelineError(f"探测失败: {e}")

    if not probe.audio_streams:
        raise PipelineError("文件中没有音频流，无法进行语音识别。")

    # ── 步骤 2: 提取音轨 ──
    logger.info("阶段 2/4: 提取音轨")
    from processing.core.extract import ExtractError, extract_stream

    if temp_dir is None:
        temp_dir = _temp_dir(video_path)
    temp_dir.mkdir(parents=True, exist_ok=True)

    try:
        extract_result = extract_stream(
            input_path=video_path,
            output_dir=temp_dir,
            stream_index=0,
            stream_type="audio",
            ffmpeg_path=ffmpeg_path,
            ffprobe_path=ffprobe_path,
            overwrite=True,
        )
    except ExtractError as e:
        raise PipelineError(f"音轨提取失败: {e}")

    audio_path = extract_result.output_path
    temp_files.append(audio_path)

    # ── 步骤 3: ASR 语音识别 ──
    logger.info("阶段 3/4: ASR 语音识别")
    t_asr_start = time.monotonic()

    try:
        asr_segments = asr_engine.transcribe(audio_path, language=_asr_lang(source_language))
    except Exception as e:
        raise PipelineError(f"语音识别失败: {e}")

    if not asr_segments:
        raise PipelineError("语音识别未返回任何片段。")

    t_asr_end = time.monotonic()

    # 检测到的语言
    detected_lang = _get_detected_language(asr_engine, audio_path, source_language)

    # 生成原文 SRT
    srt_original = segments_to_srt(asr_segments)

    # ── 步骤 4: 翻译 ──
    logger.info("阶段 4/4: 翻译 (%s → %s)", detected_lang or "auto", target_language)
    t_translate_start = time.monotonic()

    translate_segments = _asr_to_translate_segments(asr_segments)

    src_lang_for_translation = _source_language_for_translation(source_language, detected_lang)

    try:
        translated = translate_engine.translate(
            translate_segments, target_language, src_lang_for_translation,
        )
    except Exception as e:
        raise PipelineError(f"翻译失败: {e}")

    t_translate_end = time.monotonic()

    # 生成译文 SRT
    srt_translated = translated_segments_to_srt(translated)

    # ── 步骤 5: 封装字幕 ──
    logger.info("封装: 将译文字幕嵌入 %s", video_path.name)
    from processing.core.mux import MuxError, add_subtitle

    # 输出路径
    if output_dir is None:
        output_dir = video_path.parent / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── 暂存 SRT 到输出目录（方便后续复用，避免重复 ASR + 翻译）──
    srt_original_path = output_dir / f"{video_path.stem}_original.srt"
    srt_original_path.write_text(srt_original, encoding="utf-8")
    srt_translated_path = output_dir / f"{video_path.stem}_translated.srt"
    srt_translated_path.write_text(srt_translated, encoding="utf-8")
    logger.info("SRT 已暂存: %s, %s", srt_original_path.name, srt_translated_path.name)

    # 译文 SRT 作为封装输入
    srt_temp = srt_translated_path
    output_path = output_dir / f"{video_path.stem}_subtitled.{container}"

    try:
        mux_result = add_subtitle(
            video_path=video_path,
            subtitle_path=srt_temp,
            language=_lang_name_to_code(target_language),
            output_path=output_path,
            container=container,
            ffmpeg_path=ffmpeg_path,
            overwrite=overwrite,
        )
    except MuxError as e:
        raise PipelineError(f"封装失败: {e}")

    total_elapsed = round(time.monotonic() - started, 1)
    asr_elapsed = round(t_asr_end - t_asr_start, 1)
    translate_elapsed = round(t_translate_end - t_translate_start, 1)

    # ── 清理临时文件 ──
    for f in temp_files:
        try:
            f.unlink(missing_ok=True)
        except OSError:
            logger.debug("清理临时文件失败: %s", f)

    logger.info(
        "流水线完成: %s → %s (ASR %.1fs + 翻译 %.1fs = %.1fs)",
        video_path.name, output_path.name, asr_elapsed, translate_elapsed, total_elapsed,
    )

    return PipelineResult(
        video_path=video_path,
        output_path=output_path,
        output_size=mux_result.output_size,
        srt_original=srt_original,
        srt_translated=srt_translated,
        srt_original_path=srt_original_path,
        srt_translated_path=srt_translated_path,
        asr_segments=[
            {"start": s.start, "end": s.end, "text": s.text, "confidence": s.confidence}
            for s in asr_segments
        ],
        translated_segments=[
            {
                "start": s.start,
                "end": s.end,
                "source_text": s.source_text,
                "translated_text": s.translated_text,
            }
            for s in translated
        ],
        source_language=detected_lang,
        target_language=target_language,
        asr_elapsed=asr_elapsed,
        translate_elapsed=translate_elapsed,
        total_elapsed=total_elapsed,
    )


# ---------------------------------------------------------------------------
# 流式流水线
# ---------------------------------------------------------------------------


def run_full_pipeline_stream(
    video_path: Path,
    target_language: str,
    asr_engine: ASREngine,
    translate_engine: TranslateEngine,
    source_language: str = "",
    output_dir: Path | None = None,
    temp_dir: Path | None = None,
    ffmpeg_path: str = "ffmpeg",
    ffprobe_path: str = "ffprobe",
    container: str = "mkv",
    overwrite: bool = True,
) -> Iterator[dict[str, Any]]:
    """流式端到端流水线生成器。

    逐阶段 yield 事件 dict：{"event": "status"|"segment"|"translated"|"progress"|"done"|"error", "data": {...}}

    阶段顺序：
    1. probe    → event: "status" {"phase": "probe"}
    2. extract  → event: "status" {"phase": "extract"}
    3. ASR      → 逐片段 event: "segment"
    4. translate → 逐批 event: "translated"
    5. mux      → event: "done"
    """
    temp_files: list[Path] = []

    # ── 验证 ──
    if not video_path.exists():
        yield _evt("error", {"message": f"视频文件不存在: {video_path}"})
        return
    if not video_path.is_file():
        yield _evt("error", {"message": f"视频路径不是文件: {video_path}"})
        return

    container = container.lower()
    if container not in ("mkv", "mp4"):
        yield _evt("error", {"message": f"不支持的容器格式: {container}"})
        return

    # ── 阶段 1: 探测 ──
    yield _evt("status", {"phase": "probe", "message": "正在探测媒体文件..."})

    from processing.core.probe import ProbeError, probe_file

    try:
        probe = probe_file(video_path, ffprobe_path=ffprobe_path, timeout=30)
    except ProbeError as e:
        yield _evt("error", {"message": str(e)})
        return

    if not probe.audio_streams:
        yield _evt("error", {"message": "文件中没有音频流，无法进行语音识别。"})
        return

    audio_stream = probe.audio_streams[0]

    # ── 阶段 2: 提取音轨 ──
    yield _evt("status", {
        "phase": "extract",
        "message": f"正在提取音轨... (编码: {audio_stream.codec})",
    })

    from processing.core.extract import ExtractError, extract_stream

    if temp_dir is None:
        temp_dir = _temp_dir(video_path)
    temp_dir.mkdir(parents=True, exist_ok=True)

    try:
        extract_result = extract_stream(
            input_path=video_path,
            output_dir=temp_dir,
            stream_index=0,
            stream_type="audio",
            ffmpeg_path=ffmpeg_path,
            ffprobe_path=ffprobe_path,
            overwrite=True,
        )
    except ExtractError as e:
        yield _evt("error", {"message": f"音轨提取失败: {e}"})
        return

    audio_path = extract_result.output_path
    temp_files.append(audio_path)

    # ── 阶段 3: ASR 语音识别 ──
    yield _evt("status", {
        "phase": "asr",
        "message": f"正在进行语音识别... (引擎: {getattr(asr_engine, '_model_size', 'unknown')})",
    })

    all_asr_segments: list[dict[str, Any]] = []

    # 检测语言（与同步版一致）
    detected_lang = _get_detected_language(asr_engine, audio_path, source_language)

    try:
        seg_count = 0
        for seg in asr_engine.transcribe_stream(audio_path, language=_asr_lang(source_language)):
            seg_count += 1
            seg_data = {
                "start": seg.start,
                "end": seg.end,
                "text": seg.text,
                "confidence": seg.confidence,
                "index": seg_count,
            }
            all_asr_segments.append(seg_data)
            yield _evt("segment", seg_data)
        logger.info("ASR 流式完成: %d 片段, 语言=%s", seg_count, detected_lang)
    except Exception as e:
        yield _evt("error", {"message": f"语音识别失败: {e}"})
        return

    if not all_asr_segments:
        yield _evt("error", {"message": "语音识别未返回任何片段。"})
        return

    yield _evt("status", {
        "phase": "asr_done",
        "message": f"语音识别完成: {len(all_asr_segments)} 个片段 (语言: {detected_lang})",
    })

    # 生成原文 SRT（用于 done 事件中返回）
    srt_original = segments_to_srt([
        ASRSegment(s["start"], s["end"], s["text"], s["confidence"])
        for s in all_asr_segments
    ])

    # ── 阶段 4: 翻译 ──
    yield _evt("status", {
        "phase": "translate",
        "message": f"正在翻译... ({detected_lang or 'auto'} → {target_language})",
    })

    translate_segments = _dicts_to_translate_segments(all_asr_segments)

    src_lang_for_translation = _source_language_for_translation(source_language, detected_lang)

    try:
        all_translated: list[dict[str, Any]] = []
        for batch_idx, batch in enumerate(
            translate_engine.translate_stream(translate_segments, target_language, src_lang_for_translation), 1
        ):
            batch_data = []
            for s in batch:
                seg_dict = {
                    "start": s.start,
                    "end": s.end,
                    "source_text": s.source_text,
                    "translated_text": s.translated_text,
                }
                batch_data.append(seg_dict)
                all_translated.append(seg_dict)
            yield _evt("translated", {
                "batch": batch_idx,
                "segments": batch_data,
            })
    except Exception as e:
        yield _evt("error", {"message": f"翻译失败: {e}"})
        return

    yield _evt("status", {
        "phase": "translate_done",
        "message": f"翻译完成: {len(all_translated)} 条",
    })

    # 生成译文 SRT
    srt_translated = translated_segments_to_srt([
        TranslateSegment(
            s["start"], s["end"],
            s["source_text"], s["translated_text"],
        )
        for s in all_translated
    ])

    # ── 阶段 5: 封装 ──
    yield _evt("status", {
        "phase": "mux",
        "message": "正在封装字幕到视频...",
    })

    from processing.core.mux import MuxError, add_subtitle

    if output_dir is None:
        output_dir = video_path.parent / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── 暂存 SRT 到输出目录（方便后续复用，避免重复 ASR + 翻译）──
    srt_original_path = output_dir / f"{video_path.stem}_original.srt"
    srt_original_path.write_text(srt_original, encoding="utf-8")
    srt_translated_path = output_dir / f"{video_path.stem}_translated.srt"
    srt_translated_path.write_text(srt_translated, encoding="utf-8")
    logger.info("SRT 已暂存: %s, %s", srt_original_path.name, srt_translated_path.name)

    # 译文 SRT 作为封装输入（用暂存文件，无需额外临时文件）
    srt_temp = srt_translated_path
    output_path = output_dir / f"{video_path.stem}_subtitled.{container}"

    try:
        mux_result = add_subtitle(
            video_path=video_path,
            subtitle_path=srt_temp,
            language=_lang_name_to_code(target_language),
            output_path=output_path,
            container=container,
            ffmpeg_path=ffmpeg_path,
            overwrite=overwrite,
        )
    except MuxError as e:
        yield _evt("error", {"message": f"封装失败: {e}"})
        return

    # ── 清理 ──
    for f in temp_files:
        try:
            f.unlink(missing_ok=True)
        except OSError:
            pass

    # ── 完成 ──
    yield _evt("done", {
        "output_path": str(output_path),
        "output_size": mux_result.output_size,
        "source_language": detected_lang,
        "target_language": target_language,
        "total_segments": len(all_translated),
        "srt_original": srt_original,
        "srt_translated": srt_translated,
        "srt_original_path": str(srt_original_path) if srt_original_path else None,
        "srt_translated_path": str(srt_translated_path) if srt_translated_path else None,
        "segments": all_translated,
        "download_url": f"/api/subtitle/download?path={output_path.as_posix()}",
        "download_srt_original": f"/api/subtitle/download?path={srt_original_path.as_posix()}" if srt_original_path else None,
        "download_srt_translated": f"/api/subtitle/download?path={srt_translated_path.as_posix()}" if srt_translated_path else None,
    })


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------


def _evt(event: str, data: dict[str, Any]) -> dict[str, Any]:
    """构建事件 dict。"""
    return {"event": event, "data": data}


def _temp_dir(video_path: Path) -> Path:
    """获取临时目录的回退路径（仅当调用方未传入 temp_dir 时使用）。

    应优先通过函数的 temp_dir 参数传入配置的临时目录。
    """
    return video_path.parent / "output" / ".pipeline_tmp"


def _asr_lang(source_language: str) -> str | None:
    """将源语言参数转为 ASR 引擎可接受的格式。

    ISO 639-3 代码 (zho, eng) 需转为 ISO 639-1 (zh, en) 供 faster-whisper 使用。
    空字符串表示自动检测。
    """
    if not source_language:
        return None
    # ISO 639-3 → ISO 639-1 常用映射
    mapping = {
        "zho": "zh", "chi": "zh",
        "eng": "en",
        "jpn": "ja",
        "kor": "ko",
        "fra": "fr", "fre": "fr",
        "deu": "de", "ger": "de",
        "spa": "es",
        "por": "pt",
        "rus": "ru",
        "ara": "ar",
        "ita": "it",
        "tha": "th",
        "vie": "vi",
        "hin": "hi",
        "ind": "id",
        "msa": "ms",
        "tur": "tr",
        "pol": "pl",
        "nld": "nl",
        "swe": "sv",
        "nor": "no",
        "dan": "da",
        "fin": "fi",
        "ukr": "uk",
    }
    code = source_language.strip().lower()
    return mapping.get(code, code)


def _lang_name_to_code(name: str) -> str:
    """语言名称 → ISO 639-2/T 代码（用于 FFmpeg metadata）。

    这是一个简化的映射，覆盖常用语言。
    """
    mapping = {
        "chinese": "zho", "english": "eng", "japanese": "jpn",
        "korean": "kor", "french": "fra", "german": "deu",
        "spanish": "spa", "portuguese": "por", "russian": "rus",
        "arabic": "ara", "italian": "ita", "thai": "tha",
        "vietnamese": "vie", "hindi": "hin", "indonesian": "ind",
        "malay": "msa", "turkish": "tur", "polish": "pol",
        "dutch": "nld", "swedish": "swe", "norwegian": "nor",
        "danish": "dan", "finnish": "fin", "ukrainian": "ukr",
    }
    return mapping.get(name.strip().lower(), name[:3].lower())


_ISO639_1_TO_NAME: dict[str, str] = {
    "zh": "Chinese",
    "en": "English",
    "ja": "Japanese",
    "ko": "Korean",
    "fr": "French",
    "de": "German",
    "es": "Spanish",
    "pt": "Portuguese",
    "ru": "Russian",
    "ar": "Arabic",
    "it": "Italian",
    "th": "Thai",
    "vi": "Vietnamese",
    "hi": "Hindi",
    "id": "Indonesian",
    "ms": "Malay",
    "tr": "Turkish",
    "pl": "Polish",
    "nl": "Dutch",
    "sv": "Swedish",
    "no": "Norwegian",
    "da": "Danish",
    "fi": "Finnish",
    "uk": "Ukrainian",
}


def _source_language_for_translation(source_language: str, detected_lang: str) -> str:
    """确定传给翻译引擎的源语言。

    用户已指定时直接使用；未指定时将 ASR 检测到的 ISO 639-1 代码转换为语言名称。
    """
    if source_language and source_language.strip():
        return source_language.strip()
    return _ISO639_1_TO_NAME.get(detected_lang.strip().lower(), detected_lang.strip())


def _asr_to_translate_segments(asr_segments: list[ASRSegment]) -> list[TranslateSegment]:
    """ASRSegment → TranslateSegment 转换。"""
    return [
        TranslateSegment(
            start=s.start,
            end=s.end,
            source_text=s.text,
        )
        for s in asr_segments
    ]


def _dicts_to_translate_segments(seg_dicts: list[dict[str, Any]]) -> list[TranslateSegment]:
    """片段 dict 列表 → TranslateSegment 列表。"""
    return [
        TranslateSegment(
            start=s["start"],
            end=s["end"],
            source_text=s["text"],
        )
        for s in seg_dicts
    ]


def _get_detected_language(
    asr_engine: ASREngine, audio_path: Path, fallback: str,
) -> str:
    """获取 ASR 检测到的语言。"""
    if fallback and fallback.strip():
        return fallback.strip()
    try:
        detected = asr_engine.detect_language(audio_path)
        return detected or "unknown"
    except Exception:
        return "unknown"
