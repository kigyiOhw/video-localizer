"""ASR 引擎抽象基类。

所有语音识别实现必须继承此基类（策略模式）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


@dataclass
class ASRSegment:
    """单个转写片段。"""

    start: float       # 起始秒数
    end: float         # 结束秒数
    text: str          # 转写文本
    confidence: float  # 置信度 0-1


class ASREngine(ABC):
    """ASR 引擎抽象基类。

    所有 ASR 实现（whisper_local、whisper_api 等）必须实现此接口。
    """

    @abstractmethod
    def transcribe(
        self,
        audio_path: Path,
        language: str | None = None,
    ) -> list[ASRSegment]:
        """将音频文件转写为文本片段。

        Args:
            audio_path: 音频文件路径（WAV/M4A/MP3 等）。
            language: 源语言代码（ISO 639-1，如 "ja", "en"）。
                      None 表示自动检测。

        Returns:
            ASRSegment 列表，按时间顺序排列。
        """
        ...

    @abstractmethod
    def detect_language(self, audio_path: Path) -> str | None:
        """检测音频中的语言。

        Args:
            audio_path: 音频文件路径。

        Returns:
            ISO 639-1 语言代码，或 None（无法检测时）。
        """
        ...

    def transcribe_stream(
        self,
        audio_path: Path,
        language: str | None = None,
    ) -> Iterator[ASRSegment]:
        """流式转写音频，逐片段 yield。

        默认实现调用 transcribe() 后逐个 yield；支持流式的实现可覆盖本方法。

        Args:
            audio_path: 音频文件路径。
            language: 源语言代码，None 表示自动检测。

        Yields:
            ASRSegment 片段。
        """
        for seg in self.transcribe(audio_path, language=language):
            yield seg


def segments_to_srt(segments: list[ASRSegment]) -> str:
    """将转写片段列表转换为 SRT 格式文本。

    Args:
        segments: ASRSegment 列表。

    Returns:
        SRT 格式字符串。
    """
    lines: list[str] = []
    for i, seg in enumerate(segments, 1):
        start_ts = _seconds_to_srt_time(seg.start)
        end_ts = _seconds_to_srt_time(seg.end)
        lines.append(str(i))
        lines.append(f"{start_ts} --> {end_ts}")
        lines.append(seg.text.strip())
        lines.append("")  # 空行分隔
    return "\n".join(lines)


def _seconds_to_srt_time(seconds: float) -> str:
    """秒数 → SRT 时间戳格式 HH:MM:SS,mmm。"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
