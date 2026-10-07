"""WIエントリの依存先（`depends_on`）の設定と、依存関係の循環の検出（`atk wi set-dependencies`）。"""

from __future__ import annotations

import argparse
import pathlib
import sys
import typing

from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._atk.wi import add as _add
from agent_toolkit._atk.wi import entries as _wi_entries
from agent_toolkit._atk.wi import filenames as _wi_filenames
from agent_toolkit._atk.wi import frontmatter as _frontmatter
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._atk.wi.constants import WI_STATE_HOLD, WI_STATE_INBOX, WI_STATE_PROCESSING, WI_TYPE_AWI
from agent_toolkit._atk.wi.mutations.targets import atomic_write_text, resolve_active_targets
from agent_toolkit._atk.wi.repo import (
    resolve_repo_id,
)
from agent_toolkit._atk.wi.web_input import WebInputError

_BROKEN_ENTRY_NEXT_ACTION = "`atk wi show {name}`で保存内容を確認し、ユーザーへ報告する"


def _entry_dependencies(path: pathlib.Path, data: dict[str, object]) -> tuple[str, ...]:
    """エントリの依存先を文字列列として検証して返す。"""
    raw_dependencies = data.get("depends_on", [])
    if not isinstance(raw_dependencies, list) or not all(isinstance(value, str) for value in raw_dependencies):
        raise WebInputError(
            f"depends_onが不正です: {path.name}",
            next_action=f"`atk wi set-dependencies {path.name} --depends-on <依存先>`で依存を指定し直す",
        )
    return tuple(raw_dependencies)


def set_entry_dependencies(
    private_notes: pathlib.Path,
    *,
    filename: str,
    depends_on: tuple[str, ...],
    target_repo: str | None = None,
    lock_timeout: float = -1,
) -> dict[str, object | None]:
    """既存AWIの明示依存だけを更新し、保存済みメタデータを返す。

    対象は未終端の`inbox`、`processing`および`hold`とする。`hold`を含めるのは、投入済み項目の修正手順が
    投入済み項目を修正する際は、`hold`中に本文と依存を確定する。そのため、この状態でも依存を更新できる必要がある。
    依存の更新は保存状態を変えないため、`hold`の項目は更新後も`hold`のまま残る。
    """
    inbox_dir = private_notes / WI_STATE_INBOX
    processing_dir = _wi_entries.subdir(private_notes, WI_STATE_PROCESSING)
    hold_dir = private_notes / WI_STATE_HOLD
    _wi_filenames.validate_filenames_only([filename, *depends_on], inbox_dir)
    normalized_target_repo = resolve_repo_id(target_repo) if target_repo is not None else None

    with _wi_sync.repo_lock(private_notes, timeout=lock_timeout):
        _wi_sync.push_pending_commits(private_notes)
        _wi_sync.pull(private_notes)
        path = resolve_active_targets([filename], inbox_dir, processing_dir)[0]
        text = path.read_text(encoding="utf-8")
        parsed = _frontmatter.parse_frontmatter(text)
        if parsed is None:
            raise WebInputError(
                f"frontmatterが破損しているため依存を更新できません: {path.name}",
                next_action=_BROKEN_ENTRY_NEXT_ACTION.format(name=path.name),
            )
        data, body = parsed
        if _wi_entries.entry_type_of(path, text) != WI_TYPE_AWI:
            raise WebInputError(
                f"AWIだけ依存を更新できます: {path.name}",
                next_action="依存を更新するAWIのファイル名を指定し直す",
            )
        raw_entry_repo = data.get("target_repo")
        if not isinstance(raw_entry_repo, str):
            raise WebInputError(
                f"target_repoが不正です: {path.name}",
                next_action=_BROKEN_ENTRY_NEXT_ACTION.format(name=path.name),
            )
        entry_repo = resolve_repo_id(raw_entry_repo)
        if normalized_target_repo is not None and entry_repo != normalized_target_repo:
            raise WebInputError(
                f"target_repoが一致しません: {path.name}は{entry_repo}、指定値は{normalized_target_repo}",
                next_action=(
                    f"実際の値を--target-repoへ指定し直すか、`atk wi show {path.name}`で別リポジトリの項目でないか確認する"
                ),
            )

        canonical_dependencies = tuple(
            dict.fromkeys(_wi_filenames.validate_filename(value, inbox_dir).name for value in depends_on)
        )
        if path.name in canonical_dependencies:
            raise WebInputError(
                f"自分自身を依存先へ指定できません: {path.name}",
                next_action="--depends-onから自分自身を外して再実行する",
            )
        dependency_graph = _active_dependency_graph(inbox_dir, processing_dir, hold_dir)
        dependency_graph[path.name] = set(canonical_dependencies)
        cycle = _dependency_cycle(dependency_graph, path.name, canonical_dependencies)
        if cycle is not None:
            raise WebInputError(
                f"循環する依存を指定できません: {path.name}（依存先の並び: {' → '.join(cycle)}）",
                next_action=(
                    f"`atk wi show {cycle[1]}`で依存先を確認し、循環の原因となる依存先を--depends-onの指定から外して再実行する"
                ),
            )
        data.pop("queue_schedule", None)
        if canonical_dependencies:
            data["depends_on"] = list(canonical_dependencies)
        else:
            data.pop("depends_on", None)
        updated_text = _frontmatter.serialize_frontmatter(data, body)
        if updated_text != text:
            atomic_write_text(path, updated_text)
            relative_path = str(path.relative_to(private_notes))
            _wi_sync.commit_and_push(private_notes, "chore: update awi dependencies", [relative_path])
        return _add.read_saved_entry_details(
            path,
            expected_body=updated_text,
        )


def _active_dependency_graph(
    inbox_dir: pathlib.Path,
    processing_dir: pathlib.Path,
    hold_dir: pathlib.Path | None = None,
) -> dict[str, set[str]]:
    """ロック内で取得したactiveなAWIの依存グラフを返す。"""
    entries: dict[str, pathlib.Path] = {}
    directories = (inbox_dir, processing_dir) if hold_dir is None else (inbox_dir, hold_dir, processing_dir)
    for directory in directories:
        if directory.is_dir():
            entries.update({path.name: path for path in directory.glob("*.md") if path.is_file()})
    graph: dict[str, set[str]] = {}
    for name, entry_path in entries.items():
        entry_text = entry_path.read_text(encoding="utf-8")
        parsed = _frontmatter.parse_frontmatter(entry_text)
        if parsed is None:
            raise WebInputError(
                f"active項目のfrontmatterが破損しているため依存を更新できません: {name}",
                next_action=_BROKEN_ENTRY_NEXT_ACTION.format(name=name),
            )
        data, _body = parsed
        if _wi_entries.entry_type_of(entry_path, entry_text) != WI_TYPE_AWI:
            continue
        raw_dependencies = data.get("depends_on", [])
        if not isinstance(raw_dependencies, list) or not all(isinstance(value, str) for value in raw_dependencies):
            raise WebInputError(
                f"active項目のdepends_onが不正なため依存を更新できません: {name}",
                next_action=f"`atk wi set-dependencies {name} --depends-on <依存先>`で依存を指定し直してから再実行する",
            )
        graph[name] = {_wi_filenames.validate_filename(value, inbox_dir).name for value in raw_dependencies}
    return graph


def _dependency_reaches(graph: dict[str, set[str]], start: str, target: str) -> bool:
    """startからtargetへ到達できる場合に真を返す。"""
    return _dependency_path(graph, start, target) is not None


def _dependency_path(graph: dict[str, set[str]], start: str, target: str) -> tuple[str, ...] | None:
    """startからtargetへ至る依存先を辿った並びを返す。到達できない場合は`None`を返す。"""
    pending: list[tuple[str, ...]] = [(start,)]
    visited: set[str] = set()
    while pending:
        route = pending.pop()
        current = route[-1]
        if current == target:
            return route
        if current in visited:
            continue
        visited.add(current)
        pending.extend((*route, following) for following in sorted(graph.get(current, ()), reverse=True))
    return None


def _dependency_cycle(
    graph: dict[str, set[str]],
    name: str,
    dependencies: typing.Iterable[str],
) -> tuple[str, ...] | None:
    """nameから依存先を経てnameへ戻る循環を構成する依存先の並びを返す。循環が無い場合は`None`を返す。

    受信側が循環の原因となる依存先を特定できるよう、拒否の理由へ依存先の並びを載せるために使う。
    """
    for dependency in dependencies:
        route = _dependency_path(graph, dependency, name)
        if route is not None:
            return (name, *route)
    return None


def cmd_set_dependencies(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """set-dependenciesサブコマンドを実行する。"""
    target_repo = args.target_repo
    if target_repo is None:
        target_repo, _local_worktree = _add.resolve_add_target(None)
    try:
        details = set_entry_dependencies(
            private_notes,
            filename=args.filename,
            depends_on=tuple(args.depends_on or ()),
            target_repo=target_repo,
        )
    except WebInputError as error:
        _outcome.report_failure(f"依存更新を拒否した: {error}", next_action=error.next_action)
        sys.exit(1)
    _outcome.report_success(f"依存を更新した: {args.filename}")
    dependencies = details["depends_on"]
    assert isinstance(dependencies, list)
    print(f"    depends_on: {'、'.join(str(value) for value in dependencies) or 'なし'}")
