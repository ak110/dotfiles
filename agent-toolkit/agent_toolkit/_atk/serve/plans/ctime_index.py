"""計画ファイルの作成日時インデックス（ホスト・root・相対パスをキーとする単一JSON）の読み書きと排他。

同一ホスト上のリモートヘルパー（`atk_serve_plans_remote_helper.py`）と同じファイルを共有するため、
キーと値の形式は両実装で一致させる。旧形式（1エントリ1ファイル）のキャッシュの移行と一時ファイルの回収も扱う。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import pathlib
import re
import typing

import platformdirs

from agent_toolkit._common import file_lock as _file_lock

# 作成日時の永続インデックス。ホスト・root・相対パスの3項をキーとする単一JSONへ集約する。
# 同一ホスト上でリモートヘルパー（`atk_serve_plans_remote_helper.py`）も同じファイルを共有するため、
# キーと値の形式を両実装で一致させる。
# ディレクトリ名は計画ファイル閲覧機能が`atk serve`へ統合される前から蓄積した索引をそのまま使うため維持する。
# 名前を変えると初回観測時刻が失われ、一覧の並び順が変わる。
_CREATION_TIME_INDEX_PATH = (
    pathlib.Path(platformdirs.user_cache_dir("claude-plans-viewer", appauthor=False)) / "creation-times" / "index.json"
)


# 旧形式（1エントリ1ファイル）のキャッシュ名。sha256 hexdigestと`.json`から成る。
_LEGACY_CACHE_NAME_RE = re.compile(r"^[0-9a-f]{64}\.json$")


# 旧実装が生成した一時ファイル名。`.<sha256 hexdigest>.json.<pid>.<スレッドID>.tmp`。
_LEGACY_TEMPORARY_NAME_RE = re.compile(r"^\.[0-9a-f]{64}\.json\.\d+\.\d+\.tmp$")


# --------------------------------------------------------------------------------------
# 作成日時インデックス
# --------------------------------------------------------------------------------------


@contextlib.contextmanager
def _exclusive_file_lock(path: pathlib.Path) -> typing.Iterator[None]:
    """`path`をロックファイルとしてプロセス間の排他ロックを保持する。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        _file_lock.acquire_lock(handle)
        try:
            yield
        finally:
            _file_lock.release_lock(handle)


def _index_lock_path() -> pathlib.Path:
    """作成日時インデックスの排他ロックファイルのパス。"""
    return _CREATION_TIME_INDEX_PATH.with_name(_CREATION_TIME_INDEX_PATH.name + ".lock")


def _enter_index_lock(stack: contextlib.ExitStack) -> bool:
    """作成日時インデックスの排他ロックを`stack`へ登録する。取得できない場合は`False`を返す。

    キャッシュディレクトリを作成・書き込みできない環境ではロックファイルを開けず`OSError`となる。
    作成日時キャッシュの失敗で一覧機能を止めないため、この例外は呼び出し元へ伝播させない。
    """
    try:
        stack.enter_context(_exclusive_file_lock(_index_lock_path()))
    except OSError:
        return False
    return True


def _index_key(host: str, root_key: str, rel: str) -> str:
    r"""インデックスのキー。`(host, root, rel)`を`\0`で連結した文字列のsha256 hexdigest。"""
    return hashlib.sha256(f"{host}\0{root_key}\0{rel}".encode()).hexdigest()


def _root_key(root: pathlib.Path) -> str:
    """インデックスのキーと値へ用いる`root`の正規化表記。"""
    return str(root.resolve()).replace("\\", "/")


def _entry_ctime(entry: typing.Any) -> float | None:
    """インデックスまたは旧形式のエントリから作成日時を取り出す。取得できない場合はNone。"""
    value = entry.get("ctime_epoch")
    return float(value) if isinstance(value, (int, float)) else None


def _load_index() -> dict[str, typing.Any]:
    """インデックスを読み込む。不在・読み取り失敗・形式不正はいずれも空として扱う。"""
    try:
        payload = json.loads(_CREATION_TIME_INDEX_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {key: value for key, value in payload.items() if isinstance(value, dict)}


def _load_legacy_entries() -> dict[tuple[str, str], tuple[float, pathlib.Path]]:
    """旧形式のキャッシュを`(host, 相対パス)`から作成日時と実ファイルへの対応として読み込む。

    旧形式は`root`を保持しないため、現在の走査対象と一致するものだけを移行対象にできる。
    """
    entries: dict[tuple[str, str], tuple[float, pathlib.Path]] = {}
    try:
        candidates = [path for path in _CREATION_TIME_INDEX_PATH.parent.iterdir() if _LEGACY_CACHE_NAME_RE.match(path.name)]
    except OSError:
        return entries
    for candidate in candidates:
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        host = payload.get("host")
        rel = payload.get("path")
        ctime = _entry_ctime(payload)
        if isinstance(host, str) and isinstance(rel, str) and ctime is not None:
            entries[(host, rel)] = (ctime, candidate)
    return entries


def _write_index(index: dict[str, typing.Any]) -> bool:
    """インデックスを原子的に保存する。失敗した場合は`False`を返す。

    一時ファイル名はリモートヘルパーの除去規則と一致する`index.json.<ランダム文字列>.tmp`とする。
    """
    content = json.dumps(index, ensure_ascii=False, indent=2) + "\n"
    temporary: pathlib.Path | None = None
    try:
        _CREATION_TIME_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = _CREATION_TIME_INDEX_PATH.with_name(f"{_CREATION_TIME_INDEX_PATH.name}.{os.getpid()}.tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(_CREATION_TIME_INDEX_PATH)
    except OSError:
        if temporary is not None:
            with contextlib.suppress(OSError):
                temporary.unlink()
        return False
    return True


def update_creation_time_index(
    host: str,
    root: pathlib.Path,
    observed: dict[str, float],
    *,
    migrate_legacy: bool = True,
) -> dict[str, float]:
    """走査結果の観測時刻をインデックスへ反映し、確定した作成日時を相対パスごとに返す。

    初回観測時の値を作成日時として保持することで、編集で変動する値に依らず並び順を維持する。
    同一の`(host, root)`に属し今回の走査に現れなかったキーは回収し、
    別の`root`に属するキーは維持する（`root`ごとに走査対象が異なるため）。
    `migrate_legacy=True`の場合だけ、旧形式から`host`と相対パスが今回の走査と一致するものを取り込み、
    インデックスの書き込みに成功した場合に限り取り込んだファイルを削除する。
    ロックを取得できない場合はインデックスの更新を諦め、観測値をそのまま返す。
    """
    root_key = _root_key(root)
    resolved: dict[str, float] = {}
    with contextlib.ExitStack() as stack:
        if not _enter_index_lock(stack):
            return dict(observed)
        index = _load_index()
        legacy = _load_legacy_entries() if migrate_legacy else {}
        migrated: list[pathlib.Path] = []
        updated: dict[str, typing.Any] = {}
        for rel, observed_epoch in observed.items():
            key = _index_key(host, root_key, rel)
            cached = _entry_ctime(index.get(key, {}))
            if cached is None:
                legacy_entry = legacy.get((host, rel))
                if legacy_entry is not None:
                    cached, legacy_path = legacy_entry
                    migrated.append(legacy_path)
            creation = min(observed_epoch, cached) if cached is not None else observed_epoch
            resolved[rel] = creation
            updated[key] = {"host": host, "root": root_key, "path": rel, "ctime_epoch": creation}
        # インデックスの更新時は必ず同じロックを保持するため、冒頭で読み込んだ内容へ直接反映できる。
        for key, entry in list(index.items()):
            if key not in updated and entry.get("host") == host and entry.get("root") == root_key:
                del index[key]
        index.update(updated)
        if _write_index(index):
            for legacy_path in migrated:
                with contextlib.suppress(OSError):
                    legacy_path.unlink()
    return resolved


def cleanup_creation_time_temporaries() -> None:
    """作成日時インデックスの残存一時ファイルをロック下で除去する。

    対象は`index.json.<接尾辞>.tmp`と、
    旧実装が生成した`.<sha256 hexdigest>.json.<pid>.<スレッドID>.tmp`の2形式とする。
    書き込み途中のファイルを削除しないよう、除去はインデックスと同じロックの保持中に行う。
    ロックを取得できない場合は何もせずに返る。
    """
    directory = _CREATION_TIME_INDEX_PATH.parent
    if not directory.is_dir():
        return
    temporary_pattern = f"{_CREATION_TIME_INDEX_PATH.name}.*.tmp"
    with contextlib.ExitStack() as stack:
        if not _enter_index_lock(stack):
            return
        try:
            candidates = list(directory.iterdir())
        except OSError:
            return
        for candidate in candidates:
            if not candidate.match(temporary_pattern) and not _LEGACY_TEMPORARY_NAME_RE.match(candidate.name):
                continue
            with contextlib.suppress(OSError):
                candidate.unlink()
