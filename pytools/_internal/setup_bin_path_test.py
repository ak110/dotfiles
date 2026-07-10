"""setup_bin_path.run() のテスト。"""

from unittest import mock

import pytest

from pytools._internal import setup_bin_path


def test_run_non_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(setup_bin_path.sys, "platform", "linux")
    assert setup_bin_path.run() is False


def test_run_all_entries_appended(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(setup_bin_path.sys, "platform", "win32")
    mock_append = mock.Mock(return_value=True)
    mock_broadcast = mock.Mock()
    monkeypatch.setattr(setup_bin_path.winutils, "append_user_path", mock_append)
    monkeypatch.setattr(setup_bin_path.winutils, "broadcast_environment_change", mock_broadcast)
    assert setup_bin_path.run() is True
    assert mock_append.call_count == len(setup_bin_path._BIN_ENTRIES)  # noqa: SLF001  # pylint: disable=protected-access  # エントリ数とのSSOT保持のため直接参照
    mock_broadcast.assert_called_once()


def test_run_all_entries_existing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(setup_bin_path.sys, "platform", "win32")
    mock_append = mock.Mock(return_value=False)
    mock_broadcast = mock.Mock()
    monkeypatch.setattr(setup_bin_path.winutils, "append_user_path", mock_append)
    monkeypatch.setattr(setup_bin_path.winutils, "broadcast_environment_change", mock_broadcast)
    assert setup_bin_path.run() is False
    mock_broadcast.assert_not_called()


def test_run_partial_appended(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(setup_bin_path.sys, "platform", "win32")
    mock_append = mock.Mock(side_effect=[True, False])
    mock_broadcast = mock.Mock()
    monkeypatch.setattr(setup_bin_path.winutils, "append_user_path", mock_append)
    monkeypatch.setattr(setup_bin_path.winutils, "broadcast_environment_change", mock_broadcast)
    assert setup_bin_path.run() is True
    mock_broadcast.assert_called_once()


def test_run_entry_exception_continues(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(setup_bin_path.sys, "platform", "win32")
    mock_append = mock.Mock(side_effect=[RuntimeError("boom"), True])
    mock_broadcast = mock.Mock()
    monkeypatch.setattr(setup_bin_path.winutils, "append_user_path", mock_append)
    monkeypatch.setattr(setup_bin_path.winutils, "broadcast_environment_change", mock_broadcast)
    assert setup_bin_path.run() is True
    assert mock_append.call_count == 2
    mock_broadcast.assert_called_once()
