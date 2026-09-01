#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""origin/developの変化時だけupdate-dotfilesを起動する。"""

import argparse
import pathlib
import subprocess
import sys

_DOTFILES_ROOT = pathlib.Path(__file__).resolve().parent.parent
_UPSTREAM = "origin/develop"
_UPSTREAM_REF = "refs/heads/develop"


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


def main(argv: list[str] | None = None) -> int:
    """上流commit IDが変化した場合だけupdate-dotfilesを起動する。"""
    parser = argparse.ArgumentParser(description="origin/developの変化時だけdotfilesを更新する")
    parser.parse_args(argv)
    try:
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
        if local_commit == upstream_commit:
            print(f"{_UPSTREAM}に更新はありません: commit {local_commit}")
            return 0

        update_dotfiles = _DOTFILES_ROOT / "bin" / "update-dotfiles"
        if not update_dotfiles.is_file():
            print(f"update-dotfilesが未配置です: {update_dotfiles}", file=sys.stderr)
            return 1
        print(f"{_UPSTREAM}が変化しました: local {local_commit}, upstream {upstream_commit}")
        return _run([str(update_dotfiles)], capture_output=False).returncode
    except (OSError, subprocess.SubprocessError) as error:
        print(f"上流更新の確認に失敗しました: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
