"""計画バンドルの構成要素・保存元の列挙。"""

from __future__ import annotations

import pathlib
import re

from agent_toolkit._atk.wi import web_input as _wi_web_input
from agent_toolkit._git import command as _git_command
from agent_toolkit._plan import bundle_kinds as _bundle_kinds
from agent_toolkit._plan import locations as _plan_file

CI_REVIEW_DIRECTORY = pathlib.Path("ci")

CI_REVIEW_NAME_RE = re.compile(rf"^ci-[0-9a-f]{{7,64}}{re.escape(_bundle_kinds.EXEC_REVIEW.suffix)}$")

SAVED_BUNDLE_CONFLICT_MESSAGE = "保存先に内容の異なる計画ファイルがあります: {destination}"

SAVED_BUNDLE_CONFLICT_NEXT_ACTION = (
    "保存済み計画を正とする場合は作業側を退避し、作業側を残す場合は別名の新しい計画として保存してください"
)

PLAN_PATH_NEXT_ACTION = _plan_file.PLAN_INPUT_NEXT_ACTION

UNPUSHED_NEXT_ACTION = "commitはローカルに残っている。`atk wi commit`でpushしてから、同じコマンドを再実行して到達を確認する"

REPORT_BUG_NEXT_ACTION = "agent-toolkitの不具合としてユーザーへ報告する"

READBACK_NEXT_ACTION = (
    "保存先のファイルシステムの空き容量と権限を確認してから同じコマンドを再実行する。繰り返す場合はユーザーへ報告する"
)

CHANGED_DURING_SAVE_NEXT_ACTION = (
    "`private-notes/plans/`への保存とcommitは完了している。"
    "作業ファイルの変更内容を確認し、反映する場合は同じ`atk plans commit`を再実行する"
)

LOCK_SUFFIX = ".lock"

PLAN_CREATE_LOCK_NAME = f".agent-toolkit-plan-create{LOCK_SUFFIX}"

WORKING_RESIDUE_SUFFIXES = (LOCK_SUFFIX, ".bak", ".tmp")

"""計画バンドルへ含めず、作業バンドルの回収時に`~/.claude/plans`から取り除く付随ファイルの拡張子。"""


def excluded_path(path: pathlib.Path) -> bool:
    """計画バンドルから一時ファイルと局所記録を除外する。

    所有記録と作成日記録は`~/.claude/plans`の局所状態であり、private-notesへ保存しない。
    """
    kind = _bundle_kinds.kind_of_name(path.name)
    return path.name.endswith(WORKING_RESIDUE_SUFFIXES) or (kind is not None and not kind.stored)


def remove_working_residue(working_main: pathlib.Path) -> None:
    """指定計画のstemに対応する作業側の付随ファイルを削除する。

    これらは計画バンドルから除外されて`private-notes/plans/`へ移らないため、作業バンドルの回収と
    同じ時点で取り除く。stemに前方一致する名前だけを対象とするため、計画作成の排他に
    使う`~/.claude/plans`直下の共有ロックは削除しない。
    `.bak`と`.tmp`は現行の書き込み処理が生成する。`.lock`はレビュー指摘管理表の
    ロックを兄弟ファイルとして置いていた旧版の生成物であり、現行版は`~/.claude/plans`の外へ置く。
    """
    prefix = f"{working_main.stem}."
    for path in working_main.parent.iterdir():
        if path.name.startswith(prefix) and path.name.endswith(WORKING_RESIDUE_SUFFIXES):
            path.unlink()


def as_relative_notes_path(path: pathlib.Path, private_notes: pathlib.Path) -> str:
    """private-notesからの安全なPOSIX相対パスを返す。"""
    try:
        relative = path.resolve(strict=False).relative_to(private_notes.resolve(strict=False))
    except (OSError, ValueError) as error:
        raise _wi_web_input.WebInputError(
            f"private-notes外のパスをcommit対象にできません: {path}",
            next_action=f"`private-notes/plans/`の配置を確認し、解消しない場合は{REPORT_BUG_NEXT_ACTION}",
        ) from error
    if any(part in ("", ".", "..") for part in relative.parts):
        raise _wi_web_input.WebInputError(
            f"commit対象の相対パスが不正です: {path}",
            next_action=f"`private-notes/plans/`の配置を確認し、解消しない場合は{REPORT_BUG_NEXT_ACTION}",
        )
    return relative.as_posix()


def tracked_deleted_paths(private_notes: pathlib.Path) -> set[pathlib.Path]:
    """Gitが追跡している削除済みパスを返す。"""
    result = _git_command.run(
        ["ls-files", "--deleted", "-z"],
        cwd=private_notes,
        check=True,
        capture_output=True,
        text=False,
    )
    if not isinstance(result.stdout, bytes):
        raise _wi_web_input.WebInputError(
            "git ls-files --deletedの出力をbytesとして取得できません", next_action=REPORT_BUG_NEXT_ACTION
        )
    return {private_notes / pathlib.Path(raw.decode("utf-8")) for raw in result.stdout.split(b"\0") if raw}


def plan_bundle(private_notes: pathlib.Path, relative_main: pathlib.Path) -> tuple[pathlib.Path, ...]:
    """指定メイン計画に属する実在・削除済みのbundleを返す。"""
    parent = (private_notes / _plan_file.NEW_PLANS_DIRECTORY / relative_main.parent).resolve(strict=False)
    main = parent / relative_main.name
    if not parent.is_relative_to((private_notes / _plan_file.NEW_PLANS_DIRECTORY).resolve(strict=False)):
        raise _wi_web_input.WebInputError("計画バンドルがplans root外を指しています", next_action=PLAN_PATH_NEXT_ACTION)
    stem = main.stem
    candidates: set[pathlib.Path] = set()
    if main.is_file() and not main.is_symlink():
        candidates.add(main)
    if parent.is_dir():
        for path in parent.iterdir():
            if path.name.startswith(f"{stem}.") and path.is_file() and not path.is_symlink() and not excluded_path(path):
                candidates.add(path)
    for path in tracked_deleted_paths(private_notes):
        if path.parent == parent and (path.name == main.name or path.name.startswith(f"{stem}.")) and not excluded_path(path):
            candidates.add(path)
    if not candidates:
        raise _wi_web_input.WebInputError(
            f"指定したメイン計画または計画バンドルが見つかりません: {relative_main}", next_action=PLAN_PATH_NEXT_ACTION
        )
    if main not in candidates:
        raise _wi_web_input.WebInputError(
            f"指定したメイン計画が見つかりません: {relative_main}", next_action=PLAN_PATH_NEXT_ACTION
        )
    return tuple(sorted(candidates))


def working_plan_bundle(home: pathlib.Path | str | None, relative_main: pathlib.Path) -> tuple[pathlib.Path, ...]:
    """`~/.claude/plans`にある現行形式の計画バンドルを返す。"""
    root = _plan_file.working_plans_root(home).resolve(strict=False)
    parent = (root / relative_main.parent).resolve(strict=False)
    if not parent.is_relative_to(root):
        raise _wi_web_input.WebInputError(
            "計画作業バンドルが`~/.claude/plans`の外を指しています", next_action=PLAN_PATH_NEXT_ACTION
        )
    main = parent / relative_main.name
    if not main.is_file() or main.is_symlink():
        return ()
    stem = main.stem
    candidates = tuple(
        sorted(
            path
            for path in parent.iterdir()
            if (path == main or path.name in {kind.name_for(stem) for kind in _bundle_kinds.STORED_ATTACHMENTS})
            and path.is_file()
            and not path.is_symlink()
            and not excluded_path(path)
        )
    )
    return candidates


def current_bundle_contents(contents: dict[str, bytes], main_name: str) -> dict[str, bytes]:
    """保存済みbundleから現行形式の構成要素だけを返す。"""
    stem = pathlib.Path(main_name).stem
    names = {main_name, *(kind.name_for(stem) for kind in _bundle_kinds.STORED_ATTACHMENTS)}
    return {name: content for name, content in contents.items() if name in names}


def saved_plan_bundle(private_notes: pathlib.Path, relative_main: pathlib.Path) -> tuple[pathlib.Path, ...]:
    """`private-notes/plans/`に実在する指定stemの通常ファイルを返す。"""
    root = _plan_file.new_plans_root(private_notes).resolve(strict=False)
    parent = (root / relative_main.parent).resolve(strict=False)
    if not parent.is_relative_to(root):
        raise _wi_web_input.WebInputError(
            "計画保存バンドルが`private-notes/plans/`の外を指しています", next_action=PLAN_PATH_NEXT_ACTION
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
            and not excluded_path(path)
        )
    )


def bundle_contents_at_ref(
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
        raise _wi_web_input.WebInputError("git ls-treeの出力をbytesとして取得できません", next_action=REPORT_BUG_NEXT_ACTION)
    main_path = relative_parent / relative_main.name
    stem = relative_main.stem
    paths = tuple(pathlib.Path(raw.decode("utf-8")) for raw in result.stdout.split(b"\0") if raw)
    bundle = tuple(
        path
        for path in paths
        if path.parent == relative_parent
        and (path == main_path or path.name.startswith(f"{stem}."))
        and not excluded_path(path)
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
            raise _wi_web_input.WebInputError("git showの出力をbytesとして取得できません", next_action=REPORT_BUG_NEXT_ACTION)
        contents[path.name] = content.stdout
    return contents
