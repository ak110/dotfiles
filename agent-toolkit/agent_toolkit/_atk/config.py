from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import pathlib
import subprocess
import sys
from collections.abc import Callable
from typing import Any, cast

lazy import platformdirs

from agent_toolkit._atk import config_migration as _config_migration
from agent_toolkit._atk import config_repositories_cli, config_usage
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
lazy from agent_toolkit._atk import git_sync
lazy from agent_toolkit._atk.wi import sync as wi_sync
lazy from agent_toolkit._git import command as git_command

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
    notes = path.parent
    next_action = f"{notes}のGit同期の状態を確認し、未同期の変更を解消してから再実行する"
    try:
        with wi_sync.DEFAULT_STORAGE.mutation(notes):
            _require_shared_migration()
            if git_command.output(["status", "--porcelain", "--ignored=matching", "--", path.name], notes):
                raise _next_action.ActionableError(f"{path}に未保存の変更がある", next_action=next_action)
            current = _config_settings.load_shared_settings(path)
            changed = current | updates
            if changed != current:
                _commit_shared_settings(path, changed, "chore: update shared agent settings")
            wi_sync.push_pending_commits(notes)
            if git_sync.push_was_deferred(notes):
                raise _next_action.ActionableError(
                    f"{path}はローカルに保存したが共有先へ同期できていない", next_action=next_action
                )
    except subprocess.SubprocessError as error:
        raise _next_action.ActionableError(f"{path}の保存・同期に失敗した: {error}", next_action=next_action) from error
    return changed != current


def _commit_shared_settings(path: pathlib.Path, values: dict[str, str], message: str) -> None:
    existed = path.exists()
    atomic_write(path, json.dumps(values, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    try:
        wi_sync.commit_and_push(path.parent, message, [path.name], skip_push=True)
    except OSError, subprocess.SubprocessError:
        git_command.run(["reset", "--", path.name], cwd=path.parent, check=True)
        if existed:
            git_command.run(["restore", "--worktree", "--", path.name], cwd=path.parent, check=True)
        else:
            path.unlink()
        raise


def resolved_settings(home: pathlib.Path) -> dict[str, str]:
    _config_migration.check_legacy_environment()
    _config_migration.migrate_legacy_settings()
    _config_migration.migrate_serve_settings()
    # Windowsでappnameがappauthorとしても付与される二重階層を防ぐ。
    settings = {
        "config_dir": str(_config_settings.config_dir()),
        "state_dir": str(_state_paths.state_dir()),
        "data_dir": str(pathlib.Path(platformdirs.user_data_dir("agent-toolkit", appauthor=False))),
        "private_notes": str(_private_notes.default_private_notes(home)),
        **{key: resolve_mutable_setting(key) for key in _config_settings.MUTABLE_KEY_DEFAULTS},
    }
    return settings


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


def _cmd_config_show(home: pathlib.Path) -> None:
    human = is_human_terminal()
    settings = resolved_settings(home)
    for key, value in settings.items():
        print(f"{key} {value}")
    if (path := _config_settings.shared_settings_path()) is not None:
        shared = _config_settings.load_shared_settings(path)
        local = _config_settings.load_config()
        temporary = usage_settings.temporary_settings()
        active = [
            key
            for key in sorted(shared)
            if key not in local and key not in temporary and not runtime_environment.environment().get(_config_env_name(key))
        ]
        print(f"共有設定ファイル: {path}", file=sys.stderr)
        if active:
            print("共有設定から解決したキー: " + "、".join(active), file=sys.stderr)
        if git_sync.has_remote(path.parent) and _pending_shared(local):
            print(f"未移行の共有設定がある。{_MIGRATION_NEXT_ACTION}。", file=sys.stderr)
    if human and settings["codex_fast_mode"] == "auto":
        print(_auto_fast_report(), file=sys.stderr)


def _auto_fast_report() -> str:
    managed = connections_configured("codex")
    if managed and not any("subscription" in connection_order("codex", role) for role in ("orchestrator", "worker")):
        return "Codex自動速度: 通常（使用順にサブスクリプション接続がない）"

    async def report() -> str:
        cwd = str(pathlib.Path.cwd())

        async def query(request):
            return await codex_fast_mode.auto_service_tier(request, cwd, subscription=True if managed else None)

        async with asyncio.timeout(5):
            decision = await codex_models.with_app_server(query, cwd=cwd)
        speed = "高速" if decision.tier == "priority" else "通常"
        if managed:
            return f"Codex自動速度: 接続ごとに判定（サブスクリプションは{speed}: {decision.explanation}、APIは通常）"
        return f"Codex自動速度: {speed}（{decision.explanation}）"

    try:
        return asyncio.run(report())
    except OSError, ValueError, TimeoutError:
        return "Codex自動速度: 通常（判定に必要な接続情報を取得できない）"


def _pending_shared(local: dict[str, Any]) -> dict[str, Any]:
    return {key: local[key] for key in _config_settings.SHARED_SETTING_KEYS if key in local}


_MIGRATION_NEXT_ACTION = "atk config migrate-sharedで差を確認し、表示された版を--applyに指定して移行する"


def _require_shared_migration() -> None:
    if pending := _pending_shared(_config_settings.load_config_strict()):
        raise _next_action.ActionableError(
            "ローカルに移行していない共有設定がある: " + "、".join(sorted(pending)),
            next_action=_MIGRATION_NEXT_ACTION,
        )


def _migration_shared_defaults(local: dict[str, Any], shared: dict[str, str]) -> dict[str, str]:
    return {key: _MUTABLE_KEY_DEFAULTS[key] for key in local if key not in shared}


def _migration_revision(path: pathlib.Path, local: dict[str, Any], shared: dict[str, str]) -> str:
    snapshot = [
        str(_config_file_path().resolve()),
        path.exists(),
        local,
        shared,
        _migration_shared_defaults(local, shared),
    ]
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _migration_values(
    path: pathlib.Path, local: dict[str, Any], shared: dict[str, str], choices: dict[str, str]
) -> dict[str, str]:
    if not path.exists():
        selected = {key: value for key, value in local.items() if key != "codex_fast_mode"}
        required: set[str] = set()
    else:
        required = {key for key, value in local.items() if value != shared.get(key, _MUTABLE_KEY_DEFAULTS[key])}
        selected = {key: local[key] for key in required if choices.get(key) == "local"}
    if required != choices.keys():
        raise _next_action.ActionableError(
            "異なる設定ごとに選択が必要: " + "、".join(sorted(required)),
            next_action=f"{_MIGRATION_NEXT_ACTION}。残す値を--keep-local KEYまたは--keep-shared KEYで指定する",
        )
    normalized = {}
    for key, value in selected.items():
        if not isinstance(value, str):
            raise ValueError(f"{key}の保存値は文字列で指定してください")
        _config_settings.validate_mutable_setting(key, value)
        normalized[key] = _config_settings.normalize_setting(key, value)
    return shared | normalized


def _migrate_shared_settings(args: argparse.Namespace) -> None:
    path = _shared_write_path()
    if path is None:
        raise _next_action.ActionableError("共有設定の同期先がない", next_action="private-notesの同期先を設定してから移行する")
    keep_local, keep_shared = args.keep_local, args.keep_shared
    choices = dict.fromkeys(keep_local, "local") | dict.fromkeys(keep_shared, "shared")
    if len(choices) != len(keep_local) + len(keep_shared) or (choices and args.apply is None):
        raise ValueError("選択は重複させず、--applyと併用してください")
    notes = path.parent
    try:
        with wi_sync.repo_lock(notes):
            wi_sync.ensure_mutation_allowed(notes)
            wi_sync.run_git(["fetch"], notes)
            git_sync.keep_remote_initial_file(notes, path.name, _config_settings.SHARED_SETTINGS_MIGRATION_MESSAGE)
            wi_sync.run_git(["merge", "--ff-only", "@{u}"], notes)
            if git_command.output(["status", "--porcelain", "--ignored=matching", "--", path.name], notes):
                raise _next_action.ActionableError(f"{path}に未保存の変更がある", next_action=_MIGRATION_NEXT_ACTION)
            local = _pending_shared(_config_settings.load_config_strict())
            shared = _config_settings.load_shared_settings(path)
            revision = _migration_revision(path, local, shared)
            if args.apply is None:
                print(
                    json.dumps(
                        {
                            "revision": revision,
                            "local": local,
                            "shared": shared,
                            "comparison": _config_migration.compare_shared_values(local, shared),
                            "shared_defaults": _migration_shared_defaults(local, shared),
                            "initial_not_shared": (
                                {"codex_fast_mode": _MUTABLE_KEY_DEFAULTS["codex_fast_mode"]}
                                if not path.exists() and "codex_fast_mode" in local
                                else {}
                            ),
                            "initial": not path.exists(),
                        },
                        ensure_ascii=False,
                    )
                )
                return
            if revision != args.apply:
                raise _next_action.ActionableError("比較後にローカルか共有の設定が変わった", next_action=_MIGRATION_NEXT_ACTION)
            values = _migration_values(path, local, shared, choices)
            if not local:
                _outcome.report_success("移行するローカル共有設定はない")
                return
            if _pending_shared(_config_settings.load_config_strict()) != local:
                raise _next_action.ActionableError("移行中にローカル設定が変わった", next_action=_MIGRATION_NEXT_ACTION)
            if not path.exists() or values != shared:
                _commit_shared_settings(path, values, _config_settings.SHARED_SETTINGS_MIGRATION_MESSAGE)
            wi_sync.run_git(["push"], notes)
            wi_sync.run_git(["fetch"], notes)
            if (
                not git_sync.remote_contains_head(notes)
                or json.loads(git_command.output(["show", f"@{{u}}:{path.name}"], notes)) != values
            ):
                raise _next_action.ActionableError("移行した共有値が同期先で確認できない", next_action=_MIGRATION_NEXT_ACTION)
            _config_settings.drop_migrated_local_settings(local)
    except subprocess.SubprocessError as error:
        raise _next_action.ActionableError(
            f"{path}を共有先へ移行できない: {error}", next_action=_MIGRATION_NEXT_ACTION
        ) from error
    _outcome.report_success(f"共有設定への移行を確認した: {len(local)}件")


def _report_candidate_warnings(warnings: list[str]) -> None:
    if warnings:
        message = "\n".join([warnings[0], *(f"{_outcome.WARNING_PREFIX}{warning}" for warning in warnings[1:])])
        _outcome.report_warning(message, next_action=_UNKNOWN_CANDIDATE_NEXT_ACTION)


def settings_snapshot(home: pathlib.Path) -> dict[str, Any]:
    effective = resolved_settings(home)
    saved = _config_settings.load_config()
    shared_path = _config_settings.shared_settings_path()
    shared = _config_settings.load_shared_settings(shared_path) if shared_path is not None else {}
    shared_sync = shared_path is not None and git_sync.has_remote(shared_path.parent)
    temporary = usage_settings.temporary_settings()
    settings = {}
    for key, default in _config_settings.MUTABLE_KEY_DEFAULTS.items():
        shared_key = shared_sync and key in _config_settings.SHARED_SETTING_KEYS
        migration_pending = shared_key and key in saved
        source = _mutable_source(key, saved, shared, temporary, shared_target=shared_key)
        description = _config_settings.setting_description(key)
        if migration_pending:
            description += "\n共有の保存値を使うには、atk config migrate-shared で差を確認し、適用してください。"
        settings[key] = {
            "effective": effective.pop(key),
            "saved": shared.get(key, default) if shared_key else saved.get(key, default),
            "default": default,
            "source": source,
            "shared": shared_key,
            "shared_sync": shared_key and shared_sync,
            "description": description,
            "format": _config_settings.setting_format(key),
            "choices": _config_settings.SETTING_CHOICES.get(key),
            "array": key in _config_settings.ARRAY_KEYS,
            "temporary": config_usage.allowed_temporary_key(key),
        }
    usage = usage_observation.snapshot()
    state = usage_settings.read_settings()
    return {
        "settings": settings,
        "paths": effective,
        "presets": _config_settings.PRESET_MODELS,
        "temporary": {"values": temporary, "until": state.get("until")},
        "repositories": json.loads(config_repositories_cli.update_repositories(argparse.Namespace(repos_subcommand="list"))),
        "model_candidates": {engine: sorted(names) for engine, names in _config_settings.KNOWN_MODELS.items()},
        "usage": config_usage.profile_windows(usage),
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
    if resolve_mutable_setting(key) == value:
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
        _atk_help.add_command(sub, name, **_atk_help.HELP[f"atk config {name}"])
    for name in ("add", "remove"):
        array = _atk_help.add_command(sub, name, **_atk_help.HELP[f"atk config {name}"])
        array.add_argument("key", metavar="KEY", help="JSON配列型の設定キー。")
        array.add_argument("value", metavar="VALUE", help="追加・削除する要素1件。")
    config_repositories_cli.build_parser(sub)
    migrate = _atk_help.add_command(sub, "migrate-shared", **_atk_help.HELP["atk config migrate-shared"])
    migrate.add_argument("--apply", metavar="REVISION", help="比較で取得した版を指定して適用する。")
    migrate.add_argument("--keep-local", action="append", default=[], metavar="KEY", help="手元の値を全ホストで使う。")
    migrate.add_argument("--keep-shared", action="append", default=[], metavar="KEY", help="共有側の値を使う。")
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


def dispatch(args: argparse.Namespace, home: pathlib.Path, *, refresh: Callable[..., None] | None = None) -> int:
    sub = getattr(args, "config_subcommand", None) or "show"
    try:
        if sub in {"usage", "temporary"}:
            _config_migration.check_legacy_environment()
            _config_migration.migrate_legacy_settings()
            config_usage.dispatch(args, resolve=resolve_mutable_setting, refresh=refresh)
        elif sub in {"show", "list"}:
            _cmd_config_show(home)
        elif sub == "snapshot":
            print(json.dumps(settings_snapshot(home)))
        elif sub == "update":
            payload = json.load(sys.stdin.buffer)
            if not isinstance(payload, dict):
                raise ValueError("標準入力に設定操作のJSONオブジェクトを指定してください")
            update_settings(payload)
            _outcome.report_success("設定操作を適用した")
        elif sub == "migrate-shared":
            _migrate_shared_settings(args)
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
