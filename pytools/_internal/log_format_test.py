from pathlib import Path

from pytools._internal import log_format as _log_format


class TestFormatStatus:
    def test_basic(self):
        assert _log_format.format_status("target", "state") == "    target: state"

    def test_empty_state(self):
        assert _log_format.format_status("x", "") == "    x: "


class TestHomeShort:
    def test_inside_home(self, tmp_path: Path):
        assert _log_format.home_short(tmp_path / ".config" / "x.toml", home=tmp_path) == "~/.config/x.toml"

    def test_home_itself(self, tmp_path: Path):
        assert _log_format.home_short(tmp_path, home=tmp_path) == "~"

    def test_outside_home(self, tmp_path: Path):
        outside = tmp_path / "etc" / "config"
        assert _log_format.home_short(outside, home=tmp_path / "user") == str(outside)

    def test_nested_path(self, tmp_path: Path):
        """深い階層も `~/.../` で短縮される (POSIX 区切り固定)。"""
        path = tmp_path / "a" / "b" / "c.txt"
        assert _log_format.home_short(path, home=tmp_path) == "~/a/b/c.txt"
