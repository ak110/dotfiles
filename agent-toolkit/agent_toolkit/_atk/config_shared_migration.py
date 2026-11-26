"""端末の設定を共有設定へ移す比較と適用。CLIと設定画面が同じ処理を使う。  pyfltr: allow-comment"""

from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
from collections.abc import Callable
from typing import Any, Literal

from agent_toolkit._atk import config_repositories, outcome, reset_reservations
from agent_toolkit._atk import config_settings as _settings
from agent_toolkit._common import next_action as _next_action
from agent_toolkit._common.atomic_file import atomic_write
lazy from agent_toolkit._atk import git_sync
lazy from agent_toolkit._atk.wi import sync as wi_sync
lazy from agent_toolkit._git import command as git_command

FORMAT = 4

"""比較結果の形式の版。設定画面はこの値が無いSSH先を移行に対応しない旧版として扱う。  pyfltr: allow-comment"""

REPOSITORY_PREFIX = "repositories:"

NEXT_ACTION = "設定画面で残す値を選ぶか、atk config migrate-sharedで比較する"

type Choice = Literal["local", "shared"]

_MIGRATION_COMMIT = "chore: consolidate shared agent settings"
_startup_failures: set[pathlib.Path] = set()


def startup_sync_failed(path: pathlib.Path) -> bool:
    return path in _startup_failures


_CONNECTION_GROUPS = {
    "codex_connections": tuple(
        sorted(_settings.PROVIDER_KEYS | {key for key in _settings.CONNECTION_ORDER_KEYS if key.startswith("codex_")})
    ),
    "claude_connections": tuple(sorted(key for key in _settings.CONNECTION_ORDER_KEYS if key.startswith("claude_"))),
}
_REPOSITORIES = "repositories"


def pending(local: dict[str, Any]) -> dict[str, Any]:
    return {key: local[key] for key in _settings.SHARED_FILE_KEYS if key in local}


def _shared_repositories(shared: dict[str, str]) -> dict[str, dict[str, Any]]:
    return config_repositories.parse_shared(shared.get(_REPOSITORIES, "{}"))


def _local_value(key: str, value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{key}の保存値は文字列で指定してください")
    _settings.validate_mutable_setting(key, value)
    normalized = _settings.normalize_setting(key, value)
    return reset_reservations.public_permissions(normalized) if key == reset_reservations.PERMISSIONS else normalized


def _item(
    key: str, local: Any, shared: Any, effective: Any, *, safe: bool = False, error: str | None = None, **details: Any
) -> dict[str, Any]:
    return {
        "key": key,
        "label": details.pop("label", key),
        "local": local,
        "shared": shared,
        "shared_effective": effective,
        "kind": "invalid" if error else "local_only" if safe else "equal" if local == effective else "different",
        "error": error,
        **details,
    }


def _safe_missing(key: str, value: str) -> bool:
    if key == reset_reservations.PERMISSIONS:
        # Codexの旧許可は再発行するまで共有から使えない。  # pyfltr: allow-comment
        return all(entry["engine"] == "codex" for entry in json.loads(value))
    if key in {"serve_web_push", "serve_web_push_details", "codex_fast_mode"}:
        return value == "false"
    return True


def _settings_items(local: dict[str, Any], shared: dict[str, str]) -> list[dict[str, Any]]:
    grouped = {key for fields in _CONNECTION_GROUPS.values() for key in fields}
    items: list[dict[str, Any]] = []
    for key in sorted(local.keys() - {_REPOSITORIES} - grouped):
        current = shared.get(key)
        try:
            value = _local_value(key, local[key])
            if current is not None:
                current = _local_value(key, current)
            if key == reset_reservations.PERMISSIONS:
                value = reset_reservations.encode_permissions(json.loads(value))
                if current is not None:
                    current = reset_reservations.encode_permissions(json.loads(current))
        except ValueError as error:
            items.append(_item(key, local[key], current, current, error=str(error), fields=(key,)))
            continue
        effective = _settings.MUTABLE_KEY_DEFAULTS[key] if current is None else current
        items.append(_item(key, value, current, effective, safe=current is None and _safe_missing(key, value), fields=(key,)))
    for key, fields in _CONNECTION_GROUPS.items():
        explicit = tuple(field for field in fields if field in local)
        if not explicit:
            continue
        try:
            shared_values = {
                field: _local_value(field, shared.get(field, _settings.CONNECTION_DEFAULTS[field])) for field in fields
            }
            local_values = shared_values | {field: _local_value(field, local[field]) for field in explicit}
        except ValueError as error:
            items.append(_item(key, {field: local[field] for field in explicit}, None, None, error=str(error), fields=fields))
        else:
            safe = (
                not shared.keys() & set(fields)
                and not any(local_values.get(provider) for provider in _settings.PROVIDER_KEYS)
                and not any("payg" in value.split(",") for value in local_values.values())
            )
            items.append(_item(key, local_values, shared_values, shared_values, safe=safe, fields=fields))
    return items


def _repo_items(local: dict[str, Any], shared: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    if _REPOSITORIES not in local:
        return []
    raw = local[_REPOSITORIES]
    try:
        if not isinstance(raw, str):
            raise ValueError("repositoriesの保存値はJSON文字列で指定する")
        entries = config_repositories.local_entries(raw)
    except ValueError as error:
        return [_item(_REPOSITORIES, None, None, None, error=str(error), fields=())]
    items: list[dict[str, Any]] = []
    for repo_id, matches in sorted(config_repositories.local_entries_by_id(entries).items()):
        if not config_repositories.is_shared_id(repo_id):
            continue
        key = REPOSITORY_PREFIX + repo_id
        try:
            if len(matches) != 1:
                raise ValueError(f"同じリポジトリの端末登録が重複している: {repo_id}")
            raw_key, stored = matches[0]
            _, entry = config_repositories.parse_local_entry(raw_key, stored)
        except (OSError, ValueError) as error:
            items.append(_item(key, None, None, None, error=str(error), repo=repo_id, fields=()))
            continue
        linked = config_repositories.is_link(entry)
        attributes = entry.get("overrides", {}) if linked else config_repositories.shared_attributes(entry)
        if linked and not attributes:
            continue
        if repo_id not in shared:
            safe = attributes.get("edit") == "deny" and not attributes.get("github_issues")
            if "edit" not in attributes:
                items.append(_item(key, attributes, None, None, error="共有側の登録が削除された", repo=repo_id, fields=()))
            else:
                items.append(
                    _item(key, attributes, None, None, safe=safe, repo=repo_id, fields=config_repositories.SHARED_ATTRIBUTES)
                )
            continue
        current = config_repositories.shared_attributes(shared[repo_id])
        for fields in config_repositories.SHARED_UNITS:
            if linked and not attributes.keys() & set(fields):
                continue
            here = {field: attributes[field] for field in fields if field in attributes}
            there = {field: current[field] for field in fields if field in current}
            if here.get("branches") == []:
                here.pop("branches")
            if here.get("github_issues") is False:
                here.pop("github_issues")
            name = fields[0]
            items.append(
                _item(
                    f"{key}:{name}",
                    here,
                    there,
                    there,
                    label=f"{repo_id} · {name}",
                    repo=repo_id,
                    fields=fields,
                    loosens_edit=name == "edit" and _edit_rank(there["edit"]) > _edit_rank(here["edit"]),
                )
            )
    return items


def _edit_rank(edit: str) -> int:
    return {"deny": 0, "awi": 1, "allow": 2}[edit]


def _plan(path: pathlib.Path, local: dict[str, Any], shared: dict[str, str]) -> dict[str, Any]:
    initial = not path.exists()
    snapshot = [str(_settings.config_file_path().resolve()), initial, local, shared, _settings.MUTABLE_KEY_DEFAULTS, FORMAT]
    revision = hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    items = [*_settings_items(local, shared), *_repo_items(local, _shared_repositories(shared))]
    return {
        "format": FORMAT,
        "revision": revision,
        "initial": initial,
        "items": items,
        "required": [item["key"] for item in items if item["kind"] == "different"],
        "invalid": [item["key"] for item in items if item["kind"] == "invalid"],
        "pending": bool(items),
    }


def _read(path: pathlib.Path) -> tuple[dict[str, Any], dict[str, str]]:
    if git_command.output(["status", "--porcelain", "--ignored=matching", "--", path.name], path.parent):
        raise _next_action.ActionableError(f"{path}に未保存の変更がある", next_action="共有設定のGit作業ツリーを確認する")
    return pending(_settings.load_config_strict()), _settings.load_shared_settings(path)


def _sync(notes: pathlib.Path, path: pathlib.Path) -> None:
    wi_sync.ensure_mutation_allowed(notes)
    wi_sync.run_git(["fetch"], notes)
    _discard_unpublished_transfer(notes, path.name)
    git_sync.keep_remote_initial_file(notes, path.name, _settings.SHARED_SETTINGS_MIGRATION_MESSAGE)
    wi_sync.run_git(["merge", "--ff-only", "@{u}"], notes)


def _discard_unpublished_transfer(notes: pathlib.Path, filename: str) -> None:
    commits = git_command.output(["rev-list", "--parents", "@{u}..HEAD"], notes).splitlines()
    if len(commits) != 1:
        return
    lineage = commits[0].split()
    if len(lineage) != 2 or git_command.output(["log", "-1", "--format=%s"], notes) != _MIGRATION_COMMIT:
        return
    if git_command.output(["diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"], notes) != filename:
        return
    if git_command.output(["status", "--porcelain", "--", filename], notes):
        return
    check = git_command.run(["merge-base", "--is-ancestor", lineage[1], "@{u}"], cwd=notes, check=False)
    if check.returncode == 0:
        wi_sync.run_git(["reset", "--keep", lineage[1]], notes)


def write_path() -> pathlib.Path:
    path = shared_write_path()
    if path is None:
        raise _next_action.ActionableError("共有設定の同期先がない", next_action="private-notesの同期先を設定してから移行する")
    return path


def preview(local: dict[str, Any], path: pathlib.Path, shared: dict[str, str]) -> dict[str, Any]:
    """定期取得と設定表示のため、共有先を取得せず手元の値で判定する。  pyfltr: allow-comment"""
    return _plan(path, pending(local), shared)


def compare(*, fetch: bool = True) -> dict[str, Any]:
    path = write_path()
    notes = path.parent
    try:
        with wi_sync.repo_lock(notes):
            if fetch:
                _sync(notes, path)
            return _plan(path, *_read(path))
    except subprocess.SubprocessError as error:
        raise _next_action.ActionableError(
            f"{path}の共有先を確認できない: {error}", next_action="private-notesの同期を確認する"
        ) from error


def _update_attributes(entry: dict[str, Any], fields: tuple[str, ...], values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in entry.items() if key not in fields} | values


def _transfer(
    local: dict[str, Any],
    shared: dict[str, str],
    items: list[dict[str, Any]],
    choices: dict[str, Choice],
) -> tuple[dict[str, str], dict[str, Any]]:
    incoming = _shared_repositories(shared)
    repositories = {key: dict(item) for key, item in incoming.items()}
    values = dict(shared)
    retained = dict(local)
    moved_fields: dict[str, set[str]] = {}
    for item in items:
        key, fields = item["key"], item["fields"]
        use_local = item["kind"] == "local_only" or choices.get(key) == "local"
        if repo_id := item.get("repo"):
            if use_local:
                repositories[repo_id] = _update_attributes(repositories.get(repo_id, {}), fields, item["local"])
            moved_fields.setdefault(repo_id, set()).update(fields)
        else:
            if use_local:
                if key in _CONNECTION_GROUPS:
                    values.update({field: item["local"][field] for field in fields if field in local})
                elif key == reset_reservations.PERMISSIONS:
                    values[key] = reset_reservations.migrate_permissions(shared, item["local"])
                else:
                    values[key] = item["local"]
            for field in fields:
                retained.pop(field, None)
    if incoming != repositories:
        values[_REPOSITORIES] = json.dumps(repositories, ensure_ascii=False, sort_keys=True)
    raw = local.get(_REPOSITORIES)
    if isinstance(raw, str):
        retained[_REPOSITORIES] = _retain_repositories(raw, incoming, repositories, moved_fields)
    return values, retained


def _retain_repositories(
    raw: str,
    incoming: dict[str, dict[str, Any]],
    repositories: dict[str, dict[str, Any]],
    moved: dict[str, set[str]],
) -> str:
    try:
        entries = config_repositories.local_entries(raw)
    except ValueError:
        return raw
    updated = dict(entries)
    for repo_id, matches in config_repositories.local_entries_by_id(entries).items():
        if len(matches) != 1 or (repo_id not in repositories and repo_id not in moved):
            continue
        raw_key, stored = matches[0]
        try:
            _, item = config_repositories.parse_local_entry(raw_key, stored)
        except OSError, ValueError:
            continue
        if config_repositories.is_link(item):
            if "process_loop" not in item and item.get("path"):
                updated[repo_id] = {**stored, "process_loop": incoming.get(repo_id, {}).get("process_loop", False)}
            overrides = item.get("overrides", {})
        elif repo_id in moved:
            updated[repo_id] = {
                key: item[key] for key in config_repositories.HOST_ATTRIBUTES if key in item and item[key] != ""
            }
            overrides = config_repositories.shared_attributes(item)
            for field, value in (("branches", []), ("github_issues", False)):
                overrides.setdefault(field, value)
        else:
            continue
        if repo_id in moved:
            if repo_id != raw_key:
                updated.pop(raw_key)
            updated[repo_id] = dict(updated[repo_id])
            remaining = {key: value for key, value in overrides.items() if key not in moved[repo_id]}
            updated[repo_id].pop("overrides", None)
            if remaining:
                updated[repo_id]["overrides"] = remaining
            if not updated[repo_id].get("path") and not remaining and "process_loop" not in stored:
                updated.pop(repo_id)
    return raw if updated == entries else json.dumps(updated, ensure_ascii=False)


def commit_values(path: pathlib.Path, values: dict[str, str], message: str) -> None:
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


def _drop_local(expected: dict[str, Any], retained: dict[str, Any]) -> None:
    path = _settings.config_file_path()
    with _settings.local_config_lock(path):
        current = _settings.load_config_strict()
        if pending(current) != expected:
            raise _next_action.ActionableError("移行中にローカル設定が変わった", next_action=NEXT_ACTION)
        if retained != expected:
            _settings.write_config_locked(
                path, {key: value for key, value in current.items() if key not in expected} | retained
            )


def _migrate_locked(
    path: pathlib.Path,
    local: dict[str, Any],
    shared: dict[str, str],
    plan: dict[str, Any],
    choices: dict[str, Choice],
) -> int:
    items = [item for item in plan["items"] if item["kind"] in ("equal", "local_only") or item["key"] in choices]
    values, retained = _transfer(local, shared, items, choices)
    if values == shared and retained == local:
        return 0
    if pending(_settings.load_config_strict()) != local:
        raise _next_action.ActionableError("移行中にローカル設定が変わった", next_action=NEXT_ACTION)
    notes = path.parent
    base = git_command.output(["rev-parse", "HEAD"], notes)
    committed = values != shared
    if committed:
        commit_values(path, values, _MIGRATION_COMMIT)
    try:
        if committed or not git_sync.remote_contains_head(notes):
            wi_sync.run_git(["push"], notes)
        wi_sync.run_git(["fetch"], notes)
        upstream_settings = git_command.run(["show", f"@{{u}}:{path.name}"], cwd=notes, capture_output=True, text=True)
        upstream = json.loads(upstream_settings.stdout) if upstream_settings.returncode == 0 else {}
        if not git_sync.remote_contains_head(notes) or upstream != values:
            raise _next_action.ActionableError(
                "移行した共有値が同期先で確認できない", next_action="private-notesの同期を確認する"
            )
    except OSError, ValueError, subprocess.SubprocessError:
        if committed:
            git_command.run(["reset", "--keep", base], cwd=notes, check=True)
        raise
    _drop_local(local, retained)
    return len(items)


def apply(revision: str, choices: dict[str, Choice]) -> int:
    path = write_path()
    notes = path.parent
    try:
        with wi_sync.repo_lock(notes):
            _sync(notes, path)
            local, shared = _read(path)
            plan = _plan(path, local, shared)
            if plan["revision"] != revision:
                raise _next_action.ActionableError("比較後にローカルか共有の設定が変わった", next_action=NEXT_ACTION)
            if set(plan["required"]) != choices.keys() or any(value not in ("shared", "local") for value in choices.values()):
                raise _next_action.ActionableError(
                    "異なる設定ごとに選択が必要: " + "、".join(plan["required"]), next_action=NEXT_ACTION
                )
            moved = _migrate_locked(path, local, shared, plan, choices)
            _startup_failures.discard(path)
            if not moved and plan["invalid"]:
                raise _next_action.ActionableError(
                    "共有できない値がある: " + "、".join(plan["invalid"]),
                    next_action="不正な設定値を直してから再実行する",
                )
            return moved
    except subprocess.SubprocessError as error:
        raise _next_action.ActionableError(
            f"{path}を共有先へ移行できない: {error}", next_action="private-notesの同期を確認する"
        ) from error


def shared_write_path() -> pathlib.Path | None:
    path = _settings.shared_settings_path()
    return path if path is not None and git_sync.has_remote(path.parent) else None


def _auto_locked(path: pathlib.Path) -> None:
    local, shared = _read(path)
    _migrate_locked(path, local, shared, _plan(path, local, shared), {})


def _has_unsettled_values(local: dict[str, Any]) -> bool:
    if local.keys() & _settings.SHARED_SETTING_KEYS:
        return True
    if _REPOSITORIES not in local:
        return False
    raw = local[_REPOSITORIES]
    if not isinstance(raw, str):
        return True
    try:
        entries = config_repositories.local_entries(raw)
    except ValueError:
        return True
    for repo_id, matches in config_repositories.local_entries_by_id(entries).items():
        if not config_repositories.is_shared_id(repo_id):
            continue
        raw_key, item = matches[0]
        if (
            len(matches) != 1
            or raw_key != repo_id
            or not isinstance(item, dict)
            or not config_repositories.is_link(item)
            or "overrides" in item
            or ("path" in item and "process_loop" not in item)
        ):
            return True
    return False


def migrate_at_startup() -> None:
    if (path := shared_write_path()) is None:
        return
    try:
        if not _has_unsettled_values(_settings.load_config_strict()):
            _startup_failures.discard(path)
            return
        with wi_sync.repo_lock(path.parent):
            _sync(path.parent, path)
            _auto_locked(path)
        _startup_failures.discard(path)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        _startup_failures.add(path)
        outcome.report_warning(
            f"共有設定の自動移行を保留した: {error}", next_action="private-notesの同期・保存の状態を確認してください。"
        )


def _blocked(
    plan: dict[str, Any],
    current: dict[str, str],
    changed: dict[str, str],
    touched: set[str],
    touched_repositories: dict[str, set[str]],
) -> list[str]:
    changed_keys = {key for key in current.keys() | changed.keys() if current.get(key) != changed.get(key)} | touched
    before, after = _shared_repositories(current), _shared_repositories(changed)
    blocked = []
    for item in plan["items"]:
        if item["kind"] not in ("different", "invalid"):
            continue
        repo = item.get("repo")
        fields = item["fields"]
        if repo:
            left, right = before.get(repo), after.get(repo)
            written = touched_repositories.get(repo, set())
            if written.intersection(fields or config_repositories.SHARED_ATTRIBUTES) or (
                left != right
                and (left is None or right is None or not fields or any(left.get(k) != right.get(k) for k in fields))
            ):
                blocked.append(item["key"])
        elif changed_keys.intersection(fields or (item["key"],)):
            blocked.append(item["key"])
    return blocked


def update_shared_settings(
    path: pathlib.Path,
    update: Callable[[dict[str, str]], dict[str, str]],
    *,
    touched: set[str] | None = None,
    touched_repositories: dict[str, set[str]] | None = None,
) -> bool:
    notes = path.parent
    next_action = f"{notes}のGit同期の状態を確認し、未同期の変更を解消してから再実行する"
    try:
        with wi_sync.repo_lock(notes):
            _sync(notes, path)
            _auto_locked(path)
            _startup_failures.discard(path)
            local, current = _read(path)
            changed = update(dict(current))
            if blocked := _blocked(_plan(path, local, current), current, changed, touched or set(), touched_repositories or {}):
                raise _next_action.ActionableError(
                    "共有へ保存できない未解決の設定: " + "、".join(blocked), next_action=NEXT_ACTION
                )
            if changed != current:
                commit_values(path, changed, "chore: update shared agent settings")
            wi_sync.push_pending_commits(notes)
            if git_sync.push_was_deferred(notes):
                raise _next_action.ActionableError(
                    f"{path}はローカルに保存したが共有先へ同期できていない", next_action=next_action
                )
    except subprocess.SubprocessError as error:
        raise _next_action.ActionableError(f"{path}の保存・同期に失敗した: {error}", next_action=next_action) from error
    return changed != current
