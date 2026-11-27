"""Claudeの認証検出・環境隔離・起動設定を、CLIとSDKで共有する。"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import pathlib
import shlex
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Iterable, Mapping

from agent_toolkit._common import claude_settings_sources, env_values, host_homes, runtime_environment
from agent_toolkit._common.state_paths import state_dir

AUTH_ENV = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_CUSTOM_HEADERS",
        "ANTHROPIC_PROFILE",
        "ANTHROPIC_FEDERATION_RULE_ID",
        "ANTHROPIC_ORGANIZATION_ID",
        "CLAUDE_CODE_API_KEY_FILE_DESCRIPTOR",
        "CLAUDE_CODE_API_KEY_HELPER_TTL_MS",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "CLAUDE_CODE_USE_FOUNDRY",
        "ANTHROPIC_FOUNDRY_API_KEY",
        "ANTHROPIC_FOUNDRY_RESOURCE",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_PROFILE",
        "AWS_REGION",
        "AWS_DEFAULT_REGION",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL",
        "ANTHROPIC_DEFAULT_SONNET_MODEL",
        "ANTHROPIC_DEFAULT_OPUS_MODEL",
        "ANTHROPIC_MODEL",
    }
)


API_KINDS = frozenset({"credits", "payg"})
"""API環境ファイルで接続する種別（有限のAPIクレジットと従量課金API）。"""

LEGACY_API_KIND = "api"
"""種別を区別する前の版が登録簿へ保存したAPI接続の種別。再開時に`credits`か`payg`へ読み替える。"""


@dataclasses.dataclass(frozen=True)
class Connection:
    kind: str
    api_env_file: str | None = None
    isolation_env_files: tuple[str, ...] = ()


class SettingsFile(str):
    def cleanup(self) -> None:
        pathlib.Path(self).unlink(missing_ok=True)

    def __enter__(self) -> SettingsFile:
        return self

    def __exit__(self, *_exception: object) -> None:
        self.cleanup()


def read_api_env_file(path: str) -> dict[str, str]:
    return env_values.read(path)


def merge_settings(
    parent: str | None,
    env: dict[str, str],
    root: str | None,
    *,
    force_file: bool = False,
    key_helper_command: str | None = None,
    isolate_auth: bool = True,
    isolation_keys: frozenset[str] = AUTH_ENV,  # pylint: disable=redefined-outer-name  # 公開キーワード名を保持する。
    cwd: str | pathlib.Path | None = None,
    project_settings: bool = True,
    drop_parent_env: frozenset[str] = frozenset(),
    startup_overrides: Mapping[str, bool] | None = None,
) -> str:
    from_file = parent is not None and not parent.lstrip().startswith("{")
    try:
        raw = pathlib.Path(parent).read_text(encoding="utf-8") if from_file and parent is not None else parent or "{}"
        value = json.loads(raw)
    except OSError, ValueError, UnicodeError:
        value = {}
    settings = value if isinstance(value, dict) else {}
    if startup_overrides is not None:
        settings.update(startup_overrides)
    inherited = settings.get("env")
    if isolate_auth and (
        "apiKeyHelper" in settings or "apiKeyHelper" in claude_settings_sources.setting_keys(cwd, project=project_settings)
    ):
        # 継承する設定のhelperだけを空値で打ち消す。定義が無いのに空値を渡すと、Claude Codeは
        # helperの指定ありと扱い、Code Modeなどhelperと併用できない機能を無効にする（2.1.326で観測）。
        settings["apiKeyHelper"] = ""
    if key_helper_command is not None:
        settings["apiKeyHelper"] = key_helper_command
    settings["env"] = {
        **(
            {
                key: value
                for key, value in inherited.items()
                if key not in drop_parent_env and (not isolate_auth or key not in isolation_keys)
            }
            if isinstance(inherited, dict)
            else {}
        ),
        **(
            dict.fromkeys(isolation_keys & claude_settings_sources.environment_keys(cwd, project=project_settings), "")
            if isolate_auth
            else {}
        ),
        **env,
    }
    content = json.dumps(settings, ensure_ascii=False)
    if not from_file and not force_file:
        return content
    directory = state_dir() / "agents-server" / (root or f"settings-{uuid.uuid4()}") / "claude-settings"
    directory.mkdir(parents=True, exist_ok=True)
    fd, path = tempfile.mkstemp(prefix="settings-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
    except BaseException:
        pathlib.Path(path).unlink(missing_ok=True)
        raise
    return SettingsFile(path)


def api_key_helper(path: str) -> str:
    root = pathlib.Path(__file__).resolve().parents[2]
    argv = [
        sys.executable,
        "-c",
        (
            f"import sys; sys.path.insert(0, {str(root)!r}); "
            "from agent_toolkit._common.claude_connection import read_api_env_file; "
            "print(read_api_env_file(sys.argv[1]).get('ANTHROPIC_API_KEY', ''))"
        ),
        path,
    ]
    return subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)


def cleanup_settings(options: object) -> None:
    settings = getattr(options, "settings", None)
    if isinstance(settings, SettingsFile):
        settings.cleanup()


def subscription_available(
    env: Mapping[str, str] | None = None,
    *,
    platform: str = sys.platform,
    isolation_keys: frozenset[str] = AUTH_ENV,  # pylint: disable=redefined-outer-name  # 公開キーワード名を保持する。
) -> bool:
    """OAuth環境またはClaude CodeのOAuth資格情報の存在を値の公開なしに判定する。"""
    environment = runtime_environment.environment() if env is None else env
    if environment.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return True
    if platform == "darwin":
        # CLI自身のauth statusを使い、CLAUDE_CONFIG_DIR別のKeychainエントリをCLIに解決させる。
        # 公開仕様: https://code.claude.com/docs/en/cli-reference#cli-commands
        try:
            result = subprocess.run(
                ["claude", "--settings", merge_settings(None, {}, None, isolation_keys=isolation_keys), "auth", "status"],
                env=clean_environment(environment, isolation_keys=isolation_keys),
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
                errors="replace",
                timeout=10,
            )
            status = json.loads(result.stdout)
        except OSError, ValueError, subprocess.TimeoutExpired:
            return False
        return result.returncode == 0 and isinstance(status, dict) and status.get("authMethod") in {"claude.ai", "oauth_token"}
    path = host_homes.claude_config_dir(environment) / ".credentials.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError, UnicodeError:
        return False
    oauth = data.get("claudeAiOauth") if isinstance(data, dict) else None
    return isinstance(oauth, dict) and bool(oauth.get("accessToken") or oauth.get("refreshToken"))


def clean_environment(
    env: Mapping[str, str],
    *,
    isolation_keys: frozenset[str] = AUTH_ENV,  # pylint: disable=redefined-outer-name  # 公開キーワード名を保持する。
) -> dict[str, str]:
    """親のAPI接続変数を除去する。OAuthはサブスクリプションの認証として維持する。"""
    return {key: value for key, value in env.items() if key not in isolation_keys}


def api_environment_keys(paths: Iterable[str]) -> frozenset[str]:
    keys = set(AUTH_ENV)
    for path in paths:
        with contextlib.suppress(OSError, ValueError, UnicodeError):
            keys.update(read_api_env_file(path))
    return frozenset(keys)


def isolation_keys(connection: Connection) -> frozenset[str]:
    if connection.kind == "inherit":
        return frozenset()
    paths = (*connection.isolation_env_files, *((connection.api_env_file,) if connection.api_env_file else ()))
    keys = api_environment_keys(paths)
    if connection.kind in API_KINDS:
        keys |= {"CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_OAUTH_REFRESH_TOKEN"}
    return frozenset(keys)


def connection_overrides(connection: Connection) -> dict[str, str]:
    if connection.kind in API_KINDS and connection.api_env_file:
        return {
            key: value
            for key, value in read_api_env_file(connection.api_env_file).items()
            if key not in {"CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_OAUTH_REFRESH_TOKEN"}
        }
    return {}


def connection_environment(env: Mapping[str, str], connection: Connection) -> dict[str, str]:
    result = clean_environment(env, isolation_keys=isolation_keys(connection))
    result.update(connection_overrides(connection))
    return result
