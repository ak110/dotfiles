import dataclasses
import datetime
import pathlib
import sys
from collections.abc import Iterable, Iterator, Mapping

from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._atk.wi import branches as _wi_branches
from agent_toolkit._atk.wi import filenames as _wi_filenames
from agent_toolkit._atk.wi import filters as _wi_filters
from agent_toolkit._atk.wi.constants import WI_STATES, WI_TYPES, normalized_wi_type, unrepairable_entry_next_action
from agent_toolkit._atk.wi.entry_text import EntryTexts, parse_frontmatter
from agent_toolkit._atk.wi.formatters import parse_source, parse_target_repo
from agent_toolkit._atk.wi.frontmatter import write_entry_text
from agent_toolkit._atk.wi.layout import entry_path, entry_state_of, iter_entry_paths, unique_dated_entry_path
from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._git import remote as _git_remote

__all__ = [
    "CommitMetadata",
    "EntryRecord",
    "canonical_repo",
    "entry_path",
    "entry_state_of",
    "entry_type_from_metadata",
    "entry_type_of",
    "iter_entries",
    "iter_entry_paths",
    "read_named_entries",
    "select_entries",
    "stamp_result",
    "subdir",
    "validate_named_filenames",
]


@dataclasses.dataclass(frozen=True)
class CommitMetadata:
    short_oid: str
    subject: str


def subdir(private_notes: pathlib.Path, name: str) -> pathlib.Path:
    path = private_notes / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def stamp_result(
    path: pathlib.Path,
    *,
    outcome: str,
    now: datetime.datetime,
    commit: CommitMetadata | list[CommitMetadata] | None = None,
    note: str | None = None,
) -> None:
    body = path.read_text(encoding="utf-8")
    if not body.endswith("\n"):
        body += "\n"
    lines = [
        "",
        "## 処理結果",
        "",
        f"- 採否: {outcome}",
        f"- 処理日時: {now.astimezone().isoformat(timespec='seconds')}",
    ]
    commits = [commit] if isinstance(commit, CommitMetadata) else commit or []
    for item in commits:
        lines.extend(
            (
                f"- 対応commit: {item.short_oid}",
                f"- 対応commit件名: {item.subject}",
            )
        )
    if note:
        lines.append(f"- メモ: {note}")
    body += "\n".join(lines) + "\n"
    write_entry_text(path, body)


def canonical_repo(value: str, cache: dict[str, str | None]) -> str | None:
    return _git_remote.canonical_repo(value, cache)


def entry_type_of(path: pathlib.Path, text: str) -> str | None:
    parsed = parse_frontmatter(text)
    if parsed is None:
        return None
    return entry_type_from_metadata(path, parsed[0])


def entry_type_from_metadata(path: pathlib.Path, metadata: Mapping[str, object]) -> str:
    """解析済みfrontmatterの種別を検証して返す。

    保存値は読み取り互換として正規化する。旧値を持つ項目は現行の種別として扱う。
    """
    entry_type = normalized_wi_type(metadata.get("type"))
    if entry_type is None:
        _outcome.report_failure(
            f"frontmatterのtypeが不正または欠落している（{'・'.join(WI_TYPES)}のいずれかが必要）: {path}",
            next_action=unrepairable_entry_next_action(path.name),
        )
        sys.exit(2)
    return entry_type


def iter_entries(
    private_notes: pathlib.Path,
    states: Iterable[str],
    filter_repo: str | Iterable[str] | None,
    entry_type: str | Iterable[str] = "all",
    *,
    texts: EntryTexts | None = None,
) -> Iterator[tuple[pathlib.Path, str, str, str, str | None]]:
    matcher = _wi_filters.TargetRepoMatcher(filter_repo)
    entry_types = {entry_type} if isinstance(entry_type, str) else set(entry_type)
    texts = texts if texts is not None else EntryTexts()
    selected_states = set(states)
    for path in iter_entry_paths(private_notes):
        text = texts.read(path)
        state = "inbox" if parse_frontmatter(text) is None else entry_state_of(path, text)
        if state not in selected_states:
            continue
        target_repo = parse_target_repo(text)
        actual_type = entry_type_of(path, text)
        if actual_type is not None and not matcher.matches(target_repo):
            continue
        if "all" not in entry_types and actual_type not in entry_types:
            continue
        yield path, target_repo, text, state, actual_type


type EntryRecord = tuple[pathlib.Path, str, str, str, str | None]
"""`iter_entries`が返す1件（パス、対象repo、本文、状態、種別）。"""


def validate_named_filenames(private_notes: pathlib.Path, filenames: list[str]) -> list[tuple[str, str]]:
    for filename in filenames:
        parts = filename.replace("\\", "/").split("/")
        if len(parts) == 2 and parts[0] in WI_STATES and parts[1] not in ("", ".", ".."):
            _outcome.report_failure(
                f"状態名付きのファイル名は受理しない: {filename}",
                next_action=f"状態名を除いたファイル名を指定する: {parts[1]}（showは年月配置を探索する）",
            )
            sys.exit(2)
    names = _wi_filenames.dedup_positional_filenames(filenames, "show")
    return [(name, _wi_filenames.validate_filename(name, private_notes / WI_STATES[0]).name) for name in names]


def read_named_entries(
    private_notes: pathlib.Path,
    filenames: list[tuple[str, str]],
    *,
    target_repo: str | Iterable[str] | None,
    entry_type: Iterable[str] = ("all",),
    source: str | Iterable[str] | None = None,
) -> tuple[list[EntryRecord], list[str]]:
    """検証済みの指定名を状態の優先順で読み、該当項目と見つからない要求名を返す。

    明示名の照会では状態と回答有無を検索条件にしない。同期と表示は呼出側が担う。
    """
    matcher = _wi_filters.TargetRepoMatcher(target_repo)
    kinds = set(entry_type)
    selected: list[EntryRecord] = []
    missing: list[str] = []
    texts = EntryTexts()
    candidates: dict[str, pathlib.Path] = {}

    dated = {normalized: unique_dated_entry_path(private_notes, normalized) for _, normalized in filenames}
    if all(path is not None for path in dated.values()):
        candidates = {name: path for name, path in dated.items() if path is not None}
    else:
        for path in iter_entry_paths(private_notes):
            candidates.setdefault(path.name, path)
    for requested, normalized in filenames:
        path = candidates.get(normalized)
        if path is not None:
            text = texts.read(path)
            parsed = parse_frontmatter(text)
            kind = normalized_wi_type(parsed[0].get("type")) if parsed is not None else None
            if "all" not in kinds and kind not in kinds:
                missing.append(requested)
                continue
            repo = parse_target_repo(text)
            if not matcher.matches(repo) or not _wi_filters.source_matches_any(parse_source(text), source):
                missing.append(requested)
                continue
            try:
                state = entry_state_of(path, text)
            except ActionableError:
                state = "unknown"
            selected.append((path, repo, text, state, kind))
        else:
            missing.append(requested)
    return selected, missing


def select_entries(
    private_notes: pathlib.Path,
    *,
    status: str | Iterable[str],
    target_repo: str | Iterable[str] | None,
    entry_type: str | Iterable[str],
    answered: str | Iterable[str],
    source: str | Iterable[str] | None,
    texts: EntryTexts | None = None,
    important: str | Iterable[str] = "all",
    snapshot: Iterable[EntryRecord] | None = None,
    target_branch: str | None = None,
    branch_settings: _wi_branches.Registry | None = None,
    user_comment: str = "all",
) -> list[EntryRecord]:
    selected: list[EntryRecord] = []
    states = _wi_filters.resolve_states(status)
    kinds = {entry_type} if isinstance(entry_type, str) else set(entry_type)
    matcher = _wi_filters.TargetRepoMatcher(target_repo)
    entries = snapshot if snapshot is not None else iter_entries(private_notes, states, target_repo, entry_type, texts=texts)
    for entry in entries:
        _, _, text, _, actual_type = entry
        if entry[3] not in states or ("all" not in kinds and actual_type not in kinds):
            continue
        if actual_type is not None and not matcher.matches(entry[1]):
            continue
        if not _wi_filters.answered_matches(actual_type, text, answered):
            continue
        if not _wi_filters.source_matches_any(parse_source(text), source):
            continue
        if not _wi_filters.important_matches(text, important):
            continue
        if not _wi_filters.user_comment_matches(text, user_comment):
            continue
        if target_branch is not None and not _wi_branches.matches(text, entry[1], target_branch, branch_settings or {}):
            continue
        selected.append(entry)
    return selected
