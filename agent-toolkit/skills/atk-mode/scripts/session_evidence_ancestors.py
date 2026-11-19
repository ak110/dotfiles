"""委譲先からセッション記録を、既存の親子判定と登録簿で解決する。"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from agent_toolkit._agents_server import record_paths
from agent_toolkit._atk import session_record_format
from agent_toolkit._atk.serve import session_watch
from agent_toolkit._common import host_homes, session_launchers, session_parents, state_paths
from agent_toolkit._plan import owner_records

RESOLVE_NEXT_ACTION = (
    "委譲元から対象会話の識別子または記録の絶対パスを受け取り、"
    "`--claude-session-id`・`--codex-thread-id`・`--transcript`のいずれかで再実行する"
)


class AmbiguousOwnerRecord(ValueError):
    """環境の所有識別子を診断本文へ再掲せず、記録の選択候補を呼出元へ渡す。"""

    def __init__(self, paths: tuple[Path, ...]) -> None:
        super().__init__("所有会話を解決できない: 記録が複数ある")
        self.paths = paths


def owner_record() -> Path:
    """環境が示す所有会話の記録を一意に解決する。所有識別子は診断へ含めない。"""
    identity = os.environ.get("AGENT_TOOLKIT_OWNER_SESSION", "")
    if not identity or identity.startswith(owner_records.PROCESS_ROOT_SESSION_PREFIX):
        raise ValueError("所有会話を解決できない: 会話を示す所有ルートが環境に無い")
    found = record_paths.find_session_record(identity)
    paths = list(found.paths) if found is not None else []
    if len(paths) == 1:
        return paths[0]
    if paths:
        raise AmbiguousOwnerRecord(tuple(paths))
    raise ValueError("所有会話を解決できない: 記録が無い")


def _verified_registry_chain(
    identity: str,
    codex_root: Path,
    launcher_of: Callable[[str], str | None],
) -> tuple[list[session_parents.ParentSource], dict[str, session_watch.RecordScan]] | None:
    """起動結果で裏付けた登録簿の連鎖を返す。裏付けが欠けた場合は全記録の親子解決へ進む。"""
    sources: list[session_parents.ParentSource] = []
    scans: dict[str, session_watch.RecordScan] = {}
    visited: set[str] = set()
    child_id: str | None = None
    current: str | None = identity
    while current is not None:
        if current in visited:
            return None
        visited.add(current)
        found = record_paths.find_session_record(current, codex_home=codex_root)
        if found is None or len(found.paths) != 1 or found.engine not in {"claude", "codex"}:
            return None
        path = found.paths[0]
        scan = session_watch.scan_record(path, found.engine)
        if child_id is not None and child_id not in scan.delegated_ids:
            return None
        scans[str(path)] = scan
        sources.append(session_parents.ParentSource(str(path), found.engine, current, None, scan.fields[3]))
        child_id, current = current, launcher_of(current)
    # 登録簿の欠落だけで途中の会話を最上位としない。所有会話まで起動結果で連結できた場合だけ探索を省く。
    owner = os.environ.get("AGENT_TOOLKIT_OWNER_SESSION", "")
    if (
        len(sources) < 2
        or not owner
        or owner.startswith(owner_records.PROCESS_ROOT_SESSION_PREFIX)
        or sources[-1].session_id != owner
    ):
        return None
    return sources, scans


def delegation_record(scope: str, codex_home: str | None = None) -> Path:
    """呼出元の直接の委譲元、またはその最上位のセッション記録を返す。"""
    identity = (
        os.environ.get("CLAUDE_CODE_SESSION_ID")
        if os.environ.get("AGENT_TOOLKIT_DELEGATED_SESSION") == "1"
        else os.environ.get("CODEX_THREAD_ID")
    )
    if not identity:
        raise ValueError("委譲関係を解決できない: 委譲先自身の識別子が環境に無い")
    claude_home = host_homes.claude_config_dir()
    codex_root = Path(codex_home) if codex_home else host_homes.codex_home()
    sources: list[session_parents.ParentSource] = []
    scans: dict[str, session_watch.RecordScan] = {}
    launchers = session_launchers.launcher_reader(state_paths.state_dir())

    def add(path: Path, engine: str, session_id: str, parent: str | None = None) -> None:
        scan = session_watch.scan_record(path, engine)
        scans[str(path)] = scan
        sources.append(session_parents.ParentSource(str(path), engine, session_id, parent, scan.fields[3]))

    verified = _verified_registry_chain(identity, codex_root, launchers)
    if verified is not None:
        sources, scans = verified
    else:
        for path in session_record_format.claude_session_records(claude_home):
            add(path, "claude", path.stem)
            for agent_id, child_path, parent_path in session_record_format.claude_subagent_records(path):
                add(child_path, "claude", agent_id, parent_path)
        for path in session_record_format.codex_session_records(codex_root):
            add(path, "codex", session_record_format.codex_session_id(path))
    own = [source for source in sources if source.session_id == identity]
    if len(own) != 1:
        raise ValueError("委譲関係を解決できない: 自身の記録が0件または複数ある")
    parents = session_parents.resolve_parent_paths(
        sources,
        delegated_ids=lambda path, _engine: scans[path].delegated_ids,
        launcher_of=launchers,
    )
    by_path = {source.path: source for source in sources}
    current = own[0].path
    visited = {current}
    first: str | None = None
    while current in parents:
        child = by_path[current]
        candidates = [source for source in sources if child.session_id in scans[source.path].delegated_ids]
        if len(candidates) > 1:
            raise ValueError("委譲関係を解決できない: 起動結果が示す親の記録が複数ある")
        parent_path = parents[current]
        if parent_path in visited:
            raise ValueError("委譲関係を解決できない: 親子関係が循環する")
        if sum(source.session_id == by_path[parent_path].session_id for source in sources) != 1:
            raise ValueError("委譲関係を解決できない: 親の識別子に一致する記録が複数ある")
        # 登録簿は/clear以前の会話を指し得る。起動結果とmetadataで裏付けられない参照は受理しない。
        if not candidates and child.subagent_parent_path is None and child.codex_parent_thread_id is None:
            raise ValueError("委譲関係を解決できない: 親の記録に子の起動結果が無い")
        visited.add(parent_path)
        first = first or parent_path
        current = parent_path
    if first is None:
        raise ValueError("委譲関係を解決できない: 直接の委譲元が一意に決まらない")
    if launchers(by_path[current].session_id) is not None:
        raise ValueError("委譲関係を解決できない: 上位のセッション記録を解決できない")
    return Path(first if scope == "parent" else current)
