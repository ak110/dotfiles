"""agent-toolkitプラグイン配下の`atk config`サブコマンド用補助モジュール。

PEP 723 entrypoint`atk.py`と同一ディレクトリに配置され、`sys.path`挿入で相互import可能。
XDG関連パス（設定・状態・データ各ディレクトリ、private-notesの解決結果）の確認と、
codexモデル判定設定（`codex_model`）の確認・変更を提供する。
"""

import argparse
import json
import pathlib
import sys

import platformdirs
from _atk_mq_common import _private_notes_path

_CONFIG_FILENAME = "config.json"

# 変更可能な設定キー。XDG関連パスは`platformdirs`からの導出値のため読み取り専用とし、
# `codex_model`のみ`atk config set`での変更対象とする。
_MUTABLE_KEYS = frozenset(("codex_model",))


def _config_dir() -> pathlib.Path:
    """platformdirsの設定ディレクトリ解決規約に従い、設定ファイル配置ディレクトリを返す。

    `appauthor=False`はWindowsでappnameが二重階層になる挙動を防ぐ。
    """
    return pathlib.Path(platformdirs.user_config_dir("agent-toolkit", appauthor=False))


def _config_file_path() -> pathlib.Path:
    """変更可能設定を永続化するJSONファイルの絶対パスを返す。"""
    return _config_dir() / _CONFIG_FILENAME


def _load_config() -> dict[str, str]:
    """永続化済みの変更可能設定を読み込む。ファイル不在・破損時は空辞書を返す。"""
    path = _config_file_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {key: value for key, value in data.items() if isinstance(value, str)}


def _save_config(config: dict[str, str]) -> None:
    """変更可能設定をJSONファイルへ永続化する。"""
    path = _config_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _resolved_settings(home: pathlib.Path) -> dict[str, str]:
    """XDG関連パスの導出値と変更可能設定をまとめて返す（表示・`get`共通の解決結果）。"""
    config = _load_config()
    # Windowsでappnameがappauthorとしても付与される二重階層を防ぐ。
    return {
        "config_dir": str(_config_dir()),
        "state_dir": str(pathlib.Path(platformdirs.user_state_dir("agent-toolkit", appauthor=False))),
        "data_dir": str(pathlib.Path(platformdirs.user_data_dir("agent-toolkit", appauthor=False))),
        "private_notes": str(_private_notes_path(home)),
        "codex_model": config.get("codex_model", "(未設定)"),
    }


def _cmd_config_show(home: pathlib.Path) -> None:
    """showサブコマンド: 解決済み設定を一覧表示する。"""
    for key, value in _resolved_settings(home).items():
        print(f"{key}: {value}")


def _cmd_config_get(args: argparse.Namespace, home: pathlib.Path) -> None:
    """getサブコマンド: 単一設定値を表示する。未知キーはexit 2。"""
    settings = _resolved_settings(home)
    if args.key not in settings:
        print(f"未知の設定キーです: {args.key}（利用可能: {', '.join(sorted(settings))}）", file=sys.stderr)
        sys.exit(2)
    print(settings[args.key])


def _cmd_config_set(args: argparse.Namespace) -> None:
    """setサブコマンド: 変更可能設定を更新する。対象外キーはexit 2。"""
    if args.key not in _MUTABLE_KEYS:
        print(
            f"変更できない設定キーです: {args.key}（変更可能: {', '.join(sorted(_MUTABLE_KEYS))}）",
            file=sys.stderr,
        )
        sys.exit(2)
    config = _load_config()
    config[args.key] = args.value
    _save_config(config)
    print(f"設定を更新しました: {args.key}={args.value}")


def build_parser(config: argparse.ArgumentParser) -> None:
    """`config`サブパーサ配下にshow/get/setサブコマンドを登録する。"""
    sub = config.add_subparsers(dest="config_subcommand")
    sub.add_parser("show", help="XDG関連パスとcodexモデル判定設定を一覧表示する（既定動作）")
    get = sub.add_parser("get", help="単一設定値を取得する")
    get.add_argument("key", metavar="KEY", help="取得するキー（config showの出力キーと同一）。")
    set_ = sub.add_parser("set", help="変更可能な設定値を更新する")
    set_.add_argument("key", metavar="KEY", help=f"変更可能なキー: {', '.join(sorted(_MUTABLE_KEYS))}")
    set_.add_argument("value", metavar="VALUE", help="設定する値。")


def dispatch(args: argparse.Namespace, home: pathlib.Path) -> None:
    """`config`サブコマンドを実行しexit 0で終了する（サブコマンド省略時は`show`扱い）。"""
    sub = getattr(args, "config_subcommand", None) or "show"
    if sub == "show":
        _cmd_config_show(home)
    elif sub == "get":
        _cmd_config_get(args, home)
    else:
        _cmd_config_set(args)
    sys.exit(0)
