"""DV7→DV8.1 remux: detection, pipeline command construction, verification,
temp cleanup, and the CLI missing-tools gate. No real ffmpeg/dovi_tool needed —
all subprocess work is mocked.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# test_rebuild_entries_after_edit installs a non-subscriptable textual.app stub
# via sys.modules.setdefault; under `unittest discover` it runs before this
# module and the leftover stub breaks `App[None]` when we import titleforge.cli
# (→ review_app). Restore the real module first (stubs made with
# types.ModuleType have __spec__ = None; real imports don't).
_maybe_stub = sys.modules.get("textual.app")
if _maybe_stub is not None and getattr(_maybe_stub, "__spec__", None) is None:
    del sys.modules["textual.app"]

from titleforge import remux  # noqa: E402
from titleforge.cli import _ensure_remux_tools_available  # noqa: E402
from titleforge.config import get_convert_for_streaming_enabled, get_remux_tools  # noqa: E402
from titleforge.remux import (  # noqa: E402
    BL_RPU_FILENAME,
    RemuxError,
    RemuxTools,
    check_tools,
    is_dv7_mkv,
    probe_video,
    remux_dv7_to_dv8,
)

TOOLS = RemuxTools()


def _ffprobe_json(
    dv_profile: int | None = None,
    r_frame_rate: str = "24000/1001",
    duration: str | None = "7200.000000",
    audio: int = 2,
    subs: int = 3,
    title: str | None = "My Movie",
) -> str:
    """Build a realistic ffprobe -show_format -show_streams JSON payload."""
    video: dict = {"codec_type": "video", "codec_name": "hevc", "r_frame_rate": r_frame_rate}
    if dv_profile is not None:
        video["side_data_list"] = [
            {"side_data_type": "Content light level metadata"},
            {
                "side_data_type": "DOVI configuration record",
                "dv_profile": dv_profile,
                "dv_bl_signal_compatibility_id": 6,
            },
        ]
    streams = [video]
    streams += [{"codec_type": "audio", "codec_name": "truehd"} for _ in range(audio)]
    streams += [{"codec_type": "subtitle", "codec_name": "hdmv_pgs_subtitle"} for _ in range(subs)]
    fmt: dict = {"format_name": "matroska,webm"}
    if duration is not None:
        fmt["duration"] = duration
    if title is not None:
        fmt["tags"] = {"title": title}
    return json.dumps({"streams": streams, "format": fmt})


def _completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> mock.MagicMock:
    proc = mock.MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


def _src_info(**overrides) -> dict:
    info = {
        "dv_profile": 7,
        "r_frame_rate": "24000/1001",
        "duration": 7200.0,
        "audio_count": 2,
        "subtitle_count": 3,
        "title": "My Movie",
    }
    info.update(overrides)
    return info


def _out_info(**overrides) -> dict:
    info = _src_info()
    info.update({"dv_profile": 8, "title": None})
    info.update(overrides)
    return info


class TestProbeVideo(unittest.TestCase):
    def test_parses_dv_profile_frame_rate_duration_counts_title(self) -> None:
        with mock.patch(
            "titleforge.remux.subprocess.run",
            return_value=_completed(stdout=_ffprobe_json(dv_profile=7)),
        ) as run:
            info = probe_video(Path("/in/movie.mkv"), "ffprobe")
        self.assertEqual(info["dv_profile"], 7)
        self.assertEqual(info["r_frame_rate"], "24000/1001")
        self.assertAlmostEqual(info["duration"], 7200.0)
        self.assertEqual(info["audio_count"], 2)
        self.assertEqual(info["subtitle_count"], 3)
        self.assertEqual(info["title"], "My Movie")
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[0], "ffprobe")
        self.assertIn("-show_streams", cmd)
        self.assertIn("/in/movie.mkv", cmd)

    def test_no_dovi_side_data_yields_none_profile(self) -> None:
        with mock.patch(
            "titleforge.remux.subprocess.run",
            return_value=_completed(stdout=_ffprobe_json(dv_profile=None, title=None)),
        ):
            info = probe_video(Path("/in/movie.mkv"))
        self.assertIsNone(info["dv_profile"])
        self.assertIsNone(info["title"])

    def test_ffprobe_failure_raises_remux_error(self) -> None:
        with mock.patch(
            "titleforge.remux.subprocess.run",
            return_value=_completed(returncode=1, stderr="boom"),
        ):
            with self.assertRaises(RemuxError):
                probe_video(Path("/in/movie.mkv"))


class TestIsDv7Mkv(unittest.TestCase):
    def _probe(self, dv_profile: int | None) -> mock.MagicMock:
        return mock.patch(
            "titleforge.remux.subprocess.run",
            return_value=_completed(stdout=_ffprobe_json(dv_profile=dv_profile)),
        )

    def test_profile_7_mkv_is_detected(self) -> None:
        with self._probe(7):
            self.assertTrue(is_dv7_mkv(Path("/in/movie.mkv")))

    def test_profile_8_is_skipped(self) -> None:
        with self._probe(8):
            self.assertFalse(is_dv7_mkv(Path("/in/movie.mkv")))

    def test_no_dovi_metadata_is_skipped(self) -> None:
        with self._probe(None):
            self.assertFalse(is_dv7_mkv(Path("/in/movie.mkv")))

    def test_non_mkv_is_skipped_without_probing(self) -> None:
        with mock.patch("titleforge.remux.subprocess.run") as run:
            self.assertFalse(is_dv7_mkv(Path("/in/movie.mp4")))
        run.assert_not_called()

    def test_probe_failure_is_treated_as_not_dv7(self) -> None:
        with mock.patch(
            "titleforge.remux.subprocess.run",
            return_value=_completed(returncode=1, stderr="unreadable"),
        ):
            with self.assertLogs("titleforge.remux", level="WARNING"):
                self.assertFalse(is_dv7_mkv(Path("/in/movie.mkv")))


class TestCheckTools(unittest.TestCase):
    def test_all_present(self) -> None:
        with mock.patch("titleforge.remux.shutil.which", return_value="/usr/bin/x"):
            self.assertEqual(check_tools(TOOLS), [])

    def test_missing_are_listed_by_name(self) -> None:
        def which(exe: str) -> str | None:
            return None if exe in ("dovi_tool", "mkvmerge") else "/usr/bin/x"

        with mock.patch("titleforge.remux.shutil.which", side_effect=which):
            self.assertEqual(check_tools(TOOLS), ["dovi_tool", "mkvmerge"])


class _PipelineCase(unittest.TestCase):
    """Shared fixture: real (tiny) src file, mocked probe/Popen/run/disk_usage."""

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.work = Path(self._td.name)
        self.src = self.work / "Movie.2020.DV.mkv"
        self.src.write_bytes(b"\x00" * 1024)
        self.out = self.work / f".{self.src.stem}.dv8.tmp.mkv"
        self.bl_rpu = self.work / BL_RPU_FILENAME

    def _popen_pair(self, ffmpeg_rc: int = 0, dovi_rc: int = 0) -> list[mock.MagicMock]:
        ffmpeg = mock.MagicMock()
        ffmpeg.returncode = ffmpeg_rc
        ffmpeg.communicate.return_value = (None, b"ffmpeg-stderr")
        dovi = mock.MagicMock()
        dovi.returncode = dovi_rc
        dovi.communicate.return_value = (None, b"dovi-stderr")
        return [ffmpeg, dovi]

    def _run(
        self,
        probe_side_effect: list,
        ffmpeg_rc: int = 0,
        dovi_rc: int = 0,
        mkvmerge: mock.MagicMock | None = None,
    ):
        """Run remux_dv7_to_dv8 with everything mocked; return the mocks."""
        procs = self._popen_pair(ffmpeg_rc, dovi_rc)
        if mkvmerge is None:
            mkvmerge = _completed(returncode=0)
        with (
            mock.patch("titleforge.remux.probe_video", side_effect=probe_side_effect) as probe,
            mock.patch("titleforge.remux.subprocess.Popen", side_effect=procs) as popen,
            mock.patch("titleforge.remux.subprocess.run", return_value=mkvmerge) as run,
        ):
            remux_dv7_to_dv8(self.src, self.out, TOOLS)
        return probe, popen, run, procs


class TestPipelineCommands(_PipelineCase):
    def test_success_builds_exact_commands(self) -> None:
        _, popen, run, procs = self._run([_src_info(), _out_info()])

        ffmpeg_cmd = popen.call_args_list[0].args[0]
        self.assertEqual(
            ffmpeg_cmd,
            [
                "ffmpeg", "-nostdin", "-v", "error", "-i", str(self.src),
                "-map", "0:v:0", "-c:v", "copy",
                "-bsf:v", "hevc_mp4toannexb", "-f", "hevc", "-",
            ],
        )
        dovi_call = popen.call_args_list[1]
        self.assertEqual(
            dovi_call.args[0],
            ["dovi_tool", "-m", "2", "convert", "--discard", "-", "-o", str(self.bl_rpu)],
        )
        # dovi_tool's stdin is wired to ffmpeg's stdout (one streamed pass),
        # and the parent drops its handle so ffmpeg gets SIGPIPE on early exit.
        self.assertIs(dovi_call.kwargs["stdin"], procs[0].stdout)
        procs[0].stdout.close.assert_called_once()

        mkv_cmd = run.call_args.args[0]
        self.assertEqual(
            mkv_cmd,
            [
                "mkvmerge", "-o", str(self.out),
                "--title", "My Movie",
                "--default-duration", "0:24000/1001p",
                str(self.bl_rpu), "-D", str(self.src),
            ],
        )

    def test_untitled_source_omits_title_flag(self) -> None:
        _, _, run, _ = self._run([_src_info(title=None), _out_info()])
        self.assertNotIn("--title", run.call_args.args[0])

    def test_custom_tool_paths_are_used(self) -> None:
        tools = RemuxTools(
            ffmpeg="/opt/ffmpeg", ffprobe="/opt/ffprobe",
            dovi_tool="/opt/dovi_tool", mkvmerge="/opt/mkvmerge",
        )
        procs = self._popen_pair()
        with (
            mock.patch("titleforge.remux.probe_video", side_effect=[_src_info(), _out_info()]),
            mock.patch("titleforge.remux.subprocess.Popen", side_effect=procs) as popen,
            mock.patch("titleforge.remux.subprocess.run", return_value=_completed()) as run,
        ):
            remux_dv7_to_dv8(self.src, self.out, tools)
        self.assertEqual(popen.call_args_list[0].args[0][0], "/opt/ffmpeg")
        self.assertEqual(popen.call_args_list[1].args[0][0], "/opt/dovi_tool")
        self.assertEqual(run.call_args.args[0][0], "/opt/mkvmerge")


class TestPipelineFailures(_PipelineCase):
    def test_non_dv7_source_raises(self) -> None:
        with self.assertRaisesRegex(RemuxError, "not DV profile 7"):
            self._run([_src_info(dv_profile=8)])

    def test_missing_frame_rate_raises(self) -> None:
        with self.assertRaisesRegex(RemuxError, "r_frame_rate"):
            self._run([_src_info(r_frame_rate=None)])

    def test_insufficient_free_space_skips_before_any_subprocess(self) -> None:
        usage = mock.MagicMock(free=self.src.stat().st_size - 1)
        with (
            mock.patch("titleforge.remux.probe_video", return_value=_src_info()),
            mock.patch("titleforge.remux.shutil.disk_usage", return_value=usage),
            mock.patch("titleforge.remux.subprocess.Popen") as popen,
        ):
            with self.assertRaisesRegex(RemuxError, "free space"):
                remux_dv7_to_dv8(self.src, self.out, TOOLS)
        popen.assert_not_called()

    def test_ffmpeg_failure_raises_and_cleans_temp(self) -> None:
        self.bl_rpu.write_bytes(b"partial")
        with self.assertRaisesRegex(RemuxError, "ffmpeg"):
            self._run([_src_info()], ffmpeg_rc=1)
        self.assertFalse(self.bl_rpu.exists())
        self.assertFalse(self.out.exists())
        self.assertTrue(self.src.exists())  # original untouched

    def test_dovi_tool_failure_raises_and_cleans_temp(self) -> None:
        self.bl_rpu.write_bytes(b"partial")
        with self.assertRaisesRegex(RemuxError, "dovi_tool"):
            self._run([_src_info()], dovi_rc=2)
        self.assertFalse(self.bl_rpu.exists())
        self.assertTrue(self.src.exists())

    def test_mkvmerge_exit_2_raises_and_cleans_temp(self) -> None:
        self.bl_rpu.write_bytes(b"stream")
        self.out.write_bytes(b"partial out")
        with self.assertRaisesRegex(RemuxError, "mkvmerge"):
            self._run([_src_info()], mkvmerge=_completed(returncode=2, stderr="muxing failed"))
        self.assertFalse(self.bl_rpu.exists())
        self.assertFalse(self.out.exists())
        self.assertTrue(self.src.exists())

    def test_mkvmerge_exit_1_is_success_with_logged_warning(self) -> None:
        with self.assertLogs("titleforge.remux", level="WARNING") as logs:
            self._run(
                [_src_info(), _out_info()],
                mkvmerge=_completed(returncode=1, stdout="Warning: something cosmetic"),
            )
        self.assertTrue(any("warning" in line.lower() for line in logs.output))

    def test_bl_rpu_deleted_after_successful_mkvmerge(self) -> None:
        self.bl_rpu.write_bytes(b"stream")
        self._run([_src_info(), _out_info()])
        self.assertFalse(self.bl_rpu.exists())


class TestVerification(_PipelineCase):
    def _run_expecting_verify_failure(self, out_info: dict, pattern: str) -> None:
        self.out.write_bytes(b"remuxed")  # simulate mkvmerge having produced output
        with self.assertRaisesRegex(RemuxError, pattern):
            self._run([_src_info(), out_info])
        # A failed verification must remove the temp output and keep the source.
        self.assertFalse(self.out.exists())
        self.assertTrue(self.src.exists())

    def test_wrong_output_profile_fails(self) -> None:
        self._run_expecting_verify_failure(_out_info(dv_profile=7), "dv_profile")

    def test_duration_drift_beyond_tolerance_fails(self) -> None:
        self._run_expecting_verify_failure(_out_info(duration=7203.5), "duration")

    def test_duration_within_tolerance_passes(self) -> None:
        self.out.write_bytes(b"remuxed")
        self._run([_src_info(), _out_info(duration=7201.5)])
        self.assertTrue(self.out.exists())

    def test_audio_stream_count_mismatch_fails(self) -> None:
        self._run_expecting_verify_failure(_out_info(audio_count=1), "audio")

    def test_subtitle_stream_count_mismatch_fails(self) -> None:
        self._run_expecting_verify_failure(_out_info(subtitle_count=0), "subtitle")


class TestCliMissingTools(unittest.TestCase):
    def test_missing_tools_exit_with_actionable_message(self) -> None:
        with mock.patch("titleforge.remux.shutil.which", return_value=None):
            with self.assertRaises(SystemExit) as ctx:
                _ensure_remux_tools_available(TOOLS)
        msg = str(ctx.exception)
        self.assertIn("--convert-for-streaming", msg)
        for name in ("ffmpeg", "ffprobe", "dovi_tool", "mkvmerge"):
            self.assertIn(name, msg)
        self.assertIn("brew install", msg)
        self.assertIn("github.com/quietvoid/dovi_tool", msg)

    def test_all_tools_present_is_a_no_op(self) -> None:
        with mock.patch("titleforge.remux.shutil.which", return_value="/usr/bin/x"):
            self.assertIsNone(_ensure_remux_tools_available(TOOLS))


class TestConfig(unittest.TestCase):
    def test_convert_for_streaming_enabled_truthy_values(self) -> None:
        for value in ("true", "TRUE", "1", "yes", "on"):
            with mock.patch.dict(os.environ, {"CONVERT_FOR_STREAMING": value}, clear=False):
                self.assertTrue(get_convert_for_streaming_enabled(), value)

    def test_convert_for_streaming_disabled_by_default_and_for_falsy_values(self) -> None:
        env = dict(os.environ)
        env.pop("CONVERT_FOR_STREAMING", None)
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertFalse(get_convert_for_streaming_enabled())
        for value in ("false", "0", "no", "off", ""):
            with mock.patch.dict(os.environ, {"CONVERT_FOR_STREAMING": value}, clear=False):
                self.assertFalse(get_convert_for_streaming_enabled(), value)

    def test_tool_paths_default_to_bare_names(self) -> None:
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("FFMPEG_PATH", "FFPROBE_PATH", "DOVI_TOOL_PATH", "MKVMERGE_PATH")
        }
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(get_remux_tools(), RemuxTools())

    def test_tool_paths_respect_env_overrides(self) -> None:
        overrides = {
            "FFMPEG_PATH": "/opt/bin/ffmpeg",
            "FFPROBE_PATH": "/opt/bin/ffprobe",
            "DOVI_TOOL_PATH": "/opt/bin/dovi_tool",
            "MKVMERGE_PATH": "/opt/bin/mkvmerge",
        }
        with mock.patch.dict(os.environ, overrides, clear=False):
            self.assertEqual(
                get_remux_tools(),
                RemuxTools(
                    ffmpeg="/opt/bin/ffmpeg",
                    ffprobe="/opt/bin/ffprobe",
                    dovi_tool="/opt/bin/dovi_tool",
                    mkvmerge="/opt/bin/mkvmerge",
                ),
            )


if __name__ == "__main__":
    unittest.main()
