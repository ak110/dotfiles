"""setup_bin_path.run() のテスト。"""

from unittest import mock

import pytest

from pytools._internal import setup_bin_path


def test_run_all_entries_appended(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_append = mock.Mock(return_value=True)
    mock_broadcast = mock.Mock()
    monkeypatch.setattr(setup_bin_path.winutils, "append_user_path", mock_append)
    monkeypatch.setattr(setup_bin_path.winutils, "broadcast_environment_change", mock_broadcast)
    assert setup_bin_path.run().changed is True
    assert mock_append.call_count == len(setup_bin_path._BIN_ENTRIES)  # noqa: SLF001  # pylint: disable=protected-access  # エントリ数とのSSOT保持のため直接参照
    mock_broadcast.assert_called_once()


def test_run_all_entries_existing(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_append = mock.Mock(return_value=False)
    mock_broadcast = mock.Mock()
    monkeypatch.setattr(setup_bin_path.winutils, "append_user_path", mock_append)
    monkeypatch.setattr(setup_bin_path.winutils, "broadcast_environment_change", mock_broadcast)
    assert setup_bin_path.run().changed is False
    mock_broadcast.assert_not_called()


def test_run_partial_appended(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_append = mock.Mock(side_effect=[True, False])
    mock_broadcast = mock.Mock()
    monkeypatch.setattr(setup_bin_path.winutils, "append_user_path", mock_append)
    monkeypatch.setattr(setup_bin_path.winutils, "broadcast_environment_change", mock_broadcast)
    assert setup_bin_path.run().changed is True
    mock_broadcast.assert_called_once()


def test_run_entry_write_failure_continues_and_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """1件の書き込みに失敗しても残りを登録し、工程は失敗と数える。"""
    mock_append = mock.Mock(side_effect=[PermissionError("boom"), True])
    mock_broadcast = mock.Mock()
    monkeypatch.setattr(setup_bin_path.winutils, "append_user_path", mock_append)
    monkeypatch.setattr(setup_bin_path.winutils, "broadcast_environment_change", mock_broadcast)
    outcome = setup_bin_path.run()
    assert outcome.changed is True
    assert outcome.failure is not None
    assert "boom" in outcome.failure
    assert mock_append.call_count == 2
    mock_broadcast.assert_called_once()
