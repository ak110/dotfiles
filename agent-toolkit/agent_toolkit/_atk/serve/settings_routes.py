import asyncio
import base64
import datetime
import json
import pathlib
import re
import subprocess
import typing

import quart

from agent_toolkit._atk import config, config_migration, config_settings
from agent_toolkit._atk.serve.config import ServeConfig
from agent_toolkit._atk.serve.shell_routes import json_no_store, no_store, render_index, safe_base_path
from agent_toolkit._atk.wi.web_input import WebInputError, web_input_error_from
from agent_toolkit._common.process_tree import run_with_timeout


def _remote_hosts() -> tuple[str, ...]:
    config_migration.migrate_serve_settings()
    return config.parse_remote_hosts(config.resolve_mutable_setting("remote_hosts"))


def _ssh(host: str, operation: str, payload: dict[str, typing.Any] | None = None) -> subprocess.CompletedProcess[bytes]:
    # pyfltr: allow-comment Windowsのcmd.exeとPOSIXシェルで同じコマンドを使えるように、自由入力は標準入力で渡す。
    return run_with_timeout(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "--", host, operation],
        input=json.dumps(payload).encode("ascii") if payload is not None else b"",
        capture_output=True,
        timeout=20,
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
            ["ssh-unelevated", "--timeout", "20", "--", host, "-"],
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


def _remote_snapshot(host: str) -> tuple[dict[str, typing.Any], _ConfigRunner]:
    platform = _ssh(host, "uname -s")
    if platform.returncode == 255:
        raise WebInputError("SSH先に接続できません", next_action="接続先とSSHの接続を確認する")
    # pyfltr: allow-comment snapshotと旧版showは旧設定を移行するため、OSを判別してから実行する。
    runner: _ConfigRunner = (
        _ssh
        if platform.returncode == 0 and platform.stdout.strip() in {b"Linux", b"Darwin", b"FreeBSD", b"OpenBSD", b"NetBSD"}
        else _ssh_unelevated
    )
    result = runner(host, "atk config snapshot", None)
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
        raise ValueError("SSH先の設定を解釈できません。SSH先のatkの版を確認してください")
    return {"legacy": True, "settings": settings, "paths": paths}, runner


def _remote_update(host: str, payload: dict[str, typing.Any]) -> None:
    snapshot, runner = _remote_snapshot(host)
    action = payload.get("action")
    if action in ("set", "temporary", "array-add", "array-remove"):
        key = config.operation_text(payload, "key")
        if key is None or snapshot["settings"].get(key, {"read_only": True}).get("read_only"):
            raise ValueError("このホストで変更できる設定を選んでください")
    if not snapshot.get("legacy"):
        result = runner(host, "atk config update", payload)
    else:
        key, value = config.operation_text(payload, "key"), config.operation_text(payload, "value")
        # pyfltr: allow-comment 旧版は標準入力に非対応のため、cmd.exe・POSIXシェルの両方で引用が不要な1引数に限る。
        if action != "set" or key is None or value is None or not _LEGACY_TOKEN.fullmatch(value):
            raise ValueError("この値を設定画面から保存するにはSSH先のatkを更新してください")
        config_settings.validate_mutable_setting(key, value)
        result = runner(host, f"atk config set {key} {value}", None)
    if runner is _ssh_unelevated and result.returncode == 124:
        raise ValueError("保存処理の結果を確認できません。SSH先の設定を読み直してから再試行してください")
    if result.returncode != 0:
        raise ValueError("SSH先のatkが設定を保存できませんでした。SSH先で設定と値の書式を確認してください")


def _request_host() -> str:
    host = quart.request.args.get("host", "")
    if host and host not in _remote_hosts():
        raise ValueError("ローカル設定のremote_hostsに登録したホストを選んでください")
    return host


def register_settings_routes(app: quart.Quart, serve: ServeConfig) -> None:
    locks: dict[str, asyncio.Lock] = {}
    snapshots: dict[str, dict[str, typing.Any]] = {}

    @app.get("/settings")
    async def settings_index() -> quart.Response:
        return no_store(
            render_index(app.config["PLANS_CONTEXT"], safe_base_path(quart.request.root_path), "settings"),
            "text/html; charset=utf-8",
        )

    @app.get("/api/settings")
    async def settings_get() -> quart.Response:
        try:
            host = await asyncio.to_thread(_request_host)
            if host and quart.request.args.get("cached") == "1" and host in snapshots:
                hosts = await asyncio.to_thread(_remote_hosts)
                cached = snapshots.get(host)
                if cached is not None and host in hosts:
                    return json_no_store({**cached, "cached": True, "hosts": hosts})
            async with locks.setdefault(host, asyncio.Lock()):
                if host:
                    snapshot, _ = await asyncio.to_thread(_remote_snapshot, host)
                    snapshot["fetched_at"] = datetime.datetime.now(datetime.UTC).isoformat()
                    snapshots[host] = snapshot
                else:
                    snapshot = await asyncio.to_thread(config.settings_snapshot, pathlib.Path.home())
                    snapshot["listen"] = {
                        "serve_host": [serve.host, serve.host_source],
                        "serve_port": [serve.port, serve.port_source],
                    }
                hosts = await asyncio.to_thread(_remote_hosts)
                return json_no_store({**snapshot, "hosts": hosts})
        except (ValueError, TypeError, OSError, subprocess.TimeoutExpired) as error:
            message = error if isinstance(error, ValueError) else ValueError("設定を読み込めませんでした")
            raise web_input_error_from(message, next_action="ホストの設定とSSHの接続を確認する") from error

    @app.post("/api/settings")
    async def settings_post() -> quart.Response:
        payload = await quart.request.get_json(silent=True)
        if not isinstance(payload, dict):
            raise WebInputError("JSONオブジェクトを指定してください", next_action="画面から値を入力して保存する")
        try:
            host = await asyncio.to_thread(_request_host)
            async with locks.setdefault(host, asyncio.Lock()):
                if host:
                    # 失敗時も遠隔で変更が行われたか分からないため、保存前の値を再利用しない。 pyfltr: allow-comment
                    snapshots.pop(host, None)
                    await asyncio.to_thread(_remote_update, host, payload)
                else:
                    await asyncio.to_thread(config.update_settings, payload)
        except (ValueError, TypeError, OSError, subprocess.TimeoutExpired) as error:
            raise web_input_error_from(error, next_action="説明で受理される値を確認し、入力を直して保存する") from error
        return json_no_store({"saved": True})
