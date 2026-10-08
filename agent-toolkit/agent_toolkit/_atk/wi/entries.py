"""WIエントリの走査、種別の判定、処理結果の追記。"""

import dataclasses
import datetime
import pathlib
import sys
from collections.abc import Iterable, Iterator, Mapping

from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._atk.wi import filenames as _wi_filenames
from agent_toolkit._atk.wi import filters as _wi_filters
from agent_toolkit._atk.wi.constants import WI_STATES, WI_TYPES, normalized_wi_type, unrepairable_entry_next_action
from agent_toolkit._atk.wi.formatters import parse_source, parse_target_repo
from agent_toolkit._atk.wi.frontmatter import parse_frontmatter, write_entry_text
from agent_toolkit._git import remote as _git_remote


@dataclasses.dataclass(frozen=True)
class CommitMetadata:
    """WIの処理結果へ保存するcommit識別情報。`short_oid`は記録時に一意な長さの短縮OID。"""

    short_oid: str
    subject: str


def subdir(private_notes: pathlib.Path, name: str) -> pathlib.Path:
    """管理repo直下の指定サブディレクトリパスを返す。必要時に作成する。"""
    path = private_notes / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def stamp_result(
    path: pathlib.Path,
    *,
    outcome: str,
    now: datetime.datetime,
    commit: CommitMetadata | None = None,
    note: str | None = None,
) -> None:
    """対象ファイル末尾へ`## 処理結果`節を追記する。

    outcomeは`adopted`・`rejected`のいずれかを受け取る。
    commit・noteは省略可能で、指定時のみ対応する箇条書き項目を追加する。
    """
    body = path.read_text(encoding="utf-8")
    if not body.endswith("\n"):
        body += "\n"
    lines = [
        "",
        "## 処理結果",
        "",
        f"- 採否: {outcome}",
        f"- 処理日時: {now.isoformat(timespec='seconds')}",
    ]
    if commit:
        lines.extend(
            (
                f"- 対応commit: {commit.short_oid}",
                f"- 対応commit件名: {commit.subject}",
            )
        )
    if note:
        lines.append(f"- メモ: {note}")
    body += "\n".join(lines) + "\n"
    write_entry_text(path, body)


def canonical_repo(value: str, cache: dict[str, str | None]) -> str | None:
    """リポジトリ識別子を操作単位のキャッシュを介して正規化する。"""
    return _git_remote.canonical_repo(value, cache)


def iter_inbox_entries(inbox_dir: pathlib.Path, target_repo: str | None = None) -> Iterator[tuple[pathlib.Path, str, str]]:
    """inbox配下の`.md`ファイルを名前順に走査し、`(path, target_repo, text)`を返す。

    `target_repo`指定時は、frontmatterの`target_repo`が`TargetRepoMatcher`の判定で一致するエントリのみ返す。
    ディレクトリ不在時は何も返さない。
    """
    if not inbox_dir.exists():
        return
    matcher = _wi_filters.TargetRepoMatcher(target_repo)
    for path in sorted(inbox_dir.iterdir()):
        if path.suffix != ".md":
            continue
        text = path.read_text(encoding="utf-8")
        entry_repo = parse_target_repo(text)
        if not matcher.matches(entry_repo):
            continue
        yield path, entry_repo, text


def parse_type(text: str) -> str | None:
    """本文先頭のfrontmatterから`type`を抽出する。"""
    parsed = parse_frontmatter(text)
    if parsed is None:
        return None
    return normalized_wi_type(parsed[0].get("type"))


def entry_type_of(path: pathlib.Path, text: str) -> str | None:
    """エントリの種別を検証して返す。"""
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
) -> Iterator[tuple[pathlib.Path, str, str, str, str | None]]:
    """指定状態のエントリをパス・対象repo・本文・状態・種別の順で列挙する。

    frontmatter全体が破損したエントリ（種別がNone）は`target_repo`を読めないため、対象repoの限定条件によらず返す。
    """
    matcher = _wi_filters.TargetRepoMatcher(filter_repo)
    entry_types = {entry_type} if isinstance(entry_type, str) else set(entry_type)
    for state in states:
        state_dir = private_notes / state
        for path, target_repo, text in iter_inbox_entries(state_dir):
            actual_type = entry_type_of(path, text)
            if actual_type is not None and not matcher.matches(target_repo):
                continue
            if "all" not in entry_types and actual_type not in entry_types:
                continue
            yield path, target_repo, text, state, actual_type


def count_awi(awi_dir: pathlib.Path, target_repo: str | None = None) -> int:
    """指定ディレクトリ配下の`*.md`ファイル件数を返す。

    `target_repo`指定時はfrontmatterの`target_repo`が一致するエントリのみ数える。
    未指定時は全リポジトリ分を数える。
    """
    if not awi_dir.exists():
        return 0
    if target_repo is None:
        return sum(1 for p in awi_dir.iterdir() if p.suffix == ".md")
    return sum(1 for _ in iter_inbox_entries(awi_dir, target_repo))


type EntryRecord = tuple[pathlib.Path, str, str, str, str | None]
"""`iter_entries`が返す1件（パス、対象repo、本文、状態、種別）。"""


def validate_named_filenames(private_notes: pathlib.Path, filenames: list[str]) -> list[tuple[str, str]]:
    """全状態から探す明示名を検証し、要求名と正規化した名前を要求順に返す。"""
    for filename in filenames:
        parts = filename.replace("\\", "/").split("/")
        if len(parts) == 2 and parts[0] in WI_STATES and parts[1] not in ("", ".", ".."):
            _outcome.report_failure(
                f"状態名付きのファイル名は受理しない: {filename}",
                next_action=f"状態名を除いたファイル名を指定する: {parts[1]}（showは全状態フォルダを探索する）",
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
    for requested, normalized in filenames:
        for state in WI_STATES:
            path = private_notes / state / normalized
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            kind = entry_type_of(path, text)
            if "all" not in kinds and kind not in kinds:
                continue
            repo = parse_target_repo(text)
            if not matcher.matches(repo) or not _wi_filters.source_matches_any(parse_source(text), source):
                continue
            selected.append((path, repo, text, state, kind))
            break
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
) -> list[EntryRecord]:
    """状態・対象repo・種別・回答状況・sourceの5条件の積集合を返す。一覧系のサブコマンドが共有する。"""
    selected: list[EntryRecord] = []
    for entry in iter_entries(private_notes, _wi_filters.resolve_states(status), target_repo, entry_type):
        _, _, text, _, actual_type = entry
        if not _wi_filters.answered_matches(actual_type, text, answered):
            continue
        if not _wi_filters.source_matches_any(parse_source(text), source):
            continue
        selected.append(entry)
    return selected
