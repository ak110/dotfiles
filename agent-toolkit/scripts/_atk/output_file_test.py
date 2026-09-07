"""標準出力のファイル保存処理を検証する。"""

from __future__ import annotations

import argparse
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import atk  # noqa: E402  # pylint: disable=wrong-import-position

from _atk import output_file  # noqa: E402  # pylint: disable=wrong-import-position


def test_redirect_writes_stdout_to_file_and_prints_path_and_line_count(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output_path = tmp_path / "output.txt"

    with output_file.redirect(output_path):
        print("1行目")
        print("2行目")

    assert output_path.read_text(encoding="utf-8") == "1行目\n2行目\n"
    assert capsys.readouterr().out == f"保存先: {output_path.resolve()}\n行数: 2\n"


def test_relative_output_file_is_rejected(tmp_path: pathlib.Path) -> None:
    parser = argparse.ArgumentParser()
    output_file.add_output_file_arg(parser)
    assert parser.parse_args(["--output-file", "relative.txt"]).output_file == pathlib.Path("relative.txt")

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["plans", "list", "--output-file", "relative.txt"], home=tmp_path)

    assert exc_info.value.code == 2
