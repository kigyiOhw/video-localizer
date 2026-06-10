"""faster-whisper 本地 ASR 实现。

通过 CTranslate2 运行 Whisper 模型，支持 CPU/GPU 推理和 VAD 过滤。
"""

from __future__ import annotations

import logging
from pathlib import Path

from engines.asr.engine import ASREngine, ASRSegment

logger = logging.getLogger("video_localizer.asr.whisper")


class WhisperLocalEngine(ASREngine):
    """faster-whisper 本地实现。

    模型在首次请求时延迟加载，后续请求复用同一实例。
    """

    def __init__(
        self,
        model_size: str = "medium",
        device: str = "cpu",
        compute_type: str = "int8",
        beam_size: int = 5,
        vad_filter: bool = True,
    ):
        """初始化引擎（模型未加载，首次 transcribe 时才加载）。

        Args:
            model_size: 模型大小 (tiny/base/small/medium/large-v3 等)。
            device: 推理设备 (cpu/cuda)。
            compute_type: 计算精度 (int8/float16/int8_float16)。
            beam_size: beam search 大小。
            vad_filter: 是否启用 VAD 过滤（减少静音幻觉）。
        """
        self._model_size = model_size
        self._device = device
        self._compute_type = compute_type
        self._beam_size = beam_size
        self._vad_filter = vad_filter
        self._model = None  # 延迟加载

    def _get_model(self):
        """延迟加载模型（线程安全由 faster-whisper 内部处理）。"""
        if self._model is None:
            from faster_whisper import WhisperModel

            logger.info(
                "加载 Whisper 模型: %s (device=%s, compute=%s)",
                self._model_size, self._device, self._compute_type,
            )

            # CPU 模式下 int8_float16 不可用，回退到 int8
            compute = self._compute_type
            if self._device == "cpu" and compute in ("float16", "int8_float16"):
                compute = "int8"
                logger.info("CPU 模式回落 compute_type: %s → int8", self._compute_type)

            self._model = WhisperModel(
                self._model_size,
                device=self._device,
                compute_type=compute,
            )
            logger.info("模型加载完成")
        return self._model

    def transcribe(
        self,
        audio_path: Path,
        language: str | None = None,
    ) -> list[ASRSegment]:
        """转写音频为文本片段。

        Args:
            audio_path: 音频文件路径。
            language: ISO 639-1 语言代码，None 则自动检测。

        Returns:
            ASRSegment 列表。
        """
        model = self._get_model()

        logger.info(
            "开始转写: %s (语言=%s, beam=%d, vad=%s)",
            audio_path.name, language or "auto", self._beam_size, self._vad_filter,
        )

        seg_iter, info = model.transcribe(
            str(audio_path),
            language=language,
            beam_size=self._beam_size,
            vad_filter=self._vad_filter,
            vad_parameters=dict(
                min_silence_duration_ms=500,
                threshold=0.5,
            ) if self._vad_filter else None,
        )

        segments = [
            ASRSegment(
                start=round(seg.start, 3),
                end=round(seg.end, 3),
                text=seg.text.strip(),
                confidence=round(seg.avg_logprob, 3) if hasattr(seg, "avg_logprob") else 0.0,
            )
            for seg in seg_iter
            if seg.text.strip()
        ]

        logger.info(
            "转写完成: %d 个片段, 语言=%s (%.2f)",
            len(segments), info.language, info.language_probability,
        )

        return segments

    def detect_language(self, audio_path: Path) -> str | None:
        """检测音频中的主要语言。

        Args:
            audio_path: 音频文件路径。

        Returns:
            ISO 639-1 语言代码，或 None。
        """
        model = self._get_model()

        logger.info("检测语言: %s", audio_path.name)
        try:
            # 只跑前 30 秒来加速语言检测
            seg_iter, info = model.transcribe(
                str(audio_path),
                beam_size=1,
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=500, threshold=0.5),
            )
            # 消耗迭代器（需要的只是 info）
            for _ in seg_iter:
                pass
            lang = info.language if info.language_probability > 0.5 else None
            logger.info("检测结果: %s (概率=%.2f)", lang, info.language_probability)
            return lang
        except Exception:
            logger.warning("语言检测失败", exc_info=True)
            return None
