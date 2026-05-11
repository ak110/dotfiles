"""pytools._internal.setup_msys_env のテスト (winreg 依存部はモック化)."""

# pylint: disable=protected-access

import pytest

from pytools._internal import setup_msys_env as _setup_msys_env


class _FakeWinreg:
    """`winutils.import_winreg`が返すモジュール代替。"""

    REG_SZ = 1


class TestRun:
    """`run`の冪等性と書き込み挙動を検証する。"""

    def _patch_common(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        existing_value: str | None,
        platform: str = "win32",
    ) -> dict:
        captured: dict = {"writes": [], "broadcasts": 0}

        def fake_read(name: str) -> tuple[str | None, int]:
            assert name == _setup_msys_env._MSYS_VAR_NAME
            return existing_value, _FakeWinreg.REG_SZ

        def fake_write(name: str, value: str, reg_type: int) -> None:
            captured["writes"].append((name, value, reg_type))

        def fake_broadcast() -> None:
            captured["broadcasts"] += 1

        monkeypatch.setattr(_setup_msys_env.sys, "platform", platform)
        monkeypatch.setattr(_setup_msys_env.winutils, "read_user_env_var", fake_read)
        monkeypatch.setattr(_setup_msys_env.winutils, "write_user_env_var", fake_write)
        monkeypatch.setattr(_setup_msys_env.winutils, "broadcast_environment_change", fake_broadcast)
        monkeypatch.setattr(_setup_msys_env.winutils, "import_winreg", lambda: _FakeWinreg)
        return captured

    def test_non_windows_skips(self, monkeypatch: pytest.MonkeyPatch):
        captured = self._patch_common(monkeypatch, existing_value=None, platform="linux")
        assert _setup_msys_env.run() is False
        assert not captured["writes"]
        assert captured["broadcasts"] == 0

    def test_already_set_is_noop(self, monkeypatch: pytest.MonkeyPatch):
        """既に同値が設定済みなら書き込まない（冪等性）。"""
        captured = self._patch_common(monkeypatch, existing_value=_setup_msys_env._MSYS_VAR_VALUE)
        assert _setup_msys_env.run() is False
        assert not captured["writes"]
        assert captured["broadcasts"] == 0

    def test_unset_writes_value(self, monkeypatch: pytest.MonkeyPatch):
        """未設定時は新規書き込みとブロードキャストを行う。"""
        captured = self._patch_common(monkeypatch, existing_value=None)
        assert _setup_msys_env.run() is True
        assert captured["writes"] == [(_setup_msys_env._MSYS_VAR_NAME, _setup_msys_env._MSYS_VAR_VALUE, _FakeWinreg.REG_SZ)]
        assert captured["broadcasts"] == 1

    def test_different_value_overwrites(self, monkeypatch: pytest.MonkeyPatch):
        """別値が設定されている場合は上書きする。"""
        captured = self._patch_common(monkeypatch, existing_value="winsymlinks:lnk")
        assert _setup_msys_env.run() is True
        assert captured["writes"] == [(_setup_msys_env._MSYS_VAR_NAME, _setup_msys_env._MSYS_VAR_VALUE, _FakeWinreg.REG_SZ)]
        assert captured["broadcasts"] == 1
