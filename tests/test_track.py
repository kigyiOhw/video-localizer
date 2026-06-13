"""默认轨道切换模块测试。"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from processing.core.mux import (
    MuxError,
    SwitchDefaultResult,
    _build_switch_default_args,
    switch_default_track,
)

FAKE_VIDEO = Path("/tmp/video.mkv")


# ---------------------------------------------------------------------------
# 测试数据构造
# ---------------------------------------------------------------------------


def _make_probe_result(video_count=1, audio_count=2, subtitle_count=1):
    """创建包含 disposition 的 ProbeResult。"""
    from processing.core.probe import (
        AudioStream,
        FormatInfo,
        ProbeResult,
        SubtitleStream,
        VideoStream,
    )

    video_streams = [
        VideoStream(
            index=i, codec="h264", codec_long="H.264", codec_type="video",
            language=None, disposition={"default": 1 if i == 0 else 0},
            width=1920, height=1080,
        )
        for i in range(video_count)
    ]
    audio_streams = [
        AudioStream(
            index=video_count + i, codec="aac", codec_long="AAC", codec_type="audio",
            language="jpn" if i == 0 else "eng",
            disposition={"default": 1 if i == 0 else 0},
            sample_rate=48000, channels=2,
        )
        for i in range(audio_count)
    ]
    subtitle_streams = [
        SubtitleStream(
            index=video_count + audio_count + i,
            codec="subrip", codec_long="SubRip", codec_type="subtitle",
            language="zho", disposition={"default": 1 if i == 0 else 0},
        )
        for i in range(subtitle_count)
    ]

    return ProbeResult(
        format=FormatInfo(filename="video.mkv", format_name="matroska", duration=120.0),
        video_streams=video_streams,
        audio_streams=audio_streams,
        subtitle_streams=subtitle_streams,
    )


# ---------------------------------------------------------------------------
# TestBuildSwitchDefaultArgs
# ---------------------------------------------------------------------------


class TestBuildSwitchDefaultArgs:
    """_build_switch_default_args 命令构建测试。"""

    def test_audio_default_second_track(self, tmp_path: Path) -> None:
        """将第二条音轨设为默认，第一条设为 none。"""
        out = tmp_path / "out.mkv"
        cmd = _build_switch_default_args(
            video_path=tmp_path / "in.mkv",
            output_path=out,
            stream_type="audio",
            stream_index=1,
            stream_count=2,
            ffmpeg_path="ffmpeg",
            overwrite=True,
        )
        assert cmd[0] == "ffmpeg"
        assert "-y" in cmd
        assert "-map" in cmd and "0" in cmd
        assert "-c" in cmd and "copy" in cmd
        assert "-disposition:a:0" in cmd
        assert cmd[cmd.index("-disposition:a:0") + 1] == "none"
        assert "-disposition:a:1" in cmd
        assert cmd[cmd.index("-disposition:a:1") + 1] == "default"
        assert str(out) in cmd

    def test_subtitle_default_first_track(self, tmp_path: Path) -> None:
        """将第一条字幕轨设为默认。"""
        out = tmp_path / "out.mkv"
        cmd = _build_switch_default_args(
            video_path=tmp_path / "in.mkv",
            output_path=out,
            stream_type="subtitle",
            stream_index=0,
            stream_count=1,
            ffmpeg_path="ffmpeg",
            overwrite=False,
        )
        assert "-n" in cmd
        assert "-disposition:s:0" in cmd
        assert cmd[cmd.index("-disposition:s:0") + 1] == "default"

    def test_video_selector(self, tmp_path: Path) -> None:
        """视频流使用 v: 选择器。"""
        out = tmp_path / "out.mkv"
        cmd = _build_switch_default_args(
            video_path=tmp_path / "in.mkv",
            output_path=out,
            stream_type="video",
            stream_index=0,
            stream_count=1,
            ffmpeg_path="ffmpeg",
            overwrite=True,
        )
        assert "-disposition:v:0" in cmd


# ---------------------------------------------------------------------------
# TestSwitchDefaultTrack
# ---------------------------------------------------------------------------


class TestSwitchDefaultTrack:
    """switch_default_track 核心函数测试。"""

    def test_switch_audio_default_success(self, tmp_path: Path) -> None:
        """成功切换默认音轨。"""
        video = tmp_path / "video.mkv"
        video.write_bytes(b"fake")
        output_dir = tmp_path / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        expected_output = output_dir / "video_default_audio_1.mkv"

        probe_result = _make_probe_result()

        with (
            mock.patch("processing.core.mux._get_streams_of_type", return_value=probe_result.audio_streams),
            mock.patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            # ffmpeg 成功后会检查输出文件存在，提前创建
            expected_output.write_bytes(b"x" * 5000)

            result = switch_default_track(
                video_path=video,
                stream_type="audio",
                stream_index=1,
                ffmpeg_path="ffmpeg",
                overwrite=True,
            )

        assert isinstance(result, SwitchDefaultResult)
        assert result.stream_type == "audio"
        assert result.stream_index == 1
        assert result.output_path == expected_output
        assert result.output_size == 5000
        assert len(result.changed_tracks) == 2
        assert result.changed_tracks[0]["disposition"] == "none"
        assert result.changed_tracks[1]["disposition"] == "default"

    def test_invalid_stream_type(self, tmp_path: Path) -> None:
        """无效流类型 → MuxError。"""
        video = tmp_path / "video.mkv"
        video.write_bytes(b"fake")
        with pytest.raises(MuxError, match="不支持的流类型"):
            switch_default_track(video, "data", 0)

    def test_stream_index_out_of_range(self, tmp_path: Path) -> None:
        """流序号越界 → MuxError。"""
        video = tmp_path / "video.mkv"
        video.write_bytes(b"fake")
        probe_result = _make_probe_result(audio_count=2)

        with mock.patch(
            "processing.core.mux._get_streams_of_type",
            return_value=probe_result.audio_streams,
        ):
            with pytest.raises(MuxError, match="不存在"):
                switch_default_track(video, "audio", 99)

    def test_file_not_found(self) -> None:
        """输入文件不存在 → MuxError。"""
        with pytest.raises(MuxError, match="不存在"):
            switch_default_track(Path("/nonexistent/video.mkv"), "audio", 0)

    def test_ffmpeg_failure(self, tmp_path: Path) -> None:
        """ffmpeg 返回非零 → MuxError。"""
        video = tmp_path / "video.mkv"
        video.write_bytes(b"fake")
        probe_result = _make_probe_result(audio_count=2)

        with (
            mock.patch(
                "processing.core.mux._get_streams_of_type",
                return_value=probe_result.audio_streams,
            ),
            mock.patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = mock.Mock(returncode=1, stdout="", stderr="codec error")
            with pytest.raises(MuxError, match="ffmpeg 返回非零"):
                switch_default_track(video, "audio", 1, overwrite=True)

    def test_output_exists_no_overwrite(self, tmp_path: Path) -> None:
        """输出文件已存在且 overwrite=False → MuxError。"""
        video = tmp_path / "video.mkv"
        video.write_bytes(b"fake")
        output_dir = tmp_path / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        expected_output = output_dir / "video_default_audio_0.mkv"
        expected_output.write_bytes(b"x")

        probe_result = _make_probe_result(audio_count=2)

        with mock.patch(
            "processing.core.mux._get_streams_of_type",
            return_value=probe_result.audio_streams,
        ):
            with pytest.raises(MuxError, match="输出文件已存在"):
                switch_default_track(video, "audio", 0, overwrite=False)


# ---------------------------------------------------------------------------
# TestAPITrack
# ---------------------------------------------------------------------------


@pytest.fixture
def test_app_track() -> FastAPI:
    """创建独立的测试 FastAPI app。"""
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
    import web.api.track as track_module

    def _test_settings():
        cfg = umock.Mock()
        cfg.paths.media_input = Path("/tmp")
        cfg.paths.media_output = Path("/tmp")
        cfg.paths.temp_dir = Path("/tmp")
        cfg.ffmpeg.executable = "ffmpeg"
        cfg.ffmpeg.ffprobe_executable = "ffprobe"
        return cfg

    probe_module._get_settings = _test_settings
    extract_module._get_settings = _test_settings
    subtitle_module._get_settings = _test_settings
    pipeline_module._get_settings = _test_settings
    track_module._get_settings = _test_settings

    @app.get("/track", response_model=None)
    async def track_page(request: Request):
        return templates.TemplateResponse(request, "track.html", {"version": "test"})

    return app


@pytest.fixture
def client_track(test_app_track: FastAPI) -> TestClient:
    return TestClient(test_app_track)


class TestAPITrack:
    """轨道切换 API 测试。"""

    def test_get_track_page(self, client_track: TestClient) -> None:
        """GET /track 返回 HTML。"""
        resp = client_track.get("/track")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_post_missing_params(self, client_track: TestClient) -> None:
        """POST 缺参数 → 400。"""
        resp = client_track.post("/api/track/default")
        assert resp.status_code == 400
        data = resp.json()
        assert data["success"] is False

    def test_post_invalid_container(self, client_track: TestClient) -> None:
        """不支持容器 → 400。"""
        resp = client_track.post("/api/track/default", data={
            "file_path": "/tmp/video.mkv",
            "stream_type": "audio",
            "stream_index": "0",
            "container": "avi",
        })
        assert resp.status_code == 400

    def test_post_disallowed_path(self, client_track: TestClient) -> None:
        """不允许的绝对路径 → 403。"""
        resp = client_track.post("/api/track/default", data={
            "file_path": "/etc/passwd",
            "stream_type": "audio",
            "stream_index": "0",
        })
        assert resp.status_code == 403
        assert resp.json()["success"] is False

    def test_post_success_json(self, client_track: TestClient, tmp_path: Path) -> None:
        """POST 成功 → JSON。"""
        video = tmp_path / "video.mkv"
        video.write_bytes(b"fake")

        fake_result = SwitchDefaultResult(
            input_video=video,
            output_path=tmp_path / "video_default_audio_1.mkv",
            output_size=1024,
            stream_type="audio",
            stream_index=1,
            changed_tracks=[
                {"type": "audio", "index": 0, "disposition": "none"},
                {"type": "audio", "index": 1, "disposition": "default"},
            ],
        )

        with mock.patch(
            "processing.core.mux.switch_default_track", return_value=fake_result
        ):
            resp = client_track.post("/api/track/default", data={
                "file_path": str(video),
                "stream_type": "audio",
                "stream_index": "1",
            })

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["result"]["stream_type"] == "audio"
        assert data["result"]["stream_index"] == 1
        assert "download_url" in data["result"]

    def test_post_htmx_returns_html(self, client_track: TestClient, tmp_path: Path) -> None:
        """HX-Request 头 → HTML。"""
        video = tmp_path / "video.mkv"
        video.write_bytes(b"fake")

        fake_result = SwitchDefaultResult(
            input_video=video,
            output_path=tmp_path / "video_default_subtitle_0.mkv",
            output_size=2048,
            stream_type="subtitle",
            stream_index=0,
            changed_tracks=[
                {"type": "subtitle", "index": 0, "disposition": "default"},
            ],
        )

        with mock.patch(
            "processing.core.mux.switch_default_track", return_value=fake_result
        ):
            resp = client_track.post(
                "/api/track/default",
                data={"file_path": str(video), "stream_type": "subtitle", "stream_index": "0"},
                headers={"HX-Request": "true"},
            )

        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "默认轨道切换成功" in resp.text

    def test_post_engine_error(self, client_track: TestClient, tmp_path: Path) -> None:
        """核心函数抛 MuxError → 422。"""
        video = tmp_path / "video.mkv"
        video.write_bytes(b"fake")

        with mock.patch(
            "processing.core.mux.switch_default_track",
            side_effect=MuxError("音频流 #99 不存在（共 2 个）"),
        ):
            resp = client_track.post("/api/track/default", data={
                "file_path": str(video),
                "stream_type": "audio",
                "stream_index": "99",
            })

        assert resp.status_code == 422
        assert resp.json()["success"] is False

    def test_download_path_traversal(self, client_track: TestClient, tmp_path: Path) -> None:
        """下载路径穿越 → 403。"""
        resp = client_track.get("/api/track/download?path=../secret.txt")
        assert resp.status_code == 403

    def test_download_file_not_found(self, client_track: TestClient, tmp_path: Path) -> None:
        """下载文件不存在 → 404。"""
        resp = client_track.get("/api/track/download?path=/tmp/nonexistent.mkv")
        assert resp.status_code == 404
