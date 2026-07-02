"""process-loopサブコマンド実装。"""

import argparse
import pathlib
import subprocess
import sys

from pytools.dotfiles_fb._formatters import _parse_target_repo
from pytools.dotfiles_fb._repo import _resolve_local_worktree, _resolve_repo_id


def _count_feedback_for_repo(feedback_dir: pathlib.Path, target_repo: str) -> int:
    """frontmatterの`target_repo`が指定値と一致するinboxファイル件数を返す。"""
    if not feedback_dir.exists():
        return 0
    count = 0
    for path in feedback_dir.iterdir():
        if path.suffix != ".md":
            continue
        if _parse_target_repo(path.read_text(encoding="utf-8")) == target_repo:
            count += 1
    return count


def _cmd_process_loop(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """process-loopサブコマンド: 対象リポジトリのinboxが0件になるまでclaude /process-feedbacksを繰り返し起動する。

    件数判定には`_resolve_repo_id`で取得した正規化リモートURLを使う。
    claudeへの起動引数には`--target-repo`指定値（未指定時は`git rev-parse --show-toplevel`の値）の
    ローカルパス文字列を渡す。
    """
    inbox_dir = private_notes / "feedback" / "inbox"

    # ローカルパスと正規化リモートURLをそれぞれ取得する
    local_path_str = str(_resolve_local_worktree(args.target_repo))
    repo_id = _resolve_repo_id(args.target_repo)

    iteration = 0
    while True:
        remaining = _count_feedback_for_repo(inbox_dir, repo_id)
        if remaining == 0:
            if iteration == 0:
                print(f"対象リポジトリのinboxは空です（target_repo={repo_id}）。処理対象なし。")
            else:
                print(f"対象リポジトリのinboxが空になりました（{iteration}回実行、target_repo={repo_id}）。")
            return
        if args.max_iterations is not None and iteration >= args.max_iterations:
            print(f"反復上限{args.max_iterations}回に達しました（対象リポジトリのinbox残{remaining}件）。")
            return
        iteration += 1
        print(f"[反復 {iteration}] 対象リポジトリのinbox残{remaining}件、claudeを起動します")
        result = subprocess.run(
            ["claude", "--permission-mode=auto", "/process-feedbacks", local_path_str],
            check=False,
        )
        if result.returncode != 0:
            print(
                f"claudeがexit code {result.returncode}で終了しました。反復を中断します。",
                file=sys.stderr,
            )
            sys.exit(result.returncode)
