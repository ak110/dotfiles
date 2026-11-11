"""設定の公開CLIと実効値・モデル候補の解決。"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from typing import Any, cast

lazy import platformdirs

from agent_toolkit._atk import config_migration as _config_migration
from agent_toolkit._atk import config_repositories_cli
from agent_toolkit._atk import config_settings as _config_settings
from agent_toolkit._atk import help_text as _atk_help
from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._atk.config_migration import migrate_serve_settings
from agent_toolkit._atk.config_settings import (
    CONNECTION_KINDS,
    MODEL_TYPES,
    ConfiguredRepository,
    atomic_write,
    config_environment_variable_names,
    connection_order_key,
    mutable_setting_default,
    parse_remote_hosts,
    process_loop_configuration_path,
    resolve_configured_repositories,
    serve_default_port,
)
from agent_toolkit._atk.config_settings import MUTABLE_KEY_DEFAULTS as _MUTABLE_KEY_DEFAULTS
from agent_toolkit._atk.config_settings import RETIRED_CONNECTION_KEYS as _RETIRED_CONNECTION_KEYS
from agent_toolkit._atk.config_settings import config_env_name as _config_env_name
from agent_toolkit._atk.config_settings import config_file_path as _config_file_path
from agent_toolkit._common import codex_models
from agent_toolkit._common import next_action as _next_action
from agent_toolkit._common import private_notes as _private_notes
from agent_toolkit._common import state_paths as _state_paths

__all__ = [
    "CONNECTION_KINDS",
    "ConfiguredRepository",
    "MODEL_TYPES",
    "_MUTABLE_KEY_DEFAULTS",
    "_RETIRED_CONNECTION_KEYS",
    "_config_env_name",
    "_config_file_path",
    "atomic_write",
    "config_environment_variable_names",
    "connection_order_key",
    "migrate_serve_settings",
    "mutable_setting_default",
    "parse_remote_hosts",
    "process_loop_configuration_path",
    "resolve_configured_repositories",
    "serve_default_port",
]


def connection_order(engine: str, role: str) -> tuple[str, ...]:
    """エンジンと役割の使用順を、設定の解決結果から種別のタプルで返す。"""
    return _config_settings.parse_connection_order(resolve_mutable_setting(_config_settings.connection_order_key(engine, role)))


def connections_configured(engine: str) -> bool:
    """エンジンの認証キー2つと使用順2つのいずれかを、環境変数か保存値で設定しているかを返す。

    どれも設定していない環境は、接続を管理せず親環境と通常の認証解決を引き継ぐ。
    使用順には初期値があるため、実効値ではなく明示の有無で判定する。
    """
    keys = [key for key in _config_settings.CONNECTION_DEFAULTS if key.startswith(f"{engine}_")]
    if not keys:
        raise KeyError(engine)
    _config_migration.check_legacy_environment()
    _config_migration.migrate_legacy_settings()
    stored = _config_settings.load_config()
    return any(os.environ.get(_config_settings.config_env_name(key), "") or key in stored for key in keys)


def resolve_mutable_setting(key: str) -> str:
    """変更可能な設定は環境変数を優先し、無ければ保存値、どちらも無ければ初期値を使う。"""
    if key not in _config_settings.MUTABLE_KEY_DEFAULTS:
        raise KeyError(key)
    _config_migration.check_legacy_environment()
    _config_migration.migrate_legacy_settings()
    env_name = _config_settings.config_env_name(key)
    env_value = os.environ.get(env_name, "")
    value = _config_settings.raw_mutable_setting(key)
    try:
        _config_settings.validate_mutable_setting(key, value)
    except ValueError as error:
        source = f"環境変数{env_name}" if env_value else f"設定キー{key}"
        next_action = (
            f"環境変数{env_name}を受理可能書式の値へ直すか解除して再実行する"
            if env_value
            else f"`atk config set {key} <VALUE>`で受理可能書式の値へ直して再実行する"
        )
        raise _next_action.ActionableError(
            f"{source}の値が不正です（値: {value}）。{error}", next_action=next_action
        ) from error
    return _config_settings.normalize_setting(key, value)


def save_mutable_settings(updates: dict[str, str]) -> bool:
    """変更可能設定の複数キーを検証して保存し、保存内容が変わったかを返す。

    他のキーと他用途の設定は保持する。値が既存の保存値と同じキーだけなら書き込まない。
    統一設定の破損と値の不正は`ValueError`（`ActionableError`を含む）、書込失敗は`OSError`で送出する。
    """
    for key, value in updates.items():
        if key not in _config_settings.MUTABLE_KEY_DEFAULTS:
            raise KeyError(key)
        _config_settings.validate_mutable_setting(key, value)
    updates = {key: _config_settings.normalize_setting(key, value) for key, value in updates.items()}
    config = _config_settings.load_config_strict()
    if all(config.get(key) == value for key, value in updates.items()):
        return False
    _config_settings.save_config({**config, **updates})
    return True


def _resolved_settings(home: pathlib.Path) -> dict[str, str]:
    """XDG関連パスの導出値と変更可能設定をまとめて返す（表示・`get`共通の解決結果）。"""
    _config_migration.check_legacy_environment()
    _config_migration.migrate_legacy_settings()
    _config_migration.migrate_serve_settings()
    # Windowsでappnameがappauthorとしても付与される二重階層を防ぐ。
    settings = {
        "config_dir": str(_config_settings.config_dir()),
        "state_dir": str(_state_paths.state_dir()),
        "data_dir": str(pathlib.Path(platformdirs.user_data_dir("agent-toolkit", appauthor=False))),
        "private_notes": str(_private_notes.default_private_notes(home)),
        **{key: resolve_mutable_setting(key) for key in _config_settings.MUTABLE_KEY_DEFAULTS},
    }
    return settings


_UNKNOWN_CANDIDATE_NEXT_ACTION = "意図した名前か確認し、誤記なら同じキーへ`atk config set <KEY> <VALUE>`で設定し直す"

"""参考一覧外のモデル候補の警告に添える次の操作。"""


def _stage_model_candidate_warnings(key: str, value: str) -> list[str]:
    """参考一覧外のモデル候補を警告の本文へ変換する。接頭辞は`report_warning`が付ける。"""
    warnings: list[str] = []
    if key not in _config_settings.MODEL_DEFAULTS:
        return warnings
    candidates = value.split(",")
    for candidate in candidates:
        engine, model, effort = _parse_stage_model(candidate)
        shown = candidate
        if engine == "opencode":
            continue
        models = ", ".join(sorted(_config_settings.KNOWN_MODELS[engine]))
        if model not in _config_settings.KNOWN_MODELS[engine]:
            warnings.append(f"設定キー`{key}`の候補`{shown}`のモデル名`{model}`は主に使うモデルの一覧（{models}）にありません")
        if effort is not None and effort not in _config_settings.KNOWN_EFFORTS:
            efforts = ", ".join(sorted(_config_settings.KNOWN_EFFORTS))
            warnings.append(f"設定キー`{key}`の候補`{shown}`のeffort`{effort}`は主に使う値の一覧（{efforts}）にありません")
    return warnings


def _cmd_config_show(home: pathlib.Path) -> None:
    """showサブコマンド: 設定値を1キー1行で表示する。"""
    settings = _resolved_settings(home)
    for key, value in settings.items():
        print(f"{key} {value}")


def _report_candidate_warnings(warnings: list[str]) -> None:
    """候補の警告を全件示し、共通の次の操作は1回だけ添える。"""
    if warnings:
        message = "\n".join([warnings[0], *(f"{_outcome.WARNING_PREFIX}{warning}" for warning in warnings[1:])])
        _outcome.report_warning(message, next_action=_UNKNOWN_CANDIDATE_NEXT_ACTION)


def _legacy_key_guidance(keys: list[str]) -> str:
    """旧モデル設定キーと旧接続設定キーを指定した場合に、置き換える新キーを示す文を返す。"""
    guidance = ""
    if "process_loop_repositories" in keys:
        guidance += "。process_loop_repositoriesは廃止した。`atk config repos`でrepositoriesを変更する"
    if any(key in _config_settings.RETIRED_MODEL_KEYS for key in keys):
        guidance += "。旧モデル設定キーは廃止した。対応する工程別の`*_model`キーを指定する"
    for key in keys:
        if key in _config_settings.RETIRED_CONNECTION_KEYS:
            guidance += f"。旧接続設定キー{key}は廃止した。移行先: {', '.join(_config_settings.RETIRED_CONNECTION_KEYS[key])}"
    return guidance


def _cmd_config_get(args: argparse.Namespace, home: pathlib.Path) -> None:
    """getサブコマンド: 1件以上の設定値を指定した順に表示する。未知キーはexit 2。"""
    requested_keys = cast(list[str], args.key)
    settings = _resolved_settings(home)
    unknown_keys = [key for key in requested_keys if key not in settings]
    if unknown_keys:
        _outcome.report_failure(
            f"未知の設定キーを指定した: {', '.join(unknown_keys)}{_legacy_key_guidance(unknown_keys)}",
            next_action=f"利用可能なキーから選び直す: {', '.join(settings)}",
        )
        sys.exit(2)
    for key in requested_keys:
        print(settings[key])


def _cmd_config_set(args: argparse.Namespace) -> None:
    """setサブコマンド: 変更可能設定を更新する。対象外キーはexit 2。

    旧モデルの候補未設定が残る場合も明示値を保存し、未設定の各工程をsetで順に修復できる。
    """
    if args.key not in _config_settings.MUTABLE_KEY_DEFAULTS:
        _outcome.report_failure(
            f"変更できない設定キーを指定した: {args.key}{_legacy_key_guidance([args.key])}",
            next_action=f"変更可能なキーから選び直す: {', '.join(_config_settings.MUTABLE_KEY_DEFAULTS)}",
        )
        sys.exit(2)
    try:
        _config_settings.validate_mutable_setting(args.key, args.value)
    except ValueError as error:
        _outcome.report_failure(
            f"設定値の書式が不正である。{error}",
            next_action=f"受理可能書式の値で`atk config set {args.key} <VALUE>`を再実行する",
        )
        sys.exit(2)
    _report_candidate_warnings(_stage_model_candidate_warnings(args.key, args.value))
    # 旧serve.tomlの値を先に移行し、続く保存で明示値が移行値に上書きされないようにする。
    _config_migration.migrate_serve_settings()
    value = _config_settings.normalize_setting(args.key, args.value)
    config = _config_migration.plan_legacy_migration(_config_settings.load_config_strict(), updates={args.key: value})
    _config_settings.save_config(config)
    _outcome.report_success(f"設定を更新した: {args.key}={value}")
    active_env = (
        _config_settings.config_env_name(args.key) if os.environ.get(_config_settings.config_env_name(args.key), "") else None
    )
    if active_env:
        _outcome.report_warning(
            f"環境変数{active_env}が優先されるため、解除するまで更新値は実効値にならない",
            next_action=f"保存した値を使う場合は環境変数{active_env}を解除する。環境変数の値を使い続ける場合は対応不要",
        )


def _cmd_config_apply_preset(args: argparse.Namespace) -> None:
    """apply-presetサブコマンド: 工程別の6設定を一括保存し、write_modelを保持する。

    `show`の指定時とプリセット名の省略時は、設定ファイルを読み書きせずに全プリセットの値を表示する。
    """
    if args.preset in (None, _config_settings.APPLY_PRESET_SHOW):
        for preset, preset_settings in _config_settings.PRESET_MODELS.items():
            print(f"{preset}:")
            for key, value in preset_settings.items():
                print(f"  {key}: {value}")
        return
    settings = _config_settings.PRESET_MODELS[args.preset]
    config = _config_migration.plan_legacy_migration(_config_settings.load_config(), updates=settings)
    _config_settings.save_config(config)
    _outcome.report_success(f"工程別モデル設定をpreset「{args.preset}」で一括保存した: {len(settings)}件")
    for key, value in settings.items():
        print(f"{key}: {value}")


def _parse_stage_model(value: str) -> tuple[str, str, str | None]:
    """検証済み設定値をengine・model・effort（未指定はNone）へ分解する。"""
    engine, _, rest = value.partition(":")
    if engine == "opencode":
        model, explicit, effort = rest.rpartition("@")
        if explicit:
            return engine, model, effort
        return engine, rest, None
    model, effort_sep, effort = rest.partition("/")
    return engine, model, effort if effort_sep else None


def parse_stage_model_candidates(value: str) -> list[tuple[str, str, str]]:
    """候補列をengine・model・effortの3つ組へ分解し、effort省略時は`medium`を補う。"""
    _config_settings.validate_stage_model_candidates(value)
    return [
        (engine, model, effort or "medium")
        for engine, model, effort in (_parse_stage_model(candidate) for candidate in value.split(","))
    ]


def parse_unresolved_model_candidates(model_type: str) -> list[tuple[str, str, str]]:
    """工程名または直接候補列から候補の3つ組を返す。"""
    if model_type in _config_settings.MODEL_TYPES:
        return parse_stage_model_candidates(resolve_mutable_setting(f"{model_type}_model"))
    try:
        return parse_stage_model_candidates(model_type)
    except ValueError as error:
        available = list(_config_settings.MODEL_TYPES)
        raise ValueError(
            f"unknown model_type: {model_type} "
            f"(available: {', '.join(available)}; or pass candidates like codex:gpt-6-sol/medium)"
        ) from error


def resolve_catalog(
    candidates: list[tuple[str, str, str]], *, catalog: list[dict[str, Any]] | None = None
) -> list[tuple[str, str, str]]:
    """候補中のCodex系列名を実行時の完全IDへ解決する。"""
    if codex_models.needs_catalog(candidates):
        active_catalog = catalog if catalog is not None else codex_models.list_models()
        return codex_models.resolve_candidates(candidates, active_catalog)
    return candidates


def resolve_model_candidates(model_type: str, *, catalog: list[dict[str, Any]] | None = None) -> list[tuple[str, str, str]]:
    """Codex系列名を実行時の完全IDへ解決して工程別の候補を返す。"""
    return resolve_catalog(parse_unresolved_model_candidates(model_type), catalog=catalog)


def build_parser(config: argparse.ArgumentParser) -> None:
    """`config`サブパーサ配下にshow/get/set/apply-presetを登録する。"""
    sub = _atk_help.add_subcommands(config, dest="config_subcommand", required=False)
    _atk_help.add_command(sub, "show", **_atk_help.HELP["atk config show"])
    _atk_help.add_command(sub, "list", **_atk_help.HELP["atk config list"])
    for name in ("add", "remove"):
        array = _atk_help.add_command(sub, name, **_atk_help.HELP[f"atk config {name}"])
        array.add_argument("key", metavar="KEY", help="JSON配列型の設定キー。")
        array.add_argument("value", metavar="VALUE", help="追加・削除する要素1件。")
    config_repositories_cli.build_parser(sub)
    get = _atk_help.add_command(sub, "get", **_atk_help.HELP["atk config get"])
    get.add_argument("key", metavar="KEY", nargs="+", help="取得する1件以上のキー（config showの出力キーと同一）。")
    set_ = _atk_help.add_command(sub, "set", **_atk_help.HELP["atk config set"])
    set_.add_argument("key", metavar="KEY", help=f"変更可能なキー: {', '.join(_config_settings.MUTABLE_KEY_DEFAULTS)}")
    set_.add_argument("value", metavar="VALUE", help="設定する値。モデルの複数候補はASCIIカンマ区切りで指定する。")
    apply_preset = _atk_help.add_command(sub, "apply-preset", **_atk_help.HELP["atk config apply-preset"])
    apply_preset.add_argument(
        "preset",
        nargs="?",
        choices=(_config_settings.APPLY_PRESET_SHOW, *_config_settings.PRESET_MODELS),
        help=f"適用するプリセット名。`{_config_settings.APPLY_PRESET_SHOW}`または省略で全プリセットの値を保存せずに表示する。",
    )


def dispatch(args: argparse.Namespace, home: pathlib.Path) -> int:
    """`config`サブコマンドを実行し、終了コードを返す（サブコマンド省略時は`show`扱い）。"""
    sub = getattr(args, "config_subcommand", None) or "show"
    try:
        if sub in {"show", "list"}:
            _cmd_config_show(home)
        elif sub == "get":
            _cmd_config_get(args, home)
        elif sub == "apply-preset":
            _cmd_config_apply_preset(args)
        elif sub in {"add", "remove"}:
            _cmd_config_array(args, remove=sub == "remove")
        elif sub == "repos":
            config_repositories_cli.dispatch(args)
        else:
            _cmd_config_set(args)
    except ValueError as error:
        next_action = (
            error.next_action
            if isinstance(error, _next_action.ActionableError)
            else "`atk config show`で現在値を確認し、`atk config set <KEY> <VALUE>`で不正な値を直して再実行する"
        )
        _outcome.report_failure(str(error), next_action=next_action)
        return 2
    return 0


def _cmd_config_array(args: argparse.Namespace, *, remove: bool) -> None:
    """配列要素を1件更新し、重複追加と不在削除を区別する。"""
    if args.key not in _config_settings.ARRAY_KEYS:
        raise ValueError(f"{args.key}は配列型の設定ではない。単一値の設定には`atk config set`を使う")
    _config_migration.migrate_legacy_settings()
    stored = _config_settings.load_config_strict()
    value = stored.get(args.key, _config_settings.mutable_setting_default(args.key))
    if not isinstance(value, str):
        raise ValueError(f"{args.key}の保存値はJSON文字列配列を表す文字列で指定する")
    items = list(_config_settings.parse_remote_hosts(value))
    item = args.value
    _config_settings.validate_mutable_setting(args.key, json.dumps([item]))
    if remove:
        if item not in items:
            raise ValueError(f"要素が存在しない: {item}。現在の一覧: {json.dumps(items, ensure_ascii=False)}")
        items = [current for current in items if current != item]
    elif item in items:
        _outcome.report_success(f"既に登録されているため変更しない: {item}")
        return
    else:
        items.append(item)
    _cmd_config_set(argparse.Namespace(key=args.key, value=json.dumps(items, ensure_ascii=False)))
