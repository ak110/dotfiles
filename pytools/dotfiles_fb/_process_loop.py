"""process-loopサブコマンド実装。"""

import argparse
import pathlib
import subprocess
import sys

from pytools.dotfiles_fb._common import _is_tbd_answered
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


def _count_answered_tbd_for_repo(tbd_dir: pathlib.Path, target_repo: str) -> int:
    """frontmatterの`target_repo`が指定値と一致し、回答済みのTBD inboxファイル件数を返す。"""
    if not tbd_dir.exists():
        return 0
    count = 0
    for path in tbd_dir.iterdir():
        if path.suffix != ".md":
            continue
        text = path.read_text(encoding="utf-8")
        if _parse_target_repo(text) != target_repo:
            continue
        if _is_tbd_answered(text):
            count += 1
    return count


def _count_process_targets_for_repo(private_notes: pathlib.Path, target_repo: str) -> int:
    """process-feedbacksスキルが1反復で扱うfeedback件数と回答済みTBD件数の合計を返す。

    process-feedbacksスキル版とCLI版（本サブコマンド）で終了判定条件を揃えるため、
    feedback inboxと回答済みTBD inboxの両方をカウント対象とする。
    """
    feedback_count = _count_feedback_for_repo(private_notes / "feedback" / "inbox", target_repo)
    tbd_count = _count_answered_tbd_for_repo(private_notes / "tbd" / "inbox", target_repo)
    return feedback_count + tbd_count


def _cmd_process_loop(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """process-loopサブコマンド: 対象リポジトリのinboxが0件になるまでclaude /process-feedbacksを繰り返し起動する。

    件数判定には`_resolve_repo_id`で取得した正規化リモートURLを使う。
    件数はfeedback inboxと回答済みTBD inboxの合計とし、process-feedbacksスキル版と揃える。
    claudeへの起動引数には`--target-repo`指定値（未指定時は`git rev-parse --show-toplevel`の値）の
    ローカルパス文字列を渡す。
    """
    # ローカルパスと正規化リモートURLをそれぞれ取得する
    local_path_str = str(_resolve_local_worktree(args.target_repo))
    repo_id = _resolve_repo_id(args.target_repo)

    iteration = 0
    while True:
        remaining = _count_process_targets_for_repo(private_notes, repo_id)
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
