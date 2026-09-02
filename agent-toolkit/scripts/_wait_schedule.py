"""公開情報からプロンプトキャッシュTTLを判定する。

TTL判定は、委譲待機用cron式と質問自動継続タイムアウトが共有する正本である。

Claude Codeはプロンプトキャッシュ保持期間を、`FORCE_PROMPT_CACHING_5M`、bucket別の環境変数、
bucket別の設定、サブエージェント定義のfrontmatterの`cacheTtl`、`ENABLE_PROMPT_CACHING_1H`、
bucket別の既定の順に評価し、最初に一致した指定を採用する。設定と環境変数はv2.1.242以降が受理し、
値は`5m`と`1h`だけを受理する。典拠は公式資料<https://code.claude.com/docs/en/prompt-caching.md>の
「Choose the TTL yourself」節と<https://code.claude.com/docs/en/settings-reference.md>の当該設定の節
（いずれも2026年9月2日取得）とする。再検証は同資料の当該節を再取得して順序と受理値を照合する。

本モジュールが読む設定はユーザー設定ファイル`~/.claude/settings.json`に限る。
プロジェクト設定と`--settings`の指定は、判定を要求する主体の起動条件から確定できないため読まない。
サブエージェント定義のfrontmatterも、判定時点では対象の定義が定まらないため読まない。
"""

import json
import os
import pathlib
import subprocess

import pytilpack.jsonc

# キャッシュTTLごとの再確認間隔。TTLが満了する前に必ず再確認するため、間隔はTTLより短く取る。
_SCHEDULE_FOR_5M_TTL = "*/3 * * * *"
_SCHEDULE_FOR_1H_TTL = "*/30 * * * *"
_BUCKET_TTL_ENV = {
    "main": "CLAUDE_CODE_PROMPT_CACHE_TTL",
    "subagent": "CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL",
}
_BUCKET_TTL_SETTING = {
    "main": "promptCacheTtl",
    "subagent": "subagentPromptCacheTtl",
}
_ACCEPTED_TTL_VALUES = ("5m", "1h")
_CREDENTIAL_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL")
_PROVIDER_ENV = (
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_MANTLE",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
    "CLAUDE_CODE_USE_ANTHROPIC_AWS",
)


def _user_settings_ttl(request_bucket: str) -> str | None:
    """ユーザー設定ファイルのbucket別TTL指定を返す。

    ファイルの不在、読み取り失敗、解析失敗及び受理しない値では`None`を返し、後続の判定へ委ねる。
    設定ファイルはコメント付きで書かれる場合があるためJSONCとして解析する。
    """
    path = pathlib.Path.home() / ".claude" / "settings.json"
    try:
        settings = pytilpack.jsonc.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    if not isinstance(settings, dict):
        return None
    value = settings.get(_BUCKET_TTL_SETTING[request_bucket])
    if isinstance(value, str) and value in _ACCEPTED_TTL_VALUES:
        return value
    return None


def _has_valid_subscription_status() -> bool:
    """`claude auth status`がメイン会話のサブスクリプションを示すか判定する。"""
    try:
        result = subprocess.run(  # noqa: S603
            ["claude", "auth", "status"],
            capture_output=True,
            check=False,
            shell=False,
            text=True,
            timeout=5,
        )
    except (OSError, UnicodeError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0 or not isinstance(result.stdout, str):
        return False
    try:
        status = json.loads(result.stdout)
    except json.JSONDecodeError:
        return False
    if not isinstance(status, dict):
        return False
    return (
        status.get("loggedIn") is True
        and status.get("authMethod") == "claude.ai"
        and isinstance(status.get("subscriptionType"), str)
        and bool(status["subscriptionType"])
    )


def get_prompt_cache_ttl(request_bucket: str) -> str:
    """Request bucketと公開情報からプロンプトキャッシュTTLを返す。"""
    if request_bucket not in _BUCKET_TTL_ENV:
        raise ValueError(f"未対応のrequest bucket: {request_bucket}")

    if os.environ.get("FORCE_PROMPT_CACHING_5M") == "1":
        return "5m"

    bucket_ttl = os.environ.get(_BUCKET_TTL_ENV[request_bucket])
    if bucket_ttl == "5m":
        return "5m"
    if bucket_ttl == "1h":
        return "1h"

    settings_ttl = _user_settings_ttl(request_bucket)
    if settings_ttl is not None:
        return settings_ttl

    if os.environ.get("ENABLE_PROMPT_CACHING_1H") == "1":
        return "1h"
    if os.environ.get("CLAUDE_CODE_SUBPROCESS_ENV_SCRUB") == "1":
        return "5m"
    if request_bucket == "subagent":
        return "5m"
    if request_bucket == "main" and any(os.environ.get(name) for name in _CREDENTIAL_ENV):
        return "5m"
    if request_bucket == "main" and any(os.environ.get(name) == "1" for name in _PROVIDER_ENV):
        return "5m"
    if _has_valid_subscription_status():
        return "1h"
    return "5m"


def get_schedule(request_bucket: str) -> str:
    """プロンプトキャッシュTTLを委譲待機用のcron式へ変換する。"""
    if get_prompt_cache_ttl(request_bucket) == "1h":
        return _SCHEDULE_FOR_1H_TTL
    return _SCHEDULE_FOR_5M_TTL
