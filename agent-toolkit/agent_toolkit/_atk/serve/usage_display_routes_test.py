import asyncio
import pathlib
import threading

import pytest
import quart

from agent_toolkit._atk import config, usage_refresh
from agent_toolkit._atk.serve import usage_routes
from agent_toolkit._common import state_paths, usage_observation, usage_settings


@pytest.fixture
def usage_client(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(usage_observation, "state_dir", lambda: tmp_path)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_USAGE_RESET_PERMISSIONS", "[]")
    app = quart.Quart(__name__)
    usage_routes.register_usage_routes(app)
    return app.test_client()


@pytest.mark.asyncio
async def test_usage_api_only_exposes_public_reset_credit_fields(usage_client) -> None:
    usage_refresh.record_codex(
        {
            "rateLimitResetCredits": {
                "availableCount": 0,
                "credits": [
                    {
                        "id": "private-reset-marker",
                        "status": "used",
                        "resetType": "codexRateLimits",
                        "unknown": "private-unknown-marker",
                    }
                ],
            }
        },
        account="private-account-marker",
        contract="pro",
    )
    response = await usage_client.get("/api/usage")
    assert response.status_code == 200
    payload = await response.get_json()
    assert payload["metadata"]["codex"]["reset_available_count"] == 0
    assert payload["metadata"]["codex"]["reset_credit_details"] == [{"status": "used", "resetType": "codexRateLimits"}]
    for private in ("private-reset-marker", "private-unknown-marker", "private-account-marker"):
        assert private not in await response.get_data(as_text=True)


@pytest.mark.asyncio
async def test_display_refresh_at_five_minutes_without_permissions(usage_client, monkeypatch: pytest.MonkeyPatch) -> None:
    now = 2000000000
    calls = []
    monkeypatch.setattr(usage_refresh.time, "time", lambda: now)

    def refresh(*, quiet, environment):
        calls.append(now)
        return []

    monkeypatch.setattr(usage_refresh, "refresh", refresh)
    assert config.resolve_mutable_setting("usage_reset_permissions") == "[]"
    initial = await usage_client.post("/api/usage/refresh", json={"force": False})
    assert initial.status_code == 200
    now += 299
    recent = await usage_client.post("/api/usage/refresh", json={"force": False})
    assert (await recent.get_json())["last_refresh_at"] == now - 299
    assert calls == [now - 299]
    now += 1
    stale = await usage_client.post("/api/usage/refresh", json={"force": False})
    assert (await stale.get_json())["last_refresh_at"] == now
    assert calls == [now - 300, now]


@pytest.mark.asyncio
@pytest.mark.parametrize("same_context", [True, False])
async def test_display_refresh_waits_for_cli_and_reuses_only_matching_context(
    usage_client, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, same_context: bool
) -> None:
    display_context = usage_settings.context_id()
    with monkeypatch.context() as other:
        other.setenv("CODEX_HOME", str(tmp_path / "other-codex"))
        other_context = usage_settings.context_id()
    cli_context = display_context if same_context else other_context
    local = threading.local()
    monkeypatch.setattr(usage_settings, "context_id", lambda: getattr(local, "context", display_context))
    cli_started = threading.Event()
    display_waiting = threading.Event()
    release_cli = threading.Event()
    calls = []
    acquire = usage_refresh.acquire_lock

    def acquire_lock(lock, *, blocking=True):
        if lock.name.endswith(".refresh.lock") and blocking:
            display_waiting.set()
        return acquire(lock, blocking=blocking)

    def refresh(*, quiet, environment):
        if getattr(local, "context", None) == cli_context:
            calls.append("cli")
            cli_started.set()
            assert release_cli.wait(10)
            return ["Claude: CLIで取得できない"]
        calls.append("display")
        return ["Codex: 画面で取得できない"]

    def run_cli():
        local.context = cli_context
        try:
            usage_refresh.ensure_fresh(force=True)
        finally:
            del local.context

    monkeypatch.setattr(usage_refresh, "acquire_lock", acquire_lock)
    monkeypatch.setattr(usage_refresh, "refresh", refresh)
    cli = asyncio.create_task(asyncio.to_thread(run_cli))
    display = None
    try:
        assert await asyncio.to_thread(cli_started.wait, 10)
        display = asyncio.create_task(usage_client.post("/api/usage/refresh", json={"force": True}))
        assert await asyncio.to_thread(display_waiting.wait, 10)
        assert not display.done()
    finally:
        release_cli.set()
        await cli
        if display is not None:
            response = await display
    assert response.status_code == 200
    payload = await response.get_json()
    assert payload["refresh_failures"] == (["Claude: CLIで取得できない"] if same_context else ["Codex: 画面で取得できない"])
    assert calls == (["cli"] if same_context else ["cli", "display"])
