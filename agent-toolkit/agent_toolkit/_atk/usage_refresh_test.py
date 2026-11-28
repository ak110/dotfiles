"""公式応答の単位・欠損・対象の違いを観測値へ保つ。"""

import hashlib
import pathlib
from types import SimpleNamespace

import pytest

from agent_toolkit._atk import config, config_usage, usage_refresh
from agent_toolkit._atk.usage_refresh import _ActivityClock
from agent_toolkit._common import state_paths, usage_observation, usage_settings


@pytest.fixture(autouse=True)
def isolated_observations(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(usage_observation, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(usage_refresh.time, "time", lambda: 100.0)
    monkeypatch.setattr(usage_refresh, "_CLOCK", _ActivityClock())
    monkeypatch.setattr(usage_refresh, "_ACTIVE", {})
    monkeypatch.setattr(usage_refresh, "_SECONDS", {})
    config.update_settings({"action": "set", "key": "codex_auto_reset", "value": "true"})


def test_usage_report_percent_and_extra_spend_are_distinct() -> None:
    usage_refresh.record_claude_report(
        {
            "rate_limits": {
                "limits": [
                    {
                        "kind": "weekly_all",
                        "percent": 75,
                        "resets_at": "1970-01-01T01:00:00+00:00",
                        "group": "weekly",
                        "severity": "warning",
                        "is_active": False,
                    }
                ],
                "extra_usage": {
                    "is_enabled": True,
                    "used_credits": 200,
                    "monthly_limit": 1000,
                    "currency": "USD",
                    "utilization": 20,
                },
            }
        }
    )
    observed = usage_observation.snapshot()
    assert observed["windows"][0]["used_percent"] == 75
    assert observed["windows"][0]["resets_at"] == 3600
    assert observed["windows"][0]["group"] == "weekly"
    assert observed["windows"][0]["severity"] == "warning"
    assert observed["windows"][0]["is_active"] is False
    assert usage_settings.read_metadata("claude")["extra_usage_spend"] == 200
    assert usage_settings.read_metadata("claude")["extra_usage_utilization"] == 20


def test_sdk_fraction_is_converted_without_guessing_missing_value(monkeypatch: pytest.MonkeyPatch) -> None:
    usage_refresh.record_claude_event({"rateLimitType": "five_hour", "utilization": 0.75, "resetsAt": 3600})
    assert usage_observation.snapshot()["windows"][0]["used_percent"] == 75
    monkeypatch.setattr(usage_refresh.time, "time", lambda: 101.0)
    usage_refresh.record_claude_event({"rateLimitType": "five_hour", "utilization": None, "resetsAt": 3600})
    assert usage_observation.snapshot()["windows"][0]["used_percent"] is None


def test_codex_keeps_multiple_limit_ids_and_account() -> None:
    usage_refresh.record_codex(
        {
            "accountId": "workspace",
            "rateLimitsByLimitId": {
                "codex": {"primary": {"usedPercent": 20, "resetsAt": 1000}},
                "other": {"secondary": {"usedPercent": None, "resetsAt": None}},
            },
        }
    )
    windows = usage_observation.snapshot()["windows"]
    assert {window["window_id"] for window in windows} == {"codex:primary", "other:secondary"}
    assert {window["account"] for window in windows} == {"workspace"}
    assert next(window for window in windows if window["window_id"] == "other:secondary")["used_percent"] is None


def test_codex_notification_retains_verified_account_and_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    auth = {"account": {"email": "user@example.test"}, "workspaceRouting": {"chatgptAccountId": "workspace"}}
    usage_refresh.record_codex(
        {"rateLimits": {"primary": {"usedPercent": 20, "resetsAt": 1000}}},
        account=usage_refresh.codex_account(auth),
        contract="plan",
    )
    monkeypatch.setattr(usage_refresh.time, "time", lambda: 101.0)
    usage_refresh.record_codex({"rateLimits": {"primary": {"usedPercent": 21, "resetsAt": 1000}}})
    windows = usage_observation.snapshot()["current_windows"]
    assert {(window["account"], window["contract"]) for window in windows} == {("workspace", "plan")}


def test_interleaved_windows_use_their_own_activity_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [0.0]
    monkeypatch.setattr(usage_refresh.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(usage_refresh.time, "time", lambda: clock[0] + 100)
    monkeypatch.setattr(usage_refresh, "_CLOCK", _ActivityClock(tick=0, checked_at=0, enabled=True))
    session = SimpleNamespace(
        session_id="active",
        engine="codex",
        model="model",
        effort="low",
        fast_mode=False,
        claude_connection=None,
        codex_model_provider="openai",
        status="running",
        cli_turn_state=None,
        usage_limit_first_at=None,
        awaiting_auto_resume=False,
    )
    usage_refresh.track_session(session)
    for at, name, used in [(10, "primary", 10), (20, "secondary", 1), (30, "primary", 20)]:
        clock[0] = at
        usage_refresh.record_codex(
            {
                "accountId": "a",
                "rateLimits": {
                    name: {
                        "usedPercent": used,
                        "resetsAt": 1000,
                        "windowDurationMins": 5 if name == "primary" else 60,
                    }
                },
            },
            contract="plan",
        )
    window = usage_observation.QuotaWindow("codex", "a", "codex:primary", 20, 1000, 130, contract="plan", duration_seconds=300)
    assert usage_observation.snapshot()["rates"][usage_observation.rate_key(window)] == 0.5


def test_cli_activity_excludes_post_exit_refresh_and_idle(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [0.0]
    monkeypatch.setattr(usage_refresh.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(usage_refresh.time, "time", lambda: clock[0] + 100)
    monkeypatch.setattr(usage_refresh, "_CLOCK", _ActivityClock(tick=0, checked_at=0, enabled=True))

    def report(used: int) -> None:
        usage_refresh.record_codex(
            {"accountId": "a", "rateLimits": {"primary": {"usedPercent": used, "resetsAt": 1000}}}, contract="plan"
        )

    def refreshed() -> None:
        clock[0] = 25
        report(20)

    monkeypatch.setattr(usage_refresh, "ensure_fresh", refreshed)
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CODEX_FAST_MODE", "false")
    with usage_refresh.cli_activity("codex", "chosen", "low", "openai", service_tier="priority"):
        report(10)
        clock[0] = 20
    observed = usage_observation.snapshot()
    assert list(observed["rates"].values()) == [0.5]
    assert any('"chosen", "low", true' in signature for signature in observed["setting_rates"])
    clock[0] = 1000
    report(20)
    assert not usage_observation.snapshot()["rates"]


def test_refresh_cache_reuses_one_observation_until_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [100.0]
    calls = []
    monkeypatch.setattr(usage_refresh.time, "time", lambda: clock[0])

    def refresh(**kwargs):
        calls.append(kwargs)
        return []

    monkeypatch.setattr(usage_refresh, "refresh", refresh)
    usage_refresh.ensure_fresh()
    clock[0] = 101
    usage_refresh.ensure_fresh()
    assert len(calls) == 1
    clock[0] = 401
    usage_refresh.ensure_fresh()
    assert len(calls) == 2


@pytest.mark.parametrize("auth", [None, "not-json", "{}", '{"email":"changed@example.test","subscriptionType":"plan"}'])
def test_failed_refresh_preserves_verified_identity_unless_account_changed(
    monkeypatch: pytest.MonkeyPatch, auth: str | None
) -> None:
    usage_settings.record_metadata("claude", {"account": "known", "contract": "plan"})
    usage_observation.observe(
        [usage_observation.QuotaWindow("claude", "known", "weekly", 70, 1000, 100, contract="plan")],
        setting_signature="A",
        active_seconds=0,
    )

    async def unavailable(*_args, **_kwargs):
        raise ValueError("synthetic read failure")

    def run(argv, **_kwargs):
        return SimpleNamespace(returncode=0 if auth is not None and "auth" in argv else 1, stdout=auth or "")

    monkeypatch.setattr(usage_refresh.codex_models, "with_app_server", unavailable)
    monkeypatch.setattr(
        usage_refresh.claude_connection,
        "candidates",
        lambda *_args, **_kwargs: SimpleNamespace(connections=[SimpleNamespace(kind="subscription")]),
    )
    monkeypatch.setattr(usage_refresh.claude_connection, "connection_environment", lambda *_args: {})
    monkeypatch.setattr(usage_refresh.claude_connection, "isolation_keys", lambda *_args: frozenset())
    monkeypatch.setattr(usage_refresh.claude_settings, "merge_settings", lambda *_args, **_kwargs: "{}")
    monkeypatch.setattr(usage_refresh.process_tree, "run_with_timeout", run)
    assert usage_refresh.refresh(quiet=True)
    windows = usage_observation.snapshot()["windows"]
    assert windows[0]["observed_at"] == 100
    changed = auth is not None and auth.startswith('{"email"')
    # 認証境界の回帰を、表示対象の選別まで通す。
    assert bool(config_usage.profile_windows({"windows": windows})) is not changed
    if not changed:
        assert usage_settings.read_metadata("claude")["account"] == "known"


def test_refresh_uses_explicit_authentication_after_parent_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    """親がOAuthを退避しても、候補判定と両読取コマンドは明示した同じ認証を使う。"""
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_WORKER_CONNECTIONS", "subscription")
    environments = []

    async def unavailable(*_args, **_kwargs):
        raise ValueError("synthetic Codex unavailable")

    def run(argv, **kwargs):
        environments.append(kwargs["env"])
        content = (
            '{"email":"synthetic@example.test","subscriptionType":"plan"}'
            if "auth" in argv
            else '{"usage_report":{"rate_limits":{"limits":[{"kind":"weekly","percent":75}]}}}'
        )
        return SimpleNamespace(returncode=0, stdout=content)

    monkeypatch.setattr(usage_refresh.codex_models, "with_app_server", unavailable)
    monkeypatch.setattr(usage_refresh.process_tree, "run_with_timeout", run)
    usage_refresh.ensure_fresh(force=True, environment={"CLAUDE_CODE_OAUTH_TOKEN": "synthetic-oauth"})
    assert len(environments) == 2
    assert all(environment["CLAUDE_CODE_OAUTH_TOKEN"] == "synthetic-oauth" for environment in environments)
    assert usage_observation.snapshot()["windows"][0]["used_percent"] == 75


@pytest.mark.parametrize(
    ("notification", "keep"),
    [
        ({"accountId": "workspace"}, True),
        ({"workspaceRouting": {"chatgptAccountId": "workspace"}}, True),
        ({"accountId": "other"}, False),
        ({"accountId": "unknown:1"}, False),
        ({}, False),
    ],
)
@pytest.mark.asyncio
async def test_codex_permission_account_tracks_identified_account(notification, keep) -> None:
    async def request(method, _params):
        if method == "account/read":
            return {
                "account": {"email": "user@example.test", "type": "chatgpt"},
                "workspaceRouting": {"chatgptAccountId": "workspace"},
            }
        return {"accountId": "workspace"}

    await usage_refresh._codex_usage(request)  # pylint: disable=protected-access
    permission = usage_settings.read_metadata("codex")["permission_account"]
    assert permission == hashlib.sha256(b"user@example.test").hexdigest()
    assert "user@example.test" not in usage_settings.settings_path().read_text("utf-8")
    usage_refresh.record_codex(notification)
    assert usage_settings.read_metadata("codex")["permission_account"] == (permission if keep else "")


def test_codex_stores_projected_details_and_weekly_selection() -> None:
    weekly = {"windowDurationMins": 10080, "usedPercent": 20, "resetsAt": 1000}
    usage_refresh.record_codex(
        {
            "ordinaryUsageAllowed": False,
            "rateLimitsByLimitId": {
                "codex": {
                    "primary": weekly,
                    "secondary": {**weekly, "resetsAt": 2000},
                    "spendControlReached": True,
                    "rateLimitReachedType": "weekly",
                    "credits": {"hasCredits": True, "unlimited": False, "balance": "1.5", "id": "private-credit"},
                },
            },
            "rateLimitResetCredits": {
                "availableCount": 0,
                "credits": [{"resetType": "codexRateLimits", "status": "used", "id": "private-reset", "expiresAt": "unknown"}],
            },
        }
    )
    metadata = usage_settings.read_metadata("codex")
    assert metadata["weekly_window"] == {"window_id": "codex:primary", "resets_at": 1000}
    assert metadata["ordinaryUsageAllowed"] is False
    assert metadata["spendControlReached"] is True
    assert metadata["rateLimitReachedType"] == "weekly"
    assert metadata["credits"] == {"hasCredits": True, "unlimited": False, "balance": "1.5"}
    assert metadata["reset_available_count"] == 0
    assert metadata["reset_credit_details"] == [{"resetType": "codexRateLimits", "status": "used"}]
    assert "private-" not in usage_settings.settings_path().read_text("utf-8")


def test_weekly_validation_failure_does_not_discard_observation() -> None:
    usage_refresh.record_codex(
        {
            "rateLimits": {
                "limitId": "codex",
                "primary": {"windowDurationMins": 10080, "usedPercent": 20, "resetsAt": "1970-01-01T01:00:00+00:00"},
            },
        }
    )
    assert usage_settings.read_metadata("codex")["weekly_window"] is None
    windows = usage_observation.snapshot()["current_windows"]
    assert [(row["used_percent"], row["resets_at"]) for row in windows] == [(20, 3600)]
