"""`atk config`サブコマンド。

XDG関連パス（設定・状態・データ各ディレクトリ、private-notesの解決結果）の確認と、
モデル設定（engine別・段位別のモデルと用途別のengine順）の確認・変更を提供する。
"""

import argparse
import dataclasses
import json
import os
import pathlib
import re
import sys
from typing import Any, cast

# ヘルプや他の処理で使わない依存は、使用時まで読み込まない。
lazy import platformdirs

from agent_toolkit._atk import help_text as _atk_help
from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._common import codex_models
from agent_toolkit._common import next_action as _next_action
from agent_toolkit._common import private_notes as _private_notes
from agent_toolkit._common import state_paths as _state_paths
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._git import command as _git_command
from agent_toolkit._git import remote as _git_remote

_CONFIG_FILENAME = "config.json"

ENGINES = ("claude", "codex")
"""段位モデルと用途別engine順が扱うengine。"""

TIERS = ("high", "medium", "low")
"""段位。"""

USAGES = ("base", "review", "chores", "orchestrate")
"""用途。各用途はengine順を1つ持ち、そのengine順に段位のモデルを並べて候補列とする。"""

TIER_MODEL_TYPES = {"high_tier": "high", "medium_tier": "medium", "low_tier": "low"}
"""`model_type`の段位名と段位の対応。"""

_TIER_DEFAULT_USAGES = {"high_tier": "base", "medium_tier": "review", "low_tier": "chores"}
"""呼び出し元が用途を指定しない段位名の用途（delegateの対応）。"""

_TIER_MODEL_DEFAULTS = {
    "claude_high_tier_model": "opus[1m]/medium",
    "claude_medium_tier_model": "sonnet[1m]/medium",
    "claude_low_tier_model": "haiku/medium",
    "codex_high_tier_model": "sol/medium",
    "codex_medium_tier_model": "terra/medium",
    "codex_low_tier_model": "luna/medium",
}

# プリセットは用途別engine順だけを持ち、段位モデルとwrite_modelを書き換えない。
_PRESET_ENGINE_ORDERS = {
    "codex-balanced": {
        "base_engine": "codex,claude",
        "review_engine": "claude,codex",
        "chores_engine": "codex,claude",
        "orchestrate_engine": "claude,codex",
    },
    "codex-near-primary": {
        "base_engine": "codex,claude",
        "review_engine": "codex,claude",
        "chores_engine": "codex,claude",
        "orchestrate_engine": "claude,codex",
    },
    "codex-primary": {
        "base_engine": "codex,claude",
        "review_engine": "codex,claude",
        "chores_engine": "codex,claude",
        "orchestrate_engine": "codex,claude",
    },
    "claude-balanced": {
        "base_engine": "claude,codex",
        "review_engine": "codex,claude",
        "chores_engine": "codex,claude",
        "orchestrate_engine": "claude,codex",
    },
    "claude-primary": {
        "base_engine": "claude,codex",
        "review_engine": "claude,codex",
        "chores_engine": "claude,codex",
        "orchestrate_engine": "claude,codex",
    },
}

# apply-presetの位置引数でプリセット名の代わりに受理し、全プリセットの値を表示する値。
_APPLY_PRESET_SHOW = "show"


# 挿入順は`atk config show`の表示順とキー案内の順を兼ねる。
_MUTABLE_KEY_DEFAULTS = {
    **_TIER_MODEL_DEFAULTS,
    **_PRESET_ENGINE_ORDERS["codex-balanced"],
    "write_model": "agy:gemini-3.8-flash/medium,claude:claude-opus-5-5/medium",
    "codex_fast_mode": "false",
    "codex_model_providers": "",
    "process_loop_repositories": "[]",
}

# 旧キーは段位とengine順を1つの候補列で持っていた。保存値は移行先へ一括で移し、新しいget・setの対象から除く。
_LEGACY_MODEL_KEYS = {
    "high_tier_model": ("high", "base_engine"),
    "medium_tier_model": ("medium", "review_engine"),
    "low_tier_model": ("low", "chores_engine"),
    "orchestrate_model": ("high", "orchestrate_engine"),
}

_STAGE_MODEL_PATTERN = re.compile(r"^(?:claude|codex|agy):[^/,\s]+(?:/[^/,\s]+)?$")
_TIER_MODEL_PATTERN = re.compile(r"^[^/,\s]+(?:/[^/,\s]+)?$")
_ENGINE_PREFIX_PATTERN = re.compile(r"^(?:claude|codex|agy):")
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


def process_loop_configuration_path() -> pathlib.Path:
    """常駐プロセスが登録先の変更を監視する設定ファイルのパスを返す。"""
    return _config_file_path()


def _load_config() -> dict[str, str]:
    """永続化済みの変更可能設定を読み込む。ファイル不在・破損時は空辞書を返す。"""
    path = _config_file_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError, OSError:
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


def config_environment_variable_names() -> tuple[str, ...]:
    """現行設定と、置換案内の対象である旧設定の環境変数名を返す。"""
    return tuple(_config_env_name(key) for key in (*_MUTABLE_KEY_DEFAULTS, *_LEGACY_MODEL_KEYS))


def tier_model_key(engine: str, tier: str) -> str:
    """engineと段位に対応するモデル設定キーを返す。"""
    return f"{engine}_{tier}_tier_model"


def _validate_stage_model_candidates(value: str) -> None:
    """候補列の各候補を検証し、書式不正なら`ValueError`を送出する。"""
    candidates = value.split(",")
    if not candidates or any(_STAGE_MODEL_PATTERN.fullmatch(candidate) is None for candidate in candidates):
        raise ValueError("受理可能書式: <claude|codex|agy>:<model>[/<effort>]（複数候補はASCIIカンマ区切り）")


def _validate_tier_model(value: str) -> None:
    """engine接頭辞を持たない単一のモデル値を検証し、書式不正なら`ValueError`を送出する。"""
    if _TIER_MODEL_PATTERN.fullmatch(value) is None or _ENGINE_PREFIX_PATTERN.match(value):
        raise ValueError("受理可能書式: <model>[/<effort>]（engine接頭辞を付けない単一のモデル）")


def parse_engine_order(value: str) -> tuple[str, ...]:
    """用途別engine順を検証して分解し、書式不正なら`ValueError`を送出する。"""
    engines = tuple(value.split(","))
    if any(engine not in ENGINES for engine in engines) or len(set(engines)) != len(engines):
        raise ValueError("受理可能書式: codexとclaudeの一方または両方を重複なしで並べたASCIIカンマ区切り")
    return engines


def _validate_mutable_setting(key: str, value: str) -> None:
    """速度、接続先列、段位モデル、engine順、モデル候補をそれぞれの受理形式で確かめる。"""
    if key == "process_loop_repositories":
        parse_process_loop_repositories(value)
    elif key == "codex_fast_mode":
        if value not in {"true", "false"}:
            raise ValueError("受理可能値: true, false")
    elif key == "codex_model_providers":
        parse_codex_provider_candidates(value)
    elif key in _TIER_MODEL_DEFAULTS:
        _validate_tier_model(value)
    elif key.endswith("_engine"):
        parse_engine_order(value)
    else:
        _validate_stage_model_candidates(value)


@dataclasses.dataclass(frozen=True)
class ConfiguredRepository:
    """同じoriginの登録先から登録順で最初の1件を残した対象と、その作業ツリー・Git共通ディレクトリ。"""

    repo_id: str
    worktree: pathlib.Path
    common_dir: pathlib.Path


def parse_process_loop_repositories(value: str) -> tuple[str, ...]:
    """登録先をJSON文字列配列として解釈する。空配列は待機を表す。"""
    try:
        paths = json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError("process_loop_repositoriesはローカルworktreeのJSON文字列配列で指定する") from error
    if not isinstance(paths, list) or any(not isinstance(path, str) or not path.strip() for path in paths):
        raise ValueError("process_loop_repositoriesは空でないパスのJSON文字列配列で指定する（空配列も可）")
    return tuple(paths)


def resolve_configured_repositories() -> tuple[ConfiguredRepository, ...]:
    """登録先を厳密に読み、全件のGit作業ツリーとoriginを確かめ、同じoriginは登録順で最初の1件だけを返す。"""
    key = "process_loop_repositories"
    env_value = os.environ.get(_config_env_name(key))
    try:
        if env_value is not None:
            value = env_value
        else:
            path = _config_file_path()
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                data = {}
            if not isinstance(data, dict):
                raise ValueError(f"設定JSONがobjectではない: {path}")
            value = data.get(key, "[]")
            if not isinstance(value, str):
                raise ValueError(f"設定キー{key}が文字列ではない: {path}")
        repositories: dict[str, ConfiguredRepository] = {}
        for raw_path in parse_process_loop_repositories(value):
            candidate = pathlib.Path(raw_path).expanduser()
            if not candidate.is_absolute():
                raise ValueError(f"登録先は絶対パスで指定する: {raw_path}")
            result = _git_command.run(
                ["-C", str(candidate), "rev-parse", "--show-toplevel", "--path-format=absolute", "--git-common-dir"],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                raise ValueError(f"登録先のGit作業ツリーを解決できない: {candidate}: {result.stderr.strip()}")
            lines = result.stdout.splitlines()
            if len(lines) != 2:
                raise ValueError(f"登録先のGit実体が不正: {candidate}")
            worktree, common_dir = (pathlib.Path(line).resolve() for line in lines)
            origin = _git_remote.origin_url(worktree)
            if origin is None:
                raise ValueError(f"登録先のoriginを取得できない: {worktree}")
            repo_id = _git_remote.normalize_remote_url(origin)
            repositories.setdefault(repo_id, ConfiguredRepository(repo_id, worktree, common_dir))
        return tuple(repositories.values())
    except (OSError, UnicodeError, ValueError) as error:
        raise _next_action.ActionableError(
            str(error),
            next_action=f"`atk config set {key} <JSON配列>`で登録先を直して再実行する",
        ) from error


def parse_codex_provider_candidates(value: str) -> tuple[str, ...]:
    """接続先IDを空白除去・順序保持で読み、同じIDは一度だけ返す。"""
    if not value.strip():
        return ()
    providers = tuple(part.strip() for part in value.split(","))
    if any(not provider or any(char.isspace() for char in provider) for provider in providers):
        raise ValueError("受理可能書式: 空文字列、またはprovider IDのASCIIカンマ区切り（空要素は不可）")
    return tuple(dict.fromkeys(providers))


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


def _legacy_targets(legacy_key: str) -> list[str]:
    """旧キーの移行先となる新キーを、engine順の設定、段位モデルの順で返す。"""
    tier, engine_key = _LEGACY_MODEL_KEYS[legacy_key]
    return [engine_key, *(tier_model_key(engine, tier) for engine in ENGINES)]


def _plan_legacy_migration(config: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """保存済みの旧キーから新キーへの保存値と、新構成で表現できない競合の説明を返す。

    新キーが既に保存されている場合はその値を優先し、旧キーの対応する値を移さない。
    engine順で表せない旧候補（agy、同じengineの重複、書式不正）は用途のengine設定が保存済みなら解消済みとし、
    同じengine・段位へ異なるモデルが対応する場合はその段位モデル設定が保存済みなら解消済みとする。
    """
    updates: dict[str, str] = {}
    conflicts: list[str] = []
    slot_values: dict[str, dict[str, list[str]]] = {}
    for legacy_key, (tier, engine_key) in _LEGACY_MODEL_KEYS.items():
        value = config.get(legacy_key)
        if value is None:
            continue
        try:
            candidates = parse_stage_model_candidates(value)
        except ValueError:
            if engine_key not in config:
                conflicts.append(f"{legacy_key}の値`{value}`は旧書式として読めません（修正する新キー: {engine_key}）")
            continue
        engines = [engine for engine, _model, _effort in candidates]
        unrepresentable = [
            f"{engine}:{model}/{effort}"
            for engine, model, effort in candidates
            if engine not in ENGINES or engines.count(engine) > 1
        ]
        if unrepresentable and engine_key not in config:
            conflicts.append(
                f"{legacy_key}の候補{', '.join(f'`{item}`' for item in unrepresentable)}はengine順で表せません"
                f"（修正する新キー: {engine_key}）"
            )
        elif engine_key not in config:
            updates[engine_key] = ",".join(engines)
        for engine, model, effort in candidates:
            if engine in ENGINES:
                slot_values.setdefault(tier_model_key(engine, tier), {}).setdefault(f"{model}/{effort}", []).append(legacy_key)
    for model_key, values in slot_values.items():
        if model_key in config:
            continue
        if len(values) > 1:
            listed = "、".join(f"{', '.join(keys)}の`{model}`" for model, keys in values.items())
            conflicts.append(f"{model_key}へ異なる旧モデルが対応します: {listed}（修正する新キー: {model_key}）")
            continue
        updates[model_key] = next(iter(values))
    return updates, conflicts


def migrate_legacy_settings() -> None:
    """保存済みの旧モデル設定を新キーへ一括移行し、同じ保存で旧キーを除く。

    環境変数の値は移行しない。新構成で表現できない旧値があれば何も保存せず、
    競合した旧キー・候補と修正する新キーを示す`ActionableError`を送出する。
    """
    config = _load_config()
    if not any(key in config for key in _LEGACY_MODEL_KEYS):
        return
    updates, conflicts = _plan_legacy_migration(config)
    if conflicts:
        raise _next_action.ActionableError(
            "旧モデル設定を新しい設定へ移行できません（旧保存値は変更していません）: " + "; ".join(conflicts),
            next_action=(
                "示された新キーを`atk config set <KEY> <VALUE>`で保存してから再実行する"
                "（保存した新キーの値が旧保存値より優先され、残りの旧保存値は自動で移行される）"
            ),
        )
    migrated = {key: value for key, value in config.items() if key not in _LEGACY_MODEL_KEYS}
    migrated.update(updates)
    _save_config(migrated)


def _check_legacy_environment() -> None:
    """旧キーの環境変数が空でなければ、新キーへの置き換えを示す`ActionableError`を送出する。"""
    found = [key for key in _LEGACY_MODEL_KEYS if os.environ.get(_config_env_name(key), "")]
    if found:
        replacements = "; ".join(
            f"{_config_env_name(key)} → {', '.join(_config_env_name(target) for target in _legacy_targets(key))}"
            for key in found
        )
        raise _next_action.ActionableError(
            f"廃止した旧モデル設定の環境変数が設定されています: {', '.join(_config_env_name(key) for key in found)}",
            next_action=f"旧環境変数を解除し、必要なら新しい環境変数へ置き換えて再実行する（{replacements}）",
        )


def _resolved_settings(home: pathlib.Path) -> dict[str, str]:
    """XDG関連パスの導出値と変更可能設定をまとめて返す（表示・`get`共通の解決結果）。"""
    migrate_legacy_settings()
    # Windowsでappnameがappauthorとしても付与される二重階層を防ぐ。
    return {
        "config_dir": str(_config_dir()),
        "state_dir": str(_state_paths.state_dir()),
        "data_dir": str(pathlib.Path(platformdirs.user_data_dir("agent-toolkit", appauthor=False))),
        "private_notes": str(_private_notes.default_private_notes(home)),
        **{key: resolve_mutable_setting(key) for key in _MUTABLE_KEY_DEFAULTS},
    }


_UNKNOWN_CANDIDATE_NEXT_ACTION = "利用可否は実行時に各engineが判定します。対応不要（処理は継続した）"
"""参考一覧外のモデル候補の警告に添える次の操作。"""


def _stage_model_candidate_warnings(key: str, value: str) -> list[str]:
    """参考一覧外のモデル候補を警告の本文へ変換する。接頭辞は`report_warning`が付ける。"""
    warnings: list[str] = []
    if key in _TIER_MODEL_DEFAULTS:
        candidates = [f"{key.partition('_')[0]}:{value}"]
    elif key == "write_model":
        candidates = value.split(",")
    else:
        return warnings
    for candidate in candidates:
        engine, model, effort = _parse_stage_model(candidate)
        shown = value if key in _TIER_MODEL_DEFAULTS else candidate
        models = ", ".join(sorted(_KNOWN_MODELS[engine]))
        if model not in _KNOWN_MODELS[engine]:
            warnings.append(f"設定キー`{key}`の候補`{shown}`のモデル名`{model}`は主に使うモデルの一覧（{models}）にありません")
        if effort is not None and effort not in _KNOWN_EFFORTS:
            efforts = ", ".join(sorted(_KNOWN_EFFORTS))
            warnings.append(f"設定キー`{key}`の候補`{shown}`のeffort`{effort}`は主に使う値の一覧（{efforts}）にありません")
    return warnings


def _cmd_config_show(home: pathlib.Path) -> None:
    """showサブコマンド: 設定値を1キー1行で表示する。"""
    settings = _resolved_settings(home)
    warnings: list[str] = []
    for key, value in settings.items():
        print(f"{key} {value}")
        if key in _MUTABLE_KEY_DEFAULTS:
            warnings.extend(_stage_model_candidate_warnings(key, value))
    _report_candidate_warnings(warnings)


def _report_candidate_warnings(warnings: list[str]) -> None:
    """候補の警告を全件示し、共通の次の操作は1回だけ添える。"""
    if warnings:
        message = "\n".join([warnings[0], *(f"{_outcome.WARNING_PREFIX}{warning}" for warning in warnings[1:])])
        _outcome.report_warning(message, next_action=_UNKNOWN_CANDIDATE_NEXT_ACTION)


def _legacy_key_guidance(keys: list[str]) -> str:
    """旧モデル設定キーを指定した場合に、置き換える新キーを示す文を返す。"""
    legacy = [key for key in keys if key in _LEGACY_MODEL_KEYS]
    if not legacy:
        return ""
    return "。旧モデル設定キーは廃止した（" + "; ".join(f"{key} → {', '.join(_legacy_targets(key))}" for key in legacy) + "）"


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

    旧モデル設定の自動移行の成否に依存せずに保存し、移行で停止した場合の新キーの明示保存を受け付ける。
    """
    if args.key not in _MUTABLE_KEY_DEFAULTS:
        _outcome.report_failure(
            f"変更できない設定キーを指定した: {args.key}{_legacy_key_guidance([args.key])}",
            next_action=f"変更可能なキーから選び直す: {', '.join(_MUTABLE_KEY_DEFAULTS)}",
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
    _save_config(config)
    _outcome.report_success(f"設定を更新した: {args.key}={value}")
    env_name = _config_env_name(args.key)
    if os.environ.get(env_name, ""):
        _outcome.report_warning(
            f"環境変数{env_name}が優先されるため、解除するまで更新値は実効値にならない",
            next_action=f"保存した値を使う場合は環境変数{env_name}を解除する。環境変数の値を使い続ける場合は対応不要",
        )


def _cmd_config_apply_preset(args: argparse.Namespace) -> None:
    """apply-presetサブコマンド: 用途別engine順の4設定を一括保存し、段位モデルとwrite_modelを保持する。

    `show`の指定時とプリセット名の省略時は、設定ファイルを読み書きせずに全プリセットの値を表示する。
    """
    if args.preset in (None, _APPLY_PRESET_SHOW):
        for preset, preset_settings in _PRESET_ENGINE_ORDERS.items():
            print(f"{preset}:")
            for key, value in preset_settings.items():
                print(f"  {key}: {value}")
        return
    settings = _PRESET_ENGINE_ORDERS[args.preset]
    config = _load_config()
    config.update(settings)
    _save_config(config)
    _outcome.report_success(f"用途別engine設定をpreset「{args.preset}」で一括保存した: {len(settings)}件")
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


def _usage_candidates(usage: str, tier: str) -> list[tuple[str, str, str]]:
    """用途のengine順と段位モデル設定から候補の3つ組を合成する。"""
    _check_legacy_environment()
    migrate_legacy_settings()
    candidates: list[tuple[str, str, str]] = []
    for engine in parse_engine_order(resolve_mutable_setting(f"{usage}_engine")):
        model, _, effort = resolve_mutable_setting(tier_model_key(engine, tier)).partition("/")
        candidates.append((engine, model, effort or "medium"))
    return candidates


def parse_unresolved_model_candidates(model_type: str, *, usage: str | None = None) -> list[tuple[str, str, str]]:
    """model_typeと用途に対応する設定から候補の3つ組を返す。

    段位名（`high_tier`など）は`usage`のengine順と段位モデル設定から候補を合成し、
    `usage`の省略時は段位ごとの用途（high→base、medium→review、low→chores）を使う。
    `orchestrate`はorchestrateのengine順とhigh段位、`write`は`write_model`を使い、`usage`を参照しない。
    設定値と同じ書式の候補列を受け取った場合は設定を読まず、その候補列をそのまま分解して返す。
    """
    if usage is not None and usage not in USAGES:
        raise ValueError(f"unknown usage: {usage} (available: {', '.join(USAGES)})")
    if model_type == "write":
        return parse_stage_model_candidates(resolve_mutable_setting("write_model"))
    if model_type == "orchestrate":
        return _usage_candidates("orchestrate", "high")
    tier = TIER_MODEL_TYPES.get(model_type)
    if tier is not None:
        return _usage_candidates(usage or _TIER_DEFAULT_USAGES[model_type], tier)
    try:
        return parse_stage_model_candidates(model_type)
    except ValueError as error:
        available = [*TIER_MODEL_TYPES, "orchestrate", "write"]
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


def resolve_model_candidates(
    model_type: str, *, usage: str | None = None, catalog: list[dict[str, Any]] | None = None
) -> list[tuple[str, str, str]]:
    """Codex系列名を実行時の完全IDへ解決して用途・段位別の候補を返す。"""
    return resolve_catalog(parse_unresolved_model_candidates(model_type, usage=usage), catalog=catalog)


def build_parser(config: argparse.ArgumentParser) -> None:
    """`config`サブパーサ配下にshow/get/set/apply-presetを登録する。"""
    sub = _atk_help.add_subcommands(config, dest="config_subcommand", required=False)
    _atk_help.add_command(sub, "show", **_atk_help.HELP["atk config show"])
    get = _atk_help.add_command(sub, "get", **_atk_help.HELP["atk config get"])
    get.add_argument("key", metavar="KEY", nargs="+", help="取得する1件以上のキー（config showの出力キーと同一）。")
    set_ = _atk_help.add_command(sub, "set", **_atk_help.HELP["atk config set"])
    set_.add_argument("key", metavar="KEY", help=f"変更可能なキー: {', '.join(_MUTABLE_KEY_DEFAULTS)}")
    set_.add_argument(
        "value", metavar="VALUE", help="設定する値。write_modelの複数候補とengine順はASCIIカンマ区切りで指定する。"
    )
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
