from __future__ import annotations

import logging
import pathlib
import re

from agent_toolkit._common import state_paths as _state_paths

_SESSION_ID_PATTERN = re.compile(r"^[0-9A-Za-z_-]+$")


RESERVED_DIRECTORY_NAMES = frozenset({"aliases", "sessions", "compaction", "admission"})
"""状態ディレクトリ直下でrootに属さない管理用ディレクトリの名前（索引・session登録簿・計測記録・起動待ち）。"""


def status_directory(root_session_id: str, state_root: pathlib.Path | None = None) -> pathlib.Path:
    root = _state_paths.state_dir() if state_root is None else state_root
    return root / "agents-server" / root_session_id


def session_log_path(
    root_session_id: str, session_id: str, state_root: pathlib.Path | None = None, *, engine: str = "agy"
) -> pathlib.Path:
    if not valid_session_id(root_session_id) or not valid_session_id(session_id):
        raise ValueError("invalid session identifier")
    directory = status_directory(root_session_id, state_root) / "logs"
    if engine == "opencode":
        directory /= "opencode"
    elif engine != "agy":
        raise ValueError(f"unsupported CLI log engine: {engine}")
    return directory / f"{session_id}.jsonl"


def list_status_files(
    root_session_id: str, state_root: pathlib.Path | None = None, *, strict: bool = False
) -> list[pathlib.Path]:
    """書込主体ごとの状態ファイルを絶対パスの安定順で返す。

    書込主体ごとに`root.json`と`<host_session_id>.json`へ分かれるため、
    読取主体は単一のファイル名を組み立てない。ファイル名の規則を定めるのは
    `resolve_status_file_identity`である。
    """
    directory = status_directory(root_session_id, state_root)
    try:
        paths = [path.absolute() for path in directory.iterdir() if path.suffix == ".json" and path.is_file()]
    except FileNotFoundError:
        return []
    except OSError:
        if strict:
            raise
        return []
    return sorted(paths)


def list_root_session_ids(state_root: pathlib.Path | None = None, *, strict: bool = False) -> list[str]:
    root = _state_paths.state_dir() if state_root is None else state_root
    base = root / "agents-server"
    try:
        identifiers = [
            path.name
            for path in base.iterdir()
            if path.is_dir() and path.name not in RESERVED_DIRECTORY_NAMES and valid_session_id(path.name)
        ]
    except FileNotFoundError:
        return []
    except OSError:
        if strict:
            raise
        return []
    return sorted(identifiers)


def aliases_directory(state_root: pathlib.Path | None = None) -> pathlib.Path:
    root = _state_paths.state_dir() if state_root is None else state_root
    return root / "agents-server" / "aliases"


def valid_session_id(session_id: str) -> bool:
    return bool(session_id and _SESSION_ID_PATTERN.fullmatch(session_id))


def results_directory(root_session_id: str, state_root: pathlib.Path | None = None) -> pathlib.Path:
    return status_directory(root_session_id, state_root) / "results"


def published_markers_directory(results: pathlib.Path, session_id: str) -> pathlib.Path:
    """結果を公開した終了ごとの印（`<turn番号>-<終了時刻のSHA-256>`）を置く、sessionごとのディレクトリを返す。

    原本を回収した後も印は残るため、印があり原本が無い終了は回収済みを表す。
    """
    return results / ".published" / session_id


def wait_targets_directory(
    root_session_id: str,
    owner_status_file: str,
    state_root: pathlib.Path | None = None,
) -> pathlib.Path:
    """書込主体ごとの待機対象登録簿のディレクトリを返す。"""
    return status_directory(root_session_id, state_root) / "wait-targets" / owner_status_file


def notices_directory(root_session_id: str, state_root: pathlib.Path | None = None) -> pathlib.Path:
    return status_directory(root_session_id, state_root) / "notices"


def hosts_directory(root_session_id: str, state_root: pathlib.Path | None = None) -> pathlib.Path:
    return status_directory(root_session_id, state_root) / "hosts"


_LOG = logging.getLogger("agent-toolkit.agents-server.status-file")


def result_session_id(path: pathlib.Path) -> str | None:
    if path.suffix != ".json":
        return None
    identifier, separator, end = path.stem.partition(".")
    if valid_session_id(identifier) and (not separator or re.fullmatch(r"[0-9]+-[a-f0-9]{64}", end)):
        return identifier
    return None
