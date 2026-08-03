"""対象リポジトリのTBDが全件回答済みへ遷移したことを検出し、通知本文を組み立てる。

PostToolUseフックから毎回呼ばれる。セッション状態へ対象リポジトリごとの未回答件数を記録し、
前回観測値が1件以上かつ今回が0件で、回答済みが1件以上ある場合に限り通知本文を返す。
セッション開始後の初回観測は基準値の記録だけを行い、通知しない。

`git pull`は実行しない。同一ホストのWeb UIおよびCLIからの回答はローカルファイルを
直接更新するため、pullなしで観測できる。他端末からの回答は当該ホストで次にpullが
実行されるまで観測できない。
"""

import subprocess

import _git_remote
import _tbd_scan
from _session_state import update_state

STATE_KEY = "tbd_unanswered_by_repo"
"""対象リポジトリIDごとの直近の未回答TBD件数を保持するセッション状態キー。"""

_GIT_TIMEOUT_SEC = 5.0
"""`git remote get-url origin`の実行上限。フックの滞留を防ぐ。"""


def resolve_target_repo(cwd: str) -> str | None:
    """作業ディレクトリから対象リポジトリIDを解決する。解決できない場合はNoneを返す。

    `_atk_mq_repo._resolve_repo_id`は解決失敗時にプロセスを終了し、
    `_atk_mq_common`経由で依存パッケージも読み込むため使用しない。
    """
    try:
        completed = subprocess.run(
            ["git", "-C", cwd, "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=False,
            timeout=_GIT_TIMEOUT_SEC,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    try:
        return _git_remote.normalize_remote_url(completed.stdout.strip())
    except ValueError:
        return None


def build_notice(session_id: str, cwd: str) -> str | None:
    """全件回答済みへの遷移を検出した場合に通知本文を返す。それ以外はNoneを返す。"""
    root = _tbd_scan.private_notes_root()
    if root is None:
        return None
    target_repo = resolve_target_repo(cwd)
    if target_repo is None:
        return None
    scan = _tbd_scan.scan_active_tbds(root, target_repo)
    if not scan.complete:
        return None
    entries = scan.entries
    unanswered = [entry for entry in entries if not entry.answered]
    answered = [entry for entry in entries if entry.answered]
    outcome: dict[str, bool] = {"notify": False}

    def _record(state: dict) -> dict | None:
        counts = state.get(STATE_KEY)
        if not isinstance(counts, dict):
            counts = {}
        previous = counts.get(target_repo)
        outcome["notify"] = isinstance(previous, int) and previous > 0 and not unanswered and bool(answered)
        if previous == len(unanswered):
            return None
        counts[target_repo] = len(unanswered)
        state[STATE_KEY] = counts
        return state

    state_updated = update_state(session_id, _record)
    if not outcome["notify"] or not state_updated:
        return None
    filenames = ", ".join(entry.filename for entry in answered)
    return (
        f"all TBD entries for repository {target_repo} are now answered"
        f" (unanswered: 0, answered: {len(answered)}). Answered entries: {filenames}."
        " Decide whether to take them into the current session before it ends:"
        " read each entry with `atk mq show <filename>` and follow the recorded answer,"
        " revising any provisional decision that the answer contradicts."
        " If the answers are unrelated to the current task, leave them for the next"
        " `agent-toolkit:process-feedbacks` run and continue."
    )
