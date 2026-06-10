"""流提取模块测试。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest import mock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from processing.core.extract import (
    ExtractError,
    ExtractResult,
    _build_ffmpeg_args,
    _detect_codec,
    _format_size,
    _suggest_extension,
    extract_multiple,
    extract_stream,
)
from processing.core.probe import ProbeResult, parse_ffprobe_output


# ---------------------------------------------------------------------------
# 测试数据
# ---------------------------------------------------------------------------

VALID_FFPROBE_OUTPUT = {
    "format": {
        "filename": "/media/input/sample.mkv",
        "format_name": "matroska",
        "format_long_name": "Matroska",
        "size": "104857600",
        "duration": "300.000000",
        "bit_rate": "2800000",
    },
    "streams": [
        {
            "index": 0,
            "codec_name": "h264",
            "codec_long_name": "H.264 / AVC",
            "codec_type": "video",
            "width": 1920, "height": 1080,
            "pix_fmt": "yuv420p",
            "avg_frame_rate": "24000/1001",
            "bit_rate": "2500000",
            "duration": "300.000000",
            "disposition": {"default": 1},
            "tags": {"language": "und"},
        },
        {
            "index": 1,
            "codec_name": "aac",
            "codec_long_name": "AAC (Advanced Audio Coding)",
            "codec_type": "audio",
            "sample_rate": "48000", "channels": 6,
            "channel_layout": "5.1",
            "bit_rate": "384000",
            "duration": "300.000000",
            "disposition": {"default": 1},
            "tags": {"language": "jpn"},
        },
        {
            "index": 2,
            "codec_name": "aac",
            "codec_long_name": "AAC (Advanced Audio Coding)",
            "codec_type": "audio",
            "sample_rate": "44100", "channels": 2,
            "channel_layout": "stereo",
            "bit_rate": "192000",
            "duration": "300.000000",
            "disposition": {"default": 0},
            "tags": {"language": "eng"},
        },
        {
            "index": 3,
            "codec_name": "subrip",
            "codec_long_name": "SubRip subtitle",
            "codec_type": "subtitle",
            "duration": "298.500000",
            "disposition": {"default": 1},
            "tags": {"language": "zho"},
        },
        {
            "index": 4,
            "codec_name": "ass",
            "codec_long_name": "ASS subtitle",
            "codec_type": "subtitle",
            "duration": "298.500000",
            "disposition": {"default": 0},
            "tags": {"language": "eng"},
        },
    ],
}


# ---------------------------------------------------------------------------
# TestSuggestExtension
# ---------------------------------------------------------------------------


class TestSuggestExtension:
    """扩展名推断测试。"""

    def test_video_h264(self) -> None:
        assert _suggest_extension("h264", "video") == "mkv"

    def test_video_hevc(self) -> None:
        assert _suggest_extension("hevc", "video") == "mkv"

    def test_video_vp9(self) -> None:
        assert _suggest_extension("vp9", "video") == "webm"

    def test_audio_aac(self) -> None:
        assert _suggest_extension("aac", "audio") == "m4a"

    def test_audio_mp3(self) -> None:
        assert _suggest_extension("mp3", "audio") == "mp3"

    def test_audio_opus(self) -> None:
        assert _suggest_extension("opus", "audio") == "opus"

    def test_audio_pcm(self) -> None:
        """PCM 类 codec → wav。"""
        assert _suggest_extension("pcm_s16le", "audio") == "wav"
        assert _suggest_extension("pcm_f32le", "audio") == "wav"

    def test_subtitle_subrip(self) -> None:
        assert _suggest_extension("subrip", "subtitle") == "srt"

    def test_subtitle_ass(self) -> None:
        assert _suggest_extension("ass", "subtitle") == "ass"

    def test_subtitle_webvtt(self) -> None:
        assert _suggest_extension("webvtt", "subtitle") == "vtt"

    def test_unknown_codec(self) -> None:
        """未知 codec 返回默认扩展名。"""
        assert _suggest_extension("weird_codec", "audio") == "mka"


# ---------------------------------------------------------------------------
# TestBuildFFmpegArgs
# ---------------------------------------------------------------------------


class TestBuildFFmpegArgs:
    """FFmpeg 参数构建测试。"""

    def test_audio_extract(self) -> None:
        args = _build_ffmpeg_args(
            Path("/input/test.mkv"),
            Path("/output/test_audio_0.mka"),
            stream_index=0,
            stream_type="audio",
            ffmpeg_path="ffmpeg",
            overwrite=False,
        )
        assert "-map" in args
        map_idx = args.index("-map")
        assert args[map_idx + 1] == "0:a:0"
        assert "-c:a" in args
        assert "copy" in args
        # 不应覆盖已有文件
        assert "-n" in args
        assert "-y" not in args

    def test_video_extract(self) -> None:
        args = _build_ffmpeg_args(
            Path("/input/test.mkv"),
            Path("/output/test_video_0.mkv"),
            stream_index=0,
            stream_type="video",
            ffmpeg_path="ffmpeg",
            overwrite=False,
        )
        map_idx = args.index("-map")
        assert args[map_idx + 1] == "0:v:0"
        assert "-c:v" in args
        assert "-an" in args
        assert "-sn" in args

    def test_subtitle_extract(self) -> None:
        args = _build_ffmpeg_args(
            Path("/input/test.mkv"),
            Path("/output/test_subtitle_1.srt"),
            stream_index=1,
            stream_type="subtitle",
            ffmpeg_path="ffmpeg",
            overwrite=False,
        )
        map_idx = args.index("-map")
        assert args[map_idx + 1] == "0:s:1"
        assert "-c:s" in args
        assert "-vn" in args
        assert "-an" in args

    def test_overwrite_true(self) -> None:
        args = _build_ffmpeg_args(
            Path("/input/test.mkv"),
            Path("/output/test.mka"),
            stream_index=0,
            stream_type="audio",
            ffmpeg_path="ffmpeg",
            overwrite=True,
        )
        assert "-y" in args
        assert "-n" not in args

    def test_custom_ffmpeg_path(self) -> None:
        args = _build_ffmpeg_args(
            Path("/input/test.mkv"),
            Path("/output/test.mka"),
            stream_index=0,
            stream_type="audio",
            ffmpeg_path="/usr/local/bin/ffmpeg",
            overwrite=False,
        )
        assert args[0] == "/usr/local/bin/ffmpeg"

    def test_stream_index_2(self) -> None:
        """非零流索引。"""
        args = _build_ffmpeg_args(
            Path("/input/test.mkv"),
            Path("/output/test_audio_2.mka"),
            stream_index=2,
            stream_type="audio",
            ffmpeg_path="ffmpeg",
            overwrite=False,
        )
        map_idx = args.index("-map")
        assert args[map_idx + 1] == "0:a:2"


# ---------------------------------------------------------------------------
# TestExtractStream
# ---------------------------------------------------------------------------


class TestExtractStream:
    """extract_stream 函数测试（mock subprocess + mock probe）。"""

    @pytest.fixture
    def mock_probe(self):
        """Mock probe_file 返回有效探测结果。"""
        result = parse_ffprobe_output(VALID_FFPROBE_OUTPUT)
        with mock.patch("processing.core.extract._detect_codec", return_value="aac"), \
             mock.patch("processing.core.extract._get_stream_duration", return_value=300.0):
            yield result

    def test_normal_extract(self, tmp_path: Path, mock_probe) -> None:
        """正常提取返回 ExtractResult。"""
        input_path = tmp_path / "input.mkv"
        input_path.write_bytes(b"fake")
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        # aac → m4a，与 _suggest_extension 一致
        output_path = output_dir / "input_audio_0.m4a"

        with mock.patch("subprocess.run") as mock_run:
            # mock ffmpeg 成功后需要实际创建输出文件
            def _fake_run(*args, **kwargs):
                output_path.write_bytes(b"x" * 5000)
                return mock.Mock(returncode=0, stdout="", stderr="")
            mock_run.side_effect = _fake_run
            result = extract_stream(input_path, output_dir, 0, "audio")

            assert isinstance(result, ExtractResult)
            assert result.stream_type == "audio"
            assert result.stream_index == 0
            assert result.codec == "aac"
            assert result.output_path == output_path
            assert result.output_size == 5000

    def test_file_not_found(self) -> None:
        """输入文件不存在 → ExtractError。"""
        with pytest.raises(ExtractError, match="文件不存在"):
            extract_stream(Path("/nonexistent/file.mp4"), Path("/output"), 0)

    def test_invalid_stream_type(self, tmp_path: Path) -> None:
        """无效流类型 → ExtractError。"""
        f = tmp_path / "test.mkv"
        f.write_bytes(b"x")
        with pytest.raises(ExtractError, match="不支持的流类型"):
            extract_stream(f, tmp_path / "out", 0, "data")

    def test_stream_not_found(self, tmp_path: Path) -> None:
        """流索引越界 → ExtractError。"""
        f = tmp_path / "test.mkv"
        f.write_bytes(b"x")
        with mock.patch("processing.core.extract._detect_codec") as mock_detect:
            mock_detect.side_effect = ExtractError("音频流 #99 不存在（共 2 个）")
            with pytest.raises(ExtractError, match="#99"):
                extract_stream(f, tmp_path / "out", 99, "audio")

    def test_output_exists_no_overwrite(self, tmp_path: Path, mock_probe) -> None:
        """输出文件已存在 + overwrite=False → ExtractError。"""
        input_path = tmp_path / "input.mkv"
        input_path.write_bytes(b"fake")
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        # aac → m4a，与 _suggest_extension 一致
        (output_dir / "input_audio_0.m4a").write_bytes(b"x")

        with pytest.raises(ExtractError, match="输出文件已存在"):
            extract_stream(input_path, output_dir, 0, "audio", overwrite=False)

    def test_ffmpeg_not_found(self, tmp_path: Path, mock_probe) -> None:
        """ffmpeg 未找到 → ExtractError。"""
        f = tmp_path / "test.mkv"
        f.write_bytes(b"x")
        output_dir = tmp_path / "out"
        output_dir.mkdir()
        with mock.patch("subprocess.run", side_effect=FileNotFoundError):
            with pytest.raises(ExtractError, match="ffmpeg 未找到"):
                extract_stream(f, output_dir, 0, "audio")

    def test_ffmpeg_nonzero(self, tmp_path: Path, mock_probe) -> None:
        """ffmpeg 返回非零 → ExtractError。"""
        f = tmp_path / "test.mkv"
        f.write_bytes(b"x")
        output_dir = tmp_path / "out"
        output_dir.mkdir()
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(
                returncode=1, stdout="", stderr="codec not supported"
            )
            with pytest.raises(ExtractError, match="返回非零"):
                extract_stream(f, output_dir, 0, "audio")

    def test_timeout(self, tmp_path: Path, mock_probe) -> None:
        """超时 → ExtractError。"""
        f = tmp_path / "test.mkv"
        f.write_bytes(b"x")
        output_dir = tmp_path / "out"
        output_dir.mkdir()
        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("ffmpeg", 120)):
            with pytest.raises(ExtractError, match="超时"):
                extract_stream(f, output_dir, 0, "audio")

    def test_output_file_empty(self, tmp_path: Path, mock_probe) -> None:
        """输出文件为空 → ExtractError。"""
        f = tmp_path / "test.mkv"
        f.write_bytes(b"x")
        output_dir = tmp_path / "out"
        output_dir.mkdir()

        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            with pytest.raises(ExtractError, match="输出文件为空或不存在"):
                extract_stream(f, output_dir, 0, "audio")


# ---------------------------------------------------------------------------
# TestExtractMultiple
# ---------------------------------------------------------------------------


class TestExtractMultiple:
    """批量提取测试。"""

    @pytest.fixture
    def mock_single(self):
        """Mock 单个 extract_stream 调用。"""
        with mock.patch("processing.core.extract.extract_stream") as m:
            yield m

    def test_batch_all_success(self, tmp_path: Path, mock_single) -> None:
        """全部提取成功。"""
        mock_single.side_effect = [
            ExtractResult(0, "audio", "aac", tmp_path / "a_0.mka", 1000, None),
            ExtractResult(0, "subtitle", "subrip", tmp_path / "s_0.srt", 200, None),
        ]
        results = extract_multiple(
            tmp_path / "in.mkv",
            tmp_path / "out",
            [
                {"index": 0, "type": "audio"},
                {"index": 0, "type": "subtitle", "ext": "srt"},
            ],
        )
        assert len(results) == 2

    def test_batch_partial_failure(self, tmp_path: Path, mock_single) -> None:
        """部分失败：成功的返回，记录失败日志。"""
        mock_single.side_effect = [
            ExtractResult(0, "audio", "aac", tmp_path / "a_0.mka", 1000, None),
            ExtractError("字幕流 #0 不存在"),
        ]
        results = extract_multiple(
            tmp_path / "in.mkv",
            tmp_path / "out",
            [{"index": 0, "type": "audio"}, {"index": 0, "type": "subtitle"}],
        )
        assert len(results) == 1  # 只返回成功的

    def test_batch_empty_list(self, tmp_path: Path) -> None:
        """空流列表 → ExtractError。"""
        with pytest.raises(ExtractError, match="流列表为空"):
            extract_multiple(tmp_path / "in.mkv", tmp_path / "out", [])

    def test_batch_all_fail(self, tmp_path: Path, mock_single) -> None:
        """全部失败 → ExtractError。"""
        mock_single.side_effect = [
            ExtractError("失败 1"),
            ExtractError("失败 2"),
        ]
        with pytest.raises(ExtractError, match="所有流提取失败"):
            extract_multiple(
                tmp_path / "in.mkv",
                tmp_path / "out",
                [{"index": 0, "type": "audio"}, {"index": 1, "type": "audio"}],
            )


# ---------------------------------------------------------------------------
# TestFormatSize
# ---------------------------------------------------------------------------


class TestFormatSize:
    """_format_size 测试。"""

    def test_zero(self) -> None:
        assert _format_size(0) == "0 B"

    def test_bytes(self) -> None:
        assert "500" in _format_size(500)

    def test_kb(self) -> None:
        assert "KB" in _format_size(2048)

    def test_mb(self) -> None:
        assert "MB" in _format_size(5_000_000)

    def test_gb(self) -> None:
        assert "GB" in _format_size(2_000_000_000)


# ---------------------------------------------------------------------------
# TestAPIExtract
# ---------------------------------------------------------------------------


@pytest.fixture
def test_app_extract() -> FastAPI:
    """创建独立的测试 FastAPI app（不导入 app.py）。"""
    from fastapi.templating import Jinja2Templates

    app = FastAPI()

    template_dir = Path(__file__).resolve().parent.parent / "web" / "templates"
    templates = Jinja2Templates(directory=str(template_dir))

    from web.api import router as api_router
    app.include_router(api_router)

    # GET /extract 页面路由
    @app.get("/extract", response_model=None)
    async def extract_page(request: Request):
        return templates.TemplateResponse(request, "extract.html", {"version": "test"})

    return app


@pytest.fixture
def client_extract(test_app_extract: FastAPI) -> TestClient:
    return TestClient(test_app_extract)


class TestAPIExtract:
    """API 端点测试。"""

    def test_get_extract_page(self, client_extract: TestClient) -> None:
        """GET /extract 返回 HTML 页面。"""
        response = client_extract.get("/extract")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_post_missing_params(self, client_extract: TestClient) -> None:
        """POST /api/extract 缺参 → 400。"""
        response = client_extract.post("/api/extract")
        assert response.status_code == 400
        data = response.json()
        assert data["success"] is False

    def test_post_single_stream_success(self, client_extract: TestClient) -> None:
        """POST /api/extract 单流提取成功。"""
        with mock.patch("processing.core.extract.extract_stream") as mock_ext:
            mock_ext.return_value = ExtractResult(
                0, "audio", "aac", Path("/media/output/test_audio_0.mka"), 5242880, 300.0
            )
            response = client_extract.post("/api/extract", data={
                "file_path": "/tmp/test.mkv",
                "stream_index": "0",
                "stream_type": "audio",
            })
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["count"] == 1
            assert data["results"][0]["codec"] == "aac"
            assert "download_url" in data["results"][0]

    def test_post_single_stream_error_404(self, client_extract: TestClient) -> None:
        """单流提取 → 流不存在 → 404。"""
        with mock.patch("processing.core.extract.extract_stream") as mock_ext:
            mock_ext.side_effect = ExtractError("音频流 #5 不存在（共 2 个）")
            response = client_extract.post("/api/extract", data={
                "file_path": "/tmp/test.mkv",
                "stream_index": "5",
                "stream_type": "audio",
            })
            assert response.status_code == 404
            data = response.json()
            assert "不存在" in data["error"]

    def test_post_stream_error_409(self, client_extract: TestClient) -> None:
        """单流提取 → 输出已存在 → 409。"""
        with mock.patch("processing.core.extract.extract_stream") as mock_ext:
            mock_ext.side_effect = ExtractError(
                "输出文件已存在: /media/output/test_audio_0.mka。请使用 overwrite=True"
            )
            response = client_extract.post("/api/extract", data={
                "file_path": "/tmp/test.mkv",
                "stream_index": "0",
                "stream_type": "audio",
            })
            assert response.status_code == 409
            assert "已存在" in response.json()["error"]

    def test_post_batch_success(self, client_extract: TestClient) -> None:
        """POST /api/extract 批量提取成功。"""
        with mock.patch("processing.core.extract.extract_multiple") as mock_multi:
            mock_multi.return_value = [
                ExtractResult(0, "audio", "aac", Path("/media/output/t_audio_0.mka"), 5000, 300.0),
                ExtractResult(0, "subtitle", "subrip", Path("/media/output/t_subtitle_0.srt"), 200, 298.5),
            ]
            response = client_extract.post("/api/extract", data={
                "file_path": "/tmp/test.mkv",
                "streams": ["audio:0", "subtitle:0:srt"],
            })
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["count"] == 2

    def test_post_htmx_returns_html(self, client_extract: TestClient) -> None:
        """HX-Request 头 → 返回 HTML fragment。"""
        with mock.patch("processing.core.extract.extract_stream") as mock_ext:
            mock_ext.return_value = ExtractResult(
                0, "audio", "aac", Path("/media/output/test_audio_0.mka"), 5242880, 300.0
            )
            response = client_extract.post(
                "/api/extract",
                data={"file_path": "/tmp/test.mkv", "stream_index": "0", "stream_type": "audio"},
                headers={"HX-Request": "true"},
            )
            assert response.status_code == 200
            assert "text/html" in response.headers["content-type"]
            assert "提取完成" in response.text

    def test_post_htmx_error_returns_html(self, client_extract: TestClient) -> None:
        """HX-Request 头 + 提取失败 → HTML 错误片段。"""
        with mock.patch("processing.core.extract.extract_stream") as mock_ext:
            mock_ext.side_effect = ExtractError("ffmpeg 炸了")
            response = client_extract.post(
                "/api/extract",
                data={"file_path": "/tmp/test.mkv", "stream_index": "0", "stream_type": "audio"},
                headers={"HX-Request": "true"},
            )
            assert response.status_code == 200
            assert "text/html" in response.headers["content-type"]
            assert "ffmpeg 炸了" in response.text

    def test_download_success(self, client_extract: TestClient, tmp_path: Path) -> None:
        """GET /api/extract/download → 文件下载成功。"""
        # 创建临时 output 目录
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        test_file = output_dir / "test_audio_0.mka"
        test_file.write_bytes(b"fake audio data")

        with mock.patch("web.api.extract._get_settings") as mock_settings:
            from config import Settings

            # 必须让 media_output 解析到 tmp 下的真实路径
            real_output = output_dir.resolve()
            mock_settings.return_value = Settings.load()
            mock_settings.return_value.paths.media_output = real_output

            response = client_extract.get(
                "/api/extract/download",
                params={"path": str(real_output / "test_audio_0.mka")},
            )
            assert response.status_code == 200
            assert response.content == b"fake audio data"

    def test_download_file_not_found(self, client_extract: TestClient, tmp_path: Path) -> None:
        """GET /api/extract/download → 文件不存在 → 404。"""
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        with mock.patch("web.api.extract._get_settings") as mock_settings:
            real_output = output_dir.resolve()
            mock_settings.return_value = mock.Mock()
            mock_settings.return_value.paths.media_output = real_output

            response = client_extract.get(
                "/api/extract/download",
                params={"path": str(real_output / "nope.mka")},
            )
            assert response.status_code == 404

    def test_download_path_traversal(self, client_extract: TestClient, tmp_path: Path) -> None:
        """路径穿越攻击 → 403。"""
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        with mock.patch("web.api.extract._get_settings") as mock_settings:
            real_output = output_dir.resolve()
            mock_settings.return_value = mock.Mock()
            mock_settings.return_value.paths.media_output = real_output

            # 尝试访问上级目录
            response = client_extract.get(
                "/api/extract/download",
                params={"path": str(real_output.parent / "secret.txt")},
            )
            assert response.status_code == 403
