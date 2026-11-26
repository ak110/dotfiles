from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
import uuid
from collections.abc import Callable
from typing import Any, cast

lazy import platformdirs

from agent_toolkit._atk import config_migration as _config_migration
from agent_toolkit._atk import config_repositories_cli, config_shared_migration, config_usage
from agent_toolkit._atk import config_settings as _config_settings
from agent_toolkit._atk import help_text as _atk_help
from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._atk.config_migration import migrate_serve_settings
from agent_toolkit._atk.config_resolution import (
    connection_order,
    connections_configured,
    mutable_setting_source,
    resolve_mutable_setting,
)
from agent_toolkit._atk.config_settings import (
    CONNECTION_KINDS,
    MODEL_TYPES,
    ConfiguredRepository,
    atomic_write,
    config_environment_variable_names,
    connection_order_key,
    mutable_setting_default,
    parse_remote_hosts,
    process_loop_configuration_path,
    repository_branch_settings,
    resolve_configured_repositories,
    serve_default_port,
    target_key,
)
from agent_toolkit._atk.config_settings import MUTABLE_KEY_DEFAULTS as _MUTABLE_KEY_DEFAULTS
from agent_toolkit._atk.config_settings import RETIRED_CONNECTION_KEYS as _RETIRED_CONNECTION_KEYS
from agent_toolkit._atk.config_settings import config_env_name as _config_env_name
from agent_toolkit._atk.config_settings import config_file_path as _config_file_path
from agent_toolkit._atk.environment import is_human_terminal
from agent_toolkit._common import codex_fast_mode, codex_models, runtime_environment, usage_observation, usage_settings
from agent_toolkit._common import next_action as _next_action
from agent_toolkit._common import private_notes as _private_notes
from agent_toolkit._common import state_paths as _state_paths
lazy from agent_toolkit._atk import codex_provider_check, git_sync, reset_reservations
lazy from agent_toolkit._git import command as git_command
lazy from agent_toolkit._git import remote as git_remote

__all__ = [
    "CONNECTION_KINDS",
    "ConfiguredRepository",
    "MODEL_TYPES",
    "_MUTABLE_KEY_DEFAULTS",
    "_RETIRED_CONNECTION_KEYS",
    "_config_env_name",
    "_config_file_path",
    "atomic_write",
    "config_environment_variable_names",
    "connection_order",
    "connection_order_key",
    "connections_configured",
    "migrate_serve_settings",
    "mutable_setting_default",
    "parse_remote_hosts",
    "process_loop_configuration_path",
    "repository_branch_settings",
    "resolve_configured_repositories",
    "resolve_mutable_setting",
    "serve_default_port",
    "target_key",
]


def _shared_write_path() -> pathlib.Path | None:
    path = _config_settings.shared_settings_path()
    return path if path is not None and git_sync.has_remote(path.parent) else None


def save_mutable_settings(updates: dict[str, str]) -> bool:
    """変更可能設定の複数キーを検証して保存し、保存内容が変わったかを返す。

    他のキーと他用途の設定は保持する。値が既存の保存値と同じキーだけなら書き込まない。
    統一設定の破損と値の不正は`ValueError`（`ActionableError`を含む）、書込失敗は`OSError`で送出する。
    """
    for key, value in updates.items():
        if key not in _config_settings.MUTABLE_KEY_DEFAULTS:
            raise KeyError(key)
        _config_settings.validate_mutable_setting(key, value)
    updates = {key: _config_settings.normalize_setting(key, value) for key, value in updates.items()}
    shared = {key: value for key, value in updates.items() if key in _config_settings.SHARED_SETTING_KEYS}
    path = _shared_write_path() if shared else None
    shared_changed = False
    if path is not None:
        shared_changed = _save_shared_settings(path, shared)
        updates = {key: value for key, value in updates.items() if key not in shared}
    config = _config_settings.load_config_strict()
    if all(config.get(key) == value for key, value in updates.items()):
        return shared_changed
    _config_settings.save_config({**config, **updates})
    return True


def _save_shared_settings(path: pathlib.Path, updates: dict[str, str]) -> bool:
    def update(current: dict[str, str]) -> dict[str, str]:
        changed = current | updates
        key = reset_reservations.PERMISSIONS
        if key in updates:
            changed[key] = reset_reservations.mark_new_permissions(current, updates[key])
        return changed

    return config_shared_migration.update_shared_settings(path, update)


def _reissue_remaining(notes: pathlib.Path, grant: dict[str, Any]) -> int:
    identifier = grant["id"]
    known = reset_reservations.local_unshared_usage(_state_paths.state_dir(), identifier) + reset_reservations.read_used(
        notes, identifier
    )
    return max(0, int(grant["count"]) - known)


def _legacy_reset_grant(grant: dict[str, Any]) -> bool:
    return (
        grant["engine"] == "codex"
        and grant.get(reset_reservations.ISSUED) is not True
        and grant.get(reset_reservations.RETIRED) is not True
    )


def _reissue_reset(identifier: str, count: int | None) -> None:
    path = config_shared_migration.write_path()

    def update(current: dict[str, str]) -> dict[str, str]:
        entries = reset_reservations.permission_entries(current)
        for index, grant in enumerate(entries):
            if grant["id"] != identifier or not _legacy_reset_grant(grant):
                continue
            try:
                remaining = _reissue_remaining(path.parent, grant)
            except (ValueError, OSError) as error:
                raise _next_action.ActionableError(
                    "この端末で確認できるreset許可の残りを数えられません",
                    next_action="端末のCodex reset記録と共有台帳を確認してから発行し直す",
                ) from error
            if count is not None and not 0 <= count <= remaining:
                raise ValueError(f"共有許可は確認できた残りの{remaining}回以内で発行してください")
            entries[index] = reset_reservations.retire_grant(grant)
            issued = dict(grant, id=f"codex-{uuid.uuid4()}", count=remaining if count is None else count)
            entries.append(issued | {reset_reservations.ISSUED: True})
            return current | {reset_reservations.PERMISSIONS: reset_reservations.encode_permissions(entries)}
        raise ValueError("共有へ移行した旧Codex許可を指定してください")

    config_shared_migration.update_shared_settings(path, update)


def _reset_reissue_options(path: pathlib.Path, shared: dict[str, str]) -> dict[str, int | None]:
    options: dict[str, int | None] = {}
    for grant in reset_reservations.permission_entries(shared):
        if _legacy_reset_grant(grant):
            try:
                options[grant["id"]] = _reissue_remaining(path.parent, grant)
            except ValueError, OSError:
                options[grant["id"]] = None
    return options


def resolved_settings(home: pathlib.Path, *, read_only: bool = False) -> dict[str, str]:
    _config_migration.check_legacy_environment()
    if not read_only:
        _config_migration.migrate_legacy_settings()
        _config_migration.migrate_serve_settings()
    return {
        **_setting_paths(home),
        **{key: resolve_mutable_setting(key, read_only=True) for key in _config_settings.MUTABLE_KEY_DEFAULTS},
    }


def _setting_paths(home: pathlib.Path) -> dict[str, str]:
    # Windowsでappnameがappauthorとしても付与される二重階層を防ぐ。
    return {
        "config_dir": str(_config_settings.config_dir()),
        "state_dir": str(_state_paths.state_dir()),
        "data_dir": str(pathlib.Path(platformdirs.user_data_dir("agent-toolkit", appauthor=False))),
        "private_notes": str(_private_notes.default_private_notes(home)),
    }


_UNKNOWN_CANDIDATE_NEXT_ACTION = "意図した名前か確認し、誤記なら同じキーへ`atk config set <KEY> <VALUE>`で設定し直す"

"""参考一覧外のモデル候補の警告に添える次の操作。"""


def _stage_model_candidate_warnings(key: str, value: str) -> list[str]:
    warnings: list[str] = []
    if key not in _config_settings.MODEL_DEFAULTS:
        return warnings
    candidates = value.split(",")
    for candidate in candidates:
        engine, model, effort = _parse_stage_model(candidate)
        shown = candidate
        if engine == "opencode":
            continue
        models = ", ".join(sorted(_config_settings.KNOWN_MODELS[engine]))
        if model not in _config_settings.KNOWN_MODELS[engine]:
            warnings.append(f"設定キー`{key}`の候補`{shown}`のモデル名`{model}`は主に使うモデルの一覧（{models}）にありません")
        if effort is not None and effort not in _config_settings.KNOWN_EFFORTS:
            efforts = ", ".join(sorted(_config_settings.KNOWN_EFFORTS))
            warnings.append(f"設定キー`{key}`の候補`{shown}`のeffort`{effort}`は主に使う値の一覧（{efforts}）にありません")
    return warnings


def _cmd_config_show(home: pathlib.Path) -> int:
    human = is_human_terminal()
    stored = _config_settings.load_config_strict()
    local, errors = _config_migration.preview_local_settings(stored)
    settings = _setting_paths(home)
    incomplete = False
    for key in _MUTABLE_KEY_DEFAULTS:
        try:
            settings[key] = resolve_mutable_setting(key, read_only=True, local=local, local_error=errors.get(key))
        except ValueError as error:
            incomplete = True
            action = (
                error.next_action
                if isinstance(error, _next_action.ActionableError)
                else f"設定ファイルで{key}に関係する値を直して一覧を再表示する"
            )
            if error is errors.get(key) and isinstance(error, _next_action.ActionableError):
                action += "。設定を変更するコマンドは、旧キーの置換と設定の保存を行うことがある"
            _outcome.report_warning(f"{key}の実効値を表示できない: {error}", next_action=action)
    for error in dict.fromkeys(error for key, error in errors.items() if key not in _MUTABLE_KEY_DEFAULTS):
        incomplete = True
        action = (
            error.next_action if isinstance(error, _next_action.ActionableError) else "該当する旧環境変数を解除して再実行する"
        )
        _outcome.report_warning(str(error), next_action=action)
    for key, value in settings.items():
        print(f"{key} {value}")
    if (path := _config_settings.shared_settings_path()) is not None:
        shared = _config_settings.load_shared_settings(path)
        temporary = usage_settings.temporary_settings()
        active = [
            key
            for key in sorted(shared)
            if key in settings
            and key not in local
            and key not in temporary
            and not runtime_environment.environment().get(_config_env_name(key))
        ]
        print(f"共有設定ファイル: {path}", file=sys.stderr)
        if active:
            print("共有設定から解決したキー: " + "、".join(active), file=sys.stderr)
        if git_sync.has_remote(path.parent) and _pending_shared(stored):
            print(f"未移行の共有設定がある。{_MIGRATION_NEXT_ACTION}。", file=sys.stderr)
    if human and settings.get("codex_fast_mode") == "auto":
        print(_auto_fast_report(), file=sys.stderr)
    return 2 if incomplete else 0


def _fast_start_snapshot() -> dict[str, str] | None:
    previous = codex_fast_mode.latest_start_decision()
    if previous is None:
        return None
    instant, decision = previous
    return {
        "at": instant,
        "speed": "高速" if decision.tier == "priority" else "通常",
        "reason": decision.explanation,
        "source": {"current": "今回確認", "previous": "直前の成功判定", "unavailable": "確認不能", "legacy": "出所の記録なし"}[
            decision.source
        ],
    }


def _auto_fast_report() -> str:
    start = _fast_start_snapshot()
    if start is None:
        return "Codex自動速度: 起動時判定の記録なし"
    return f"Codex自動速度: 最後の起動時判定 {start['speed']}（{start['source']}） — {start['at']}、{start['reason']}"


def _pending_shared(local: dict[str, Any]) -> dict[str, Any]:
    return config_shared_migration.pending(local)


_MIGRATION_NEXT_ACTION = config_shared_migration.NEXT_ACTION


def _require_shared_migration() -> None:
    config_shared_migration.require_migrated()


def _print_shared_value(key: str, label: str, value: Any) -> None:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    if key in _config_settings.MODEL_DEFAULTS:
        text = text.replace(",", ",\n      ")
    print(f"  {label}: {text}")


def _print_shared_comparison(plan: dict[str, Any]) -> None:
    print("共有設定の比較")
    print("初回: 共有設定ファイルがない" if plan["initial"] else "既存の共有設定ファイルと比較")
    labels = {"equal": "一致", "different": "差あり", "local_only": "共有に無い"}
    for item in plan["items"]:
        key = item["key"]
        print(f"\n{item['label']}（{labels[item['kind']]}）")
        _print_shared_value(key, "この端末の値", item["local"])
        shared = item["shared"]
        if shared is None:
            shared = "未保存" if item["shared_effective"] is None else f"未保存（初期値 {item['shared_effective']}）"
        _print_shared_value(key, "共有の値", shared)
        if item["error"]:
            print(f"  この端末の値は移行できない: {item['error']}")
        if item["kind"] == "different":
            print(f"    --keep-local {key} / --keep-shared {key}")
    if not plan["pending"]:
        print("\n移行するローカル共有設定はない。適用は不要。")
        return
    if plan["invalid"]:
        print("\n移行できないローカル値を直してから、もう一度比較する。")
        return
    print("\n環境変数や一時指定があれば、移行後もその値が優先される。")
    command = f"atk config migrate-shared --apply {plan['revision']}"
    command += "".join(f" --keep-shared {key}" for key in plan["required"])
    print("設定画面の「共有へ移行」でも同じ移行を行える。CLIでは次のコマンドで適用する。")
    print(f"  {command}")
    if plan["required"]:
        print("この端末の値を残すキーだけ --keep-shared を --keep-local に変える。選んだ値は他の端末にも同期する。")


def _migrate_shared_settings(args: argparse.Namespace) -> None:
    if args.json and args.apply is not None:
        raise ValueError("--jsonは比較だけの実行で指定する")
    keep_local, keep_shared = args.keep_local, args.keep_shared
    choices = dict.fromkeys(keep_local, "local") | dict.fromkeys(keep_shared, "shared")
    if len(choices) != len(keep_local) + len(keep_shared) or (choices and args.apply is None):
        raise ValueError("選択は重複させず、--applyと併用してください")
    if args.apply is None:
        plan = config_shared_migration.compare()
        if args.json or not is_human_terminal(sys.stdout):
            print(json.dumps(plan, ensure_ascii=False))
        else:
            _print_shared_comparison(plan)
        return
    moved = config_shared_migration.apply(args.apply, cast(dict[str, config_shared_migration.Choice], choices))
    if not moved:
        _outcome.report_success("移行するローカル共有設定はない")
        return
    _outcome.report_success(f"共有設定への移行を確認した: {moved}件")


def _report_candidate_warnings(warnings: list[str]) -> None:
    if warnings:
        message = "\n".join([warnings[0], *(f"{_outcome.WARNING_PREFIX}{warning}" for warning in warnings[1:])])
        _outcome.report_warning(message, next_action=_UNKNOWN_CANDIDATE_NEXT_ACTION)


def _shared_settings_identity(path: pathlib.Path) -> str | None:
    directory = path.parent
    branch = git_command.optional_stdout(["symbolic-ref", "-q", "HEAD"], directory)
    if not branch:
        return None
    upstream = git_command.optional_stdout(
        ["for-each-ref", "--format=%(upstream:remotename)%00%(upstream:remoteref)", branch.strip()], directory
    )
    if not upstream:
        return None
    remote, _, ref = upstream.rstrip("\n").partition("\0")
    if not remote or remote == "." or not ref:
        return None
    url = git_command.optional_stdout(["remote", "get-url", "--push", "--", remote], directory)
    if not url:
        return None
    try:
        location = git_remote.parse_remote_location(url)
    except ValueError:
        return None
    if not location.hostname or location.protocol not in (None, "http", "https", "ssh"):
        return None
    default_port = {None: 22, "ssh": 22, "http": 80, "https": 443}[location.protocol]
    endpoint = None if location.port in (None, default_port) else (location.protocol, location.port)
    identity = (location.hostname.lower(), endpoint, location.project_path, ref, path.name)
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()


def settings_snapshot(home: pathlib.Path, *, read_only: bool = False) -> dict[str, Any]:
    effective = resolved_settings(home, read_only=read_only)
    saved = _config_settings.load_config()
    shared_path = _config_settings.shared_settings_path()
    shared = _config_settings.load_shared_settings(shared_path) if shared_path is not None else {}
    shared_sync = shared_path is not None and git_sync.has_remote(shared_path.parent)
    temporary = usage_settings.temporary_settings()
    pending = config_shared_migration.pending(saved) if shared_sync else {}
    settings = {}
    for key, default in _config_settings.MUTABLE_KEY_DEFAULTS.items():
        shared_key = shared_sync and key in _config_settings.SHARED_SETTING_KEYS
        migration_pending = key in pending
        source = _mutable_source(key, saved, shared, temporary, shared_target=shared_key)
        description = _config_settings.setting_description(key)
        saved_value = shared.get(key, default) if shared_key else saved.get(key, default)
        if key == reset_reservations.PERMISSIONS and isinstance(saved_value, str):
            saved_value = reset_reservations.public_permissions(saved_value)
        settings[key] = {
            "effective": effective.pop(key),
            "saved": saved_value,
            "default": default,
            "source": source,
            "shared": shared_key,
            "shared_sync": shared_key and shared_sync,
            "migration_pending": migration_pending,
            "description": description,
            "format": _config_settings.setting_format(key),
            "choices": _config_settings.SETTING_CHOICES.get(key),
            "array": key in _config_settings.ARRAY_KEYS,
            "temporary": config_usage.allowed_temporary_key(key),
            "host_reason": _config_settings.HOST_SETTING_REASONS.get(key),
        }
    usage = usage_observation.snapshot(include_history=True)
    state = usage_settings.read_settings()
    windows = config_usage.profile_windows(usage)
    return {
        "settings": settings,
        "codex_fast_start": _fast_start_snapshot(),
        "shared_identity": _shared_settings_identity(shared_path) if shared_path is not None and shared_sync else None,
        "paths": effective,
        "presets": _config_settings.PRESET_MODELS,
        "temporary": {"values": temporary, "until": state.get("until")},
        "repositories": _config_settings.repository_settings(check_paths=False),
        "shared_migration": {"format": config_shared_migration.FORMAT, "pending": sorted(pending)},
        "provider_warnings": codex_provider_check.provider_warnings(
            {key: str(settings[key]["effective"]) for key in sorted(_config_settings.PROVIDER_KEYS)},
            runtime_environment.environment(),
        ),
        "model_candidates": {engine: sorted(names) for engine, names in _config_settings.KNOWN_MODELS.items()},
        "usage": config_usage.cli_windows(windows),
        "can_observe_usage": True,
        "reset_editor": config_usage.reset_editor_snapshot(windows, usage["history"], state),
        "reset_reissue": _reset_reissue_options(shared_path, shared) if shared_sync and shared_path is not None else {},
        "model_order_policy": config_usage.MODEL_ORDER_POLICY,
    }


def operation_text(payload: dict[str, Any], name: str, *, optional: bool = False) -> str | None:
    value = payload.get(name)
    if optional and value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name}は文字列で指定してください")
    return value


def update_settings(payload: dict[str, Any]) -> None:
    action = payload.get("action")
    mutable_write = action in ("set", "array-add", "array-remove")
    key = operation_text(payload, "key") if mutable_write else None
    if (action == "preset" or (mutable_write and key in _config_settings.SHARED_SETTING_KEYS)) and (
        _shared_write_path() is not None
    ):
        _require_shared_migration()
    _config_migration.migrate_serve_settings()
    _config_migration.migrate_legacy_settings()
    if action == "reset-reissue":
        identifier, count = operation_text(payload, "id"), payload.get("count")
        if not identifier or (count is not None and (not isinstance(count, int) or isinstance(count, bool))):
            raise ValueError("旧許可のIDと発行する回数を指定してください")
        _reissue_reset(identifier, count)
        return
    if action == "temporary-clear":
        config_usage.update_temporary(None, None, None)
        return
    if action == "preset":
        preset = operation_text(payload, "preset")
        if preset not in _config_settings.PRESET_MODELS:
            raise ValueError("登録済みのプリセットを選んでください")
        save_mutable_settings(_config_settings.PRESET_MODELS[preset])
        return
    if action == "repo":
        operation = operation_text(payload, "operation")
        repo = operation_text(payload, "repo")
        if operation not in ("add", "set", "remove") or not repo:
            raise ValueError("登録操作とリポジトリを指定してください")
        fields = ("edit", "path", "platforms", "branches", "default_branch", "merge_from")
        values = {name: operation_text(payload, name, optional=True) for name in fields}
        values["github_issue_trusted_authors"] = operation_text(payload, "github_issue_trusted_authors", optional=True)
        github_issues = payload.get("github_issues")
        if github_issues is not None and not isinstance(github_issues, bool):
            raise ValueError("GitHubのIssueとPRを取り込むかどうかを指定してください")
        process_loop = payload.get("process_loop")
        if process_loop is not None and not isinstance(process_loop, bool):
            raise ValueError("process-loopの対象かどうかを指定してください")
        if operation == "add" and not values["edit"]:
            raise ValueError("編集可否を指定してください")
        config_repositories_cli.update_repositories(
            argparse.Namespace(
                repos_subcommand=operation, repo=repo, process_loop=process_loop, github_issues=github_issues, **values
            )
        )
        return
    key, value = operation_text(payload, "key"), operation_text(payload, "value")
    if key is None or value is None or key not in _config_settings.MUTABLE_KEY_DEFAULTS:
        raise ValueError("変更可能な設定を選んでください")
    if action == "set":
        save_mutable_settings({key: value})
        _verify_saved_setting(key, _config_settings.normalize_setting(key, value))
    elif action == "temporary":
        config_usage.update_temporary(key, value, operation_text(payload, "until", optional=True))
    elif action in ("array-add", "array-remove") and key in _config_settings.ARRAY_KEYS:
        updated = array_setting_update(key, value, remove=action == "array-remove")
        if updated is not None:
            save_mutable_settings({key: updated})
    else:
        raise ValueError("設定の操作が不正です")


def _legacy_key_guidance(keys: list[str]) -> str:
    guidance = ""
    if "process_loop_repositories" in keys:
        guidance += "。process_loop_repositoriesは廃止した。`atk config repos`でrepositoriesを変更する"
    if "codex_auto_reset" in keys:
        guidance += "。codex_auto_resetは廃止した。明示許可はusage_reset_permissionsで確認する"
    if "usage_auto_allocation" in keys:
        guidance += "。usage_auto_allocationは廃止した。候補順は工程別の`*_model`キーで指定する"
    if any(key in _config_settings.LEGACY_REMOTE_HOSTS_KEYS for key in keys):
        guidance += "。計画用とセッション用のSSH接続先はremote_hostsへ統合した"
    if any(key in _config_settings.RETIRED_SERVE_HOME_KEYS for key in keys):
        guidance += "。セッションの参照先はCLAUDE_CONFIG_DIRまたはCODEX_HOMEで指定する"
    if any(key in _config_settings.RETIRED_MODEL_KEYS for key in keys):
        guidance += "。旧モデル設定キーは廃止した。対応する工程別の`*_model`キーを指定する"
    for key in keys:
        if key in _config_settings.RETIRED_FIXED_KEYS:
            guidance += f"。旧設定キー{key}は廃止した: {_config_settings.RETIRED_FIXED_KEYS[key]}"
        if key in _config_settings.RETIRED_RESEARCH_KEYS:
            guidance += f"。旧調査設定キー{key}は廃止した。変更先: {_config_settings.RETIRED_RESEARCH_KEYS[key]}"
        if key in _config_settings.RETIRED_CONNECTION_KEYS:
            guidance += f"。旧接続設定キー{key}は廃止した。移行先: {', '.join(_config_settings.RETIRED_CONNECTION_KEYS[key])}"
    return guidance


def _cmd_config_get(args: argparse.Namespace, home: pathlib.Path) -> None:
    requested_keys = cast(list[str], args.key)
    settings = resolved_settings(home)
    unknown_keys = [key for key in requested_keys if key not in settings]
    if unknown_keys:
        _outcome.report_failure(
            f"未知の設定キーを指定した: {', '.join(unknown_keys)}{_legacy_key_guidance(unknown_keys)}",
            next_action=f"利用可能なキーから選び直す: {', '.join(settings)}",
        )
        sys.exit(2)
    for key in requested_keys:
        print(settings[key])


def _cmd_config_set(args: argparse.Namespace) -> None:
    if args.key not in _config_settings.MUTABLE_KEY_DEFAULTS:
        _outcome.report_failure(
            f"変更できない設定キーを指定した: {args.key}{_legacy_key_guidance([args.key])}",
            next_action=f"変更可能なキーから選び直す: {', '.join(_config_settings.MUTABLE_KEY_DEFAULTS)}",
        )
        sys.exit(2)
    try:
        _config_settings.validate_mutable_setting(args.key, args.value)
    except ValueError as error:
        _outcome.report_failure(
            f"設定値の書式が不正である。{error}",
            next_action=f"受理可能書式の値で`atk config set {args.key} <VALUE>`を再実行する",
        )
        sys.exit(2)
    shared_target = args.key in _config_settings.SHARED_SETTING_KEYS and _shared_write_path() is not None
    if shared_target:
        _require_shared_migration()
    _report_candidate_warnings(_stage_model_candidate_warnings(args.key, args.value))

    _config_migration.migrate_serve_settings()
    value = _config_settings.normalize_setting(args.key, args.value)
    if not shared_target:
        config = _config_migration.plan_legacy_migration(_config_settings.load_config_strict(), updates={args.key: value})
        _config_settings.save_config(config)
    else:
        save_mutable_settings({args.key: value})
    _verify_saved_setting(args.key, value)
    _outcome.report_success(f"設定を更新した: {args.key}={value}")


def _verify_saved_setting(key: str, value: str) -> None:
    effective = resolve_mutable_setting(key)
    if effective == value:
        return
    if key == reset_reservations.PERMISSIONS and (
        json.loads(effective) == json.loads(reset_reservations.public_permissions(value))
    ):
        return
    local = _config_settings.load_config()
    path = _config_settings.shared_settings_path()
    shared = _config_settings.load_shared_settings(path) if path is not None else {}
    shared_target = key in _config_settings.SHARED_SETTING_KEYS and _shared_write_path() is not None
    source = _mutable_source(key, local, shared, usage_settings.temporary_settings(), shared_target=shared_target)
    if source == "一時指定":
        next_action = f"atk config temporary clear {key}で一時指定を解除して、atk config get {key}で確かめる"
    elif source == "移行前のローカル保存値":
        next_action = f"{_config_file_path()}の設定を確認する。{_MIGRATION_NEXT_ACTION}"
    elif source.startswith(_config_settings.CONFIG_ENV_PREFIX):
        next_action = f"環境変数{source}を解除し、atk config get {key}で確かめる"
    else:
        next_action = f"atk config snapshotで設定元を確認し、atk config get {key}で現在値を確かめる"
    raise _next_action.ActionableError(
        f"{key}の指定値は保存済みだが、実効値は別の設定を優先している: {source}", next_action=next_action
    )


def _mutable_source(
    key: str, local: dict[str, Any], shared: dict[str, str], temporary: dict[str, str], *, shared_target: bool
) -> str:
    env = _config_settings.config_env_name(key)
    source = (
        _config_migration.remote_hosts_environment()[1]
        if key == "remote_hosts"
        else env
        if runtime_environment.environment().get(env)
        else ""
    )
    return mutable_setting_source(
        key, environment_source=source, temporary=temporary, local=local, shared=shared, shared_target=shared_target
    )


def _cmd_config_apply_preset(args: argparse.Namespace) -> None:
    if args.preset in (None, _config_settings.APPLY_PRESET_SHOW):
        for preset, preset_settings in _config_settings.PRESET_MODELS.items():
            print(f"{preset}:")
            for key, value in preset_settings.items():
                print(f"  {key}: {value}")
        return
    settings = _config_settings.PRESET_MODELS[args.preset]
    if _shared_write_path() is None:
        config = _config_migration.plan_legacy_migration(_config_settings.load_config(), updates=settings)
        _config_settings.save_config(config)
    else:
        save_mutable_settings(settings)
    _outcome.report_success(f"工程別モデル設定をpreset「{args.preset}」で一括保存した: {len(settings)}件")
    for key, value in settings.items():
        print(f"{key}: {value}")


def _parse_stage_model(value: str) -> tuple[str, str, str | None]:
    engine, _, rest = value.partition(":")
    if engine == "opencode":
        model, explicit, effort = rest.rpartition("@")
        if explicit:
            return engine, model, effort
        return engine, rest, None
    model, effort_sep, effort = rest.partition("/")
    return engine, model, effort if effort_sep else None


def parse_stage_model_candidates(value: str) -> list[tuple[str, str, str]]:
    _config_settings.validate_stage_model_candidates(value)
    return [
        (engine, model, effort or "medium")
        for engine, model, effort in (_parse_stage_model(candidate) for candidate in value.split(","))
    ]


def parse_unresolved_model_candidates(model_type: str, *, exec_lane: bool = False) -> list[tuple[str, str, str]]:
    if model_type in _config_settings.MODEL_TYPES:
        key = f"{model_type}_model"
        if exec_lane and model_type == "execute":
            fallback = _config_settings.MODEL_DEFAULTS[key].replace("codex:sol/low", "codex:sol/medium")
            value = resolve_mutable_setting(key, fallback=fallback)
        else:
            value = resolve_mutable_setting(key)
        return parse_stage_model_candidates(value)
    try:
        return parse_stage_model_candidates(model_type)
    except ValueError as error:
        available = list(_config_settings.MODEL_TYPES)
        raise ValueError(
            f"unknown model_type: {model_type} "
            f"(available: {', '.join(available)}; or pass candidates like codex:gpt-6-sol/medium)"
        ) from error


def resolve_catalog(
    candidates: list[tuple[str, str, str]], *, catalog: list[dict[str, Any]] | None = None
) -> list[tuple[str, str, str]]:
    """候補中のCodex系列名を実行時の完全IDへ解決する。"""
    if codex_models.needs_catalog(candidates):
        active_catalog = catalog if catalog is not None else codex_models.list_models()
        return codex_models.resolve_candidates(candidates, active_catalog)
    return candidates


def resolve_model_candidates(model_type: str, *, catalog: list[dict[str, Any]] | None = None) -> list[tuple[str, str, str]]:
    """Codex系列名を実行時の完全IDへ解決して工程別の候補を返す。"""
    return resolve_catalog(parse_unresolved_model_candidates(model_type), catalog=catalog)


def build_parser(config: argparse.ArgumentParser) -> None:
    sub = _atk_help.add_subcommands(config, dest="config_subcommand", required=False)
    config_usage.build_parser(sub)
    _atk_help.add_command(sub, "show", **_atk_help.HELP["atk config show"])
    _atk_help.add_command(sub, "list", **_atk_help.HELP["atk config list"])
    for name in ("snapshot", "update"):
        parser = _atk_help.add_command(sub, name, **_atk_help.HELP[f"atk config {name}"])
        if name == "snapshot":
            parser.add_argument("--read-only", action="store_true", help="設定を移行または保存せずに読み取る。")
            parser.add_argument("--observe-usage", action="store_true", help="読み取り専用で利用枠の観測も更新する。")
    for name in ("add", "remove"):
        array = _atk_help.add_command(sub, name, **_atk_help.HELP[f"atk config {name}"])
        array.add_argument("key", metavar="KEY", help="JSON配列型の設定キー。")
        array.add_argument("value", metavar="VALUE", help="追加・削除する要素1件。")
    config_repositories_cli.build_parser(sub)
    migrate = _atk_help.add_command(sub, "migrate-shared", **_atk_help.HELP["atk config migrate-shared"])
    migrate.add_argument("--json", action="store_true", help="比較の結果をJSONで表示する")
    migrate.add_argument("--apply", metavar="REVISION", help="比較で取得した版を指定して適用する。")
    migrate.add_argument("--keep-local", action="append", default=[], metavar="KEY", help="手元の値を全ホストで使う。")
    migrate.add_argument("--keep-shared", action="append", default=[], metavar="KEY", help="共有側の値を使う。")
    reissue = _atk_help.add_command(sub, "reissue-reset", **_atk_help.HELP["atk config reissue-reset"])
    reissue.add_argument("permission_id", metavar="ID", help="共有へ移した旧Codex許可のID。")
    reissue.add_argument("--count", type=int, help="新しい許可の回数。省略するとこの端末で確認できる残りの回数。")
    get = _atk_help.add_command(sub, "get", **_atk_help.HELP["atk config get"])
    get.add_argument("key", metavar="KEY", nargs="+", help="取得する1件以上のキー（config showの出力キーと同一）。")
    set_ = _atk_help.add_command(sub, "set", **_atk_help.HELP["atk config set"])
    set_.add_argument("key", metavar="KEY", help=f"変更可能なキー: {', '.join(_config_settings.MUTABLE_KEY_DEFAULTS)}")
    set_.add_argument("value", metavar="VALUE", help="設定する値。モデルの複数候補はASCIIカンマ区切りで指定する。")
    apply_preset = _atk_help.add_command(sub, "apply-preset", **_atk_help.HELP["atk config apply-preset"])
    apply_preset.add_argument(
        "preset",
        nargs="?",
        choices=(_config_settings.APPLY_PRESET_SHOW, *_config_settings.PRESET_MODELS),
        help=f"適用するプリセット名。`{_config_settings.APPLY_PRESET_SHOW}`または省略で全プリセットの値を保存せずに表示する。",
    )


def dispatch(
    args: argparse.Namespace,
    home: pathlib.Path,
    *,
    refresh: Callable[..., None] | None = None,
    observe_usage: Callable[[], Any] | None = None,
) -> int:
    sub = getattr(args, "config_subcommand", None) or "show"
    try:
        if sub in {"usage", "temporary"}:
            _config_migration.check_legacy_environment()
            _config_migration.migrate_legacy_settings()
            config_usage.dispatch(args, resolve=resolve_mutable_setting, refresh=refresh)
        elif sub in {"show", "list"}:
            return _cmd_config_show(home)
        elif sub == "snapshot":
            read_only = getattr(args, "read_only", False)
            if getattr(args, "observe_usage", False):
                if not read_only:
                    raise ValueError("--observe-usageには--read-onlyが必要です")
                if observe_usage is not None:
                    observe_usage()
            print(json.dumps(settings_snapshot(home, read_only=read_only)))
        elif sub == "update":
            payload = json.load(sys.stdin.buffer)
            if not isinstance(payload, dict):
                raise ValueError("標準入力に設定操作のJSONオブジェクトを指定してください")
            update_settings(payload)
            _outcome.report_success("設定操作を適用した")
        elif sub == "migrate-shared":
            _migrate_shared_settings(args)
        elif sub == "reissue-reset":
            _reissue_reset(args.permission_id, args.count)
            _outcome.report_success("旧Codex許可を共有許可として発行し直した")
        elif sub == "get":
            _cmd_config_get(args, home)
        elif sub == "apply-preset":
            _cmd_config_apply_preset(args)
        elif sub in {"add", "remove"}:
            _cmd_config_array(args, remove=sub == "remove")
        elif sub == "repos":
            config_repositories_cli.dispatch(args)
        else:
            _cmd_config_set(args)
    except ValueError as error:
        next_action = (
            error.next_action
            if isinstance(error, _next_action.ActionableError)
            else "`atk config show`で現在値を確認し、`atk config set <KEY> <VALUE>`で不正な値を直して再実行する"
        )
        _outcome.report_failure(str(error), next_action=next_action)
        return 2
    return 0


def array_setting_update(key: str, item: str, *, remove: bool) -> str | None:
    if key not in _config_settings.ARRAY_KEYS:
        raise ValueError(f"{key}は配列型の設定ではない。単一値の設定には`atk config set`を使う")
    _config_migration.migrate_serve_settings()
    _config_migration.migrate_legacy_settings()
    stored = _config_settings.load_config_strict()
    value = stored.get(key, _config_settings.mutable_setting_default(key))
    if not isinstance(value, str):
        raise ValueError(f"{key}の保存値はJSON文字列配列を表す文字列で指定する")
    items = list(_config_settings.parse_remote_hosts(value))
    _config_settings.validate_mutable_setting(key, json.dumps([item]))
    if remove:
        if item not in items:
            raise ValueError(f"要素が存在しない: {item}。現在の一覧: {json.dumps(items, ensure_ascii=False)}")
        items = [current for current in items if current != item]
    elif item in items:
        return None
    else:
        items.append(item)
    return json.dumps(items, ensure_ascii=False)


def _cmd_config_array(args: argparse.Namespace, *, remove: bool) -> None:
    value = array_setting_update(args.key, args.value, remove=remove)
    if value is None:
        _outcome.report_success(f"既に登録されているため変更しない: {args.value}")
    else:
        _cmd_config_set(argparse.Namespace(key=args.key, value=value))
