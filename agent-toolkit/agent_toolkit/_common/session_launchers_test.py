"""`_common/session_launchers.py`の登録簿の読み取りを検証する。"""

import json
import os
import pathlib
from collections.abc import Iterator
from typing import Any

import pytest

from agent_toolkit._common import session_launchers


def _write_record(state_root: pathlib.Path, session_id: str, payload: Any) -> None:
    directory = session_launchers.registry_directory(state_root)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{session_id}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_launcher_reader_returns_recorded_launcher(tmp_path: pathlib.Path) -> None:
    """登録簿に記録された委譲元だけを返し、記録の無いsessionと不正な識別子には`None`を返す。"""
    _write_record(tmp_path, "child", {session_launchers.LAUNCHER_KEY: "parent"})
    _write_record(tmp_path, "invalid", {session_launchers.LAUNCHER_KEY: "../parent"})

    read = session_launchers.launcher_reader(tmp_path)

    assert read("child") == "parent"
    assert read("invalid") is None
    assert read("missing") is None
    assert read("../child") is None


def test_launcher_reader_without_registry_returns_none(tmp_path: pathlib.Path) -> None:
    """状態ディレクトリを解決できない場合と登録簿が無い場合は、常に`None`を返す。"""
    assert session_launchers.launcher_reader(None)("child") is None
    assert session_launchers.launcher_reader(tmp_path)("child") is None


class _FailingScandir:
    """列挙の途中で`OSError`を送出し、閉じられたかを記録する`os.scandir`の代替。"""

    def __init__(self) -> None:
        self.closed = False

    def __enter__(self) -> _FailingScandir:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def __iter__(self) -> Iterator[os.DirEntry[str]]:
        raise OSError("列挙中の失敗")

    def close(self) -> None:
        self.closed = True


def test_launcher_reader_closes_scandir_on_enumeration_failure(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """列挙の途中で失敗しても、ディレクトリのハンドルを閉じてから常に`None`を返す関数へ切り替える。"""
    _write_record(tmp_path, "child", {session_launchers.LAUNCHER_KEY: "parent"})
    iterator = _FailingScandir()
    monkeypatch.setattr(session_launchers.os, "scandir", lambda _path: iterator)

    read = session_launchers.launcher_reader(tmp_path)

    assert iterator.closed
    assert read("child") is None
