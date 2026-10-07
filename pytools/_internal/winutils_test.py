"""pytools._internal.winutils のテスト。"""

import pytest

from pytools._internal import winutils

from ._test_helpers import FakeEnvironmentRegistry

_PROFILE = r"C:\Users\test"


@pytest.fixture(autouse=True)
def _profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("USERPROFILE", _PROFILE)


@pytest.mark.parametrize(
    ("user_path", "system_path"),
    [
        (r"%USERPROFILE%\.local\bin", ""),
        (rf"{_PROFILE}\.local\bin", ""),
        (rf"{_PROFILE.upper()}\.LOCAL\BIN", ""),
        (rf"{_PROFILE}\.local\bin\\", ""),
        (rf"{_PROFILE}/.local/bin", ""),
        (r"C:\Windows", rf"C:\Windows;{_PROFILE}\.local\bin"),
    ],
)
def test_append_user_path_skips_registered_entries(monkeypatch: pytest.MonkeyPatch, user_path: str, system_path: str) -> None:
    """表記違いの同じディレクトリがユーザー側かシステム側にあれば書き込まない。"""
    registry = FakeEnvironmentRegistry(user_path=user_path, system_path=system_path)
    registry.install(monkeypatch)

    assert winutils.append_user_path(r"%USERPROFILE%\.local\bin") is False
    assert not registry.writes


def test_append_user_path_appends_and_promotes_type(monkeypatch: pytest.MonkeyPatch) -> None:
    """未登録なら末尾へ追記し、`%`を含む値はREG_EXPAND_SZで書く。"""
    registry = FakeEnvironmentRegistry(user_path=r"C:\Windows", user_path_type=FakeEnvironmentRegistry.REG_SZ)
    registry.install(monkeypatch)

    assert winutils.append_user_path(r"%USERPROFILE%\.local\bin") is True
    assert registry.writes == [("Path", r"C:\Windows;%USERPROFILE%\.local\bin", FakeEnvironmentRegistry.REG_EXPAND_SZ)]
