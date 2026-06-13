"""配置系统测试。"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest
import yaml

from config import (
    ASRConfig,
    FFmpegConfig,
    PathsConfig,
    Settings,
    SubtitleConfig,
    TTSConfig,
    TranslateConfig,
    _apply_env_overrides,
    _deep_merge,
)
from config.requirements import select_profile


# ---------------------------------------------------------------------------
# 数据类 from_dict 测试
# ---------------------------------------------------------------------------


class TestPathsConfig:
    def test_defaults(self) -> None:
        c = PathsConfig.from_dict({})
        assert c.model_root == Path("/models")

    def test_custom(self) -> None:
        c = PathsConfig.from_dict({"model_root": "/custom/models"})
        assert c.model_root == Path("/custom/models")


class TestASRConfig:
    def test_defaults(self) -> None:
        c = ASRConfig.from_dict({})
        assert c.model_size == "medium"
        assert c.device == "cpu"
        assert c.compute_type == "int8"
        assert c.beam_size == 5
        assert c.vad_filter is True
        assert c.gpu_worker_url == ""

    def test_custom(self) -> None:
        c = ASRConfig.from_dict({"model_size": "large-v3", "device": "cuda", "gpu_worker_url": "http://worker:9001"})
        assert c.model_size == "large-v3"
        assert c.device == "cuda"
        assert c.gpu_worker_url == "http://worker:9001"


class TestTTSConfig:
    def test_defaults(self) -> None:
        c = TTSConfig.from_dict({})
        assert c.engine == "edge_tts"


class TestTranslateConfig:
    def test_defaults(self) -> None:
        c = TranslateConfig.from_dict({})
        assert c.engine == "none"


class TestSubtitleConfig:
    def test_defaults(self) -> None:
        c = SubtitleConfig.from_dict({})
        assert c.default_language == "zho"
        assert c.default_format == "srt"


class TestFFmpegConfig:
    def test_defaults(self) -> None:
        c = FFmpegConfig.from_dict({})
        assert c.executable == "ffmpeg"


# ---------------------------------------------------------------------------
# Settings.load 测试
# ---------------------------------------------------------------------------


class TestSettingsLoad:
    def test_load_from_file(self, temp_settings_file: Path) -> None:
        s = Settings.load(temp_settings_file)
        assert s.asr.model_size == "medium"
        assert s.paths.model_root == Path("/tmp/models")

    def test_missing_file_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            Settings.load("/nonexistent/config.yaml")

    def test_local_override(self, temp_settings_file: Path, temp_local_override: Path) -> None:
        """验证 settings.local.yaml 覆盖生效。"""
        # 将 local_override 移动到主配置同目录并命名为 .local.yaml
        main_path = Path(temp_settings_file)
        local_path = main_path.with_name(main_path.stem + ".local" + main_path.suffix)
        Path(temp_local_override).rename(local_path)

        try:
            s = Settings.load(main_path)
            # local override 应覆盖 asr.model_size
            assert s.asr.model_size == "large-v3-turbo"
            assert s.asr.device == "cuda"
            # translate 被覆盖
            assert s.translate.engine == "llm"
            # tts 未被覆盖，保持原值
            assert s.tts.engine == "edge_tts"
        finally:
            if local_path.exists():
                local_path.unlink()


# ---------------------------------------------------------------------------
# 配置档应用测试
# ---------------------------------------------------------------------------


class TestApplyProfile:
    def test_apply_cpu_profile(self, temp_settings_file: Path) -> None:
        s = Settings.load(temp_settings_file)
        s.apply_profile("cpu")
        assert s.selected_profile == "cpu"
        assert s.asr.model_size == "tiny"
        assert s.asr.device == "cpu"

    def test_apply_gpu_high_profile(self, temp_settings_file: Path) -> None:
        s = Settings.load(temp_settings_file)
        s.apply_profile("gpu_high")
        assert s.selected_profile == "gpu_high"
        assert s.asr.model_size == "large-v3"
        assert s.asr.device == "cuda"
        assert s.asr.compute_type == "int8_float16"
        assert s.tts.engine == "edge_tts"
        assert s.translate.engine == "llm"

    def test_unknown_profile_noop(self, temp_settings_file: Path) -> None:
        s = Settings.load(temp_settings_file)
        original = s.asr.model_size
        s.apply_profile("nonexistent")
        assert s.asr.model_size == original  # 不变


# ---------------------------------------------------------------------------
# 深度合并测试
# ---------------------------------------------------------------------------


class TestDeepMerge:
    def test_scalar_override(self) -> None:
        assert _deep_merge({"a": 1}, {"a": 2}) == {"a": 2}

    def test_nested_merge(self) -> None:
        base = {"a": {"x": 1, "y": 2}, "b": 3}
        override = {"a": {"y": 99, "z": 100}}
        result = _deep_merge(base, override)
        assert result == {"a": {"x": 1, "y": 99, "z": 100}, "b": 3}

    def test_new_key(self) -> None:
        assert _deep_merge({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}

    def test_empty_override(self) -> None:
        assert _deep_merge({"a": 1}, {}) == {"a": 1}


# ---------------------------------------------------------------------------
# 环境变量覆盖测试
# ---------------------------------------------------------------------------


class TestEnvOverrides:
    """验证环境变量能覆盖 YAML 配置。"""

    def test_apply_env_overrides_paths(self) -> None:
        base = {
            "paths": {
                "media_input": "/tmp/in",
                "media_output": "/tmp/out",
                "temp_dir": "/tmp/temp",
                "hf_cache": "/tmp/hf",
            },
        }
        import os
        env = {
            "MEDIA_INPUT": "/media/input",
            "MEDIA_OUTPUT": "/media/output",
            "TEMP_DIR": "/media/temp",
            "HF_HOME": "/models/huggingface",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            result = _apply_env_overrides(base)

        assert result["paths"]["media_input"] == "/media/input"
        assert result["paths"]["media_output"] == "/media/output"
        assert result["paths"]["temp_dir"] == "/media/temp"
        assert result["paths"]["hf_cache"] == "/models/huggingface"

    def test_apply_env_overrides_gpu_worker_url(self) -> None:
        base = {"asr": {"engine": "whisper_local"}}
        import os
        with mock.patch.dict(os.environ, {"GPU_WORKER_URL": "http://host.docker.internal:9001"}, clear=False):
            result = _apply_env_overrides(base)
        assert result["asr"]["gpu_worker_url"] == "http://host.docker.internal:9001"

    def test_apply_env_overrides_no_env(self) -> None:
        """无相关环境变量时保持原配置。"""
        base = {"paths": {"media_input": "/tmp/in"}, "asr": {"engine": "whisper_local"}}
        result = _apply_env_overrides(base)
        assert result["paths"]["media_input"] == "/tmp/in"
        assert "gpu_worker_url" not in result.get("asr", {})


# ---------------------------------------------------------------------------
# select_profile 测试
# ---------------------------------------------------------------------------


class TestSelectProfile:
    def test_gpu_ultra(self) -> None:
        assert select_profile(24.0) == "gpu_ultra"
        assert select_profile(16.0) == "gpu_ultra"

    def test_gpu_high(self) -> None:
        assert select_profile(12.0) == "gpu_high"
        assert select_profile(8.0) == "gpu_high"

    def test_gpu_medium(self) -> None:
        assert select_profile(6.0) == "gpu_medium"
        assert select_profile(4.0) == "gpu_medium"

    def test_gpu_low(self) -> None:
        assert select_profile(3.0) == "gpu_low"
        assert select_profile(2.0) == "gpu_low"

    def test_cpu(self) -> None:
        assert select_profile(1.0) == "cpu"
        assert select_profile(0.0) == "cpu"
