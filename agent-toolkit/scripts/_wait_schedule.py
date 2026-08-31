"""公開情報からプロンプトキャッシュTTLを判定する。

TTL判定は、委譲待機用cron式と質問自動継続タイムアウトが共有する正本である。
"""

import json
import os
import subprocess

# キャッシュTTLごとの再確認間隔。TTLが満了する前に必ず再確認するため、間隔はTTLより短く取る。
_SCHEDULE_FOR_5M_TTL = "*/3 * * * *"
_SCHEDULE_FOR_1H_TTL = "*/30 * * * *"
_BUCKET_TTL_ENV = {
    "main": "CLAUDE_CODE_PROMPT_CACHE_TTL",
    "subagent": "CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL",
}
_CREDENTIAL_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL")
_PROVIDER_ENV = (
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_MANTLE",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
    "CLAUDE_CODE_USE_ANTHROPIC_AWS",
)


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
