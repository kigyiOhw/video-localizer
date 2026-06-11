"""字幕封装模块测试。"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest import mock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from processing.core.mux import (
    MuxError,
    MuxResult,
    _build_add_subtitle_args,
    _count_subtitle_streams,
    _format_size,
    _validate_subtitle_format,
    add_subtitle,
)


# ---------------------------------------------------------------------------
# 测试数据
# ---------------------------------------------------------------------------

VALID_PROBE_STREAMS = [
    {
        "index": 0,
        "codec_name": "h264",
        "codec_type": "video",
        "width": 1920, "height": 1080,
        "tags": {"language": "und"},
        "disposition": {},
    },
    {
        "index": 1,
        "codec_name": "aac",
        "codec_type": "audio",
        "tags": {"language": "jpn"},
        "disposition": {},
    },
    {
        "index": 2,
        "codec_name": "subrip",
        "codec_type": "subtitle",
        "tags": {"language": "zho"},
        "disposition": {"default": 1},
    },
]


# ---------------------------------------------------------------------------
# TestValidateSubtitleFormat
# ---------------------------------------------------------------------------


class TestValidateSubtitleFormat:
    """字幕格式验证测试。"""

    def test_srt_subrip(self) -> None:
        assert _validate_subtitle_format(Path("sub.srt")) == "subrip"
        assert _validate_subtitle_format(Path("sub.SRT")) == "subrip"

    def test_ass(self) -> None:
        assert _validate_subtitle_format(Path("sub.ass")) == "ass"

    def test_ssa(self) -> None:
        assert _validate_subtitle_format(Path("sub.ssa")) == "ass"

    def test_vtt_webvtt(self) -> None:
        assert _validate_subtitle_format(Path("sub.vtt")) == "webvtt"

    def test_txt_raises(self) -> None:
        with pytest.raises(MuxError, match="不支持的字幕格式"):
            _validate_subtitle_format(Path("notes.txt"))

    def test_no_extension_raises(self) -> None:
        with pytest.raises(MuxError, match="不支持的字幕格式"):
            _validate_subtitle_format(Path("no_extension"))


# ---------------------------------------------------------------------------
# TestBuildAddSubtitleArgs
# ---------------------------------------------------------------------------


class TestBuildAddSubtitleArgs:
    """FFmpeg 添加字幕参数构建测试。"""

    def test_mkv_basic(self) -> None:
        args = _build_add_subtitle_args(
            video_path=Path("/input/movie.mkv"),
            subtitle_path=Path("/input/sub.srt"),
            output_path=Path("/output/movie_subtitled.mkv"),
            language="eng",
            container="mkv",
            ffmpeg_path="ffmpeg",
            overwrite=False,
        )
        assert args[0] == "ffmpeg"
        assert "-n" in args
        assert "-i" in args
        assert str(Path("/input/movie.mkv")) in args
        assert str(Path("/input/sub.srt")) in args
        assert "-c" in args
        assert "copy" in args
        assert "-map" in args
        # Should have two -map entries: 0 and 1
        map_indices = [i for i, a in enumerate(args) if a == "-map"]
        assert len(map_indices) == 2
        assert args[map_indices[0] + 1] == "0"
        assert args[map_indices[1] + 1] == "1"
        assert "-metadata:s:s:0" in args
        meta_idx = args.index("-metadata:s:s:0")
        assert args[meta_idx + 1] == "language=eng"
        assert str(Path("/output/movie_subtitled.mkv")) in args

    def test_mp4_adds_mov_text(self) -> None:
        args = _build_add_subtitle_args(
            video_path=Path("/input/movie.mp4"),
            subtitle_path=Path("/input/sub.srt"),
            output_path=Path("/output/movie_subtitled.mp4"),
            language="jpn",
            container="mp4",
            ffmpeg_path="ffmpeg",
            overwrite=False,
        )
        assert "-c:s" in args
        cs_idx = args.index("-c:s")
        assert args[cs_idx + 1] == "mov_text"

    def test_overwrite_true(self) -> None:
        args = _build_add_subtitle_args(
            video_path=Path("/input/movie.mkv"),
            subtitle_path=Path("/input/sub.srt"),
            output_path=Path("/output/movie_subtitled.mkv"),
            language="eng",
            container="mkv",
            ffmpeg_path="ffmpeg",
            overwrite=True,
        )
        assert "-y" in args
        assert "-n" not in args

    def test_paths_with_spaces(self) -> None:
        args = _build_add_subtitle_args(
            video_path=Path("/path with spaces/movie.mkv"),
            subtitle_path=Path("/some dir/subtitle.srt"),
            output_path=Path("/output dir/movie_subtitled.mkv"),
            language="eng",
            container="mkv",
            ffmpeg_path="ffmpeg",
            overwrite=False,
        )
        assert str(Path("/path with spaces/movie.mkv")) in args
        assert str(Path("/some dir/subtitle.srt")) in args

    def test_custom_ffmpeg_path(self) -> None:
        args = _build_add_subtitle_args(
            video_path=Path("/input/movie.mkv"),
            subtitle_path=Path("/input/sub.srt"),
            output_path=Path("/output/out.mkv"),
            language="zho",
            container="mkv",
            ffmpeg_path="/usr/local/bin/ffmpeg",
            overwrite=False,
        )
        assert args[0] == "/usr/local/bin/ffmpeg"


# ---------------------------------------------------------------------------
# TestCountSubtitleStreams
# ---------------------------------------------------------------------------


class TestCountSubtitleStreams:
    """_count_subtitle_streams 测试。"""

    def test_has_subtitles(self) -> None:
        """已有字幕轨 → 返回正确数量。"""
        from processing.core.probe import parse_ffprobe_output

        probe_data = {
            "format": {"filename": "test.mkv", "format_name": "matroska", "duration": "60.0"},
            "streams": [
                {"index": 0, "codec_name": "h264", "codec_type": "video", "tags": {}, "disposition": {}},
                {"index": 1, "codec_name": "subrip", "codec_type": "subtitle", "tags": {"language": "eng"}, "disposition": {}},
                {"index": 2, "codec_name": "subrip", "codec_type": "subtitle", "tags": {"language": "zho"}, "disposition": {}},
            ],
        }
        result = parse_ffprobe_output(probe_data)

        with mock.patch("processing.core.probe.probe_file", return_value=result):
            assert _count_subtitle_streams(Path("/test.mkv"), "ffprobe") == 2

    def test_no_subtitles(self) -> None:
        """无字幕轨 → 返回 0。"""
        from processing.core.probe import parse_ffprobe_output

        probe_data = {
            "format": {"filename": "test.mkv", "format_name": "matroska", "duration": "60.0"},
            "streams": [
                {"index": 0, "codec_name": "h264", "codec_type": "video", "tags": {}, "disposition": {}},
                {"index": 1, "codec_name": "aac", "codec_type": "audio", "tags": {}, "disposition": {}},
            ],
        }
        result = parse_ffprobe_output(probe_data)

        with mock.patch("processing.core.probe.probe_file", return_value=result):
            assert _count_subtitle_streams(Path("/test.mkv"), "ffprobe") == 0

    def test_probe_failure_returns_zero(self) -> None:
        """探测失败 → 返回 0。"""
        from processing.core.probe import ProbeError

        with mock.patch("processing.core.probe.probe_file", side_effect=ProbeError("fail")):
            assert _count_subtitle_streams(Path("/test.mkv"), "ffprobe") == 0


# ---------------------------------------------------------------------------
# TestAddSubtitle
# ---------------------------------------------------------------------------


class TestAddSubtitle:
    """add_subtitle 函数测试（mock subprocess）。"""

    def _make_fake_run(self, output_path: Path, size: int = 5000):
        """创建 mock subprocess.run 的 side_effect。"""
        def _fake(*args, **kwargs):
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"x" * size)
            return mock.Mock(returncode=0, stdout="", stderr="")
        return _fake

    def test_mkv_normal(self, tmp_path: Path) -> None:
        """MKV 正常添加字幕。"""
        video = tmp_path / "movie.mkv"
        video.write_bytes(b"fake video")
        subtitle = tmp_path / "sub.srt"
        subtitle.write_bytes(b"1\n00:00:01,000 --> 00:00:02,000\nHello\n")
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        output = output_dir / "movie_subtitled.mkv"

        with mock.patch("processing.core.mux._count_subtitle_streams", return_value=1):
            with mock.patch("subprocess.run") as mock_run:
                mock_run.side_effect = self._make_fake_run(output)
                result = add_subtitle(video, subtitle, "eng", output_path=output, overwrite=True)

        assert isinstance(result, MuxResult)
        assert result.input_video == video
        assert result.output_path == output
        assert result.output_size == 5000
        assert result.subtitle_count == 2  # 1 existing + 1 new
        assert result.added_track_index == 1
        assert result.language == "eng"

    def test_mp4_normal(self, tmp_path: Path) -> None:
        """MP4 正常添加字幕。"""
        video = tmp_path / "movie.mp4"
        video.write_bytes(b"fake video")
        subtitle = tmp_path / "sub.srt"
        subtitle.write_bytes(b"1\n00:00:01,000 --> 00:00:02,000\nTest\n")
        output = tmp_path / "out" / "movie_subtitled.mp4"

        with mock.patch("processing.core.mux._count_subtitle_streams", return_value=0):
            with mock.patch("subprocess.run") as mock_run:
                mock_run.side_effect = self._make_fake_run(output)
                result = add_subtitle(video, subtitle, "jpn", container="mp4", output_path=output, overwrite=True)

        assert result.language == "jpn"
        assert result.subtitle_count == 1
        assert result.added_track_index == 0

    def test_video_not_found(self) -> None:
        """视频文件不存在 → MuxError。"""
        with pytest.raises(MuxError, match="视频文件不存在"):
            add_subtitle(Path("/nonexistent/video.mkv"), Path("/tmp/sub.srt"), "eng")

    def test_subtitle_not_found(self, tmp_path: Path) -> None:
        """字幕文件不存在 → MuxError。"""
        video = tmp_path / "video.mkv"
        video.write_bytes(b"x")
        with pytest.raises(MuxError, match="字幕文件不存在"):
            add_subtitle(video, Path("/nonexistent/sub.srt"), "eng")

    def test_unsupported_subtitle_format(self, tmp_path: Path) -> None:
        """不支持的字幕格式 → MuxError。"""
        video = tmp_path / "video.mkv"
        video.write_bytes(b"x")
        sub = tmp_path / "notes.txt"
        sub.write_bytes(b"hello")
        with pytest.raises(MuxError, match="不支持的字幕格式"):
            add_subtitle(video, sub, "eng")

    def test_invalid_container(self, tmp_path: Path) -> None:
        """不支持的容器格式 → MuxError。"""
        video = tmp_path / "video.mkv"
        video.write_bytes(b"x")
        sub = tmp_path / "sub.srt"
        sub.write_bytes(b"1\n00:00:01,000 --> 00:00:02,000\nHi\n")
        with pytest.raises(MuxError, match="不支持的容器格式"):
            add_subtitle(video, sub, "eng", container="avi")

    def test_empty_language(self, tmp_path: Path) -> None:
        """空语言代码 → MuxError。"""
        video = tmp_path / "video.mkv"
        video.write_bytes(b"x")
        sub = tmp_path / "sub.srt"
        sub.write_bytes(b"1\n00:00:01,000 --> 00:00:02,000\nHi\n")
        with pytest.raises(MuxError, match="语言代码不能为空"):
            add_subtitle(video, sub, "")

    def test_ffmpeg_not_found(self, tmp_path: Path) -> None:
        """ffmpeg 未找到 → MuxError。"""
        video = tmp_path / "video.mkv"
        video.write_bytes(b"x")
        sub = tmp_path / "sub.srt"
        sub.write_bytes(b"1\n00:00:01,000 --> 00:00:02,000\nHi\n")

        with mock.patch("processing.core.mux._count_subtitle_streams", return_value=0):
            with mock.patch("subprocess.run", side_effect=FileNotFoundError):
                with pytest.raises(MuxError, match="ffmpeg 未找到"):
                    add_subtitle(video, sub, "eng")

    def test_ffmpeg_nonzero(self, tmp_path: Path) -> None:
        """ffmpeg 非零返回码 → MuxError。"""
        video = tmp_path / "video.mkv"
        video.write_bytes(b"x")
        sub = tmp_path / "sub.srt"
        sub.write_bytes(b"1\n00:00:01,000 --> 00:00:02,000\nHi\n")

        with mock.patch("processing.core.mux._count_subtitle_streams", return_value=0):
            with mock.patch("subprocess.run") as mock_run:
                mock_run.return_value = mock.Mock(returncode=1, stdout="", stderr="invalid data")
                with pytest.raises(MuxError, match="返回非零"):
                    add_subtitle(video, sub, "eng")

    def test_timeout(self, tmp_path: Path) -> None:
        """超时 → MuxError。"""
        video = tmp_path / "video.mkv"
        video.write_bytes(b"x")
        sub = tmp_path / "sub.srt"
        sub.write_bytes(b"1\n00:00:01,000 --> 00:00:02,000\nHi\n")

        with mock.patch("processing.core.mux._count_subtitle_streams", return_value=0):
            with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("ffmpeg", 120)):
                with pytest.raises(MuxError, match="超时"):
                    add_subtitle(video, sub, "eng")

    def test_output_exists_no_overwrite(self, tmp_path: Path) -> None:
        """输出文件已存在 + overwrite=False → MuxError。"""
        video = tmp_path / "video.mkv"
        video.write_bytes(b"x")
        sub = tmp_path / "sub.srt"
        sub.write_bytes(b"1\n00:00:01,000 --> 00:00:02,000\nHi\n")
        output = tmp_path / "out.mkv"
        output.write_bytes(b"existing")

        with pytest.raises(MuxError, match="输出文件已存在"):
            add_subtitle(video, sub, "eng", output_path=output, overwrite=False)


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
# TestAPISubtitle
# ---------------------------------------------------------------------------


@pytest.fixture
def test_app_subtitle() -> FastAPI:
    """创建独立的测试 FastAPI app。"""
    from fastapi.templating import Jinja2Templates

    app = FastAPI()

    template_dir = Path(__file__).resolve().parent.parent / "web" / "templates"
    templates = Jinja2Templates(directory=str(template_dir))

    from web.api import router as api_router
    app.include_router(api_router)

    @app.get("/subtitle", response_model=None)
    async def subtitle_page(request: Request):
        return templates.TemplateResponse(request, "subtitle.html", {"version": "test"})

    return app


@pytest.fixture
def client_subtitle(test_app_subtitle: FastAPI) -> TestClient:
    return TestClient(test_app_subtitle)


class TestAPISubtitle:
    """字幕添加 API 端点测试。"""

    @pytest.fixture
    def mock_settings(self):
        """Mock _get_settings 返回有效配置。"""
        from config import Settings

        settings = Settings.load()
        with mock.patch("web.api.subtitle._get_settings", return_value=settings):
            yield settings

    def test_get_subtitle_page(self, client_subtitle: TestClient) -> None:
        """GET /subtitle 返回 HTML 页面。"""
        response = client_subtitle.get("/subtitle")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_post_missing_all_params(self, client_subtitle: TestClient) -> None:
        """POST /api/subtitle/add 无参 → 400。"""
        response = client_subtitle.post("/api/subtitle/add")
        assert response.status_code == 400
        data = response.json()
        assert data["success"] is False

    def test_post_missing_language(self, client_subtitle: TestClient) -> None:
        """缺语言 → 400。"""
        response = client_subtitle.post("/api/subtitle/add", data={
            "video_path": "/tmp/video.mkv",
            "subtitle_path": "/tmp/sub.srt",
        })
        assert response.status_code == 400
        assert "语言" in response.json()["error"]

    def test_post_missing_video(self, client_subtitle: TestClient) -> None:
        """缺视频 → 400。"""
        response = client_subtitle.post("/api/subtitle/add", data={
            "subtitle_path": "/tmp/sub.srt",
            "language": "eng",
        })
        assert response.status_code == 400

    def test_post_missing_subtitle(self, client_subtitle: TestClient) -> None:
        """缺字幕 → 400。"""
        response = client_subtitle.post("/api/subtitle/add", data={
            "video_path": "/tmp/video.mkv",
            "language": "eng",
        })
        assert response.status_code == 400

    def test_post_path_mode_success(self, client_subtitle: TestClient, tmp_path: Path) -> None:
        """路径+路径 模式成功。"""
        video = tmp_path / "video.mkv"
        video.write_bytes(b"fake video")
        sub = tmp_path / "sub.srt"
        sub.write_bytes(b"1\n00:00:01,000 --> 00:00:02,000\nHi\n")
        output = tmp_path / "out_subtitled.mkv"

        with mock.patch("web.api.subtitle._get_settings") as mock_settings:
            settings_mock = mock.Mock()
            settings_mock.ffmpeg.executable = "ffmpeg"
            settings_mock.paths.media_output = tmp_path
            settings_mock.paths.temp_dir = tmp_path / "temp"
            settings_mock.paths.media_input = tmp_path
            mock_settings.return_value = settings_mock

            with mock.patch("web.api.subtitle._resolve_path") as mock_resolve:
                mock_resolve.side_effect = [video, sub]

                with mock.patch("processing.core.mux.add_subtitle") as mock_add:
                    mock_add.return_value = MuxResult(
                        input_video=video,
                        output_path=output,
                        output_size=5000,
                        subtitle_count=1,
                        added_track_index=0,
                        language="eng",
                    )
                    response = client_subtitle.post("/api/subtitle/add", data={
                        "video_path": str(video),
                        "subtitle_path": str(sub),
                        "language": "eng",
                    })
                    assert response.status_code == 200
                    data = response.json()
                    assert data["success"] is True
                    assert data["result"]["language"] == "eng"
                    assert "download_url" in data["result"]

    def test_post_video_not_found(self, client_subtitle: TestClient, tmp_path: Path) -> None:
        """视频不存在 → 422。"""
        sub = tmp_path / "sub.srt"
        sub.write_bytes(b"1\n00:00:01,000 --> 00:00:02,000\nHi\n")

        with mock.patch("web.api.subtitle._get_settings") as mock_settings:
            settings_mock = mock.Mock()
            settings_mock.ffmpeg.executable = "ffmpeg"
            settings_mock.paths.media_output = tmp_path
            settings_mock.paths.temp_dir = tmp_path / "temp"
            mock_settings.return_value = settings_mock

            with mock.patch("web.api.subtitle._resolve_path") as mock_resolve:
                mock_resolve.side_effect = [Path("/nonexistent/video.mkv"), sub]
                response = client_subtitle.post("/api/subtitle/add", data={
                    "video_path": "/nonexistent/video.mkv",
                    "subtitle_path": str(sub),
                    "language": "eng",
                })
                assert response.status_code == 422

    def test_post_unsupported_format(self, client_subtitle: TestClient, tmp_path: Path) -> None:
        """不支持的字幕格式 → 422。"""
        video = tmp_path / "video.mkv"
        video.write_bytes(b"fake video")
        sub = tmp_path / "notes.txt"
        sub.write_bytes(b"hello")

        with mock.patch("web.api.subtitle._get_settings") as mock_settings:
            settings_mock = mock.Mock()
            settings_mock.ffmpeg.executable = "ffmpeg"
            settings_mock.paths.media_output = tmp_path
            settings_mock.paths.temp_dir = tmp_path / "temp"
            mock_settings.return_value = settings_mock

            with mock.patch("web.api.subtitle._resolve_path") as mock_resolve:
                mock_resolve.side_effect = [video, sub]
                response = client_subtitle.post("/api/subtitle/add", data={
                    "video_path": str(video),
                    "subtitle_path": str(sub),
                    "language": "eng",
                })
                assert response.status_code == 422

    def test_post_htmx_returns_html(self, client_subtitle: TestClient, tmp_path: Path) -> None:
        """HX-Request 头 → 返回 HTML 片段。"""
        video = tmp_path / "video.mkv"
        video.write_bytes(b"fake video")
        sub = tmp_path / "sub.srt"
        sub.write_bytes(b"1\n00:00:01,000 --> 00:00:02,000\nHi\n")
        output = tmp_path / "out_subtitled.mkv"

        with mock.patch("web.api.subtitle._get_settings") as mock_settings:
            settings_mock = mock.Mock()
            settings_mock.ffmpeg.executable = "ffmpeg"
            settings_mock.paths.media_output = tmp_path
            settings_mock.paths.temp_dir = tmp_path / "temp"
            mock_settings.return_value = settings_mock

            with mock.patch("web.api.subtitle._resolve_path") as mock_resolve:
                mock_resolve.side_effect = [video, sub]

                with mock.patch("processing.core.mux.add_subtitle") as mock_add:
                    mock_add.return_value = MuxResult(
                        input_video=video,
                        output_path=output,
                        output_size=5000,
                        subtitle_count=1,
                        added_track_index=0,
                        language="eng",
                    )
                    response = client_subtitle.post(
                        "/api/subtitle/add",
                        data={
                            "video_path": str(video),
                            "subtitle_path": str(sub),
                            "language": "eng",
                        },
                        headers={"HX-Request": "true"},
                    )
                    assert response.status_code == 200
                    assert "text/html" in response.headers["content-type"]
                    assert "添加成功" in response.text

    def test_post_htmx_error_returns_html(self, client_subtitle: TestClient, tmp_path: Path) -> None:
        """HX-Request 头 + 错误 → HTML 错误片段。"""
        video = tmp_path / "video.mkv"
        video.write_bytes(b"fake video")
        sub = tmp_path / "sub.srt"
        sub.write_bytes(b"1\n00:00:01,000 --> 00:00:02,000\nHi\n")

        with mock.patch("web.api.subtitle._get_settings") as mock_settings:
            settings_mock = mock.Mock()
            settings_mock.ffmpeg.executable = "ffmpeg"
            settings_mock.paths.media_output = tmp_path
            settings_mock.paths.temp_dir = tmp_path / "temp"
            mock_settings.return_value = settings_mock

            with mock.patch("web.api.subtitle._resolve_path") as mock_resolve:
                mock_resolve.side_effect = [Path("/nonexistent/video.mkv"), sub]
                response = client_subtitle.post(
                    "/api/subtitle/add",
                    data={
                        "video_path": "/nonexistent/video.mkv",
                        "subtitle_path": str(sub),
                        "language": "eng",
                    },
                    headers={"HX-Request": "true"},
                )
                assert response.status_code == 422
                assert "text/html" in response.headers["content-type"]
                assert "失败" in response.text

    def test_download_success(self, client_subtitle: TestClient, tmp_path: Path) -> None:
        """GET /api/subtitle/download → 文件下载成功。"""
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        test_file = output_dir / "movie_subtitled.mkv"
        test_file.write_bytes(b"fake mkv data")

        with mock.patch("web.api.subtitle._get_settings") as mock_settings:
            settings_mock = mock.Mock()
            settings_mock.paths.media_output = output_dir.resolve()
            mock_settings.return_value = settings_mock

            response = client_subtitle.get(
                "/api/subtitle/download",
                params={"path": str(output_dir.resolve() / "movie_subtitled.mkv")},
            )
            assert response.status_code == 200
            assert response.content == b"fake mkv data"

    def test_download_file_not_found(self, client_subtitle: TestClient, tmp_path: Path) -> None:
        """文件不存在 → 404。"""
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        with mock.patch("web.api.subtitle._get_settings") as mock_settings:
            settings_mock = mock.Mock()
            settings_mock.paths.media_output = output_dir.resolve()
            mock_settings.return_value = settings_mock

            response = client_subtitle.get(
                "/api/subtitle/download",
                params={"path": str(output_dir.resolve() / "nope.mkv")},
            )
            assert response.status_code == 404

    def test_download_path_traversal(self, client_subtitle: TestClient, tmp_path: Path) -> None:
        """路径穿越攻击 → 403。"""
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        with mock.patch("web.api.subtitle._get_settings") as mock_settings:
            settings_mock = mock.Mock()
            settings_mock.paths.media_output = output_dir.resolve()
            mock_settings.return_value = settings_mock

            response = client_subtitle.get(
                "/api/subtitle/download",
                params={"path": str(output_dir.parent / "secret.txt")},
            )
            assert response.status_code == 403

    def test_post_invalid_container(self, client_subtitle: TestClient, tmp_path: Path) -> None:
        """不支持的容器格式 → 400。"""
        video = tmp_path / "video.mkv"
        video.write_bytes(b"fake video")
        sub = tmp_path / "sub.srt"
        sub.write_bytes(b"1\n00:00:01,000 --> 00:00:02,000\nHi\n")

        with mock.patch("web.api.subtitle._get_settings") as mock_settings:
            settings_mock = mock.Mock()
            settings_mock.paths.media_output = tmp_path
            settings_mock.paths.temp_dir = tmp_path / "temp"
            mock_settings.return_value = settings_mock

            with mock.patch("web.api.subtitle._resolve_path") as mock_resolve:
                mock_resolve.side_effect = [video, sub]
                response = client_subtitle.post("/api/subtitle/add", data={
                    "video_path": str(video),
                    "subtitle_path": str(sub),
                    "language": "eng",
                    "container": "avi",
                })
                assert response.status_code == 400
