"""Claudeの認証検出・環境隔離・起動設定を、CLIとSDKで共有する。"""

from __future__ import annotations

import dataclasses
import json
import os
import pathlib
import re
import shlex
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Mapping

from agent_toolkit._common import claude_settings_sources, host_homes
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


@dataclasses.dataclass(frozen=True)
class Connection:
    """登録簿へ保存できる接続種別と、API環境ファイルの所在。"""

    kind: str
    api_env_file: str | None = None
    isolation_env_file: str | None = None


class SettingsFile(str):
    """この起動が所有する合成設定のパス。SDKへは文字列として渡す。"""

    def cleanup(self) -> None:
        """他の起動の設定を触らず、自身のファイルを回収する。"""
        pathlib.Path(self).unlink(missing_ok=True)

    def __enter__(self) -> SettingsFile:
        return self

    def __exit__(self, *_exception: object) -> None:
        self.cleanup()


def read_api_env_file(path: str) -> dict[str, str]:
    """限定形式のenvファイルを毎起動読み、変数展開をせず値を返す。"""
    values: dict[str, str] = {}
    for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export ")
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) is None:
            raise ValueError("API envファイルは1行1つのKEY=VALUEで指定する")
        value = value.strip()
        if value.startswith(("'", '"')) and (len(value) < 2 or value[-1] != value[0]):
            raise ValueError("API envファイルの値を囲む引用符を閉じる")
        if len(value) >= 2 and value[0] in {"'", '"'} and value[-1] == value[0]:
            value = value[1:-1]
        values[key] = value
    return values


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
) -> str:
    """親のJSONの意味を保ち、委譲先のenvを最後に適用する。

    親の設定がファイルの場合と`force_file`の指定時は、所有者だけが読める起動ごとのファイルへ書き、そのパスを返す。
    親の設定を読めない場合、JSONとして不正な場合およびobjectでない場合は継承せず、envだけを渡す。
    """
    from_file = parent is not None and not parent.lstrip().startswith("{")
    try:
        raw = pathlib.Path(parent).read_text(encoding="utf-8") if from_file and parent is not None else parent or "{}"
        value = json.loads(raw)
    except OSError, ValueError, UnicodeError:
        value = {}
    settings = value if isinstance(value, dict) else {}
    inherited = settings.get("env")
    if isolate_auth:
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
    """対話起動のAPIキー確認を要しない、ファイルからのキー取得コマンドを返す。"""
    argv = [
        sys.executable,
        "-c",
        (
            "import sys; from agent_toolkit._common.claude_connection import read_api_env_file; "
            "print(read_api_env_file(sys.argv[1]).get('ANTHROPIC_API_KEY', ''))"
        ),
        path,
    ]
    return subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)


def cleanup_settings(options: object) -> None:
    """SDK設定が保持する、この起動自身の合成ファイルだけを回収する。"""
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
    environment = os.environ if env is None else env
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


def isolation_keys(connection: Connection) -> frozenset[str]:
    """選択した接続で親から継承しない変数名を返す。"""
    if connection.kind == "inherit":
        return frozenset()
    path = connection.isolation_env_file or connection.api_env_file
    keys = AUTH_ENV | (read_api_env_file(path).keys() if path else frozenset())
    if connection.kind == "api":
        keys |= {"CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_OAUTH_REFRESH_TOKEN"}
    return frozenset(keys)


def connection_overrides(connection: Connection) -> dict[str, str]:
    """選択したAPI接続が明示する値だけを返す。"""
    if connection.kind == "api" and connection.api_env_file:
        return {
            key: value
            for key, value in read_api_env_file(connection.api_env_file).items()
            if key not in {"CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_OAUTH_REFRESH_TOKEN"}
        }
    return {}


def connection_environment(env: Mapping[str, str], connection: Connection) -> dict[str, str]:
    """親の隔離対象を除去して、選択したAPIファイルだけを適用する。"""
    result = clean_environment(env, isolation_keys=isolation_keys(connection))
    result.update(connection_overrides(connection))
    return result
