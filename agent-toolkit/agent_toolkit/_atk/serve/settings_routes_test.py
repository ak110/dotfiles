import asyncio
import base64
import json
import re
import subprocess
import threading

import pytest
import quart

from agent_toolkit._atk import git_sync
from agent_toolkit._atk.serve import settings_routes, shell_routes
from agent_toolkit._atk.serve.config import ServeConfig


@pytest.mark.parametrize("legacy", [False, True])
def test_posix_probe_keeps_snapshot_show_and_update_on_ssh(monkeypatch, legacy):
    calls = []

    def run(command, **_kwargs):
        assert command[0] == "ssh"
        operation = command[-1]
        calls.append(operation)
        if operation == "uname -s":
            return subprocess.CompletedProcess(command, 0, b"Linux\n", b"")
        if operation == "atk config snapshot":
            return subprocess.CompletedProcess(command, 2 if legacy else 0, b'{"settings":{"serve_host":{}}}', b"")
        if operation == "atk config show":
            return subprocess.CompletedProcess(command, 0, b"serve_host old\n", b"")
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(settings_routes, "run_with_timeout", run)
    settings_routes._remote_update("host", {"action": "set", "key": "serve_host", "value": "localhost"})  # noqa: SLF001  # pylint: disable=protected-access
    assert calls == ["uname -s", "atk config snapshot"] + (
        ["atk config show", "atk config set serve_host localhost"] if legacy else ["atk config update"]
    )


@pytest.mark.parametrize("update_status", [0, 124])
def test_windows_runs_snapshot_and_update_only_on_the_unelevated_route(monkeypatch, update_status):
    payload = {"action": "set", "key": "serve_host", "value": "日本語 & | % ^"}
    calls = []

    def run(command, **kwargs):
        calls.append(command[0])
        data = kwargs["input"]
        if command[0] == "ssh":
            assert command[-1] == "uname -s"
            return subprocess.CompletedProcess(command, 1, b"", b"uname unavailable")
        assert command[0] == "ssh-unelevated"
        assert command[-3:] == ["--", "windows-host", "-"]
        assert b"exit $global:LASTEXITCODE" in data
        if b"'snapshot'" in data:
            return subprocess.CompletedProcess(command, 0, b'{"settings":{"serve_host":{}}}', b"")
        assert b"'update'" in data
        marker = re.search(rb"FromBase64String\('([^']+)'\)", data)
        assert marker is not None
        assert json.loads(base64.b64decode(marker[1])) == payload
        return subprocess.CompletedProcess(command, update_status, b"", b"")

    monkeypatch.setattr(settings_routes, "run_with_timeout", run)
    if update_status:
        with pytest.raises(ValueError, match="保存"):
            settings_routes._remote_update("windows-host", payload)  # noqa: SLF001  # pylint: disable=protected-access
    else:
        settings_routes._remote_update("windows-host", payload)  # noqa: SLF001  # pylint: disable=protected-access
    assert calls == ["ssh", "ssh-unelevated", "ssh-unelevated"]


def test_unrecognized_host_runs_legacy_operations_only_on_the_unelevated_route(monkeypatch):
    operations = []

    def run(command, **kwargs):
        if command[0] == "ssh":
            assert command[-1] == "uname -s"
            return subprocess.CompletedProcess(command, 0, b"MSYS_NT-10.0\n", b"")
        assert command[0] == "ssh-unelevated"
        operation = kwargs["input"].splitlines()[2]
        operations.append(operation)
        if b"'snapshot'" in operation:
            return subprocess.CompletedProcess(command, 2, b"", b"")
        if b"'show'" in operation:
            return subprocess.CompletedProcess(command, 0, b"serve_host old\n", b"")
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(settings_routes, "run_with_timeout", run)
    settings_routes._remote_update("host", {"action": "set", "key": "serve_host", "value": "localhost"})  # noqa: SLF001  # pylint: disable=protected-access
    assert operations == [
        b"$atkArgs = @('config', 'snapshot')",
        b"$atkArgs = @('config', 'show')",
        b"$atkArgs = @('config', 'set', 'serve_host', 'localhost')",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("windows", [False, True])
async def test_remote_save_timeout_only_instructs_to_read_the_current_value(monkeypatch, windows):
    def run(command, **kwargs):
        if command[-1] == "uname -s":
            return subprocess.CompletedProcess(command, 1 if windows else 0, b"Linux\n", b"")
        if command[-1] == "atk config snapshot" or windows and b"'snapshot'" in kwargs["input"]:
            return subprocess.CompletedProcess(command, 0, b'{"settings":{"execute_model":{}}}', b"")
        if windows:
            return subprocess.CompletedProcess(command, 124, b"", b"")
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(settings_routes, "run_with_timeout", run)
    monkeypatch.setattr(settings_routes, "_remote_hosts", lambda **_: ("host",))
    app = quart.Quart(__name__)
    shell_routes.register_error_handlers(app)
    settings_routes.register_settings_routes(app, ServeConfig("127.0.0.1", 8080))

    response = await app.test_client().post(
        "/api/settings?host=host", json={"action": "set", "key": "execute_model", "value": "claude:opus"}
    )
    assert response.status_code == 400
    assert await response.get_json() == {
        "error": "保存処理の結果を確認できません",
        "next_action": "設定画面を再読み込みし、SSH先の現在の値を確認する",
    }


@pytest.mark.parametrize("save", [False, True])
def test_ssh_probe_connection_failure_does_not_try_unelevated(monkeypatch, save):
    def run(command, **_kwargs):
        assert command[0] == "ssh"
        assert command[-1] == "uname -s"
        return subprocess.CompletedProcess(command, 255, b"", b"Connection refused")

    monkeypatch.setattr(settings_routes, "run_with_timeout", run)
    with pytest.raises(settings_routes.WebInputError) as result:
        if save:
            settings_routes._remote_update("host", {"action": "set", "key": "serve_host", "value": "localhost"})  # noqa: SLF001  # pylint: disable=protected-access
        else:
            settings_routes._remote_snapshot("host")  # noqa: SLF001  # pylint: disable=protected-access
    assert result.value.next_action == "接続先とSSHの接続を確認する"


@pytest.mark.parametrize("windows", [False, True])
@pytest.mark.parametrize(
    ("payload", "shared"),
    [
        ({"action": "preset", "preset": "codex-near-primary"}, True),
        ({"action": "set", "key": "execute_model", "value": "claude:opus"}, True),
        ({"action": "set", "key": "serve_port", "value": "25000"}, False),
        ({"action": "temporary-clear"}, False),
    ],
)
def test_remote_settings_limit_covers_shared_git_update_without_extending_reads(monkeypatch, windows, payload, shared):
    calls = []

    def run(command, **kwargs):
        if command[-1] == "uname -s":
            return subprocess.CompletedProcess(command, 1 if windows else 0, b"Linux\n", b"")
        duration = int(command[2]) if windows else kwargs["timeout"]
        calls.append(duration)
        if len(calls) == 1:
            return subprocess.CompletedProcess(command, 0, b'{"settings":{"execute_model":{},"serve_port":{}}}', b"")
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(settings_routes, "run_with_timeout", run)
    settings_routes._remote_update("host", payload)  # noqa: SLF001  # pylint: disable=protected-access
    assert len(calls) == 2
    assert calls[0] == 20
    if shared:
        assert calls[1] > git_sync.LOCK_WAIT_TIMEOUT_SECONDS + git_sync.LOCK_HOLD_TIMEOUT_SECONDS
    else:
        assert calls[1] == calls[0]


@pytest.mark.parametrize("supported", [True, False])
def test_background_snapshot_never_falls_back_to_legacy_reads(monkeypatch, supported):
    calls = []

    def run(command, **_kwargs):
        operation = command[-1]
        calls.append(operation)
        if operation == "uname -s":
            return subprocess.CompletedProcess(command, 0, b"Linux\n", b"")
        if operation == "atk config snapshot --help":
            return subprocess.CompletedProcess(command, 0, b"usage: atk config snapshot", b"")
        assert operation == "atk config snapshot --read-only"
        return subprocess.CompletedProcess(command, 0 if supported else 2, b'{"settings":{}}', b"")

    monkeypatch.setattr(settings_routes, "run_with_timeout", run)
    if supported:
        snapshot, _runner = settings_routes._remote_snapshot("host", read_only=True)  # noqa: SLF001  # pylint: disable=protected-access
        assert snapshot == {"settings": {}}
    else:
        with pytest.raises(settings_routes._ReadOnlyUnsupported):  # noqa: SLF001  # pylint: disable=protected-access
            settings_routes._remote_snapshot("host", read_only=True)  # noqa: SLF001  # pylint: disable=protected-access
    assert calls == ["uname -s", "atk config snapshot --read-only"] + ([] if supported else ["atk config snapshot --help"])


@pytest.mark.asyncio
async def test_opening_settings_prefetches_unselected_hosts_without_migrating_them(monkeypatch):
    calls = []
    monkeypatch.setattr(settings_routes, "_remote_hosts", lambda *, read_only=False: ("current", "legacy"))
    monkeypatch.setattr(settings_routes.config, "settings_snapshot", lambda *_args, **_kwargs: {"settings": {}})

    def run(command, **_kwargs):
        host, operation = command[-2:]
        calls.append((host, operation))
        if operation == "uname -s":
            return subprocess.CompletedProcess(command, 0, b"Linux\n", b"")
        if operation == "atk config snapshot --help":
            return subprocess.CompletedProcess(command, 0, b"usage: atk config snapshot", b"")
        assert operation == "atk config snapshot --read-only"
        return subprocess.CompletedProcess(command, 0 if host == "current" else 2, b'{"settings":{}}', b"")

    monkeypatch.setattr(settings_routes, "run_with_timeout", run)
    app = quart.Quart(__name__)
    shell_routes.register_error_handlers(app)
    settings_routes.register_settings_routes(app, ServeConfig("127.0.0.1", 8080))
    await app.startup()
    try:
        client = app.test_client()
        assert (await client.get("/api/settings")).status_code == 200
        assert not calls
        assert (await client.post("/api/settings/prepare")).status_code == 200
        async with asyncio.timeout(3):
            while True:
                listing = await (await client.get("/api/settings?background=1")).get_json()
                states = {name: peer["state"] for name, peer in listing["comparison"].items()}
                if states == {"current": "ready", "legacy": "legacy"}:
                    break
                await asyncio.sleep(0.01)
        assert listing["automatic_unsupported"] == ["legacy"]
        cached = await (await client.get("/api/settings?host=current&background=1")).get_json()
        assert cached["settings"] == {}
        assert set(calls) == {
            (host, operation) for host in ("current", "legacy") for operation in ("uname -s", "atk config snapshot --read-only")
        } | {("legacy", "atk config snapshot --help")}
    finally:
        await app.shutdown()


@pytest.mark.asyncio
async def test_selection_joins_one_read_and_rejects_saving_the_previous_snapshot(monkeypatch):
    host = "current"
    endpoint = f"/api/settings?host={host}"
    release = threading.Event()
    started = asyncio.Event()
    loop = asyncio.get_running_loop()
    reads: list[str] = []
    updates: list[dict] = []
    monkeypatch.setattr(settings_routes, "_remote_hosts", lambda *, read_only=False: (host,))

    def snapshot(target, *, read_only):
        assert read_only
        reads.append(target)
        loop.call_soon_threadsafe(started.set)
        assert release.wait(5)
        return {"settings": {"serve_host": {"saved": str(len(reads))}}}, None

    monkeypatch.setattr(settings_routes, "_remote_snapshot", snapshot)
    monkeypatch.setattr(settings_routes, "_remote_update", lambda _host, payload: updates.append(payload))
    app = quart.Quart(__name__)
    shell_routes.register_error_handlers(app)
    settings_routes.register_settings_routes(app, ServeConfig("127.0.0.1", 8080))
    await app.startup()
    try:
        client = app.test_client()
        assert (await client.post("/api/settings/prepare")).status_code == 200
        try:
            await asyncio.wait_for(started.wait(), 3)
            initial = await (await client.get(endpoint + "&selection=1")).get_json()
            assert initial["settings_state"] == "pending"
            assert "settings" not in initial
        finally:
            release.set()
        ready = await (await client.get(endpoint + "&selection=1&wait=1")).get_json()
        assert ready["settings_state"] == "ready"
        assert reads == [host]
        release.clear()
        started.clear()
        try:
            old = ready["fetched_at"]
            cached = await (
                await client.get("/api/settings", query_string={"host": host, "selection": "1", "after": old})
            ).get_json()
            assert cached["settings_state"] == "pending"
            assert cached["settings"] == ready["settings"]
            await asyncio.wait_for(started.wait(), 3)
            payload = {"action": "set", "key": "serve_host", "value": "updated"}
            assert (await client.post(endpoint, json={**payload, "settings_revision": old})).status_code == 409
        finally:
            release.set()
        latest = await (await client.get(endpoint + "&selection=1&wait=1")).get_json()
        assert latest["settings_state"] == "ready"
        assert latest["settings"] != cached["settings"]
        assert (await client.post(endpoint, json={**payload, "settings_revision": old})).status_code == 409
        assert (await client.post(endpoint, json={**payload, "settings_revision": latest["fetched_at"]})).status_code == 200
        assert updates == [payload]
        assert reads == [host, host]
    finally:
        await app.shutdown()
