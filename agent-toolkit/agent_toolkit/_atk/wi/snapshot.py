import hashlib
import json
import os
import pathlib
import uuid
from collections.abc import Callable, Iterable

from agent_toolkit._atk import managed_temp
from agent_toolkit._atk.wi import uwi_scan
from agent_toolkit._atk.wi.constants import WI_TYPE_UWI
from agent_toolkit._atk.wi.entries import EntryRecord, canonical_repo
from agent_toolkit._common import delegated_session, next_action, private_directory, process_loop_session, session_state
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._git.remote import normalize_remote_url

type Identifier = tuple[str, str]


def identifiers(entries: Iterable[EntryRecord]) -> frozenset[Identifier]:
    cache: dict[str, str | None] = {}
    return frozenset((canonical_repo(repo, cache) or "", path.name) for path, repo, *_ in entries)


def save(path: pathlib.Path, entries: Iterable[EntryRecord]) -> str:
    encoded = _encode(identifiers(entries))
    _write_snapshot(path, encoded)
    return hashlib.sha256(encoded).hexdigest()


def _encode(values: Iterable[Identifier]) -> bytes:
    payload = [{"target_repo": repo, "filename": name} for repo, name in sorted(values)]
    return (json.dumps(payload, ensure_ascii=False) + "\n").encode()


def _write_snapshot(path: pathlib.Path, encoded: bytes) -> None:
    try:
        atomic_write(path, encoded)
    except OSError as error:
        raise ActionableError(
            f"開始時集合を保存できない: {path}: {error}",
            next_action="--snapshot-outputの保存先と書込権限を確認し、初回取得を再実行する",
        ) from error


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


_PROCESSING_STARTED = "process_loop_processing_started"


def _progress_session_id(*, include_delegate: bool = False) -> str | None:
    if os.environ.get("AGENT_TOOLKIT_PROCESS_LOOP_SESSION") != "1":
        return None
    conversation = delegated_session.current_conversation_session_id(os.environ)
    expected = process_loop_session._expected_session_id(os.environ)  # noqa: SLF001  # pylint: disable=protected-access
    if delegated_session.is_delegated(os.environ):
        owner = delegated_session.owner_session_id(os.environ)
        return expected if include_delegate and owner == expected else None
    return expected if conversation is None or conversation == expected else None


def processing_started(session_id: str, private_notes: pathlib.Path) -> frozenset[str]:
    roots = session_state.read_state(session_id).get(_PROCESSING_STARTED)
    names = roots.get(str(private_notes.resolve())) if isinstance(roots, dict) else None
    return frozenset(name for name in names if isinstance(name, str)) if isinstance(names, list) else frozenset()


def remember_processing_started(private_notes: pathlib.Path, filenames: Iterable[str]) -> None:
    session_id = _progress_session_id(include_delegate=True)
    new_names = set(filenames)
    if not session_id or not new_names:
        return
    location = str(private_notes.resolve())

    def remember(state: dict) -> dict:
        previous = state.get(_PROCESSING_STARTED)
        roots = previous if isinstance(previous, dict) else {}
        names = roots.get(location)
        known = {name for name in names if isinstance(name, str)} if isinstance(names, list) else set()
        return {**state, _PROCESSING_STARTED: {**roots, location: sorted(known | new_names)}}

    session_state.update_state(session_id, remember)


_LEGACY_START = "process_loop_progress_snapshots"
_START_DIRECTORY = "wi-circuit-start"
_START_UNAVAILABLE = "process_loop_start_directory_unavailable"
_START_LOCATION = "process_loop_start_directory"


def _registered_directory(state: dict) -> pathlib.Path | None:
    value = state.get(_START_LOCATION)
    return pathlib.Path(value) if isinstance(value, str) and pathlib.Path(value).is_absolute() else None


def _read_start(directory: pathlib.Path) -> dict | None:
    try:
        return json.loads((directory / "source.json").read_bytes())
    except FileNotFoundError:
        return None


def record_list_start(
    private_notes: pathlib.Path,
    entries: tuple[EntryRecord, ...],
    *,
    configured_start: Callable[[], tuple[list[tuple[str, str | None]], frozenset[Identifier]]],
    candidate_list: bool,
    output: pathlib.Path | None,
    configured_output: bool,
) -> bool:
    if not candidate_list and output is None:
        return False
    session_id = _progress_session_id()
    direct = session_id is None
    if session_id is None:
        if delegated_session.is_delegated(os.environ):
            return False
        session_id = delegated_session.current_conversation_session_id(os.environ)
        if not session_id or not session_state.read_state(session_id).get(session_state.PROCESS_WI_STATE_KEY):
            return False
    state = session_state.read_state(session_id)
    if state.get(_START_UNAVAILABLE):
        _unavailable("巡回の開始時集合は初回取得で保存できなかった", output)
        return True
    if _LEGACY_START in state:
        if output is not None:
            scope = configured_start()[0] if configured_output else None
            _export_legacy(state[_LEGACY_START], private_notes, scope, output)
        return True
    try:
        registered = _registered_directory(state)
        name = f"{_START_DIRECTORY}-{uuid.uuid4().hex}" if direct else _START_DIRECTORY
        directory = registered or managed_temp.resolve_session_temp(session_id) / name
        created = registered is None and _claim_start(directory)
    except (OSError, managed_temp.ManagedTempError) as error:
        saved = session_state.update_state(session_id, lambda state: {**state, _START_UNAVAILABLE: True})
        if not saved:
            raise ActionableError(
                "巡回の開始時集合と失敗の記録を保存できない",
                next_action="この巡回では候補を選定せず、保存先の障害を解消して新しい巡回を開始する",
            ) from error
        _unavailable(f"巡回の開始時集合を保存できない: {error}", output)
        return True
    path = directory / "baseline.json"
    if created:
        # 集合の確定前に試行を記録し、途中で失敗しても次の一覧を初回にしない。  # pyfltr: allow-comment
        try:
            digest = save(path, entries)
            unanswered = identifiers(
                entry for entry in entries if entry[4] == WI_TYPE_UWI and uwi_scan.answer_label(entry[2]) == "unanswered"
            )
            targets, configured_ids = configured_start()
            new_source = {
                "targets": targets,
                "configured": sorted(configured_ids),
                "notes": str(private_notes.resolve()),
                "digest": digest,
                "unanswered": sorted(unanswered),
            }
            atomic_write(directory / "source.json", json.dumps(new_source, ensure_ascii=False).encode())
            session_state.update_state(session_id, lambda current: {**current, _START_LOCATION: str(directory.resolve())})
        except (OSError, ActionableError) as error:
            _unavailable(f"巡回の開始時集合を保存できない: {error}", output)
            return True

    if output is not None:
        source = _read_start(directory)
        if source is None or source["notes"] != str(private_notes.resolve()):
            _unavailable("巡回の開始時集合を参照できない", output)
        else:
            values = read(path, expected_digest=source["digest"])
            if configured_output:
                values &= frozenset(tuple(value) for value in source["configured"])
            _export(output, values, (path, directory / "source.json"))
    return True


def clear_direct_start(state: dict) -> None:
    state.pop(_START_LOCATION, None)
    state.pop(_START_UNAVAILABLE, None)


def _claim_start(directory: pathlib.Path) -> bool:
    try:
        directory.mkdir(mode=private_directory.MODE)
    except FileExistsError:
        return False
    return True


def _unavailable(reason: str, output: pathlib.Path | None) -> None:
    action = "初回取得の診断を確認し、この巡回では現在の一覧を基準にしないでください"
    if output is not None:
        raise ActionableError(reason, next_action=action)
    next_action.report(reason, next_action=action)


def _export_legacy(rows: object, notes: pathlib.Path, scope: list[tuple[str, str | None]] | None, output: pathlib.Path) -> None:
    if not isinstance(rows, dict) or not rows:
        _unavailable("旧版の開始時集合を参照できない", output)
        return
    values_by_source = set()
    paths = []
    if scope is not None and any(branch is not None for _, branch in scope):
        _unavailable("旧版の開始時集合から登録系統別の識別子を区別できない", output)
        return
    for row in rows.values():
        if not isinstance(row, dict) or row.get("notes") != str(notes.resolve()):
            _unavailable("旧版の開始時集合の保存先が一致しない", output)
            return
        if not isinstance(row.get("digest"), str) or not isinstance(row.get("path"), str):
            _unavailable("旧版の開始時集合の参照が不正", output)
            return
        path = pathlib.Path(row["path"])
        values_by_source.add(read(path, expected_digest=row["digest"]))
        paths.append(path)
    if len(values_by_source) != 1:
        _unavailable("旧版の開始時集合が複数あり、共通の受け渡し元を確定できない", output)
        return
    values = next(iter(values_by_source))
    if scope is not None:
        repos = {repo for repo, _ in scope}
        values = frozenset(value for value in values if value[0] in repos or (repos and not value[0]))
    _export(output, values, paths)


def _export(output: pathlib.Path, values: Iterable[Identifier], protected: Iterable[pathlib.Path]) -> None:
    encoded = _encode(values)
    if output.resolve() in {path.resolve() for path in protected}:
        if output.read_bytes() != encoded:
            _unavailable("明示出力の指定先が巡回の開始時記録と同じ", output)
        return
    _write_snapshot(output, encoded)


def progress_sources(session_id: str) -> dict:
    state = session_state.read_state(session_id)
    if _LEGACY_START in state:
        legacy = state[_LEGACY_START]
        return legacy if isinstance(legacy, dict) else {}
    directory = _registered_directory(state)
    if directory is None:
        root = managed_temp.find_session_temp(session_id)
        if root is None:
            return {}
        directory = root / _START_DIRECTORY
    source = _read_start(directory)
    if source is None:
        return {}
    return {
        json.dumps([repo, branch]): {
            "repo": repo,
            "branch": branch,
            "path": str(directory / "baseline.json"),
            "notes": source["notes"],
            "digest": source["digest"],
            "unanswered": source["unanswered"],
        }
        for repo, branch in source["targets"]
    }
