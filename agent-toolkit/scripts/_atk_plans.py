"""計画ファイルのcheckout・commitと旧保存先からの移行を提供するCLI補助。

checkout記録は取得からcommit成功まで保持し、取得元と取得時点の内容を更新時の
競合検出に使う。commit又はpush失敗後の再実行と、作業バンドルを削除して取得を
取り消した後の記録回収も、同じ`atk plans commit`で処理する。
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import stat
import subprocess
import sys
import tempfile
from collections.abc import Iterable

import _atk_git_sync
import _atk_help
import _atk_mq_common as _common
import _atk_mq_frontmatter as _frontmatter
import _git_command
import _plan_file
import platformdirs

_MIGRATION_SECTION_NAMES = frozenset(("変更履歴（計画時）", "変更履歴"))
_FENCE_RE = re.compile(r"^(?P<indent>\s*)(?P<marker>`{3,}|~{3,})(?P<info>[^\r\n]*)")
_HEADING_RE = re.compile(r"^\s*(?P<marks>#{1,6})\s+(?P<title>.*?)\s*#*\s*$")
_PATH_TOKEN_CHARACTER_CLASS = r"[A-Za-z0-9_./\\~+%@-]"
_MAIN_ATTACHMENT_SUFFIXES = (".detail.md", ".bugs.md", ".review.md", "-workaround-check.md", ".codex.log")
_SAVED_BUNDLE_CONFLICT_MESSAGE = (
    "保存先に内容の異なる計画ファイルがあります: {destination}。"
    "保存済みの計画を更新する場合は、作業側のファイルを作業root外へ退避し、"
    "`atk plans checkout`で取得してから退避した内容へ置き換えて再実行してください"
)


def build_parser(parser) -> None:
    """`atk plans`配下のparserを構築する。"""
    sub = _atk_help.add_subcommands(
        parser,
        dest="plans_subcommand",
        required=False,
        show_help_when_missing=True,
    )
    commit_parser = _atk_help.add_command(sub, "commit", **_atk_help.HELP["atk plans commit"])
    commit_parser.add_argument(
        "plan_file",
        metavar="PLAN_FILE",
        help=(
            "計画作業root直下のメイン計画ファイル名（dd-{名称}-{16進数4桁}.md）、"
            "または保存root相対のyyyy/MM/dd-{名称}-{16進数4桁}.md。"
        ),
    )
    commit_parser.add_argument(
        "--skip-push",
        action="store_true",
        help="保存rootへ対象限定commitを作成し、pushは行わない",
    )
    checkout_parser = _atk_help.add_command(sub, "checkout", **_atk_help.HELP["atk plans checkout"])
    checkout_parser.add_argument(
        "plan_file",
        metavar="PLAN_FILE",
        help="plans rootからの相対メイン計画パス。",
    )
    _atk_help.add_command(sub, "migrate", **_atk_help.HELP["atk plans migrate"])


def _excluded_path(path: pathlib.Path) -> bool:
    """計画バンドルから一時ファイルを除外する。"""
    return path.name.endswith((".lock", ".bak", ".tmp"))


def _as_relative_notes_path(path: pathlib.Path, private_notes: pathlib.Path) -> str:
    """private-notesからの安全なPOSIX相対パスを返す。"""
    try:
        relative = path.resolve(strict=False).relative_to(private_notes.resolve(strict=False))
    except (OSError, ValueError) as error:
        raise _common.WebInputError(f"private-notes外のパスをcommit対象にできません: {path}") from error
    if any(part in ("", ".", "..") for part in relative.parts):
        raise _common.WebInputError(f"commit対象の相対パスが不正です: {path}")
    return relative.as_posix()


def _tracked_deleted_paths(private_notes: pathlib.Path) -> set[pathlib.Path]:
    """Gitが追跡している削除済みパスを返す。"""
    result = _git_command.run(
        ["ls-files", "--deleted", "-z"],
        cwd=private_notes,
        check=True,
        capture_output=True,
        text=False,
    )
    if not isinstance(result.stdout, bytes):
        raise RuntimeError("git ls-files --deletedの出力をbytesとして取得できません")
    return {private_notes / pathlib.Path(raw.decode("utf-8")) for raw in result.stdout.split(b"\0") if raw}


def _plan_bundle(private_notes: pathlib.Path, relative_main: pathlib.Path) -> tuple[pathlib.Path, ...]:
    """指定メイン計画に属する実在・削除済みのbundleを返す。"""
    parent = (private_notes / _plan_file.NEW_PLANS_DIRECTORY / relative_main.parent).resolve(strict=False)
    main = parent / relative_main.name
    if not parent.is_relative_to((private_notes / _plan_file.NEW_PLANS_DIRECTORY).resolve(strict=False)):
        raise _common.WebInputError("計画バンドルがplans root外を指しています")
    stem = main.stem
    candidates: set[pathlib.Path] = set()
    if main.is_file() and not main.is_symlink():
        candidates.add(main)
    if parent.is_dir():
        for path in parent.iterdir():
            if path.name.startswith(f"{stem}.") and path.is_file() and not path.is_symlink() and not _excluded_path(path):
                candidates.add(path)
    for path in _tracked_deleted_paths(private_notes):
        if path.parent == parent and (path.name == main.name or path.name.startswith(f"{stem}.")) and not _excluded_path(path):
            candidates.add(path)
    if not candidates:
        raise _common.WebInputError(f"指定したメイン計画または計画バンドルが見つかりません: {relative_main}")
    if main not in candidates:
        raise _common.WebInputError(f"指定したメイン計画が見つかりません: {relative_main}")
    return tuple(sorted(candidates))


def _working_plan_bundle(home: pathlib.Path | str | None, relative_main: pathlib.Path) -> tuple[pathlib.Path, ...]:
    """作業rootにある指定stemの通常ファイルを返す。"""
    root = _plan_file.working_plans_root(home).resolve(strict=False)
    parent = (root / relative_main.parent).resolve(strict=False)
    if not parent.is_relative_to(root):
        raise _common.WebInputError("計画作業バンドルが作業root外を指しています")
    main = parent / relative_main.name
    if not main.is_file() or main.is_symlink():
        return ()
    stem = main.stem
    candidates = tuple(
        sorted(
            path
            for path in parent.iterdir()
            if (path == main or path.name.startswith(f"{stem}."))
            and path.is_file()
            and not path.is_symlink()
            and not _excluded_path(path)
        )
    )
    return candidates


def _saved_plan_bundle(private_notes: pathlib.Path, relative_main: pathlib.Path) -> tuple[pathlib.Path, ...]:
    """保存rootに実在する指定stemの通常ファイルを返す。"""
    root = _plan_file.new_plans_root(private_notes).resolve(strict=False)
    parent = (root / relative_main.parent).resolve(strict=False)
    if not parent.is_relative_to(root):
        raise _common.WebInputError("計画保存バンドルが保存root外を指しています")
    main = parent / relative_main.name
    if not main.is_file() or main.is_symlink():
        return ()
    stem = main.stem
    return tuple(
        sorted(
            path
            for path in parent.iterdir()
            if (path == main or path.name.startswith(f"{stem}."))
            and path.is_file()
            and not path.is_symlink()
            and not _excluded_path(path)
        )
    )


def _bundle_contents_at_ref(
    private_notes: pathlib.Path,
    relative_main: pathlib.Path,
    ref: str,
) -> dict[str, bytes]:
    """Git参照上の指定stemをworktreeへ反映せず読み取る。"""
    relative_parent = pathlib.Path(_plan_file.NEW_PLANS_DIRECTORY) / relative_main.parent
    result = _git_command.run(
        ["ls-tree", "-rz", "--name-only", ref, "--", relative_parent.as_posix()],
        cwd=private_notes,
        check=True,
        capture_output=True,
        text=False,
    )
    if not isinstance(result.stdout, bytes):
        raise RuntimeError("git ls-treeの出力をbytesとして取得できません")
    main_path = relative_parent / relative_main.name
    stem = relative_main.stem
    paths = tuple(pathlib.Path(raw.decode("utf-8")) for raw in result.stdout.split(b"\0") if raw)
    bundle = tuple(
        path
        for path in paths
        if path.parent == relative_parent
        and (path == main_path or path.name.startswith(f"{stem}."))
        and not _excluded_path(path)
    )
    if main_path not in bundle:
        return {}
    contents: dict[str, bytes] = {}
    for path in bundle:
        content = _git_command.run(
            ["show", f"{ref}:{path.as_posix()}"],
            cwd=private_notes,
            check=True,
            capture_output=True,
            text=False,
        )
        if not isinstance(content.stdout, bytes):
            raise RuntimeError("git showの出力をbytesとして取得できません")
        contents[path.name] = content.stdout
    return contents


def _checkout_record_root(relative_main: pathlib.Path) -> pathlib.Path:
    """指定計画stemのcheckout記録ディレクトリを返す。"""
    state_root = pathlib.Path(platformdirs.user_state_dir("agent-toolkit", appauthor=False))
    return state_root / "plan-checkouts" / relative_main.stem


def _validate_saved_plan_relative_path(plan_file: str) -> pathlib.Path:
    """正規形式又は移行済み形式の保存root相対メイン計画パスを返す。"""
    try:
        return _plan_file.validate_plan_relative_path(plan_file)
    except ValueError as canonical_error:
        try:
            return _plan_file.validate_migrated_plan_relative_path(plan_file)
        except ValueError as migrated_error:
            raise _common.WebInputError(str(canonical_error)) from migrated_error


def _read_checkout_record(relative_main: pathlib.Path) -> tuple[pathlib.Path, dict[str, bytes]] | None:
    """checkout記録を検証して読み込む。"""
    root = _checkout_record_root(relative_main)
    if not root.exists():
        return None
    if root.is_symlink() or not root.is_dir():
        raise _common.WebInputError(f"計画の取得記録が不正です: {root}")
    try:
        raw_metadata = json.loads((root / "meta.json").read_text(encoding="utf-8"))
        recorded_relative = _validate_saved_plan_relative_path(raw_metadata["relative_main"])
    except (OSError, KeyError, TypeError, _common.WebInputError, json.JSONDecodeError) as error:
        raise _common.WebInputError(f"計画の取得記録を読み取れません: {root}") from error
    if recorded_relative.stem != relative_main.stem:
        raise _common.WebInputError(f"計画の取得記録とstemが一致しません: {root}")
    files_root = root / "files"
    if files_root.is_symlink() or not files_root.is_dir():
        raise _common.WebInputError(f"計画の取得記録にファイルsnapshotがありません: {root}")
    snapshots: dict[str, bytes] = {}
    for path in files_root.iterdir():
        if path.is_symlink() or not path.is_file() or _excluded_path(path):
            raise _common.WebInputError(f"計画の取得記録に不正なファイルがあります: {path}")
        snapshots[path.name] = path.read_bytes()
    if recorded_relative.name not in snapshots:
        raise _common.WebInputError(f"計画の取得記録にメイン計画がありません: {root}")
    return recorded_relative, snapshots


def _remove_checkout_record(relative_main: pathlib.Path) -> None:
    """指定計画のcheckout記録だけを回収する。"""
    root = _checkout_record_root(relative_main)
    if not root.exists():
        return
    files_root = root / "files"
    if files_root.is_dir() and not files_root.is_symlink():
        for path in files_root.iterdir():
            if path.is_file() and not path.is_symlink():
                path.unlink()
        files_root.rmdir()
    metadata = root / "meta.json"
    if metadata.is_file() and not metadata.is_symlink():
        metadata.unlink()
    root.rmdir()


def _write_checkout_record(relative_main: pathlib.Path, snapshots: dict[str, bytes]) -> None:
    """取得元と取得時点のbytesをcheckout記録へ排他的に保存する。"""
    root = _checkout_record_root(relative_main)
    try:
        root.mkdir(parents=True, exist_ok=False)
    except FileExistsError as error:
        raise _common.WebInputError(f"同じ計画を取得済みです: {relative_main}") from error
    try:
        files_root = root / "files"
        files_root.mkdir()
        (root / "meta.json").write_text(
            json.dumps({"relative_main": relative_main.as_posix()}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        for name, content in snapshots.items():
            (files_root / name).write_bytes(content)
    except Exception:
        _remove_checkout_record(relative_main)
        raise


def checkout_plan(
    private_notes: pathlib.Path,
    plan_file: str,
    *,
    home: pathlib.Path | str | None = None,
) -> tuple[pathlib.Path, ...]:
    """保存済み計画バンドルを作業rootへ取得し、取得時点を記録する。"""
    relative_main = _validate_saved_plan_relative_path(plan_file)
    if _checkout_record_root(relative_main).exists():
        raise _common.WebInputError(f"同じ計画を取得済みです: {relative_main}")
    saved_bundle = _saved_plan_bundle(private_notes, relative_main)
    if not saved_bundle:
        raise _common.WebInputError(f"指定したメイン計画が見つかりません: {relative_main}")
    working_root = _plan_file.working_plans_root(home)
    destinations = tuple(working_root / path.name for path in saved_bundle)
    conflicts = tuple(path for path in destinations if path.exists())
    if conflicts:
        raise _common.WebInputError(f"作業rootに同名ファイルがあります: {conflicts[0]}")
    snapshots = {path.name: path.read_bytes() for path in saved_bundle}
    working_root.mkdir(parents=True, exist_ok=True)
    copied: list[pathlib.Path] = []
    try:
        for destination in destinations:
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                output.write(snapshots[destination.name])
            copied.append(destination)
        _write_checkout_record(relative_main, snapshots)
    except Exception:
        for path in copied:
            path.unlink(missing_ok=True)
        raise
    return destinations


def _copy_working_bundle(
    private_notes: pathlib.Path,
    relative_main: pathlib.Path,
    working_bundle: tuple[pathlib.Path, ...],
) -> tuple[tuple[pathlib.Path, ...], dict[pathlib.Path, tuple[tuple[int, int], bytes]]]:
    """作業バンドルを保存rootへ排他的に複製し、回収用snapshotを返す。"""
    destination_directory = _plan_file.new_plans_root(private_notes) / relative_main.parent
    destination_directory.mkdir(parents=True, exist_ok=True)
    snapshots: dict[pathlib.Path, tuple[tuple[int, int], bytes]] = {}
    destinations: list[pathlib.Path] = []
    for source in working_bundle:
        source_stat = source.stat(follow_symlinks=False)
        content = source.read_bytes()
        snapshots[source] = ((source_stat.st_dev, source_stat.st_ino), content)
        destination = destination_directory / source.name
        if destination.exists():
            if destination.is_symlink() or not destination.is_file() or destination.read_bytes() != content:
                raise _common.WebInputError(_SAVED_BUNDLE_CONFLICT_MESSAGE.format(destination=destination))
            destinations.append(destination)
            continue
        descriptor, raw_temporary = tempfile.mkstemp(prefix=f".{source.name}.", suffix=".tmp", dir=destination_directory)
        temporary = pathlib.Path(raw_temporary)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            try:
                os.link(temporary, destination)
            except FileExistsError:
                if destination.is_symlink() or not destination.is_file() or destination.read_bytes() != content:
                    raise _common.WebInputError(_SAVED_BUNDLE_CONFLICT_MESSAGE.format(destination=destination)) from None
            if destination.read_bytes() != content:
                raise _common.WebInputError(f"保存した計画ファイルの読戻し内容が一致しません: {destination}")
        finally:
            temporary.unlink(missing_ok=True)
        destinations.append(destination)
    return tuple(sorted(destinations)), snapshots


def _remove_finalized_working_bundle(
    working_bundle: tuple[pathlib.Path, ...],
    snapshots: dict[pathlib.Path, tuple[tuple[int, int], bytes]],
    destinations: tuple[pathlib.Path, ...],
    relative_main: pathlib.Path,
    home: pathlib.Path | str | None,
) -> None:
    """保存成功後に変更されていない作業ファイルを日時ごと移す。"""
    for source in working_bundle:
        identity, content = snapshots[source]
        current = source.stat(follow_symlinks=False)
        if (current.st_dev, current.st_ino) != identity or source.read_bytes() != content:
            raise _common.WebInputError(f"保存処理中に作業ファイルが変更されたため回収しません: {source}")
    destination_by_name = {path.name: path for path in destinations}
    ordered_sources = sorted(working_bundle, key=lambda path: (path.name == relative_main.name, path.name))
    for source in ordered_sources:
        _finalize_plan_file(source, destination_by_name[source.name], snapshots[source][1])
    root = _plan_file.working_plans_root(home).resolve(strict=False)
    for directory in (working_bundle[0].parent, working_bundle[0].parent.parent):
        if directory != root and directory.is_relative_to(root):
            try:
                directory.rmdir()
            except OSError:
                break


def _working_snapshots(
    working_bundle: tuple[pathlib.Path, ...],
) -> dict[pathlib.Path, tuple[tuple[int, int], bytes]]:
    """作業バンドルの回収前照合用snapshotを返す。"""
    snapshots: dict[pathlib.Path, tuple[tuple[int, int], bytes]] = {}
    for source in working_bundle:
        metadata = source.stat(follow_symlinks=False)
        snapshots[source] = ((metadata.st_dev, metadata.st_ino), source.read_bytes())
    return snapshots


def _remove_checked_out_working_bundle(
    working_bundle: tuple[pathlib.Path, ...],
    snapshots: dict[pathlib.Path, tuple[tuple[int, int], bytes]],
    relative_main: pathlib.Path,
) -> None:
    """保存成功後に変更されていないcheckout作業バンドルを回収する。"""
    for source in working_bundle:
        identity, content = snapshots[source]
        current = source.stat(follow_symlinks=False)
        if (current.st_dev, current.st_ino) != identity or source.read_bytes() != content:
            raise _common.WebInputError(f"保存処理中に作業ファイルが変更されたため回収しません: {source}")
    for source in sorted(working_bundle, key=lambda path: (path.name == relative_main.name, path.name)):
        source.unlink()


def _bundle_contents(bundle: Iterable[pathlib.Path]) -> dict[str, bytes]:
    """計画バンドルをファイル名からbytesへの写像として返す。"""
    return {path.name: path.read_bytes() for path in bundle}


def _update_saved_bundle(
    destination_directory: pathlib.Path,
    saved_bundle: tuple[pathlib.Path, ...],
    working_contents: dict[str, bytes],
) -> tuple[pathlib.Path, ...]:
    """保存側のinodeを維持して作業側の内容へ更新する。

    claude-plans-viewerは作成日時を計画の並び順へ使うため、同名の既存ファイルは
    置換せず書き込み、新規ファイルだけを排他的に作成する。
    """
    saved_by_name = {path.name: path for path in saved_bundle}
    for name, content in working_contents.items():
        destination = destination_directory / name
        if name in saved_by_name:
            destination.write_bytes(content)
        else:
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                output.write(content)
        if destination.read_bytes() != content:
            raise _common.WebInputError(f"保存した計画ファイルの読戻し内容が一致しません: {destination}")
    for name, path in saved_by_name.items():
        if name not in working_contents:
            path.unlink()
    return tuple(sorted(destination_directory / name for name in working_contents))


def _finalize_plan_file(source: pathlib.Path, destination: pathlib.Path, content: bytes) -> None:
    """commit済みの移し先を、移し元の内容と日時を保って確定する。

    内容が同じ場合は`os.replace`だけでinodeごと移す。内容を変換する場合は、移し元と同じ
    ディレクトリへ原文の複製を作成し、移し元への書戻し、日時復元及び内容検証を確定前に行う。
    確定前の失敗では複製から移し元を復元し、復元自体が失敗した場合は実在するパスと手作業を
    報告する。確定後の複製削除だけが失敗した場合は、移行結果へ影響しない警告として扱う。

    `atk serve`の計画ファイル画面は作成日時を計画の並び順へ使う。Linuxには作成日時を設定するAPIが
    無いため、新規ファイルによる確定ではなく移し元のinodeを`os.replace`で移す。
    """
    original = source.read_bytes()
    if original == content:
        os.replace(source, destination)
        return

    metadata = source.stat(follow_symlinks=False)
    timestamps = (metadata.st_atime_ns, metadata.st_mtime_ns)
    descriptor, raw_backup = tempfile.mkstemp(prefix=f".{source.name}.", suffix=".tmp", dir=source.parent)
    backup = pathlib.Path(raw_backup)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(original)
            output.flush()
            os.fsync(output.fileno())
        os.utime(backup, ns=timestamps)
    except OSError:
        backup.unlink(missing_ok=True)
        raise

    try:
        source.write_bytes(content)
        os.utime(source, ns=timestamps)
        if source.read_bytes() != content:
            raise _common.WebInputError(f"確定前の計画ファイルの読戻し内容が一致しません: {source}")
        os.replace(source, destination)
    except (OSError, _common.WebInputError) as error:
        try:
            source.write_bytes(backup.read_bytes())
            os.utime(source, ns=timestamps)
            backup.unlink()
        except OSError as recovery_error:
            existing = "、".join(str(path) for path in (source, destination, backup) if path.exists())
            print(
                f"error: 計画ファイルを自動復元できません: {recovery_error}。"
                f"実在するパス: {existing}。退避用の複製 {backup} の内容を {source} へ書き戻し、"
                "移行前の日時を復元してください",
                file=sys.stderr,
            )
            raise _common.WebInputError(f"計画ファイルを自動復元できません: {source}") from error
        raise

    try:
        backup.unlink()
    except OSError as error:
        print(
            f"warning: 移行は完了しましたが、退避用の複製を削除できません: {backup}: {error}。この複製は移行結果に影響しません",
            file=sys.stderr,
        )


def _plan_display_name(relative_main: pathlib.Path) -> str:
    """commitメッセージへ含める計画名を返す。"""
    filename = relative_main.name
    match = re.fullmatch(r"[0-9]{2}-(?P<label>.+)-[0-9a-f]{4}\.md", filename)
    if match is not None:
        return match.group("label")
    migrated = re.fullmatch(r"[0-9]{2}-(?P<label>.+)\.md", filename)
    if migrated is not None:
        return migrated.group("label")
    return relative_main.stem


def commit_plan(
    private_notes: pathlib.Path,
    plan_file: str,
    *,
    home: pathlib.Path | str | None = None,
    lock_timeout: float = -1,
    skip_push: bool = False,
) -> dict[str, object]:
    """指定計画bundleを保存rootへ移し、対象限定commitを作成する。"""
    working_relative: pathlib.Path | None = None
    try:
        working_relative = _plan_file.validate_working_plan_relative_path(plan_file)
        relative_main = working_relative
    except ValueError as working_error:
        try:
            relative_main = _plan_file.validate_plan_relative_path(plan_file)
        except ValueError as saved_error:
            try:
                relative_main = _plan_file.validate_migrated_plan_relative_path(plan_file)
            except ValueError:
                raise _common.WebInputError(str(working_error)) from saved_error
    requested_relative = working_relative or relative_main
    checkout_record = _read_checkout_record(requested_relative)
    recorded_contents: dict[str, bytes] = {}
    if checkout_record is not None:
        relative_main, recorded_contents = checkout_record
        working_lookup_relative = pathlib.Path(relative_main.name)
    else:
        working_lookup_relative = requested_relative
    working_bundle = _working_plan_bundle(home, working_lookup_relative)
    if checkout_record is not None:
        if not working_bundle:
            _remove_checkout_record(requested_relative)
            return {"plan_file": relative_main.as_posix(), "paths": (), "message": ""}
    elif working_relative is not None and not working_bundle:
        raise _common.WebInputError(f"指定した作業中の計画バンドルが見つかりません: {working_relative}")
    if checkout_record is None and working_relative is not None and working_bundle:
        working_main = _plan_file.working_plans_root(home) / working_relative
        date = _birth_date(working_main).split("/")
        relative_main = pathlib.Path(date[0], date[1], working_relative.name)
    with _atk_git_sync.repo_lock(private_notes, timeout=lock_timeout):
        saved_main = _plan_file.new_plans_root(private_notes) / relative_main
        saved_bundle_exists = saved_main.is_file()
        if checkout_record is not None and _atk_git_sync.has_remote(private_notes):
            _git_command.run(["fetch"], cwd=private_notes, check=True)
        elif not working_bundle or not saved_bundle_exists:
            if not skip_push:
                _atk_git_sync.push_pending_commits(private_notes)
            _atk_git_sync.pull(private_notes)
        snapshots: dict[pathlib.Path, tuple[tuple[int, int], bytes]] = {}
        if checkout_record is not None:
            saved_bundle = _saved_plan_bundle(private_notes, relative_main)
            if not saved_bundle:
                raise _common.WebInputError(f"取得元の計画バンドルが見つかりません: {relative_main}")
            saved_contents = _bundle_contents(saved_bundle)
            working_contents = _bundle_contents(working_bundle)
            remote_contents = (
                _bundle_contents_at_ref(private_notes, relative_main, "@{u}")
                if _atk_git_sync.has_remote(private_notes)
                else saved_contents
            )
            if saved_contents not in (recorded_contents, working_contents) or remote_contents not in (
                recorded_contents,
                working_contents,
            ):
                raise _common.WebInputError(f"取得後に保存元の計画バンドルが変更されています: {relative_main}")
            snapshots = _working_snapshots(working_bundle)
            if saved_contents == recorded_contents:
                _update_saved_bundle(saved_main.parent, saved_bundle, working_contents)
            bundle = _plan_bundle(private_notes, relative_main)
        elif working_bundle:
            bundle, snapshots = _copy_working_bundle(private_notes, relative_main, working_bundle)
        else:
            bundle = _plan_bundle(private_notes, relative_main)
        relative_paths = tuple(_as_relative_notes_path(path, private_notes) for path in bundle)
        message = f"chore: update plan {_plan_display_name(relative_main)}"
        _atk_git_sync.commit_and_push(private_notes, message, relative_paths, skip_push=skip_push)
        if not skip_push and _atk_git_sync.has_remote(private_notes) and not _atk_git_sync.remote_contains_head(private_notes):
            raise _common.WebInputError("計画commitがremote branchへ到達したことを確認できません")
        if checkout_record is not None:
            _remove_checked_out_working_bundle(working_bundle, snapshots, requested_relative)
            _remove_checkout_record(requested_relative)
        elif working_bundle:
            _remove_finalized_working_bundle(working_bundle, snapshots, bundle, relative_main, home)
    return {"plan_file": relative_main.as_posix(), "paths": relative_paths, "message": message}


def _birth_date(path: pathlib.Path) -> str:
    """保存先の年月を決める日時を実行ホストのローカル日付へ変換する。"""
    try:
        return _plan_file.file_birth_date(path).strftime("%Y/%m/%d")
    except OSError as error:
        raise _common.WebInputError(str(error)) from error


def _legacy_files(legacy_root: pathlib.Path) -> tuple[pathlib.Path, ...]:
    """旧root配下の移行対象通常ファイルを再帰列挙する。"""
    if not legacy_root.exists():
        return ()
    if legacy_root.is_symlink() or not legacy_root.is_dir():
        raise _common.WebInputError(f"旧計画rootが通常ディレクトリではありません: {legacy_root}")
    files: list[pathlib.Path] = []
    root_resolved = legacy_root.resolve()
    for path in sorted(legacy_root.rglob("*")):
        if path.is_symlink() or _excluded_path(path):
            continue
        try:
            if not path.resolve(strict=False).is_relative_to(root_resolved):
                raise _common.WebInputError(f"旧計画root外を指すパスを検出しました: {path}")
        except OSError as error:
            raise _common.WebInputError(f"旧計画パスを検証できません: {path}") from error
        try:
            mode = path.stat(follow_symlinks=False).st_mode
        except OSError as error:
            raise _common.WebInputError(f"旧計画パスを検証できません: {path}") from error
        if stat.S_ISREG(mode):
            files.append(path)
    return tuple(files)


def _main_candidates(files: Iterable[pathlib.Path]) -> tuple[pathlib.Path, ...]:
    """付属ファイルを除いたmain候補を返す。"""
    candidates = tuple(
        path for path in files if path.name.endswith(".md") and not path.name.endswith(_MAIN_ATTACHMENT_SUFFIXES)
    )
    return tuple(
        path
        for path in candidates
        if not any(
            other != path and other.parent == path.parent and path.name.startswith(f"{other.stem}.") for other in candidates
        )
    )


def _associated_groups(files: tuple[pathlib.Path, ...]) -> dict[pathlib.Path, tuple[pathlib.Path, ...]]:
    """旧ファイルをmainと同stemのbundleへ所属させる。"""
    candidates = _main_candidates(files)
    groups: dict[pathlib.Path, list[pathlib.Path]] = {}
    for path in files:
        matches = tuple(
            candidate
            for candidate in candidates
            if candidate.parent == path.parent and (path == candidate or path.name.startswith(f"{candidate.stem}."))
        )
        if len(matches) > 1:
            names = "、".join(str(item) for item in matches)
            raise _common.WebInputError(f"計画付属ファイルの所属を一意に決められません: {path}（{names}）")
        group_key = matches[0] if matches else path
        groups.setdefault(group_key, []).append(path)
    return {key: tuple(sorted(values)) for key, values in groups.items()}


def _migratable_legacy_files(
    legacy_root: pathlib.Path,
    files: tuple[pathlib.Path, ...],
) -> tuple[pathlib.Path, ...]:
    """正規作業バンドルを除いた旧形式ファイルを返す。"""
    migratable: list[pathlib.Path] = []
    for main, members in _associated_groups(files).items():
        try:
            relative = main.relative_to(legacy_root)
            if len(relative.parts) == 1:
                _plan_file.validate_working_plan_relative_path(relative)
            else:
                _plan_file.validate_plan_relative_path(relative)
        except ValueError:
            migratable.extend(members)
    return tuple(sorted(migratable))


def _destination_map(
    private_notes: pathlib.Path,
    files: tuple[pathlib.Path, ...],
) -> dict[pathlib.Path, pathlib.Path]:
    """旧ファイルと新ファイルの対応表を事前確定する。"""
    groups = _associated_groups(files)
    destinations: dict[pathlib.Path, pathlib.Path] = {}
    seen_destinations: dict[pathlib.Path, pathlib.Path] = {}
    new_root = _plan_file.new_plans_root(private_notes)
    for key, members in groups.items():
        date = _birth_date(key if key in files and key.name.endswith(".md") else members[0])
        year, month, day = date.split("/")
        for source in members:
            destination = new_root / year / month / f"{day}-{source.name}"
            destination = destination.resolve(strict=False)
            if not destination.is_relative_to(new_root.resolve(strict=False)):
                raise _common.WebInputError(f"移行先がplans root外を指しています: {source}")
            previous = seen_destinations.get(destination)
            if previous is not None and previous != source:
                raise _common.WebInputError(f"移行先が重複しています: {destination}（{previous}、{source}）")
            seen_destinations[destination] = source
            destinations[source] = destination
    return destinations


def _path_tokens(path: pathlib.Path, legacy_root: pathlib.Path) -> tuple[str, ...]:
    """旧計画パスの完全な互換トークンを返す。"""
    relative = path.relative_to(legacy_root).as_posix()
    return (
        str(path),
        path.as_posix(),
        f"~/.claude/plans/{relative}",
    )


def _protected_ranges(text: str) -> tuple[set[int], tuple[str, ...]]:
    """計画履歴配下の逐語text fence行を保護範囲として返す。"""
    lines = text.splitlines(keepends=True)
    protected: set[int] = set()
    hashes: list[str] = []
    section = False
    opening: tuple[str, int] | None = None
    start = 0
    for index, line in enumerate(lines):
        if opening is not None:
            protected.add(index)
            marker, length = opening
            stripped = line.lstrip()
            if stripped.startswith(marker * length) and stripped[length:].strip() == "":
                block = "".join(lines[start : index + 1]).encode("utf-8")
                hashes.append(hashlib.sha256(block).hexdigest())
                opening = None
            continue
        heading = _HEADING_RE.match(line)
        if heading is not None and len(heading.group("marks")) <= 2:
            section = heading.group("title") in _MIGRATION_SECTION_NAMES
        if section:
            fence = _FENCE_RE.match(line)
            if fence is not None and fence.group("info").strip() == "text":
                opening = (fence.group("marker")[0], len(fence.group("marker")))
                start = index
                protected.add(index)
    if opening is not None:
        block = "".join(lines[start:]).encode("utf-8")
        hashes.append(hashlib.sha256(block).hexdigest())
    return protected, tuple(hashes)


def _replacement_pattern(replacements: dict[str, str]) -> re.Pattern[str] | None:
    """対応表の完全パストークンだけに一致する正規表現を返す。"""
    if not replacements:
        return None
    alternatives = "|".join(re.escape(old) for old in sorted(replacements, key=len, reverse=True))
    return re.compile(rf"(?<!{_PATH_TOKEN_CHARACTER_CLASS})(?:{alternatives})(?!{_PATH_TOKEN_CHARACTER_CLASS})")


def _replace_tokens(text: str, replacements: dict[str, str]) -> tuple[str, int, tuple[str, ...]]:
    """保護範囲外の完全パストークンを置換する。"""
    lines = text.splitlines(keepends=True)
    protected, hashes = _protected_ranges(text)
    count = 0
    pattern = _replacement_pattern(replacements)
    output: list[str] = []
    for index, line in enumerate(lines):
        if index in protected or pattern is None:
            output.append(line)
            continue

        def replace(match: re.Match[str]) -> str:
            nonlocal count
            count += 1
            return replacements[match.group(0)]

        output.append(pattern.sub(replace, line))
    return "".join(output), count, hashes


def _unprotected_tokens(text: str, replacements: dict[str, str]) -> tuple[str, ...]:
    """保護範囲外に残る旧tokenを返す。"""
    _, count, _ = _replace_tokens(text, replacements)
    del count
    lines = text.splitlines(keepends=True)
    protected, _ = _protected_ranges(text)
    pattern = _replacement_pattern(replacements)
    if pattern is None:
        return ()
    leftovers: list[str] = []
    for index, line in enumerate(lines):
        if index in protected:
            continue
        leftovers.extend(match.group(0) for match in pattern.finditer(line))
    return tuple(dict.fromkeys(leftovers))


def _read_transformed(path: pathlib.Path, replacements: dict[str, str]) -> tuple[bytes, int, tuple[str, ...]]:
    """UTF-8本文を置換し、非UTF-8ならbyte列を維持する。"""
    original = path.read_bytes()
    try:
        text = original.decode("utf-8")
    except UnicodeDecodeError:
        return original, 0, ()
    transformed, count, protected_hashes = _replace_tokens(text, replacements)
    return transformed.encode("utf-8"), count, protected_hashes


def _migrate_mq_contents(
    private_notes: pathlib.Path,
    replacements: dict[str, str],
) -> dict[pathlib.Path, bytes]:
    """全MQ状態の既知の計画参照と本文の旧パスをportable値へ移す。"""
    changes: dict[pathlib.Path, bytes] = {}
    for state in _common.MQ_STATES:
        directory = private_notes / state
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            if not path.is_file() or path.is_symlink() or path.suffix != ".md":
                continue
            original = path.read_bytes()
            try:
                text = original.decode("utf-8")
            except UnicodeDecodeError:
                continue
            # frontmatterも本文と同じ置換経路へ通し、plan_file以外の字面を再整形しない。
            serialized, _count, _hashes = _replace_tokens(text, replacements)
            updated = serialized.encode("utf-8")
            if updated != original:
                changes[path] = updated
    return changes


def _snapshot(paths: Iterable[pathlib.Path]) -> dict[pathlib.Path, bytes | None]:
    """変更対象の既存内容を保存する。"""
    return {path: path.read_bytes() if path.is_file() else None for path in paths}


def _restore_files(snapshot: dict[pathlib.Path, bytes | None]) -> None:
    """commit前失敗時に自処理のファイル変更だけを復元する。"""
    for path, content in snapshot.items():
        if content is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)


def _validate_references(
    private_notes: pathlib.Path,
    changes: dict[pathlib.Path, bytes],
) -> None:
    """変更後MQのportable plan_fileと移行先の実在を検証する。"""
    for path, content in changes.items():
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            continue
        parsed = _frontmatter.parse_frontmatter(text)
        if parsed is None:
            continue
        raw_plan = parsed[0].get("plan_file")
        if not isinstance(raw_plan, str) or not raw_plan.startswith(_plan_file.PORTABLE_PLAN_PREFIX):
            continue
        try:
            target = _plan_file.resolve_plan_file(raw_plan, private_notes=private_notes)
        except ValueError as error:
            raise _common.WebInputError(f"MQのplan_fileを解決できません: {path}") from error
        if not target.is_file() and target not in changes:
            raise _common.WebInputError(f"MQのplan_fileが移行先に存在しません: {path}")


def migrate_plans(
    private_notes: pathlib.Path,
    home: pathlib.Path | str | None = None,
    *,
    lock_timeout: float = -1,
) -> dict[str, object]:
    """旧計画rootを新plans rootへ移行し、push成功後に旧ファイルを削除する。"""
    legacy_root = _plan_file.legacy_plans_root(home)
    with _common.repo_lock(private_notes, timeout=lock_timeout):
        try:
            _atk_git_sync.ensure_not_rebasing(private_notes)
            if not _atk_git_sync.has_remote(private_notes):
                raise _common.WebInputError("remoteなしのprivate-notesでは計画移行を実行できません")
            if _atk_git_sync.is_worktree_dirty(private_notes):
                raise _common.WebInputError("private-notesのindex・worktreeがcleanでないため移行を開始できません")
            _atk_git_sync.require_upstream(private_notes)
            _atk_git_sync.pull(private_notes)
            if _atk_git_sync.is_worktree_dirty(private_notes):
                raise _common.WebInputError("remote同期後のprivate-notesがcleanでないため移行を開始できません")
        except _atk_git_sync.RebaseInProgressError as error:
            raise _common.WebInputError(str(error)) from error
        except _atk_git_sync.GitSyncError as error:
            raise _common.WebInputError(str(error)) from error

        files = _migratable_legacy_files(legacy_root, _legacy_files(legacy_root))
        if not files:
            return {"migrated": 0, "deleted": 0, "commit": None}
        destinations = _destination_map(private_notes, files)
        replacements = {
            token: _plan_file.to_portable_plan_file(destination, private_notes=private_notes)
            for source, destination in destinations.items()
            for token in _path_tokens(source, legacy_root)
        }

        changes: dict[pathlib.Path, bytes] = {}
        finalized_contents: dict[pathlib.Path, bytes] = {}
        protected_before: dict[pathlib.Path, tuple[str, ...]] = {}
        replacement_count = 0
        for source, destination in destinations.items():
            transformed, count, hashes = _read_transformed(source, replacements)
            finalized_contents[source] = transformed
            replacement_count += count
            protected_before[destination] = hashes
            existing = destination.read_bytes() if destination.is_file() else None
            if existing is not None and existing != transformed:
                raise _common.WebInputError(f"移行先に異なる内容のファイルが存在します: {destination}")
            if existing != transformed:
                changes[destination] = transformed

        mq_changes = _migrate_mq_contents(private_notes, replacements)
        changes.update(mq_changes)
        for path, content in changes.items():
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError:
                continue
            leftovers = _unprotected_tokens(text, replacements)
            if leftovers:
                raise _common.WebInputError(f"移行後も旧計画パスが残っています: {path}")
            if path in protected_before:
                _before = protected_before[path]
                _after = _protected_ranges(text)[1]
                if _before != _after:
                    raise _common.WebInputError(f"逐語textブロックが変更されています: {path}")

        _validate_references(private_notes, changes)
        start_head_result = _git_command.run(
            ["rev-parse", "HEAD"],
            cwd=private_notes,
            check=True,
            capture_output=True,
            text=True,
        )
        if not isinstance(start_head_result.stdout, str):
            raise RuntimeError("移行開始時のHEADを取得できません")
        start_head = start_head_result.stdout.strip()
        snapshot = _snapshot(changes)
        source_snapshots = {source: source.read_bytes() for source in files}
        try:
            for path, content in changes.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            _validate_references(private_notes, changes)
            commit_targets = set(destinations.values()) | set(mq_changes)
            relative_paths = tuple(_as_relative_notes_path(path, private_notes) for path in sorted(commit_targets))
            _atk_git_sync.commit_and_push(
                private_notes,
                "chore: migrate legacy plan files",
                relative_paths,
            )
            end_head_result = _git_command.run(
                ["rev-parse", "HEAD"],
                cwd=private_notes,
                check=True,
                capture_output=True,
                text=True,
            )
            if not isinstance(end_head_result.stdout, str):
                raise RuntimeError("移行後のHEADを取得できません")
            end_head = end_head_result.stdout.strip()
            if not _atk_git_sync.remote_contains_head(private_notes):
                raise _common.WebInputError("移行commitがremote branchへ到達したことを確認できません")
        except (OSError, subprocess.SubprocessError, _common.WebInputError):
            if start_head == _git_head(private_notes):
                _restore_files(snapshot)
            raise

        for source in files:
            if source.is_symlink() or not source.is_file() or source.read_bytes() != source_snapshots[source]:
                raise _common.WebInputError(f"旧ファイルの内容が移行中に変化しました: {source}")
            _finalize_plan_file(source, destinations[source], finalized_contents[source])
        return {
            "migrated": len(destinations),
            "deleted": len(files),
            "commit": end_head if end_head != start_head else None,
            "replacements": replacement_count,
        }


def _git_head(private_notes: pathlib.Path) -> str:
    """現在のHEADを返す。"""
    result = _git_command.run(
        ["rev-parse", "HEAD"],
        cwd=private_notes,
        check=True,
        capture_output=True,
        text=True,
    )
    if not isinstance(result.stdout, str):
        raise RuntimeError("HEADを取得できません")
    return result.stdout.strip()


def dispatch(args, private_notes: pathlib.Path, home: pathlib.Path) -> int:
    """`atk plans`のサブコマンドを実行する。"""
    if args.plans_subcommand == "checkout":
        for path in checkout_plan(private_notes, args.plan_file, home=home):
            print(path.resolve(strict=False))
        return 0
    if args.plans_subcommand == "commit":
        result = commit_plan(private_notes, args.plan_file, home=home, skip_push=args.skip_push)
        action = "commitしました" if args.skip_push else "commit・pushしました"
        print(f"計画bundleを保存rootへ移動して{action}: {result['plan_file']}")
        return 0
    if args.plans_subcommand == "migrate":
        result = migrate_plans(private_notes, home)
        print(f"計画ファイルを移行しました: {result['migrated']}件（旧ファイル削除: {result['deleted']}件）")
        return 0
    raise _common.WebInputError(f"未知のplansサブコマンド: {args.plans_subcommand}")


# テスト・既存呼び出し向けの短い別名。
commit = commit_plan
checkout = checkout_plan
migrate = migrate_plans
