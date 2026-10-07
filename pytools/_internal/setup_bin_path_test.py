"""setup_bin_path.run() のテスト。"""

from pathlib import Path
from unittest import mock

import pytest

from pytools._internal import common, setup_bin_path

from ._test_helpers import FakeEnvironmentRegistry

_PREVIOUS_ENTRIES = [
    r"%USERPROFILE%\dotfiles\bin",
    r"%USERPROFILE%\dotfiles\agent-toolkit\bin",
    r"%USERPROFILE%\.local\bin",
]


@pytest.fixture(autouse=True, name="dotfiles_under_home")
def _dotfiles_under_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """作業ツリーが`~/dotfiles`にある環境を再現する。"""
    home = tmp_path / "home"
    monkeypatch.setattr(setup_bin_path.Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(common, "find_dotfiles_root", lambda: home / "dotfiles")
    return home


def test_entries_keep_previous_userprofile_form_when_root_is_home_dotfiles(dotfiles_under_home: Path) -> None:
    """ルートが`~/dotfiles`なら、登録値は変更前と同じ`%USERPROFILE%`形式の3件になる。

    絶対パスで登録すると、PATH整理が`%USERPROFILE%`形式へ戻して次の登録が再び追記する往復が起こる。
    """
    entries = setup_bin_path._bin_entries(dotfiles_under_home / "dotfiles", dotfiles_under_home)  # pylint: disable=protected-access
    assert list(entries) == _PREVIOUS_ENTRIES


def test_entries_outside_home_use_absolute_root() -> None:
    """ホーム外の作業ツリーは絶対パスで登録し、`.local\\bin`は`%USERPROFILE%`形式のまま登録する。"""
    entries = setup_bin_path._bin_entries(Path("/srv/dotfiles"), Path("/home/user"))  # pylint: disable=protected-access
    assert entries[2] == r"%USERPROFILE%\.local\bin"
    assert entries[0].endswith("dotfiles\\bin")
    assert entries[1].endswith("dotfiles\\agent-toolkit\\bin")
    assert "%USERPROFILE%" not in entries[0]


def test_entries_without_root_register_only_local_bin() -> None:
    """作業ツリーを解決できない場合は`.local\\bin`だけを登録する。"""
    assert setup_bin_path._bin_entries(None, Path("/home/user")) == (r"%USERPROFILE%\.local\bin",)  # pylint: disable=protected-access


def test_run_all_entries_appended(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_append = mock.Mock(return_value=True)
    mock_broadcast = mock.Mock()
    monkeypatch.setattr(setup_bin_path.winutils, "append_user_path", mock_append)
    monkeypatch.setattr(setup_bin_path.winutils, "broadcast_environment_change", mock_broadcast)
    assert setup_bin_path.run().changed is True
    assert [call.args[0] for call in mock_append.call_args_list] == _PREVIOUS_ENTRIES
    mock_broadcast.assert_called_once()


def test_run_all_entries_existing(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_append = mock.Mock(return_value=False)
    mock_broadcast = mock.Mock()
    monkeypatch.setattr(setup_bin_path.winutils, "append_user_path", mock_append)
    monkeypatch.setattr(setup_bin_path.winutils, "broadcast_environment_change", mock_broadcast)
    assert setup_bin_path.run().changed is False
    mock_broadcast.assert_not_called()


def test_run_partial_appended(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_append = mock.Mock(side_effect=[True, False, False])
    mock_broadcast = mock.Mock()
    monkeypatch.setattr(setup_bin_path.winutils, "append_user_path", mock_append)
    monkeypatch.setattr(setup_bin_path.winutils, "broadcast_environment_change", mock_broadcast)
    assert setup_bin_path.run().changed is True
    mock_broadcast.assert_called_once()


def test_run_entry_write_failure_continues_and_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """1件の書き込みに失敗しても残りを登録し、工程は失敗と数える。"""
    mock_append = mock.Mock(side_effect=[PermissionError("boom"), True, False])
    mock_broadcast = mock.Mock()
    monkeypatch.setattr(setup_bin_path.winutils, "append_user_path", mock_append)
    monkeypatch.setattr(setup_bin_path.winutils, "broadcast_environment_change", mock_broadcast)
    outcome = setup_bin_path.run()
    assert outcome.changed is True
    assert outcome.failure is not None
    assert "boom" in outcome.failure
    assert mock_append.call_count == 3
    mock_broadcast.assert_called_once()


def test_run_appends_local_bin_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """どちらにも無い`.local\\bin`を既存2件の後へ1回だけ追加し、2回目は書き込まない。"""
    monkeypatch.setenv("USERPROFILE", r"C:\Users\test")
    registry = FakeEnvironmentRegistry(user_path=r"C:\Windows", system_path=r"C:\Windows\System32")
    registry.install(monkeypatch)

    assert setup_bin_path.run().changed is True
    assert setup_bin_path.run().changed is False

    assert registry.user_path().split(";") == [r"C:\Windows", *_PREVIOUS_ENTRIES]
    assert len(registry.writes) == 3
