"""agent-toolkitの状態ディレクトリと、その配下に置くロックファイルのディレクトリの解決。

状態ファイルを書く処理と読む処理（hook、`atk`、agents_server、`atk serve`のSSH先のヘルパー）が同じ位置を得るため、
解決の規則を本モジュールの`state_dir`だけに置く。
SSH先のヘルパーは標準ライブラリと`platformdirs`だけを前提に動き、`platformdirs`を持たない環境でも
本モジュールのimportだけは成立させるため、`platformdirs`は解決時に遅延importする。
"""

from __future__ import annotations

import os
import pathlib

_APP_NAME = "agent-toolkit"


def state_dir(*, os_name: str | None = None) -> pathlib.Path:
    """agent-toolkitの状態ファイルを置くディレクトリを返す。

    `atk config get state_dir`の出力と、状態ファイルを書く・読む全ての処理がこの値を使う。
    Linuxなどでは`platformdirs`の`user_state_dir`に従い、絶対パスの`XDG_STATE_HOME`だけを受理して、
    相対値は`HOME/.local/state`へ退避する。相対値は作業ディレクトリごとに別の場所を指すためである。
    Windowsでは`LOCALAPPDATA`が設定されていればその配下を使い、未設定なら`platformdirs`の値を使う。
    通常の環境では両者は同じ位置を指し、環境変数を使うと隔離した環境で位置を変えられる。
    `appauthor=False`はWindowsでappnameが二重階層になる挙動を防ぐ。
    `os_name`は`os.name`と同じ値で分岐を選び、省略すると実行中のOSの値を使う。
    """
    if (os.name if os_name is None else os_name) == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return pathlib.Path(local_app_data) / _APP_NAME
    else:
        xdg_state_home = os.environ.get("XDG_STATE_HOME")
        if xdg_state_home and not pathlib.Path(xdg_state_home).is_absolute():
            return pathlib.Path.home() / ".local" / "state" / _APP_NAME
    import platformdirs  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

    return pathlib.Path(platformdirs.user_state_dir(_APP_NAME, appauthor=False))


def lock_dir() -> pathlib.Path:
    """複数のプロセスが排他に使うロックファイルを置くディレクトリ（状態ディレクトリ配下の`locks/`）を返す。

    agents_serverの待機のロック（`agents-server/wait-locks/`）は共有状態の登録と組で働くため、本ディレクトリを使わない。
    """
    return state_dir() / "locks"
