"""pytools._log_format のテスト。"""

from pathlib import Path

import pytest

from pytools import _log_format


class TestFormatStatus:
    """`format_status()` のフォーマット検証。"""

    def test_basic(self):
        """`    target: state` 形式 (列 4 + コロン)。"""
        assert _log_format.format_status("target", "state") == "    target: state"

    def test_empty_state(self):
        """state が空でも例外を送出しない。"""
        assert _log_format.format_status("x", "") == "    x: "


class TestHomeShort:
    """`home_short()` のホームディレクトリ短縮処理。"""

    def test_inside_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        """home 配下のパスは `~/...` に短縮される。"""
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        assert _log_format.home_short(tmp_path / ".config" / "x.toml") == "~/.config/x.toml"

    def test_home_itself(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        """`Path.home()` 自身は `~` を返す。"""
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        assert _log_format.home_short(tmp_path) == "~"

    def test_outside_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        """home 配下でないパスはそのまま str()。"""
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "user"))
        outside = tmp_path / "etc" / "config"
        assert _log_format.home_short(outside) == str(outside)

    def test_nested_path(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        """深い階層も `~/.../` で短縮される (POSIX 区切り固定)。"""
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        path = tmp_path / "a" / "b" / "c.txt"
        assert _log_format.home_short(path) == "~/a/b/c.txt"
