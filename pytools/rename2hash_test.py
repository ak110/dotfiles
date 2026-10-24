"""rename2hashモジュールのテスト。"""

# pylint: disable=protected-access

import hashlib
import pathlib
import sys
import typing

import pytest

from pytools import rename2hash


def _run_main(monkeypatch: pytest.MonkeyPatch, *args: str) -> int | str | None:
    """`rename2hash`の公開エントリポイントを引数付きで実行し、終了コードを返す。"""
    monkeypatch.setattr(sys, "argv", ["rename2hash", *args])
    with pytest.raises(SystemExit) as exc_info:
        rename2hash.main()
    return exc_info.value.code


def _md5_name(content: bytes, suffix: str) -> str:
    return hashlib.md5(content, usedforsecurity=False).hexdigest() + suffix


def test_exit_code_one_when_read_fails_and_continues(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """1件の読み込みが失敗しても残りを改名し、終了コード1で終える。"""
    (tmp_path / "unreadable.txt").write_bytes(b"a")
    (tmp_path / "readable.txt").write_bytes(b"b")
    real_md5 = rename2hash._md5_of_file

    def flaky_md5(path: pathlib.Path, **kwargs: typing.Any) -> str:
        del kwargs
        if path.name == "unreadable.txt":
            raise PermissionError("denied")
        return real_md5(path)

    monkeypatch.setattr(rename2hash, "_md5_of_file", flaky_md5)

    assert _run_main(monkeypatch, str(tmp_path)) == 1
    assert (tmp_path / "unreadable.txt").exists()
    assert (tmp_path / _md5_name(b"b", ".txt")).exists()


def test_exit_code_zero_without_failure(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """失敗が無ければ全ファイルを改名して終了コード0で終える。"""
    (tmp_path / "a.txt").write_bytes(b"a")

    assert _run_main(monkeypatch, str(tmp_path)) == 0
    assert (tmp_path / _md5_name(b"a", ".txt")).exists()
