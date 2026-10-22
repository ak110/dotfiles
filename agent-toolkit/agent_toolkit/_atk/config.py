"""`atk config`サブコマンド。

XDG関連パス（設定・状態・データ各ディレクトリ、private-notesの解決結果）の確認と、
工程ごとのモデル候補列の確認・変更を提供する。
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

MODEL_TYPES = ("execute", "review", "review_fast", "chores", "chores_fast", "orchestrate", "write")
"""工程ごとに直接解決するモデル候補列の種別。"""

_MODEL_DEFAULTS = {
    "execute_model": "claude:opus[1m]/medium,codex:sol/medium",
    "review_model": "codex:terra/medium,claude:sonnet[1m]/medium",
    "review_fast_model": "codex:luna/medium,claude:haiku/medium",
    "chores_model": "codex:terra/medium,claude:sonnet[1m]/medium",
    "chores_fast_model": "codex:luna/medium,claude:haiku/medium",
    "orchestrate_model": "claude:opus[1m]/medium,codex:sol/medium",
    "write_model": "agy:gemini-3.8-flash/medium,claude:claude-opus-5-5/medium",
}

# write_modelはプリセットによる変更対象から外す。
_PRESET_MODELS = {
    "codex-balanced": {
        **{key: value for key, value in _MODEL_DEFAULTS.items() if key != "write_model"},
    },
    "codex-near-primary": {
        **{key: value for key, value in _MODEL_DEFAULTS.items() if key != "write_model"},
        "execute_model": "codex:sol/medium,claude:opus[1m]/medium",
    },
    "codex-primary": {
        **{key: value for key, value in _MODEL_DEFAULTS.items() if key != "write_model"},
        "execute_model": "codex:sol/medium,claude:opus[1m]/medium",
        "orchestrate_model": "codex:sol/medium,claude:opus[1m]/medium",
    },
    "claude-balanced": {
        **{key: value for key, value in _MODEL_DEFAULTS.items() if key != "write_model"},
        "chores_model": "claude:sonnet[1m]/medium,codex:terra/medium",
        "chores_fast_model": "claude:haiku/medium,codex:luna/medium",
    },
    "claude-primary": {
        **{key: value for key, value in _MODEL_DEFAULTS.items() if key != "write_model"},
        "review_model": "claude:sonnet[1m]/medium,codex:terra/medium",
        "review_fast_model": "claude:haiku/medium,codex:luna/medium",
        "chores_model": "claude:sonnet[1m]/medium,codex:terra/medium",
        "chores_fast_model": "claude:haiku/medium,codex:luna/medium",
    },
}

# apply-presetの位置引数でプリセット名の代わりに受理し、全プリセットの値を表示する値。
_APPLY_PRESET_SHOW = "show"


# 挿入順は`atk config show`の表示順とキー案内の順を兼ねる。
_MUTABLE_KEY_DEFAULTS = {
    **_MODEL_DEFAULTS,
    "codex_fast_mode": "false",
    "codex_model_providers": "",
    "claude_api_env_file": "",
    "claude_api_mode": "pay-as-you-go",
    "process_loop_repositories": "[]",
    "dotfiles_mode": "full",
}

# 旧キーは段位とengine順を1つの候補列で持っていた。保存値は移行先へ一括で移し、新しいget・setの対象から除く。
_LEGACY_TIER_DEFAULTS = {
    "claude_high_tier_model": "opus[1m]/medium",
    "claude_medium_tier_model": "sonnet[1m]/medium",
    "claude_low_tier_model": "haiku/medium",
    "codex_high_tier_model": "sol/medium",
    "codex_medium_tier_model": "terra/medium",
    "codex_low_tier_model": "luna/medium",
    "opencode_high_tier_model": "",
    "opencode_medium_tier_model": "",
    "opencode_low_tier_model": "",
}
_LEGACY_ENGINE_DEFAULTS = {
    "base_engine": "codex,claude",
    "execute_engine": "claude,codex",
    "review_engine": "codex,claude",
    "chores_engine": "codex,claude",
    "orchestrate_engine": "claude,codex",
}
_RETIRED_MODEL_KEYS = frozenset(
    {
        *_LEGACY_TIER_DEFAULTS,
        *_LEGACY_ENGINE_DEFAULTS,
        "execute_fast_model",
        "execute_review_model",
        "explore_model",
        "explore_fast_model",
        "pick_wi_model",
        "plan_model",
        "plan_review_model",
        "session_review_model",
        "high_tier_model",
        "medium_tier_model",
        "low_tier_model",
    }
)

_STAGE_MODEL_PATTERN = re.compile(r"^(?:claude|codex|agy):[^/:,，\s]+(?:/[^/:,，\s]+)?$")
_OPENCODE_MODEL_PATTERN = re.compile(r"^[^/:,@\s]+/[^/,@\s]+(?:/[^/,@\s]+)*(?:@[^/,@\s]+)?$")
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
    "agy": frozenset({"gemini-4-argon", "gemini-3.8-flash", "claude-opus-5-5", "claude-sonnet-5-5", "gpt-oss-120b"}),
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
    return tuple(_config_env_name(key) for key in (*_MUTABLE_KEY_DEFAULTS, *sorted(_RETIRED_MODEL_KEYS)))


def _validate_stage_model_candidates(value: str) -> None:
    """候補列の各候補を検証し、書式不正なら`ValueError`を送出する。"""
    candidates = value.split(",")
    if not candidates or any(
        _STAGE_MODEL_PATTERN.fullmatch(candidate) is None
        and not (candidate.startswith("opencode:") and _OPENCODE_MODEL_PATTERN.fullmatch(candidate.removeprefix("opencode:")))
        for candidate in candidates
    ):
        raise ValueError(
            "受理可能書式: <claude|codex|agy>:<model>[/<effort>]、"
            "opencode:<provider>/<model>[@<effort>]（複数候補はASCIIカンマ区切り）"
        )


def parse_engine_order(value: str) -> tuple[str, ...]:
    """用途別engine順を検証して分解し、書式不正なら`ValueError`を送出する。"""
    engines = tuple(value.split(","))
    if any(engine not in {"claude", "codex", "opencode"} for engine in engines) or len(set(engines)) != len(engines):
        raise ValueError("受理可能書式: codex・claude・opencodeを重複なしで並べたASCIIカンマ区切り")
    return engines


def _validate_mutable_setting(key: str, value: str) -> None:
    """速度、接続先列、工程別モデル候補をそれぞれの受理形式で確かめる。"""
    if key == "dotfiles_mode":
        if value not in {"full", "minimal"}:
            raise ValueError("受理可能値: full, minimal")
    elif key == "claude_api_env_file":
        if "\n" in value or "\r" in value or "\0" in value:
            raise ValueError("envファイルのパス1つ、または空文字列で指定する")
    elif key == "claude_api_mode":
        if value not in {"pay-as-you-go", "api-credits", "api-only"}:
            raise ValueError("受理可能値: pay-as-you-go, api-credits, api-only")
    elif key == "process_loop_repositories":
        parse_process_loop_repositories(value)
    elif key == "codex_fast_mode":
        if value not in {"true", "false"}:
            raise ValueError("受理可能値: true, false")
    elif key == "codex_model_providers":
        parse_codex_provider_candidates(value)
    elif key in _MODEL_DEFAULTS:
        _validate_stage_model_candidates(value)
        if key == "orchestrate_model" and any(candidate.startswith("opencode:") for candidate in value.split(",")):
            raise ValueError("受理可能書式: orchestrate_modelはclaude、codex、agyの候補だけをASCIIカンマ区切りで指定する")
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
    _check_legacy_environment()
    migrate_legacy_settings()
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
    if key == "claude_api_env_file":
        return str(pathlib.Path(value).expanduser()) if value else ""
    return ",".join(parse_codex_provider_candidates(value)) if key == "codex_model_providers" else value


def _plan_legacy_migration(config: dict[str, str], *, updates: dict[str, str] | None = None) -> dict[str, str]:
    """旧設定を合成し、明示保存値を同じ原子的更新へまとめる。

    読取時は全工程を検証する。明示保存では候補未設定の他工程を空のまま保持し、
    その後のsetで修復できるようにする。新しいCLI値として空候補は受理しない。
    """
    migrated = {**config, **(updates or {})}
    if not any(key in config for key in _RETIRED_MODEL_KEYS):
        return migrated
    stages = {
        "execute": ("execute", "high"),
        "review": ("review", "medium"),
        "review_fast": ("review", "low"),
        "chores": ("chores", "medium"),
        "chores_fast": ("chores", "low"),
        "orchestrate": ("orchestrate", "high"),
    }
    if any(key in config for key in (*_LEGACY_TIER_DEFAULTS, *_LEGACY_ENGINE_DEFAULTS)):
        for stage, (usage, tier) in stages.items():
            target = f"{stage}_model"
            if updates is not None and target in updates:
                continue
            engine_key = f"{usage}_engine"
            default_order = _LEGACY_ENGINE_DEFAULTS[engine_key]
            if stage == "execute" and "base_engine" in config:
                default_order = config["base_engine"]
            engines = parse_engine_order(config.get(engine_key, default_order))
            candidates = []
            for engine in engines:
                key = f"{engine}_{tier}_tier_model"
                value = config.get(key, _LEGACY_TIER_DEFAULTS[key])
                if engine == "opencode" and not value:
                    continue
                if not value:
                    raise ValueError(f"旧設定{key}が空です。移行前に候補モデルを指定する")
                candidates.append(f"{engine}:{value}")
            migrated[target] = ",".join(candidates)
            if not candidates:
                if updates is not None:
                    continue
                raise _next_action.ActionableError(
                    f"旧設定から{target}へ合成するモデル候補がありません。受理可能書式の候補列が必要です",
                    next_action=(
                        f"`atk config set {target} <候補列>`で候補を明示するか、"
                        "`atk config apply-preset <プリセット名>`で6工程を一括設定して再実行する"
                    ),
                )
            _validate_mutable_setting(target, migrated[target])
    return {key: value for key, value in migrated.items() if key not in _RETIRED_MODEL_KEYS}


def migrate_legacy_settings() -> None:
    """旧保存値を工程別候補列へ一括移行する。環境変数は保存しない。"""
    config = _load_config()
    if not any(key in config for key in _RETIRED_MODEL_KEYS):
        return
    _save_config(_plan_legacy_migration(config))


def _check_legacy_environment() -> None:
    """廃止キーの環境変数があれば、対応する工程別環境変数を案内する。"""
    found = sorted(key for key in _RETIRED_MODEL_KEYS if _config_env_name(key) in os.environ)
    if found:
        replacements = []
        for key in found:
            if "orchestrate" in key:
                stages = ("orchestrate",)
            elif "medium" in key:
                stages = ("review", "chores")
            elif "low" in key:
                stages = ("review_fast", "chores_fast")
            elif key == "review_engine":
                stages = ("review", "review_fast")
            elif key == "chores_engine":
                stages = ("chores", "chores_fast")
            elif "high" in key:
                stages = ("execute", "orchestrate")
            else:
                stages = ("execute",)
            replacements.append(
                f"{_config_env_name(key)} → {', '.join(_config_env_name(f'{stage}_model') for stage in stages)}"
            )
        raise _next_action.ActionableError(
            f"廃止した旧モデル設定の環境変数が設定されています: {', '.join(_config_env_name(key) for key in found)}",
            next_action="旧環境変数を解除し、工程別の環境変数へ置き換えて再実行する: " + "; ".join(replacements),
        )


def _resolved_settings(home: pathlib.Path) -> dict[str, str]:
    """XDG関連パスの導出値と変更可能設定をまとめて返す（表示・`get`共通の解決結果）。"""
    _check_legacy_environment()
    migrate_legacy_settings()
    # Windowsでappnameがappauthorとしても付与される二重階層を防ぐ。
    return {
        "config_dir": str(_config_dir()),
        "state_dir": str(_state_paths.state_dir()),
        "data_dir": str(pathlib.Path(platformdirs.user_data_dir("agent-toolkit", appauthor=False))),
        "private_notes": str(_private_notes.default_private_notes(home)),
        **{key: resolve_mutable_setting(key) for key in _MUTABLE_KEY_DEFAULTS},
    }


_UNKNOWN_CANDIDATE_NEXT_ACTION = "意図した名前か確認し、誤記なら同じキーへ`atk config set <KEY> <VALUE>`で設定し直す"
"""参考一覧外のモデル候補の警告に添える次の操作。"""


def _stage_model_candidate_warnings(key: str, value: str) -> list[str]:
    """参考一覧外のモデル候補を警告の本文へ変換する。接頭辞は`report_warning`が付ける。"""
    warnings: list[str] = []
    if key not in _MODEL_DEFAULTS:
        return warnings
    candidates = value.split(",")
    for candidate in candidates:
        engine, model, effort = _parse_stage_model(candidate)
        shown = candidate
        if engine == "opencode":
            continue
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
    for key, value in settings.items():
        print(f"{key} {value}")


def _report_candidate_warnings(warnings: list[str]) -> None:
    """候補の警告を全件示し、共通の次の操作は1回だけ添える。"""
    if warnings:
        message = "\n".join([warnings[0], *(f"{_outcome.WARNING_PREFIX}{warning}" for warning in warnings[1:])])
        _outcome.report_warning(message, next_action=_UNKNOWN_CANDIDATE_NEXT_ACTION)


def _legacy_key_guidance(keys: list[str]) -> str:
    """旧モデル設定キーを指定した場合に、置き換える新キーを示す文を返す。"""
    legacy = [key for key in keys if key in _RETIRED_MODEL_KEYS]
    if not legacy:
        return ""
    return "。旧モデル設定キーは廃止した。対応する工程別の`*_model`キーを指定する"


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
    value = ",".join(parse_codex_provider_candidates(args.value)) if args.key == "codex_model_providers" else args.value
    config = _plan_legacy_migration(_load_config(), updates={args.key: value})
    _save_config(config)
    _outcome.report_success(f"設定を更新した: {args.key}={value}")
    env_name = _config_env_name(args.key)
    if os.environ.get(env_name, ""):
        _outcome.report_warning(
            f"環境変数{env_name}が優先されるため、解除するまで更新値は実効値にならない",
            next_action=f"保存した値を使う場合は環境変数{env_name}を解除する。環境変数の値を使い続ける場合は対応不要",
        )


def _cmd_config_apply_preset(args: argparse.Namespace) -> None:
    """apply-presetサブコマンド: 工程別の6設定を一括保存し、write_modelを保持する。

    `show`の指定時とプリセット名の省略時は、設定ファイルを読み書きせずに全プリセットの値を表示する。
    """
    if args.preset in (None, _APPLY_PRESET_SHOW):
        for preset, preset_settings in _PRESET_MODELS.items():
            print(f"{preset}:")
            for key, value in preset_settings.items():
                print(f"  {key}: {value}")
        return
    settings = _PRESET_MODELS[args.preset]
    config = _plan_legacy_migration(_load_config(), updates=settings)
    _save_config(config)
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
    _validate_stage_model_candidates(value)
    return [
        (engine, model, effort or "medium")
        for engine, model, effort in (_parse_stage_model(candidate) for candidate in value.split(","))
    ]


def parse_unresolved_model_candidates(model_type: str) -> list[tuple[str, str, str]]:
    """工程名または直接候補列から候補の3つ組を返す。"""
    if model_type in MODEL_TYPES:
        return parse_stage_model_candidates(resolve_mutable_setting(f"{model_type}_model"))
    try:
        return parse_stage_model_candidates(model_type)
    except ValueError as error:
        available = list(MODEL_TYPES)
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
    get = _atk_help.add_command(sub, "get", **_atk_help.HELP["atk config get"])
    get.add_argument("key", metavar="KEY", nargs="+", help="取得する1件以上のキー（config showの出力キーと同一）。")
    set_ = _atk_help.add_command(sub, "set", **_atk_help.HELP["atk config set"])
    set_.add_argument("key", metavar="KEY", help=f"変更可能なキー: {', '.join(_MUTABLE_KEY_DEFAULTS)}")
    set_.add_argument("value", metavar="VALUE", help="設定する値。モデルの複数候補はASCIIカンマ区切りで指定する。")
    apply_preset = _atk_help.add_command(sub, "apply-preset", **_atk_help.HELP["atk config apply-preset"])
    apply_preset.add_argument(
        "preset",
        nargs="?",
        choices=(_APPLY_PRESET_SHOW, *_PRESET_MODELS),
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
