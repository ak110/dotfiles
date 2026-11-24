from __future__ import annotations

import json
import pathlib
import re
from collections.abc import Iterable

from agent_toolkit._atk import git_sync
from agent_toolkit._atk.wi import sync
from agent_toolkit._common import runtime_environment
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._git import command as git

FILENAME = "research-subscriptions.json"
FIELDS = ("accounts", "queries")
ACCOUNT = r"[a-zA-Z0-9_]{1,15}"

type Subscriptions = dict[str, tuple[str, ...]]


def _identity(field: str, value: str) -> str:
    return value.removeprefix("@").lower() if field == "accounts" else value


def _values(items: object, *, field: str) -> tuple[str, ...]:
    if not isinstance(items, (list, tuple)) or any(not isinstance(item, str) or not item.strip() for item in items):
        raise ValueError(f"{field}は空でない文字列のJSON配列で指定する（空配列も可）")
    unique: dict[str, str] = {}
    for item in items:
        value = item.strip()
        if field == "accounts":
            value = value.removeprefix("@")
            if not re.fullmatch(ACCOUNT, value):
                raise ValueError("Xのアカウントは英数字またはアンダースコアの1〜15文字で指定する（先頭の@は省略可）")
        unique.setdefault(_identity(field, value), value)
    return tuple(unique.values())


def parse(data: object) -> Subscriptions:
    if not isinstance(data, dict) or data.keys() - set(FIELDS):
        raise ValueError(f"{FILENAME}はaccountsとqueriesを持つJSON objectで指定する（各キーは省略可）")
    return {key: _values(items, field=key) for key, items in data.items()}


def root() -> pathlib.Path:
    return sync.ensure_environment(runtime_environment.home()).resolve()


def read(private_notes: pathlib.Path) -> Subscriptions:
    path = private_notes / FILENAME
    if path.is_symlink():
        raise ValueError(f"{FILENAME}にシンボリックリンクは使えない")
    try:
        return parse(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return {}


def _save(private_notes: pathlib.Path, subscriptions: Subscriptions) -> None:
    atomic_write(private_notes / FILENAME, json.dumps(subscriptions, ensure_ascii=False, indent=2) + "\n")


def _require_clean(private_notes: pathlib.Path) -> None:
    if git.output(["status", "--porcelain", "--ignored=matching", "--", FILENAME], private_notes):
        raise ActionableError(
            f"{FILENAME}に未保存の変更がある",
            next_action=f"private-notesの作業ツリーで{FILENAME}の変更を確認し、解消してから再実行する",
        )


def _check_push(private_notes: pathlib.Path) -> None:
    if git_sync.push_was_deferred(private_notes):
        raise ActionableError(
            "Xの購読を共有先へ同期できていない",
            next_action="private-notesの同期の診断に従って差分を解消し、再実行する",
        )


def migrate(initial: Subscriptions) -> None:
    if not initial:
        return
    notes = root()
    with sync.DEFAULT_STORAGE.mutation(notes) as transaction:
        _require_clean(notes)
        existing = read(notes)
        migrated = initial | existing
        if migrated != existing:
            _save(notes, migrated)
        # 直前にcommitまで成功しpushに失敗した場合も、設定を消す前に同期する。  # pyfltr: allow-comment
        transaction.commit("chore: migrate X research subscriptions", [FILENAME] if migrated != existing else [])
        _check_push(notes)


def update(*, add: Subscriptions, remove: Subscriptions) -> Subscriptions:
    add, remove = parse(add), parse(remove)
    if any(
        {_identity(field, value) for value in add.get(field, ())}.intersection(
            _identity(field, value) for value in remove.get(field, ())
        )
        for field in FIELDS
    ):
        raise ValueError("同じ購読を追加と削除の両方に指定できない")
    notes = root()
    with sync.DEFAULT_STORAGE.mutation(notes) as transaction:
        _require_clean(notes)
        existing = read(notes)
        changed = dict(existing)
        for field in FIELDS:
            removed = {_identity(field, value) for value in remove.get(field, ())}
            values = _values((*existing.get(field, ()), *add.get(field, ())), field=field)
            values = tuple(value for value in values if _identity(field, value) not in removed)
            if values != existing.get(field, ()):
                changed[field] = values
        if changed != existing:
            _save(notes, changed)
        transaction.commit("chore: update X research subscriptions", [FILENAME] if changed != existing else [])
        _check_push(notes)
        return read(notes)


def account_identities(accounts: Iterable[str]) -> set[str]:
    return {_identity("accounts", account) for account in accounts}
