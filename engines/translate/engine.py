"""翻译引擎抽象基类。

所有翻译实现必须继承此基类（策略模式）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterator


@dataclass
class TranslateSegment:
    """翻译片段，包含原始字幕的时间戳和文本。"""

    start: float      # 起始秒数
    end: float        # 结束秒数
    source_text: str  # 原文
    translated_text: str = ""  # 译文，未翻译时为空


class TranslateEngine(ABC):
    """翻译引擎抽象基类。

    所有翻译实现（llm、llm_local、deepl 等）必须实现此接口。
    """

    @abstractmethod
    def translate(
        self,
        segments: list[TranslateSegment],
        target_language: str,
        source_language: str = "",
    ) -> list[TranslateSegment]:
        """将字幕片段翻译为目标语言。

        Args:
            segments: 待翻译的片段列表（含时间戳和原文）。
            target_language: 目标语言名称（如 "Chinese", "English"）。
            source_language: 源语言名称（空字符串表示自动）。

        Returns:
            翻译后的 TranslateSegment 列表，时间戳保持不变。
        """
        ...

    @abstractmethod
    def translate_stream(
        self,
        segments: list[TranslateSegment],
        target_language: str,
        source_language: str = "",
    ) -> Iterator[list[TranslateSegment]]:
        """流式翻译生成器 —— 逐批 yield 翻译片段。

        供 SSE 层实时推送。每批翻译完成后立即 yield，
        前端可以逐批展示结果。

        Args:
            segments: 待翻译的片段列表。
            target_language: 目标语言名称。
            source_language: 源语言名称（空=自动）。

        Yields:
            每批翻译完成的 TranslateSegment 列表。
        """
        ...


# ---------------------------------------------------------------------------
# ISO 639-3 代码 → LLM prompt 用的语言名称
# ---------------------------------------------------------------------------

_LANG_CODE_TO_NAME: dict[str, str] = {
    "zho": "Chinese",
    "chi": "Chinese",
    "eng": "English",
    "jpn": "Japanese",
    "kor": "Korean",
    "fre": "French",
    "fra": "French",
    "ger": "German",
    "deu": "German",
    "spa": "Spanish",
    "por": "Portuguese",
    "rus": "Russian",
    "ara": "Arabic",
    "ita": "Italian",
    "tha": "Thai",
    "vie": "Vietnamese",
    "hin": "Hindi",
    "ind": "Indonesian",
    "msa": "Malay",
    "tur": "Turkish",
    "pol": "Polish",
    "nld": "Dutch",
    "swe": "Swedish",
    "nor": "Norwegian",
    "dan": "Danish",
    "fin": "Finnish",
    "ukr": "Ukrainian",
}


def lang_code_to_name(code: str) -> str:
    """ISO 639-3 代码 → 语言名称（用于 LLM prompt）。

    Args:
        code: ISO 639-3 代码（如 "zho", "eng"）。

    Returns:
        语言名称（如 "Chinese", "English"），未知代码返回原文。
    """
    return _LANG_CODE_TO_NAME.get(code, code)


# ---------------------------------------------------------------------------
# SRT ↔ TranslateSegment 转换
# ---------------------------------------------------------------------------


def srt_to_segments(srt_text: str) -> list[TranslateSegment]:
    """将 SRT 文本解析为 TranslateSegment 列表。

    Args:
        srt_text: SRT 格式文本。

    Returns:
        TranslateSegment 列表，按时间顺序排列。

    Raises:
        ValueError: SRT 格式无效。
    """
    import pysrt
    from pathlib import Path
    import tempfile
    import os

    if not srt_text.strip():
        return []

    # pysrt 只能从文件读取，写入临时文件
    # 在 Windows 上使用 delete=False 避免权限问题
    tmp = None
    try:
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".srt", encoding="utf-8", delete=False,
        )
        tmp.write(srt_text)
        tmp.close()

        subs = pysrt.open(tmp.name, encoding="utf-8")
        segments = []
        for sub in subs:
            start_sec = (
                sub.start.hours * 3600
                + sub.start.minutes * 60
                + sub.start.seconds
                + sub.start.milliseconds / 1000
            )
            end_sec = (
                sub.end.hours * 3600
                + sub.end.minutes * 60
                + sub.end.seconds
                + sub.end.milliseconds / 1000
            )
            segments.append(TranslateSegment(
                start=round(start_sec, 3),
                end=round(end_sec, 3),
                source_text=sub.text.replace("\n", " ").strip(),
            ))
        return segments
    except Exception as e:
        raise ValueError(f"SRT 解析失败: {e}") from e
    finally:
        if tmp:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass


def translated_segments_to_srt(segments: list[TranslateSegment]) -> str:
    """将翻译后的片段列表转换为 SRT 格式文本。

    取 translated_text 字段；若为空则回退到 source_text。

    Args:
        segments: TranslateSegment 列表（translated_text 已填充）。

    Returns:
        SRT 格式字符串。
    """
    lines: list[str] = []
    for i, seg in enumerate(segments, 1):
        start_ts = _seconds_to_srt_time(seg.start)
        end_ts = _seconds_to_srt_time(seg.end)
        text = seg.translated_text or seg.source_text
        lines.append(str(i))
        lines.append(f"{start_ts} --> {end_ts}")
        lines.append(text.strip())
        lines.append("")  # 空行分隔
    return "\n".join(lines)


def _seconds_to_srt_time(seconds: float) -> str:
    """秒数 → SRT 时间戳格式 HH:MM:SS,mmm。"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
