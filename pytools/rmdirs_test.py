"""rmdirsモジュールのテスト。"""

import pathlib
import shutil
import sys
import typing

import pytest

from pytools import rmdirs


def _run_main(monkeypatch: pytest.MonkeyPatch, *args: str) -> int | str | None:
    """`py-rmdirs`の公開エントリポイントを引数付きで実行し、終了コードを返す。"""
    monkeypatch.setattr(sys, "argv", ["py-rmdirs", *args])
    with pytest.raises(SystemExit) as exc_info:
        rmdirs.main()
    return exc_info.value.code


def test_exit_code_one_when_rmtree_fails_and_continues(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """1件の削除が失敗しても残りを削除し、終了コード1で終える。"""
    (tmp_path / "cache_a").mkdir()
    (tmp_path / "cache_b").mkdir()
    real_rmtree = shutil.rmtree

    def flaky_rmtree(path: str | pathlib.Path, **kwargs: typing.Any) -> None:
        del kwargs
        if pathlib.Path(path).name == "cache_a":
            raise PermissionError("denied")
        real_rmtree(path)

    monkeypatch.setattr(shutil, "rmtree", flaky_rmtree)

    assert _run_main(monkeypatch, "^cache_", str(tmp_path)) == 1
    assert (tmp_path / "cache_a").exists()
    assert not (tmp_path / "cache_b").exists()
    assert "rmtree failed" in capsys.readouterr().out


def test_exit_code_zero_without_failure(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """失敗が無ければ一致したディレクトリを削除して終了コード0で終える。"""
    (tmp_path / "cache_a").mkdir()
    (tmp_path / "keep").mkdir()

    assert _run_main(monkeypatch, "^cache_", str(tmp_path)) == 0
    assert not (tmp_path / "cache_a").exists()
    assert (tmp_path / "keep").exists()
