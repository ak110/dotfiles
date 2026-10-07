#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["filelock>=3.30", "platformdirs>=4.0"]
# ///
"""origin/developの変化または前回の未完了時にupdate-dotfilesを起動する。"""

import argparse
import hashlib
import pathlib
import subprocess
import sys

import filelock
import platformdirs

_DOTFILES_ROOT = pathlib.Path(__file__).resolve().parent.parent
_UPSTREAM = "origin/develop"
_UPSTREAM_REF = "refs/heads/develop"
_LOCK_TIMEOUT_SEC = 600


def _state_paths() -> tuple[pathlib.Path, pathlib.Path]:
    """対象リポジトリ固有の未完了状態と排他ロックを返す。"""
    # 絶対パスには区切り文字やドライブ名を含み得るため、ファイル名へ写す前に固定長へ変換する。
    key = hashlib.sha256(str(_DOTFILES_ROOT.resolve()).encode("utf-8")).hexdigest()
    directory = pathlib.Path(platformdirs.user_state_dir("agent-toolkit", appauthor=False)) / "auto-update"
    return directory / f"{key}.pending", directory / f"{key}.lock"


def _run(command: list[str], *, capture_output: bool = True) -> subprocess.CompletedProcess[str]:
    """dotfilesルートで外部コマンドを実行する。"""
    return subprocess.run(
        command,
        cwd=_DOTFILES_ROOT,
        capture_output=capture_output,
        text=True,
        encoding="utf-8",
        check=False,
    )


def _git_output(args: list[str], *, label: str) -> str | None:
    """gitコマンドの標準出力を返し、失敗時は理由を表示する。"""
    result = _run(["git", "-C", str(_DOTFILES_ROOT), *args])
    if result.returncode != 0:
        print(f"{label}の取得に失敗しました: exit {result.returncode}", file=sys.stderr)
        return None
    return result.stdout.strip()


def _update_if_needed(pending_path: pathlib.Path) -> int:
    """取得元を検証したうえで、上流変更または未完了の更新を1回実行する。"""
    branch = _git_output(["rev-parse", "--abbrev-ref", "HEAD"], label="現在branch")
    if branch is None:
        return 1
    if branch != "develop":
        print(f"現在branchがdevelopではありません: {branch}", file=sys.stderr)
        return 1

    upstream = _git_output(
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
        label="upstream",
    )
    if upstream is None:
        return 1
    if upstream != _UPSTREAM:
        print(f"upstreamが{_UPSTREAM}ではありません: {upstream}", file=sys.stderr)
        return 1

    remote_result = _run(["git", "-C", str(_DOTFILES_ROOT), "ls-remote", "--exit-code", "--refs", "origin", _UPSTREAM_REF])
    if remote_result.returncode != 0:
        print(
            f"{_UPSTREAM_REF}の取得に失敗しました: exit {remote_result.returncode}",
            file=sys.stderr,
        )
        return 1
    lines = remote_result.stdout.splitlines()
    if not lines or not lines[0]:
        print(f"{_UPSTREAM_REF}のcommit IDを取得できませんでした", file=sys.stderr)
        return 1
    upstream_commit = lines[0].split("\t", maxsplit=1)[0]

    local_commit = _git_output(["rev-parse", "HEAD"], label="ローカルHEAD")
    if local_commit is None:
        return 1
    try:
        pending_path.stat()
    except FileNotFoundError:
        pending = False
    else:
        pending = True
    if local_commit == upstream_commit and not pending:
        print(f"{_UPSTREAM}に更新はありません: commit {local_commit}")
        return 0

    update_dotfiles = _DOTFILES_ROOT / "bin" / "update-dotfiles"
    if not update_dotfiles.is_file():
        print(f"update-dotfilesが未配置です: {update_dotfiles}", file=sys.stderr)
        return 1
    if local_commit != upstream_commit:
        print(f"{_UPSTREAM}が変化しました: local {local_commit}, upstream {upstream_commit}")
    else:
        print(f"前回の自動更新が未完了のため再試行します: commit {local_commit}")
    try:
        pending_path.touch()
    except OSError as error:
        print(f"自動更新の未完了状態を保存できません: {error}", file=sys.stderr)
        return 1
    result = _run([str(update_dotfiles)], capture_output=False)
    if result.returncode != 0:
        return result.returncode
    try:
        pending_path.unlink()
    except OSError as error:
        print(f"自動更新の未完了状態を解除できません: {error}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    """euryaleの上流変更と未完了の自動更新を直列化して実行する。"""
    parser = argparse.ArgumentParser(description="origin/developの変化または前回の未完了時にdotfilesを更新する")
    parser.parse_args(argv)
    try:
        pending_path, lock_path = _state_paths()
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with filelock.FileLock(str(lock_path), timeout=_LOCK_TIMEOUT_SEC):
            return _update_if_needed(pending_path)
    except filelock.Timeout:
        print("自動更新の排他ロックを取得できません。次回のタイマー起動で再試行します。", file=sys.stderr)
        return 1
    except (OSError, subprocess.SubprocessError) as error:
        print(f"上流更新の確認に失敗しました: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
