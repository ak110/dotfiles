"""managed-tempのディレクトリと、会話ごとのセッションのmanaged-tempの作成・解決。

作成した領域は登録簿や管理情報を持たず、一時root直下の実体だけで管理する。
回収は作成者の明示回収と、`sweep`の期限による自動回収が担う。
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import stat
import tempfile

from agent_toolkit._atk.managed_temp import paths
from agent_toolkit._atk.managed_temp.errors import ManagedTempError

SESSION_TEMP_PREFIX = "session"
"""会話ごとに再利用するセッションのmanaged-tempの接頭辞。

フックとagents_serverの双方が同じ領域を求めるため、両者が読み込む下位の層にあるこのモジュールで定義する。
"""


def create_managed_temp(prefix: str) -> pathlib.Path:
    """一時root直下へ所有者だけが読み書きできるディレクトリを作成し、絶対パスを返す。"""
    paths.require_valid_prefix(prefix)
    root = paths.temp_root()
    try:
        return pathlib.Path(tempfile.mkdtemp(prefix=f"{prefix}-", dir=root))
    except OSError as error:
        raise ManagedTempError(f"一時ディレクトリを作成できない: {root}: {error}") from error


def session_temp_path(session_id: str) -> pathlib.Path:
    """会話の識別子から決まるセッションのmanaged-tempの絶対パスを返す。

    フック、agents_server、委譲起動と圧縮復元が同じ関数で解決し、登録簿を介さずに同じ領域へ収束させる。
    """
    digest = hashlib.sha256(session_id.encode()).hexdigest()[:16]
    return paths.temp_root_path() / f"{SESSION_TEMP_PREFIX}-{digest}"


def _usable_directory(path: pathlib.Path) -> bool:
    """一時root直下に実在する、リンクでない自分所有のディレクトリかを返す。"""
    if path.parent != paths.temp_root_path():
        return False
    try:
        metadata = path.lstat()
    except OSError:
        return False
    if not stat.S_ISDIR(metadata.st_mode) or paths.is_link(metadata):
        return False
    return os.name != "posix" or metadata.st_uid == os.geteuid()


def _legacy_session_temp(session_id: str) -> pathlib.Path | None:
    """旧版が会話へ割り当てた領域を、参照を読むだけで返す。成立しない参照は無視する。

    旧版の会話を新版で再開しても同じ絶対パスとデータを使い続けるため、参照の指すpathが
    一時root直下の実在する非リンクディレクトリである場合に限り再利用する。参照は書き換えない。
    """
    reference = paths.state_root_path() / ".sessions" / hashlib.sha256(session_id.encode()).hexdigest()
    try:
        record = json.loads(reference.read_text(encoding="utf-8"))
    except OSError, UnicodeError, ValueError:
        return None
    if not isinstance(record, dict) or not isinstance(record.get("path"), str):
        return None
    path = pathlib.Path(record["path"])
    return path if path.is_absolute() and _usable_directory(path) else None


def find_session_temp(session_id: str) -> pathlib.Path | None:
    """既存のセッションのmanaged-tempを、作成せずに返す。"""
    legacy = _legacy_session_temp(session_id)
    if legacy is not None:
        return legacy
    path = session_temp_path(session_id)
    return path if _usable_directory(path) else None


def resolve_session_temp(session_id: str) -> pathlib.Path:
    """セッションのmanaged-tempを返す。旧版の参照が無ければ決定的なパスを作成する。"""
    legacy = _legacy_session_temp(session_id)
    if legacy is not None:
        return legacy
    path = session_temp_path(session_id)
    paths.temp_root()
    try:
        path.mkdir(mode=0o700, exist_ok=True)
    except OSError as error:
        raise ManagedTempError(f"セッションのmanaged-tempを作成できない: {path}: {error}") from error
    if not _usable_directory(path):
        raise ManagedTempError(
            f"セッションのmanaged-tempが自分所有の通常ディレクトリではない: {path}",
            next_action=ManagedTempError.PERMISSION_NEXT_ACTION,
        )
    return path


def create_session_temp(prefix: str, session_root: pathlib.Path | str) -> pathlib.Path:
    """セッションのmanaged-tempの直下へ作業ディレクトリを作成し、絶対パスを返す。"""
    paths.require_valid_prefix(prefix)
    root = paths.contained_target(session_root)
    try:
        return pathlib.Path(tempfile.mkdtemp(prefix=f"{prefix}-", dir=root))
    except OSError as error:
        raise ManagedTempError(f"セッション内の作業ディレクトリを作成できない: {root}: {error}") from error


def create_output_temp(prefix: str) -> pathlib.Path:
    """会話が分かる保存物をセッションのmanaged-tempの子へ、端末の保存物を独立領域へ作成する。"""
    session_id = os.environ.get("CLAUDE_CODE_SESSION_ID") or os.environ.get("CODEX_THREAD_ID")
    if session_id:
        return create_session_temp(prefix, resolve_session_temp(session_id))
    return create_managed_temp(prefix)
