"""Tests for user config directory resolution."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

from titleforge.config import CONFIG_FILENAME, user_config_dir, user_config_file


class TestUserConfigPaths(unittest.TestCase):
    @mock.patch("titleforge.config.os.name", "posix")
    def test_unix_uses_xdg_config_home_when_set(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"HOME": "/tmp/tfhome", "XDG_CONFIG_HOME": "/tmp/xdgcfg"},
            clear=False,
        ):
            self.assertEqual(user_config_dir(), Path("/tmp/xdgcfg/titleforge"))
            self.assertEqual(
                user_config_file(),
                Path("/tmp/xdgcfg/titleforge") / CONFIG_FILENAME,
            )

    @mock.patch("titleforge.config.os.name", "posix")
    def test_unix_default_dot_config(self) -> None:
        with mock.patch.dict(os.environ, {"HOME": "/tmp/tfhome"}, clear=False):
            os.environ.pop("XDG_CONFIG_HOME", None)
            self.assertEqual(
                user_config_dir(),
                Path("/tmp/tfhome/.config/titleforge"),
            )
            self.assertEqual(
                user_config_file(),
                Path("/tmp/tfhome/.config/titleforge") / CONFIG_FILENAME,
            )

    @unittest.skipUnless(sys.platform == "win32", "requires Windows Path semantics")
    def test_windows_uses_appdata(self) -> None:
        roaming = r"C:\Users\SomeUser\AppData\Roaming"
        with mock.patch.dict(os.environ, {"APPDATA": roaming}, clear=False):
            self.assertEqual(user_config_dir(), Path(roaming) / "TitleForge")
            self.assertEqual(
                user_config_file(),
                Path(roaming) / "TitleForge" / CONFIG_FILENAME,
            )


if __name__ == "__main__":
    unittest.main()
