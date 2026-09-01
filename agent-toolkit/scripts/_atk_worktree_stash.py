#!/usr/bin/env -S uv run --no-project --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""worktree間で共有される`refs/stash`を安全に退避する補助コマンド。"""

from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys

import _atk_help
import _file_lock

_LOCK_NAME = "agent-toolkit-stash.lock"
_STASH_IDENTIFIER_PATTERN = re.compile(r"stash@\{[0-9]+\}\Z")
_QUEUE_REPOSITORY_ERROR = (
    "操作を拒否しました: 対象はキュー管理リポジトリです。"
    "変更にはatk mq・atk plans・atk serveが提供する経路を使い、"
    "未コミットのキュー操作はatk mq commitで確定してください。"
)


def _run_git(args: list[str], cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    """指定worktreeでgitを実行する。"""
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)


def _git_output(args: list[str], cwd: pathlib.Path) -> str | None:
    """成功したgitコマンドの標準出力を返し、失敗時はNoneを返す。"""
    result = _run_git(args, cwd)
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None


def _common_dir(cwd: pathlib.Path) -> pathlib.Path | None:
    """worktreeからGit共通ディレクトリを絶対パスへ解決する。"""
    result = _run_git(["rev-parse", "--git-common-dir"], cwd)
    if result.returncode != 0 or not result.stdout.strip():
        print(f"Git共通ディレクトリを解決できません: {result.stderr.strip()}", file=sys.stderr)
        return None
    value = pathlib.Path(result.stdout.strip())
    return value.resolve() if value.is_absolute() else (cwd / value).resolve()


def _is_queue_repository(worktree: pathlib.Path, private_notes: pathlib.Path | None) -> bool:
    """キュー管理リポジトリでは退避を拒否し、並行するキュー操作の喪失を防ぐ。"""
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
    print(f"退避refの存在を照会できません: {result.stderr.strip()}", file=sys.stderr)
    return None


def _stash_oid(cwd: pathlib.Path) -> str | None:
    """`refs/stash`先頭のOIDを返す。未作成時はNoneを返す。"""
    return _git_output(["rev-parse", "--verify", "refs/stash"], cwd)


def _report_failure(
    message: str,
    *,
    stash_oid: str | None,
    ref: str,
    ref_recorded: bool,
    cwd: pathlib.Path,
) -> None:
    """途中失敗時に退避物と復旧識別子を標準エラーへ記録する。"""
    location = "worktree固有refへ記録済み" if ref_recorded else "共有refs/stashへ保持"
    print(
        f"{message}: {location}; stash_oid={stash_oid or '(なし)'}; ref={ref}; cwd={cwd}",
        file=sys.stderr,
    )


def _worktree_ref(label: str, cwd: pathlib.Path) -> str | None:
    """有効な退避ラベルからworktree固有refを返す。"""
    ref = f"refs/worktree/{label}"
    check = _run_git(["check-ref-format", ref], cwd)
    if check.returncode == 0:
        return ref
    print(f"退避ラベルが不正です: {label}", file=sys.stderr)
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
        print(_QUEUE_REPOSITORY_ERROR, file=sys.stderr)
        return 2
    ref = _worktree_ref(label, worktree)
    if ref is None:
        return 2
    common_dir = _common_dir(worktree)
    if common_dir is None:
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
                    print(f"退避refが既に存在します: {ref}", file=sys.stderr)
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
                    print("退避対象がありません", file=sys.stderr)
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


def drop(
    identifier: str,
    *,
    cwd: pathlib.Path | None = None,
    private_notes: pathlib.Path | None = None,
) -> int:
    """退避識別子を固定ロック下でOID照合して削除する。"""
    worktree = (cwd or pathlib.Path.cwd()).resolve()
    if _is_queue_repository(worktree, private_notes):
        print(_QUEUE_REPOSITORY_ERROR, file=sys.stderr)
        return 2
    if identifier.startswith("refs/worktree/"):
        check = _run_git(["check-ref-format", identifier], worktree)
        is_worktree_ref = check.returncode == 0
    else:
        is_worktree_ref = False
    if not is_worktree_ref and _STASH_IDENTIFIER_PATTERN.fullmatch(identifier) is None:
        print(f"退避識別子が不正です: {identifier}", file=sys.stderr)
        return 2
    common_dir = _common_dir(worktree)
    if common_dir is None:
        return 1
    lock_path = common_dir / _LOCK_NAME
    try:
        with lock_path.open("a+", encoding="utf-8") as lock_file:
            _file_lock.acquire_lock(lock_file)
            try:
                oid = _git_output(["rev-parse", "--verify", identifier], worktree)
                if oid is None:
                    print(f"退避識別子が存在しません: {identifier}", file=sys.stderr)
                    return 2
                delete_args = ["update-ref", "-d", identifier, oid] if is_worktree_ref else ["stash", "drop", identifier]
                deleted = _run_git(delete_args, worktree)
                if deleted.returncode != 0:
                    print(f"退避識別子を削除できません: {deleted.stderr.strip()}", file=sys.stderr)
                    return 1
                print(identifier)
                return 0
            finally:
                _file_lock.release_lock(lock_file)
    except OSError as error:
        print(f"退避用ロックを取得できません: {error}", file=sys.stderr)
        return 1


def build_parser(parser: argparse.ArgumentParser, *, command_dest: str = "command") -> None:
    """worktree退避サブコマンドを登録する。"""
    subparsers = _atk_help.add_subcommands(parser, dest=command_dest)
    save_parser = _atk_help.add_command(subparsers, "save", **_atk_help.HELP["atk worktree-stash save"])
    save_parser.add_argument("--label", required=True, help="退避先refのラベル")
    drop_parser = _atk_help.add_command(subparsers, "drop", **_atk_help.HELP["atk worktree-stash drop"])
    drop_parser.add_argument("identifier", help="削除するstash又はworktree固有refの識別子")


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
    return 2


def main(argv: list[str] | None = None) -> int:
    """CLI引数を解釈してworktreeの変更を退避する。"""
    parser = argparse.ArgumentParser(description=__doc__)
    build_parser(parser)
    return dispatch(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
