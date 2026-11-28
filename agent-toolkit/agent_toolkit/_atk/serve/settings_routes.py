import asyncio
import base64
import contextlib
import datetime
import json
import pathlib
import re
import subprocess
import typing

import quart

from agent_toolkit._atk import config, config_migration, config_settings, config_shared_migration, git_sync, usage_refresh
from agent_toolkit._atk.serve.config import ServeConfig
from agent_toolkit._atk.serve.shell_routes import json_no_store, no_store, render_index, safe_base_path
from agent_toolkit._atk.wi.web_input import WebInputError, web_input_error_from
from agent_toolkit._common.process_tree import run_with_timeout


def _remote_hosts(*, read_only: bool = False) -> tuple[str, ...]:
    if not read_only:
        config_migration.migrate_serve_settings()
    return config.parse_remote_hosts(config.resolve_mutable_setting("remote_hosts", read_only=read_only))


def _setting_timeout(operation: str, payload: dict[str, typing.Any] | None) -> int:
    if operation.startswith("atk config migrate-shared"):
        return int(git_sync.LOCK_WAIT_TIMEOUT_SECONDS + git_sync.LOCK_HOLD_TIMEOUT_SECONDS) + 60
    if operation == "atk config update" and payload is not None:
        action, key = payload.get("action"), payload.get("key")
        writes_shared = (
            action in ("set", "array-add", "array-remove")
            and isinstance(key, str)
            and key in config_settings.SHARED_SETTING_KEYS
        )
        if action in ("preset", "repo", "reset-reissue") or writes_shared:
            return int(git_sync.LOCK_WAIT_TIMEOUT_SECONDS + git_sync.LOCK_HOLD_TIMEOUT_SECONDS) + 20
    return 100 if operation.endswith(" --observe-usage") else 20


def _ssh(host: str, operation: str, payload: dict[str, typing.Any] | None = None) -> subprocess.CompletedProcess[bytes]:
    # pyfltr: allow-comment Windowsのcmd.exeとPOSIXシェルで同じコマンドを使えるように、自由入力は標準入力で渡す。
    return run_with_timeout(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "--", host, operation],
        input=json.dumps(payload).encode("ascii") if payload is not None else b"",
        capture_output=True,
        timeout=_setting_timeout(operation, payload),
        check=False,
    )


def _ssh_unelevated(
    host: str, operation: str, payload: dict[str, typing.Any] | None = None
) -> subprocess.CompletedProcess[bytes]:
    # pyfltr: allow-comment 配列の引数とbase64の本文は、PowerShellのコードとして展開しない。
    arguments = ", ".join("'" + arg.replace("'", "''") + "'" for arg in operation.removeprefix("atk ").split())
    script = "$env:PYTHONIOENCODING = 'utf-8'\n$global:LASTEXITCODE = 1\n"
    script += f"$atkArgs = @({arguments})\n"
    if payload is not None:
        encoded = base64.b64encode(json.dumps(payload).encode("ascii")).decode("ascii")
        script += f"$packet = [Text.Encoding]::ASCII.GetString([Convert]::FromBase64String('{encoded}'))\n"
        script += "$packet | "
    script += "& atk @atkArgs 2>$null\nexit $global:LASTEXITCODE\n"
    try:
        return run_with_timeout(
            ["ssh-unelevated", "--timeout", str(_setting_timeout(operation, payload)), "--", host, "-"],
            input=script.encode("utf-8"),
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as error:
        raise ValueError("SSH先の設定を非昇格で操作するため、ローカルにssh-unelevatedを導入してください") from error


type _ConfigRunner = typing.Callable[[str, str, dict[str, typing.Any] | None], subprocess.CompletedProcess[bytes]]


_LEGACY_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_,./:@+-]*")
_SETTING_KEY = re.compile(r"[a-z][a-z0-9_]*")
_PATH_KEYS = {"config_dir", "state_dir", "data_dir", "private_notes"}


class _ReadOnlyUnsupported(Exception):
    pass


def _remote_snapshot(
    host: str, *, read_only: bool = False, observe_usage: bool = False
) -> tuple[dict[str, typing.Any], _ConfigRunner]:
    platform = _ssh(host, "uname -s")
    if platform.returncode == 255:
        raise WebInputError("SSH先に接続できません", next_action="接続先とSSHの接続を確認する")
    # pyfltr: allow-comment snapshotと旧版showは旧設定を移行するため、OSを判別してから実行する。
    runner: _ConfigRunner = (
        _ssh
        if platform.returncode == 0 and platform.stdout.strip() in {b"Linux", b"Darwin", b"FreeBSD", b"OpenBSD", b"NetBSD"}
        else _ssh_unelevated
    )
    operation = "atk config snapshot --read-only" if read_only else "atk config snapshot"
    if observe_usage and read_only:
        operation += " --observe-usage"
    result = runner(host, operation, None)
    if result.returncode == 0:
        snapshot = json.loads(result.stdout)
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("settings"), dict):
            raise ValueError("SSH先の設定一覧を解釈できません")
        for key, item in snapshot["settings"].items():
            if not isinstance(item, dict):
                raise ValueError("SSH先の設定一覧を解釈できません")
            if key not in config_settings.MUTABLE_KEY_DEFAULTS:
                item["read_only"] = True
        return snapshot, runner
    if runner is _ssh_unelevated and result.returncode == 125:
        raise ValueError("非昇格の設定取得に失敗しました。SSH先でユーザーがログオンしているかを確認してください")
    if result.returncode != 2:
        raise ValueError("SSH先で設定を取得できません。接続とSSH先のatkの起動を確認してください")

    if read_only:
        capability = runner(host, "atk config snapshot --help", None)
        if capability.returncode == 2 or (capability.returncode == 0 and b"--read-only" not in capability.stdout):
            raise _ReadOnlyUnsupported
        raise ValueError("SSH先の読み取り専用の設定取得に失敗しました")
    legacy = runner(host, "atk config show", None)
    if legacy.returncode != 0:
        raise ValueError("SSH先で設定を取得できません。SSH先でatk config showの実行結果を確認してください")
    settings, paths = {}, {}
    for line in legacy.stdout.decode("utf-8", errors="replace").splitlines():
        key, sep, value = line.partition(" ")
        if not sep or not _SETTING_KEY.fullmatch(key):
            continue
        if key in _PATH_KEYS:
            paths[key] = value
            continue
        known = key in config_settings.MUTABLE_KEY_DEFAULTS
        settings[key] = {
            "effective": value,
            "saved": value,
            "description": config_settings.setting_description(key) if known else "",
            "format": config_settings.setting_format(key) if known else "",
            "choices": config_settings.SETTING_CHOICES.get(key),
            "read_only": not known or key == "repositories" or not _LEGACY_TOKEN.fullmatch(value),
        }
    if not settings:
        raise ValueError("SSH先の設定を解釈できません。SSH先のatkのバージョンを確認してください")
    return {"legacy": True, "settings": settings, "paths": paths}, runner


def _remote_update(host: str, payload: dict[str, typing.Any]) -> None:
    snapshot, runner = _remote_snapshot(host)
    action = payload.get("action")
    if action in ("set", "temporary", "array-add", "array-remove"):
        key = config.operation_text(payload, "key")
        if key is None or snapshot["settings"].get(key, {"read_only": True}).get("read_only"):
            raise ValueError("このホストで変更できる設定を選んでください")
    update_payload: dict[str, typing.Any] | None = payload
    if not snapshot.get("legacy"):
        operation = "atk config update"
    else:
        key, value = config.operation_text(payload, "key"), config.operation_text(payload, "value")
        # pyfltr: allow-comment 旧版は標準入力に非対応のため、cmd.exe・POSIXシェルの両方で引用が不要な1引数に限る。
        if action != "set" or key is None or value is None or not _LEGACY_TOKEN.fullmatch(value):
            raise ValueError("この値を設定画面から保存するにはSSH先のatkを更新してください")
        config_settings.validate_mutable_setting(key, value)
        operation = f"atk config set {key} {value}"
        update_payload = None
    try:
        result = runner(host, operation, update_payload)
    except subprocess.TimeoutExpired as error:
        raise _unconfirmed_remote_save() from error
    if runner is _ssh_unelevated and result.returncode == 124:
        raise _unconfirmed_remote_save()
    if result.returncode != 0:
        raise ValueError("SSH先のatkが設定を保存できませんでした。SSH先で設定と値の書式を確認してください")


_MIGRATION_TOKEN = re.compile(r"[A-Za-z0-9_:./@-]+")


def _migration_payload(payload: dict[str, typing.Any]) -> tuple[str, str, dict[str, config_shared_migration.Choice]]:
    action = payload.get("action")
    if action == "compare":
        return action, "", {}
    revision, choices = payload.get("revision"), payload.get("choices", {})
    if action != "apply" or not isinstance(revision, str) or re.fullmatch(r"[0-9a-f]{64}", revision) is None:
        raise ValueError("差を確認し直してから移行してください")
    if not isinstance(choices, dict) or any(
        not isinstance(key, str) or not _MIGRATION_TOKEN.fullmatch(key) or value not in ("local", "shared")
        for key, value in choices.items()
    ):
        raise ValueError("差のある項目ごとに残す値を選んでください")
    return action, revision, typing.cast(dict[str, config_shared_migration.Choice], choices)


def _shared_migration(host: str, payload: dict[str, typing.Any]) -> dict[str, typing.Any]:
    """共有設定への移行をCLIと同じ処理で比較・適用する。SSH先は同じCLIを呼ぶ。  pyfltr: allow-comment"""
    action, revision, choices = _migration_payload(payload)
    if not host:
        if action == "compare":
            return config_shared_migration.compare()
        return {"moved": config_shared_migration.apply(revision, choices)}
    try:
        snapshot, runner = _remote_snapshot(host, read_only=True)
    except _ReadOnlyUnsupported:
        snapshot, runner = {"legacy": True}, _ssh
    capability = snapshot.get("shared_migration")
    if snapshot.get("legacy") or not isinstance(capability, dict) or capability.get("format") != config_shared_migration.FORMAT:
        raise ValueError("SSH先のatkが旧バージョンのため移行できません。SSH先のatkを更新してください")
    if action == "compare":
        result = runner(host, "atk config migrate-shared --json", None)
        if result.returncode != 0:
            raise ValueError("SSH先で共有設定との差を確認できません。SSH先のprivate-notesの同期を確認してください")
        plan = json.loads(result.stdout)
        if not isinstance(plan, dict) or plan.get("format") != config_shared_migration.FORMAT:
            raise ValueError("SSH先のatkが旧バージョンのため移行できません。SSH先のatkを更新してください")
        return plan
    options = "".join(f" --keep-{value} {key}" for key, value in sorted(choices.items()))
    try:
        result = runner(host, f"atk config migrate-shared --apply {revision}{options}", None)
    except subprocess.TimeoutExpired as error:
        raise _unconfirmed_remote_save() from error
    if runner is _ssh_unelevated and result.returncode == 124:
        raise _unconfirmed_remote_save()
    if result.returncode != 0:
        raise ValueError("SSH先で移行できませんでした。差を確認し直してから再実行してください")
    return {"moved": None}


def _unconfirmed_remote_save() -> WebInputError:
    return WebInputError(
        "保存処理の結果を確認できません",
        next_action="設定画面を再読み込みし、SSH先の現在の値を確認する",
    )


def _request_host() -> str:
    host = quart.request.args.get("host", "")
    if host and host not in _remote_hosts(read_only=True):
        raise ValueError("ローカル設定のremote_hostsに登録したホストを選んでください")
    return host


def register_settings_routes(app: quart.Quart, serve: ServeConfig) -> None:
    locks: dict[str, asyncio.Lock] = {}
    snapshots: dict[str, dict[str, typing.Any]] = {}

    unsupported: set[str] = set()
    errors: dict[str, str] = {}

    def comparison(hosts: tuple[str, ...]) -> dict[str, typing.Any]:
        return {
            name: {
                "state": "legacy"
                if name in unsupported
                else "error"
                if name in errors
                else "ready"
                if name in snapshots
                else "pending",
                "shared_identity": snapshots.get(name, {}).get("shared_identity"),
                "provider_warnings": snapshots.get(name, {}).get("provider_warnings", []),
                "settings": {
                    key: {field: item.get(field) for field in ("saved", "shared", "migration_pending")}
                    for key, item in snapshots.get(name, {}).get("settings", {}).items()
                    if isinstance(item, dict)
                },
            }
            for name in hosts
        }

    def remember_snapshot(host: str, snapshot: dict[str, typing.Any]) -> None:
        snapshot["fetched_at"] = datetime.datetime.now(datetime.UTC).isoformat()
        snapshots[host] = snapshot
        errors.pop(host, None)

    def with_host_status(snapshot: dict[str, typing.Any], hosts: tuple[str, ...], **status: str) -> quart.Response:
        return json_no_store(
            {
                **snapshot,
                **status,
                "hosts": hosts,
                "automatic_errors": {key: errors[key] for key in hosts if key in errors},
                "automatic_unsupported": sorted(unsupported.intersection(hosts)),
                "comparison": comparison(hosts),
            }
        )

    async def read_remote(host: str, observe_usage: bool) -> None:
        async with locks.setdefault(host, asyncio.Lock()):
            try:
                if observe_usage:
                    snapshot, _ = await asyncio.to_thread(_remote_snapshot, host, read_only=True, observe_usage=True)
                else:
                    snapshot, _ = await asyncio.to_thread(_remote_snapshot, host, read_only=True)
            except _ReadOnlyUnsupported:
                unsupported.add(host)
                errors.pop(host, None)
            except ValueError, TypeError, OSError, subprocess.TimeoutExpired, WebInputError:
                errors[host] = "自動取得に失敗しました"
            else:
                unsupported.discard(host)
                remember_snapshot(host, snapshot)

    pending: dict[str, asyncio.Task[None]] = {}

    def start_remote(host: str, *, observe_usage: bool = False) -> asyncio.Task[None]:
        task = pending.get(host)
        if task is None:
            task = asyncio.create_task(read_remote(host, observe_usage))
            pending[host] = task
            task.add_done_callback(lambda _: pending.pop(host, None))
        return task

    async def refresh_remote(host: str, *, observe_usage: bool = False) -> None:
        await asyncio.shield(start_remote(host, observe_usage=observe_usage))

    async def select_remote(host: str, hosts: tuple[str, ...]) -> quart.Response:
        args = quart.request.args
        task = pending.get(host)
        current = snapshots.get(host)
        waiting = args.get("wait") == "1"
        retry = args.get("retry") == "1"
        if task is None and not waiting:
            known = host in unsupported or host in errors
            if retry or (not known and (current is None or current["fetched_at"] == args.get("after"))):
                task = start_remote(host)
        if task is not None and waiting:
            await asyncio.shield(task)
        state = (
            "pending"
            if task is not None and not task.done()
            else "legacy"
            if host in unsupported
            else "error"
            if host in errors or host not in snapshots
            else "ready"
        )
        return with_host_status(snapshots.get(host, {}), hosts, settings_state=state)

    async def refresh_remotes() -> None:
        shutdown = typing.cast(asyncio.Event, app.shutdown_event)
        while not shutdown.is_set():
            try:
                hosts = await asyncio.to_thread(_remote_hosts, read_only=True)
            except ValueError, OSError:
                app.logger.warning("自動更新の対象ホストを取得できません")
            else:
                await asyncio.gather(*(refresh_remote(host) for host in hosts if host not in unsupported))
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(shutdown.wait(), 60)

    background_started = False

    def start_refresh() -> None:
        nonlocal background_started
        if not background_started:
            background_started = True
            app.add_background_task(refresh_remotes)

    @app.get("/settings")
    async def settings_index() -> quart.Response:
        start_refresh()
        return no_store(
            render_index(app.config["PLANS_CONTEXT"], safe_base_path(quart.request.root_path), "settings"),
            "text/html; charset=utf-8",
        )

    @app.post("/api/settings/prepare")
    async def settings_prepare() -> quart.Response:
        start_refresh()
        return json_no_store({})

    @app.get("/api/settings")
    async def settings_get() -> quart.Response:
        try:
            host = await asyncio.to_thread(_request_host)
            if host and quart.request.args.get("selection") == "1":
                hosts = await asyncio.to_thread(_remote_hosts, read_only=True)
                return await select_remote(host, hosts)
            background = quart.request.args.get("background") == "1"
            observe_usage = background and quart.request.args.get("observe_usage") == "1"
            if observe_usage and not host:
                await asyncio.to_thread(usage_refresh.refresh_for_display)
            if observe_usage and host and snapshots.get(host, {}).get("can_observe_usage"):
                await refresh_remote(host, observe_usage=True)
            elif background and quart.request.args.get("retry") == "1":
                hosts = (host,) if host else await asyncio.to_thread(_remote_hosts, read_only=True)
                if not host:
                    hosts = tuple(name for name in hosts if name in errors)
                await asyncio.gather(*(refresh_remote(name) for name in hosts if name not in unsupported))
            if background and host:
                current = snapshots.get(host)
                if current is None or host in errors:
                    response = json_no_store({"error": errors.get(host, "設定を取得できません。")})
                    response.status_code = 503
                    return response
                hosts = await asyncio.to_thread(_remote_hosts, read_only=True)
                return with_host_status(current, hosts)
            if host and quart.request.args.get("cached") == "1" and host in snapshots:
                hosts = await asyncio.to_thread(_remote_hosts, read_only=True)
                cached = snapshots.get(host)
                if cached is not None and host in hosts:
                    return json_no_store({**cached, "cached": True, "hosts": hosts})
            async with locks.setdefault(host, asyncio.Lock()):
                if host:
                    snapshot, _ = await asyncio.to_thread(_remote_snapshot, host, read_only=True)
                    remember_snapshot(host, snapshot)
                else:
                    snapshot = await asyncio.to_thread(config.settings_snapshot, pathlib.Path.home(), read_only=True)
                    snapshot["listen"] = {
                        "serve_host": [serve.host, serve.host_source],
                        "serve_port": [serve.port, serve.port_source],
                    }
                hosts = await asyncio.to_thread(_remote_hosts, read_only=True)
                return with_host_status(snapshot, hosts)
        except _ReadOnlyUnsupported as error:
            raise web_input_error_from(
                ValueError("SSH先のatkが旧バージョンです"), next_action="SSH先のatkを更新する"
            ) from error
        except (ValueError, TypeError, OSError, subprocess.TimeoutExpired) as error:
            message = error if isinstance(error, ValueError) else ValueError("設定を読み込めませんでした")
            raise web_input_error_from(message, next_action="ホストの設定とSSHの接続を確認する") from error

    @app.post("/api/settings/shared-migration")
    async def settings_shared_migration() -> quart.Response:
        payload = await quart.request.get_json(silent=True)
        if not isinstance(payload, dict):
            raise WebInputError("JSONオブジェクトを指定してください", next_action="画面から差を確認し直す")
        try:
            host = await asyncio.to_thread(_request_host)
            async with locks.setdefault(host, asyncio.Lock()):
                if host and payload.get("action") == "apply":
                    snapshots.pop(host, None)
                result = await asyncio.to_thread(_shared_migration, host, payload)
        except (ValueError, TypeError, OSError, subprocess.TimeoutExpired) as error:
            raise web_input_error_from(error, next_action="差を確認し直してから移行する") from error
        return json_no_store(result)

    @app.post("/api/settings")
    async def settings_post() -> quart.Response:
        payload = await quart.request.get_json(silent=True)
        if not isinstance(payload, dict):
            raise WebInputError("JSONオブジェクトを指定してください", next_action="画面から値を入力して保存する")
        if payload.get("action") == "reset-reissue" or payload.get("key") in (
            *config_settings.AUTO_RESET_KEYS,
            config_settings.LEGACY_RESET_PERMISSIONS,
        ):
            raise WebInputError("設定画面ではリセット設定を変更できません", next_action="利用枠画面から変更する")
        try:
            host = await asyncio.to_thread(_request_host)
            revision = payload.pop("settings_revision", None)
            if host and revision is not None and host in pending:
                return _stale_write()
            async with locks.setdefault(host, asyncio.Lock()):
                if host:
                    if revision is not None and (
                        revision != snapshots.get(host, {}).get("fetched_at") or host in errors or host in unsupported
                    ):
                        return _stale_write()
                    # 失敗時も遠隔で変更が行われたか分からないため、保存前の値を再利用しない。 pyfltr: allow-comment
                    snapshots.pop(host, None)
                    await asyncio.to_thread(_remote_update, host, payload)
                else:
                    await asyncio.to_thread(config.update_settings, payload)
        except (ValueError, TypeError, OSError, subprocess.TimeoutExpired) as error:
            raise web_input_error_from(error, next_action="説明で受理される値を確認し、入力を直して保存する") from error
        return json_no_store({"saved": True})


def _stale_write() -> quart.Response:
    response = json_no_store({"error": "最新の設定を取得し直してから保存してください"})
    response.status_code = 409
    return response
