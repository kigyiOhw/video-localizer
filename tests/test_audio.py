"""音频轨管理模块测试。"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from processing.core.audio import (
    AudioTrackError,
    AudioTrackResult,
    _build_add_audio_args,
    _build_mute_args,
    _build_remove_audio_args,
    _build_replace_audio_args,
    _build_speed_args,
    _build_sync_args,
    _chain_atempo,
    add_audio_track,
    adjust_audio_speed,
    adjust_audio_sync,
    mute_audio_track,
    remove_audio_track,
    replace_audio_track,
)

FAKE_VIDEO = Path("/tmp/video.mkv")
FAKE_AUDIO = Path("/tmp/audio.m4a")


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
# TestBuildAddAudioArgs
# ---------------------------------------------------------------------------


class TestBuildAddAudioArgs:
    """_build_add_audio_args 命令构建测试。"""

    def test_mkv_success(self, tmp_path: Path) -> None:
        """MKV 追加音频命令。"""
        out = tmp_path / "out.mkv"
        cmd = _build_add_audio_args(
            video_path=tmp_path / "in.mkv",
            audio_path=tmp_path / "in.m4a",
            output_path=out,
            language="jpn",
            existing_audio_count=2,
            set_default=True,
            ffmpeg_path="ffmpeg",
            overwrite=True,
        )
        assert cmd[0] == "ffmpeg"
        assert "-y" in cmd
        assert "-map" in cmd and "0" in cmd
        assert "-map" in cmd and "1" in cmd
        assert "-c" in cmd and "copy" in cmd
        assert "-metadata:s:a:2" in cmd
        assert cmd[cmd.index("-metadata:s:a:2") + 1] == "language=jpn"
        assert "-disposition:a:2" in cmd
        assert cmd[cmd.index("-disposition:a:2") + 1] == "default"
        assert str(out) in cmd

    def test_no_default(self, tmp_path: Path) -> None:
        """不设置默认时不应出现 disposition 参数。"""
        out = tmp_path / "out.mkv"
        cmd = _build_add_audio_args(
            video_path=tmp_path / "in.mkv",
            audio_path=tmp_path / "in.m4a",
            output_path=out,
            language="eng",
            existing_audio_count=1,
            set_default=False,
            ffmpeg_path="ffmpeg",
            overwrite=False,
        )
        assert "-n" in cmd
        assert "-disposition" not in " ".join(cmd)


# ---------------------------------------------------------------------------
# TestBuildReplaceAudioArgs
# ---------------------------------------------------------------------------


class TestBuildReplaceAudioArgs:
    """_build_replace_audio_args 命令构建测试。"""

    def test_replace_second_of_three(self, tmp_path: Path) -> None:
        """3 条音轨中替换第 2 条（index=1）。"""
        out = tmp_path / "out.mkv"
        cmd = _build_replace_audio_args(
            video_path=tmp_path / "in.mkv",
            audio_path=tmp_path / "new.m4a",
            output_path=out,
            audio_index=1,
            language="chi",
            video_count=1,
            audio_count=3,
            subtitle_count=1,
            ffmpeg_path="ffmpeg",
            overwrite=True,
        )
        assert "-map" in cmd and "0:v" in cmd
        assert "-map" in cmd and "0:a:0" in cmd
        assert "-map" in cmd and "0:a:1" not in cmd
        assert "-map" in cmd and "1:a" in cmd
        assert "-map" in cmd and "0:a:2" in cmd
        assert "-map" in cmd and "0:s" in cmd
        assert "-metadata:s:a:1" in cmd
        assert cmd[cmd.index("-metadata:s:a:1") + 1] == "language=chi"

    def test_replace_only_audio(self, tmp_path: Path) -> None:
        """只有 1 条音轨时替换它。"""
        out = tmp_path / "out.mkv"
        cmd = _build_replace_audio_args(
            video_path=tmp_path / "in.mkv",
            audio_path=tmp_path / "new.m4a",
            output_path=out,
            audio_index=0,
            language="eng",
            video_count=1,
            audio_count=1,
            subtitle_count=0,
            ffmpeg_path="ffmpeg",
            overwrite=True,
        )
        assert "-map" in cmd and "0:v" in cmd
        assert "-map" in cmd and "0:a:0" not in cmd
        assert "-map" in cmd and "1:a" in cmd


# ---------------------------------------------------------------------------
# TestBuildRemoveAudioArgs
# ---------------------------------------------------------------------------


class TestBuildRemoveAudioArgs:
    """_build_remove_audio_args 命令构建测试。"""

    def test_remove_second_of_three(self, tmp_path: Path) -> None:
        """3 条音轨中移除第 2 条。"""
        out = tmp_path / "out.mkv"
        cmd = _build_remove_audio_args(
            video_path=tmp_path / "in.mkv",
            output_path=out,
            audio_index=1,
            video_count=1,
            audio_count=3,
            subtitle_count=1,
            ffmpeg_path="ffmpeg",
            overwrite=True,
        )
        assert "-map" in cmd and "0:v" in cmd
        assert "-map" in cmd and "0:a:0" in cmd
        assert "-map" in cmd and "0:a:1" not in cmd
        assert "-map" in cmd and "0:a:2" in cmd
        assert "-map" in cmd and "0:s" in cmd

    def test_remove_only_audio(self, tmp_path: Path) -> None:
        """只有 1 条音轨时移除后只剩视频和字幕。"""
        out = tmp_path / "out.mkv"
        cmd = _build_remove_audio_args(
            video_path=tmp_path / "in.mkv",
            output_path=out,
            audio_index=0,
            video_count=1,
            audio_count=1,
            subtitle_count=1,
            ffmpeg_path="ffmpeg",
            overwrite=True,
        )
        audio_maps = [c for c in cmd if c.startswith("0:a:")]
        assert audio_maps == []


# ---------------------------------------------------------------------------
# TestBuildMuteArgs
# ---------------------------------------------------------------------------


class TestBuildMuteArgs:
    """_build_mute_args 命令构建测试。"""

    def test_mute_single_track(self, tmp_path: Path) -> None:
        """单条音轨静音。"""
        out = tmp_path / "out.mkv"
        cmd = _build_mute_args(
            video_path=tmp_path / "in.mkv",
            output_path=out,
            audio_index=1,
            video_count=1,
            audio_count=3,
            subtitle_count=1,
            ffmpeg_path="ffmpeg",
            overwrite=True,
        )
        assert "-filter_complex" in cmd
        idx = cmd.index("-filter_complex")
        assert "[0:a:1]volume=0[m1]" in cmd[idx + 1]
        assert "-map" in cmd and "[m1]" in cmd

    def test_mute_all_tracks(self, tmp_path: Path) -> None:
        """全部音轨静音。"""
        out = tmp_path / "out.mkv"
        cmd = _build_mute_args(
            video_path=tmp_path / "in.mkv",
            output_path=out,
            audio_index=None,
            video_count=1,
            audio_count=2,
            subtitle_count=1,
            ffmpeg_path="ffmpeg",
            overwrite=True,
        )
        assert "volume=0" in " ".join(cmd)
        assert " ".join(cmd).count("volume=0") == 2


# ---------------------------------------------------------------------------
# TestBuildSyncArgs
# ---------------------------------------------------------------------------


class TestBuildSyncArgs:
    """_build_sync_args 命令构建测试。"""

    def test_delay_specific_track(self, tmp_path: Path) -> None:
        """延迟指定音轨。"""
        out = tmp_path / "out.mkv"
        cmd = _build_sync_args(
            video_path=tmp_path / "in.mkv",
            output_path=out,
            offset_seconds=0.5,
            audio_index=1,
            video_count=1,
            audio_count=3,
            subtitle_count=1,
            ffmpeg_path="ffmpeg",
            overwrite=True,
        )
        assert cmd[0] == "ffmpeg"
        assert "-itsoffset" in cmd
        idx = cmd.index("-itsoffset")
        assert cmd[idx + 1] == "0.5"
        # 第二路输入带偏移
        input_indices = [i for i, c in enumerate(cmd) if c == "-i"]
        assert len(input_indices) == 2
        # 目标音频从第二路输入取
        assert "1:a:1" in cmd
        # 其他音频从第一路取
        assert "0:a:0" in cmd
        assert "0:a:2" in cmd

    def test_advance_all_audio(self, tmp_path: Path) -> None:
        """提前所有音频。"""
        out = tmp_path / "out.mkv"
        cmd = _build_sync_args(
            video_path=tmp_path / "in.mkv",
            output_path=out,
            offset_seconds=-0.2,
            audio_index=None,
            video_count=1,
            audio_count=2,
            subtitle_count=1,
            ffmpeg_path="ffmpeg",
            overwrite=True,
        )
        assert "-itsoffset" in cmd
        idx = cmd.index("-itsoffset")
        assert cmd[idx + 1] == "-0.2"
        assert "1:a" in cmd


# ---------------------------------------------------------------------------
# TestBuildSpeedArgs
# ---------------------------------------------------------------------------


class TestBuildSpeedArgs:
    """_build_speed_args 命令构建测试。"""

    def test_speed_1_05(self, tmp_path: Path) -> None:
        """速度 1.05。"""
        out = tmp_path / "out.mkv"
        cmd = _build_speed_args(
            video_path=tmp_path / "in.mkv",
            output_path=out,
            speed_ratio=1.05,
            audio_index=0,
            video_count=1,
            audio_count=2,
            subtitle_count=1,
            ffmpeg_path="ffmpeg",
            overwrite=True,
        )
        assert "-filter_complex" in cmd
        idx = cmd.index("-filter_complex")
        assert "atempo=1.05" in cmd[idx + 1]
        assert "-map" in cmd and "[s0]" in cmd

    def test_speed_2_5(self, tmp_path: Path) -> None:
        """速度 2.5 需要链式 atempo。"""
        out = tmp_path / "out.mkv"
        cmd = _build_speed_args(
            video_path=tmp_path / "in.mkv",
            output_path=out,
            speed_ratio=2.5,
            audio_index=0,
            video_count=1,
            audio_count=1,
            subtitle_count=0,
            ffmpeg_path="ffmpeg",
            overwrite=True,
        )
        idx = cmd.index("-filter_complex")
        assert "atempo=2.0,atempo=1.25" in cmd[idx + 1]

    def test_speed_0_3(self, tmp_path: Path) -> None:
        """速度 0.3 需要链式 atempo。"""
        out = tmp_path / "out.mkv"
        cmd = _build_speed_args(
            video_path=tmp_path / "in.mkv",
            output_path=out,
            speed_ratio=0.3,
            audio_index=0,
            video_count=1,
            audio_count=1,
            subtitle_count=0,
            ffmpeg_path="ffmpeg",
            overwrite=True,
        )
        idx = cmd.index("-filter_complex")
        assert "atempo=0.5,atempo=0.6" in cmd[idx + 1]


# ---------------------------------------------------------------------------
# TestChainAtempo
# ---------------------------------------------------------------------------


class TestChainAtempo:
    """_chain_atempo 滤镜链计算测试。"""

    @pytest.mark.parametrize(
        ("ratio", "expected"),
        [
            (1.0, "atempo=1.0"),
            (1.05, "atempo=1.05"),
            (2.0, "atempo=2.0"),
            (0.5, "atempo=0.5"),
            (2.5, "atempo=2.0,atempo=1.25"),
            (0.3, "atempo=0.5,atempo=0.6"),
            (4.0, "atempo=2.0,atempo=2.0"),
        ],
    )
    def test_chain(self, ratio: float, expected: str) -> None:
        assert _chain_atempo(ratio) == expected


# ---------------------------------------------------------------------------
# TestAddAudioTrack
# ---------------------------------------------------------------------------


class TestAddAudioTrack:
    """add_audio_track 核心函数测试。"""

    def test_success(self, tmp_path: Path) -> None:
        """成功追加音轨。"""
        video = tmp_path / "video.mkv"
        video.write_bytes(b"fake")
        audio = tmp_path / "audio.m4a"
        audio.write_bytes(b"audio")
        output_dir = tmp_path / "output"
        expected_output = output_dir / "video_added_audio.mkv"
        probe_result = _make_probe_result(audio_count=2)

        with (
            mock.patch("processing.core.audio._get_audio_streams", return_value=probe_result.audio_streams),
            mock.patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            expected_output.write_bytes(b"x" * 5000)

            result = add_audio_track(
                video_path=video,
                audio_path=audio,
                language="jpn",
                set_default=True,
                overwrite=True,
            )

        assert isinstance(result, AudioTrackResult)
        assert result.operation == "add"
        assert result.output_path == expected_output
        assert result.output_size == 5000
        assert result.extra["added_index"] == 2
        assert result.extra["language"] == "jpn"

    def test_video_not_found(self, tmp_path: Path) -> None:
        """视频不存在 → AudioTrackError。"""
        with pytest.raises(AudioTrackError, match="视频文件不存在"):
            add_audio_track(tmp_path / "no.mkv", tmp_path / "a.m4a")

    def test_audio_not_found(self, tmp_path: Path) -> None:
        """音频不存在 → AudioTrackError。"""
        video = tmp_path / "video.mkv"
        video.write_bytes(b"fake")
        with pytest.raises(AudioTrackError, match="音频文件不存在"):
            add_audio_track(video, tmp_path / "no.m4a")

    def test_invalid_container(self, tmp_path: Path) -> None:
        """不支持容器 → AudioTrackError。"""
        video = tmp_path / "video.mkv"
        video.write_bytes(b"fake")
        audio = tmp_path / "audio.m4a"
        audio.write_bytes(b"audio")
        with pytest.raises(AudioTrackError, match="不支持的容器格式"):
            add_audio_track(video, audio, container="avi")

    def test_ffmpeg_failure(self, tmp_path: Path) -> None:
        """ffmpeg 失败 → AudioTrackError。"""
        video = tmp_path / "video.mkv"
        video.write_bytes(b"fake")
        audio = tmp_path / "audio.m4a"
        audio.write_bytes(b"audio")
        probe_result = _make_probe_result(audio_count=2)

        with (
            mock.patch("processing.core.audio._get_audio_streams", return_value=probe_result.audio_streams),
            mock.patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = mock.Mock(returncode=1, stdout="", stderr="codec error")
            with pytest.raises(AudioTrackError, match="ffmpeg 返回非零"):
                add_audio_track(video, audio, overwrite=True)


# ---------------------------------------------------------------------------
# TestReplaceAudioTrack
# ---------------------------------------------------------------------------


class TestReplaceAudioTrack:
    """replace_audio_track 核心函数测试。"""

    def test_success(self, tmp_path: Path) -> None:
        """成功替换音轨。"""
        video = tmp_path / "video.mkv"
        video.write_bytes(b"fake")
        audio = tmp_path / "audio.m4a"
        audio.write_bytes(b"audio")
        output_dir = tmp_path / "output"
        expected_output = output_dir / "video_replaced_audio_1.mkv"

        probe_result = _make_probe_result(audio_count=3)

        with (
            mock.patch("processing.core.audio._probe_video", return_value=probe_result),
            mock.patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            expected_output.write_bytes(b"x" * 5000)

            result = replace_audio_track(
                video_path=video,
                audio_path=audio,
                audio_index=1,
                language="chi",
                overwrite=True,
            )

        assert result.operation == "replace"
        assert result.audio_index == 1
        assert result.output_path == expected_output

    def test_index_out_of_range(self, tmp_path: Path) -> None:
        """索引越界 → AudioTrackError。"""
        video = tmp_path / "video.mkv"
        video.write_bytes(b"fake")
        audio = tmp_path / "audio.m4a"
        audio.write_bytes(b"audio")
        probe_result = _make_probe_result(audio_count=2)

        with mock.patch(
            "processing.core.audio._probe_video",
            return_value=probe_result,
        ):
            with pytest.raises(AudioTrackError, match="音频流 #5 不存在"):
                replace_audio_track(video, audio, audio_index=5)

    def test_no_audio_streams(self, tmp_path: Path) -> None:
        """没有音频流时替换退化为追加。"""
        video = tmp_path / "video.mkv"
        video.write_bytes(b"fake")
        audio = tmp_path / "audio.m4a"
        audio.write_bytes(b"audio")
        probe_result = _make_probe_result(audio_count=0)

        with (
            mock.patch("processing.core.audio._probe_video", return_value=probe_result),
            mock.patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            output_dir = tmp_path / "output"
            expected_output = output_dir / "video_replaced_audio_0.mkv"
            expected_output.write_bytes(b"x" * 5000)

            result = replace_audio_track(video, audio, audio_index=0, overwrite=True)

        assert result.operation == "replace"
        assert result.audio_index == 0


# ---------------------------------------------------------------------------
# TestRemoveAudioTrack
# ---------------------------------------------------------------------------


class TestRemoveAudioTrack:
    """remove_audio_track 核心函数测试。"""

    def test_success(self, tmp_path: Path) -> None:
        """成功移除音轨。"""
        video = tmp_path / "video.mkv"
        video.write_bytes(b"fake")
        output_dir = tmp_path / "output"
        expected_output = output_dir / "video_removed_audio_1.mkv"
        probe_result = _make_probe_result(audio_count=3)

        with (
            mock.patch("processing.core.audio._probe_video", return_value=probe_result),
            mock.patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            expected_output.write_bytes(b"x" * 5000)

            result = remove_audio_track(video, audio_index=1, overwrite=True)

        assert result.operation == "remove"
        assert result.audio_index == 1
        assert result.extra["remaining"] == 2

    def test_index_out_of_range(self, tmp_path: Path) -> None:
        """索引越界 → AudioTrackError。"""
        video = tmp_path / "video.mkv"
        video.write_bytes(b"fake")
        probe_result = _make_probe_result(audio_count=2)

        with mock.patch(
            "processing.core.audio._probe_video",
            return_value=probe_result,
        ):
            with pytest.raises(AudioTrackError, match="不存在"):
                remove_audio_track(video, audio_index=9)


# ---------------------------------------------------------------------------
# TestMuteAudioTrack
# ---------------------------------------------------------------------------


class TestMuteAudioTrack:
    """mute_audio_track 核心函数测试。"""

    def test_success(self, tmp_path: Path) -> None:
        """成功静音单条音轨。"""
        video = tmp_path / "video.mkv"
        video.write_bytes(b"fake")
        output_dir = tmp_path / "output"
        expected_output = output_dir / "video_muted_audio_1.mkv"
        probe_result = _make_probe_result(audio_count=3)

        with (
            mock.patch("processing.core.audio._probe_video", return_value=probe_result),
            mock.patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            expected_output.write_bytes(b"x" * 5000)

            result = mute_audio_track(video, audio_index=1, overwrite=True)

        assert result.operation == "mute"
        assert result.audio_index == 1

    def test_all_tracks(self, tmp_path: Path) -> None:
        """全部静音。"""
        video = tmp_path / "video.mkv"
        video.write_bytes(b"fake")
        output_dir = tmp_path / "output"
        expected_output = output_dir / "video_muted_audio.mkv"
        probe_result = _make_probe_result(audio_count=2)

        with (
            mock.patch("processing.core.audio._probe_video", return_value=probe_result),
            mock.patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            expected_output.write_bytes(b"x" * 5000)

            result = mute_audio_track(video, audio_index=None, overwrite=True)

        assert result.operation == "mute"
        assert result.audio_index is None


# ---------------------------------------------------------------------------
# TestAdjustAudioSync
# ---------------------------------------------------------------------------


class TestAdjustAudioSync:
    """adjust_audio_sync 核心函数测试。"""

    def test_delay_success(self, tmp_path: Path) -> None:
        """成功延迟音频。"""
        video = tmp_path / "video.mkv"
        video.write_bytes(b"fake")
        output_dir = tmp_path / "output"
        expected_output = output_dir / "video_synced_audio_1_+0.5.mkv"
        probe_result = _make_probe_result(audio_count=3)

        with (
            mock.patch("processing.core.audio._probe_video", return_value=probe_result),
            mock.patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            expected_output.write_bytes(b"x" * 5000)

            result = adjust_audio_sync(video, offset_seconds=0.5, audio_index=1, overwrite=True)

        assert result.operation == "sync"
        assert result.audio_index == 1
        assert result.extra["offset_seconds"] == 0.5

    def test_all_audio(self, tmp_path: Path) -> None:
        """调整所有音频同步。"""
        video = tmp_path / "video.mkv"
        video.write_bytes(b"fake")
        output_dir = tmp_path / "output"
        expected_output = output_dir / "video_synced_audio_-0.2.mkv"
        probe_result = _make_probe_result(audio_count=2)

        with (
            mock.patch("processing.core.audio._probe_video", return_value=probe_result),
            mock.patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            expected_output.write_bytes(b"x" * 5000)

            result = adjust_audio_sync(video, offset_seconds=-0.2, audio_index=None, overwrite=True)

        assert result.audio_index is None
        assert result.extra["offset_seconds"] == -0.2


# ---------------------------------------------------------------------------
# TestAdjustAudioSpeed
# ---------------------------------------------------------------------------


class TestAdjustAudioSpeed:
    """adjust_audio_speed 核心函数测试。"""

    def test_success(self, tmp_path: Path) -> None:
        """成功调整速度。"""
        video = tmp_path / "video.mkv"
        video.write_bytes(b"fake")
        output_dir = tmp_path / "output"
        expected_output = output_dir / "video_speed_1.05_audio_0.mkv"
        probe_result = _make_probe_result(audio_count=2)

        with (
            mock.patch("processing.core.audio._probe_video", return_value=probe_result),
            mock.patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            expected_output.write_bytes(b"x" * 5000)

            result = adjust_audio_speed(video, speed_ratio=1.05, audio_index=0, overwrite=True)

        assert result.operation == "speed"
        assert result.audio_index == 0
        assert result.extra["speed_ratio"] == 1.05

    def test_all_audio(self, tmp_path: Path) -> None:
        """调整所有音频速度。"""
        video = tmp_path / "video.mkv"
        video.write_bytes(b"fake")
        output_dir = tmp_path / "output"
        expected_output = output_dir / "video_speed_0.9_audio.mkv"
        probe_result = _make_probe_result(audio_count=2)

        with (
            mock.patch("processing.core.audio._probe_video", return_value=probe_result),
            mock.patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            expected_output.write_bytes(b"x" * 5000)

            result = adjust_audio_speed(video, speed_ratio=0.9, audio_index=None, overwrite=True)

        assert result.audio_index is None


# ---------------------------------------------------------------------------
# API Tests
# ---------------------------------------------------------------------------


@pytest.fixture
def test_app_audio() -> FastAPI:
    """创建独立的测试 FastAPI app。"""
    from fastapi.templating import Jinja2Templates
    from unittest import mock as umock

    app = FastAPI()
    template_dir = Path(__file__).resolve().parent.parent / "web" / "templates"
    templates = Jinja2Templates(directory=str(template_dir))

    from web.api import router as api_router
    app.include_router(api_router)

    import web.api.probe as probe_module
    import web.api.extract as extract_module
    import web.api.subtitle as subtitle_module
    import web.api.pipeline as pipeline_module
    import web.api.track as track_module
    import web.api.audio as audio_module

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
    audio_module._get_settings = _test_settings

    @app.get("/audio", response_model=None)
    async def audio_page(request: Request):
        return templates.TemplateResponse(request, "audio.html", {"version": "test"})

    return app


@pytest.fixture
def client_audio(test_app_audio: FastAPI) -> TestClient:
    return TestClient(test_app_audio)


class TestAPIAudio:
    """音频管理 API 测试。"""

    def _fake_result(self, operation: str, audio_index: int | None, tmp_path: Path) -> AudioTrackResult:
        output = tmp_path / f"out_{operation}.mkv"
        output.write_bytes(b"x" * 5000)
        return AudioTrackResult(
            input_video=tmp_path / "video.mkv",
            output_path=output,
            output_size=5000,
            operation=operation,
            audio_index=audio_index,
            extra={"language": "chi"} if operation == "add" else {},
        )

    def test_get_audio_page(self, client_audio: TestClient) -> None:
        """GET /audio 返回 HTML。"""
        resp = client_audio.get("/audio")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_post_add_missing_params(self, client_audio: TestClient) -> None:
        """POST /api/audio/add 缺参数 → 400。"""
        resp = client_audio.post("/api/audio/add")
        assert resp.status_code == 400
        assert resp.json()["success"] is False

    def test_post_add_invalid_container(self, client_audio: TestClient) -> None:
        """不支持容器 → 400。"""
        resp = client_audio.post("/api/audio/add", data={
            "file_path": "/tmp/video.mkv",
            "audio_path": "/tmp/audio.m4a",
            "container": "avi",
        })
        assert resp.status_code == 400

    def test_post_add_disallowed_path(self, client_audio: TestClient) -> None:
        """非法路径 → 403。"""
        resp = client_audio.post("/api/audio/add", data={
            "file_path": "/etc/passwd",
            "audio_path": "/tmp/audio.m4a",
        })
        assert resp.status_code == 403

    def test_post_add_success_json(self, client_audio: TestClient, tmp_path: Path) -> None:
        """POST /api/audio/add 成功 → JSON。"""
        video = tmp_path / "video.mkv"
        video.write_bytes(b"fake")
        audio = tmp_path / "audio.m4a"
        audio.write_bytes(b"audio")
        fake = self._fake_result("add", 2, tmp_path)

        with mock.patch("processing.core.audio.add_audio_track", return_value=fake):
            resp = client_audio.post("/api/audio/add", data={
                "file_path": str(video),
                "audio_path": str(audio),
                "language": "chi",
                "set_default": "true",
            })

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["result"]["operation"] == "add"
        assert "download_url" in data["result"]

    def test_post_replace_success(self, client_audio: TestClient, tmp_path: Path) -> None:
        """POST /api/audio/replace 成功。"""
        video = tmp_path / "video.mkv"
        video.write_bytes(b"fake")
        audio = tmp_path / "audio.m4a"
        audio.write_bytes(b"audio")
        fake = self._fake_result("replace", 1, tmp_path)

        with mock.patch("processing.core.audio.replace_audio_track", return_value=fake):
            resp = client_audio.post("/api/audio/replace", data={
                "file_path": str(video),
                "audio_path": str(audio),
                "audio_index": "1",
            })

        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_post_remove_success(self, client_audio: TestClient, tmp_path: Path) -> None:
        """POST /api/audio/remove 成功。"""
        video = tmp_path / "video.mkv"
        video.write_bytes(b"fake")
        fake = self._fake_result("remove", 0, tmp_path)

        with mock.patch("processing.core.audio.remove_audio_track", return_value=fake):
            resp = client_audio.post("/api/audio/remove", data={
                "file_path": str(video),
                "audio_index": "0",
            })

        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_post_mute_success_htmx(self, client_audio: TestClient, tmp_path: Path) -> None:
        """POST /api/audio/mute HTMX → HTML。"""
        video = tmp_path / "video.mkv"
        video.write_bytes(b"fake")
        fake = self._fake_result("mute", 0, tmp_path)

        with mock.patch("processing.core.audio.mute_audio_track", return_value=fake):
            resp = client_audio.post(
                "/api/audio/mute",
                data={"file_path": str(video), "audio_index": "0"},
                headers={"HX-Request": "true"},
            )

        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "音频操作成功" in resp.text

    def test_post_sync_success(self, client_audio: TestClient, tmp_path: Path) -> None:
        """POST /api/audio/sync 成功。"""
        video = tmp_path / "video.mkv"
        video.write_bytes(b"fake")
        fake = self._fake_result("sync", 1, tmp_path)
        fake.extra["offset_seconds"] = 0.5

        with mock.patch("processing.core.audio.adjust_audio_sync", return_value=fake):
            resp = client_audio.post("/api/audio/sync", data={
                "file_path": str(video),
                "offset_seconds": "0.5",
                "audio_index": "1",
            })

        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_post_speed_success(self, client_audio: TestClient, tmp_path: Path) -> None:
        """POST /api/audio/speed 成功。"""
        video = tmp_path / "video.mkv"
        video.write_bytes(b"fake")
        fake = self._fake_result("speed", 0, tmp_path)
        fake.extra["speed_ratio"] = 1.05

        with mock.patch("processing.core.audio.adjust_audio_speed", return_value=fake):
            resp = client_audio.post("/api/audio/speed", data={
                "file_path": str(video),
                "speed_ratio": "1.05",
                "audio_index": "0",
            })

        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_post_engine_error(self, client_audio: TestClient, tmp_path: Path) -> None:
        """核心函数抛 AudioTrackError → 422。"""
        video = tmp_path / "video.mkv"
        video.write_bytes(b"fake")
        audio = tmp_path / "audio.m4a"
        audio.write_bytes(b"audio")

        with mock.patch(
            "processing.core.audio.add_audio_track",
            side_effect=AudioTrackError("音频流 #99 不存在"),
        ):
            resp = client_audio.post("/api/audio/add", data={
                "file_path": str(video),
                "audio_path": str(audio),
                "audio_index": "99",
            })

        assert resp.status_code == 422
        assert resp.json()["success"] is False

    def test_download_path_traversal(self, client_audio: TestClient) -> None:
        """下载路径穿越 → 403。"""
        resp = client_audio.get("/api/audio/download?path=../secret.txt")
        assert resp.status_code == 403

    def test_download_file_not_found(self, client_audio: TestClient) -> None:
        """下载文件不存在 → 404。"""
        resp = client_audio.get("/api/audio/download?path=/tmp/nonexistent.mkv")
        assert resp.status_code == 404
