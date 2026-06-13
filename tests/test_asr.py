"""ASR 模块测试。"""

from __future__ import annotations

import asyncio
import random
from pathlib import Path
from unittest import mock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from engines.asr.engine import ASRSegment, _seconds_to_srt_time, segments_to_srt
from web.api.asr import _get_engine


def make_segments(n: int = 5) -> list[ASRSegment]:
    """创建测试用转写片段。"""
    return [
        ASRSegment(
            start=i * 2.0,
            end=i * 2.0 + 1.8,
            text=f"测试文本 {i + 1}",
            confidence=random.uniform(0.8, 0.99),
        )
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# TestSegmentsToSRT
# ---------------------------------------------------------------------------


class TestSegmentsToSRT:
    """SRT 格式转换测试。"""

    def test_basic(self) -> None:
        """基本转换。"""
        segs = [
            ASRSegment(0.0, 2.5, "你好世界", 0.95),
            ASRSegment(3.0, 5.5, "第二句", 0.90),
        ]
        srt = segments_to_srt(segs)
        lines = srt.split("\n")
        assert lines[0] == "1"
        assert "00:00:00,000 --> 00:00:02,500" in lines[1]
        assert lines[2] == "你好世界"
        assert lines[3] == ""
        assert lines[4] == "2"
        assert lines[5] == "00:00:03,000 --> 00:00:05,500"
        assert lines[6] == "第二句"

    def test_empty(self) -> None:
        """空列表返回空字符串。"""
        assert segments_to_srt([]) == ""

    def test_single(self) -> None:
        """单个片段。"""
        segs = [ASRSegment(0.0, 1.0, "一句", 1.0)]
        srt = segments_to_srt(segs)
        assert srt.count("\n") == 3  # 1, timestamp, text, empty
        assert "一句" in srt

    def test_time_format(self) -> None:
        """SRT 时间戳格式正确。"""
        # 1h 2m 3.456s
        ts = _seconds_to_srt_time(3723.456)
        assert ts == "01:02:03,456"

    def test_time_format_ms(self) -> None:
        """毫秒部分正确。"""
        ts = _seconds_to_srt_time(0.001)
        assert ts == "00:00:00,001"


# ---------------------------------------------------------------------------
# TestWhisperLocalEngine
# ---------------------------------------------------------------------------


class FakeSegment:
    """Mock faster-whisper segment。"""
    def __init__(self, start, end, text, avg_logprob=-0.5):
        self.start = start
        self.end = end
        self.text = text
        self.avg_logprob = avg_logprob


class FakeInfo:
    """Mock faster-whisper info。"""
    language = "ja"
    language_probability = 0.98


class TestWhisperLocalEngine:
    """WhisperLocalEngine 测试（mock faster-whisper 模型）。"""

    @pytest.fixture
    def engine(self):
        """创建使用 mock 模型的引擎。"""
        from engines.asr.whisper_local import WhisperLocalEngine

        eng = WhisperLocalEngine(model_size="tiny", device="cpu", compute_type="int8")
        mock_model = mock.Mock()
        segs = [
            FakeSegment(0.0, 2.0, "こんにちは"),
            FakeSegment(2.5, 5.0, "元気ですか"),
        ]
        mock_model.transcribe.return_value = (iter(segs), FakeInfo())
        eng._model = mock_model
        return eng

    def test_transcribe(self, engine) -> None:
        """转写返回 ASRSegment 列表。"""
        segments = engine.transcribe(Path("/fake/audio.wav"))
        assert len(segments) == 2
        assert segments[0].text == "こんにちは"
        assert segments[0].start == 0.0

    def test_detect_language(self, engine) -> None:
        """检测语言。"""
        lang = engine.detect_language(Path("/fake/audio.wav"))
        assert lang == "ja"

    def test_detect_language_low_confidence(self, engine) -> None:
        """低置信度语言检测 → None。"""
        engine._model.transcribe.return_value = (
            iter([]),
            mock.Mock(language="xx", language_probability=0.1),
        )
        lang = engine.detect_language(Path("/fake/audio.wav"))
        assert lang is None

    def test_lazy_model_loading(self) -> None:
        """模型延迟加载：创建引擎时不加载模型。"""
        from engines.asr.whisper_local import WhisperLocalEngine

        eng = WhisperLocalEngine()
        assert eng._model is None

    def test_transcribe_filters_empty(self, engine) -> None:
        """空文本片段被过滤。"""
        segs = [
            FakeSegment(0.0, 2.0, "hello"),
            FakeSegment(2.5, 5.0, ""),   # 空，应过滤
            FakeSegment(5.0, 7.0, "   "),  # 空白，应过滤
            FakeSegment(7.0, 9.0, "world"),
        ]
        engine._model.transcribe.return_value = (iter(segs), FakeInfo())
        segments = engine.transcribe(Path("/fake/audio.wav"))
        assert len(segments) == 2
        assert segments[0].text == "hello"
        assert segments[1].text == "world"


from web.api.asr import _get_engine


# ---------------------------------------------------------------------------
# TestGetEngine
# ---------------------------------------------------------------------------


class TestGetEngine:
    """ASR 引擎分发测试。"""

    def teardown_method(self):
        """每次测试后重置单例，避免影响其他测试。"""
        import web.api.asr as asr_module
        asr_module._engine = None

    def test_whisper_local_engine(self) -> None:
        """engine=whisper_local 时实例化本地引擎。"""
        settings_mock = mock.Mock()
        settings_mock.asr.engine = "whisper_local"
        settings_mock.asr.model_size = "tiny"
        settings_mock.asr.device = "cpu"
        settings_mock.asr.compute_type = "int8"
        settings_mock.asr.beam_size = 5
        settings_mock.asr.vad_filter = True

        with mock.patch("web.api.asr._get_settings", return_value=settings_mock):
            engine = _get_engine()
            from engines.asr.whisper_local import WhisperLocalEngine
            assert isinstance(engine, WhisperLocalEngine)

    def test_whisper_api_not_implemented(self) -> None:
        """engine=whisper_api 时报错。"""
        settings_mock = mock.Mock()
        settings_mock.asr.engine = "whisper_api"

        with mock.patch("web.api.asr._get_settings", return_value=settings_mock):
            with pytest.raises(ValueError, match="尚未实现"):
                _get_engine()

    def test_none_engine_disabled(self) -> None:
        """engine=none 时报错。"""
        settings_mock = mock.Mock()
        settings_mock.asr.engine = "none"

        with mock.patch("web.api.asr._get_settings", return_value=settings_mock):
            with pytest.raises(ValueError, match="已禁用"):
                _get_engine()

    def test_unknown_engine(self) -> None:
        """未知 engine 时报错。"""
        settings_mock = mock.Mock()
        settings_mock.asr.engine = "magic_asr"

        with mock.patch("web.api.asr._get_settings", return_value=settings_mock):
            with pytest.raises(ValueError, match="未知"):
                _get_engine()


# ---------------------------------------------------------------------------
# TestRunASRDetectedLanguage
# ---------------------------------------------------------------------------


class TestRunASRDetectedLanguage:
    """_run_asr 检测语言相关测试。"""

    def teardown_method(self):
        """每次测试后重置单例。"""
        import web.api.asr as asr_module
        asr_module._engine = None

    def test_auto_language_uses_detected(self) -> None:
        """language=auto 时使用引擎检测到的语言。"""
        fake_engine = mock.Mock()
        fake_engine.transcribe.return_value = [
            ASRSegment(0.0, 1.0, "hello", 0.9),
        ]
        fake_engine.detect_language.return_value = "en"

        import web.api.asr as asr_module
        asr_module._engine = fake_engine

        settings_mock = mock.Mock()
        settings_mock.ffmpeg.ffprobe_executable = "ffprobe"
        settings_mock.paths.temp_dir = Path("/tmp")

        probe_mock = mock.Mock()
        probe_mock.audio_streams = [mock.Mock(duration=10.0)]

        with mock.patch("web.api.asr._get_settings", return_value=settings_mock):
            with mock.patch("processing.core.probe.probe_file", return_value=probe_mock):
                with mock.patch("web.api.asr._extract_audio", return_value=Path("/tmp/audio.wav")):
                    from web.api.asr import _run_asr
                    request = mock.Mock()
                    result = asyncio.run(_run_asr(request, Path("/tmp/test.mp4"), None))

        assert result["language"] == "en"
        fake_engine.detect_language.assert_called_once_with(Path("/tmp/audio.wav"))

    def test_specified_language_skip_detection(self) -> None:
        """指定语言时不调用 detect_language。"""
        fake_engine = mock.Mock()
        fake_engine.transcribe.return_value = [
            ASRSegment(0.0, 1.0, "hello", 0.9),
        ]

        import web.api.asr as asr_module
        asr_module._engine = fake_engine

        settings_mock = mock.Mock()
        settings_mock.ffmpeg.ffprobe_executable = "ffprobe"
        settings_mock.paths.temp_dir = Path("/tmp")

        probe_mock = mock.Mock()
        probe_mock.audio_streams = [mock.Mock(duration=10.0)]

        with mock.patch("web.api.asr._get_settings", return_value=settings_mock):
            with mock.patch("processing.core.probe.probe_file", return_value=probe_mock):
                with mock.patch("web.api.asr._extract_audio", return_value=Path("/tmp/audio.wav")):
                    from web.api.asr import _run_asr
                    request = mock.Mock()
                    result = asyncio.run(_run_asr(request, Path("/tmp/test.mp4"), "ja"))

        assert result["language"] == "ja"
        fake_engine.detect_language.assert_not_called()


# ---------------------------------------------------------------------------
# TestAPIASR
# ---------------------------------------------------------------------------


@pytest.fixture
def test_app_asr() -> FastAPI:
    """创建独立的测试 FastAPI app。"""
    from fastapi.templating import Jinja2Templates

    app = FastAPI()
    template_dir = Path(__file__).resolve().parent.parent / "web" / "templates"
    templates = Jinja2Templates(directory=str(template_dir))

    from web.api import router as api_router
    app.include_router(api_router)

    @app.get("/asr", response_model=None)
    async def asr_page(request: Request):
        return templates.TemplateResponse(request, "asr.html", {"version": "test"})

    return app


@pytest.fixture
def client_asr(test_app_asr: FastAPI) -> TestClient:
    return TestClient(test_app_asr)


class TestAPIASR:
    """ASR API 端点测试。"""

    def test_get_asr_page(self, client_asr: TestClient) -> None:
        """GET /asr 返回 HTML。"""
        resp = client_asr.get("/asr")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_post_missing_file_path(self, client_asr: TestClient) -> None:
        """POST /api/asr/transcribe 无参数 → 400。"""
        resp = client_asr.post("/api/asr/transcribe")
        assert resp.status_code == 400

    def test_post_success_json(self, client_asr: TestClient) -> None:
        """POST 成功返回 JSON。"""
        fake_audio = Path("/tmp/fake_audio.m4a")
        # 需要 mock 整个 _run_asr 流程
        with mock.patch("web.api.asr._run_asr") as mock_run:
            mock_result = {
                "language": "ja",
                "duration": 120.5,
                "segments": [
                    {"start": 0.0, "end": 2.0, "text": "こんにちは", "confidence": 0.95},
                ],
                "srt": "1\n00:00:00,000 --> 00:00:02,000\nこんにちは\n\n",
                "stats": {
                    "model": "medium", "device": "cpu", "compute_type": "int8",
                    "elapsed_seconds": 10.0, "segment_count": 1,
                    "audio_stream_index": 0,
                },
            }
            mock_run.return_value = mock_result

            resp = client_asr.post("/api/asr/transcribe", data={"file_path": "/tmp/test.mp4"})
            assert resp.status_code == 200
            data = resp.json()
            assert data["success"] is True
            assert data["language"] == "ja"

    def test_post_asr_error(self, client_asr: TestClient) -> None:
        """转写失败 → 422。"""
        with mock.patch("web.api.asr._run_asr", side_effect=ValueError("文件中没有音频流")):
            resp = client_asr.post("/api/asr/transcribe", data={"file_path": "/tmp/test.mp4"})
            assert resp.status_code == 422

    def test_post_htmx_returns_html(self, client_asr: TestClient) -> None:
        """HX-Request 头 → HTML fragment。"""
        with mock.patch("web.api.asr._run_asr") as mock_run:
            mock_run.return_value = {
                "language": "en",
                "duration": 10.0,
                "segments": [{"start": 0.0, "end": 1.0, "text": "test", "confidence": 0.9}],
                "srt": "1\n00:00:00,000 --> 00:00:01,000\ntest\n\n",
                "stats": {"model": "tiny", "device": "cpu", "compute_type": "int8",
                          "elapsed_seconds": 1.0, "segment_count": 1, "audio_stream_index": 0},
            }
            resp = client_asr.post(
                "/api/asr/transcribe",
                data={"file_path": "/tmp/test.mp4"},
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert "text/html" in resp.headers["content-type"]
            assert "转写完成" in resp.text

    def test_post_htmx_error_html(self, client_asr: TestClient) -> None:
        """HX-Request + 错误 → HTML 错误。"""
        with mock.patch("web.api.asr._run_asr", side_effect=ValueError("失败了")):
            resp = client_asr.post(
                "/api/asr/transcribe",
                data={"file_path": "/tmp/test.mp4"},
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert "text/html" in resp.headers["content-type"]
            assert "失败了" in resp.text

    def test_get_with_file_path(self, client_asr: TestClient) -> None:
        """GET /api/asr/transcribe?file_path= → JSON。"""
        with mock.patch("web.api.asr._run_asr") as mock_run:
            mock_run.return_value = {
                "language": "ja",
                "duration": 60.0,
                "segments": [],
                "srt": "",
                "stats": {"model": "medium", "device": "cpu",
                          "compute_type": "int8", "elapsed_seconds": 5.0,
                          "segment_count": 0, "audio_stream_index": 0},
            }
            resp = client_asr.get("/api/asr/transcribe", params={"file_path": "/tmp/test.mp4"})
            assert resp.status_code == 200
            assert resp.json()["success"] is True

    def test_get_missing_param(self, client_asr: TestClient) -> None:
        """GET 无参数 → 400。"""
        resp = client_asr.get("/api/asr/transcribe")
        assert resp.status_code == 400
