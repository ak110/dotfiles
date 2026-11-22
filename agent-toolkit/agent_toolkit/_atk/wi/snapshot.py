import json
import pathlib
from collections.abc import Iterable

from agent_toolkit._atk.wi.entries import EntryRecord, canonical_repo
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._git.remote import normalize_remote_url

type Identifier = tuple[str, str]


def identifiers(entries: Iterable[EntryRecord]) -> frozenset[Identifier]:
    cache: dict[str, str | None] = {}
    return frozenset((canonical_repo(repo, cache) or "", path.name) for path, repo, *_ in entries)


def save(path: pathlib.Path, entries: Iterable[EntryRecord]) -> None:
    payload = [{"target_repo": repo, "filename": name} for repo, name in sorted(identifiers(entries))]
    try:
        atomic_write(path, json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError as error:
        raise ActionableError(
            f"開始時集合を保存できない: {path}: {error}",
            next_action="--snapshot-outputの保存先と書込権限を確認し、初回取得を再実行する",
        ) from error


def read(path: pathlib.Path) -> frozenset[Identifier]:
    """保存した集合を検証して読み、不正・読取不能は基準を変更せず失敗させる。"""
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, list):
            raise ValueError("JSON配列が必要")
        result: set[Identifier] = set()
        for item in payload:
            if not isinstance(item, dict) or set(item) != {"target_repo", "filename"}:
                raise ValueError("target_repoとfilenameだけを持つオブジェクトが必要")
            repo, name = item["target_repo"], item["filename"]
            if not isinstance(repo, str) or not isinstance(name, str):
                raise ValueError("target_repoとfilenameには文字列が必要")
            if not name.endswith(".md") or "/" in name or "\\" in name or name == ".md":
                raise ValueError("filenameにはWIのファイル名が必要")

            if repo and normalize_remote_url(repo) != repo:
                raise ValueError("target_repoには正規化済みの識別子が必要")
            result.add((repo, name))
        return frozenset(result)
    except (OSError, UnicodeError, ValueError) as error:
        raise ActionableError(
            f"開始時集合を読み込めない: {path}: {error}",
            next_action="同じ実行の開始時に保存したJSONと読取権限を確認し、--new-sinceへ指定し直す。基準は更新しない",
        ) from error


def new_entries(entries: Iterable[EntryRecord], baseline: frozenset[Identifier]) -> list[EntryRecord]:
    cache: dict[str, str | None] = {}
    return [entry for entry in entries if (canonical_repo(entry[1], cache) or "", entry[0].name) not in baseline]
