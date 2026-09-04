"""フィードバックキューの旧レイアウト・旧予約形式を一方向に移行する。"""

# 旧レイアウトの読取互換では、現行状態名を独立した定数として固定する。
# pylint: disable=duplicate-code

import pathlib
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager
from typing import Any

from _atk_mq_frontmatter import parse_frontmatter, serialize_frontmatter
from _tbd_scan import _TBD_TYPE as MQ_TYPE_TBD

MQ_STATE_INBOX = "inbox"
MQ_STATE_PLANNING = "planning"
MQ_STATE_PROCESSING = "processing"
MQ_STATE_ADOPTED = "adopted"
MQ_STATE_REJECTED = "rejected"
MQ_STATES = (MQ_STATE_INBOX, MQ_STATE_PROCESSING, MQ_STATE_PLANNING, MQ_STATE_ADOPTED, MQ_STATE_REJECTED)
"""旧ディレクトリ構成を読み取るための状態名。現行の保存状態が廃止した`planning`も走査対象に残す。"""
MQ_TYPE_FEEDBACK = "feedback"
MQ_TYPES = (MQ_TYPE_FEEDBACK, MQ_TYPE_TBD)


def _with_type_frontmatter(text: str, entry_type: str) -> str | None:
    """先頭frontmatterへ`type`キーを補った本文を返す。"""
    parsed = parse_frontmatter(text)
    if parsed is None:
        return None
    data, body = parsed
    if data.get("type") is not None:
        return text
    ordered = {"target_repo": data["target_repo"]} if "target_repo" in data else {}
    ordered["type"] = entry_type
    ordered.update((key, value) for key, value in data.items() if key not in ordered)
    return serialize_frontmatter(ordered, body)


def _plan_legacy_migration(
    private_notes: pathlib.Path, legacy_dirs: list[pathlib.Path]
) -> list[tuple[pathlib.Path, pathlib.Path, str]]:
    """旧レイアウト配下の移行計画を`(移行元, 移行先, 移行後本文)`の一覧として返す。

    移行を妨げる要素（未知のディレクトリ・エントリ以外のファイル・移行先の衝突・
    frontmatterの破損）を検出した場合は、全件を列挙してからexit 2で終了する。
    ファイルへ触れる前に全件を検証し、中断時に移行途中の状態を残さない。
    """
    planned: list[tuple[pathlib.Path, pathlib.Path, str]] = []
    destinations: set[pathlib.Path] = set()
    errors: list[str] = []
    for legacy_dir in legacy_dirs:
        entry_type = legacy_dir.name
        for path in sorted(legacy_dir.rglob("*")):
            relative = path.relative_to(legacy_dir)
            if path.is_dir():
                if len(relative.parts) != 1 or relative.parts[0] not in MQ_STATES:
                    errors.append(f"未知のディレクトリ: {path}")
                continue
            if path.name == ".gitkeep":
                continue
            if len(relative.parts) != 2 or path.suffix != ".md":
                errors.append(f"エントリとして解釈できないファイル: {path}")
                continue
            destination = private_notes / relative.parts[0] / path.name
            if destination.exists() or destination in destinations:
                errors.append(f"移行先が既に存在: {destination}")
                continue
            migrated = _with_type_frontmatter(path.read_text(encoding="utf-8"), entry_type)
            if migrated is None:
                errors.append(f"frontmatterが不正: {path}")
                continue
            destinations.add(destination)
            planned.append((path, destination, migrated))
    if errors:
        print(f"旧レイアウトの移行を中止しました（{private_notes}）。以下を解消してから再実行してください。", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        sys.exit(2)
    return planned


def _tracked_names(private_notes: pathlib.Path, names: Iterable[str]) -> list[str]:
    """指定名のうち、gitの追跡対象を含むものだけを返す。

    追跡対象を1件も含まないパスを`git add`へ渡すとpathspec不一致で失敗するため、
    削除済みディレクトリをcommit対象へ含める際の限定に用いる。
    """
    name_list = list(names)
    result = subprocess.run(
        ["git", "ls-files", "--", *name_list],
        cwd=private_notes,
        capture_output=True,
        text=True,
        check=True,
    )
    assert isinstance(result.stdout, str)
    tracked = {line.split("/", 1)[0] for line in result.stdout.splitlines()}
    return [name for name in name_list if name in tracked]


def migrate_legacy_layout(
    private_notes: pathlib.Path,
    *,
    repo_lock_fn: Callable[[pathlib.Path], AbstractContextManager[object]],
    pull_fn: Callable[[pathlib.Path], None],
    commit_fn: Callable[[pathlib.Path, str, Iterable[str]], None],
) -> None:
    """旧2階層レイアウトの管理repoを平坦レイアウトへ移行する。

    旧レイアウトは種別ごとのディレクトリ（`<type>/<state>/`）でエントリ種別を表現していた。
    平坦レイアウトは状態ディレクトリ（`<state>/`）のみをrepo直下へ置き、種別は
    frontmatterの`type`で表現する。移行では各エントリへ`type`行を補ってから状態
    ディレクトリへ移し、旧ディレクトリを削除してcommit・pushする。

    旧ディレクトリが無い場合は無動作で戻る（通常運用でのロック取得・pullを避けるため）。
    移行前のバージョンのコマンドが並行稼働していると空の旧ディレクトリが再生成されうるため、
    commit対象が生じない場合はcommitへ進まず削除のみで完結させる。
    """
    if not any((private_notes / name).is_dir() for name in MQ_TYPES):
        return
    with repo_lock_fn(private_notes):
        pull_fn(private_notes)
        # 他プロセス・他端末が先に移行済みの場合があるため、pull後の状態で再判定する。
        legacy_dirs = [private_notes / name for name in MQ_TYPES if (private_notes / name).is_dir()]
        if not legacy_dirs:
            return
        planned = _plan_legacy_migration(private_notes, legacy_dirs)
        for source, destination, migrated in planned:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(migrated, encoding="utf-8")
            source.unlink()
        legacy_names = [legacy_dir.name for legacy_dir in legacy_dirs]
        tracked_legacy_names = _tracked_names(private_notes, legacy_names)
        for legacy_dir in legacy_dirs:
            shutil.rmtree(legacy_dir)
        count = len(planned)
        if not count and not tracked_legacy_names:
            print(f"旧レイアウトの空ディレクトリを削除しました: {private_notes}", file=sys.stderr)
            return
        commit_fn(
            private_notes,
            f"chore: migrate {count} {'entry' if count == 1 else 'entries'} to flat layout",
            [*(name for name in MQ_STATES if (private_notes / name).is_dir()), *tracked_legacy_names],
        )
        print(f"旧レイアウトの{count}件を平坦レイアウトへ移行しました: {private_notes}", file=sys.stderr)


_LEGACY_RESERVATION_INTERNAL_REPO = "internal/agent-toolkit/reservations"


def _is_legacy_token_hash(value: object) -> bool:
    """2.34.0の予約が記録したSHA-256値かを返す。"""
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _legacy_reservation_companion(data: dict[str, object]) -> dict[str, str] | None:
    """2.34.0が生成した内部companion metadataを返す。"""
    raw = data.get("reservation_companion")
    if (
        data.get("target_repo") != _LEGACY_RESERVATION_INTERNAL_REPO
        or data.get("type") != MQ_TYPE_FEEDBACK
        or not isinstance(raw, dict)
    ):
        return None
    metadata: dict[Any, Any] = raw
    target_repo = metadata.get("target_repo")
    target_filename = metadata.get("target_filename")
    token_hash = metadata.get("token_hash")
    if (
        not isinstance(target_repo, str)
        or not target_repo
        or not isinstance(target_filename, str)
        or pathlib.Path(target_filename).name != target_filename
        or not target_filename.endswith(".md")
        or not _is_legacy_token_hash(token_hash)
    ):
        return None
    assert isinstance(token_hash, str)
    return {"target_repo": target_repo, "target_filename": target_filename, "token_hash": token_hash}


def _is_legacy_reservation(
    data: dict[str, object],
    *,
    filename: str,
    state: str,
    companions: tuple[tuple[str, dict[str, str]], ...],
) -> bool:
    """対応する内部companionを持つ2.34.0形式の予約かを返す。"""
    raw = data.get("reservation")
    target_repo = data.get("target_repo")
    if state != MQ_STATE_PROCESSING or data.get("type") != MQ_TYPE_FEEDBACK or raw is None:
        return False
    if not isinstance(target_repo, str) or not target_repo:
        return False
    reservation: dict[Any, Any] = raw if isinstance(raw, dict) else {}
    companion = reservation.get("companion")
    token_hash = reservation.get("token_hash")
    matching = tuple(
        (companion_filename, metadata)
        for companion_filename, metadata in companions
        if metadata["target_repo"] == target_repo and metadata["target_filename"] == filename
    )
    if not matching:
        return False
    if isinstance(companion, str) and _is_legacy_token_hash(token_hash):
        return any(
            companion_filename == companion and metadata["token_hash"] == token_hash
            for companion_filename, metadata in matching
        )
    return True


def migrate_legacy_reservations(
    private_notes: pathlib.Path,
    *,
    assert_lock_fn: Callable[[pathlib.Path], None],
    commit_fn: Callable[[pathlib.Path, str, Iterable[str]], None],
) -> int:
    """2.34.0形式の予約を通常のinbox項目へ一方向に移行する。"""
    assert_lock_fn(private_notes)
    inbox_dir = private_notes / MQ_STATE_INBOX
    processing_dir = private_notes / MQ_STATE_PROCESSING
    parsed_entries: list[tuple[pathlib.Path, dict[str, object], str]] = []
    companion_paths: set[pathlib.Path] = set()
    companion_metadata: list[tuple[str, dict[str, str]]] = []
    companion_names: set[str] = set()
    reservation_paths: set[pathlib.Path] = set()

    for state_dir in (inbox_dir, processing_dir):
        if not state_dir.is_dir():
            continue
        for path in sorted(state_dir.glob("*.md")):
            parsed = parse_frontmatter(path.read_text(encoding="utf-8"))
            if parsed is None:
                continue
            data, body = parsed
            parsed_entries.append((path, data, body))
            metadata = _legacy_reservation_companion(data)
            if metadata is not None:
                companion_paths.add(path)
                companion_metadata.append((path.name, metadata))
                companion_names.add(path.name)
            if _is_legacy_reservation(
                data,
                filename=path.name,
                state=path.parent.name,
                companions=tuple(companion_metadata),
            ):
                reservation_paths.add(path)

    if not companion_paths and not reservation_paths:
        return 0

    inbox_dir.mkdir(parents=True, exist_ok=True)

    move_conflicts = sorted(
        path.name for path in reservation_paths if path.parent == processing_dir and (inbox_dir / path.name).exists()
    )
    if move_conflicts:
        raise RuntimeError("旧予約の移行先に同名項目が存在します: " + ", ".join(move_conflicts))

    for path, data, body in parsed_entries:
        if path in companion_paths:
            path.unlink()
            continue

        changed = False
        if path in reservation_paths:
            data.pop("reservation", None)
            data.pop("target_commit_history", None)
            changed = True
        dependencies = data.get("depends_on")
        if isinstance(dependencies, list):
            retained = [value for value in dependencies if value not in companion_names]
            if retained != dependencies:
                data["depends_on"] = retained
                changed = True
        if changed:
            path.write_text(serialize_frontmatter(data, body), encoding="utf-8")

    for path in sorted(reservation_paths):
        if path.parent != processing_dir:
            continue
        destination = inbox_dir / path.name
        shutil.move(str(path), str(destination))

    migrated_count = len(reservation_paths) + len(companion_paths)
    commit_fn(
        private_notes,
        f"chore: migrate {migrated_count} legacy queue reservations",
        (MQ_STATE_INBOX, MQ_STATE_PROCESSING),
    )
    print(f"旧予約形式の{migrated_count}件を通常inboxへ移行しました。", file=sys.stderr)
    return migrated_count
