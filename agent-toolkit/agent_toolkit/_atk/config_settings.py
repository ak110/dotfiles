from __future__ import annotations

import dataclasses
import json
import math
import os
import pathlib
import re
import sys
from typing import Any

lazy import platformdirs

from agent_toolkit._atk import config_repositories
from agent_toolkit._common import codex_models, runtime_environment
from agent_toolkit._common import next_action as _next_action
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._common.usage_settings import validate_permissions

CONFIG_FILENAME = "config.json"

MODEL_TYPES = ("execute", "review", "review_fast", "chores", "chores_fast", "orchestrate", "write")

"""工程ごとに直接解決するモデル候補列の種別。"""

MODEL_DEFAULTS = {
    "execute_model": "codex:sol/low,claude:opus[1m]/low",
    "review_model": "codex:sol/medium,claude:sonnet[1m]/medium",
    "review_fast_model": "codex:luna/medium,claude:haiku/medium",
    "chores_model": "codex:sol/low,claude:sonnet[1m]/low",
    "chores_fast_model": "codex:luna/low,claude:haiku/low",
    "orchestrate_model": "claude:opus[1m]/medium,codex:sol/medium",
    "write_model": "agy:gemini-3.8-flash/low,claude:claude-opus-5-5/low",
}


PRESET_MODELS = {
    "codex-balanced": {
        **{key: value for key, value in MODEL_DEFAULTS.items() if key != "write_model"},
        "execute_model": "claude:opus[1m]/low,codex:sol/low",
    },
    "codex-near-primary": {
        **{key: value for key, value in MODEL_DEFAULTS.items() if key != "write_model"},
        "execute_model": "codex:sol/low,claude:opus[1m]/low",
    },
    "codex-primary": {
        **{key: value for key, value in MODEL_DEFAULTS.items() if key != "write_model"},
        "execute_model": "codex:sol/low,claude:opus[1m]/low",
        "orchestrate_model": "codex:sol/medium,claude:opus[1m]/medium",
    },
    "claude-balanced": {
        **{key: value for key, value in MODEL_DEFAULTS.items() if key != "write_model"},
        "execute_model": "claude:opus[1m]/low,codex:sol/low",
        "chores_model": "claude:sonnet[1m]/low,codex:sol/low",
        "chores_fast_model": "claude:haiku/low,codex:luna/low",
    },
    "claude-primary": {
        **{key: value for key, value in MODEL_DEFAULTS.items() if key != "write_model"},
        "execute_model": "claude:opus[1m]/low,codex:sol/low",
        "review_model": "claude:sonnet[1m]/medium,codex:sol/medium",
        "review_fast_model": "claude:haiku/medium,codex:luna/medium",
        "chores_model": "claude:sonnet[1m]/low,codex:sol/low",
        "chores_fast_model": "claude:haiku/low,codex:luna/low",
    },
}


APPLY_PRESET_SHOW = "show"

# IDEのリモート開発拡張はLinux側の待受ポートをWindows側へ自動転送するため、Windowsでも同じ番号を
# 未指定時の値にすると、Windowsでローカル実行したatk serveの待受ポートが転送先と衝突する。
# Windowsだけ別の番号にして衝突を避ける。
SERVE_LINUX_DEFAULT_PORT = 28766

SERVE_WINDOWS_DEFAULT_PORT = 28876


def serve_default_port(platform: str | None = None) -> int:
    return SERVE_WINDOWS_DEFAULT_PORT if (platform or sys.platform) == "win32" else SERVE_LINUX_DEFAULT_PORT


SERVE_KEY_DEFAULTS = {
    "serve_host": "127.0.0.1",
    "serve_port": str(serve_default_port()),
    "serve_plans_root": "",
    "remote_hosts": "[]",
}

SERVE_PATH_KEYS = frozenset({"serve_plans_root"})

SERVE_HOSTS_KEYS = frozenset({"remote_hosts"})
LEGACY_REMOTE_HOSTS_KEYS = ("serve_plans_remote_hosts", "serve_sessions_remote_hosts")
RETIRED_SERVE_HOME_KEYS = ("serve_sessions_claude_home", "serve_sessions_codex_home")

SERVE_CONFIG_ENV = "AGENT_TOOLKIT_SERVE_CONFIG"

"""旧`serve.toml`の場所を指定していた環境変数。統合後は自動移行の移行元を示す。"""

# 旧`serve.toml`の位置（節名とキー）と移行先のキー。
LEGACY_SERVE_KEYS = {
    (None, "host"): "serve_host",
    (None, "port"): "serve_port",
    ("plans", "root"): "serve_plans_root",
    ("plans", "remote_hosts"): "remote_hosts",
    ("sessions", "remote_hosts"): "remote_hosts",
}

CONNECTION_KINDS = ("subscription", "credits", "payg")

"""接続の種別。サブスクリプション、有限のAPIクレジット、従量課金APIの順に並べる。"""

# 接続の認証の所在（種別ごとに1キー）と、役割別の使用順。`payg`は追加料金が発生するため、使用順の初期値へ含めない。
CONNECTION_DEFAULTS = {
    "claude_orchestrator_connections": "subscription,credits",
    "claude_worker_connections": "credits,subscription",
    "codex_credits_provider": "",
    "codex_payg_provider": "",
    "codex_orchestrator_connections": "subscription,credits",
    "codex_worker_connections": "credits,subscription",
}

PATH_KEYS = SERVE_PATH_KEYS
ARRAY_KEYS = SERVE_HOSTS_KEYS

PROVIDER_KEYS = frozenset({"codex_credits_provider", "codex_payg_provider"})

CONNECTION_ORDER_KEYS = frozenset(key for key in CONNECTION_DEFAULTS if key.endswith("_connections"))

WEEKLY_KEY_DEFAULTS = {
    "weekly_meta_auto_start": "true",
    "weekly_research_auto_start": "false",
    "weekly_research_x_api": "false",
}

WEEKLY_FLAG_KEYS = frozenset({"weekly_meta_auto_start", "weekly_research_auto_start", "weekly_research_x_api"})


MUTABLE_KEY_DEFAULTS = {
    **MODEL_DEFAULTS,
    "codex_fast_mode": "false",
    "usage_reset_permissions": "[]",
    **CONNECTION_DEFAULTS,
    "claude_usage_limit_wait": "five-hour",
    "codex_usage_limit_wait": "five-hour",
    "repositories": "{}",
    "session_review_auto_start": "true",
    **WEEKLY_KEY_DEFAULTS,
    # agents_serverが同時に作業させる委譲先の上限。論理CPU数への係数（例: 2.5x）、絶対値、offを受け付ける。
    "agents_concurrency": "2.5x",
    "dotfiles_mode": "full",
    **SERVE_KEY_DEFAULTS,
}

# 旧接続設定キーと移行先。保存値は読込時に移行先へ移し、環境変数とget・setでは移行先を示して拒否する。
RETIRED_CONNECTION_KEYS = {
    "claude_api_env_file": ("設定ディレクトリのclaude-credits.envまたはclaude-payg.env",),
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

CLAUDE_ENV_FILENAMES = {"credits": "claude-credits.env", "payg": "claude-payg.env"}
X_ENV_FILENAME = "x-api.env"
X_MAX_POSTS = 100
X_MAX_USERS = 20

RETIRED_FIXED_KEYS = {
    "claude_credits_env_file": CLAUDE_ENV_FILENAMES["credits"],
    "claude_payg_env_file": CLAUDE_ENV_FILENAMES["payg"],
    "weekly_research_x_env_file": X_ENV_FILENAME,
    "weekly_research_x_max_posts": f"1回あたり{X_MAX_POSTS}投稿",
    "weekly_research_x_max_users": f"1回あたり{X_MAX_USERS}ユーザー",
    "usage_activity_mode": "観測に基づく自動補正",
    "claude_reset_inventory": "`atk config usage --set-claude-reset-inventory`",
}


CLAUDE_RETIRED_CONNECTION_KEYS = (
    "claude_api_env_file",
    "claude_orchestrator_api_only",
    "claude_worker_api_first",
    "claude_api_mode",
)


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
    caller = runtime_environment.current()
    if caller is not None:
        env = caller.environment
        if os.name == "nt":
            base = (
                pathlib.Path(env["LOCALAPPDATA"])
                if env.get("LOCALAPPDATA")
                else runtime_environment.home() / "AppData" / "Local"
            )
        else:
            base = (
                pathlib.Path(env["XDG_CONFIG_HOME"]) if env.get("XDG_CONFIG_HOME") else runtime_environment.home() / ".config"
            )
        return (base if base.is_absolute() else caller.cwd / base) / "agent-toolkit"
    return pathlib.Path(platformdirs.user_config_dir("agent-toolkit", appauthor=False))


def config_file_path() -> pathlib.Path:
    return config_dir() / CONFIG_FILENAME


def process_loop_configuration_path() -> pathlib.Path:
    return config_file_path()


def load_config() -> dict[str, str]:
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
    path = config_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def config_env_name(key: str) -> str:
    return f"{CONFIG_ENV_PREFIX}{key.upper()}"


def config_environment_variable_names() -> tuple[str, ...]:
    keys = (
        *MUTABLE_KEY_DEFAULTS,
        *LEGACY_REMOTE_HOSTS_KEYS,
        *RETIRED_CONNECTION_KEYS,
        *(key for key in RETIRED_FIXED_KEYS if key != "usage_activity_mode"),
        *sorted(RETIRED_MODEL_KEYS),
        "process_loop_repositories",
        "usage_auto_allocation",
    )
    return tuple(config_env_name(key) for key in keys)


def validate_stage_model_candidates(value: str) -> None:
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
    engines = tuple(value.split(","))
    if any(engine not in {"claude", "codex", "opencode"} for engine in engines) or len(set(engines)) != len(engines):
        raise ValueError("受理可能書式: codex・claude・opencodeを重複なしで並べたASCIIカンマ区切り")
    return engines


def parse_agents_concurrency(value: str) -> tuple[str, float] | None:
    text = value.strip()
    if text == "off":
        return None
    if text.endswith("x"):
        factor = float(text[:-1])
        if not math.isfinite(factor) or factor <= 0:
            raise ValueError("係数は正の数で指定する")
        return "factor", factor
    if text.isdecimal() and int(text) >= 1:
        return "absolute", float(int(text))
    raise ValueError("受理可能書式: 論理CPU数への係数（例: 2.5x）、1以上の整数（絶対値）、off（無効化）")


SETTING_CHOICES = {
    "dotfiles_mode": ("full", "minimal"),
    "claude_usage_limit_wait": ("never", "five-hour", "always"),
    "codex_usage_limit_wait": ("never", "five-hour", "always"),
    "codex_fast_mode": ("true", "false"),
    "session_review_auto_start": ("true", "false"),
    **dict.fromkeys(sorted(WEEKLY_FLAG_KEYS), ("true", "false")),
}

_SETTING_DESCRIPTIONS = {
    "execute_model": "実装を担当するモデル候補",
    "review_model": "通常のレビューを担当するモデル候補",
    "review_fast_model": "一括置換後レビューを担当するモデル候補",
    "chores_model": "補助作業を担当するモデル候補",
    "chores_fast_model": "軽量な補助作業を担当するモデル候補",
    "orchestrate_model": "統括を担当するモデル候補",
    "write_model": "文書執筆を担当するモデル候補",
    "codex_fast_mode": "Codexの高速モードを使うか",
    "usage_reset_permissions": "利用枠のresetを許可する対象と期間",
    "codex_credits_provider": "Codexの有限クレジット用provider ID",
    "codex_payg_provider": "Codexの従量課金用provider ID",
    "claude_orchestrator_connections": "Claudeの統括で試す接続順",
    "claude_worker_connections": "Claudeの委譲先で試す接続順",
    "codex_orchestrator_connections": "Codexの統括で試す接続順",
    "codex_worker_connections": "Codexの委譲先で試す接続順",
    "claude_usage_limit_wait": "Claudeの利用上限に達した場合に回復を待つ範囲",
    "codex_usage_limit_wait": "Codexの利用上限に達した場合に回復を待つ範囲",
    "repositories": "処理対象リポジトリと編集可否・検証OS・系統の登録",
    "session_review_auto_start": "会話の終わりに振り返りの候補を自動で処理するか",
    "weekly_meta_auto_start": "日曜14時の週枠でメタ分析を自動で起動するか",
    "weekly_research_auto_start": "日曜14時の週枠で外部情報の調査を自動で起動するか",
    "weekly_research_x_api": "外部情報の調査でX APIの有償取得を使うか",
    "agents_concurrency": "委譲先の同時実行数の上限",
    "dotfiles_mode": "dotfilesの配布範囲",
    "serve_host": "ブラウザーから接続するサーバーの待受アドレス",
    "serve_port": "ブラウザーから接続するサーバーの待受ポート",
    "remote_hosts": "設定・計画・エージェント・セッションのSSH接続先",
    "serve_plans_root": "計画ファイルのローカル保存先の上書き。空ならprivate-notesのplansと~/.claude/plansの両方",
}


def setting_format(key: str) -> str:
    if key in SETTING_CHOICES:
        return ", ".join(SETTING_CHOICES[key])
    if key in MODEL_DEFAULTS:
        standard = "<claude|codex|agy>:<model>[/<effort>]"
        opencode = "" if key == "orchestrate_model" else " または opencode:<provider>/<model>[@<effort>]"
        return f"{standard}{opencode}。候補はASCIIカンマ区切り。effortを省略するとmedium"
    if key in CONNECTION_ORDER_KEYS:
        return f"{', '.join(CONNECTION_KINDS)}から使用順にカンマで区切る"
    if key in PATH_KEYS:
        return "パス1つ、または未指定を表す空文字列"
    if key in PROVIDER_KEYS:
        return "config.tomlのprovider ID 1つ、または空文字列"
    if key in ARRAY_KEYS:
        return "SSHホスト名のJSON文字列配列"
    return {
        "repositories": "リポジトリIDと登録属性のJSONオブジェクト",
        "agents_concurrency": "上限の整数、論理CPU数への倍率（例: 2.5x）、またはoff",
        "serve_host": "空でないホスト名またはIPアドレス",
        "serve_port": "1から65535までの整数",
        "usage_reset_permissions": "id・engine・account・scope・count・from・untilを含むJSON配列。日時はタイムゾーン付き",
    }.get(key, "設定値")


def setting_description(key: str) -> str:
    return _SETTING_DESCRIPTIONS.get(key, key)


def validate_mutable_setting(key: str, value: str) -> None:
    if key in SETTING_CHOICES:
        if value not in SETTING_CHOICES[key]:
            raise ValueError(f"受理可能値: {setting_format(key)}")
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
    elif key == "repositories":
        config_repositories.parse(value)
    elif key == "usage_reset_permissions":
        validate_permissions(value)
    elif key == "agents_concurrency":
        parse_agents_concurrency(value)
    elif key in MODEL_DEFAULTS:
        validate_stage_model_candidates(value)
        if key == "orchestrate_model" and any(candidate.startswith("opencode:") for candidate in value.split(",")):
            raise ValueError("受理可能書式: orchestrate_modelはclaude、codex、agyの候補だけをASCIIカンマ区切りで指定する")
    else:
        validate_stage_model_candidates(value)


@dataclasses.dataclass(frozen=True)
class ConfiguredRepository:
    repo_id: str
    worktree: pathlib.Path
    common_dir: pathlib.Path
    branch: str | None = None
    default_branch: str | None = None

    @property
    def key(self) -> str:
        return target_key(self.repo_id, self.branch)

    @property
    def is_default(self) -> bool:
        return self.branch is None or self.branch == self.default_branch


def target_key(repo_id: str, branch: str | None) -> str:
    return repo_id if branch is None else f"{repo_id}@{branch}"


def resolve_configured_repositories() -> tuple[ConfiguredRepository, ...]:
    try:
        repositories = repository_settings()
        result = []
        for repo_id, item in repositories.items():
            if not item["process_loop"]:
                continue
            location = config_repositories.locate(pathlib.Path(item["path"]))
            if location is None:
                raise ValueError(f"登録先のGit作業ツリーを解決できない: {item['path']}")
            settings = config_repositories.branch_settings(item)
            if settings is None:
                result.append(ConfiguredRepository(repo_id, location.worktree, location.common_dir))
                continue
            result.extend(
                ConfiguredRepository(repo_id, location.worktree, location.common_dir, branch, settings.default_branch)
                for branch in settings.branches
            )
        return tuple(result)
    except (OSError, UnicodeError, ValueError) as error:
        raise _next_action.ActionableError(
            str(error),
            next_action="`atk config repos list`で登録を確認し、`atk config repos set REPO --path PATH`で直して再実行する",
        ) from error


def repository_settings(*, check_paths: bool = True) -> dict[str, dict[str, Any]]:
    check_repository_environment()
    data = load_config_strict()
    # repositoryを読む主体にはモデル移行を依存させず、同じ初回読取で旧repository登録だけを移す。
    # config CLIの既存の全設定移行もconfig_repositories.migrateを使い、移行規則は一か所に保つ。
    migrated = config_repositories.migrate(data)
    if migrated != data:
        save_config(migrated)
    data = migrated
    value = runtime_environment.environment().get(config_env_name("repositories")) or data.get("repositories", "{}")
    if not isinstance(value, str):
        raise ValueError("repositoriesの保存値はJSON文字列で指定する")
    return config_repositories.parse(value, check_paths=check_paths)


def repository_branch_settings() -> dict[str, config_repositories.BranchSettings]:
    return {
        repo_id: settings
        for repo_id, item in repository_settings(check_paths=False).items()
        if (settings := config_repositories.branch_settings(item)) is not None
    }


def check_repository_environment() -> None:
    if runtime_environment.environment().get(config_env_name("process_loop_repositories")):
        raise _next_action.ActionableError(
            "廃止した環境変数AGENT_TOOLKIT_CONFIG_PROCESS_LOOP_REPOSITORIESが設定されている",
            next_action="旧環境変数を解除し、`atk config repos add REPO --edit allow --path PATH --process-loop`で登録する",
        )


def parse_remote_hosts(value: str) -> tuple[str, ...]:
    try:
        hosts = json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError('SSH接続先名のJSON文字列配列で指定する（例: ["remote-host"]、空配列も可）') from error
    if not isinstance(hosts, list) or any(not isinstance(host, str) or not host.strip() for host in hosts):
        raise ValueError("空でないSSH接続先名のJSON文字列配列で指定する（空配列も可）")
    return tuple(hosts)


def parse_string_list(value: str) -> tuple[str, ...]:
    try:
        items = json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError('空でない文字列のJSON配列で指定する（例: ["value"]、空配列も可）') from error
    if not isinstance(items, list) or any(not isinstance(item, str) or not item.strip() for item in items):
        raise ValueError("空でない文字列のJSON配列で指定する（空配列も可）")
    return tuple(dict.fromkeys(item.strip() for item in items))


def parse_codex_provider_candidates(value: str) -> tuple[str, ...]:
    """旧設定`codex_model_providers`の接続先IDを空白除去・順序保持で読み、同じIDは一度だけ返す。"""
    if not value.strip():
        return ()
    providers = tuple(part.strip() for part in value.split(","))
    if any(not provider or any(char.isspace() for char in provider) for provider in providers):
        raise ValueError("受理可能書式: 空文字列、またはprovider IDのASCIIカンマ区切り（空要素は不可）")
    return tuple(dict.fromkeys(providers))


def parse_connection_order(value: str) -> tuple[str, ...]:
    kinds = tuple(part.strip() for part in value.split(","))
    if not value.strip() or any(kind not in CONNECTION_KINDS for kind in kinds) or len(set(kinds)) != len(kinds):
        raise ValueError("受理可能書式: subscription・credits・paygを重複なしで1つ以上並べたASCIIカンマ区切り")
    return kinds


def build_connection_order_key(engine: str, role: str) -> str:
    if engine not in {"claude", "codex"}:
        raise KeyError(engine)
    return f"{engine}_{'orchestrator' if role == 'orchestrator' else 'worker'}_connections"


def connection_order_key(engine: str, role: str) -> str:
    return build_connection_order_key(engine, role)


def mutable_setting_default(key: str) -> str:
    return MUTABLE_KEY_DEFAULTS[key]


def raw_mutable_setting(key: str, *, fallback: str | None = None) -> str:
    """変更可能な設定の検証前の値を、環境変数、保存値、初期値の順に解決して返す。未知のキーは`KeyError`を送出する。"""
    default = mutable_setting_default(key)
    explicit = runtime_environment.environment().get(config_env_name(key), "")
    if explicit:
        return explicit
    return load_config().get(key, default if fallback is None else fallback)


def normalize_setting(key: str, value: str) -> str:
    """検証済みの値を保存・表示する正規形へ揃える。"""
    if key in PATH_KEYS:
        return config_repositories.normalize_path(value)
    if key == "repositories":
        return json.dumps(config_repositories.parse(value), ensure_ascii=False)
    if key in CONNECTION_ORDER_KEYS:
        return ",".join(parse_connection_order(value))
    return value
