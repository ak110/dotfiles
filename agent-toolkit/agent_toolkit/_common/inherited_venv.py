"""起動元ツールのplugin仮想環境を子プロセス環境から取り除く共通処理。

plugin rootを`uv run --project`へ指定して起動したツールは、pluginの仮想環境を指す
`VIRTUAL_ENV`と、その仮想環境のコマンド格納ディレクトリを先頭へ挿入した`PATH`を持つ。
この環境を子セッションへ引き継ぐと、対象リポジトリでのパッケージ操作と
`python`・`pip`・コンソールスクリプトの解決が起動元ツールの環境を対象にする。

`VIRTUAL_ENV`だけを取り除くと`PATH`側が残り、解決先は起動元ツールの環境のままになる。
本モジュールは両方を同じ契約で取り除く処理を、`atk`のprocess-loop、laneの内部実行と
委譲サーバーへ提供する。
"""

from __future__ import annotations

import json
import os
import pathlib
from collections.abc import MutableMapping

# `uv run`が設定する仮想環境の印。子プロセスへの引き継ぎを実際に観測したキーだけを除去対象とする。
INHERITED_VENV_ENV_KEYS: tuple[str, ...] = ("VIRTUAL_ENV",)
PLUGIN_RUNTIME_ENVIRONMENT_KEY = "AGENT_TOOLKIT_RUNTIME_ENVIRONMENT"
CALLER_PROJECT_ENVIRONMENT_KEY = "AGENT_TOOLKIT_CALLER_UV_PROJECT_ENVIRONMENT"
LAUNCH_RESTORE_KEY = "AGENT_TOOLKIT_LAUNCH_RESTORE"

# 仮想環境のコマンド格納ディレクトリ名（POSIXは`bin`、Windowsは`Scripts`）。
VENV_BIN_DIR_NAMES: tuple[str, ...] = ("bin", "Scripts")


def strip_inherited_venv(env: MutableMapping[str, str]) -> None:
    """起動元ツールのplugin仮想環境を渡された環境から取り除く。

    除去対象は、除去する`VIRTUAL_ENV`の値から導いたコマンド格納ディレクトリと
    一致する`PATH`の要素だけとする。
    POSIXの`PATH`では空要素がカレントディレクトリを表すため、空要素は解決順序を保つよう残す。
    """
    _restore_launch_environment(env)
    runtime = env.pop(PLUGIN_RUNTIME_ENVIRONMENT_KEY, None)
    caller_project = env.pop(CALLER_PROJECT_ENVIRONMENT_KEY, None)
    if runtime and env.get("UV_PROJECT_ENVIRONMENT") == runtime:
        if caller_project is None:
            env.pop("UV_PROJECT_ENVIRONMENT", None)
        else:
            env["UV_PROJECT_ENVIRONMENT"] = caller_project
    venv_roots = [value for key in INHERITED_VENV_ENV_KEYS if (value := env.get(key))]
    for key in INHERITED_VENV_ENV_KEYS:
        env.pop(key, None)
    path_value = env.get("PATH")
    if not venv_roots or path_value is None:
        return
    venv_bin_dirs = {pathlib.Path(root).absolute() / name for root in venv_roots for name in VENV_BIN_DIR_NAMES}
    remaining = [
        entry for entry in path_value.split(os.pathsep) if not entry or pathlib.Path(entry).absolute() not in venv_bin_dirs
    ]
    env["PATH"] = os.pathsep.join(remaining)


def record_launch_changes(env: MutableMapping[str, str], caller: MutableMapping[str, str]) -> None:
    """起動層が変えた値を、子へ渡す直前に復元できる形で記録する。"""
    env.pop(LAUNCH_RESTORE_KEY, None)
    changes = {
        key: [caller.get(key), value]
        for key, value in env.items()
        if key != "AGENT_TOOLKIT_RUNTIME_DATA" and caller.get(key) != value
    }
    changes.update({key: [value, None] for key, value in caller.items() if key not in env})
    if changes:
        env[LAUNCH_RESTORE_KEY] = json.dumps(changes, ensure_ascii=True, sort_keys=True)


def _restore_launch_environment(env: MutableMapping[str, str]) -> None:
    """記録された起動層の差分だけを呼出元の値へ戻す。"""
    encoded = env.pop(LAUNCH_RESTORE_KEY, None)
    if encoded is None:
        return
    try:
        changes = json.loads(encoded)
    except json.JSONDecodeError:
        return
    if not isinstance(changes, dict):
        return
    for key, values in changes.items():
        if not isinstance(key, str) or not isinstance(values, list) or len(values) != 2:
            continue
        caller, launched = values
        if (caller is not None and not isinstance(caller, str)) or (launched is not None and not isinstance(launched, str)):
            continue
        if env.get(key) != launched:
            continue
        if caller is None:
            env.pop(key, None)
        else:
            env[key] = caller
