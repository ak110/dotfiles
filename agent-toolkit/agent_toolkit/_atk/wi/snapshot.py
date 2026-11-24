import hashlib
import json
import os
import pathlib
from collections.abc import Iterable

from agent_toolkit._atk.wi import uwi_scan
from agent_toolkit._atk.wi.constants import WI_TYPE_UWI
from agent_toolkit._atk.wi.entries import EntryRecord, canonical_repo
from agent_toolkit._common import delegated_session, process_loop_session, session_state
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._git.remote import normalize_remote_url

type Identifier = tuple[str, str]


def identifiers(entries: Iterable[EntryRecord]) -> frozenset[Identifier]:
    cache: dict[str, str | None] = {}
    return frozenset((canonical_repo(repo, cache) or "", path.name) for path, repo, *_ in entries)


def save(path: pathlib.Path, entries: Iterable[EntryRecord]) -> str:
    payload = [{"target_repo": repo, "filename": name} for repo, name in sorted(identifiers(entries))]
    encoded = (json.dumps(payload, ensure_ascii=False) + "\n").encode()
    try:
        atomic_write(path, encoded)
    except OSError as error:
        raise ActionableError(
            f"開始時集合を保存できない: {path}: {error}",
            next_action="--snapshot-outputの保存先と書込権限を確認し、初回取得を再実行する",
        ) from error
    return hashlib.sha256(encoded).hexdigest()


def read(path: pathlib.Path, *, expected_digest: str | None = None) -> frozenset[Identifier]:
    """保存した集合を検証して読み、不正・読取不能は基準を変更せず失敗させる。"""
    try:
        encoded = path.read_bytes()
        if expected_digest is not None and hashlib.sha256(encoded).hexdigest() != expected_digest:
            raise ValueError("開始時の保存後に内容が変化した")
        payload = json.loads(encoded.decode("utf-8-sig"))
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


_PROGRESS_SNAPSHOTS = "process_loop_progress_snapshots"


def remember_progress_start(
    path: pathlib.Path,
    private_notes: pathlib.Path,
    targets: Iterable[tuple[str, str | None]],
    digest: str,
    entries: Iterable[EntryRecord],
) -> None:
    if os.environ.get("AGENT_TOOLKIT_PROCESS_LOOP_SESSION") != "1" or delegated_session.is_delegated(os.environ):
        return
    conversation = delegated_session.current_conversation_session_id(os.environ)
    expected = process_loop_session._expected_session_id(os.environ)  # noqa: SLF001  # pylint: disable=protected-access
    if not expected or (conversation is not None and conversation != expected):
        return
    unanswered = identifiers(entry for entry in entries if entry[4] == WI_TYPE_UWI and not uwi_scan.is_uwi_answered(entry[2]))
    updates = {
        json.dumps([repo, branch]): {
            "repo": repo,
            "branch": branch,
            "path": str(path.resolve()),
            "digest": digest,
            "notes": str(private_notes.resolve()),
            "unanswered": sorted(unanswered),
        }
        for repo, branch in targets
    }
    if not updates:
        return

    def remember(state: dict) -> dict:
        previous = state.get(_PROGRESS_SNAPSHOTS)
        rows = previous if isinstance(previous, dict) else {}
        return {**state, _PROGRESS_SNAPSHOTS: {**updates, **rows}}

    session_state.update_state(expected, remember)


def progress_sources(session_id: str) -> dict:
    sources = session_state.read_state(session_id).get(_PROGRESS_SNAPSHOTS)
    return sources if isinstance(sources, dict) else {}
