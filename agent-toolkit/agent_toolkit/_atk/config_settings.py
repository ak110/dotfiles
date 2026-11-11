"""設定キーの定義・形式検証とJSONの永続化。"""

from __future__ import annotations

import dataclasses
import json
import os
import pathlib
import re
import sys
from typing import Any

lazy import platformdirs

from agent_toolkit._common import codex_models
from agent_toolkit._common import next_action as _next_action
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._git import command as _git_command
from agent_toolkit._git import remote as _git_remote

CONFIG_FILENAME = "config.json"

MODEL_TYPES = ("execute", "review", "review_fast", "chores", "chores_fast", "orchestrate", "write")

"""工程ごとに直接解決するモデル候補列の種別。"""

MODEL_DEFAULTS = {
    "execute_model": "claude:opus[1m]/medium,codex:sol/medium",
    "review_model": "codex:sol/medium,claude:sonnet[1m]/medium",
    "review_fast_model": "codex:luna/medium,claude:haiku/medium",
    "chores_model": "codex:sol/medium,claude:sonnet[1m]/medium",
    "chores_fast_model": "codex:luna/medium,claude:haiku/medium",
    "orchestrate_model": "claude:opus[1m]/medium,codex:sol/medium",
    "write_model": "agy:gemini-3.8-flash/medium,claude:claude-opus-5-5/medium",
}

# write_modelはプリセットによる変更対象から外す。
PRESET_MODELS = {
    "codex-balanced": {
        **{key: value for key, value in MODEL_DEFAULTS.items() if key != "write_model"},
    },
    "codex-near-primary": {
        **{key: value for key, value in MODEL_DEFAULTS.items() if key != "write_model"},
        "execute_model": "codex:sol/medium,claude:opus[1m]/medium",
    },
    "codex-primary": {
        **{key: value for key, value in MODEL_DEFAULTS.items() if key != "write_model"},
        "execute_model": "codex:sol/medium,claude:opus[1m]/medium",
        "orchestrate_model": "codex:sol/medium,claude:opus[1m]/medium",
    },
    "claude-balanced": {
        **{key: value for key, value in MODEL_DEFAULTS.items() if key != "write_model"},
        "chores_model": "claude:sonnet[1m]/medium,codex:sol/medium",
        "chores_fast_model": "claude:haiku/medium,codex:luna/medium",
    },
    "claude-primary": {
        **{key: value for key, value in MODEL_DEFAULTS.items() if key != "write_model"},
        "review_model": "claude:sonnet[1m]/medium,codex:sol/medium",
        "review_fast_model": "claude:haiku/medium,codex:luna/medium",
        "chores_model": "claude:sonnet[1m]/medium,codex:sol/medium",
        "chores_fast_model": "claude:haiku/medium,codex:luna/medium",
    },
}

# apply-presetの位置引数でプリセット名の代わりに受理し、全プリセットの値を表示する値。
APPLY_PRESET_SHOW = "show"

# IDEのリモート開発拡張はLinux側の待受ポートをWindows側へ自動転送するため、Windowsでも同じ番号を
# 未指定時の値にすると、Windowsでローカル実行したatk serveの待受ポートが転送先と衝突する。
# Windowsだけ別の番号にして衝突を避ける。
SERVE_LINUX_DEFAULT_PORT = 28766

SERVE_WINDOWS_DEFAULT_PORT = 28876


def serve_default_port(platform: str | None = None) -> int:
    """`atk serve`の待受ポートを指定しない場合に使うOS別の値を返す。"""
    return SERVE_WINDOWS_DEFAULT_PORT if (platform or sys.platform) == "win32" else SERVE_LINUX_DEFAULT_PORT


# `atk serve`の設定。パスの空文字列は未指定を表し、`atk serve`が各画面の省略時の参照元を使う。
SERVE_KEY_DEFAULTS = {
    "serve_host": "127.0.0.1",
    "serve_port": str(serve_default_port()),
    "serve_plans_root": "",
    "serve_plans_remote_hosts": "[]",
    "serve_sessions_claude_home": "",
    "serve_sessions_codex_home": "",
    "serve_sessions_remote_hosts": "[]",
}

SERVE_PATH_KEYS = frozenset({"serve_plans_root", "serve_sessions_claude_home", "serve_sessions_codex_home"})

SERVE_HOSTS_KEYS = frozenset({"serve_plans_remote_hosts", "serve_sessions_remote_hosts"})

SERVE_CONFIG_ENV = "AGENT_TOOLKIT_SERVE_CONFIG"

"""旧`serve.toml`の場所を指定していた環境変数。統合後は自動移行の移行元を示す。"""

# 旧`serve.toml`の位置（節名とキー）と移行先のキー。
LEGACY_SERVE_KEYS = {
    (None, "host"): "serve_host",
    (None, "port"): "serve_port",
    ("plans", "root"): "serve_plans_root",
    ("plans", "remote_hosts"): "serve_plans_remote_hosts",
    ("sessions", "claude_home"): "serve_sessions_claude_home",
    ("sessions", "codex_home"): "serve_sessions_codex_home",
    ("sessions", "remote_hosts"): "serve_sessions_remote_hosts",
}

CONNECTION_KINDS = ("subscription", "credits", "payg")

"""接続の種別。サブスクリプション、有限のAPIクレジット、従量課金APIの順に並べる。"""

# 接続の認証の所在（種別ごとに1キー）と、役割別の使用順。`payg`は追加料金が発生するため、使用順の初期値へ含めない。
CONNECTION_DEFAULTS = {
    "claude_credits_env_file": "",
    "claude_payg_env_file": "",
    "claude_orchestrator_connections": "subscription,credits",
    "claude_worker_connections": "credits,subscription",
    "codex_credits_provider": "",
    "codex_payg_provider": "",
    "codex_orchestrator_connections": "subscription,credits",
    "codex_worker_connections": "credits,subscription",
}

ENV_FILE_KEYS = frozenset({"claude_credits_env_file", "claude_payg_env_file"})

PROVIDER_KEYS = frozenset({"codex_credits_provider", "codex_payg_provider"})

CONNECTION_ORDER_KEYS = frozenset(key for key in CONNECTION_DEFAULTS if key.endswith("_connections"))

# 挿入順は`atk config show`の表示順とキー案内の順を兼ねる。
MUTABLE_KEY_DEFAULTS = {
    **MODEL_DEFAULTS,
    "codex_fast_mode": "false",
    "codex_auto_reset": "false",
    **CONNECTION_DEFAULTS,
    "claude_usage_limit_wait": "five-hour",
    "codex_usage_limit_wait": "five-hour",
    "process_loop_repositories": "[]",
    "session_review_auto_start": "true",
    "dotfiles_mode": "full",
    **SERVE_KEY_DEFAULTS,
}

# 旧接続設定キーと移行先。保存値は読込時に移行先へ移し、環境変数とget・setでは移行先を示して拒否する。
RETIRED_CONNECTION_KEYS = {
    "claude_api_env_file": ("claude_credits_env_file", "claude_payg_env_file"),
    "claude_orchestrator_api_only": ("claude_orchestrator_connections",),
    "claude_worker_api_first": ("claude_worker_connections",),
    "claude_api_mode": ("claude_orchestrator_connections", "claude_worker_connections"),
    "codex_model_providers": (
        "codex_credits_provider",
        "codex_payg_provider",
        "codex_orchestrator_connections",
        "codex_worker_connections",
    ),
}

CLAUDE_RETIRED_CONNECTION_KEYS = (
    "claude_api_env_file",
    "claude_orchestrator_api_only",
    "claude_worker_api_first",
    "claude_api_mode",
)

# 旧キーは段位とengine順を1つの候補列で持っていた。保存値は移行先へ一括で移し、新しいget・setの対象から除く。
LEGACY_TIER_DEFAULTS = {
    "claude_high_tier_model": "opus[1m]/medium",
    "claude_medium_tier_model": "sonnet[1m]/medium",
    "claude_low_tier_model": "haiku/medium",
    "codex_high_tier_model": "sol/medium",
    "codex_medium_tier_model": "sol/medium",
    "codex_low_tier_model": "luna/medium",
    "opencode_high_tier_model": "",
    "opencode_medium_tier_model": "",
    "opencode_low_tier_model": "",
}

LEGACY_ENGINE_DEFAULTS = {
    "base_engine": "codex,claude",
    "execute_engine": "claude,codex",
    "review_engine": "codex,claude",
    "chores_engine": "codex,claude",
    "orchestrate_engine": "claude,codex",
}

RETIRED_MODEL_KEYS = frozenset(
    {
        *LEGACY_TIER_DEFAULTS,
        *LEGACY_ENGINE_DEFAULTS,
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

STAGE_MODEL_PATTERN = re.compile(r"^(?:claude|codex|agy):[^/:,，\s]+(?:/[^/:,，\s]+)?$")

OPENCODE_MODEL_PATTERN = re.compile(r"^[^/:,@\s]+/[^/,@\s]+(?:/[^/,@\s]+)*(?:@[^/,@\s]+)?$")

CONFIG_ENV_PREFIX = "AGENT_TOOLKIT_CONFIG_"

# 主に使うモデル名・effortの参考一覧。受理可否の判定には使わず、一覧外は警告のみで受理する。
KNOWN_MODELS = {
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

KNOWN_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})


def config_dir() -> pathlib.Path:
    """platformdirsの設定ディレクトリ解決規約に従い、設定ファイル配置ディレクトリを返す。

    `appauthor=False`はWindowsでappnameが二重階層になる挙動を防ぐ。
    """
    return pathlib.Path(platformdirs.user_config_dir("agent-toolkit", appauthor=False))


def config_file_path() -> pathlib.Path:
    """変更可能設定を永続化するJSONファイルの絶対パスを返す。"""
    return config_dir() / CONFIG_FILENAME


def process_loop_configuration_path() -> pathlib.Path:
    """常駐プロセスが登録先の変更を監視する設定ファイルのパスを返す。"""
    return config_file_path()


def load_config() -> dict[str, str]:
    """永続化済みの変更可能設定を読み込む。ファイル不在・破損時は空辞書を返す。"""
    path = config_file_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError, OSError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {key: value for key, value in data.items() if isinstance(value, str)}


def load_config_strict() -> dict[str, Any]:
    """永続化済みの変更可能設定を読み込む。破損・形式不正は空とみなさず`ActionableError`を送出する。

    他の設定を保持したまま一部のキーを書き込む処理が使う。破損したファイルを空として扱うと、
    書込みが他用途の設定を失わせるためである。文字列以外の値は保持の対象として読んだまま返す。
    """
    path = config_file_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise _next_action.ActionableError(
            f"設定ファイル{path}を読めない: {error}",
            next_action=f"{path}をJSONのobjectとして読める内容へ直して再実行する",
        ) from error
    if not isinstance(data, dict):
        raise _next_action.ActionableError(
            f"設定ファイル{path}がJSONのobjectではない",
            next_action=f"{path}をJSONのobjectへ直して再実行する",
        )
    return data


def save_config(config: dict[str, Any]) -> None:
    """変更可能設定をJSONファイルへ永続化する。"""
    path = config_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def config_env_name(key: str) -> str:
    """変更可能設定キーに対応する環境変数名を返す。"""
    return f"{CONFIG_ENV_PREFIX}{key.upper()}"


def config_environment_variable_names() -> tuple[str, ...]:
    """現行設定と、置換案内の対象である旧設定の環境変数名を返す。"""
    return tuple(config_env_name(key) for key in (*MUTABLE_KEY_DEFAULTS, *RETIRED_CONNECTION_KEYS, *sorted(RETIRED_MODEL_KEYS)))


def validate_stage_model_candidates(value: str) -> None:
    """候補列の各候補を検証し、書式不正なら`ValueError`を送出する。"""
    candidates = value.split(",")
    if not candidates or any(
        STAGE_MODEL_PATTERN.fullmatch(candidate) is None
        and not (candidate.startswith("opencode:") and OPENCODE_MODEL_PATTERN.fullmatch(candidate.removeprefix("opencode:")))
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


def validate_mutable_setting(key: str, value: str) -> None:
    """速度、接続先列、工程別モデル候補をそれぞれの受理形式で確かめる。"""
    if key == "dotfiles_mode":
        if value not in {"full", "minimal"}:
            raise ValueError("受理可能値: full, minimal")
    elif key in ENV_FILE_KEYS:
        if "\n" in value or "\r" in value or "\0" in value:
            raise ValueError("envファイルのパス1つ、または空文字列で指定する")
    elif key in PROVIDER_KEYS:
        if any(char.isspace() or char == "," for char in value):
            raise ValueError("config.tomlのprovider ID 1つ（空白とカンマを含まない）、または空文字列で指定する")
    elif key in CONNECTION_ORDER_KEYS:
        parse_connection_order(value)
    elif key == "serve_host":
        if not value.strip():
            raise ValueError("待受ホストを空でない文字列で指定する")
    elif key == "serve_port":
        if not value.isdecimal() or not 1 <= int(value) <= 65535:
            raise ValueError("1から65535までの整数で指定する")
    elif key in SERVE_PATH_KEYS:
        if "\n" in value or "\r" in value or "\0" in value:
            raise ValueError("パス1つ、または未指定を表す空文字列で指定する")
    elif key in SERVE_HOSTS_KEYS:
        parse_remote_hosts(value)
    elif key in {"claude_usage_limit_wait", "codex_usage_limit_wait"}:
        if value not in {"never", "five-hour", "always"}:
            raise ValueError("受理可能値: never, five-hour, always")
    elif key == "process_loop_repositories":
        parse_process_loop_repositories(value)
    elif key in {"codex_fast_mode", "codex_auto_reset", "session_review_auto_start"}:
        if value not in {"true", "false"}:
            raise ValueError("受理可能値: true, false")
    elif key in MODEL_DEFAULTS:
        validate_stage_model_candidates(value)
        if key == "orchestrate_model" and any(candidate.startswith("opencode:") for candidate in value.split(",")):
            raise ValueError("受理可能書式: orchestrate_modelはclaude、codex、agyの候補だけをASCIIカンマ区切りで指定する")
    else:
        validate_stage_model_candidates(value)


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
    env_value = os.environ.get(config_env_name(key))
    try:
        if env_value is not None:
            value = env_value
        else:
            path = config_file_path()
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


def parse_remote_hosts(value: str) -> tuple[str, ...]:
    """リモートホストの指定をSSH接続先名のJSON文字列配列として解釈する。空配列はリモートホストなしを表す。"""
    try:
        hosts = json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError('SSH接続先名のJSON文字列配列で指定する（例: ["remote-host"]、空配列も可）') from error
    if not isinstance(hosts, list) or any(not isinstance(host, str) or not host.strip() for host in hosts):
        raise ValueError("空でないSSH接続先名のJSON文字列配列で指定する（空配列も可）")
    return tuple(hosts)


def parse_codex_provider_candidates(value: str) -> tuple[str, ...]:
    """旧設定`codex_model_providers`の接続先IDを空白除去・順序保持で読み、同じIDは一度だけ返す。"""
    if not value.strip():
        return ()
    providers = tuple(part.strip() for part in value.split(","))
    if any(not provider or any(char.isspace() for char in provider) for provider in providers):
        raise ValueError("受理可能書式: 空文字列、またはprovider IDのASCIIカンマ区切り（空要素は不可）")
    return tuple(dict.fromkeys(providers))


def parse_connection_order(value: str) -> tuple[str, ...]:
    """接続の使用順を検証して分解し、書式不正なら`ValueError`を送出する。"""
    kinds = tuple(part.strip() for part in value.split(","))
    if not value.strip() or any(kind not in CONNECTION_KINDS for kind in kinds) or len(set(kinds)) != len(kinds):
        raise ValueError("受理可能書式: subscription・credits・paygを重複なしで1つ以上並べたASCIIカンマ区切り")
    return kinds


def build_connection_order_key(engine: str, role: str) -> str:
    """エンジンと役割（`orchestrator`・`delegate`）から使用順の設定キーを返す。"""
    if engine not in {"claude", "codex"}:
        raise KeyError(engine)
    return f"{engine}_{'orchestrator' if role == 'orchestrator' else 'worker'}_connections"


def connection_order_key(engine: str, role: str) -> str:
    """診断で案内する使用順の設定キー名を返す。"""
    return build_connection_order_key(engine, role)


def mutable_setting_default(key: str) -> str:
    """変更可能な設定の初期値を返す。未知のキーは`KeyError`を送出する。"""
    return MUTABLE_KEY_DEFAULTS[key]


def raw_mutable_setting(key: str) -> str:
    """変更可能な設定の検証前の値を、環境変数、保存値、初期値の順に解決して返す。未知のキーは`KeyError`を送出する。"""
    default = mutable_setting_default(key)
    explicit = os.environ.get(config_env_name(key), "")
    if explicit:
        return explicit
    return load_config().get(key, default)


def normalize_setting(key: str, value: str) -> str:
    """検証済みの値を保存・表示する正規形へ揃える。"""
    if key in ENV_FILE_KEYS:
        return str(pathlib.Path(value).expanduser()) if value else ""
    if key in CONNECTION_ORDER_KEYS:
        return ",".join(parse_connection_order(value))
    return value
