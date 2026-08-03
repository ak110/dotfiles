"""対象リポジトリのTBDが全件回答済みへ遷移したことを検出し、通知本文を組み立てる。

PostToolUseフックから毎回呼ばれる。セッション状態へ対象リポジトリごとの未回答件数と
active状態ディレクトリの指紋を記録し、前回観測値が1件以上かつ今回が0件で、回答済みが
1件以上ある場合に限り通知本文を返す。セッション開始後の初回観測は基準値の記録だけを
行い、通知しない。

`git pull`は実行しない。同一ホストのWeb UIおよびCLIからの回答はローカルファイルを
直接更新するため、pullなしで観測できる。他端末からの回答は当該ホストで次にpullが
実行されるまで観測できない。
"""

import subprocess

import _git_remote
import _tbd_scan
from _session_state import update_state
from _transcript_agent_id import extract_transcript_agent_id

STATE_KEY_UNANSWERED = "tbd_unanswered_by_repo"
"""エージェント別・対象リポジトリID別の直近の未回答TBD件数を保持するセッション状態キー。

値は`{エージェント識別子: {対象リポジトリID: 件数}}`の2段辞書とする。
メインとサブエージェントのフック呼び出しは同一`session_id`で届くため、
エージェント識別子で分けないと一方の呼び出しが全件回答済みへの遷移を消費し、
他方へ通知が届かない。
"""

STATE_KEY_FINGERPRINT = "tbd_fingerprint_by_repo"
"""エージェント別・作業ディレクトリ別のactive状態ディレクトリ指紋を保持するセッション状態キー。

値は`{エージェント識別子: {作業ディレクトリ: 指紋文字列}}`の2段辞書とする。
指紋が前回観測から変化していない間はTBDの回答状態も変化しないため、
走査と`git remote get-url origin`をいずれも実行しない。
"""

_MAIN_AGENT_ID = "main"
"""`transcript_path`からエージェント識別子を抽出できない場合に用いるメイン会話の識別子。"""

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


def _nested(state: dict, key: str, agent_id: str) -> dict:
    """2段辞書`state[key][agent_id]`を必要に応じて用意して返す。

    過去の版が記録した1段辞書や不正な型が残っていた場合は破棄して初期化する。
    セッション状態は同一セッション内でのみ意味を持つため、移行処理は設けない。
    """
    outer = state.get(key)
    if not isinstance(outer, dict):
        outer = {}
        state[key] = outer
    inner = outer.get(agent_id)
    if not isinstance(inner, dict):
        inner = {}
        outer[agent_id] = inner
    return inner


def _fingerprint_unchanged(session_id: str, agent_id: str, cwd: str, fingerprint: str) -> bool:
    """記録済みの指紋が今回の指紋と一致するかを返す。

    読み取りだけを行うため、`update_state`のmutatorは書き込みを要求しない
    （暗黙の`None`を返し、`update_state`は書き込みをスキップする）。
    ロック下で読むのは、他プロセスの書き込み途中の状態を読まないためである。
    """
    previous: object = None

    def _read(state: dict) -> None:
        nonlocal previous
        outer = state.get(STATE_KEY_FINGERPRINT)
        inner = outer.get(agent_id) if isinstance(outer, dict) else None
        previous = inner.get(cwd) if isinstance(inner, dict) else None

    update_state(session_id, _read)
    return previous == fingerprint


def build_notice(session_id: str, cwd: str, transcript_path: str = "") -> str | None:
    """全件回答済みへの遷移を検出した場合に通知本文を返す。それ以外はNoneを返す。"""
    if not cwd:
        return None

    root = _tbd_scan.private_notes_root()
    if root is None:
        return None

    agent_id = extract_transcript_agent_id(transcript_path) or _MAIN_AGENT_ID
    fingerprint = _tbd_scan.active_fingerprint(root)
    if fingerprint is not None and _fingerprint_unchanged(session_id, agent_id, cwd, fingerprint):
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
        counts = _nested(state, STATE_KEY_UNANSWERED, agent_id)
        previous = counts.get(target_repo)
        outcome["notify"] = isinstance(previous, int) and previous > 0 and not unanswered and bool(answered)
        counts[target_repo] = len(unanswered)
        if fingerprint is not None:
            _nested(state, STATE_KEY_FINGERPRINT, agent_id)[cwd] = fingerprint
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
