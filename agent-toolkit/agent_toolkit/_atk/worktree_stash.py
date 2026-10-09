"""worktree間で共有される`refs/stash`を安全に退避する補助コマンド。"""

from __future__ import annotations

import argparse
import pathlib
import re
import subprocess

from agent_toolkit._atk import help_text as _atk_help  # pylint: disable=wrong-import-position
from agent_toolkit._atk import outcome as _outcome  # pylint: disable=wrong-import-position
from agent_toolkit._common import file_lock as _file_lock  # pylint: disable=wrong-import-position
from agent_toolkit._git import command as _git_command

_LOCK_NAME = "agent-toolkit-stash.lock"
_STASH_IDENTIFIER_PATTERN = re.compile(r"stash@\{[0-9]+\}\Z")
_QUEUE_REPOSITORY_ERROR = "対象はprivate-notesのため操作を拒否した"
_QUEUE_REPOSITORY_NEXT_ACTION = (
    "変更はatk wi・atk plansのコマンドかatk serveの画面から行い、未コミットのキュー操作はatk wi commitで確定する"
)


def _run_git(args: list[str], cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    """指定worktreeでgitを実行する。"""
    return _git_command.run([*args], cwd, capture_output=True, text=True, check=False)


def _git_output(args: list[str], cwd: pathlib.Path) -> str | None:
    """成功したgitコマンドの標準出力を返し、失敗時はNoneを返す。"""
    result = _run_git(args, cwd)
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None


def _common_dir(cwd: pathlib.Path) -> pathlib.Path | None:
    """worktreeからGit共通ディレクトリを絶対パスへ解決する。"""
    result = _run_git(["rev-parse", "--git-common-dir"], cwd)
    if result.returncode != 0 or not result.stdout.strip():
        _outcome.report_failure(
            f"Git共通ディレクトリを解決できない: {result.stderr.strip()}",
            next_action="Gitの作業ツリーで実行し直す",
        )
        return None
    value = pathlib.Path(result.stdout.strip())
    return value.resolve() if value.is_absolute() else (cwd / value).resolve()


def _is_queue_repository(worktree: pathlib.Path, private_notes: pathlib.Path | None) -> bool:
    """private-notesでは退避を拒否し、並行するキュー操作の喪失を防ぐ。"""
    if private_notes is None or not private_notes.exists():
        return False
    common_dirs: list[pathlib.Path] = []
    for repository in (worktree, private_notes):
        try:
            result = _run_git(["rev-parse", "--git-common-dir"], repository)
            if result.returncode != 0 or not result.stdout.strip():
                return False
            value = pathlib.Path(result.stdout.strip())
            common_dirs.append(value.resolve() if value.is_absolute() else (repository / value).resolve())
        except (OSError, RuntimeError):
            return False
    return common_dirs[0] == common_dirs[1]


def _ref_exists(ref: str, cwd: pathlib.Path) -> bool | None:
    """refの存在を返し、照会失敗時はNoneを返す。"""
    result = _run_git(["show-ref", "--verify", "--quiet", ref], cwd)
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    _outcome.report_failure(
        f"退避refの存在を照会できない: {result.stderr.strip()}",
        next_action="`git status`でGitの状態を確認してから再実行する",
    )
    return None


def _stash_oid(cwd: pathlib.Path) -> str | None:
    """`refs/stash`先頭のOIDを返す。未作成時はNoneを返す。"""
    return _git_output(["rev-parse", "--verify", "refs/stash"], cwd)


def _protection_ref(ref: str, cwd: pathlib.Path, common_dir: pathlib.Path) -> str | None:
    """Gitのworktree識別子とlabelから、全worktreeのGCが参照する共有refを得る。"""
    value = _git_output(["rev-parse", "--absolute-git-dir"], cwd)
    if value is None:
        _outcome.report_failure(f"worktree識別子を解決できない: {ref}", next_action="Gitの状態を確認して再実行する")
        return None
    git_dir = pathlib.Path(value).resolve()
    owner = "main" if git_dir == common_dir else f"linked/{git_dir.name.encode('utf-8').hex()}"
    return f"refs/atk/worktree-stash/{owner}/{ref.removeprefix('refs/worktree/')}"


def _protect_ref(ref: str, shared_ref: str, oid: str, cwd: pathlib.Path) -> int:
    """既存保護を上書きせず、旧値条件付きで共有保護を成立させる。呼出元が固定ロックを保持する。"""
    object_check = _run_git(["cat-file", "-e", f"{oid}^{{commit}}"], cwd)
    if object_check.returncode != 0:
        _outcome.report_failure(
            f"退避オブジェクトを確認できない: {ref}; oid={oid}; {object_check.stderr.strip()}",
            next_action="退避refを削除せず、Gitのオブジェクトを復旧してからprotectを再実行する",
        )
        return 1
    exists = _ref_exists(shared_ref, cwd)
    if exists is None:
        return 1
    if exists:
        if _git_output(["rev-parse", "--verify", shared_ref], cwd) == oid:
            return 0
        _outcome.report_failure(
            f"共有保護が別の退避を指している: {ref}; protection={shared_ref}",
            next_action="両refの退避内容を確認し、復旧してからdropを行う",
        )
        return 1
    result = _run_git(["update-ref", shared_ref, oid, ""], cwd)
    if result.returncode != 0:
        _outcome.report_failure(
            f"共有保護を記録できない: {ref}; protection={shared_ref}; oid={oid}; {result.stderr.strip()}",
            next_action=f"共有stashを保持したまま、`atk worktree-stash protect {ref}`を再実行する",
        )
        return 1
    return 0


def _report_failure(
    message: str,
    *,
    stash_oid: str | None,
    ref: str,
    ref_recorded: bool,
    cwd: pathlib.Path,
) -> None:
    """途中失敗時に退避物と復旧識別子、復元の手順を標準エラーへ記録する。"""
    location = "worktree固有refへ記録済み" if ref_recorded else "共有refs/stashへ保持"
    if stash_oid is None:
        next_action = f"`git -C {cwd} status`と`git -C {cwd} stash list`で変更と退避の残存を確認し、原因を解消して再実行する"
    else:
        restore_source = f"`{stash_oid}`または`{ref}`" if ref_recorded else f"`{stash_oid}`"
        next_action = (
            f"`git -C {cwd} stash list`で共有stashの残存を確認し、"
            f"変更を戻す場合は`git -C {cwd} stash apply {stash_oid}`で{restore_source}から復元する"
        )
    _outcome.report_failure(
        f"{message}: {location}; stash_oid={stash_oid or '(なし)'}; ref={ref}; cwd={cwd}",
        next_action=next_action,
    )


def _worktree_ref(label: str, cwd: pathlib.Path) -> str | None:
    """有効な退避ラベルからworktree固有refを返す。"""
    ref = f"refs/worktree/{label}"
    check = _run_git(["check-ref-format", ref], cwd)
    if check.returncode == 0:
        return ref
    _outcome.report_failure(f"退避ラベルが不正である: {label}", next_action="Gitのref名として有効なラベルを指定し直す")
    return None


def save(
    label: str,
    *,
    cwd: pathlib.Path | None = None,
    private_notes: pathlib.Path | None = None,
) -> int:
    """現在worktreeの変更を`refs/worktree/<label>`へ退避する。"""
    worktree = (cwd or pathlib.Path.cwd()).resolve()
    if _is_queue_repository(worktree, private_notes):
        _outcome.report_failure(_QUEUE_REPOSITORY_ERROR, next_action=_QUEUE_REPOSITORY_NEXT_ACTION)
        return 2
    ref = _worktree_ref(label, worktree)
    if ref is None:
        return 2
    common_dir = _common_dir(worktree)
    if common_dir is None:
        return 1
    shared_ref = _protection_ref(ref, worktree, common_dir)
    if shared_ref is None:
        return 1
    lock_path = common_dir / _LOCK_NAME
    try:
        with lock_path.open("a+", encoding="utf-8") as lock_file:
            _file_lock.acquire_lock(lock_file)
            try:
                existing = _ref_exists(ref, worktree)
                if existing is None:
                    return 1
                if existing:
                    _outcome.report_failure(
                        f"退避refが既に存在する: {ref}",
                        next_action=f"別のラベルを指定するか、`atk worktree-stash drop {ref}`で既存のrefを削除する",
                    )
                    return 2
                shared_exists = _ref_exists(shared_ref, worktree)
                if shared_exists is None:
                    return 1
                if shared_exists:
                    _outcome.report_failure(
                        f"前回の共有保護が残っている: {ref}; protection={shared_ref}",
                        next_action=f"前回の退避を復旧し、`atk worktree-stash drop {ref}`で回収するか別のlabelを指定する",
                    )
                    return 2
                before = _stash_oid(worktree)
                stash_push = _run_git(["stash", "push", "--include-untracked"], worktree)
                if stash_push.returncode != 0:
                    failed_oid = _stash_oid(worktree)
                    _report_failure(
                        f"git stash pushに失敗しました: {stash_push.stderr.strip()}",
                        stash_oid=failed_oid,
                        ref=ref,
                        ref_recorded=False,
                        cwd=worktree,
                    )
                    return 1
                after = _stash_oid(worktree)
                if after is None or after == before:
                    _outcome.report_failure("退避対象の変更が無い", next_action="退避は不要。変更を加えてから退避する")
                    return 2
                update_ref = _run_git(["update-ref", ref, after], worktree)
                if update_ref.returncode != 0:
                    _report_failure(
                        f"worktree固有refの記録に失敗しました: {update_ref.stderr.strip()}",
                        stash_oid=after,
                        ref=ref,
                        ref_recorded=False,
                        cwd=worktree,
                    )
                    return 1
                ref_recorded = True
                if _protect_ref(ref, shared_ref, after, worktree) != 0:
                    _report_failure(
                        "共有保護に失敗したため作成した共有stashを保持します",
                        stash_oid=after,
                        ref=ref,
                        ref_recorded=True,
                        cwd=worktree,
                    )
                    return 1
                drop_result = _run_git(["stash", "drop", "stash@{0}"], worktree)
                if drop_result.returncode != 0:
                    _report_failure(
                        f"作成した共有stashのdropに失敗しました: {drop_result.stderr.strip()}",
                        stash_oid=after,
                        ref=ref,
                        ref_recorded=ref_recorded,
                        cwd=worktree,
                    )
                    return 1
                _outcome.report_success(f"現在worktreeの変更を退避した: {ref}", _outcome.ResultKind.VALUE_OUTPUT)
                print(ref)
                return 0
            finally:
                _file_lock.release_lock(lock_file)
    except OSError as error:
        _report_failure(
            f"退避用ロックを取得できません: {error}",
            stash_oid=None,
            ref=ref,
            ref_recorded=False,
            cwd=worktree,
        )
        return 1


def protect(
    identifier: str,
    *,
    cwd: pathlib.Path | None = None,
    private_notes: pathlib.Path | None = None,
) -> int:
    """正常な旧worktree固有refを、復元識別子を変えず共有GCから保護する。"""
    worktree = (cwd or pathlib.Path.cwd()).resolve()
    if _is_queue_repository(worktree, private_notes):
        _outcome.report_failure(_QUEUE_REPOSITORY_ERROR, next_action=_QUEUE_REPOSITORY_NEXT_ACTION)
        return 2
    if (
        not identifier.startswith("refs/worktree/")
        or _worktree_ref(identifier.removeprefix("refs/worktree/"), worktree) is None
    ):
        _outcome.report_failure(f"保護するrefが不正である: {identifier}", next_action="refs/worktree/配下のrefを指定する")
        return 2
    common_dir = _common_dir(worktree)
    if common_dir is None:
        return 1
    shared_ref = _protection_ref(identifier, worktree, common_dir)
    if shared_ref is None:
        return 1
    try:
        with (common_dir / _LOCK_NAME).open("a+", encoding="utf-8") as lock_file:
            _file_lock.acquire_lock(lock_file)
            try:
                exists = _ref_exists(identifier, worktree)
                if exists is None:
                    return 1
                if not exists:
                    _outcome.report_failure(
                        f"保護する退避refが存在しない: {identifier}",
                        next_action="実在するrefs/worktree/配下のrefを確認して指定し直す",
                    )
                    return 2
                oid = _git_output(["rev-parse", "--verify", identifier], worktree)
                if oid is None:
                    _outcome.report_failure(
                        f"保護する退避refを照会できない: {identifier}",
                        next_action="refを削除せずGitの状態を確認して再実行する",
                    )
                    return 1
                if _protect_ref(identifier, shared_ref, oid, worktree) != 0:
                    return 1
                _outcome.report_success(f"退避を保護した: {identifier}", _outcome.ResultKind.VALUE_OUTPUT)
                print(identifier)
                return 0
            finally:
                _file_lock.release_lock(lock_file)
    except OSError as error:
        _outcome.report_failure(f"退避用ロックを取得できない: {error}", next_action="原因を解消してprotectを再実行する")
        return 1


def _drop_ref(identifier: str, shared_ref: str, cwd: pathlib.Path) -> int:
    """固有refを先に削除し、共有保護だけが残る状態も同じ識別子で回収する。"""
    refs: list[tuple[str, str]] = []
    for ref in (identifier, shared_ref):
        exists = _ref_exists(ref, cwd)
        if exists is None:
            return 1
        if exists:
            oid = _git_output(["rev-parse", "--verify", ref], cwd)
            if oid is None:
                _outcome.report_failure(f"退避refを照会できない: {ref}", next_action="Gitの状態を確認してdropを再実行する")
                return 1
            refs.append((ref, oid))
    if not refs:
        _outcome.report_failure(f"退避識別子が存在しない: {identifier}", next_action="実在する識別子を確認して指定し直す")
        return 2
    if len(refs) == 2 and refs[0][1] != refs[1][1]:
        _outcome.report_failure(
            f"固有refと共有保護が別の退避を指している: {identifier}; protection={shared_ref}",
            next_action="両退避の内容を復旧してから回収する",
        )
        return 1
    for ref, oid in refs:
        result = _run_git(["update-ref", "-d", ref, oid], cwd)
        if result.returncode != 0:
            _outcome.report_failure(
                f"退避refを削除できない: {ref}; identifier={identifier}; protection={shared_ref}; {result.stderr.strip()}",
                next_action=f"残る退避を保持し、`atk worktree-stash drop {identifier}`で回収を再試行する",
            )
            return 1
    return 0


def drop(
    identifier: str,
    *,
    cwd: pathlib.Path | None = None,
    private_notes: pathlib.Path | None = None,
) -> int:
    """固定ロック下で退避識別子が現在指すOIDを解決して削除する。

    worktree固有refは解決したOIDを条件に`git update-ref -d`で削除し、共有stashは`git stash drop`で削除する。
    """
    worktree = (cwd or pathlib.Path.cwd()).resolve()
    if _is_queue_repository(worktree, private_notes):
        _outcome.report_failure(_QUEUE_REPOSITORY_ERROR, next_action=_QUEUE_REPOSITORY_NEXT_ACTION)
        return 2
    if identifier.startswith("refs/worktree/"):
        check = _run_git(["check-ref-format", identifier], worktree)
        is_worktree_ref = check.returncode == 0
    else:
        is_worktree_ref = False
    if not is_worktree_ref and _STASH_IDENTIFIER_PATTERN.fullmatch(identifier) is None:
        _outcome.report_failure(
            f"退避識別子が不正である: {identifier}",
            next_action="refs/worktree/配下のrefまたはstash@{N}形式を指定し直す",
        )
        return 2
    common_dir = _common_dir(worktree)
    if common_dir is None:
        return 1
    lock_path = common_dir / _LOCK_NAME
    try:
        with lock_path.open("a+", encoding="utf-8") as lock_file:
            _file_lock.acquire_lock(lock_file)
            try:
                if is_worktree_ref:
                    shared_ref = _protection_ref(identifier, worktree, common_dir)
                    if shared_ref is None:
                        return 1
                    result = _drop_ref(identifier, shared_ref, worktree)
                    if result != 0:
                        return result
                else:
                    oid = _git_output(["rev-parse", "--verify", identifier], worktree)
                    if oid is None:
                        _outcome.report_failure(f"退避識別子が存在しない: {identifier}", next_action="git stash listで確認する")
                        return 2
                    deleted = _run_git(["stash", "drop", identifier], worktree)
                    if deleted.returncode != 0:
                        _outcome.report_failure(
                            f"退避識別子を削除できない: {deleted.stderr.strip()}", next_action="Gitの状態を確認して再実行する"
                        )
                        return 1
                _outcome.report_success(f"退避を削除した: {identifier}", _outcome.ResultKind.VALUE_OUTPUT)
                print(identifier)
                return 0
            finally:
                _file_lock.release_lock(lock_file)
    except OSError as error:
        _outcome.report_failure(f"退避用ロックを取得できない: {error}", next_action="ロックを保持する処理の終了後に再実行する")
        return 1


def build_parser(parser: argparse.ArgumentParser, *, command_dest: str = "command") -> None:
    """worktree退避サブコマンドを登録する。"""
    subparsers = _atk_help.add_subcommands(parser, dest=command_dest)
    save_parser = _atk_help.add_command(subparsers, "save", **_atk_help.HELP["atk worktree-stash save"])
    save_parser.add_argument("--label", required=True, help="退避先refのラベル")
    protect_parser = _atk_help.add_command(subparsers, "protect", **_atk_help.HELP["atk worktree-stash protect"])
    protect_parser.add_argument("identifier", help="保護するworktree固有refの識別子")
    drop_parser = _atk_help.add_command(subparsers, "drop", **_atk_help.HELP["atk worktree-stash drop"])
    drop_parser.add_argument("identifier", help="削除するstashまたはworktree固有refの識別子")


def dispatch(
    args: argparse.Namespace,
    *,
    command_dest: str = "command",
    private_notes: pathlib.Path | None = None,
) -> int:
    """解析済み引数に対応する退避操作を実行する。"""
    command = getattr(args, command_dest)
    if command == "save":
        return save(args.label, private_notes=private_notes)
    if command == "drop":
        return drop(args.identifier, private_notes=private_notes)
    if command == "protect":
        return protect(args.identifier, private_notes=private_notes)
    return 2


def main(argv: list[str] | None = None) -> int:
    """CLI引数を解釈してworktreeの変更を退避する。"""
    parser = argparse.ArgumentParser(description=__doc__)
    build_parser(parser)
    return dispatch(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
