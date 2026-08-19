"""agent-toolkitプラグイン配下の`atk config`サブコマンド用補助モジュール。

PEP 723 entrypoint`atk.py`と同一ディレクトリに配置され、`sys.path`挿入で相互import可能。
XDG関連パス（設定・状態・データ各ディレクトリ、private-notesの解決結果）の確認と、
工程別モデル設定の確認・変更を提供する。
"""

import argparse
import json
import pathlib
import re
import sys

import platformdirs
from _atk_mq_common import _private_notes_path

_CONFIG_FILENAME = "config.json"

_DEFAULT_STAGE_MODEL = "codex:gpt-5.6-sol/medium"
_ORCHESTRATE_MODEL_DEFAULT = "claude:opus[1m]/medium"
_MUTABLE_KEY_DEFAULTS = {
    "pick_feedbacks_model": _DEFAULT_STAGE_MODEL,
    "plan_model": _DEFAULT_STAGE_MODEL,
    "plan_review_model": _DEFAULT_STAGE_MODEL,
    "execute_model": _DEFAULT_STAGE_MODEL,
    "execute_review_model": _DEFAULT_STAGE_MODEL,
    "merge_model": _DEFAULT_STAGE_MODEL,
    "orchestrate_model": _ORCHESTRATE_MODEL_DEFAULT,
}
_STAGE_MODEL_PATTERN = re.compile(r"^(?:claude|codex):[^/]+(?:/[^/]+)?$")
# 主に使うモデル名・effortの参考一覧。受理可否の判定には使わず、一覧外は警告のみで受理する。
_KNOWN_MODELS = {
    "claude": frozenset({"haiku", "sonnet", "opus", "sonnet[1m]", "opus[1m]"}),
    "codex": frozenset({"gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"}),
}
_KNOWN_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})


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
        **{key: config.get(key, default) for key, default in _MUTABLE_KEY_DEFAULTS.items()},
    }


def _cmd_config_show(home: pathlib.Path) -> None:
    """showサブコマンド: 解決済み設定を一覧表示する。"""
    for key, value in _resolved_settings(home).items():
        print(f"{key}: {value}")


def _cmd_config_get(args: argparse.Namespace, home: pathlib.Path) -> None:
    """getサブコマンド: 1件以上の設定値を表示する。未知キーはexit 2。"""
    settings = _resolved_settings(home)
    unknown_keys = [key for key in args.key if key not in settings]
    if unknown_keys:
        print(
            f"未知の設定キーです: {', '.join(unknown_keys)}（利用可能: {', '.join(sorted(settings))}）",
            file=sys.stderr,
        )
        sys.exit(2)
    for key in args.key:
        print(settings[key])


def _cmd_config_set(args: argparse.Namespace) -> None:
    """setサブコマンド: 変更可能設定を更新する。対象外キーはexit 2。"""
    if args.key not in _MUTABLE_KEY_DEFAULTS:
        print(
            f"変更できない設定キーです: {args.key}（変更可能: {', '.join(sorted(_MUTABLE_KEY_DEFAULTS))}）",
            file=sys.stderr,
        )
        sys.exit(2)
    if _STAGE_MODEL_PATTERN.fullmatch(args.value) is None:
        print(
            "設定値の書式が不正です。受理可能書式: <claude|codex>:<model>[/<effort>]",
            file=sys.stderr,
        )
        sys.exit(2)
    engine, model, effort = _parse_stage_model(args.value)
    if model not in _KNOWN_MODELS[engine]:
        print(
            f"警告: モデル名`{model}`は主に使うモデルの一覧（{', '.join(sorted(_KNOWN_MODELS[engine]))}）にありません。"
            "設定は保存します。利用可否は実行時に各engineが判定します。",
            file=sys.stderr,
        )
    if effort is not None and effort not in _KNOWN_EFFORTS:
        print(
            f"警告: effort`{effort}`は主に使う値の一覧（{', '.join(sorted(_KNOWN_EFFORTS))}）にありません。"
            "設定は保存します。利用可否は実行時に各engineが判定します。",
            file=sys.stderr,
        )
    config = _load_config()
    config[args.key] = args.value
    _save_config(config)
    print(f"設定を更新しました: {args.key}={args.value}")


def _parse_stage_model(value: str) -> tuple[str, str, str | None]:
    """検証済み設定値をengine・model・effort（未指定はNone）へ分解する。"""
    engine, _, rest = value.partition(":")
    model, effort_sep, effort = rest.partition("/")
    return engine, model, effort if effort_sep else None


def build_parser(config: argparse.ArgumentParser) -> None:
    """`config`サブパーサ配下にshow/get/setサブコマンドを登録する。"""
    sub = config.add_subparsers(dest="config_subcommand")
    sub.add_parser("show", help="XDG関連パスと工程別モデル設定を一覧表示する（既定動作）")
    get = sub.add_parser("get", help="1件以上の設定値を取得する")
    get.add_argument("key", metavar="KEY", nargs="+", help="取得する1件以上のキー（config showの出力キーと同一）。")
    set_ = sub.add_parser("set", help="変更可能な設定値を更新する")
    set_.add_argument("key", metavar="KEY", help=f"変更可能なキー: {', '.join(sorted(_MUTABLE_KEY_DEFAULTS))}")
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
