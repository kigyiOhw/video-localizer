"""pytest fixtures。"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest
import yaml


@pytest.fixture
def sample_settings_yaml() -> dict[str, Any]:
    """返回一份最小有效的 settings.yaml 字典。"""
    return {
        "paths": {
            "model_root": "/tmp/models",
            "hf_cache": "/tmp/hf",
            "media_input": "/tmp/media/in",
            "media_output": "/tmp/media/out",
            "temp_dir": "/tmp/media/temp",
        },
        "ffmpeg": {"executable": "ffmpeg"},
        "subtitle": {"default_language": "eng", "default_format": "srt"},
        "asr": {
            "engine": "whisper_local",
            "model_size": "medium",
            "device": "cpu",
            "compute_type": "int8",
            "beam_size": 5,
            "vad_filter": True,
            "language": "auto",
        },
        "tts": {"engine": "edge_tts"},
        "translate": {"engine": "none"},
        "profiles": {
            "cpu": {
                "asr": {"model_size": "tiny", "device": "cpu", "compute_type": "int8"},
                "tts": {"engine": "edge_tts"},
                "translate": {"engine": "none"},
            },
            "gpu_high": {
                "asr": {"model_size": "large-v3", "device": "cuda", "compute_type": "int8_float16"},
                "tts": {"engine": "edge_tts"},
                "translate": {"engine": "llm"},
            },
        },
        "fallback": {
            "asr": ["whisper_local", "whisper_api", "none"],
            "tts": ["xtts", "edge_tts", "none"],
            "translate": ["llm_local", "llm", "none"],
        },
    }


@pytest.fixture
def temp_settings_file(sample_settings_yaml: dict[str, Any]) -> Path:
    """将 sample_settings_yaml 写入临时文件，返回路径。"""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, encoding="utf-8"
    ) as f:
        yaml.safe_dump(sample_settings_yaml, f)
        return Path(f.name)


@pytest.fixture
def temp_local_override() -> Path:
    """写入一份 settings.local.yaml 覆盖文件，返回路径。"""
    override = {
        "asr": {"model_size": "large-v3-turbo", "device": "cuda"},
        "translate": {"engine": "llm"},
    }
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, encoding="utf-8"
    ) as f:
        yaml.safe_dump(override, f)
        return Path(f.name)
