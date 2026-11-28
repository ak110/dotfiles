import asyncio
import json
import pathlib
import shutil
import subprocess
import threading

import pytest
import quart

from agent_toolkit._atk import config, usage_refresh
from agent_toolkit._atk.serve import usage_routes
from agent_toolkit._common import process_tree, state_paths, usage_observation, usage_settings


@pytest.fixture(name="usage_client")
def _usage_client_fixture(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
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

    def refresh(**_kwargs):
        calls.append(now)
        return []

    monkeypatch.setattr(usage_refresh, "refresh", refresh)
    assert config.resolve_mutable_setting("usage_reset_permissions") == "[]"
    initial = await usage_client.post("/api/usage/refresh", json={"force": False})
    assert initial.status_code == 200
    now += 299
    cached = await usage_client.get("/api/usage")
    assert (await cached.get_json())["refresh_due"] is False
    recent = await usage_client.post("/api/usage/refresh", json={"force": False})
    assert (await recent.get_json())["last_refresh_at"] == now - 299
    assert calls == [now - 299]
    now += 1
    cached = await usage_client.get("/api/usage")
    assert (await cached.get_json())["refresh_due"] is True
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

    def refresh(**_kwargs):
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
    response = None
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
    assert response is not None
    assert response.status_code == 200
    payload = await response.get_json()
    assert payload["refresh_failures"] == (["Claude: CLIで取得できない"] if same_context else ["Codex: 画面で取得できない"])
    assert calls == (["cli"] if same_context else ["cli", "display"])


@pytest.mark.asyncio
async def test_antigravity_refresh_keeps_the_other_providers_independent(usage_client, monkeypatch: pytest.MonkeyPatch) -> None:
    available = False
    failure = False
    calls: list[list[str]] = []
    report = {
        "status": "SUCCESS",
        "command": {
            "name": "usage",
            "data": {
                "groups": [
                    {
                        "name": "Gemini Models",
                        "buckets": [
                            {
                                "id": "gemini-week",
                                "window": "weekly",
                                "name": "Weekly Remaining",
                                "remaining_fraction": 1,
                                "reset_time": "2033-05-18T06:00:00Z",
                            },
                            {
                                "id": "gemini-invalid",
                                "window": "weekly",
                                "name": "Invalid",
                                "remaining_fraction": 10**400,
                                "reset_time": "2033-05-18T06:00:00",
                            },
                            {"id": "gemini-missing", "window": "daily", "name": "Missing", "remaining_fraction": None},
                            {"id": "gemini-empty", "window": "daily", "name": "Empty", "remaining_fraction": 0},
                            {"window": "weekly", "name": "Missing ID", "remaining_fraction": 1},
                            {"id": "no-period", "name": "Missing Window", "remaining_fraction": 1},
                            {"id": 1, "window": "weekly", "name": "Wrong ID Type", "remaining_fraction": 1},
                            {"id": "wrong-period", "window": [], "name": "Wrong Window Type", "remaining_fraction": 1},
                        ],
                    }
                ]
            },
        },
    }

    def run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, int(failure), json.dumps(report), "private diagnostic")

    monkeypatch.setattr(shutil, "which", lambda _command: "agy-fixture" if available else None)
    monkeypatch.setattr(usage_refresh.process_tree, "run_with_timeout", run)

    async def request(force: bool):
        response = await usage_client.post("/api/usage/antigravity", json={"force": force})
        assert response.status_code == 200
        assert "private diagnostic" not in await response.get_data(as_text=True)
        return await response.get_json()

    assert await request(False) == {"windows": [], "observed_at": None, "stale": False}
    assert calls == []
    available = True
    failure = True
    assert await request(True) == {"windows": [], "observed_at": None, "stale": True}
    failure = False
    first = await request(True)
    assert first["observed_at"] > 0 and first["stale"] is False
    expected = {
        "group": "Gemini Models",
        "name": "Weekly Remaining",
        "remaining_fraction": 1.0,
        "resets_at": 2000008800.0,
    }
    assert first["windows"][0] == expected
    assert [(row["name"], row["remaining_fraction"], row["resets_at"]) for row in first["windows"][1:]] == [
        ("Invalid", None, None),
        ("Missing", None, None),
        ("Empty", 0.0, None),
    ]
    assert await request(False) == first
    assert len(calls) == 2
    failure = True
    assert await request(True) == {**first, "stale": True}
    standard = await usage_client.get("/api/usage")
    assert standard.status_code == 200
    assert set((await standard.get_json())["metadata"]) == {"claude", "codex"}
    failure = False
    assert (await request(True))["stale"] is False
    assert calls == [["agy-fixture", "-p", "/usage", "--output-format=json"]] * 4


@pytest.mark.asyncio
async def test_antigravity_disconnect_keeps_ownership_of_the_active_read(usage_client, monkeypatch: pytest.MonkeyPatch) -> None:
    started = threading.Event()
    overlap = threading.Event()
    release = threading.Event()

    def read():
        if started.is_set():
            overlap.set()
        started.set()
        assert release.wait(5)
        return []

    monkeypatch.setattr(usage_refresh, "read_antigravity_usage", read)
    second = None
    try:
        async with usage_client.request(
            "/api/usage/antigravity", method="POST", headers={"Content-Type": "application/json"}
        ) as first:
            await first.send(b'{"force":true}')
            await first.send_complete()
            assert await asyncio.to_thread(started.wait, 2)
            await first.disconnect()
        second = asyncio.create_task(usage_client.post("/api/usage/antigravity", json={"force": True}))
        assert (await usage_client.get("/api/usage")).status_code == 200
        assert not await asyncio.to_thread(overlap.wait, 0.3)
    finally:
        release.set()
        if second is not None:
            assert (await second).status_code == 200
    assert not overlap.is_set()


@pytest.mark.asyncio
async def test_antigravity_reclaims_the_same_child_before_retrying(usage_client, monkeypatch: pytest.MonkeyPatch) -> None:
    windows = [{"group": "Gemini", "name": "Weekly", "remaining_fraction": 0.5, "resets_at": None}]
    pending = process_tree.ReclaimError(["fixture"], [], context="fixture")
    reads = 0
    attempts = []

    def read():
        nonlocal reads
        reads += 1
        if reads == 2:
            raise pending
        return windows

    def retry():
        attempts.append(pending)
        return ["fixture"] if len(attempts) == 1 else []

    async def request():
        response = await usage_client.post("/api/usage/antigravity", json={"force": True})
        assert response.status_code == 200
        return await response.get_json()

    monkeypatch.setattr(usage_refresh, "read_antigravity_usage", read)
    monkeypatch.setattr(pending, "retry", retry)
    first = await request()
    assert first["windows"] == windows and first["stale"] is False
    assert await request() == {**first, "stale": True}
    assert await request() == {**first, "stale": True}
    assert reads == 2 and attempts == [pending]
    assert (await usage_client.get("/api/usage")).status_code == 200
    assert (await request())["stale"] is False
    assert reads == 3 and attempts == [pending, pending]
