"""`_wait_schedule`のTTL判定を検証する。"""

import json
import subprocess
from typing import Any

import _wait_schedule
import pytest

_SCHEDULE_FOR_5M_TTL = "*/3 * * * *"
_SCHEDULE_FOR_1H_TTL = "*/30 * * * *"


def _fail_if_auth_status_is_called(*args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
    """認証状態を参照しない分岐で呼び出しが発生した場合に失敗させる。"""
    del args
    del kwargs
    raise AssertionError("claude auth statusは呼び出さない分岐で実行された")


@pytest.mark.parametrize("bucket", ["main", "subagent"])
def test_force_five_minute_overrides_other_inputs(monkeypatch: pytest.MonkeyPatch, bucket: str) -> None:
    """強制5分指定はbucket別指定と強制1時間指定より優先する。"""
    monkeypatch.setenv("FORCE_PROMPT_CACHING_5M", "1")
    monkeypatch.setenv("CLAUDE_CODE_PROMPT_CACHE_TTL", "1h")
    monkeypatch.setenv("CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL", "1h")
    monkeypatch.setenv("ENABLE_PROMPT_CACHING_1H", "1")
    monkeypatch.setattr(_wait_schedule.subprocess, "run", _fail_if_auth_status_is_called)

    assert _wait_schedule.get_schedule(bucket) == _SCHEDULE_FOR_5M_TTL


@pytest.mark.parametrize(
    ("bucket", "environment_name", "value", "expected"),
    [
        ("main", "CLAUDE_CODE_PROMPT_CACHE_TTL", "5m", _SCHEDULE_FOR_5M_TTL),
        ("main", "CLAUDE_CODE_PROMPT_CACHE_TTL", "1h", _SCHEDULE_FOR_1H_TTL),
        ("subagent", "CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL", "5m", _SCHEDULE_FOR_5M_TTL),
        ("subagent", "CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL", "1h", _SCHEDULE_FOR_1H_TTL),
    ],
)
def test_bucket_ttl_override_is_selected(
    monkeypatch: pytest.MonkeyPatch,
    bucket: str,
    environment_name: str,
    value: str,
    expected: str,
) -> None:
    """bucket別の公開TTL上書きを対応するcron式へ変換する。"""
    monkeypatch.setenv(environment_name, value)
    monkeypatch.setattr(_wait_schedule.subprocess, "run", _fail_if_auth_status_is_called)

    assert _wait_schedule.get_schedule(bucket) == expected


def test_bucket_ttl_override_precedes_common_one_hour(monkeypatch: pytest.MonkeyPatch) -> None:
    """5分のbucket別指定は共通の1時間指定より優先する。"""
    monkeypatch.setenv("CLAUDE_CODE_PROMPT_CACHE_TTL", "5m")
    monkeypatch.setenv("ENABLE_PROMPT_CACHING_1H", "1")
    monkeypatch.setattr(_wait_schedule.subprocess, "run", _fail_if_auth_status_is_called)

    assert _wait_schedule.get_schedule("main") == _SCHEDULE_FOR_5M_TTL


@pytest.mark.parametrize(
    ("bucket", "environment_name"),
    [
        ("main", "CLAUDE_CODE_PROMPT_CACHE_TTL"),
        ("subagent", "CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL"),
    ],
)
def test_invalid_bucket_ttl_is_ignored(monkeypatch: pytest.MonkeyPatch, bucket: str, environment_name: str) -> None:
    """bucket別TTLの未知値を無視し、下位の共通設定を評価する。"""
    monkeypatch.setenv(environment_name, "10m")
    monkeypatch.setenv("ENABLE_PROMPT_CACHING_1H", "1")
    monkeypatch.setattr(_wait_schedule.subprocess, "run", _fail_if_auth_status_is_called)

    assert _wait_schedule.get_schedule(bucket) == _SCHEDULE_FOR_1H_TTL


@pytest.mark.parametrize("bucket", ["main", "subagent"])
def test_common_one_hour_override_applies_to_both_buckets(monkeypatch: pytest.MonkeyPatch, bucket: str) -> None:
    """共通の1時間指定は両bucketへ適用する。"""
    monkeypatch.setenv("ENABLE_PROMPT_CACHING_1H", "1")
    monkeypatch.setattr(_wait_schedule.subprocess, "run", _fail_if_auth_status_is_called)

    assert _wait_schedule.get_schedule(bucket) == _SCHEDULE_FOR_1H_TTL


@pytest.mark.parametrize("bucket", ["main", "subagent"])
def test_environment_scrub_uses_five_minute_schedule(monkeypatch: pytest.MonkeyPatch, bucket: str) -> None:
    """subprocess環境のscrub指定は5分系へ分類する。"""
    monkeypatch.setenv("CLAUDE_CODE_SUBPROCESS_ENV_SCRUB", "1")
    monkeypatch.setattr(_wait_schedule.subprocess, "run", _fail_if_auth_status_is_called)

    assert _wait_schedule.get_schedule(bucket) == _SCHEDULE_FOR_5M_TTL


def test_subagent_default_uses_five_minute_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    """subagentの既定TTLは認証状態を参照せず5分系へ分類する。"""
    monkeypatch.setattr(_wait_schedule.subprocess, "run", _fail_if_auth_status_is_called)

    assert _wait_schedule.get_schedule("subagent") == _SCHEDULE_FOR_5M_TTL


@pytest.mark.parametrize(
    ("environment_name", "value"),
    [
        ("ANTHROPIC_API_KEY", "api-key-secret"),
        ("ANTHROPIC_AUTH_TOKEN", "auth-token-secret"),
        ("ANTHROPIC_BASE_URL", "https://example.invalid"),
        ("CLAUDE_CODE_USE_BEDROCK", "1"),
        ("CLAUDE_CODE_USE_MANTLE", "1"),
        ("CLAUDE_CODE_USE_VERTEX", "1"),
        ("CLAUDE_CODE_USE_FOUNDRY", "1"),
        ("CLAUDE_CODE_USE_ANTHROPIC_AWS", "1"),
    ],
)
def test_public_api_and_provider_inputs_use_five_minute_schedule(
    monkeypatch: pytest.MonkeyPatch,
    environment_name: str,
    value: str,
) -> None:
    """API credential・custom endpoint・各provider指定を5分系へ分類する。"""
    monkeypatch.setenv(environment_name, value)
    monkeypatch.setattr(_wait_schedule.subprocess, "run", _fail_if_auth_status_is_called)

    assert _wait_schedule.get_schedule("main") == _SCHEDULE_FOR_5M_TTL


def test_valid_subscription_status_uses_one_hour_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    """正常なClaude.aiサブスクリプション応答を1時間系へ分類する。"""
    calls: list[dict[str, Any]] = []

    def fake_run(cmd: list[str], *args: object, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        del args
        calls.append({"cmd": list(cmd), "kwargs": dict(kwargs)})
        return subprocess.CompletedProcess(
            cmd,
            returncode=0,
            stdout=json.dumps(
                {
                    "loggedIn": True,
                    "authMethod": "claude.ai",
                    "subscriptionType": "max",
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(_wait_schedule.subprocess, "run", fake_run)

    assert _wait_schedule.get_schedule("main") == _SCHEDULE_FOR_1H_TTL
    assert calls == [
        {
            "cmd": ["claude", "auth", "status"],
            "kwargs": {
                "capture_output": True,
                "check": False,
                "shell": False,
                "text": True,
                "timeout": 5,
            },
        }
    ]


@pytest.mark.parametrize(
    "status",
    [
        {
            "loggedIn": True,
            "authMethod": "third_party",
            "apiProvider": "anthropicAws",
            "analyticsDisabled": True,
            "projectsDirectory": "/tmp/claude/projects",
        },
        {
            "loggedIn": True,
            "authMethod": "third_party",
            "apiProvider": "bedrock",
            "analyticsDisabled": True,
            "projectsDirectory": "/tmp/claude/projects",
        },
        {
            "loggedIn": True,
            "authMethod": "third_party",
            "apiProvider": "future-provider",
            "analyticsDisabled": True,
        },
        {"loggedIn": False, "authMethod": "claude.ai", "subscriptionType": "max"},
        {"loggedIn": True, "authMethod": "other", "subscriptionType": "max"},
        {"loggedIn": True, "authMethod": "claude.ai", "subscriptionType": ""},
        {"loggedIn": True, "authMethod": "claude.ai"},
    ],
)
def test_non_subscription_status_uses_five_minute_schedule(
    monkeypatch: pytest.MonkeyPatch,
    status: dict[str, object],
) -> None:
    """third-party・未知provider・ログアウト・必須field欠損を5分系へ分類する。"""

    def fake_run(cmd: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
        del args
        del kwargs
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=json.dumps(status), stderr="")

    monkeypatch.setattr(_wait_schedule.subprocess, "run", fake_run)

    assert _wait_schedule.get_schedule("main") == _SCHEDULE_FOR_5M_TTL


@pytest.mark.parametrize("stdout", ["not-json", "null", "[]"])
def test_invalid_auth_json_uses_five_minute_schedule(monkeypatch: pytest.MonkeyPatch, stdout: str) -> None:
    """不正JSONまたはobject以外の認証応答を5分系へ分類する。"""

    def fake_run(cmd: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
        del args
        del kwargs
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr="diagnostic")

    monkeypatch.setattr(_wait_schedule.subprocess, "run", fake_run)

    assert _wait_schedule.get_schedule("main") == _SCHEDULE_FOR_5M_TTL


def test_auth_command_failure_uses_five_minute_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    """認証状態コマンドの非0終了を5分系へ分類する。"""

    def fake_run(cmd: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
        del args
        del kwargs
        return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="auth failed")

    monkeypatch.setattr(_wait_schedule.subprocess, "run", fake_run)

    assert _wait_schedule.get_schedule("main") == _SCHEDULE_FOR_5M_TTL


@pytest.mark.parametrize(
    "exception",
    [
        FileNotFoundError("claude"),
        UnicodeDecodeError("utf-8", b"\\xff", 0, 1, "invalid start byte"),
        subprocess.TimeoutExpired(["claude", "auth", "status"], 5),
    ],
)
def test_auth_command_unavailable_or_timed_out_uses_five_minute_schedule(
    monkeypatch: pytest.MonkeyPatch,
    exception: BaseException,
) -> None:
    """認証状態コマンドの不在またはtimeoutを5分系へ分類する。"""

    def fake_run(cmd: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
        del cmd
        del args
        del kwargs
        raise exception

    monkeypatch.setattr(_wait_schedule.subprocess, "run", fake_run)

    assert _wait_schedule.get_schedule("main") == _SCHEDULE_FOR_5M_TTL


def test_rejects_unknown_request_bucket() -> None:
    """未対応のrequest bucketを拒否する。"""
    with pytest.raises(ValueError, match="未対応のrequest bucket"):
        _wait_schedule.get_schedule("worker")
