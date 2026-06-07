"""流探测模块测试。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest import mock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from processing.core.probe import (
    AudioStream,
    FormatInfo,
    ProbeError,
    ProbeResult,
    SubtitleStream,
    VideoStream,
    _format_duration,
    _format_size,
    _parse_fps,
    _safe_filename,
    parse_ffprobe_output,
    probe_file,
)


# ---------------------------------------------------------------------------
# 测试数据
# ---------------------------------------------------------------------------

VALID_FFPROBE_OUTPUT = {
    "format": {
        "filename": "/media/input/sample.mp4",
        "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
        "format_long_name": "QuickTime / MOV",
        "size": "52428800",
        "duration": "120.500000",
        "bit_rate": "3478133",
    },
    "streams": [
        {
            "index": 0,
            "codec_name": "h264",
            "codec_long_name": "H.264 / AVC / MPEG-4 AVC",
            "codec_type": "video",
            "width": 1920,
            "height": 1080,
            "pix_fmt": "yuv420p",
            "avg_frame_rate": "24000/1001",
            "bit_rate": "3000000",
            "duration": "120.500000",
            "bits_per_raw_sample": 8,
            "disposition": {"default": 1, "dub": 0},
            "tags": {"language": "eng", "title": "Main Video"},
        },
        {
            "index": 1,
            "codec_name": "aac",
            "codec_long_name": "AAC (Advanced Audio Coding)",
            "codec_type": "audio",
            "sample_rate": "48000",
            "channels": 6,
            "channel_layout": "5.1",
            "bit_rate": "384000",
            "duration": "120.500000",
            "disposition": {"default": 1},
            "tags": {"language": "jpn"},
        },
        {
            "index": 2,
            "codec_name": "aac",
            "codec_long_name": "AAC (Advanced Audio Coding)",
            "codec_type": "audio",
            "sample_rate": "44100",
            "channels": 2,
            "channel_layout": "stereo",
            "bit_rate": "192000",
            "duration": "120.500000",
            "disposition": {"default": 0},
            "tags": {"language": "eng"},
        },
        {
            "index": 3,
            "codec_name": "subrip",
            "codec_long_name": "SubRip subtitle",
            "codec_type": "subtitle",
            "duration": "118.000000",
            "disposition": {"default": 0},
            "tags": {"language": "zho"},
        },
    ],
}

VIDEO_ONLY_OUTPUT = {
    "format": {
        "filename": "/media/input/novoice.mp4",
        "format_name": "mp4",
        "format_long_name": "MP4 (MPEG-4 Part 14)",
        "size": "10485760",
        "duration": "30.000000",
    },
    "streams": [
        {
            "index": 0,
            "codec_name": "h265",
            "codec_long_name": "H.265 / HEVC",
            "codec_type": "video",
            "width": 3840,
            "height": 2160,
            "pix_fmt": "yuv420p10le",
            "avg_frame_rate": "60/1",
            "bit_rate": "15000000",
            "duration": "30.000000",
            "bits_per_raw_sample": 10,
            "disposition": {"default": 1},
            "tags": {},
        },
    ],
}

AUDIO_ONLY_OUTPUT = {
    "format": {
        "filename": "/media/input/music.mp3",
        "format_name": "mp3",
        "format_long_name": "MP2/3 (MPEG audio layer 2/3)",
        "size": "5242880",
        "duration": "180.000000",
        "bit_rate": "320000",
    },
    "streams": [
        {
            "index": 0,
            "codec_name": "mp3",
            "codec_long_name": "MP3 (MPEG audio layer 3)",
            "codec_type": "audio",
            "sample_rate": "44100",
            "channels": 2,
            "channel_layout": "stereo",
            "bit_rate": "320000",
            "duration": "180.000000",
            "disposition": {"default": 0},
            "tags": {"language": "und"},
        },
    ],
}

EMPTY_STREAMS_OUTPUT = {
    "format": {
        "filename": "/media/input/empty.mkv",
        "format_name": "matroska",
        "size": "0",
    },
    "streams": [],
}

MISSING_FIELDS_OUTPUT = {
    "format": {
        "filename": "/media/input/minimal.avi",
        "format_name": "avi",
    },
    "streams": [
        {
            "codec_type": "video",
            "codec_name": "mpeg4",
            "width": 640,
            "height": 480,
            # 缺少 pix_fmt, avg_frame_rate 等可选字段
        },
    ],
}

UNKNOWN_STREAM_OUTPUT = {
    "format": {
        "filename": "/media/input/data.mkv",
        "format_name": "matroska",
    },
    "streams": [
        {
            "index": 0,
            "codec_type": "data",
            "codec_name": "bin_data",
            "tags": {},
        },
    ],
}


# ---------------------------------------------------------------------------
# TestParseFFprobeOutput
# ---------------------------------------------------------------------------


class TestParseFFprobeOutput:
    """解析 ffprobe JSON 输出的单元测试。"""

    def test_valid_output(self) -> None:
        """完整媒体输出 → 正确的 ProbeResult。"""
        result = parse_ffprobe_output(VALID_FFPROBE_OUTPUT)

        # Format
        assert result.format.filename == "/media/input/sample.mp4"
        assert result.format.format_name == "mov,mp4,m4a,3gp,3g2,mj2"
        assert result.format.format_long == "QuickTime / MOV"
        assert result.format.size_bytes == 52428800
        assert result.format.duration == 120.5
        assert result.format.bitrate == 3478133

        # Video
        assert len(result.video_streams) == 1
        v = result.video_streams[0]
        assert isinstance(v, VideoStream)
        assert v.index == 0
        assert v.codec == "h264"
        assert v.codec_long and "H.264" in v.codec_long
        assert v.width == 1920
        assert v.height == 1080
        assert v.pix_fmt == "yuv420p"
        assert v.bitrate == 3000000
        assert v.fps == "24000/1001"
        assert v.fps_float == pytest.approx(23.976, rel=1e-3)
        assert v.duration == 120.5
        assert v.bit_depth == 8
        assert v.disposition == {"default": 1, "dub": 0}
        assert v.tags == {"language": "eng", "title": "Main Video"}
        assert v.language == "eng"

        # Audio
        assert len(result.audio_streams) == 2
        a1 = result.audio_streams[0]
        assert isinstance(a1, AudioStream)
        assert a1.index == 1
        assert a1.codec == "aac"
        assert a1.language == "jpn"
        assert a1.sample_rate == 48000
        assert a1.channels == 6
        assert a1.channel_layout == "5.1"
        assert a1.bitrate == 384000

        a2 = result.audio_streams[1]
        assert a2.index == 2
        assert a2.language == "eng"
        assert a2.channels == 2
        assert a2.sample_rate == 44100

        # Subtitle
        assert len(result.subtitle_streams) == 1
        s = result.subtitle_streams[0]
        assert isinstance(s, SubtitleStream)
        assert s.index == 3
        assert s.codec == "subrip"
        assert s.language == "zho"
        assert s.duration == 118.0

    def test_video_only(self) -> None:
        """纯视频（无音频/字幕）。"""
        result = parse_ffprobe_output(VIDEO_ONLY_OUTPUT)
        assert len(result.video_streams) == 1
        assert len(result.audio_streams) == 0
        assert len(result.subtitle_streams) == 0

        v = result.video_streams[0]
        assert v.width == 3840
        assert v.height == 2160
        assert v.bit_depth == 10
        assert v.fps == "60/1"
        assert v.fps_float == 60.0

    def test_audio_only(self) -> None:
        """纯音频文件。"""
        result = parse_ffprobe_output(AUDIO_ONLY_OUTPUT)
        assert len(result.video_streams) == 0
        assert len(result.audio_streams) == 1
        assert len(result.subtitle_streams) == 0

        a = result.audio_streams[0]
        assert a.codec == "mp3"
        assert a.sample_rate == 44100

    def test_empty_streams(self) -> None:
        """无流的文件。"""
        result = parse_ffprobe_output(EMPTY_STREAMS_OUTPUT)
        assert len(result.video_streams) == 0
        assert len(result.audio_streams) == 0
        assert len(result.subtitle_streams) == 0
        assert result.format.format_name == "matroska"

    def test_unknown_stream_skipped(self) -> None:
        """未知流类型被跳过。"""
        result = parse_ffprobe_output(UNKNOWN_STREAM_OUTPUT)
        assert len(result.video_streams) == 0
        assert len(result.audio_streams) == 0
        assert len(result.subtitle_streams) == 0

    def test_und_language(self) -> None:
        """"und" 语言标签返回 None。"""
        result = parse_ffprobe_output(AUDIO_ONLY_OUTPUT)
        # "und" 不在 BCP47 映射表中，保留原值
        assert result.audio_streams[0].language == "und"

    def test_missing_optional_fields(self) -> None:
        """缺失可选字段使用默认值。"""
        result = parse_ffprobe_output(MISSING_FIELDS_OUTPUT)
        assert len(result.video_streams) == 1
        v = result.video_streams[0]
        assert v.width == 640
        assert v.height == 480
        assert v.pix_fmt == ""  # 缺少 → 空字符串
        assert v.fps == ""  # 缺少 → 空字符串
        assert v.fps_float is None
        assert v.bitrate is None

    def test_bcp47_language_normalization(self) -> None:
        """BCP-47 语言标签被规范化为 ISO 639-2。"""
        raw = {
            "format": {"filename": "test.mkv", "format_name": "matroska"},
            "streams": [
                {"codec_type": "audio", "codec_name": "aac", "tags": {"language": "ja"}},
                {"codec_type": "audio", "codec_name": "aac", "tags": {"language": "ko"}},
                {"codec_type": "audio", "codec_name": "aac", "tags": {"language": "fr"}},
                {"codec_type": "audio", "codec_name": "aac", "tags": {"language": "de"}},
                {"codec_type": "audio", "codec_name": "aac", "tags": {"language": "xx"}},
            ],
        }
        result = parse_ffprobe_output(raw)
        langs = [s.language for s in result.audio_streams]
        assert langs == ["jpn", "kor", "fra", "deu", "xx"]  # xx 未知则保留


# ---------------------------------------------------------------------------
# TestProbeFile
# ---------------------------------------------------------------------------


class TestProbeFile:
    """probe_file 函数测试（mock subprocess + mock Path）。"""

    @pytest.fixture
    def mock_path(self) -> Path:
        """返回一个路径并 patch 其 exists/is_file 为 True。"""
        p = Path("/fake/test/video.mp4")
        with mock.patch.object(Path, "exists", return_value=True), \
             mock.patch.object(Path, "is_file", return_value=True):
            yield p

    def test_normal(self, mock_path: Path) -> None:
        """正常探测返回 ProbeResult。"""
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(
                returncode=0,
                stdout=json.dumps(VALID_FFPROBE_OUTPUT),
                stderr="",
            )
            result = probe_file(mock_path)
            assert isinstance(result, ProbeResult)
            assert len(result.video_streams) == 1
            assert len(result.audio_streams) == 2
            assert len(result.subtitle_streams) == 1

    def test_file_not_found(self) -> None:
        """文件不存在 → ProbeError。"""
        with pytest.raises(ProbeError, match="文件不存在"):
            probe_file(Path("/nonexistent/video.mp4"))

    def test_not_a_file(self, tmp_path: Path) -> None:
        """路径不是文件 → ProbeError。"""
        with pytest.raises(ProbeError, match="路径不是文件"):
            probe_file(tmp_path)  # tmp_path 是目录

    def test_ffprobe_not_found(self, mock_path: Path) -> None:
        """ffprobe 未找到 → ProbeError。"""
        with mock.patch("subprocess.run", side_effect=FileNotFoundError):
            with pytest.raises(ProbeError, match="ffprobe 未找到"):
                probe_file(mock_path)

    def test_nonzero_returncode(self, mock_path: Path) -> None:
        """ffprobe 返回非零 → ProbeError。"""
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(
                returncode=1,
                stdout="",
                stderr="Invalid data found when processing input",
            )
            with pytest.raises(ProbeError, match="返回非零"):
                probe_file(mock_path)

    def test_timeout(self, mock_path: Path) -> None:
        """超时 → ProbeError。"""
        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("ffprobe", 30)):
            with pytest.raises(ProbeError, match="超时"):
                probe_file(mock_path)

    def test_invalid_json(self, mock_path: Path) -> None:
        """ffprobe 返回无效 JSON → ProbeError。"""
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(
                returncode=0,
                stdout="not json at all",
                stderr="",
            )
            with pytest.raises(ProbeError, match="不是有效 JSON"):
                probe_file(mock_path)

    def test_uses_custom_ffprobe_path(self, mock_path: Path) -> None:
        """使用自定义 ffprobe 路径。"""
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(
                returncode=0,
                stdout=json.dumps(VALID_FFPROBE_OUTPUT),
                stderr="",
            )
            probe_file(mock_path, ffprobe_path="/usr/local/bin/ffprobe")
            args = mock_run.call_args[0][0]
            assert args[0] == "/usr/local/bin/ffprobe"


# ---------------------------------------------------------------------------
# TestHelpers
# ---------------------------------------------------------------------------


class TestHelpers:
    """辅助函数测试。"""

    def test_parse_fps_integer(self) -> None:
        assert _parse_fps("30") == 30.0
        assert _parse_fps("60/1") == 60.0

    def test_parse_fps_fraction(self) -> None:
        assert _parse_fps("24000/1001") == pytest.approx(23.976, rel=1e-3)
        assert _parse_fps("30000/1001") == pytest.approx(29.97, rel=1e-3)

    def test_parse_fps_invalid(self) -> None:
        assert _parse_fps("") is None
        assert _parse_fps("0/0") is None
        assert _parse_fps("unknown") is None

    def test_format_duration(self) -> None:
        assert _format_duration(None) == "未知"
        assert _format_duration(65.0) == "1:05"
        assert _format_duration(3661.0) == "1:01:01"
        assert _format_duration(0.0) == "0:00"

    def test_format_size(self) -> None:
        assert _format_size(0) == "未知"
        assert _format_size(500) == "500 B"
        assert _format_size(1536) == "2 KB"
        assert _format_size(5242880) == "5.0 MB"
        assert _format_size(2147483648) == "2.00 GB"

    def test_safe_filename(self) -> None:
        assert _safe_filename("short.mp4") == "short.mp4"
        long_name = "a" * 80
        result = _safe_filename(long_name)
        assert len(result) <= 60
        assert "..." in result


# ---------------------------------------------------------------------------
# TestAPIProbe
# ---------------------------------------------------------------------------


@pytest.fixture
def test_app() -> FastAPI:
    """创建独立的测试 FastAPI app（不导入 app.py，避免模块级配置加载）。"""
    from fastapi.templating import Jinja2Templates

    app = FastAPI()

    # 使用绝对路径，因为测试可能从不同目录运行
    template_dir = Path(__file__).resolve().parent.parent / "web" / "templates"
    templates = Jinja2Templates(directory=str(template_dir))

    from web.api import router as api_router
    app.include_router(api_router)

    # GET /probe 页面路由
    @app.get("/probe", response_model=None)
    async def probe_page(request: Request):  # noqa: F811
        return templates.TemplateResponse(request, "probe.html", {"version": "test"})

    return app


@pytest.fixture
def client(test_app: FastAPI) -> TestClient:
    return TestClient(test_app)


class TestAPIProbe:
    """API 端点测试。"""

    def test_get_probe_page(self, client: TestClient) -> None:
        """GET /probe 返回 HTML 页面。"""
        response = client.get("/probe")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_post_missing_input(self, client: TestClient) -> None:
        """POST /api/probe 缺输入 → 400。"""
        response = client.post("/api/probe")
        assert response.status_code == 400
        data = response.json()
        assert data["success"] is False

    def test_post_with_file_path_json(self, client: TestClient) -> None:
        """POST /api/probe 指定文件路径，返回 JSON。"""
        with mock.patch("processing.core.probe.probe_file") as mock_probe:
            mock_probe.return_value = parse_ffprobe_output(VALID_FFPROBE_OUTPUT)
            response = client.post("/api/probe", data={"file_path": "/tmp/test.mp4"})
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert len(data["video_streams"]) == 1

    def test_post_probe_error_json(self, client: TestClient) -> None:
        """POST /api/probe 探测失败 → 422。"""
        with mock.patch("processing.core.probe.probe_file", side_effect=ProbeError("test error")):
            response = client.post("/api/probe", data={"file_path": "/tmp/bad.mp4"})
            assert response.status_code == 422
            data = response.json()
            assert data["success"] is False
            assert "test error" in data["error"]

    def test_post_htmx_header_returns_html(self, client: TestClient) -> None:
        """HX-Request 头 → 返回 HTML fragment。"""
        with mock.patch("processing.core.probe.probe_file") as mock_probe:
            mock_probe.return_value = parse_ffprobe_output(VALID_FFPROBE_OUTPUT)
            response = client.post(
                "/api/probe",
                data={"file_path": "/tmp/test.mp4"},
                headers={"HX-Request": "true"},
            )
            assert response.status_code == 200
            assert "text/html" in response.headers["content-type"]
            assert "H.264" in response.text

    def test_post_htmx_error_returns_html(self, client: TestClient) -> None:
        """HX-Request 头 + 探测失败 → HTML 错误片段。"""
        with mock.patch("processing.core.probe.probe_file", side_effect=ProbeError("boom")):
            response = client.post(
                "/api/probe",
                data={"file_path": "/tmp/bad.mp4"},
                headers={"HX-Request": "true"},
            )
            assert response.status_code == 200  # HTMX 始终返回 200
            assert "text/html" in response.headers["content-type"]
            assert "boom" in response.text

    def test_post_htmx_missing_input_html(self, client: TestClient) -> None:
        """HX-Request 头 + 缺输入 → HTML 错误片段。"""
        response = client.post("/api/probe", headers={"HX-Request": "true"})
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "请提供文件" in response.text

    def test_get_with_file_path(self, client: TestClient) -> None:
        """GET /api/probe?file_path= → JSON 概览。"""
        with mock.patch("processing.core.probe.probe_file") as mock_probe:
            mock_probe.return_value = parse_ffprobe_output(VALID_FFPROBE_OUTPUT)
            response = client.get("/api/probe", params={"file_path": "/tmp/test.mp4"})
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["video_streams"] == 1
            assert data["audio_streams"] == 2
            assert data["subtitle_streams"] == 1
            assert data["total_streams"] == 4

    def test_get_missing_file_path(self, client: TestClient) -> None:
        """GET /api/probe 无参数 → 400。"""
        response = client.get("/api/probe")
        assert response.status_code == 400
        data = response.json()
        assert data["success"] is False

    def test_get_probe_error(self, client: TestClient) -> None:
        """GET /api/probe 探测失败 → 422。"""
        with mock.patch("processing.core.probe.probe_file", side_effect=ProbeError("not found")):
            response = client.get("/api/probe", params={"file_path": "/tmp/nope.mp4"})
            assert response.status_code == 422
            data = response.json()
            assert "not found" in data["error"]
