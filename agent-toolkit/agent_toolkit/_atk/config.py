"""agent-toolkitプラグイン配下の`atk config`サブコマンド用補助モジュール。

PEP 723 entrypoint`atk.py`と同一ディレクトリに配置され、`sys.path`挿入で相互import可能。
XDG関連パス（設定・状態・データ各ディレクトリ、private-notesの解決結果）の確認と、
工程別モデル設定の確認・変更を提供する。
"""

import argparse
import importlib
import json
import os
import pathlib
import re
import sys
from typing import cast

import platformdirs

from agent_toolkit._atk import help_text as _atk_help

_CONFIG_FILENAME = "config.json"

_MODEL_SETTING_CATEGORIES = {
    "explore_model": "上位",
    "explore_fast_model": "軽量",
    "pick_wi_model": "軽量",
    "plan_model": "計画",
    "plan_review_model": "軽量",
    "execute_fast_model": "軽量",
    "execute_model": "上位",
    "execute_review_model": "軽量",
    "session_review_model": "上位",
    "orchestrate_model": "上位",
}
_CATEGORY_ENGINE_MODELS = {
    "上位": {
        "codex": "codex:gpt-5.6-sol/medium",
        "claude": "claude:opus[1m]/medium",
    },
    "軽量": {
        "codex": "codex:gpt-5.6-terra/medium",
        "claude": "claude:sonnet[1m]/medium",
    },
    "計画": {
        "codex": "codex:gpt-6-astra/medium",
        "claude": "claude:opus[1m]/medium",
    },
}
_PRESET_ENGINE_ORDERS = {
    "codex-balanced": ("codex", frozenset({"plan_model", "orchestrate_model"})),
    "codex-primary": ("codex", frozenset()),
    "claude-balanced": ("claude", frozenset({"explore_model", "explore_fast_model", "execute_fast_model"})),
    "claude-primary": ("claude", frozenset()),
}


def _preset_settings(preset: str) -> dict[str, str]:
    """プリセットのengine順と用途区分から工程別モデル設定を導出する。"""
    primary_engine, reversed_keys = _PRESET_ENGINE_ORDERS[preset]
    other_engine = "claude" if primary_engine == "codex" else "codex"
    settings: dict[str, str] = {}
    for key, category in _MODEL_SETTING_CATEGORIES.items():
        first_engine, second_engine = (other_engine, primary_engine) if key in reversed_keys else (primary_engine, other_engine)
        models = _CATEGORY_ENGINE_MODELS[category]
        settings[key] = f"{models[first_engine]},{models[second_engine]}"
    return settings


_MUTABLE_KEY_DEFAULTS = _preset_settings("codex-balanced")
_STAGE_MODEL_PATTERN = re.compile(r"^(?:claude|codex):[^/,\s]+(?:/[^/,\s]+)?$")
_CONFIG_ENV_PREFIX = "AGENT_TOOLKIT_CONFIG_"
# 主に使うモデル名・effortの参考一覧。受理可否の判定には使わず、一覧外は警告のみで受理する。
_KNOWN_MODELS = {
    "claude": frozenset({"haiku", "sonnet", "opus", "fable", "sonnet[1m]", "opus[1m]"}),
    "codex": frozenset({"gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-6-astra"}),
}
_KNOWN_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})


def _config_dir() -> pathlib.Path:
    """platformdirsの設定ディレクトリ解決規約に従い、設定ファイル配置ディレクトリを返す。

    `appauthor=False`はWindowsでappnameが二重階層になる挙動を防ぐ。
    """
    return pathlib.Path(platformdirs.user_config_dir("agent-toolkit", appauthor=False))


def state_dir() -> pathlib.Path:
    """状態ファイル配置ディレクトリを返す。

    `appauthor=False`はWindowsでappnameが二重階層になる挙動を防ぐ。
    `atk config get state_dir`の出力と、フックが状態ファイルを置く位置の双方をここで決める。
    Linuxでは絶対パスの`XDG_STATE_HOME`だけを受理し、相対値は`HOME/.local/state`へ退避する。
    """
    resolved = pathlib.Path(platformdirs.user_state_dir("agent-toolkit", appauthor=False))
    xdg_state_home = os.environ.get("XDG_STATE_HOME")
    if os.name != "nt" and xdg_state_home and not pathlib.Path(xdg_state_home).is_absolute():
        return pathlib.Path.home() / ".local" / "state" / "agent-toolkit"
    return resolved


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


def _config_env_name(key: str) -> str:
    """変更可能設定キーに対応する環境変数名を返す。"""
    return f"{_CONFIG_ENV_PREFIX}{key.upper()}"


def _validate_stage_model_candidates(value: str) -> None:
    """候補列の各候補を検証し、書式不正なら`ValueError`を送出する。"""
    candidates = value.split(",")
    if not candidates or any(_STAGE_MODEL_PATTERN.fullmatch(candidate) is None for candidate in candidates):
        raise ValueError("受理可能書式: <claude|codex>:<model>[/<effort>]（複数候補はASCIIカンマ区切り）")


def resolve_mutable_setting(key: str) -> str:
    """変更可能設定を環境変数、保存値、既定値の優先順で解決する。"""
    if key not in _MUTABLE_KEY_DEFAULTS:
        raise KeyError(key)
    env_name = _config_env_name(key)
    env_value = os.environ.get(env_name, "")
    value = env_value or _load_config().get(key, _MUTABLE_KEY_DEFAULTS[key])
    try:
        _validate_stage_model_candidates(value)
    except ValueError as error:
        source = f"環境変数{env_name}" if env_value else f"設定キー{key}"
        raise ValueError(f"{source}の値が不正です（値: {value}）。{error}") from error
    return value


def _resolved_settings(home: pathlib.Path) -> dict[str, str]:
    """XDG関連パスの導出値と変更可能設定をまとめて返す（表示・`get`共通の解決結果）。"""
    private_notes_path = vars(importlib.import_module("agent_toolkit._atk.wi.common"))["_private_notes_path"]

    # Windowsでappnameがappauthorとしても付与される二重階層を防ぐ。
    return {
        "config_dir": str(_config_dir()),
        "state_dir": str(state_dir()),
        "data_dir": str(pathlib.Path(platformdirs.user_data_dir("agent-toolkit", appauthor=False))),
        "private_notes": str(private_notes_path(home)),
        **{key: resolve_mutable_setting(key) for key in _MUTABLE_KEY_DEFAULTS},
    }


def _stage_model_candidate_warnings(key: str, value: str) -> list[str]:
    """参考一覧外の工程別モデル候補を警告文へ変換する。"""
    warnings: list[str] = []
    for candidate in value.split(","):
        engine, model, effort = _parse_stage_model(candidate)
        models = ", ".join(sorted(_KNOWN_MODELS[engine]))
        if model not in _KNOWN_MODELS[engine]:
            warnings.append(
                f"警告: 設定キー`{key}`の候補`{candidate}`のモデル名`{model}`は主に使うモデルの一覧（{models}）にありません。"
                "利用可否は実行時に各engineが判定します。"
            )
        if effort is not None and effort not in _KNOWN_EFFORTS:
            efforts = ", ".join(sorted(_KNOWN_EFFORTS))
            warnings.append(
                f"警告: 設定キー`{key}`の候補`{candidate}`のeffort`{effort}`は主に使う値の一覧（{efforts}）にありません。"
                "利用可否は実行時に各engineが判定します。"
            )
    return warnings


def _cmd_config_show(home: pathlib.Path) -> None:
    """showサブコマンド: 解決済み設定を一覧表示する。"""
    for key, value in _resolved_settings(home).items():
        print(f"{key}: {value}")
        if key in _MUTABLE_KEY_DEFAULTS:
            for warning in _stage_model_candidate_warnings(key, value):
                print(warning, file=sys.stderr)


def _cmd_config_get(args: argparse.Namespace, home: pathlib.Path) -> None:
    """getサブコマンド: 1件以上の設定値を表示する。未知キーはexit 2。"""
    settings = _resolved_settings(home)
    requested_keys = cast(list[str], args.key)
    unknown_keys = [key for key in requested_keys if key not in settings]
    if unknown_keys:
        print(
            f"未知の設定キーです: {', '.join(unknown_keys)}（利用可能: {', '.join(sorted(settings))}）",
            file=sys.stderr,
        )
        sys.exit(2)
    for key in requested_keys:
        print(settings[key])


def _cmd_config_set(args: argparse.Namespace) -> None:
    """setサブコマンド: 変更可能設定を更新する。対象外キーはexit 2。"""
    if args.key not in _MUTABLE_KEY_DEFAULTS:
        print(
            f"変更できない設定キーです: {args.key}（変更可能: {', '.join(sorted(_MUTABLE_KEY_DEFAULTS))}）",
            file=sys.stderr,
        )
        sys.exit(2)
    try:
        _validate_stage_model_candidates(args.value)
    except ValueError as error:
        print(
            f"設定値の書式が不正です。{error}",
            file=sys.stderr,
        )
        sys.exit(2)
    for warning in _stage_model_candidate_warnings(args.key, args.value):
        print(warning, file=sys.stderr)
    print("設定は保存します。", file=sys.stderr)
    config = _load_config()
    config[args.key] = args.value
    _save_config(config)
    print(f"設定を更新しました: {args.key}={args.value}")
    env_name = _config_env_name(args.key)
    if os.environ.get(env_name, ""):
        print(
            f"警告: 環境変数{env_name}が優先されるため、解除するまで更新値は実効値になりません。",
            file=sys.stderr,
        )


def _cmd_config_apply_preset(args: argparse.Namespace) -> None:
    """apply-presetサブコマンド: 工程別モデル設定10キーを一括保存する。"""
    settings = _preset_settings(args.preset)
    config = _load_config()
    config.update(settings)
    _save_config(config)
    for key, value in settings.items():
        print(f"{key}: {value}")


def _parse_stage_model(value: str) -> tuple[str, str, str | None]:
    """検証済み設定値をengine・model・effort（未指定はNone）へ分解する。"""
    engine, _, rest = value.partition(":")
    model, effort_sep, effort = rest.partition("/")
    return engine, model, effort if effort_sep else None


def parse_stage_model_candidates(value: str) -> list[tuple[str, str, str]]:
    """候補列をengine・model・effortの3つ組へ分解し、effort省略時は`medium`を補う。"""
    _validate_stage_model_candidates(value)
    return [
        (engine, model, effort or "medium")
        for engine, model, effort in (_parse_stage_model(candidate) for candidate in value.split(","))
    ]


def resolve_model_candidates(model_type: str) -> list[tuple[str, str, str]]:
    """model_typeに対応する工程別モデル設定を候補の3つ組として返す。

    設定値と同じ書式の候補列を受け取った場合は設定を読まず、当該候補列をそのまま分解して返す。
    """
    key = f"{model_type}_model"
    if key not in _MUTABLE_KEY_DEFAULTS:
        try:
            return parse_stage_model_candidates(model_type)
        except ValueError as error:
            available = sorted(item.removesuffix("_model") for item in _MUTABLE_KEY_DEFAULTS if item.endswith("_model"))
            raise ValueError(
                f"unknown model_type: {model_type} "
                f"(available: {', '.join(available)}; or pass candidates like codex:gpt-5.6-sol/medium)"
            ) from error
    return parse_stage_model_candidates(resolve_mutable_setting(key))


def build_parser(config: argparse.ArgumentParser) -> None:
    """`config`サブパーサ配下にshow/get/set/apply-presetを登録する。"""
    sub = _atk_help.add_subcommands(config, dest="config_subcommand", required=False)
    _atk_help.add_command(sub, "show", **_atk_help.HELP["atk config show"])
    get = _atk_help.add_command(sub, "get", **_atk_help.HELP["atk config get"])
    get.add_argument("key", metavar="KEY", nargs="+", help="取得する1件以上のキー（config showの出力キーと同一）。")
    set_ = _atk_help.add_command(sub, "set", **_atk_help.HELP["atk config set"])
    set_.add_argument("key", metavar="KEY", help=f"変更可能なキー: {', '.join(sorted(_MUTABLE_KEY_DEFAULTS))}")
    set_.add_argument("value", metavar="VALUE", help="設定する値。複数候補はASCIIカンマ区切りで指定できる。")
    apply_preset = _atk_help.add_command(sub, "apply-preset", **_atk_help.HELP["atk config apply-preset"])
    apply_preset.add_argument("preset", choices=tuple(_PRESET_ENGINE_ORDERS), help="適用するプリセット名。")


def dispatch(args: argparse.Namespace, home: pathlib.Path) -> None:
    """`config`サブコマンドを実行しexit 0で終了する（サブコマンド省略時は`show`扱い）。"""
    sub = getattr(args, "config_subcommand", None) or "show"
    try:
        if sub == "show":
            _cmd_config_show(home)
        elif sub == "get":
            _cmd_config_get(args, home)
        elif sub == "apply-preset":
            _cmd_config_apply_preset(args)
        else:
            _cmd_config_set(args)
    except ValueError as error:
        print(error, file=sys.stderr)
        sys.exit(2)
    sys.exit(0)
