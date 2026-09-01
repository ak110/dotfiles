"""起動元ツールのエフェメラル仮想環境を子プロセス環境から取り除く共通処理。

`uv run --no-project --script`で起動したツールは、PEP 723のエフェメラル環境を指す
`VIRTUAL_ENV`と、当該環境のコマンド格納ディレクトリを先頭へ挿入した`PATH`を持つ。
この環境を子セッションへ引き継ぐと、作業対象リポジトリでのパッケージ操作と
`python`・`pip`・コンソールスクリプトの解決が起動元ツールの環境を対象にする。

`VIRTUAL_ENV`だけを取り除くと`PATH`側が残り、解決先は起動元ツールの環境のままになる。
本モジュールは両方を同じ契約で取り除く処理を、`atk`のprocess-loopと委譲サーバーの
双方へ提供する。
"""

from __future__ import annotations

import os
import pathlib
from collections.abc import MutableMapping

# `uv run`が設定する仮想環境の印。実測で子プロセスへ混入したキーだけを除去対象とする。
INHERITED_VENV_ENV_KEYS: tuple[str, ...] = ("VIRTUAL_ENV",)

# 仮想環境のコマンド格納ディレクトリ名（POSIXは`bin`、Windowsは`Scripts`）。
VENV_BIN_DIR_NAMES: tuple[str, ...] = ("bin", "Scripts")


def strip_inherited_venv(env: MutableMapping[str, str]) -> None:
    """起動元ツールのエフェメラル仮想環境を渡された環境から取り除く。

    除去対象は`PATH`の全要素ではなく、除去する`VIRTUAL_ENV`の値から導いた
    コマンド格納ディレクトリと一致する要素だけとする。
    POSIXの`PATH`では空要素がカレントディレクトリを表すため、空要素は解決順序を保つよう残す。
    """
    venv_roots = [value for key in INHERITED_VENV_ENV_KEYS if (value := env.get(key))]
    for key in INHERITED_VENV_ENV_KEYS:
        env.pop(key, None)
    path_value = env.get("PATH")
    if not venv_roots or path_value is None:
        return
    venv_bin_dirs = {pathlib.Path(root) / name for root in venv_roots for name in VENV_BIN_DIR_NAMES}
    remaining = [entry for entry in path_value.split(os.pathsep) if not entry or pathlib.Path(entry) not in venv_bin_dirs]
    env["PATH"] = os.pathsep.join(remaining)
