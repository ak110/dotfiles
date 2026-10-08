"""agents_serverの共有状態ディレクトリの配置と、配下のファイル名に使うsession識別子の検証。"""

from __future__ import annotations

import logging
import pathlib
import re

from agent_toolkit._common import state_paths as _state_paths

_SESSION_ID_PATTERN = re.compile(r"^[0-9A-Za-z_-]+$")


RESERVED_DIRECTORY_NAMES = frozenset({"aliases", "sessions", "compaction"})
"""状態ディレクトリ直下でrootに属さない管理用ディレクトリの名前（索引・session登録簿・計測記録）。"""


def status_directory(root_session_id: str, state_root: pathlib.Path | None = None) -> pathlib.Path:
    """ルートsessionの状態ファイルディレクトリを返す。"""
    root = _state_paths.state_dir() if state_root is None else state_root
    return root / "agents-server" / root_session_id


def session_log_path(root_session_id: str, session_id: str, state_root: pathlib.Path | None = None) -> pathlib.Path:
    """Antigravityの公開JSONイベントを保持するsession別JSONLのパスを返す。"""
    if not valid_session_id(root_session_id) or not valid_session_id(session_id):
        raise ValueError("invalid session identifier")
    return status_directory(root_session_id, state_root) / "logs" / f"{session_id}.jsonl"


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
    """共有状態に存在する有効なルートsession識別子を安定順で返す。"""
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
    """現行session識別子からルートsession識別子を引く索引ディレクトリを返す。"""
    root = _state_paths.state_dir() if state_root is None else state_root
    return root / "agents-server" / "aliases"


def valid_session_id(session_id: str) -> bool:
    """session識別子が状態ファイル名へ使用できる形式かを返す。"""
    return bool(session_id and _SESSION_ID_PATTERN.fullmatch(session_id))


def results_directory(root_session_id: str, state_root: pathlib.Path | None = None) -> pathlib.Path:
    """ルートsessionの終端結果ディレクトリを返す。"""
    return status_directory(root_session_id, state_root) / "results"


def wait_targets_directory(
    root_session_id: str,
    owner_status_file: str,
    state_root: pathlib.Path | None = None,
) -> pathlib.Path:
    """書込主体ごとの待機対象登録簿のディレクトリを返す。"""
    return status_directory(root_session_id, state_root) / "wait-targets" / owner_status_file


def notices_directory(root_session_id: str, state_root: pathlib.Path | None = None) -> pathlib.Path:
    """ルートsession宛ての未回収通知ディレクトリを返す。"""
    return status_directory(root_session_id, state_root) / "notices"


def hosts_directory(root_session_id: str, state_root: pathlib.Path | None = None) -> pathlib.Path:
    """書込主体から委譲元threadへの索引ディレクトリを返す。"""
    return status_directory(root_session_id, state_root) / "hosts"


_LOG = logging.getLogger("agent-toolkit.agents-server.status-file")
