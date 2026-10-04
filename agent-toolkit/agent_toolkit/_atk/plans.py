"""計画ファイルとCI対応レビュー指摘管理表のcheckout・commitを提供するCLI補助。

checkout記録は取得からcommit成功まで保持し、取得元と取得時点の内容を更新時の
競合検出に使う。commitまたはpush失敗後の再実行と、作業バンドルを削除して取得を
取り消した後の記録回収も、同じ`atk plans commit`で処理する。
"""

from __future__ import annotations

import contextlib
import datetime
import json
import os
import pathlib
import re
import subprocess
import tempfile
from collections.abc import Iterable

import platformdirs

from agent_toolkit._atk import git_sync as _atk_git_sync
from agent_toolkit._atk import help_text as _atk_help
from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._atk.wi import common as _common
from agent_toolkit._atk.wi import frontmatter as _frontmatter
from agent_toolkit._git import command as _git_command
from agent_toolkit._plan import locations as _plan_file
from agent_toolkit._plan import structure as _plan_format

_CURRENT_ATTACHMENT_SUFFIXES = (".bugs.md", ".exec-review.tsv")
_CI_REVIEW_DIRECTORY = pathlib.Path("ci")
_CI_REVIEW_NAME_RE = re.compile(r"^ci-[0-9a-f]{7,64}\.exec-review\.tsv$")
_SAVED_BUNDLE_CONFLICT_MESSAGE = "保存先に内容の異なる計画ファイルがあります: {destination}"
_SAVED_BUNDLE_CONFLICT_NEXT_ACTION = (
    "保存済み計画を正とする場合は作業側を退避し、作業側を残す場合は別名の新しい計画として保存してください"
)
_PLAN_PATH_NEXT_ACTION = (
    "`~/.claude/plans`直下のdd-{名称}-{16進数4桁}.md、または`private-notes/plans/`相対のyyyy/MM/dd-{名称}-{16進数4桁}.mdで指定し直す。"
    "作業中の計画は`atk plans list`で確認できる"
)
_UNPUSHED_NEXT_ACTION = "commitはローカルに残っている。`atk wi commit`でpushしてから、同じコマンドを再実行して到達を確認する"
_REPORT_BUG_NEXT_ACTION = "agent-toolkitの不具合としてユーザーへ報告する"
_READBACK_NEXT_ACTION = (
    "保存先のファイルシステムの空き容量と権限を確認してから同じコマンドを再実行する。繰り返す場合はユーザーへ報告する"
)
_CHANGED_DURING_SAVE_NEXT_ACTION = (
    "`private-notes/plans/`への保存とcommitは完了している。"
    "作業ファイルの変更内容を確認し、反映する場合は同じ`atk plans commit`を再実行する"
)


def build_parser(parser) -> None:
    """`atk plans`配下のparserを構築する。"""
    sub = _atk_help.add_subcommands(
        parser,
        dest="plans_subcommand",
        required=False,
        show_help_when_missing=True,
    )
    checkout_parser = _atk_help.add_command(sub, "checkout", **_atk_help.HELP["atk plans checkout"])
    checkout_parser.add_argument(
        "plan_file",
        metavar="PLAN_FILE",
        help="`private-notes/plans/`相対の計画メインファイル、またはCI対応レビュー指摘管理表のパス",
    )
    commit_parser = _atk_help.add_command(sub, "commit", **_atk_help.HELP["atk plans commit"])
    commit_parser.add_argument(
        "plan_file",
        metavar="PLAN_FILE",
        help=(
            "`~/.claude/plans`直下のメイン計画ファイル名（dd-{名称}-{16進数4桁}.md）、"
            "`private-notes/plans/`相対のyyyy/MM/dd-{名称}-{16進数4桁}.md、または"
            "ci-{原因commitの7文字以上の一意な短縮OID}.exec-review.tsv。"
        ),
    )
    commit_parser.add_argument(
        "--skip-push",
        action="store_true",
        help="`private-notes/plans/`へ対象限定commitを作成し、pushは行わない",
    )
    _atk_help.add_command(sub, "list", **_atk_help.HELP["atk plans list"])
    _atk_help.add_command(sub, "rewrite-references", **_atk_help.HELP["atk plans rewrite-references"])


_LOCK_SUFFIX = ".lock"
_PLAN_CREATE_LOCK_NAME = f".agent-toolkit-plan-create{_LOCK_SUFFIX}"
_WORKING_RESIDUE_SUFFIXES = (_LOCK_SUFFIX, ".bak", ".tmp")
"""計画バンドルへ含めず、作業バンドルの回収時に`~/.claude/plans`から取り除く付随ファイルの拡張子。"""


def _excluded_path(path: pathlib.Path) -> bool:
    """計画バンドルから一時ファイルと計画の所有記録を除外する。

    計画の所有記録は`~/.claude/plans`の局所状態であり、private-notesへ保存しない。
    """
    return path.name.endswith((*_WORKING_RESIDUE_SUFFIXES, _plan_file.OWNER_RECORD_SUFFIX))


def _remove_working_residue(working_main: pathlib.Path) -> None:
    """指定計画のstemに対応する作業側の付随ファイルを削除する。

    これらは計画バンドルから除外されて`private-notes/plans/`へ移らないため、作業バンドルの回収と
    同じ時点で取り除く。stemに前方一致する名前だけを対象とするため、計画作成の排他に
    使う`~/.claude/plans`直下の共有ロックは削除しない。
    `.bak`と`.tmp`は現行の書き込み処理が生成する。`.lock`はレビュー指摘管理表の
    ロックを兄弟ファイルとして置いていた旧版の生成物であり、現行版は`~/.claude/plans`の外へ置く。
    """
    prefix = f"{working_main.stem}."
    for path in working_main.parent.iterdir():
        if path.name.startswith(prefix) and path.name.endswith(_WORKING_RESIDUE_SUFFIXES):
            path.unlink()


def _as_relative_notes_path(path: pathlib.Path, private_notes: pathlib.Path) -> str:
    """private-notesからの安全なPOSIX相対パスを返す。"""
    try:
        relative = path.resolve(strict=False).relative_to(private_notes.resolve(strict=False))
    except (OSError, ValueError) as error:
        raise _common.WebInputError(
            f"private-notes外のパスをcommit対象にできません: {path}",
            next_action=f"`private-notes/plans/`の配置を確認し、解消しない場合は{_REPORT_BUG_NEXT_ACTION}",
        ) from error
    if any(part in ("", ".", "..") for part in relative.parts):
        raise _common.WebInputError(
            f"commit対象の相対パスが不正です: {path}",
            next_action=f"`private-notes/plans/`の配置を確認し、解消しない場合は{_REPORT_BUG_NEXT_ACTION}",
        )
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
        raise _common.WebInputError(
            "git ls-files --deletedの出力をbytesとして取得できません", next_action=_REPORT_BUG_NEXT_ACTION
        )
    return {private_notes / pathlib.Path(raw.decode("utf-8")) for raw in result.stdout.split(b"\0") if raw}


def _plan_bundle(private_notes: pathlib.Path, relative_main: pathlib.Path) -> tuple[pathlib.Path, ...]:
    """指定メイン計画に属する実在・削除済みのbundleを返す。"""
    parent = (private_notes / _plan_file.NEW_PLANS_DIRECTORY / relative_main.parent).resolve(strict=False)
    main = parent / relative_main.name
    if not parent.is_relative_to((private_notes / _plan_file.NEW_PLANS_DIRECTORY).resolve(strict=False)):
        raise _common.WebInputError("計画バンドルがplans root外を指しています", next_action=_PLAN_PATH_NEXT_ACTION)
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
        raise _common.WebInputError(
            f"指定したメイン計画または計画バンドルが見つかりません: {relative_main}", next_action=_PLAN_PATH_NEXT_ACTION
        )
    if main not in candidates:
        raise _common.WebInputError(f"指定したメイン計画が見つかりません: {relative_main}", next_action=_PLAN_PATH_NEXT_ACTION)
    return tuple(sorted(candidates))


def _working_plan_bundle(home: pathlib.Path | str | None, relative_main: pathlib.Path) -> tuple[pathlib.Path, ...]:
    """`~/.claude/plans`にある現行形式の計画バンドルを返す。"""
    root = _plan_file.working_plans_root(home).resolve(strict=False)
    parent = (root / relative_main.parent).resolve(strict=False)
    if not parent.is_relative_to(root):
        raise _common.WebInputError("計画作業バンドルが`~/.claude/plans`の外を指しています", next_action=_PLAN_PATH_NEXT_ACTION)
    main = parent / relative_main.name
    if not main.is_file() or main.is_symlink():
        return ()
    stem = main.stem
    candidates = tuple(
        sorted(
            path
            for path in parent.iterdir()
            if (path == main or path.name in {f"{stem}{suffix}" for suffix in _CURRENT_ATTACHMENT_SUFFIXES})
            and path.is_file()
            and not path.is_symlink()
            and not _excluded_path(path)
        )
    )
    return candidates


def _current_bundle_contents(contents: dict[str, bytes], main_name: str) -> dict[str, bytes]:
    """保存済みbundleから現行形式の構成要素だけを返す。"""
    stem = pathlib.Path(main_name).stem
    names = {main_name, *(f"{stem}{suffix}" for suffix in _CURRENT_ATTACHMENT_SUFFIXES)}
    return {name: content for name, content in contents.items() if name in names}


def _saved_plan_bundle(private_notes: pathlib.Path, relative_main: pathlib.Path) -> tuple[pathlib.Path, ...]:
    """`private-notes/plans/`に実在する指定stemの通常ファイルを返す。"""
    root = _plan_file.new_plans_root(private_notes).resolve(strict=False)
    parent = (root / relative_main.parent).resolve(strict=False)
    if not parent.is_relative_to(root):
        raise _common.WebInputError(
            "計画保存バンドルが`private-notes/plans/`の外を指しています", next_action=_PLAN_PATH_NEXT_ACTION
        )
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
        raise _common.WebInputError("git ls-treeの出力をbytesとして取得できません", next_action=_REPORT_BUG_NEXT_ACTION)
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
            raise _common.WebInputError("git showの出力をbytesとして取得できません", next_action=_REPORT_BUG_NEXT_ACTION)
        contents[path.name] = content.stdout
    return contents


def _checkout_record_root(relative_main: pathlib.Path) -> pathlib.Path:
    """指定計画stemのcheckout記録ディレクトリを返す。"""
    state_root = pathlib.Path(platformdirs.user_state_dir("agent-toolkit", appauthor=False))
    return state_root / "plan-checkouts" / relative_main.stem


def _validate_saved_plan_relative_path(plan_file: str) -> pathlib.Path:
    """正規形式または移行済み形式の`private-notes/plans/`相対メイン計画パスを返す。"""
    try:
        return _plan_file.validate_plan_relative_path(plan_file)
    except ValueError as canonical_error:
        try:
            return _plan_file.validate_migrated_plan_relative_path(plan_file)
        except ValueError as migrated_error:
            raise _common.web_input_error_from(canonical_error, next_action=_PLAN_PATH_NEXT_ACTION) from migrated_error


def _validate_working_ci_review_relative_path(review_table: str) -> pathlib.Path:
    """`~/.claude/plans`直下のCI対応レビュー指摘管理表名を返す。"""
    next_action = "ci-{原因commitの7文字以上の一意な短縮OID}.exec-review.tsvの形式で指定し直す"
    if "\0" in review_table or "\\" in review_table or "$(" in review_table:
        raise _common.WebInputError("CI対応レビュー指摘管理表のパスが不正です", next_action=next_action)
    relative = pathlib.Path(review_table)
    if relative.parent != pathlib.Path() or _CI_REVIEW_NAME_RE.fullmatch(relative.name) is None:
        raise _common.WebInputError(
            "CI対応レビュー指摘管理表はci-{原因commitの7文字以上の一意な短縮OID}.exec-review.tsvで指定してください",
            next_action=next_action,
        )
    return relative


def _validate_saved_ci_review_relative_path(review_table: str) -> pathlib.Path:
    """Plans root相対のCI対応レビュー指摘管理表パスを返す。"""
    next_action = "ci/ci-{原因commitの7文字以上の一意な短縮OID}.exec-review.tsvの形式で指定し直す"
    if "\0" in review_table or "\\" in review_table or "$(" in review_table:
        raise _common.WebInputError("CI対応レビュー指摘管理表のパスが不正です", next_action=next_action)
    relative = pathlib.Path(review_table)
    if relative.parent != _CI_REVIEW_DIRECTORY or _CI_REVIEW_NAME_RE.fullmatch(relative.name) is None:
        raise _common.WebInputError(
            "保存済みのCI対応レビュー指摘管理表はci/ci-{原因commitの7文字以上の一意な短縮OID}.exec-review.tsvで指定してください",
            next_action=next_action,
        )
    return relative


def _validate_saved_checkout_relative_path(path: str) -> pathlib.Path:
    """checkout記録が受理する計画またはCI対応レビュー指摘管理表の相対パスを返す。"""
    if path.endswith(".exec-review.tsv"):
        return _validate_saved_ci_review_relative_path(path)
    return _validate_saved_plan_relative_path(path)


def _read_checkout_record(relative_main: pathlib.Path) -> tuple[pathlib.Path, dict[str, bytes]] | None:
    """checkout記録を検証して読み込む。"""
    root = _checkout_record_root(relative_main)
    if not root.exists():
        return None
    # 取得記録は取得時点の内容を持つだけで、作業バンドルそのものではない。壊れた記録を削除しても作業側は失われない。
    next_action = (
        f"`~/.claude/plans`直下の該当計画バンドルを`~/.claude/plans`の外へ退避してから取得記録{root}を削除し、"
        "`atk plans checkout <private-notes/plans/相対パス>`で再取得する"
    )
    if root.is_symlink() or not root.is_dir():
        raise _common.WebInputError(f"計画の取得記録が不正です: {root}", next_action=next_action)
    try:
        raw_metadata = json.loads((root / "meta.json").read_text(encoding="utf-8"))
        recorded_relative = _validate_saved_checkout_relative_path(raw_metadata["relative_main"])
    except (OSError, KeyError, TypeError, _common.WebInputError, json.JSONDecodeError) as error:
        raise _common.WebInputError(f"計画の取得記録を読み取れません: {root}", next_action=next_action) from error
    if recorded_relative.stem != relative_main.stem:
        raise _common.WebInputError(f"計画の取得記録とstemが一致しません: {root}", next_action=next_action)
    files_root = root / "files"
    if files_root.is_symlink() or not files_root.is_dir():
        raise _common.WebInputError(f"計画の取得記録にファイルsnapshotがありません: {root}", next_action=next_action)
    snapshots: dict[str, bytes] = {}
    for path in files_root.iterdir():
        if path.is_symlink() or not path.is_file() or _excluded_path(path):
            raise _common.WebInputError(f"計画の取得記録に不正なファイルがあります: {path}", next_action=next_action)
        snapshots[path.name] = path.read_bytes()
    if recorded_relative.name not in snapshots:
        raise _common.WebInputError(f"計画の取得記録にメイン計画がありません: {root}", next_action=next_action)
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


def _write_checkout_record(
    relative_main: pathlib.Path,
    snapshots: dict[str, bytes],
    *,
    duplicate_error: _common.WebInputError,
) -> None:
    """取得元と取得時点のbytesをcheckout記録へ排他的に保存する。"""
    root = _checkout_record_root(relative_main)
    try:
        root.mkdir(parents=True, exist_ok=False)
    except FileExistsError as error:
        raise duplicate_error from error
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
    """保存済み計画バンドルを`~/.claude/plans`へ取得し、取得時点と所有セッションを記録する。"""
    if plan_file.endswith(".exec-review.tsv"):
        return checkout_ci_review(private_notes, plan_file, home=home)
    relative_main = _validate_saved_plan_relative_path(plan_file)
    working_root = _plan_file.working_plans_root(home)
    working_main = working_root / relative_main.name
    duplicate_error = _common.WebInputError(
        f"同じ計画を取得済みです: {relative_main}",
        next_action=(
            "`~/.claude/plans`直下にその計画バンドルがある場合は、それが取得結果のため再取得は不要です。"
            "`~/.claude/plans`直下にその計画バンドルが無い場合は、"
            f"`atk plans commit {working_main.name}`で取得記録を回収してください。"
        ),
    )
    with _atk_git_sync.repo_lock(private_notes):
        if _checkout_record_root(relative_main).exists():
            raise duplicate_error
        if _atk_git_sync.has_remote(private_notes):
            _atk_git_sync.push_pending_commits(private_notes)
            _atk_git_sync.pull(private_notes)
        saved_bundle = _saved_plan_bundle(private_notes, relative_main)
        if not saved_bundle:
            raise _common.WebInputError(
                f"指定したメイン計画が見つかりません: {relative_main}",
                next_action="`private-notes/plans/`相対のyyyy/MM/dd-{名称}-{16進数4桁}.mdを確かめて指定し直す",
            )
        destinations = tuple(working_root / path.name for path in saved_bundle)
        conflicts = tuple(path for path in destinations if path.exists())
        if conflicts:
            raise _common.WebInputError(
                f"`~/.claude/plans`に同名ファイルがあります: {conflicts[0]}",
                next_action="`~/.claude/plans`側のファイルを`~/.claude/plans`の外へ退避してから再実行する",
            )
        snapshots = {path.name: path.read_bytes() for path in saved_bundle}
        working_root.mkdir(parents=True, exist_ok=True)
        copied: list[pathlib.Path] = []
        try:
            for destination in destinations:
                descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as output:
                    output.write(snapshots[destination.name])
                copied.append(destination)
            _write_checkout_record(relative_main, snapshots, duplicate_error=duplicate_error)
        except Exception:
            for path in copied:
                path.unlink(missing_ok=True)
            raise
        _plan_file.record_plan_owner(working_main)
        return destinations


def checkout_ci_review(
    private_notes: pathlib.Path,
    review_table: str,
    *,
    home: pathlib.Path | str | None = None,
) -> tuple[pathlib.Path, ...]:
    """保存済みのCI対応レビュー指摘管理表を`~/.claude/plans`へ取得する。"""
    relative = _validate_saved_ci_review_relative_path(review_table)
    working = _plan_file.working_plans_root(home) / relative.name
    duplicate_error = _common.WebInputError(
        f"同じCI対応レビュー指摘管理表を取得済みです: {relative}",
        next_action=(
            "`~/.claude/plans`直下にその表がある場合は、それが取得結果のため再取得は不要です。"
            f"`~/.claude/plans`直下にその表が無い場合は、`atk plans commit {working.name}`で取得記録を回収してください。"
        ),
    )
    with _atk_git_sync.repo_lock(private_notes):
        if _checkout_record_root(relative).exists():
            raise duplicate_error
        if _atk_git_sync.has_remote(private_notes):
            _atk_git_sync.push_pending_commits(private_notes)
            _atk_git_sync.pull(private_notes)
        saved = _plan_file.new_plans_root(private_notes) / relative
        if saved.is_symlink() or not saved.is_file():
            raise _common.WebInputError(
                f"指定したCI対応レビュー指摘管理表が見つかりません: {relative}",
                next_action="`private-notes/plans/`相対のci/ci-{短縮OID}.exec-review.tsvを確かめて指定し直す",
            )
        if working.exists():
            raise _common.WebInputError(
                f"`~/.claude/plans`に同名ファイルがあります: {working}",
                next_action="`~/.claude/plans`側のファイルを`~/.claude/plans`の外へ退避してから再実行する",
            )
        content = saved.read_bytes()
        working.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(working, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                output.write(content)
            _write_checkout_record(relative, {relative.name: content}, duplicate_error=duplicate_error)
        except Exception:
            working.unlink(missing_ok=True)
            raise
        return (working,)


def _copy_working_bundle(
    private_notes: pathlib.Path,
    relative_main: pathlib.Path,
    working_bundle: tuple[pathlib.Path, ...],
) -> tuple[tuple[pathlib.Path, ...], dict[pathlib.Path, tuple[tuple[int, int], bytes]]]:
    """作業バンドルを`private-notes/plans/`へ排他的に複製し、回収用snapshotを返す。"""
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
                raise _common.WebInputError(
                    _SAVED_BUNDLE_CONFLICT_MESSAGE.format(destination=destination),
                    next_action=_SAVED_BUNDLE_CONFLICT_NEXT_ACTION,
                )
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
                    raise _common.WebInputError(
                        _SAVED_BUNDLE_CONFLICT_MESSAGE.format(destination=destination),
                        next_action=_SAVED_BUNDLE_CONFLICT_NEXT_ACTION,
                    ) from None
            if destination.read_bytes() != content:
                raise _common.WebInputError(
                    f"保存した計画ファイルの読戻し内容が一致しません: {destination}",
                    next_action=_READBACK_NEXT_ACTION,
                )
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
            raise _common.WebInputError(
                f"保存処理中に作業ファイルが変更されたため回収しません: {source}", next_action=_CHANGED_DURING_SAVE_NEXT_ACTION
            )
    destination_by_name = {path.name: path for path in destinations}
    ordered_sources = sorted(working_bundle, key=lambda path: (path.name == relative_main.name, path.name))
    for source in ordered_sources:
        _finalize_plan_file(source, destination_by_name[source.name], snapshots[source][1])
    _remove_working_residue(working_bundle[0].parent / relative_main.name)
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
    """回収前に作業バンドルが一致するかを確かめるためのsnapshotを返す。"""
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
            raise _common.WebInputError(
                f"保存処理中に作業ファイルが変更されたため回収しません: {source}", next_action=_CHANGED_DURING_SAVE_NEXT_ACTION
            )
    for source in sorted(working_bundle, key=lambda path: (path.name == relative_main.name, path.name)):
        source.unlink()
    _remove_working_residue(working_bundle[0].parent / relative_main.name)


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
            raise _common.WebInputError(
                f"保存した計画ファイルの読戻し内容が一致しません: {destination}", next_action=_READBACK_NEXT_ACTION
            )
    for name, path in saved_by_name.items():
        if name not in working_contents:
            path.unlink()
    return tuple(sorted(destination_directory / name for name in working_contents))


def _finalize_plan_file(source: pathlib.Path, destination: pathlib.Path, content: bytes) -> None:
    """commit済みの移し先を、移し元の内容と日時を保って確定する。

    移し元の内容が確認時の`content`と一致する場合だけ`os.replace`でinodeごと移す。
    確認から読取までの間に作業ファイルが書き換わった場合は、作業ファイルを移さずに失敗させる。

    `atk serve`の計画ファイル画面は作成日時を計画の並び順へ使う。Linuxには作成日時を設定するAPIが
    無いため、新規ファイルによる確定ではなく移し元のinodeを`os.replace`で移す。
    """
    if source.read_bytes() != content:
        raise _common.WebInputError(
            f"保存処理中に作業ファイルが変更されたため回収しません: {source}", next_action=_CHANGED_DURING_SAVE_NEXT_ACTION
        )
    os.replace(source, destination)


def _no_remote_next_action(private_notes: pathlib.Path) -> str:
    """Remoteを持たないprivate-notesで同期を要する操作を拒否したときの次の操作を返す。"""
    return f"`git -C {private_notes} remote -v`でremoteを確認し、remoteとupstreamを設定してから再実行する"


def _dirty_next_action(private_notes: pathlib.Path) -> str:
    """Private-notesがcleanでないため操作を開始できないときの次の操作を返す。"""
    return f"`git -C {private_notes} status`で確認し、`atk wi commit`で確定してから再実行する"


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


def _checkout_conflict_differences(
    recorded: dict[str, bytes],
    working: dict[str, bytes],
    saved: dict[str, bytes],
    remote: dict[str, bytes],
) -> str:
    """取得時点・作業側の双方と異なる保存元を、相違の別とともに返す。"""
    differences: list[str] = []
    for name in sorted(recorded.keys() | working.keys() | saved.keys() | remote.keys()):
        if saved.get(name) not in (recorded.get(name), working.get(name)):
            differences.append(f"{name}（保存元が取得時点とも作業側とも異なる）")
        if remote.get(name) not in (recorded.get(name), working.get(name)):
            differences.append(f"{name}（保存元のremoteが取得時点とも作業側とも異なる）")
    return "、".join(differences)


def commit_plan(
    private_notes: pathlib.Path,
    plan_file: str,
    *,
    home: pathlib.Path | str | None = None,
    lock_timeout: float = -1,
    skip_push: bool = False,
) -> dict[str, object]:
    """指定計画bundleを`private-notes/plans/`へ移し、対象限定commitを作成する。

    作業バンドルを回収する時点でその計画の所有記録も回収し、`~/.claude/plans`へ記録だけが残らないようにする。
    """
    if plan_file.endswith(".exec-review.tsv"):
        return commit_ci_review(
            private_notes,
            plan_file,
            home=home,
            lock_timeout=lock_timeout,
            skip_push=skip_push,
        )
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
                raise _common.web_input_error_from(working_error, next_action=_PLAN_PATH_NEXT_ACTION) from saved_error
    requested_relative = working_relative or relative_main
    checkout_record = _read_checkout_record(requested_relative)
    recorded_contents: dict[str, bytes] = {}
    if checkout_record is not None:
        relative_main, recorded_contents = checkout_record
        working_lookup_relative = pathlib.Path(relative_main.name)
    else:
        working_lookup_relative = requested_relative
    working_bundle = _working_plan_bundle(home, working_lookup_relative)
    working_main = _plan_file.working_plans_root(home) / working_lookup_relative
    if checkout_record is None and working_relative is None and not working_bundle:
        working_root = _plan_file.working_plans_root(home)
        residue = (
            tuple(
                sorted(
                    path
                    for path in working_root.iterdir()
                    if (path.name == relative_main.name or path.name.startswith(f"{relative_main.stem}."))
                    and path.is_file()
                    and not path.is_symlink()
                    and not _excluded_path(path)
                )
            )
            if working_root.is_dir()
            else ()
        )
        if residue:
            names = "、".join(path.name for path in residue)
            raise _common.WebInputError(
                f"`~/.claude/plans`直下に保存済み計画バンドルと同じstemのファイルが残っています: {names}。"
                "保存先へ反映していないため、この状態では保存を完了できません。",
                next_action=(
                    f"`~/.claude/plans`直下の{names}を`~/.claude/plans`の外へ退避し、保存済み計画を正とするか、"
                    "退避した内容を別名の新しい計画として保存してください。"
                ),
            )
    if checkout_record is not None:
        if not working_bundle:
            _remove_checkout_record(requested_relative)
            _plan_file.remove_owner_record(working_main)
            return {"plan_file": relative_main.as_posix(), "paths": (), "message": ""}
    elif working_relative is not None and not working_bundle:
        raise _common.WebInputError(
            f"指定した作業中の計画バンドルが見つかりません: {working_relative}",
            next_action=(
                "保存済みの場合は`atk plans checkout <private-notes/plans/相対パス>`で取得してください。"
                "作業中の計画は`atk plans list`で確認できる"
            ),
        )
    if checkout_record is None and working_relative is not None and working_bundle:
        working_main = _plan_file.working_plans_root(home) / working_relative
        date = _birth_date(working_main).split("/")
        relative_main = pathlib.Path(date[0], date[1], working_relative.name)
    with _atk_git_sync.repo_lock(private_notes, timeout=lock_timeout):
        saved_main = _plan_file.new_plans_root(private_notes) / relative_main
        saved_bundle_exists = saved_main.is_file()
        if checkout_record is not None and _atk_git_sync.has_remote(private_notes):
            _git_command.run_quiet(["fetch"], cwd=private_notes)
        elif not working_bundle or not saved_bundle_exists:
            if not skip_push:
                _atk_git_sync.push_pending_commits(private_notes)
            _atk_git_sync.pull(private_notes)
        snapshots: dict[pathlib.Path, tuple[tuple[int, int], bytes]] = {}
        if checkout_record is not None:
            saved_bundle = _saved_plan_bundle(private_notes, relative_main)
            if not saved_bundle:
                raise _common.WebInputError(
                    f"取得元の計画バンドルが見つかりません: {relative_main}",
                    next_action=(
                        f"`git -C {private_notes} log -- {_plan_file.NEW_PLANS_DIRECTORY}/{relative_main.as_posix()}`で"
                        "保存元の削除・移動を確認する。作業側の内容を残す場合は別名の新しい計画として保存する"
                    ),
                )
            saved_contents = _bundle_contents(saved_bundle)
            working_contents = _bundle_contents(working_bundle)
            remote_contents = (
                _bundle_contents_at_ref(private_notes, relative_main, "@{u}")
                if _atk_git_sync.has_remote(private_notes)
                else saved_contents
            )
            recorded_current = _current_bundle_contents(recorded_contents, relative_main.name)
            saved_current = _current_bundle_contents(saved_contents, relative_main.name)
            remote_current = _current_bundle_contents(remote_contents, relative_main.name)
            if saved_current not in (recorded_current, working_contents) or remote_current not in (
                recorded_current,
                working_contents,
            ):
                differences = _checkout_conflict_differences(
                    recorded_current,
                    working_contents,
                    saved_current,
                    remote_current,
                )
                bundle_names = "、".join(sorted(working_contents))
                raise _common.WebInputError(
                    f"取得後に保存元の計画バンドルが変更されています: {relative_main}。"
                    "取得時点・作業側・保存元の内容が一致しないため、どれを正とするかが確定するまで"
                    f"保存も取得もできません。相違した対象は{differences}です。",
                    next_action=(
                        "次の順に実行してください。"
                        f"`~/.claude/plans`直下の{bundle_names}を`~/.claude/plans`の外へ退避します。"
                        f"`atk plans commit {working_main.name}`を実行すると、"
                        "作業バンドルが不在のため取得記録だけを回収します。"
                        "保存済み計画を確認し、退避した内容を残す場合は別名の新しい計画として保存します。"
                    ),
                )
            snapshots = _working_snapshots(working_bundle)
            if saved_current == recorded_current:
                legacy_contents = {name: content for name, content in saved_contents.items() if name not in saved_current}
                _update_saved_bundle(saved_main.parent, saved_bundle, legacy_contents | working_contents)
            bundle = _plan_bundle(private_notes, relative_main)
        elif working_bundle:
            bundle, snapshots = _copy_working_bundle(private_notes, relative_main, working_bundle)
        else:
            bundle = _plan_bundle(private_notes, relative_main)
        relative_paths = tuple(_as_relative_notes_path(path, private_notes) for path in bundle)
        message = f"chore: update plan {_plan_display_name(relative_main)}"
        _atk_git_sync.commit_and_push(private_notes, message, relative_paths, skip_push=skip_push)
        if not skip_push and _atk_git_sync.has_remote(private_notes) and not _atk_git_sync.remote_contains_head(private_notes):
            raise _common.WebInputError(
                "計画commitがremote branchへ到達したことを確認できません", next_action=_UNPUSHED_NEXT_ACTION
            )
        if checkout_record is not None:
            _remove_checked_out_working_bundle(working_bundle, snapshots, requested_relative)
            _remove_checkout_record(requested_relative)
            _plan_file.remove_owner_record(working_main)
        elif working_bundle:
            _remove_finalized_working_bundle(working_bundle, snapshots, bundle, relative_main, home)
            _plan_file.remove_owner_record(working_main)
    return {"plan_file": relative_main.as_posix(), "paths": relative_paths, "message": message}


def commit_ci_review(
    private_notes: pathlib.Path,
    review_table: str,
    *,
    home: pathlib.Path | str | None = None,
    lock_timeout: float = -1,
    skip_push: bool = False,
) -> dict[str, object]:
    """CI対応レビュー指摘管理表を対象限定でcommitし、成功後に作業側を回収する。"""
    try:
        working_relative = _validate_working_ci_review_relative_path(review_table)
        requested_relative = working_relative
    except _common.WebInputError as working_error:
        try:
            saved_relative = _validate_saved_ci_review_relative_path(review_table)
        except _common.WebInputError as saved_error:
            raise working_error from saved_error
        requested_relative = saved_relative
        working_relative = pathlib.Path(saved_relative.name)
    checkout_record = _read_checkout_record(requested_relative)
    recorded_contents: dict[str, bytes] = {}
    relative = _CI_REVIEW_DIRECTORY / working_relative.name
    if checkout_record is not None:
        relative, recorded_contents = checkout_record
    working = _plan_file.working_plans_root(home) / working_relative.name
    if checkout_record is not None and not working.exists():
        _remove_checkout_record(requested_relative)
        return {"plan_file": relative.as_posix(), "paths": (), "message": "", "kind": "ci-review"}
    if working.is_symlink() or not working.is_file():
        raise _common.WebInputError(
            f"指定したCI対応レビュー指摘管理表が見つかりません: {working_relative}",
            next_action=(
                "`~/.claude/plans`直下の表の名前を確かめて指定し直す。"
                "保存済みの表は`atk plans checkout ci/<ファイル名>`で取得してから編集する"
            ),
        )
    snapshots = _working_snapshots((working,))
    working_contents = {working.name: snapshots[working][1]}
    with _atk_git_sync.repo_lock(private_notes, timeout=lock_timeout):
        saved = _plan_file.new_plans_root(private_notes) / relative
        if checkout_record is not None and _atk_git_sync.has_remote(private_notes):
            _git_command.run_quiet(["fetch"], cwd=private_notes)
        elif not saved.exists():
            if not skip_push:
                _atk_git_sync.push_pending_commits(private_notes)
            _atk_git_sync.pull(private_notes)
        if saved.exists() and (saved.is_symlink() or not saved.is_file()):
            raise _common.WebInputError(
                f"保存先のCI対応レビュー指摘管理表が通常ファイルではありません: {saved}",
                next_action=f"`git -C {private_notes} status`で保存先の状態を確認し、原因が分からない場合はユーザーへ報告する",
            )
        saved_contents = {saved.name: saved.read_bytes()} if saved.is_file() else {}
        if checkout_record is not None:
            remote_contents = (
                _bundle_contents_at_ref(private_notes, relative, "@{u}")
                if _atk_git_sync.has_remote(private_notes)
                else saved_contents
            )
            if saved_contents not in (recorded_contents, working_contents) or remote_contents not in (
                recorded_contents,
                working_contents,
            ):
                differences = _checkout_conflict_differences(
                    recorded_contents,
                    working_contents,
                    saved_contents,
                    remote_contents,
                )
                raise _common.WebInputError(
                    f"取得後に保存元のCI対応レビュー指摘管理表が変更されています: {relative}。"
                    "取得時点・作業側・保存元の内容が一致しないため、どれを正とするかが確定するまで"
                    f"保存も取得もできません。相違した対象は{differences}です。",
                    next_action=(
                        "次の順に実行してください。"
                        f"`~/.claude/plans`直下の{working.name}を`~/.claude/plans`の外へ退避します。"
                        f"`atk plans commit {working.name}`を実行すると、作業側が不在のため取得記録だけを回収します。"
                        "保存済みの表を確認し、退避した内容を残す場合は別の原因commitに対応する表として保存します。"
                    ),
                )
        elif saved_contents and saved_contents != working_contents:
            raise _common.WebInputError(
                _SAVED_BUNDLE_CONFLICT_MESSAGE.format(destination=saved), next_action=_SAVED_BUNDLE_CONFLICT_NEXT_ACTION
            )
        if saved_contents != working_contents:
            saved.parent.mkdir(parents=True, exist_ok=True)
            if saved_contents:
                saved.write_bytes(working_contents[working.name])
            else:
                descriptor = os.open(saved, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as output:
                    output.write(working_contents[working.name])
        if saved.read_bytes() != working_contents[working.name]:
            raise _common.WebInputError(
                f"保存したCI対応レビュー指摘管理表の読戻し内容が一致しません: {saved}", next_action=_READBACK_NEXT_ACTION
            )
        relative_path = _as_relative_notes_path(saved, private_notes)
        message = f"chore: update CI review {working.stem}"
        _atk_git_sync.commit_and_push(private_notes, message, (relative_path,), skip_push=skip_push)
        if not skip_push and _atk_git_sync.has_remote(private_notes) and not _atk_git_sync.remote_contains_head(private_notes):
            raise _common.WebInputError(
                "CI対応レビュー指摘管理表のcommitがremote branchへ到達したことを確認できません",
                next_action=_UNPUSHED_NEXT_ACTION,
            )
        _remove_checked_out_working_bundle((working,), snapshots, working_relative)
        if checkout_record is not None:
            _remove_checkout_record(requested_relative)
    return {"plan_file": relative.as_posix(), "paths": (relative_path,), "message": message, "kind": "ci-review"}


def _resolve_progress_source(
    private_notes: pathlib.Path,
    plan_file: str,
    *,
    home: pathlib.Path | str | None,
) -> pathlib.Path:
    """進捗ログを読む計画ファイル（メイン）の実体を返す。

    `private-notes/plans/`相対で指定した場合は`private-notes/plans/`の実体を先に探し、無い場合だけ同名の作業側の実体を返す。
    保存済み計画参照の既存の解決規則と同じ順序にそろえる。
    """
    working_root = _plan_file.working_plans_root(home)
    try:
        working_relative = _plan_file.validate_working_plan_relative_path(plan_file)
    except ValueError as working_error:
        try:
            relative_main = _validate_saved_plan_relative_path(plan_file)
        except _common.WebInputError as saved_error:
            raise _common.web_input_error_from(working_error, next_action=_PLAN_PATH_NEXT_ACTION) from saved_error
        candidates = (_plan_file.new_plans_root(private_notes) / relative_main, working_root / relative_main.name)
    else:
        candidates = (working_root / working_relative,)
    for candidate in candidates:
        if candidate.is_file() and not candidate.is_symlink():
            return candidate
    raise _common.WebInputError(f"指定したメイン計画が見つかりません: {plan_file}", next_action=_PLAN_PATH_NEXT_ACTION)


def plan_progress(
    private_notes: pathlib.Path,
    plan_file: str,
    *,
    home: pathlib.Path | str | None = None,
) -> tuple[dict[str, str], ...]:
    """指定した計画ファイル（メイン）の進捗ログの行を出現順に返す。

    対象ファイルを読み取りだけで解析する。進捗行が1件も無い計画では空のtupleを返す。
    再開位置を確定する消費側が同じ入力から同じ値を得るため、行の並びを本文の出現順で保つ。
    """
    main = _resolve_progress_source(private_notes, plan_file, home=home)
    try:
        rows = _plan_format.progress_log_rows(main.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise _common.WebInputError(
            f"進捗ログを読み取れません: {main}: {error}",
            next_action="計画ファイルの`## 進捗ログ`節の表をUTF-8の3列の表へ直してから再実行する",
        ) from error
    return tuple(
        {"datetime": recorded_at, "completed_step": completed_step, "notes": notes}
        for recorded_at, completed_step, notes in rows
    )


def list_working_plans(home: pathlib.Path | str | None = None) -> tuple[dict[str, object], ...]:
    """`~/.claude/plans`の計画ファイル（メイン）を所有セッションと最終更新時刻とともに返す。

    所有の有無で対象を絞らないため、他のセッションが取得した計画と所有記録を持たない計画も返す。
    最終更新時刻はその計画バンドルの構成ファイルの更新時刻の最大値とする。
    一覧の作成前に、`~/.claude/plans`へ残る孤立したsidecarロックを回収する。
    """
    root = _plan_file.working_plans_root(home).resolve(strict=False)
    if not root.is_dir():
        return ()
    _remove_legacy_sidecar_locks(root)
    entries: list[dict[str, object]] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file() or not _plan_file.is_plan_main_file(str(path)):
            continue
        bundle = _working_plan_bundle(home, path.relative_to(root)) or (path,)
        updated = max(member.stat(follow_symlinks=False).st_mtime for member in bundle)
        entries.append(
            {
                "path": str(path),
                "owner_session": _plan_file.read_owner_session_id(path),
                "updated_at": datetime.datetime.fromtimestamp(updated).astimezone().isoformat(),
            }
        )
    return tuple(entries)


def _remove_legacy_sidecar_locks(root: pathlib.Path) -> None:
    """`~/.claude/plans`配下に残る旧版のsidecarロックを削除する。

    レビュー指摘管理表のロックを兄弟ファイルとして置いていた旧版の生成物が対象であり、現行版はロックを
    `~/.claude/plans`の外へ置くため、いずれも読み書きしない。本体の表が実在するかで残置を分けると、表を
    `~/.claude/plans`へ置いたままの計画で回収の契機が永久に訪れないため、本体の有無によらず削除する。
    計画作成の排他に使う共有ロックは残す。削除できないファイルがあっても一覧の出力は続ける。
    """
    for path in root.rglob(f"*{_LOCK_SUFFIX}"):
        if path.name == _PLAN_CREATE_LOCK_NAME or path.is_symlink() or not path.is_file():
            continue
        with contextlib.suppress(OSError):
            path.unlink()


def _birth_date(path: pathlib.Path) -> str:
    """保存先の年月を決める日時を実行ホストのローカル日付へ変換する。"""
    try:
        return _plan_file.file_birth_date(path).strftime("%Y/%m/%d")
    except OSError as error:
        raise _common.WebInputError(str(error), next_action=f"{path}の存在と読み取り権限を確認してから再実行する") from error


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


# 計画のファイル名は空白を含み得るため、参照の終端は空白ではなく付属ファイルの拡張子で判定する。
_PORTABLE_REFERENCE_RE = re.compile(
    re.escape(_plan_file.PORTABLE_PLAN_PREFIX) + r"plans/\d{4}/\d{2}/[^\n`<>\"'|]+?\.(?:md|tsv)"
)


_DETAIL_SUFFIX = ".detail.md"
_NON_COMPONENT_SUFFIXES = (".bugs.md", ".review.md", "-workaround-check.md")


def _saved_plan_texts(private_notes: pathlib.Path) -> dict[pathlib.Path, str]:
    """`private-notes/plans/`の計画ファイル（メイン）と計画ファイル（詳細）の本文を返す。"""
    root = _plan_file.new_plans_root(private_notes)
    texts: dict[pathlib.Path, str] = {}
    if not root.is_dir():
        return texts
    for path in sorted(root.rglob("*.md")):
        if not path.is_file() or path.is_symlink() or path.name.endswith(_NON_COMPONENT_SUFFIXES):
            continue
        try:
            texts[path] = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
    return texts


def _plan_stem(path: pathlib.Path) -> str:
    """計画ファイル（メイン）または計画ファイル（詳細）のパスから計画stemを返す。"""
    name = path.name
    suffix = _DETAIL_SUFFIX if name.endswith(_DETAIL_SUFFIX) else ".md"
    return name[: -len(suffix)]


def _rewritten_plan_text(text: str, stem: str) -> tuple[str, int]:
    """指定した計画のstemで始まる可搬参照を新しい参照値へ書き換えた本文と件数を返す。

    書き換えるのはコードフェンスなどを除いたMarkdown本文の有効行に限る。
    除外領域はユーザー発言の逐語引用と実行したコマンドの記録を含み、そこに現れる表記は参照ではないためである。
    """
    count = 0

    def _replace(match: re.Match[str]) -> str:
        nonlocal count
        name = match.group(0).rsplit("/", 1)[-1]
        if not name.startswith(stem):
            return match.group(0)
        count += 1
        return f"{_plan_file.PLAN_ADJUNCT_REFERENCE_PREFIX}{name}"

    body_linenos = {lineno for lineno, _line in _plan_format.iter_markdown_body_lines(text)}
    lines = text.splitlines(keepends=True)
    rewritten = [
        _PORTABLE_REFERENCE_RE.sub(_replace, line) if index + 1 in body_linenos else line for index, line in enumerate(lines)
    ]
    return "".join(rewritten), count


def rewrite_plan_references(private_notes: pathlib.Path, *, lock_timeout: float = -1) -> dict[str, object]:
    """保存済み計画の可搬表記の付属ファイル参照を`plan-file-standards.md`の表記へそろえる。

    書き換えるのは、ファイル名がその計画のstemで始まる参照だけとする。
    stemが一致しない参照とキュー項目の本文は、参照先の計画が別であるため書き換えない。
    """
    with _common.repo_lock(private_notes, timeout=lock_timeout):
        try:
            _atk_git_sync.ensure_not_rebasing(private_notes)
            if not _atk_git_sync.has_remote(private_notes):
                raise _common.WebInputError(
                    "remoteなしのprivate-notesでは参照表記の書き換えを実行できません",
                    next_action=_no_remote_next_action(private_notes),
                )
            if _atk_git_sync.is_worktree_dirty(private_notes):
                raise _common.WebInputError(
                    "private-notesのindex・worktreeがcleanでないため書き換えを開始できません",
                    next_action=_dirty_next_action(private_notes),
                )
            _atk_git_sync.require_upstream(private_notes)
            _atk_git_sync.pull(private_notes)
            if _atk_git_sync.is_worktree_dirty(private_notes):
                raise _common.WebInputError(
                    "remote同期後のprivate-notesがcleanでないため書き換えを開始できません",
                    next_action=_dirty_next_action(private_notes),
                )
        except (_atk_git_sync.RebaseInProgressError, _atk_git_sync.GitSyncError) as error:
            raise _common.web_input_error_from(error, next_action=_dirty_next_action(private_notes)) from error

        changes: dict[pathlib.Path, str] = {}
        reference_count = 0
        for path, text in _saved_plan_texts(private_notes).items():
            rewritten, count = _rewritten_plan_text(text, _plan_stem(path))
            if count:
                changes[path] = rewritten
                reference_count += count
        if not changes:
            return {"plans": 0, "references": 0, "commit": None}

        start_head = _git_head(private_notes)
        snapshot = _snapshot(changes)
        try:
            for path, content in changes.items():
                path.write_bytes(_frontmatter.normalize_newlines(content).encode("utf-8"))
            relative_paths = tuple(_as_relative_notes_path(path, private_notes) for path in sorted(changes))
            _atk_git_sync.commit_and_push(
                private_notes,
                "chore: rewrite plan attachment references",
                relative_paths,
            )
            if not _atk_git_sync.remote_contains_head(private_notes):
                raise _common.WebInputError(
                    "書き換えcommitがremote branchへ到達したことを確認できません", next_action=_UNPUSHED_NEXT_ACTION
                )
        except (OSError, subprocess.SubprocessError, _common.WebInputError):
            if start_head == _git_head(private_notes):
                _restore_files(snapshot)
            raise
        return {"plans": len(changes), "references": reference_count, "commit": _git_head(private_notes)}


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
        raise _common.WebInputError("HEADを取得できません", next_action=_REPORT_BUG_NEXT_ACTION)
    return result.stdout.strip()


def dispatch(args, private_notes: pathlib.Path, home: pathlib.Path) -> int:
    """`atk plans`のサブコマンドを実行する。"""
    if args.plans_subcommand == "checkout":
        paths = checkout_plan(private_notes, args.plan_file, home=home)
        main = next(path for path in paths if path.name == pathlib.Path(args.plan_file).name)
        _outcome.report_success(f"保存済みバンドルを`~/.claude/plans`へ取得した: {main}")
        return 0
    if args.plans_subcommand == "commit":
        result = commit_plan(private_notes, args.plan_file, home=home, skip_push=args.skip_push)
        action = "commitした" if args.skip_push else "commit・pushした"
        subject = "CI対応レビュー指摘管理表" if result.get("kind") == "ci-review" else "計画bundle"
        _outcome.report_success(f"{subject}を`private-notes/plans/`へ移動して{action}: {result['plan_file']}")
        return 0
    if args.plans_subcommand == "list":
        for entry in list_working_plans(home):
            owner = entry["owner_session"] or "なし"
            print(f"{entry['path']}\t{owner}\t{entry['updated_at']}")
        return 0
    if args.plans_subcommand == "rewrite-references":
        result = rewrite_plan_references(private_notes)
        _outcome.report_success(f"付属ファイル参照を書き換えた: {result['plans']}件（参照: {result['references']}件）")
        return 0
    raise _common.WebInputError(
        f"未知のplansサブコマンド: {args.plans_subcommand}", next_action="`atk plans --help`で受理するサブコマンドを確認する"
    )


# テスト・既存呼び出し向けの短い別名。
commit = commit_plan
checkout = checkout_plan
progress = plan_progress
checkout_review = checkout_ci_review
rewrite_references = rewrite_plan_references
