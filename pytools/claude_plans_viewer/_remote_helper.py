# /// script
# requires-python = ">=3.10"
# dependencies = ["platformdirs>=4.0", "watchdog>=6.0.0"]
# ///
"""claude_plans_viewerのリモートホスト側ヘルパー。

操作種別はargvで受け取る（`list`・`read`・`search`・`watch`・`serve`）。
各サブコマンドの入出力プロトコルは対応する関数のdocstringを参照。
"""

import base64
import contextlib
import hashlib
import json
import os
import pathlib
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import typing

import platformdirs

# pylint: disable=duplicate-code
# リモート側で単独実行するためローカル側の永続キャッシュ実装・ファイルロック実装を共有できない。
# 作成日時インデックスのキーと値の形式は`_local.py`と一致させる必要がある
# （同一ホスト上で両者が同じキャッシュディレクトリを共有するため）。

if os.name == "nt":
    import msvcrt  # type: ignore[import-not-found]  # pylint: disable=import-error

    def _lock_handle(handle: typing.IO[typing.Any]) -> None:
        """Windows: 先頭1バイトのバイト範囲ロックを取得する（`LK_LOCK`の10秒制限を跨いで再試行する）。"""
        handle.seek(0)
        while True:
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)  # type: ignore[attr-defined]
                return
            except OSError:
                continue

    def _unlock_handle(handle: typing.IO[typing.Any]) -> None:
        """Windows: バイト範囲ロックを解放する。"""
        handle.seek(0)
        with contextlib.suppress(OSError):
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]

else:
    import fcntl

    def _lock_handle(handle: typing.IO[typing.Any]) -> None:
        """POSIX: ファイル全体への排他ロックを取得する。"""
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)

    def _unlock_handle(handle: typing.IO[typing.Any]) -> None:
        """POSIX: ファイル全体への排他ロックを解放する。"""
        with contextlib.suppress(OSError):
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


NEW_SOURCE_ID = "private-notes-plans"
LEGACY_SOURCE_ID = "claude-plans"
NEW_PORTABLE_ROOT = "$(atk config get private_notes)/plans"
LEGACY_PORTABLE_ROOT = "~/.claude/plans"
_DEFAULT_ROOT = pathlib.Path.home() / ".claude" / "plans"
_UNRESOLVED_PRIVATE_NOTES_ROOT = pathlib.Path.home() / ".claude" / ".plans-viewer-private-notes-unresolved"
# 旧単一rootテスト・呼び出しとの互換用。通常のmain経路ではROOTSを解決済みroot群へ設定する。
ROOT = _DEFAULT_ROOT
ROOTS: list["_RootSpec"] | None = None


class _RootSpec(typing.NamedTuple):
    """リモート側で使うroot定義。"""

    source_id: str
    path: pathlib.Path
    portable_path: str
    warning: str | None = None
    migrate_legacy_ctime: bool | None = None


def _canonical(path: pathlib.Path) -> pathlib.Path:
    """rootの比較・ファイル参照に使う正規化済みパスを返す。"""
    return path.expanduser().resolve()


def _resolve_private_notes_result() -> tuple[pathlib.Path | None, str | None]:
    """対象ホスト上の`atk config get private_notes`を実行し、失敗理由も返す。"""
    try:
        completed = subprocess.run(
            ["atk", "config", "get", "private_notes"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as error:
        warning = f"private_notesの取得に失敗しました: {error}"
        sys.stderr.write(f"warn: {warning}\n")
        return None, warning
    value = completed.stdout.strip()
    if not value or "\n" in value:
        warning = "private_notesの取得結果が不正です"
        sys.stderr.write(f"warn: {warning}\n")
        return None, warning
    return _canonical(pathlib.Path(value)), None


def _resolve_private_notes_path() -> pathlib.Path | None:
    """対象ホスト上の`atk config get private_notes`を実行する。"""
    path, _ = _resolve_private_notes_result()
    return path


def _dedupe_roots(specs: typing.Iterable[_RootSpec]) -> list[_RootSpec]:
    """同一canonical rootまたは同一実体の重複だけを除く。"""
    result: list[_RootSpec] = []
    for spec in specs:
        migrate_legacy = _migrates_legacy_ctime(spec)
        candidate = _RootSpec(
            spec.source_id,
            _canonical(spec.path),
            spec.portable_path,
            spec.warning,
            migrate_legacy,
        )
        duplicate_index: int | None = None
        for index, existing in enumerate(result):
            if candidate.path == existing.path:
                duplicate_index = index
                break
            try:
                if candidate.path.exists() and existing.path.exists() and candidate.path.samefile(existing.path):
                    duplicate_index = index
                    break
            except OSError:
                continue
        if duplicate_index is None:
            result.append(candidate)
        elif candidate.migrate_legacy_ctime and not result[duplicate_index].migrate_legacy_ctime:
            result[duplicate_index] = result[duplicate_index]._replace(migrate_legacy_ctime=True)
    return result


def _migrates_legacy_ctime(spec: _RootSpec) -> bool:
    """旧形式ctime cacheの移行資格を返す。"""
    if spec.migrate_legacy_ctime is not None:
        return spec.migrate_legacy_ctime
    return spec.source_id in ("", LEGACY_SOURCE_ID)


def _default_root_specs() -> list[_RootSpec]:
    """既定の新旧rootを対象ホスト上で解決する。"""
    specs: list[_RootSpec] = []
    private_notes, warning = _resolve_private_notes_result()
    if private_notes is not None:
        specs.append(_RootSpec(NEW_SOURCE_ID, private_notes / "plans", NEW_PORTABLE_ROOT, None, False))
    else:
        specs.append(
            _RootSpec(
                NEW_SOURCE_ID,
                _UNRESOLVED_PRIVATE_NOTES_ROOT,
                NEW_PORTABLE_ROOT,
                warning or "private_notesを解決できません",
                False,
            )
        )
    specs.append(_RootSpec(LEGACY_SOURCE_ID, _DEFAULT_ROOT, LEGACY_PORTABLE_ROOT, None, True))
    return _dedupe_roots(specs)


def _root_specs() -> list[_RootSpec]:
    """現在のroot定義を返す。テストでROOTだけ差し替えた場合は旧単一rootへ戻す。"""
    if ROOT != _DEFAULT_ROOT:
        return [_RootSpec("", _canonical(ROOT), str(ROOT).replace("\\", "/"))]
    if ROOTS is not None:
        return list(ROOTS)
    return _default_root_specs()


# 生存確認pingの送信間隔（秒）。短すぎるとトラフィックが増え、長すぎると切断検知が遅れる。
_PING_INTERVAL_SEC = 30.0

# stdoutへの書き込みは観測スレッドとRPC応答スレッドの双方から発生し得る。
# print内のwrite/flushが分割されると行JSONが破損するため、emit側で排他する。
_STDOUT_LOCK = threading.Lock()
# 作成日時の永続インデックス。`_local.py`の`_CREATION_TIME_INDEX_PATH`と同一のパス・形式とする。
_CREATION_TIME_INDEX_PATH = (
    pathlib.Path(platformdirs.user_cache_dir("claude-plans-viewer", appauthor=False)) / "creation-times" / "index.json"
)
# 旧形式（1エントリ1ファイル）のキャッシュ名。sha256 hexdigestと`.json`から成る。
_LEGACY_CACHE_NAME_RE = re.compile(r"^[0-9a-f]{64}\.json$")
# 旧実装が生成した一時ファイル名。`.<sha256 hexdigest>.json.<pid>.<スレッドID>.tmp`。
_LEGACY_TEMPORARY_NAME_RE = re.compile(r"^\.[0-9a-f]{64}\.json\.\d+\.\d+\.tmp$")
_TARGET_TSV_SUFFIXES = (".plan-review.tsv", ".exec-review.tsv")
_LISTED_EXCLUDED_SUFFIXES = (".detail.md", ".bugs.md", *_TARGET_TSV_SUFFIXES)


def _is_target_path(path: pathlib.Path, root: pathlib.Path | None = None) -> bool:
    """`path`が指定root配下の対象計画ファイルか判定する。

    `_local.py`の`is_target_path`と同一基準を保つ（両者はSSH越し実行のため実装を共有できない）。
    メイン`<stem>.md`と付属ファイル`<stem>.detail.md`・`<stem>.bugs.md`・レビュー指摘管理表を真とする
    （付属ファイルは一覧だけから除外し、読取・検索・監視の対象には含める。`_is_listed_path`が一覧専用の判定を持つ）。
    `ROOT`自身がドット配下でも通るよう、判定は`ROOT`からの相対パスに対して行う。
    シンボリックリンクを解決してから相対化するため、`ROOT`外を指すリンクは対象外となる
    （`_resolve_target`が単一ファイル取得へ課す範囲と一致させる）。
    """
    if path.suffix != ".md" and not path.name.endswith(_TARGET_TSV_SUFFIXES):
        return False
    roots = [_canonical(root)] if root is not None else [spec.path for spec in _root_specs()]
    for candidate in roots:
        try:
            rel = path.resolve().relative_to(candidate)
        except ValueError:
            continue
        if not any(p.startswith(".") for p in rel.parts):
            return True
    return False


def _is_listed_path(path: pathlib.Path, root: pathlib.Path | None = None) -> bool:
    """`path`が計画一覧の対象（`_is_target_path`が真、かつ付属計画ではない）かを判定する。

    一覧経路（`_scan_entries`）だけに使う。読取・検索・変更監視は`_is_target_path`を使い、付属計画も対象へ含める。
    """
    return _is_target_path(path, root) and not path.name.endswith(_LISTED_EXCLUDED_SUFFIXES)


@contextlib.contextmanager
def _exclusive_file_lock(path: pathlib.Path) -> typing.Iterator[None]:
    """`path`をロックファイルとしてプロセス間の排他ロックを保持する。

    `pytools/_internal/file_lock.py`の`exclusive_file_lock`と同じ排他範囲を持つ。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        _lock_handle(handle)
        try:
            yield
        finally:
            _unlock_handle(handle)


def _index_lock_path() -> pathlib.Path:
    """作成日時インデックスの排他ロックファイルのパス。"""
    return _CREATION_TIME_INDEX_PATH.with_name(_CREATION_TIME_INDEX_PATH.name + ".lock")


def _enter_index_lock(stack: contextlib.ExitStack) -> bool:
    """作成日時インデックスの排他ロックを`stack`へ登録する。取得できない場合は`False`を返す。

    キャッシュディレクトリを作成・書き込みできない環境ではロックファイルを開けず`OSError`となる。
    作成日時キャッシュの失敗で一覧機能を止めないため、当該例外は呼び出し元へ伝播させない。
    """
    try:
        stack.enter_context(_exclusive_file_lock(_index_lock_path()))
    except OSError:
        return False
    return True


def _index_key(host: str, root_key: str, rel: str) -> str:
    r"""インデックスのキー。`(host, root, rel)`を`\0`で連結した文字列のsha256 hexdigest。"""
    return hashlib.sha256(f"{host}\0{root_key}\0{rel}".encode()).hexdigest()


def _root_key(root: pathlib.Path | None = None) -> str:
    """インデックスのキーと値へ用いる`ROOT`の正規化表記。"""
    target = root if root is not None else _root_specs()[0].path
    return str(_canonical(target)).replace("\\", "/")


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
    """旧形式のキャッシュを`(host, 相対パス)`から作成日時と実ファイルへの対応として読み込む。"""
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


def _atomic_write_index(index: dict[str, typing.Any]) -> bool:
    """インデックスを同一ディレクトリの一時ファイル経由で原子的に保存する。

    一時ファイル名は`_local.py`が用いる`atomic_write_json`と同じ
    `index.json.<ランダム文字列>.tmp`の形とし、除去規則を両実装で一致させる。
    """
    content = json.dumps(index, ensure_ascii=False, indent=2) + "\n"
    temporary: pathlib.Path | None = None
    try:
        _CREATION_TIME_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=_CREATION_TIME_INDEX_PATH.parent,
            delete=False,
            prefix=f"{_CREATION_TIME_INDEX_PATH.name}.",
            suffix=".tmp",
        ) as tmp:
            tmp.write(content)
            temporary = pathlib.Path(tmp.name)
        temporary.replace(_CREATION_TIME_INDEX_PATH)
    except OSError:
        if temporary is not None:
            with contextlib.suppress(OSError):
                temporary.unlink()
        return False
    return True


def _update_creation_time_index(
    host: str,
    observed: dict[str, float],
    *,
    prune: bool,
    root: pathlib.Path | None = None,
    migrate_legacy: bool = True,
) -> dict[str, float]:
    """走査結果の観測時刻をインデックスへ反映し、確定した作成日時を相対パスごとに返す。

    `prune=True`のとき、同一の`(host, ROOT)`に属し今回の走査に現れなかったキーを回収する。
    単一ファイルの更新通知は走査結果ではないため`prune=False`で呼ぶ。
    `migrate_legacy=True`の場合だけ、旧形式から`host`と相対パスが今回の対象と一致するものを取り込み、
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
        # インデックスを更新する経路は全て同じロックを保持するため、冒頭で読み込んだ内容へ直接反映する。
        if prune:
            for key, entry in list(index.items()):
                if key not in updated and entry.get("host") == host and entry.get("root") == root_key:
                    del index[key]
        index.update(updated)
        if _atomic_write_index(index):
            for legacy_path in migrated:
                with contextlib.suppress(OSError):
                    legacy_path.unlink()
    return resolved


def _cleanup_creation_time_temporaries() -> None:
    """作成日時インデックスの残存一時ファイルをロック下で除去する。

    対象は`index.json.<ランダム文字列>.tmp`と、
    旧実装が生成した`.<sha256 hexdigest>.json.<pid>.<スレッドID>.tmp`の2形式とする。
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


def _ctime_epoch(st: os.stat_result) -> float:
    """観測時点の作成日時候補をepoch秒で返す。`st_birthtime`（存在時）を優先する。

    詳細は`pytools/claude_plans_viewer/_local.py`の同名関数のdocstringを参照
    （リモートヘルパーは独立実行スクリプトのためロジックを重複させている）。
    """
    birthtime = getattr(st, "st_birthtime", None)
    return float(birthtime) if birthtime is not None else float(st.st_mtime)


def _host_info() -> dict[str, str]:
    """このリモートホストの`host_info`エントリ（`root`・`home`・`os_type`・`os_name`）を組み立てる。

    `root`・`home`は`/`区切りへ正規化する（`_local.py`の`local_host_info`と同一の正規化。
    クライアント側`copySelectedPath`が`root`・`home`を`/`区切り前提で解析するため）。
    """
    home = str(pathlib.Path.home()).replace("\\", "/")
    return {
        "root": str(ROOT.resolve()).replace("\\", "/"),
        "home": home,
        "os_type": os.name,
        "os_name": os.name,
    }


def _root_info(spec: _RootSpec) -> dict[str, typing.Any]:
    """Source IDごとのroot情報を組み立てる。"""
    info: dict[str, typing.Any] = {
        "source_id": spec.source_id,
        "portable_root": spec.portable_path,
    }
    if spec.warning is not None:
        info["warning"] = spec.warning
    return info


def _root_status(warning: str | None) -> dict[str, str]:
    """rootの利用状態をsnapshot用辞書へ変換する。"""
    if warning is None:
        return {"status": "ok", "message": ""}
    return {"status": "warning", "message": warning}


def _scan_snapshot() -> tuple[list[dict[str, typing.Any]], dict[str, dict[str, typing.Any]], dict[str, dict[str, str]]]:
    """全rootを独立して走査し、一覧・root情報・root状態を返す。"""
    entries: list[dict[str, typing.Any]] = []
    root_info: dict[str, dict[str, typing.Any]] = {}
    root_status: dict[str, dict[str, str]] = {}
    host = socket.gethostname()
    for spec in _root_specs():
        root_info[spec.source_id] = _root_info(spec)
        warning: str | None = spec.warning
        spec_entries: list[dict[str, typing.Any]] = []
        if warning is None and not spec.path.exists():
            warning = "rootが存在しません"
        elif warning is None and not spec.path.is_dir():
            warning = "rootがディレクトリではありません"
        elif warning is None:
            observed: dict[str, float] = {}
            try:
                paths = spec.path.rglob("*")
                for path in paths:
                    try:
                        if not path.is_file() or not _is_listed_path(path, spec.path):
                            continue
                        st = path.stat()
                    except OSError as error:
                        warning = f"rootの走査に失敗しました: {error}"
                        continue
                    rel = path.relative_to(spec.path).as_posix()
                    observed[rel] = _ctime_epoch(st)
                    entry: dict[str, typing.Any] = {
                        "path": rel,
                        "name": path.name,
                        "mtime_epoch": st.st_mtime,
                    }
                    if spec.source_id:
                        entry["source_id"] = spec.source_id
                    spec_entries.append(entry)
            except OSError as error:
                warning = f"rootの走査に失敗しました: {error}"
            resolved = _update_creation_time_index(
                host,
                observed,
                prune=True,
                root=spec.path,
                migrate_legacy=_migrates_legacy_ctime(spec),
            )
            for entry in spec_entries:
                entry["ctime_epoch"] = resolved[entry["path"]]
            entries.extend(spec_entries)
        root_status[spec.source_id] = _root_status(warning)
    return entries, root_info, root_status


def _scan_entries() -> list[dict[str, typing.Any]]:
    """一覧用のエントリを走査する。付属ファイルは`_is_listed_path`で除外する。"""
    entries, _, _ = _scan_snapshot()
    return entries


def _resolve_target(source_or_rel_b64: str, rel_b64: str | None = None) -> pathlib.Path:
    """Source IDと相対パスから安全な実体を解決する。旧1引数形式も受理する。"""
    if rel_b64 is None:
        source_id = ""
        rel_b64 = source_or_rel_b64
    else:
        source_id = base64.b64decode(source_or_rel_b64).decode("utf-8")
    rel = base64.b64decode(rel_b64).decode("utf-8")
    rel_path = pathlib.PurePosixPath(rel)
    if rel_path.is_absolute() or ".." in rel_path.parts:
        raise ValueError("invalid relative path")
    specs = _root_specs()
    candidates = [spec for spec in specs if spec.source_id == source_id] if source_id else specs
    matches: list[pathlib.Path] = []
    for spec in candidates:
        target = (spec.path / rel).resolve()
        try:
            target.relative_to(spec.path.resolve())
        except ValueError:
            continue
        if _is_target_path(target, spec.path) and target.is_file():
            matches.append(target)
    if len(matches) != 1:
        if len(matches) > 1:
            raise ValueError("source is required")
        raise FileNotFoundError(rel)
    return matches[0]


def _read_payload(source_or_rel_b64: str, rel_b64: str | None = None) -> dict[str, typing.Any]:
    """指定相対パスのファイル本文と`mtime_epoch`をRPC応答用辞書として返す。

    `read_bytes`と`stat`を続けて呼ぶことで、本文と取得時点のmtimeをペアで取得する。
    呼び出し側はこの`mtime_epoch`をMarkdownキャッシュキーへ使い、watch通知の遅延に
    左右されず正確性を担保できる。
    """
    target = _resolve_target(source_or_rel_b64, rel_b64)
    data = target.read_bytes()
    st = target.stat()
    return {
        "data": base64.b64encode(data).decode("ascii"),
        "mtime_epoch": st.st_mtime,
    }


def _search_payload(query_b64: str, source_id: str | None = None) -> dict[str, typing.Any]:
    """本文へ検索語が部分一致する計画ファイルの相対パスを返す。"""
    query = base64.b64decode(query_b64).decode("utf-8").casefold()
    matched: list[str] = []
    matches: list[dict[str, str]] = []
    specs = [spec for spec in _root_specs() if source_id in (None, "") or spec.source_id == source_id]
    for spec in specs:
        if not spec.path.is_dir():
            continue
        try:
            paths = spec.path.rglob("*")
            for path in paths:
                if not path.is_file() or not _is_target_path(path, spec.path):
                    continue
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                if query in text.casefold():
                    rel = path.relative_to(spec.path).as_posix()
                    matched.append(rel)
                    matches.append({"source_id": spec.source_id, "path": rel})
        except OSError:
            continue
    payload: dict[str, typing.Any] = {"paths": sorted(matched)}
    if len(specs) > 1 or any(item["source_id"] for item in matches):
        payload["matches"] = sorted(matches, key=lambda item: (item["source_id"], item["path"]))
    return payload


def _list_files() -> None:
    """`list`サブコマンド: 全root配下の`.md`一覧をJSON文字列でstdoutへ出力する。"""
    json.dump(_scan_entries(), sys.stdout, ensure_ascii=False)


def _read_file(source_or_rel_b64: str, rel_b64: str | None = None) -> None:
    """`read`サブコマンド: 指定相対パスのファイル本文と`mtime_epoch`をJSON文字列でstdoutへ出力する。

    応答形式（fallback経路用、単発SSH呼び出し）:
        {"data":"<base64本文>", "mtime_epoch":<float>}
    """
    json.dump(_read_payload(source_or_rel_b64, rel_b64), sys.stdout, ensure_ascii=False)


def _emit(payload: dict[str, typing.Any]) -> None:
    # 1行JSONとして出力し、SSH切断時のSIGPIPEを即時に拾えるよう毎回フラッシュする。
    line = json.dumps(payload, ensure_ascii=False)
    with _STDOUT_LOCK:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


def _start_observer(stop_event: threading.Event) -> typing.Any:
    """Watchdog Observerとping送信スレッドを起動し、Observerを返す。

    `serve`/`watch`の両サブコマンドで共通利用する。rootは自動作成せず、
    不在・権限不足・監視失敗をroot単位のsnapshot状態へ記録する。
    """
    # watchdogはPEP 723の`dependencies`またはbootstrap側の`--with`指定で都度解決される。
    # `list`/`read`では不要のため遅延importでstartup時間を抑える。
    import watchdog.events  # pylint: disable=import-outside-toplevel
    import watchdog.observers  # pylint: disable=import-outside-toplevel

    # 読み取り由来の`FileOpenedEvent`/`FileClosedNoWriteEvent`は除外し、
    # `FileMovedEvent`はatomic-write rename対応のためdest側も判定対象に含める。
    watched_types = (
        watchdog.events.FileCreatedEvent,
        watchdog.events.FileModifiedEvent,
        watchdog.events.FileDeletedEvent,
        watchdog.events.FileMovedEvent,
        watchdog.events.FileClosedEvent,
    )

    specs = _root_specs()
    root_status: dict[str, dict[str, str]] = {}

    class Handler(watchdog.events.FileSystemEventHandler):
        """一つのroot配下の変更を行区切りJSONとして通知するイベントハンドラ。"""

        def __init__(self, spec: _RootSpec) -> None:
            super().__init__()
            self.spec = spec

        def on_any_event(self, event: typing.Any) -> None:
            if not isinstance(event, watched_types):
                return
            if event.is_directory:
                return
            src = pathlib.Path(str(event.src_path))
            if isinstance(event, watchdog.events.FileMovedEvent):
                dest = pathlib.Path(str(event.dest_path))
                src_ok = _is_target_path(src, self.spec.path)
                dest_ok = _is_target_path(dest, self.spec.path)
                if not (src_ok or dest_ok):
                    return
                # rename経路でsrcのみ`.md`の場合は元パス側を削除扱い、
                # destが`.md`なら新パス側をupsertする。
                if src_ok and not dest_ok:
                    payload: dict[str, typing.Any] = {
                        "type": "deleted",
                        "path": src.relative_to(self.spec.path).as_posix(),
                    }
                    if self.spec.source_id:
                        payload["source_id"] = self.spec.source_id
                    _emit(payload)
                    return
                target = dest if dest_ok else src
                self._emit_upsert(target)
                return
            if not _is_target_path(src, self.spec.path):
                return
            if isinstance(event, watchdog.events.FileDeletedEvent):
                payload = {"type": "deleted", "path": src.relative_to(self.spec.path).as_posix()}
                if self.spec.source_id:
                    payload["source_id"] = self.spec.source_id
                _emit(payload)
                return
            self._emit_upsert(src)

        def _emit_upsert(self, path: pathlib.Path) -> None:
            try:
                st = path.stat()
            except OSError as e:
                sys.stderr.write(f"warn: stat failed for {path}: {e}\n")
                return
            rel = path.relative_to(self.spec.path).as_posix()
            # 単一ファイルの更新通知は走査結果ではないため、不在キーの回収は行わない。
            resolved = _update_creation_time_index(
                socket.gethostname(),
                {rel: _ctime_epoch(st)},
                prune=False,
                root=self.spec.path,
                migrate_legacy=_migrates_legacy_ctime(self.spec),
            )
            payload = {
                "type": "upsert",
                "path": rel,
                "name": path.name,
                "mtime_epoch": st.st_mtime,
                "ctime_epoch": resolved[rel],
            }
            if self.spec.source_id:
                payload["source_id"] = self.spec.source_id
            _emit(payload)

    def ping_loop() -> None:
        while not stop_event.wait(_PING_INTERVAL_SEC):
            try:
                _emit({"type": "ping"})
            except BrokenPipeError:
                stop_event.set()
                return

    observer = watchdog.observers.Observer()
    scheduled = False
    for spec in specs:
        if not spec.path.is_dir():
            continue
        try:
            observer.schedule(Handler(spec), str(spec.path), recursive=True)
            scheduled = True
        except OSError as error:
            root_status[spec.source_id] = _root_status(f"rootの監視に失敗しました: {error}")
    if scheduled:
        try:
            observer.start()
        except OSError as error:
            for spec in specs:
                if spec.path.is_dir():
                    root_status[spec.source_id] = _root_status(f"rootの監視に失敗しました: {error}")
    # observer起動後にsnapshotを発行することで、起動以前の変更取りこぼしを排除する。
    entries, root_info, scanned_status = _scan_snapshot()
    scanned_status.update(root_status)
    _emit(
        {
            "type": "snapshot",
            "entries": entries,
            "host_info": _host_info(),
            "root_info": root_info,
            "root_status": scanned_status,
        }
    )

    ping_thread = threading.Thread(target=ping_loop, daemon=True)
    ping_thread.start()
    return observer


def _watch_files() -> int:
    """`watch`サブコマンド: `~/.claude/plans`配下をwatchdogで監視し、行区切りJSONをstdoutへ出力する。

    行プロトコル（行区切りJSON）:
        - {"type":"snapshot","entries":[{"path":..., "name":..., "mtime_epoch":..., "ctime_epoch":...}, ...],
           "host_info":{"root":..., "home":..., "os_type":..., "os_name":...}}
        - {"type":"upsert","path":..., "name":..., "mtime_epoch":..., "ctime_epoch":...}
        - {"type":"deleted","path":...}
        - {"type":"ping"}  ※30秒間隔。SSH切断時のSIGPIPE誘発で生存確認とする
    """
    stop_event = threading.Event()
    observer = _start_observer(stop_event)
    # SIGPIPEはping_loopが捕捉してstop_eventを通じて停止経路に乗せる。
    try:
        while not stop_event.is_set():
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        if observer.is_alive():
            observer.stop()
            observer.join()
    return 0


def _handle_request(req: dict[str, typing.Any]) -> dict[str, typing.Any]:
    """RPCリクエストを処理して応答辞書を返す。"""
    req_id = req.get("id")
    op = req.get("op")
    if not isinstance(req_id, int):
        return {"type": "response", "id": -1, "ok": False, "error": "invalid id"}
    try:
        if op == "read":
            source_id = str(req.get("source_id", ""))
            path_b64 = str(req.get("path", ""))
            if source_id:
                source_b64 = base64.b64encode(source_id.encode("utf-8")).decode("ascii")
                payload = _read_payload(source_b64, path_b64)
            else:
                payload = _read_payload(path_b64)
            return {"type": "response", "id": req_id, "ok": True, **payload}
        if op == "search":
            source_id = str(req.get("source_id", "")) or None
            payload = _search_payload(str(req.get("query", "")), source_id)
            return {"type": "response", "id": req_id, "ok": True, **payload}
        return {"type": "response", "id": req_id, "ok": False, "error": f"unknown op: {op}"}
    except Exception as e:  # noqa: BLE001  pylint: disable=broad-exception-caught
        return {"type": "response", "id": req_id, "ok": False, "error": f"{type(e).__name__}: {e}"}


def _serve() -> int:
    """`serve`サブコマンド: watchの行ストリームに加え、stdinのRPCリクエストへ応答する常駐モード。

    watch行プロトコルは`_watch_files`と共通。
    RPCプロトコル（行区切りJSON）:
        リクエスト（stdin）: {"id":<int>, "op":"read", "path":"<base64>"}
                            または{"id":<int>, "op":"search", "query":"<base64>"}
        応答（stdout）:
            成功: {"type":"response", "id":<int>, "ok":true, "data":"<base64本文>", "mtime_epoch":<float>}
            失敗: {"type":"response", "id":<int>, "ok":false, "error":"<msg>"}
    """
    stop_event = threading.Event()
    observer = _start_observer(stop_event)

    def reader_loop() -> None:
        # stdinはサーバー側からの行JSONリクエストを受け取る。
        # EOFまたは入力エラーで終了し、stop_eventを通じてメインループへ伝播する。
        for raw in sys.stdin:
            line = raw.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except json.JSONDecodeError as e:
                _emit({"type": "response", "id": -1, "ok": False, "error": f"json: {e}"})
                continue
            try:
                _emit(_handle_request(req))
            except BrokenPipeError:
                stop_event.set()
                return
        stop_event.set()

    reader_thread = threading.Thread(target=reader_loop, daemon=True)
    reader_thread.start()

    try:
        while not stop_event.is_set():
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        if observer.is_alive():
            observer.stop()
            observer.join()
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        sys.stderr.write("missing operation\n")
        return 2
    # 前回の異常終了で残った作成日時インデックスの一時ファイルを操作分岐の前に除去する。
    _cleanup_creation_time_temporaries()
    globals()["ROOTS"] = _root_specs()
    op = sys.argv[1]
    if op == "list":
        _list_files()
        return 0
    if op == "read":
        if len(sys.argv) < 3:
            sys.stderr.write("missing path\n")
            return 2
        if len(sys.argv) >= 4:
            _read_file(sys.argv[2], sys.argv[3])
        else:
            _read_file(sys.argv[2])
        return 0
    if op == "search":
        if len(sys.argv) < 3:
            sys.stderr.write("missing query\n")
            return 2
        if len(sys.argv) >= 4:
            source_id = base64.b64decode(sys.argv[2]).decode("utf-8")
            query_b64 = sys.argv[3]
        else:
            source_id = None
            query_b64 = sys.argv[2]
        json.dump(_search_payload(query_b64, source_id), sys.stdout, ensure_ascii=False)
        return 0
    if op == "watch":
        return _watch_files()
    if op == "serve":
        return _serve()
    sys.stderr.write(f"unknown operation: {op}\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
