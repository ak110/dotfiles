"""`atk config`サブコマンド。

XDG関連パス（設定・状態・データ各ディレクトリ、private-notesの解決結果）の確認と、
工程別モデル設定の確認・変更を提供する。
"""

import argparse
import json
import os
import pathlib
import re
import sys
from typing import Any, cast

import platformdirs

from agent_toolkit._atk import help_text as _atk_help
from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._common import codex_models
from agent_toolkit._common import next_action as _next_action
from agent_toolkit._common import private_notes as _private_notes
from agent_toolkit._common import state_paths as _state_paths
from agent_toolkit._common.atomic_file import atomic_write

_CONFIG_FILENAME = "config.json"

_MODEL_SETTING_CATEGORIES = {
    "high_tier_model": "上位",
    "medium_tier_model": "中位",
    "low_tier_model": "下位",
    "orchestrate_model": "上位",
}
# 用途区分はcodexとclaudeの候補を1組で持ち、各engineの選定値とプリセットの順序を分けて管理する。
# 各区分の段位はskills/delegation/references/runtime-routing.md「代替時の組合せの目安」に従う。
_CATEGORY_ENGINE_MODELS = {
    "上位": {
        "codex": "codex:sol/medium",
        "claude": "claude:opus[1m]/medium",
    },
    "中位": {
        "codex": "codex:terra/medium",
        "claude": "claude:sonnet[1m]/medium",
    },
    "下位": {
        "codex": "codex:luna/medium",
        "claude": "claude:haiku/medium",
    },
}
_PRESET_ENGINE_ORDERS = {
    "codex-balanced": ("codex", frozenset({"orchestrate_model", "medium_tier_model"})),
    "codex-primary": ("codex", frozenset()),
    "claude-balanced": ("claude", frozenset({"medium_tier_model", "low_tier_model"})),
    "claude-primary": ("claude", frozenset()),
}

# apply-presetの位置引数でプリセット名の代わりに受理し、全プリセットの値を表示する値。
_APPLY_PRESET_SHOW = "show"


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


_MUTABLE_KEY_DEFAULTS = {
    **_preset_settings("codex-balanced"),
    "write_model": "agy:gemini-3.8-flash/medium,claude:claude-opus-5-5/medium",
    "codex_fast_mode": "false",
    "codex_model_providers": "",
}
_STAGE_MODEL_PATTERN = re.compile(r"^(?:claude|codex|agy):[^/,\s]+(?:/[^/,\s]+)?$")
_CONFIG_ENV_PREFIX = "AGENT_TOOLKIT_CONFIG_"
# 主に使うモデル名・effortの参考一覧。受理可否の判定には使わず、一覧外は警告のみで受理する。
_KNOWN_MODELS = {
    "claude": frozenset({"haiku", "sonnet", "opus", "fable", "sonnet[1m]", "opus[1m]", "claude-opus-5-5"}),
    "codex": frozenset(
        {"gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-6-astra", "gpt-6-sol", "gpt-6-luna", *codex_models.FAMILIES}
    ),
    # Antigravity CLIの`--model`は、`agy models`が返す推論の深さ込みの完全スラッグと、
    # 深さを除いたベース名の双方を受理する。本ツールは深さを`--effort`で別に渡すためベース名を置く。
    # 実物を確認した日付と再検証手段は`docs/development/audit-records.md`の
    # 「agent-toolkit/agent_toolkit/_atk/config.py：Antigravity CLIのモデル指定：2026年9月18日」が持つ。
    # 日本語文書の推敲へ用途を限定するため、一覧はこの用途で使う1件だけとする。
    "agy": frozenset({"gemini-3.8-flash"}),
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
    atomic_write(path, json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _config_env_name(key: str) -> str:
    """変更可能設定キーに対応する環境変数名を返す。"""
    return f"{_CONFIG_ENV_PREFIX}{key.upper()}"


def _validate_stage_model_candidates(value: str) -> None:
    """候補列の各候補を検証し、書式不正なら`ValueError`を送出する。"""
    candidates = value.split(",")
    if not candidates or any(_STAGE_MODEL_PATTERN.fullmatch(candidate) is None for candidate in candidates):
        raise ValueError("受理可能書式: <claude|codex|agy>:<model>[/<effort>]（複数候補はASCIIカンマ区切り）")


def _validate_mutable_setting(key: str, value: str) -> None:
    """速度、接続先列、モデル候補をそれぞれの受理形式で確かめる。"""
    if key == "codex_fast_mode":
        if value not in {"true", "false"}:
            raise ValueError("受理可能値: true, false")
    elif key == "codex_model_providers":
        parse_codex_provider_candidates(value)
    else:
        _validate_stage_model_candidates(value)


def parse_codex_provider_candidates(value: str) -> tuple[str, ...]:
    """接続先IDを空白除去・順序保持で読み、同じIDは一度だけ返す。"""
    if not value.strip():
        return ()
    providers = tuple(part.strip() for part in value.split(","))
    if any(not provider or any(char.isspace() for char in provider) for provider in providers):
        raise ValueError("受理可能書式: 空文字列、またはprovider IDのASCIIカンマ区切り（空要素は不可）")
    return tuple(dict.fromkeys(providers))


_LEGACY_CODEX_PROVIDER_KEY = "codex_fallback_model_providers"


def legacy_codex_provider_setting() -> tuple[tuple[str, ...], bool] | None:
    """新設定がない場合だけ旧列と、保存値からの移行かを返す。"""
    stored = _load_config()
    if os.environ.get(_config_env_name("codex_model_providers")) or "codex_model_providers" in stored:
        return None
    legacy_env = os.environ.get(_config_env_name(_LEGACY_CODEX_PROVIDER_KEY))
    if legacy_env:
        return parse_codex_provider_candidates(legacy_env), False
    if _LEGACY_CODEX_PROVIDER_KEY in stored:
        return parse_codex_provider_candidates(stored[_LEGACY_CODEX_PROVIDER_KEY]), True
    return None


def migrate_codex_provider_setting(primary: str) -> tuple[str, ...]:
    """実効主接続先が得られた後に旧列を移行し、他の設定を保持する。"""
    legacy = legacy_codex_provider_setting()
    if legacy is None:
        return parse_codex_provider_candidates(resolve_mutable_setting("codex_model_providers"))
    candidates, persisted = legacy
    order = tuple(dict.fromkeys((primary, *candidates))) if candidates else ()
    if persisted:
        stored = _load_config()
        # 照会中に新しい値が保存された場合は、旧値の移行で上書きしない。
        if "codex_model_providers" in stored:
            return parse_codex_provider_candidates(stored["codex_model_providers"])
        stored["codex_model_providers"] = ",".join(order)
        stored.pop(_LEGACY_CODEX_PROVIDER_KEY, None)
        _save_config(stored)
    return order


def _report_legacy_codex_provider_setting() -> None:
    """未移行の保存値を、推測した先頭を表示せず案内する。"""
    if legacy_codex_provider_setting() is not None:
        _outcome.report_warning(
            "旧Codex接続先設定が残っています。新規Codex起動時に実効主接続先を補って移行します",
            next_action=(
                "agents_serverで新規Codex sessionを起動するか、"
                "`atk config set codex_model_providers <VALUE>`で優先順を指定する。"
                "旧環境変数はAGENT_TOOLKIT_CONFIG_CODEX_MODEL_PROVIDERSへ新形式の値で置き換える"
            ),
        )


def _reject_legacy_codex_provider_key(keys: list[str]) -> None:
    """旧キーを新規の公開設定として受理せず、置換先を示す。"""
    if _LEGACY_CODEX_PROVIDER_KEY in keys:
        _outcome.report_failure(
            "設定キーcodex_fallback_model_providersはcodex_model_providersへ置き換わりました",
            next_action=(
                "`atk config set codex_model_providers 'openai,custom'`のように主接続先を先頭に指定する。"
                "未移行の保存値は新規Codex起動時に自動移行する"
            ),
        )
        sys.exit(2)


def mutable_setting_default(key: str) -> str:
    """変更可能な設定の初期値を返す。未知のキーは`KeyError`を送出する。"""
    return _MUTABLE_KEY_DEFAULTS[key]


def raw_mutable_setting(key: str) -> str:
    """変更可能な設定の検証前の値を、環境変数、保存値、初期値の順に解決して返す。未知のキーは`KeyError`を送出する。"""
    default = mutable_setting_default(key)
    return os.environ.get(_config_env_name(key), "") or _load_config().get(key, default)


def resolve_mutable_setting(key: str) -> str:
    """変更可能な設定は環境変数を優先し、無ければ保存値、どちらも無ければ初期値を使う。"""
    if key not in _MUTABLE_KEY_DEFAULTS:
        raise KeyError(key)
    env_name = _config_env_name(key)
    env_value = os.environ.get(env_name, "")
    value = raw_mutable_setting(key)
    try:
        _validate_mutable_setting(key, value)
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
    return ",".join(parse_codex_provider_candidates(value)) if key == "codex_model_providers" else value


def _resolved_settings(home: pathlib.Path) -> dict[str, str]:
    """XDG関連パスの導出値と変更可能設定をまとめて返す（表示・`get`共通の解決結果）。"""
    # Windowsでappnameがappauthorとしても付与される二重階層を防ぐ。
    return {
        "config_dir": str(_config_dir()),
        "state_dir": str(_state_paths.state_dir()),
        "data_dir": str(pathlib.Path(platformdirs.user_data_dir("agent-toolkit", appauthor=False))),
        "private_notes": str(_private_notes.default_private_notes(home)),
        **{key: resolve_mutable_setting(key) for key in _MUTABLE_KEY_DEFAULTS},
    }


_UNKNOWN_CANDIDATE_NEXT_ACTION = "利用可否は実行時に各engineが判定します。対応不要（処理は継続した）"
"""参考一覧外の工程別モデル候補の警告に添える次の操作。"""


def _stage_model_candidate_warnings(key: str, value: str) -> list[str]:
    """参考一覧外の工程別モデル候補を警告の本文へ変換する。接頭辞は`report_warning`が付ける。"""
    warnings: list[str] = []
    if key in {"codex_fast_mode", "codex_model_providers"}:
        return warnings
    for candidate in value.split(","):
        engine, model, effort = _parse_stage_model(candidate)
        models = ", ".join(sorted(_KNOWN_MODELS[engine]))
        if model not in _KNOWN_MODELS[engine]:
            warnings.append(
                f"設定キー`{key}`の候補`{candidate}`のモデル名`{model}`は主に使うモデルの一覧（{models}）にありません"
            )
        if effort is not None and effort not in _KNOWN_EFFORTS:
            efforts = ", ".join(sorted(_KNOWN_EFFORTS))
            warnings.append(f"設定キー`{key}`の候補`{candidate}`のeffort`{effort}`は主に使う値の一覧（{efforts}）にありません")
    return warnings


def _cmd_config_show(home: pathlib.Path) -> None:
    """showサブコマンド: 設定値を1キー1行で表示する。"""
    _report_legacy_codex_provider_setting()
    settings = _resolved_settings(home)
    warnings: list[str] = []
    for key, value in settings.items():
        print(f"{key}: {value}")
        if key in _MUTABLE_KEY_DEFAULTS:
            warnings.extend(_stage_model_candidate_warnings(key, value))
    _report_candidate_warnings(warnings)


def _report_candidate_warnings(warnings: list[str]) -> None:
    """候補の警告を全件示し、共通の次の操作は1回だけ添える。"""
    if warnings:
        message = "\n".join([warnings[0], *(f"{_outcome.WARNING_PREFIX}{warning}" for warning in warnings[1:])])
        _outcome.report_warning(message, next_action=_UNKNOWN_CANDIDATE_NEXT_ACTION)


def _cmd_config_get(args: argparse.Namespace, home: pathlib.Path) -> None:
    """getサブコマンド: 1件以上の設定値を表示する。未知キーはexit 2。"""
    requested_keys = cast(list[str], args.key)
    _reject_legacy_codex_provider_key(requested_keys)
    _report_legacy_codex_provider_setting()
    settings = _resolved_settings(home)
    unknown_keys = [key for key in requested_keys if key not in settings]
    if unknown_keys:
        _outcome.report_failure(
            f"未知の設定キーを指定した: {', '.join(unknown_keys)}",
            next_action=f"利用可能なキーから選び直す: {', '.join(sorted(settings))}",
        )
        sys.exit(2)
    for key in requested_keys:
        print(settings[key])


def _cmd_config_set(args: argparse.Namespace) -> None:
    """setサブコマンド: 変更可能設定を更新する。対象外キーはexit 2。"""
    _reject_legacy_codex_provider_key([args.key])
    if args.key not in _MUTABLE_KEY_DEFAULTS:
        _outcome.report_failure(
            f"変更できない設定キーを指定した: {args.key}",
            next_action=f"変更可能なキーから選び直す: {', '.join(sorted(_MUTABLE_KEY_DEFAULTS))}",
        )
        sys.exit(2)
    try:
        _validate_mutable_setting(args.key, args.value)
    except ValueError as error:
        _outcome.report_failure(
            f"設定値の書式が不正である。{error}",
            next_action=f"受理可能書式の値で`atk config set {args.key} <VALUE>`を再実行する",
        )
        sys.exit(2)
    _report_candidate_warnings(_stage_model_candidate_warnings(args.key, args.value))
    config = _load_config()
    value = ",".join(parse_codex_provider_candidates(args.value)) if args.key == "codex_model_providers" else args.value
    config[args.key] = value
    if args.key == "codex_model_providers":
        config.pop(_LEGACY_CODEX_PROVIDER_KEY, None)
    _save_config(config)
    _outcome.report_success(f"設定を更新した: {args.key}={value}")
    env_name = _config_env_name(args.key)
    if os.environ.get(env_name, ""):
        _outcome.report_warning(
            f"環境変数{env_name}が優先されるため、解除するまで更新値は実効値にならない",
            next_action=f"保存した値を使う場合は環境変数{env_name}を解除する。環境変数の値を使い続ける場合は対応不要",
        )


def _cmd_config_apply_preset(args: argparse.Namespace) -> None:
    """apply-presetサブコマンド: 現行の工程別モデル設定を一括保存する。

    `show`の指定時とプリセット名の省略時は、設定ファイルを読み書きせずに全プリセットの値を表示する。
    """
    if args.preset in (None, _APPLY_PRESET_SHOW):
        for preset in _PRESET_ENGINE_ORDERS:
            print(f"{preset}:")
            for key, value in _preset_settings(preset).items():
                print(f"  {key}: {value}")
        return
    settings = _preset_settings(args.preset)
    config = _load_config()
    config.update(settings)
    _save_config(config)
    _outcome.report_success(f"工程別モデル設定をpreset「{args.preset}」で一括保存した: {len(settings)}件")
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


def parse_unresolved_model_candidates(model_type: str) -> list[tuple[str, str, str]]:
    """model_typeに対応する保存値を候補の3つ組として返す。

    設定値と同じ書式の候補列を受け取った場合は設定を読まず、その候補列をそのまま分解して返す。
    """
    key = f"{model_type}_model"
    if key not in _MUTABLE_KEY_DEFAULTS:
        try:
            return parse_stage_model_candidates(model_type)
        except ValueError as error:
            available = sorted(item.removesuffix("_model") for item in _MUTABLE_KEY_DEFAULTS if item.endswith("_model"))
            raise ValueError(
                f"unknown model_type: {model_type} "
                f"(available: {', '.join(available)}; or pass candidates like codex:gpt-6-sol/medium)"
            ) from error
    return parse_stage_model_candidates(resolve_mutable_setting(key))


def resolve_model_candidates(model_type: str, *, catalog: list[dict[str, Any]] | None = None) -> list[tuple[str, str, str]]:
    """Codex系列名を実行時の完全IDへ解決して工程別候補を返す。"""
    candidates = parse_unresolved_model_candidates(model_type)
    if codex_models.needs_catalog(candidates):
        active_catalog = catalog if catalog is not None else codex_models.list_models()
        return codex_models.resolve_candidates(candidates, active_catalog)
    return candidates


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
    apply_preset.add_argument(
        "preset",
        nargs="?",
        choices=(_APPLY_PRESET_SHOW, *_PRESET_ENGINE_ORDERS),
        help=f"適用するプリセット名。`{_APPLY_PRESET_SHOW}`または省略で全プリセットの値を保存せずに表示する。",
    )


def dispatch(args: argparse.Namespace, home: pathlib.Path) -> int:
    """`config`サブコマンドを実行し、終了コードを返す（サブコマンド省略時は`show`扱い）。"""
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
        next_action = (
            error.next_action
            if isinstance(error, _next_action.ActionableError)
            else "`atk config show`で現在値を確認し、`atk config set <KEY> <VALUE>`で不正な値を直して再実行する"
        )
        _outcome.report_failure(str(error), next_action=next_action)
        return 2
    return 0
