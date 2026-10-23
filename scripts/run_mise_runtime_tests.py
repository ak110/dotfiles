"""miseのshimを使うことを確かめ、同じプロセスから対象のpytestを起動する。

リポジトリの開発環境で`uv run --frozen python scripts/run_mise_runtime_tests.py
<runtime> -- <pytest引数...>`として使う。pytestの終了コードをそのまま返し、
miseの照会・応答・runtime解決に問題があれば試験を起動せず終了コード2で終える。
"""

from __future__ import annotations

import argparse
import collections.abc
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

from pytools._internal.common import format_cli_error


def main(argv: collections.abc.Sequence[str] | None = None) -> int:
    """shimの所在とruntimeを照合し、引数を保持してpytestへ渡す。"""
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        description="miseのshim経由でruntimeが解決されることを確かめてpytestを実行する。",
        epilog="pytestには-v -n 0 -p no:cacheproviderを先に渡す。以降のpytest引数は順序を保って渡す。",
    )
    parser.add_argument("runtime", help="shimの所在を確認するruntimeのコマンド名（例: node）")
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER, help="--に続けて渡すpytestの対象・引数")
    args = parser.parse_args(argv)
    try:
        doctor = subprocess.run(
            ["mise", "doctor", "--json"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as error:
        print(
            f"mise doctorの起動に失敗した: {error}。{format_cli_error(None)}。miseの導入とPATHを確認する。",
            file=sys.stderr,
        )
        return 2
    if doctor.returncode != 0:
        print(
            f"mise doctor --jsonに失敗した: {format_cli_error(doctor)}。表示された原因を解消して再実行する。",
            file=sys.stderr,
        )
        return 2
    try:
        shims_value = json.loads(doctor.stdout)["dirs"]["shims"]
        if not isinstance(shims_value, str) or not shims_value or not pathlib.Path(shims_value).is_absolute():
            raise ValueError("dirs.shimsには空でない絶対パスが必要である")
    except (KeyError, TypeError, ValueError) as error:
        print(f"mise doctorのdirs.shimsを取得できない: {error}。mise doctor --jsonの応答を確認する。", file=sys.stderr)
        return 2

    previous_path = os.environ.get("PATH")
    os.environ["PATH"] = shims_value + (os.pathsep + previous_path if previous_path else "")
    try:
        resolved = shutil.which(args.runtime)
        if resolved is None or pathlib.Path(resolved).parent != pathlib.Path(shims_value):
            print(
                f"runtimeがmiseのshimへ解決されない: {args.runtime} -> {resolved}; shims={shims_value}。"
                "miseのruntime設定とshimを確認する。",
                file=sys.stderr,
            )
            return 2
        print("runtime:", resolved, flush=True)
        pytest_args = args.pytest_args
        if pytest_args and pytest_args[0] == "--":
            pytest_args = pytest_args[1:]
        return int(pytest.main(["-v", "-n", "0", "-p", "no:cacheprovider", *pytest_args]))
    finally:
        if previous_path is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = previous_path


if __name__ == "__main__":
    sys.exit(main())
