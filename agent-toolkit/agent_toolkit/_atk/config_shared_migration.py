"""端末の設定を共有設定へ移す比較と適用。CLIと設定画面が同じ処理を使う。  pyfltr: allow-comment"""

from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
from collections.abc import Callable
from typing import Any, Literal

from agent_toolkit._atk import config_repositories, reset_reservations
from agent_toolkit._atk import config_settings as _settings
from agent_toolkit._common import next_action as _next_action
from agent_toolkit._common.atomic_file import atomic_write
lazy from agent_toolkit._atk import git_sync
lazy from agent_toolkit._atk.wi import sync as wi_sync
lazy from agent_toolkit._git import command as git_command

FORMAT = 3

"""比較結果の形式の版。設定画面はこの値が無いSSH先を移行に対応しない旧版として扱う。  pyfltr: allow-comment"""

REPOSITORY_PREFIX = "repositories:"

NEXT_ACTION = "設定画面の「共有へ移行」かatk config migrate-sharedで差を確認し直して移行する"

type Choice = Literal["local", "shared"]


def pending(local: dict[str, Any]) -> dict[str, Any]:
    """共有先へ移していない端末側の値を返す。リポジトリは共有できる登録の属性だけを返す。  pyfltr: allow-comment"""
    result: dict[str, Any] = {key: local[key] for key in _settings.SHARED_SETTING_KEYS if key in local}
    raw = local.get("repositories")
    if isinstance(raw, str):
        try:
            repositories = config_repositories.parse_local(raw)
        except ValueError:
            repositories = {}
        attributes = {
            repo_id: {key: value for key, value in item.items() if key != "path"}
            for repo_id, item in repositories.items()
            if config_repositories.is_shared_id(repo_id) and not config_repositories.is_link(item)
        }
        if attributes:
            result["repositories"] = attributes
    return result


def _shared_repositories(shared: dict[str, str]) -> dict[str, dict[str, Any]]:
    return config_repositories.parse_shared(shared.get("repositories", "{}"))


def _local_value(key: str, value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{key}の保存値は文字列で指定してください")
    _settings.validate_mutable_setting(key, value)
    normalized = _settings.normalize_setting(key, value)
    return reset_reservations.public_permissions(normalized) if key == reset_reservations.PERMISSIONS else normalized


def _items(local: dict[str, Any], shared: dict[str, str], *, initial: bool) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for key in sorted(local.keys() - {"repositories"}):
        default = _settings.MUTABLE_KEY_DEFAULTS[key]
        shared_value = shared.get(key)
        if key == reset_reservations.PERMISSIONS and shared_value is not None:
            shared_value = reset_reservations.public_permissions(shared_value)
        item: dict[str, Any] = {
            "key": key,
            "label": key,
            "local": local[key],
            "shared": shared_value,
            "shared_effective": shared_value if shared_value is not None else default,
            "error": None,
        }
        try:
            item["local"] = _local_value(key, local[key])
        except ValueError as error:
            item["error"] = str(error)
        if initial or item["local"] == item["shared_effective"]:
            item["kind"] = "equal" if item["local"] == item["shared_effective"] else "local_only"
        else:
            item["kind"] = "different"
        items.append(item)
    shared_repositories = _shared_repositories(shared)
    for repo_id, attributes in sorted(local.get("repositories", {}).items()):
        current = shared_repositories.get(repo_id)
        items.append(
            {
                "key": f"{REPOSITORY_PREFIX}{repo_id}",
                "label": repo_id,
                "local": attributes,
                "shared": current,
                "shared_effective": current,
                "error": None,
                "kind": "local_only" if current is None else "equal" if current == attributes else "different",
                "loosens_edit": current is not None and _edit_rank(current["edit"]) > _edit_rank(attributes["edit"]),
            }
        )
    return items


def _edit_rank(edit: str) -> int:
    return {"deny": 0, "awi": 1, "allow": 2}[edit]


def _revision(path: pathlib.Path, local: dict[str, Any], shared: dict[str, str]) -> str:
    # 共有に無いキーは初期値と比べるため、初期値の変更も比較後の変化として扱う。  # pyfltr: allow-comment
    defaults = {key: _settings.MUTABLE_KEY_DEFAULTS[key] for key in local if key in _settings.MUTABLE_KEY_DEFAULTS}
    snapshot = [str(_settings.config_file_path().resolve()), path.exists(), local, shared, defaults, FORMAT]
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _read(path: pathlib.Path) -> tuple[dict[str, Any], dict[str, str]]:
    notes = path.parent
    if git_command.output(["status", "--porcelain", "--ignored=matching", "--", path.name], notes):
        raise _next_action.ActionableError(f"{path}に未保存の変更がある", next_action=NEXT_ACTION)
    return pending(_settings.load_config_strict()), _settings.load_shared_settings(path)


def _sync(notes: pathlib.Path, path: pathlib.Path) -> None:
    wi_sync.ensure_mutation_allowed(notes)
    wi_sync.run_git(["fetch"], notes)
    git_sync.keep_remote_initial_file(notes, path.name, _settings.SHARED_SETTINGS_MIGRATION_MESSAGE)
    wi_sync.run_git(["merge", "--ff-only", "@{u}"], notes)


def write_path() -> pathlib.Path:
    path = _settings.shared_settings_path()
    if path is None or not git_sync.has_remote(path.parent):
        raise _next_action.ActionableError("共有設定の同期先がない", next_action="private-notesの同期先を設定してから移行する")
    return path


def compare(*, fetch: bool = True) -> dict[str, Any]:
    """共有先を取得して端末の値と比べ、適用に使う版と項目ごとの差を返す。  pyfltr: allow-comment"""
    path = write_path()
    notes = path.parent
    try:
        with wi_sync.repo_lock(notes):
            if fetch:
                _sync(notes, path)
            local, shared = _read(path)
    except subprocess.SubprocessError as error:
        raise _next_action.ActionableError(f"{path}の共有先を確認できない: {error}", next_action=NEXT_ACTION) from error
    initial = not path.exists()
    items = _items(local, shared, initial=initial)
    return {
        "format": FORMAT,
        "revision": _revision(path, local, shared),
        "initial": initial,
        "items": items,
        "required": [item["key"] for item in items if item["kind"] == "different"],
        "invalid": [item["key"] for item in items if item["error"]],
        "pending": bool(local),
    }


def _values(
    local: dict[str, Any], shared: dict[str, str], choices: dict[str, Choice], *, initial: bool
) -> tuple[dict[str, str], set[str]]:
    items = _items(local, shared, initial=initial)
    required = {item["key"] for item in items if item["kind"] == "different"}
    if required != choices.keys():
        raise _next_action.ActionableError(
            "異なる設定ごとに選択が必要: " + "、".join(sorted(required)),
            next_action=f"{NEXT_ACTION}。差のある項目だけ残す値を選ぶ",
        )
    invalid = [item["key"] for item in items if item["error"]]
    if invalid:
        raise _next_action.ActionableError(
            "移行できないローカル値がある: " + "、".join(invalid),
            next_action="atk config setで正しい値へ直してから比較し直す",
        )
    values = dict(shared)
    repositories = _shared_repositories(shared)
    moved: set[str] = set()
    for item in items:
        key = item["key"]
        adopt_local = item["kind"] in ("equal", "local_only") or choices.get(key) == "local"
        if key.startswith(REPOSITORY_PREFIX):
            if adopt_local:
                repositories[item["label"]] = item["local"]
        elif adopt_local:
            values[key] = (
                reset_reservations.migrate_permissions(shared, item["local"])
                if key == reset_reservations.PERMISSIONS
                else item["local"]
            )
        moved.add(key)
    if local.get("repositories") or "repositories" in shared:
        values["repositories"] = json.dumps(repositories, ensure_ascii=False, sort_keys=True)
    return values, moved


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


def _drop_local(expected: dict[str, Any]) -> None:
    """同期を確認した値を端末側から除く。リポジトリはpathだけの関連付けへ置き換える。  pyfltr: allow-comment"""
    path = _settings.config_file_path()
    with _settings.local_config_lock(path):
        current = _settings.load_config_strict()
        if pending(current) != expected:
            raise _next_action.ActionableError("移行中にローカル設定が変わった", next_action=NEXT_ACTION)
        updated = {key: value for key, value in current.items() if key not in expected or key == "repositories"}
        if "repositories" in expected:
            repositories = config_repositories.parse_local(current["repositories"])
            for repo_id in expected["repositories"]:
                item = repositories.pop(repo_id)
                if item.get("path"):
                    repositories[repo_id] = {"path": item["path"]}
            updated["repositories"] = json.dumps(repositories, ensure_ascii=False)
        _settings.write_config_locked(path, updated)


def apply(revision: str, choices: dict[str, Choice]) -> int:
    """比較時の版を再照合して移行し、同期先で確認できた後に端末側の値を除く。移した項目数を返す。  pyfltr: allow-comment"""
    path = write_path()
    notes = path.parent
    try:
        with wi_sync.repo_lock(notes):
            _sync(notes, path)
            local, shared = _read(path)
            if _revision(path, local, shared) != revision:
                raise _next_action.ActionableError("比較後にローカルか共有の設定が変わった", next_action=NEXT_ACTION)
            initial = not path.exists()
            values, moved = _values(local, shared, choices, initial=initial)
            if not local:
                return 0
            if initial or values != shared:
                commit_values(path, values, _settings.SHARED_SETTINGS_MIGRATION_MESSAGE)
            wi_sync.run_git(["push"], notes)
            wi_sync.run_git(["fetch"], notes)
            if (
                not git_sync.remote_contains_head(notes)
                or json.loads(git_command.output(["show", f"@{{u}}:{path.name}"], notes)) != values
            ):
                raise _next_action.ActionableError("移行した共有値が同期先で確認できない", next_action=NEXT_ACTION)
            _drop_local(local)
    except subprocess.SubprocessError as error:
        raise _next_action.ActionableError(f"{path}を共有先へ移行できない: {error}", next_action=NEXT_ACTION) from error
    return len(moved)


def shared_write_path() -> pathlib.Path | None:
    path = _settings.shared_settings_path()
    return path if path is not None and git_sync.has_remote(path.parent) else None


def require_migrated() -> None:
    if remaining := pending(_settings.load_config_strict()):
        raise _next_action.ActionableError(
            "ローカルに移行していない共有設定がある: " + "、".join(sorted(remaining)), next_action=NEXT_ACTION
        )


def update_shared_settings(path: pathlib.Path, update: Callable[[dict[str, str]], dict[str, str]]) -> bool:
    """共有先を最新にした上で共有設定を書き換え、同期まで確かめる。変わったかを返す。  pyfltr: allow-comment"""
    notes = path.parent
    next_action = f"{notes}のGit同期の状態を確認し、未同期の変更を解消してから再実行する"
    try:
        with wi_sync.DEFAULT_STORAGE.mutation(notes):
            require_migrated()
            if git_command.output(["status", "--porcelain", "--ignored=matching", "--", path.name], notes):
                raise _next_action.ActionableError(f"{path}に未保存の変更がある", next_action=next_action)
            current = _settings.load_shared_settings(path)
            changed = update(dict(current))
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
