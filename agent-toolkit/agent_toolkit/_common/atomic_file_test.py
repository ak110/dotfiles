"""共通保存の置換失敗時に旧本文と耐久書込の契約が保持されることを確かめる。"""

import errno
import os
import pathlib
import time
from typing import Any

import pytest

from agent_toolkit._common import atomic_file


class ReplaceFailure(PermissionError):
    """Windowsの置換診断を、実行ホストによらず再現する。"""

    def __init__(self, winerror: int | None, message: str) -> None:
        super().__init__(errno.EACCES, message)
        self.winerror = winerror


@pytest.mark.parametrize("winerror", (5, 32))
@pytest.mark.parametrize("fsync", (False, True))
def test_windows_replace_retry_contract(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, winerror: int, fsync: bool
) -> None:
    """置換だけが一時的に拒否されても、一度書いた新本文を同じ一時ファイルから公開する。"""
    destination = tmp_path / "state.json"
    destination.write_text("旧本文", encoding="utf-8")
    replace = os.replace
    synchronize = os.fsync
    attempts: list[str] = []
    sleeps: list[float] = []
    synced: list[int] = []

    def fail_once(source: str, target: pathlib.Path, **_kwargs: Any) -> None:
        attempts.append(source)
        assert destination.read_text(encoding="utf-8") == "旧本文"
        if len(attempts) == 1:
            raise ReplaceFailure(winerror, "読取ハンドルとの競合")
        replace(source, target)

    def record_sync(descriptor: int, **_kwargs: Any) -> None:
        synced.append(descriptor)
        synchronize(descriptor)

    monkeypatch.setattr(atomic_file.os, "replace", fail_once)
    monkeypatch.setattr(atomic_file.os, "fsync", record_sync)
    monkeypatch.setattr(time, "sleep", sleeps.append)
    atomic_file.atomic_write(destination, "新本文", fsync=fsync)

    assert destination.read_text(encoding="utf-8") == "新本文"
    assert len(attempts) == 2
    assert attempts[0] == attempts[1]
    assert len(sleeps) == 1
    assert len(synced) == int(fsync)
    assert list(tmp_path.iterdir()) == [destination]


@pytest.mark.parametrize("winerror", (5, 32, None, 3))
def test_retry_preserves_fsync_and_previous_content(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, winerror: int | None
) -> None:
    """継続するWindows拒否と対象外の失敗は有限回で返り、旧本文と元の例外を保持する。"""
    destination = tmp_path / "state.json"
    destination.write_text("旧本文", encoding="utf-8")
    error = ReplaceFailure(winerror, "置換を拒否")
    attempts: list[str] = []
    sleeps: list[float] = []

    def refuse(source: str, _target: pathlib.Path, **_kwargs: Any) -> None:
        attempts.append(source)
        assert pathlib.Path(source).read_text(encoding="utf-8") == "新本文"
        raise error

    monkeypatch.setattr(atomic_file.os, "replace", refuse)
    monkeypatch.setattr(time, "sleep", sleeps.append)
    with pytest.raises(PermissionError) as caught:
        atomic_file.atomic_write(destination, "新本文", fsync=True)

    assert caught.value is error
    assert destination.read_text(encoding="utf-8") == "旧本文"
    assert len(set(attempts)) == 1
    if winerror in (5, 32):
        assert len(attempts) > 1
        assert len(attempts) == len(sleeps) + 1
        assert sum(sleeps) < 1
    else:
        assert len(attempts) == 1
        assert not sleeps
    assert list(tmp_path.iterdir()) == [destination]
