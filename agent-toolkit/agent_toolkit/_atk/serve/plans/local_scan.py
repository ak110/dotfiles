"""ローカルのrootにある計画ファイルの走査、一覧と全文検索の対象判定、変更の監視。

一覧の各ファイルの作成日時は`ctime_index`のインデックスで初回観測時刻を保つ。
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import threading
import typing

import watchdog.events
import watchdog.observers
import watchdog.observers.api

from agent_toolkit._atk.serve import remote as _atk_serve_remote
from agent_toolkit._atk.serve.plans.ctime_index import update_creation_time_index
from agent_toolkit._atk.serve.plans.roots import (
    _BUGS_SUFFIX,
    _DETAIL_SUFFIX,
    _PYGMENTS_CSS_CLASS,
    _PYGMENTS_FORMATTER,
    _TARGET_TSV_SUFFIXES,
    _WATCHED_EVENT_TYPES,
    LEGACY_SOURCE_ID,
    BroadcastState,
    FileEntry,
    RootSpec,
    make_file_entry,
    schedule_broadcast,
)

_STATIC_DIR = pathlib.Path(__file__).with_name("static")

# リモート側で実行する短いPython bootstrap。組み立ての制約は`_atk_serve_remote`が定める。
REMOTE_BOOTSTRAP = _atk_serve_remote.remote_bootstrap("atk_serve_plans_remote_helper.py")


# --------------------------------------------------------------------------------------
# ローカル走査
# --------------------------------------------------------------------------------------


def is_target_path(path: pathlib.Path, root: pathlib.Path, source_id: str = "") -> bool:
    """`path`が対象接尾辞・`root`配下・非dotdirの全条件を満たすか判定する。

    読取・検索・変更監視の3つの処理が同一の対象集合を返すよう、この判定を1箇所へ集約する。
    `~/.claude/plans`ではメイン`<stem>.md`と付属ファイル`<stem>.bugs.md`・`<stem>.exec-review.tsv`を真とする。
    `private-notes/plans/`と設定で明示したrootでは旧付属ファイルも読取・検索・監視の対象に含める。
    リモート側`atk_serve_plans_remote_helper.py`の`_is_target_path`と同一基準を保つ
    （同ファイルはSSH越しに単独実行されるためモジュールを共有できず、意図的に重複させている）。
    `root`自身がドット配下（`~/.claude/plans`など）でも通るよう、判定は`root`からの相対パスに対して行う。
    シンボリックリンクを解決してから相対化するため、`root`外を指すリンクは対象外となる。
    """
    if path.suffix != ".md" and not path.name.endswith(_TARGET_TSV_SUFFIXES):
        return False
    if source_id == LEGACY_SOURCE_ID and path.name.endswith((_DETAIL_SUFFIX, _TARGET_TSV_SUFFIXES[0])):
        return False
    try:
        rel = path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return not any(part.startswith(".") for part in rel.parts)


def is_listed_path(path: pathlib.Path, root: pathlib.Path, source_id: str = "") -> bool:
    """`path`が計画一覧で独立項目として表示する対象かを判定する。

    メイン計画は常に一覧へ載せ、付属の詳細・計画ファイル（バグ）は除外する。レビュー指摘管理表は対応する
    メイン計画が存在する場合だけ付属ファイルとして除外し、存在しない場合は自身を一覧へ載せる。
    """
    if not is_target_path(path, root, source_id):
        return False
    review_suffix = next((suffix for suffix in _TARGET_TSV_SUFFIXES if path.name.endswith(suffix)), None)
    if review_suffix is not None:
        main = path.with_name(f"{path.name[: -len(review_suffix)]}.md")
        return not main.is_file()
    return not path.name.endswith((_DETAIL_SUFFIX, _BUGS_SUFFIX))


class PlansEventHandler(watchdog.events.FileSystemEventHandler):
    """watchdogのイベントを受信してSSE購読者へ通知するハンドラ。

    watchdogコールバックはwatchdog側のスレッドで実行されるため、
    asyncioループへ`run_coroutine_threadsafe`でブリッジする。
    """

    def __init__(
        self,
        root: pathlib.Path,
        state: BroadcastState,
        source_id: str = "",
    ) -> None:
        super().__init__()
        self.root = root
        self.state = state
        self.source_id = source_id

    @typing.override
    def on_any_event(self, event: watchdog.events.FileSystemEvent) -> None:
        """ファイルシステムイベントをフィルタリングして購読者へ通知する。"""
        if not isinstance(event, _WATCHED_EVENT_TYPES):
            return
        if event.is_directory:
            return
        # src_pathはwatchdog型定義上bytes|strだが実行時はstr。str変換でPath型エラーを回避する。
        src = pathlib.Path(str(event.src_path))
        # atomic-write保存では`FileMovedEvent(src_path="plan.md.tmp", dest_path="plan.md")`となるため、
        # src_pathとdest_pathの両方を確認する。
        if isinstance(event, watchdog.events.FileMovedEvent):
            dest = pathlib.Path(str(event.dest_path))
            if not (is_target_path(src, self.root, self.source_id) or is_target_path(dest, self.root, self.source_id)):
                return
        elif not is_target_path(src, self.root, self.source_id):
            return
        loop = self.state.loop
        if loop is None:
            # 起動直後にループ参照が未設定のイベントは取りこぼしてよい（直後のイベントで再通知される）。
            return
        asyncio.run_coroutine_threadsafe(schedule_broadcast(self.state), loop)


def _ctime_epoch(st: os.stat_result) -> float:
    """観測時点の作成日時候補をepoch秒で返す。

    `st_birthtime`（macOS・Windowsで実在し「作成時刻」を表す）を優先し、
    存在しないプラットフォームでは更新日時を用いる。
    初回観測時の値を保持する処理は`update_creation_time_index`が担う。
    編集で変動する`st_ctime`は用いない。
    """
    birthtime = getattr(st, "st_birthtime", None)
    return float(birthtime) if birthtime is not None else float(st.st_mtime)


def local_host_info(root: pathlib.Path) -> dict[str, str]:
    """ローカルホストの`host_info`エントリ（`root`・`home`・`os_type`・`os_name`）を組み立てる。

    `root`・`home`はクライアント側のパス結合と表記を統一するため常に`/`区切りへ正規化する。
    `home`はクライアント側のチルダ表記変換の基準パスとして使う。
    """
    home = str(pathlib.Path.home()).replace("\\", "/")
    return {
        "root": str(root).replace("\\", "/"),
        "home": home,
        "os_type": os.name,
        "os_name": os.name,
    }


def root_info(spec: RootSpec) -> dict[str, typing.Any]:
    """複数root APIへ返す保存元情報を組み立てる。"""
    info: dict[str, typing.Any] = {
        "source_id": spec.source_id,
        "portable_root": spec.portable_path,
    }
    if spec.warning is not None:
        info["warning"] = spec.warning
    return info


def root_warning(root: pathlib.Path) -> str | None:
    """rootを利用できない理由を返す。

    rootの不在は計画をまだ保存していない通常の状態であるため警告しない。
    非ディレクトリ以外の障害は走査時に判定する。
    """
    if root.exists() and not root.is_dir():
        return "rootがディレクトリではありません"
    return None


def root_status(warning: str | None) -> dict[str, str]:
    """rootの利用状態をAPI・SSE向けの小さな辞書へ変換する。"""
    if warning is None:
        return {"status": "ok", "message": ""}
    return {"status": "warning", "message": warning}


def scan_files(
    root: pathlib.Path,
    host: str,
    source_id: str = "",
    *,
    migrate_legacy_ctime: bool | None = None,
    stop: threading.Event | None = None,
) -> tuple[list[FileEntry], str | None]:
    """`root`を走査し、一覧とroot単位の警告を返す。

    rootの非ディレクトリ・権限不足は呼び出し元が他rootの処理を継続できるよう、
    例外ではなく警告本文として返す。rootの不在は通常の状態として空の一覧だけを返す。
    rootは自動作成しない。
    `stop`が設定されると走査の途中で`ServeStopping`を送出し、途中までの観測でインデックスを更新しない。
    """
    warning = root_warning(root)
    if warning is not None:
        return [], warning
    # 不在のrootに対する`rglob`は空を返して成功するため、走査へ進むと空の観測結果で
    # インデックスを更新し、同じ`(host, root)`に記録済みの作成日時を回収してしまう。
    if not root.is_dir():
        return [], None

    scanned: list[dict[str, typing.Any]] = []
    observed: dict[str, float] = {}
    warning = None
    try:
        for path in root.rglob("*"):
            _atk_serve_remote.raise_if_stopping(stop)
            try:
                if not path.is_file() or not is_listed_path(path, root, source_id):
                    continue
                st = path.stat()
            except OSError as error:
                warning = f"rootの走査に失敗しました: {error}"
                continue
            rel = path.relative_to(root).as_posix()
            observed[rel] = _ctime_epoch(st)
            scanned.append({"path": rel, "name": path.name, "mtime_epoch": st.st_mtime})
    except OSError as error:
        warning = f"rootの走査に失敗しました: {error}"

    # 走査後に一度だけインデックスを更新し、同じ`(host, root)`の不在エントリを回収する。
    resolved = update_creation_time_index(
        host,
        root,
        observed,
        migrate_legacy=(source_id in ("", LEGACY_SOURCE_ID) if migrate_legacy_ctime is None else migrate_legacy_ctime),
    )
    collected = [
        make_file_entry(host, {**item, "ctime_epoch": resolved[item["path"]], "source_id": source_id}) for item in scanned
    ]
    collected.sort(key=lambda entry: (entry.ctime_epoch, entry.path), reverse=True)
    return collected, warning


def list_files(root: pathlib.Path, host: str, source_id: str = "") -> list[FileEntry]:
    """`root`から一覧対象の計画ファイルを再帰的に探し、作成日時の降順で返す。"""
    entries, _ = scan_files(root, host, source_id)
    return entries


def search_files(root: pathlib.Path, query: str, source_id: str = "", *, stop: threading.Event | None = None) -> set[str]:
    """本文へ検索語が部分一致する計画ファイルの相対パス集合を返す。

    `stop`が設定されるとファイル1件ごとの確認で`ServeStopping`を送出して打ち切る。
    """
    needle = query.casefold()
    if not root.is_dir():
        return set()
    matched: set[str] = set()
    try:
        for path in root.rglob("*"):
            _atk_serve_remote.raise_if_stopping(stop)
            if not path.is_file() or not is_target_path(path, root, source_id):
                continue
            if not needle:
                matched.add(path.relative_to(root).as_posix())
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if needle in text.casefold():
                matched.add(path.relative_to(root).as_posix())
    except OSError:
        # 空の検索語は全件の一致を返すため、走査の失敗では途中までの結果を返さない。
        return matched if needle else set()
    return matched


def resolve_under_root(root: pathlib.Path, rel: str, source_id: str = "") -> pathlib.Path | None:
    """`rel`が`root`配下の対象ファイルを指す場合のみ絶対パスを返す。存在しない場合はNone。"""
    # シンボリックリンクを辿ってroot外へ出ないよう、resolve後のパスが範囲内かを確認する。
    target = (root / rel).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        return None
    if not target.is_file() or not is_target_path(target, root, source_id):
        return None
    return target


def read_pygments_css() -> str:
    """Pygmentsのスタイルシートを返す。

    pygmentsの基本ルール（`.codehilite { background: ...; color: ... }`）は除外し、
    トークン別カラールール（`.codehilite .k`等）のみを返す。
    背景と、トークンごとに指定しない文字色はapp.css側の`pre code`ルールで定め、
    `<pre>`の背景上に異色矩形が出現する事象を防ぐ。
    """
    raw = _PYGMENTS_FORMATTER.get_style_defs(f".{_PYGMENTS_CSS_CLASS}")
    base_selector = f".{_PYGMENTS_CSS_CLASS}"
    kept: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith(f"{base_selector} {{") or stripped.startswith(f"{base_selector}{{"):
            continue
        kept.append(line)
    return "\n".join(kept)
