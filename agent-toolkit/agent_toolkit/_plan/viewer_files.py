"""`atk serve`の計画ファイル画面が読む計画rootの定義と、root配下の対象判定、走査、全文検索。

ローカルの計画を読む`_atk/serve/plans/`と、SSH先で動くリモートヘルパー（`atk_serve_plans_remote_helper.py`）が
本モジュールを共有し、同じ判定で一覧・本文・検索・変更通知の対象を決める。
リモートヘルパーは`uv run --no-project --with watchdog --with platformdirs`で起動するため、本モジュールと
importする全てのモジュールは標準ライブラリ、`platformdirs`、`watchdog`と、それらだけに依存する`agent_toolkit`の
モジュールに限る。Markdownの描画など重い依存を持つ処理は`_atk/serve/plans/`に置く。
"""

from __future__ import annotations

import dataclasses
import logging
import os
import pathlib
import typing
from collections.abc import Callable, Iterable

from agent_toolkit._common import private_notes as _private_notes
from agent_toolkit._plan import bundle_kinds as _bundle_kinds
from agent_toolkit._plan import creation_times as _creation_times
from agent_toolkit._plan import locations as _locations

logger = logging.getLogger(__name__)

NEW_SOURCE_ID = "private-notes-plans"
"""保存済み計画root（`private-notes/plans/`）の保存元ID。"""
LEGACY_SOURCE_ID = "claude-plans"
"""作業中の計画root（`~/.claude/plans`）の保存元ID。"""
NEW_PORTABLE_ROOT = "$(atk config get private_notes)/plans"
LEGACY_PORTABLE_ROOT = "~/.claude/plans"


@dataclasses.dataclass(frozen=True, slots=True)
class RootSpec:
    """一つの計画rootと、画面へ返す可搬表記をまとめた定義。"""

    source_id: str
    path: pathlib.Path
    portable_path: str
    # root解決前に判明した障害（例: private_notes解決失敗）を保持する。
    warning: str | None = None
    # Noneはsource_idによる従来判定を使う。重複排除後は旧rootの資格を論理和で保持する。
    migrate_legacy_ctime: bool | None = None


def _canonical(path: pathlib.Path) -> pathlib.Path:
    """rootの比較・ファイル参照に使う正規化済みパスを返す。"""
    return path.expanduser().resolve()


def migrates_legacy_ctime(source_id: str, migrate_legacy_ctime: bool | None) -> bool:
    """旧形式の作成日時キャッシュを取り込む資格を返す。明示が無ければ旧root（と単一の明示root）だけが持つ。"""
    if migrate_legacy_ctime is not None:
        return migrate_legacy_ctime
    return source_id in ("", LEGACY_SOURCE_ID)


def normalize_root_specs(specs: Iterable[RootSpec]) -> tuple[RootSpec, ...]:
    """rootを正規化し、同一canonical pathまたは同一実体の重複だけを除く。"""
    normalized: list[RootSpec] = []
    for spec in specs:
        path = _canonical(spec.path)
        migrate_legacy = migrates_legacy_ctime(spec.source_id, spec.migrate_legacy_ctime)
        candidate = dataclasses.replace(spec, path=path, migrate_legacy_ctime=migrate_legacy)
        duplicate_index: int | None = None
        for index, existing in enumerate(normalized):
            if path == existing.path:
                duplicate_index = index
                break
            try:
                if path.exists() and existing.path.exists() and path.samefile(existing.path):
                    duplicate_index = index
                    break
            except OSError:
                # 対象が同じ実体かを確認できなくても、そのrootで起きた障害によって他rootの処理を停止しない。
                continue
        if duplicate_index is None:
            normalized.append(candidate)
        elif candidate.migrate_legacy_ctime and not normalized[duplicate_index].migrate_legacy_ctime:
            normalized[duplicate_index] = dataclasses.replace(normalized[duplicate_index], migrate_legacy_ctime=True)
    return tuple(normalized)


def explicit_root_spec(root: str | pathlib.Path) -> RootSpec:
    """設定で明示されたrootを単一root定義へ変換する。"""
    path = _canonical(pathlib.Path(root))
    legacy = _canonical(_locations.working_plans_root())
    portable = LEGACY_PORTABLE_ROOT if path == legacy else str(path).replace("\\", "/")
    return RootSpec(source_id="", path=path, portable_path=portable)


def _private_notes_result() -> tuple[pathlib.Path | None, str | None]:
    """private-notesリポジトリのrootと、解決できない場合の警告を返す。

    `atk config get private_notes`と同じ解決を同一プロセス内で呼ぶ。外部コマンドの起動を経ないため、
    常駐サービスやSSHの非対話シェルのPATHに依存しない。
    """
    try:
        value = _private_notes.default_private_notes()
    except Exception as error:  # pylint: disable=broad-exception-caught
        warning = f"private_notesの取得に失敗しました: {error}"
        logger.warning("%s。旧rootを継続します", warning)
        return None, warning
    return _canonical(pathlib.Path(value)), None


def default_root_specs() -> tuple[RootSpec, ...]:
    """設定で明示されない場合に使う新旧rootを解決し、重複rootを除いた定義を返す。"""
    specs: list[RootSpec] = []
    private_notes, warning = _private_notes_result()
    if private_notes is not None:
        specs.append(
            RootSpec(
                source_id=NEW_SOURCE_ID,
                path=private_notes / _locations.NEW_PLANS_DIRECTORY,
                portable_path=NEW_PORTABLE_ROOT,
                migrate_legacy_ctime=False,
            )
        )
    else:
        specs.append(
            RootSpec(
                source_id=NEW_SOURCE_ID,
                path=_locations.unresolved_private_notes_plans_root(),
                portable_path=NEW_PORTABLE_ROOT,
                warning=warning or "private_notesを解決できません",
                migrate_legacy_ctime=False,
            )
        )
    specs.append(
        RootSpec(
            source_id=LEGACY_SOURCE_ID,
            path=_locations.working_plans_root(),
            portable_path=LEGACY_PORTABLE_ROOT,
            migrate_legacy_ctime=True,
        )
    )
    return normalize_root_specs(specs)


def is_target_path(path: pathlib.Path, root: pathlib.Path, source_id: str = "") -> bool:
    """`path`が表示する種別・`root`配下・非dotdirの全条件を満たすか判定する。

    読取・検索・変更監視の3つの処理が同一の対象集合を返すよう、この判定を1箇所へ集約する。
    `~/.claude/plans`では現行形式の種別だけを真とし、`private-notes/plans/`と設定で明示したrootでは
    旧形式の付属ファイルも対象に含める。
    `root`自身がドット配下（`~/.claude/plans`など）でも通るよう、判定は`root`からの相対パスに対して行う。
    シンボリックリンクを解決してから相対化するため、`root`外を指すリンクは対象外となる。
    """
    if not _bundle_kinds.is_viewable_name(path.name, current_only=source_id == LEGACY_SOURCE_ID):
        return False
    try:
        rel = path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return not any(part.startswith(".") for part in rel.parts)


def is_listed_path(path: pathlib.Path, root: pathlib.Path, source_id: str = "") -> bool:
    """`path`が計画一覧で独立項目として表示する対象かを判定する。

    一覧に載せる種別は`bundle_kinds.is_listed_name`が定め、レビュー指摘管理表だけは同じstemの
    メイン計画の実在を確かめて判定する。
    """
    if not is_target_path(path, root, source_id):
        return False
    main_exists = False
    if _bundle_kinds.is_review_table_name(path.name):
        main_name = _bundle_kinds.main_name_of(path.name)
        main_exists = main_name is not None and path.with_name(main_name).is_file()
    return _bundle_kinds.is_listed_name(path.name, main_exists=main_exists)


def host_info(root: pathlib.Path) -> dict[str, str]:
    """ホストの`host_info`エントリ（`root`・`home`・`os_type`・`os_name`）を組み立てる。

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
    """複数rootの応答へ返す保存元情報を組み立てる。"""
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
    """rootの利用状態を応答向けの小さな辞書へ変換する。"""
    if warning is None:
        return {"status": "ok", "message": ""}
    return {"status": "warning", "message": warning}


def scan_root(
    root: pathlib.Path,
    host: str,
    source_id: str = "",
    *,
    migrate_legacy_ctime: bool | None = None,
    check_stop: Callable[[], None] | None = None,
) -> tuple[list[dict[str, typing.Any]], str | None]:
    """`root`を走査し、一覧の項目とroot単位の警告を返す。

    項目は`path`・`name`・`mtime_epoch`・`ctime_epoch`を持つ辞書とし、作成日時は作成日時インデックスで
    初回観測時刻を保つ。rootの非ディレクトリ・権限不足は呼び出し元が他rootの処理を継続できるよう、
    例外ではなく警告本文として返す。rootの不在は通常の状態として空の一覧だけを返す。rootは自動作成しない。
    `check_stop`はファイル1件ごとに呼び、送出した例外で走査を打ち切る。打ち切った走査はインデックスを更新しない。
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
    try:
        for path in root.rglob("*"):
            if check_stop is not None:
                check_stop()
            try:
                if not path.is_file() or not is_listed_path(path, root, source_id):
                    continue
                st = path.stat()
            except OSError as error:
                warning = f"rootの走査に失敗しました: {error}"
                continue
            rel = path.relative_to(root).as_posix()
            observed[rel] = _creation_times.observed_creation_epoch(st)
            scanned.append({"path": rel, "name": path.name, "mtime_epoch": st.st_mtime})
    except OSError as error:
        warning = f"rootの走査に失敗しました: {error}"

    # 走査後に一度だけインデックスを更新し、同じ`(host, root)`の不在エントリを回収する。
    resolved = _creation_times.update_creation_time_index(
        host,
        root,
        observed,
        migrate_legacy=migrates_legacy_ctime(source_id, migrate_legacy_ctime),
    )
    return [{**item, "ctime_epoch": resolved[item["path"]]} for item in scanned], warning


def search_root(
    root: pathlib.Path,
    query: str,
    source_id: str = "",
    *,
    check_stop: Callable[[], None] | None = None,
) -> set[str]:
    """本文へ検索語が部分一致する計画ファイルの相対パス集合を返す。

    `check_stop`はファイル1件ごとに呼び、送出した例外で検索を打ち切る。
    """
    needle = query.casefold()
    if not root.is_dir():
        return set()
    matched: set[str] = set()
    try:
        for path in root.rglob("*"):
            if check_stop is not None:
                check_stop()
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
