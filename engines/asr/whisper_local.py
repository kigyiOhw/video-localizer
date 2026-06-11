"""faster-whisper 本地 ASR 实现。

通过 CTranslate2 运行 Whisper 模型，支持 CPU/GPU 推理和 VAD 过滤。
"""

from __future__ import annotations

import logging
import os
import site
import sys
from pathlib import Path

logger = logging.getLogger("video_localizer.asr.whisper")

# 注册 NVIDIA CUDA 库路径（pip 安装的 nvidia-cublas-cu12 等包）
# 注意：os.add_dll_directory() 返回的 cookie 必须保存，否则被 GC 后路径自动移除
_NVIDIA_DLL_COOKIES: list = []
_NVIDIA_DLL_DIRS: list[str] = []

if sys.platform == "win32":
    # 收集所有可能包含 nvidia 包的 site-packages 目录
    _search_dirs: set[str] = set()
    for _p in sys.path:
        _search_dirs.add(str(Path(_p).resolve()))
    try:
        for _sp in site.getsitepackages():
            _search_dirs.add(str(Path(_sp).resolve()))
    except Exception:
        pass

    for _d in sorted(_search_dirs):
        _nvidia_base = Path(_d) / "nvidia"
        if not _nvidia_base.is_dir():
            continue
        for _sub in sorted(_nvidia_base.iterdir()):
            _bin = _sub / "bin"
            if not _bin.is_dir():
                continue
            try:
                _cookie = os.add_dll_directory(str(_bin))
                _NVIDIA_DLL_COOKIES.append(_cookie)
                _NVIDIA_DLL_DIRS.append(str(_bin))
                logger.info("已注册 NVIDIA DLL 目录: %s", _bin)
            except OSError as e:
                logger.warning("注册 DLL 目录失败 %s: %s", _bin, e)

    # 显式预加载 CUDA DLL，避免线程池中惰性加载失败
    if _NVIDIA_DLL_DIRS:
        import ctypes

        _DLLS_TO_PRELOAD = [
            "cublas64_12.dll",
            "cublasLt64_12.dll",
        ]
        for _dll_name in _DLLS_TO_PRELOAD:
            for _d in _NVIDIA_DLL_DIRS:
                _dll_path = Path(_d) / _dll_name
                if _dll_path.is_file():
                    try:
                        ctypes.CDLL(str(_dll_path))
                        logger.info("已预加载: %s", _dll_name)
                        break
                    except OSError as e:
                        logger.warning("预加载 %s 失败: %s", _dll_name, e)

    if _NVIDIA_DLL_DIRS:
        logger.info("NVIDIA DLL 注册完成: %d 个目录", len(_NVIDIA_DLL_DIRS))
    else:
        logger.warning("未找到任何 NVIDIA DLL 目录！CUDA 推理可能失败。")

from engines.asr.engine import ASREngine, ASRSegment


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
