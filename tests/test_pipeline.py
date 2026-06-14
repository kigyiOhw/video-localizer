"""端到端流水线测试。"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from engines.asr.engine import ASREngine, ASRSegment
from engines.translate.engine import TranslateEngine, TranslateSegment
from processing.pipeline.full_pipeline import (
    PipelineError,
    PipelineResult,
    _asr_to_translate_segments,
    _dicts_to_translate_segments,
    _lang_name_to_code,
    _source_language_for_translation,
    run_full_pipeline,
    run_full_pipeline_stream,
)

FAKE_VIDEO = Path("/tmp/video.mp4")


# ---------------------------------------------------------------------------
# 测试数据构造
# ---------------------------------------------------------------------------


def _make_audio_stream(index=0, codec="aac", duration=7.0):
    from processing.core.probe import AudioStream
    return AudioStream(
        index=index, codec=codec,
        codec_long="AAC (Advanced Audio Coding)",
        codec_type="audio", language=None,
        channels=2, sample_rate=48000, duration=duration,
    )


def _make_probe_result(audio_streams=None):
    from processing.core.probe import FormatInfo, ProbeResult
    return ProbeResult(
        format=FormatInfo(filename="video.mp4", format_name="mp4", duration=7.0),
        video_streams=[], subtitle_streams=[],
        audio_streams=audio_streams if audio_streams is not None else [_make_audio_stream()],
    )


def _make_extract_result():
    from processing.core.extract import ExtractResult
    return ExtractResult(
        stream_index=0, stream_type="audio", codec="aac",
        output_path=Path("/tmp/test_audio.m4a"), output_size=1024, duration=7.0,
    )


def _make_mux_result():
    from processing.core.mux import MuxResult
    return MuxResult(
        input_video=FAKE_VIDEO,
        output_path=Path("/tmp/output/video_subtitled.mkv"),
        output_size=2048, subtitle_count=1, added_track_index=0, language="zho",
    )


@contextmanager
def _mock_pipeline_deps(probe_result=None, extract_result=None, mux_result=None):
    """一次 mock 所有管线依赖：Path 存在性 + probe + extract + mux。"""
    with (
        mock.patch.object(Path, "exists", return_value=True),
        mock.patch.object(Path, "is_file", return_value=True),
        mock.patch.object(Path, "mkdir", return_value=None),
        mock.patch.object(Path, "write_text", return_value=None),
        mock.patch.object(Path, "unlink", return_value=None),
        mock.patch("processing.core.probe.probe_file",
                   return_value=probe_result or _make_probe_result()),
        mock.patch("processing.core.extract.extract_stream",
                   return_value=extract_result or _make_extract_result()),
        mock.patch("processing.core.mux.add_subtitle",
                   return_value=mux_result or _make_mux_result()),
    ):
        yield


@contextmanager
def _mock_pipeline_deps_no_audio():
    """Mock 管线依赖但 probe 返回无音轨。"""
    with (
        mock.patch.object(Path, "exists", return_value=True),
        mock.patch.object(Path, "is_file", return_value=True),
        mock.patch.object(Path, "mkdir", return_value=None),
        mock.patch("processing.core.probe.probe_file",
                   return_value=_make_probe_result(audio_streams=[])),
    ):
        yield


# ---------------------------------------------------------------------------
# 引擎 fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_asr_engine():
    engine = mock.Mock(spec=ASREngine)
    segments = [
        ASRSegment(0.0, 2.0, "Hello world", 0.95),
        ASRSegment(2.0, 4.5, "How are you", 0.92),
        ASRSegment(4.5, 7.0, "I am fine", 0.88),
    ]
    engine.transcribe.return_value = segments
    engine.transcribe_stream.return_value = iter(segments)
    engine.detect_language.return_value = "en"
    return engine


@pytest.fixture
def mock_translate_engine():
    engine = mock.Mock(spec=TranslateEngine)
    engine.translate.return_value = [
        TranslateSegment(0.0, 2.0, "Hello world", "你好世界"),
        TranslateSegment(2.0, 4.5, "How are you", "你好吗"),
        TranslateSegment(4.5, 7.0, "I am fine", "我很好"),
    ]
    engine.translate_stream.return_value = iter([
        [
            TranslateSegment(0.0, 2.0, "Hello world", "你好世界"),
            TranslateSegment(2.0, 4.5, "How are you", "你好吗"),
        ],
        [TranslateSegment(4.5, 7.0, "I am fine", "我很好")],
    ])
    return engine


# ---------------------------------------------------------------------------
# TestFullPipeline
# ---------------------------------------------------------------------------


class TestFullPipeline:

    def test_basic_flow(self, mock_asr_engine, mock_translate_engine):
        with _mock_pipeline_deps():
            result = run_full_pipeline(
                video_path=FAKE_VIDEO, target_language="Chinese",
                asr_engine=mock_asr_engine, translate_engine=mock_translate_engine,
            )

        assert isinstance(result, PipelineResult)
        assert result.output_path == Path("/fake/output/video_subtitled.mkv")
        assert result.source_language == "en"
        assert result.target_language == "Chinese"
        assert result.total_elapsed >= 0  # mock 引擎瞬间返回，耗时可为 0
        assert "你好世界" in result.srt_translated
        assert "Hello world" in result.srt_original
        assert len(result.asr_segments) == 3
        assert len(result.translated_segments) == 3

    def test_file_not_found(self, mock_asr_engine, mock_translate_engine):
        with mock.patch.object(Path, "exists", return_value=False):
            with pytest.raises(PipelineError, match="不存在"):
                run_full_pipeline(
                    video_path=Path("/nonexistent/v.mp4"), target_language="Chinese",
                    asr_engine=mock_asr_engine, translate_engine=mock_translate_engine,
                )

    def test_no_audio_stream(self, mock_asr_engine, mock_translate_engine):
        with _mock_pipeline_deps_no_audio():
            with pytest.raises(PipelineError, match="没有音频流"):
                run_full_pipeline(
                    video_path=FAKE_VIDEO, target_language="Chinese",
                    asr_engine=mock_asr_engine, translate_engine=mock_translate_engine,
                )

    def test_asr_engine_error(self, mock_translate_engine):
        asr_engine = mock.Mock(spec=ASREngine)
        asr_engine.transcribe.side_effect = RuntimeError("CUDA out of memory")

        with _mock_pipeline_deps():
            with pytest.raises(PipelineError, match="语音识别失败"):
                run_full_pipeline(
                    video_path=FAKE_VIDEO, target_language="Chinese",
                    asr_engine=asr_engine, translate_engine=mock_translate_engine,
                )

    def test_translate_engine_error(self, mock_asr_engine):
        translate_engine = mock.Mock(spec=TranslateEngine)
        translate_engine.translate.side_effect = ValueError("API key invalid")

        with _mock_pipeline_deps():
            with pytest.raises(PipelineError, match="翻译失败"):
                run_full_pipeline(
                    video_path=FAKE_VIDEO, target_language="Chinese",
                    asr_engine=mock_asr_engine, translate_engine=translate_engine,
                )

    def test_output_file_generated(self, mock_asr_engine, mock_translate_engine):
        with _mock_pipeline_deps():
            result = run_full_pipeline(
                video_path=FAKE_VIDEO, target_language="Chinese",
                asr_engine=mock_asr_engine, translate_engine=mock_translate_engine,
            )
        assert result.output_path == Path("/fake/output/video_subtitled.mkv")
        assert result.output_size == 2048


# ---------------------------------------------------------------------------
# TestFullPipelineStream
# ---------------------------------------------------------------------------


class TestFullPipelineStream:

    def test_event_types(self, mock_asr_engine, mock_translate_engine):
        with _mock_pipeline_deps():
            events = list(run_full_pipeline_stream(
                video_path=FAKE_VIDEO, target_language="Chinese",
                asr_engine=mock_asr_engine, translate_engine=mock_translate_engine,
            ))

        event_types = [e["event"] for e in events]
        assert "status" in event_types
        assert "segment" in event_types
        assert "translated" in event_types
        assert event_types[-1] == "done"
        done = events[-1]["data"]
        assert "output_path" in done
        assert "srt_translated" in done
        assert "download_url" in done

    def test_no_audio_stream_stream(self, mock_asr_engine, mock_translate_engine):
        with _mock_pipeline_deps_no_audio():
            events = list(run_full_pipeline_stream(
                video_path=FAKE_VIDEO, target_language="Chinese",
                asr_engine=mock_asr_engine, translate_engine=mock_translate_engine,
            ))
        assert events[-1]["event"] == "error"
        assert "没有音频流" in events[-1]["data"]["message"]

    def test_file_not_found_stream(self, mock_asr_engine, mock_translate_engine):
        with mock.patch.object(Path, "exists", return_value=False):
            events = list(run_full_pipeline_stream(
                video_path=Path("/nonexistent/v.mp4"), target_language="Chinese",
                asr_engine=mock_asr_engine, translate_engine=mock_translate_engine,
            ))
        assert events[-1]["event"] == "error"
        assert "不存在" in events[-1]["data"]["message"]

    def test_with_asr_stream(self, mock_translate_engine):
        """流式 ASR 通过 ASREngine.transcribe_stream 逐片段推送。"""
        asr_engine = mock.Mock(spec=ASREngine)
        asr_engine._model_size = "tiny"
        asr_engine.detect_language.return_value = "en"
        asr_engine.transcribe_stream.return_value = iter([
            ASRSegment(0.0, 2.0, "Hello", 0.9),
            ASRSegment(2.0, 4.0, "World", 0.9),
        ])

        with _mock_pipeline_deps():
            events = list(run_full_pipeline_stream(
                video_path=FAKE_VIDEO, target_language="Chinese",
                asr_engine=asr_engine, translate_engine=mock_translate_engine,
            ))

        segment_events = [e for e in events if e["event"] == "segment"]
        assert len(segment_events) == 2
        assert segment_events[0]["data"]["text"] == "Hello"
        assert segment_events[1]["data"]["text"] == "World"
        assert events[-1]["data"]["source_language"] == "en"


# ---------------------------------------------------------------------------
# 辅助函数测试
# ---------------------------------------------------------------------------


class TestHelpers:

    def test_asr_to_translate_segments(self):
        segs = [ASRSegment(0.0, 2.0, "Hello", 0.9), ASRSegment(3.0, 5.0, "World", 0.8)]
        result = _asr_to_translate_segments(segs)
        assert len(result) == 2
        assert result[0].source_text == "Hello"
        assert result[1].source_text == "World"

    def test_dicts_to_translate_segments(self):
        dicts = [
            {"start": 0.0, "end": 2.0, "text": "Hello"},
            {"start": 3.0, "end": 5.0, "text": "World"},
        ]
        result = _dicts_to_translate_segments(dicts)
        assert len(result) == 2
        assert result[0].source_text == "Hello"

    def test_lang_name_to_code_known(self):
        assert _lang_name_to_code("Chinese") == "zho"
        assert _lang_name_to_code("English") == "eng"
        assert _lang_name_to_code("Japanese") == "jpn"
        assert _lang_name_to_code("French") == "fra"
        assert _lang_name_to_code("German") == "deu"

    def test_lang_name_to_code_unknown(self):
        assert _lang_name_to_code("Esperanto") == "esp"

    def test_source_language_for_translation_user_provided(self):
        """用户已指定源语言时直接使用。"""
        assert _source_language_for_translation("English", "ja") == "English"
        assert _source_language_for_translation("eng", "ja") == "eng"

    def test_source_language_for_translation_auto_detected(self):
        """未指定源语言时，将检测到的 ISO 639-1 转为语言名称。"""
        assert _source_language_for_translation("", "en") == "English"
        assert _source_language_for_translation("", "zh") == "Chinese"
        assert _source_language_for_translation("", "ja") == "Japanese"
        assert _source_language_for_translation("", "xx") == "xx"


# ---------------------------------------------------------------------------
# TestPipelineAPI
# ---------------------------------------------------------------------------


@pytest.fixture
def test_app_pipeline() -> FastAPI:
    from fastapi.templating import Jinja2Templates
    from unittest import mock as umock

    app = FastAPI()
    template_dir = Path(__file__).resolve().parent.parent / "web" / "templates"
    templates = Jinja2Templates(directory=str(template_dir))

    from web.api import router as api_router
    app.include_router(api_router)

    # 统一把 API 模块的 settings 指向宽泛根目录，避免路径校验阻塞测试
    import web.api.probe as probe_module
    import web.api.extract as extract_module
    import web.api.subtitle as subtitle_module
    import web.api.pipeline as pipeline_module

    def _test_settings():
        cfg = umock.Mock()
        cfg.paths.media_input = Path("/tmp")
        cfg.paths.media_output = Path("/tmp")
        cfg.paths.temp_dir = Path("/tmp")
        cfg.ffmpeg.executable = "ffmpeg"
        cfg.ffmpeg.ffprobe_executable = "ffprobe"
        cfg.translate.target_language = "Chinese"
        cfg.translate.source_language = ""
        return cfg

    probe_module._get_settings = _test_settings
    extract_module._get_settings = _test_settings
    subtitle_module._get_settings = _test_settings
    pipeline_module._get_settings = _test_settings

    mock_asr = umock.Mock(spec=ASREngine)
    mock_asr.transcribe.return_value = [
        ASRSegment(0.0, 2.0, "Hello", 0.95),
        ASRSegment(2.0, 4.0, "World", 0.90),
    ]
    mock_asr.transcribe_stream.return_value = iter([
        ASRSegment(0.0, 2.0, "Hello", 0.95),
        ASRSegment(2.0, 4.0, "World", 0.90),
    ])
    mock_asr.detect_language.return_value = "en"
    mock_asr._model_size = "tiny"

    mock_trans = umock.Mock(spec=TranslateEngine)
    mock_trans.translate.return_value = [
        TranslateSegment(0.0, 2.0, "Hello", "你好"),
        TranslateSegment(2.0, 4.0, "World", "世界"),
    ]
    mock_trans.translate_stream.return_value = iter([
        [TranslateSegment(0.0, 2.0, "Hello", "你好")],
        [TranslateSegment(2.0, 4.0, "World", "世界")],
    ])

    pipeline_module._get_asr_engine = lambda: mock_asr
    pipeline_module._get_translate_engine = lambda: mock_trans

    from web.api.pipeline import router as pipeline_router
    app.include_router(pipeline_router)

    @app.get("/pipeline")
    async def pipeline_page(request: Request):
        return templates.TemplateResponse(request, "pipeline.html", {"version": "test"})

    return app


@pytest.fixture
def client_pipeline(test_app_pipeline: FastAPI) -> TestClient:
    return TestClient(test_app_pipeline)


class TestPipelineAPI:

    def test_get_page(self, client_pipeline):
        resp = client_pipeline.get("/pipeline")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_post_missing_params(self, client_pipeline):
        resp = client_pipeline.post("/api/pipeline/run")
        assert resp.status_code == 400

    def test_post_success_json(self, client_pipeline):
        with _mock_pipeline_deps():
            resp = client_pipeline.post("/api/pipeline/run", data={
                "video_path": "/fake/v.mp4", "target_language": "Chinese",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "output_path" in data
        assert "srt_translated" in data

    def test_post_htmx_returns_html(self, client_pipeline):
        with _mock_pipeline_deps():
            resp = client_pipeline.post(
                "/api/pipeline/run",
                data={"video_path": "/fake/v.mp4", "target_language": "Chinese"},
                headers={"HX-Request": "true"},
            )
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "流水线完成" in resp.text

    def test_sse_stream(self, client_pipeline):
        with _mock_pipeline_deps():
            resp = client_pipeline.post("/api/pipeline/run/stream", data={
                "video_path": "/fake/v.mp4", "target_language": "Chinese",
            })
        assert resp.status_code == 200
        text = resp.text
        assert "event: status" in text
        assert "event: segment" in text
        assert "event: translated" in text
        assert "event: done" in text

    def test_sse_error(self, client_pipeline):
        resp = client_pipeline.post("/api/pipeline/run/stream")
        assert resp.status_code == 200
        assert "event: error" in resp.text
