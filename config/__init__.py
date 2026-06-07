"""配置系统：Settings 数据类层次 + YAML 加载 + 深度合并。"""

from __future__ import annotations

import logging
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("video_localizer.config")


# ---------------------------------------------------------------------------
# 独立配置段 dataclass
# ---------------------------------------------------------------------------


@dataclass
class PathsConfig:
    """路径配置。所有路径均为容器内路径（Docker）或宿主机绝对路径（直接运行）。"""

    model_root: Path = Path("/models")
    hf_cache: Path = Path("/models/huggingface")
    media_input: Path = Path("/media/input")
    media_output: Path = Path("/media/output")
    temp_dir: Path = Path("/media/temp")

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PathsConfig":
        return cls(
            model_root=Path(d.get("model_root", "/models")),
            hf_cache=Path(d.get("hf_cache", "/models/huggingface")),
            media_input=Path(d.get("media_input", "/media/input")),
            media_output=Path(d.get("media_output", "/media/output")),
            temp_dir=Path(d.get("temp_dir", "/media/temp")),
        )


@dataclass
class ASRConfig:
    """语音识别配置。"""

    engine: str = "whisper_local"
    model_size: str = "medium"
    device: str = "cpu"
    compute_type: str = "int8"
    beam_size: int = 5
    vad_filter: bool = True
    language: str = "auto"

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ASRConfig":
        return cls(
            engine=d.get("engine", "whisper_local"),
            model_size=d.get("model_size", "medium"),
            device=d.get("device", "cpu"),
            compute_type=d.get("compute_type", "int8"),
            beam_size=int(d.get("beam_size", 5)),
            vad_filter=bool(d.get("vad_filter", True)),
            language=d.get("language", "auto"),
        )


@dataclass
class TTSConfig:
    """语音合成配置。"""

    engine: str = "edge_tts"

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TTSConfig":
        return cls(engine=d.get("engine", "edge_tts"))


@dataclass
class TranslateConfig:
    """翻译配置。"""

    engine: str = "none"

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TranslateConfig":
        return cls(engine=d.get("engine", "none"))


@dataclass
class SubtitleConfig:
    """字幕配置。"""

    default_language: str = "zho"
    default_format: str = "srt"

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SubtitleConfig":
        return cls(
            default_language=d.get("default_language", "zho"),
            default_format=d.get("default_format", "srt"),
        )


@dataclass
class FFmpegConfig:
    """FFmpeg 配置。"""

    executable: str = "ffmpeg"
    ffprobe_executable: str = "ffprobe"

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "FFmpegConfig":
        return cls(
            executable=d.get("executable", "ffmpeg"),
            ffprobe_executable=d.get("ffprobe_executable", "ffprobe"),
        )


@dataclass
class RequirementsConfig:
    """最低运行要求配置。"""

    min_ram_gb: float = 4.0
    min_disk_free_gb: float = 10.0
    min_python: str = "3.13"
    required_tools: list[str] = field(default_factory=lambda: ["ffmpeg", "ffprobe"])

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RequirementsConfig":
        return cls(
            min_ram_gb=float(d.get("min_ram_gb", 4.0)),
            min_disk_free_gb=float(d.get("min_disk_free_gb", 10.0)),
            min_python=d.get("min_python", "3.13"),
            required_tools=d.get("required_tools", ["ffmpeg", "ffprobe"]),
        )


@dataclass
class FallbackConfig:
    """引擎回退链配置。"""

    asr: list[str] = field(default_factory=lambda: ["whisper_local", "whisper_api", "none"])
    tts: list[str] = field(default_factory=lambda: ["xtts", "edge_tts", "none"])
    translate: list[str] = field(default_factory=lambda: ["llm_local", "llm", "none"])

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "FallbackConfig":
        return cls(
            asr=d.get("asr", ["whisper_local", "whisper_api", "none"]),
            tts=d.get("tts", ["xtts", "edge_tts", "none"]),
            translate=d.get("translate", ["llm_local", "llm", "none"]),
        )


# ---------------------------------------------------------------------------
# 顶层 Settings
# ---------------------------------------------------------------------------


@dataclass
class Settings:
    """全局配置，聚合所有配置段。"""

    paths: PathsConfig = field(default_factory=PathsConfig)
    ffmpeg: FFmpegConfig = field(default_factory=FFmpegConfig)
    subtitle: SubtitleConfig = field(default_factory=SubtitleConfig)
    asr: ASRConfig = field(default_factory=ASRConfig)
    tts: TTSConfig = field(default_factory=TTSConfig)
    translate: TranslateConfig = field(default_factory=TranslateConfig)
    fallback: FallbackConfig = field(default_factory=FallbackConfig)
    requirements: RequirementsConfig = field(default_factory=RequirementsConfig)
    profiles: dict[str, dict[str, Any]] = field(default_factory=dict)
    selected_profile: str = "cpu"

    # ------------------------------------------------------------------
    # 工厂方法
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, config_path: str | Path = "config/settings.yaml") -> "Settings":
        """从 YAML 加载配置，可选叠加 settings.local.yaml 深度合并。

        Args:
            config_path: 主配置文件路径。

        Returns:
            构建好的 Settings 实例。

        Raises:
            FileNotFoundError: 主配置文件缺失。
            yaml.YAMLError: YAML 解析错误。
        """
        main_path = Path(config_path)
        if not main_path.exists():
            raise FileNotFoundError(f"主配置文件缺失: {main_path}")

        logger.info("加载主配置: %s", main_path)
        with open(main_path, "r", encoding="utf-8") as f:
            merged: dict[str, Any] = yaml.safe_load(f) or {}

        # 深度合并 local.yaml
        local_path = main_path.with_name(main_path.stem + ".local" + main_path.suffix)
        if local_path.exists():
            logger.info("合并本地覆盖: %s", local_path)
            with open(local_path, "r", encoding="utf-8") as f:
                local_data = yaml.safe_load(f) or {}
            merged = _deep_merge(merged, local_data)

        return cls._from_merged_dict(merged)

    @classmethod
    def _from_merged_dict(cls, d: dict[str, Any]) -> "Settings":
        """从合并后的字典构建 Settings。"""
        return cls(
            paths=PathsConfig.from_dict(d.get("paths", {})),
            ffmpeg=FFmpegConfig.from_dict(d.get("ffmpeg", {})),
            subtitle=SubtitleConfig.from_dict(d.get("subtitle", {})),
            asr=ASRConfig.from_dict(d.get("asr", {})),
            tts=TTSConfig.from_dict(d.get("tts", {})),
            translate=TranslateConfig.from_dict(d.get("translate", {})),
            fallback=FallbackConfig.from_dict(d.get("fallback", {})),
            requirements=RequirementsConfig.from_dict(d.get("requirements", {})),
            profiles=deepcopy(d.get("profiles", {})),
        )

    # ------------------------------------------------------------------
    # 配置档应用
    # ------------------------------------------------------------------

    def apply_profile(self, profile_name: str) -> None:
        """用指定配置档覆盖 asr / tts / translate 配置。

        Args:
            profile_name: 配置档名（gpu_ultra / gpu_high / ... / cpu）。
        """
        profile = self.profiles.get(profile_name)
        if profile is None:
            logger.warning("未知配置档 '%s'，保持当前配置", profile_name)
            return

        logger.info("应用硬件配置档: %s", profile_name)
        self.selected_profile = profile_name

        if "asr" in profile:
            self.asr = ASRConfig.from_dict({**asdict(self.asr), **profile["asr"]})
            logger.debug("  ASR: model=%s device=%s compute=%s",
                         self.asr.model_size, self.asr.device, self.asr.compute_type)
        if "tts" in profile:
            self.tts = TTSConfig.from_dict({**asdict(self.tts), **profile["tts"]})
            logger.debug("  TTS: engine=%s", self.tts.engine)
        if "translate" in profile:
            self.translate = TranslateConfig.from_dict({**asdict(self.translate), **profile["translate"]})
            logger.debug("  Translate: engine=%s", self.translate.engine)

    # ------------------------------------------------------------------
    # 目录初始化
    # ------------------------------------------------------------------

    def ensure_dirs(self) -> None:
        """创建配置中所有需要的目录（如不存在）。

        对只读文件系统（如 Docker 挂载的 :ro 卷）仅记录警告，不中断启动。
        """
        dirs: list[Path] = [
            self.paths.media_input,
            self.paths.media_output,
            self.paths.temp_dir,
        ]
        for d in dirs:
            try:
                d.mkdir(parents=True, exist_ok=True)
                logger.debug("确保目录存在: %s", d)
            except OSError as e:
                if d.exists():
                    logger.debug("目录已存在（可能为挂载卷）: %s", d)
                else:
                    logger.warning("无法创建目录 %s: %s", d, e)

    # ------------------------------------------------------------------
    # 序列化（用于 /api/health 输出，隐藏敏感字段）
    # ------------------------------------------------------------------

    def to_safe_dict(self, version: str = "0.1.0") -> dict[str, Any]:
        """导出为字典（不含路径和密钥等敏感信息）。"""
        return {
            "version": version,
            "selected_profile": self.selected_profile,
            "asr": {
                "engine": self.asr.engine,
                "model_size": self.asr.model_size,
                "device": self.asr.device,
                "compute_type": self.asr.compute_type,
            },
            "tts": {"engine": self.tts.engine},
            "translate": {"engine": self.translate.engine},
            "subtitle": {
                "default_language": self.subtitle.default_language,
                "default_format": self.subtitle.default_format,
            },
        }


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """深度合并两个字典。override 中的值覆盖 base 中的同名键。

    对于嵌套字典，递归合并；对于列表和标量，直接覆盖。
    """
    result = deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result
