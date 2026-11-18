"""この会話が投入したUWIへの回答を検出し、通知本文を組み立てる。

PostToolUseフックから毎回呼ばれる。確かめる対象は、`atk wi add`が投入時にセッション状態へ記録したUWI
（`_common/uwi_watch.py`）だけであり、記録が空の会話はprivate-notesを読まない。
回答が付いたUWIは通知して記録から外し、採用・不採用で終了したUWIは通知せずに外す。

投入元の会話は`atk wi add`が解決する。委譲先が投入したUWIは委譲の最上位の会話へ記録される。
投入元を解決できずに投入されたUWIは、どの会話へも通知しない。

`git pull`は実行しない。同一ホストのWeb UIおよびCLIからの回答はローカルファイルを
直接更新するため、pullなしで観測できる。他端末からの回答は、このホストで次にpullが
実行されるまで観測できない。
"""

# 関数の実行時にだけ使う依存は、使用時まで読み込まない。
lazy import pathlib
lazy import time

lazy from agent_toolkit._atk.wi import uwi_scan as _uwi_scan
lazy from agent_toolkit._atk.wi.constants import WI_PROCESSABLE_STATES, WI_STATE_ADOPTED, WI_STATE_REJECTED
lazy from agent_toolkit._atk.wi.frontmatter import parse_frontmatter
lazy from agent_toolkit._atk.wi.layout import entry_path, entry_state_from_metadata
lazy from agent_toolkit._common import uwi_watch as _uwi_watch
lazy from agent_toolkit._common.next_action import ActionableError
lazy from agent_toolkit._common.session_state import read_state, update_state
lazy from agent_toolkit._hooks.agent_id import MAIN_AGENT_ID

_SCAN_ANSWERED_KEYS = ("uwi_answered_by_repo", "tbd_answered_by_repo")
"""全WIを走査して回答を検出していた版が残した、通知済みの基準値の状態キー。後ろは改名前のキー。

値は`{エージェント識別子: {対象リポジトリID: 回答済みのファイル名一覧}}`の2段辞書である。
配布をまたいで稼働し続ける会話は、投入時の記録を持たずにこのキーだけを持つ。
その会話が投入済みのUWIも通知の対象に保つため、`_adopt_scan_state`が1回だけ走査して記録へ引き継ぐ。
状態ファイルの回収期限（`session_state.STALE_STATE_MAX_AGE_SECONDS`）を過ぎた後の版数更新以降に、引き継ぎごと撤去できる。
"""

_SCAN_FINGERPRINT_KEYS = ("uwi_fingerprint_by_repo", "tbd_fingerprint_by_repo")
"""同じ版が残した、変化検出用の指紋の状態キー。引き継ぎの時に基準値と併せて消す。"""

_ADOPT_RETRY_KEY = "uwi_adopt_retry_at"
"""引き継ぎをやり直してよい時刻（エポック秒）の状態キー。

引き継ぎを終えられなかった会話が、ツール実行のたびに全WIを走査し直さないために持つ。
引き継ぎの完了時に基準値と併せて消す。
"""

_ADOPT_RETRY_SECONDS = 60.0
"""引き継ぎを終えられなかった後、次に走査するまで空ける秒数。"""


def _baseline(state: dict) -> dict | None:
    """走査方式の版が残した、通知済みの基準値（対象リポジトリID → 回答済みのファイル名一覧）を返す。無ければNoneを返す。"""
    for key in _SCAN_ANSWERED_KEYS:
        by_agent = state.get(key)
        by_repo = by_agent.get(MAIN_AGENT_ID) if isinstance(by_agent, dict) else None
        if isinstance(by_repo, dict):
            return by_repo
    return None


def _adopt_scan_state(session_id: str, state: dict) -> list[str]:
    """走査方式の基準値を持つ会話が投入した未通知のUWIを、1回だけ走査して記録へ引き継ぐ。

    基準値を記録済みの対象リポジトリごとに、未回答のUWIと、回答済みで基準値に無いUWIを引き継ぐ。
    保存先を解決できない場合と走査が不完全な場合は、基準値を保って投入時の記録だけを返し、
    `_ADOPT_RETRY_SECONDS`秒が過ぎた後の呼び出しでやり直す。
    読めた分だけで引き継ぎを終えると、読めなかったUWIの回答を通知する機会を失うためである。
    状態を書けなかった場合は空の一覧を返し、次の呼び出しでやり直す。
    """
    recorded = _uwi_watch.awaiting(state)
    now = time.time()
    retry_at = state.get(_ADOPT_RETRY_KEY)
    if isinstance(retry_at, (int, float)) and not isinstance(retry_at, bool) and now < retry_at:
        return recorded
    baseline = _baseline(state) or {}
    own: set[str] = set()
    root = _uwi_scan.private_notes_root()
    complete = root is not None
    if root is not None:
        for target_repo, notified in baseline.items():
            if isinstance(target_repo, str) and isinstance(notified, list):
                scan = _uwi_scan.scan_active_uwis(root, target_repo)
                complete = complete and scan.complete
                own.update(
                    entry.filename
                    for entry in scan.entries
                    if entry.submitter_session == session_id and not (entry.answered and entry.filename in notified)
                )
    if not complete:
        update_state(session_id, lambda current: {**current, _ADOPT_RETRY_KEY: now + _ADOPT_RETRY_SECONDS})
        return recorded
    adopted: list[str] = []

    def _adopt(current: dict) -> dict:
        nonlocal adopted
        for key in (*_SCAN_ANSWERED_KEYS, *_SCAN_FINGERPRINT_KEYS, _ADOPT_RETRY_KEY):
            current.pop(key, None)
        current[_uwi_watch.STATE_KEY] = sorted(own.union(_uwi_watch.awaiting(current)))
        adopted = _uwi_watch.awaiting(current)
        return current

    return adopted if update_state(session_id, _adopt) else []


def _discard(session_id: str, finished: list[str], answered: dict[str, list[str]]) -> bool:
    """確認を終えたUWIを記録から外す。書き込んだ場合だけ真を返す。

    引き継ぎを保留している会話では、通知したUWIを同じ書き込みで基準値へ加える。
    基準値に無い回答済みUWIを引き継ぐ`_adopt_scan_state`が、走査の回復後に同じ回答を再び通知しないためである。
    """
    removed = set(finished)

    def _update(current: dict) -> dict:
        current[_uwi_watch.STATE_KEY] = [name for name in _uwi_watch.awaiting(current) if name not in removed]
        baseline = _baseline(current)
        if baseline is not None:
            for target_repo, filenames in answered.items():
                notified = baseline.get(target_repo)
                if isinstance(notified, list):
                    baseline[target_repo] = sorted({*notified, *filenames})
        return current

    return update_state(session_id, _update)


def _settled(root: pathlib.Path, filename: str) -> tuple[bool, str | None]:
    """UWIの確認を終えてよいかと、回答済みの場合の対象リポジトリIDを返す。

    終了したUWIは`(True, None)`、回答済みのUWIは`(True, 対象リポジトリID)`を返す。
    未回答・保留中のUWIと、同期の途中などで読めないUWIは`(False, None)`を返し、次の呼び出しで確かめ直す。
    """
    try:
        path = entry_path(root, filename)
        text = path.read_text(encoding="utf-8")
        parsed = parse_frontmatter(text)
        if parsed is None:
            return False, None
        state = entry_state_from_metadata(path, parsed[0])
    except ActionableError, OSError, UnicodeDecodeError:
        return False, None
    if state in (WI_STATE_ADOPTED, WI_STATE_REJECTED):
        return True, None
    target_repo = parsed[0].get("target_repo")
    if state in WI_PROCESSABLE_STATES and isinstance(target_repo, str) and _uwi_scan.is_uwi_answered(text):
        return True, target_repo
    return False, None


def build_notice(session_id: str) -> str | None:
    """この会話が投入したUWIに回答が付いていれば通知本文を返す。それ以外はNoneを返す。"""
    state = read_state(session_id)
    if any(key in state for key in _SCAN_ANSWERED_KEYS):
        awaiting = _adopt_scan_state(session_id, state)
    else:
        awaiting = _uwi_watch.awaiting(state)
    if not awaiting:
        return None
    root = _uwi_scan.private_notes_root()
    if root is None:
        return None

    finished: list[str] = []
    answered: dict[str, list[str]] = {}
    for filename in awaiting:
        settled, target_repo = _settled(root, filename)
        if not settled:
            continue
        finished.append(filename)
        if target_repo is not None:
            answered.setdefault(target_repo, []).append(filename)
    # 記録から外せないまま通知すると、同じ回答を次回以降も繰り返し通知する。
    if not finished or not _discard(session_id, finished, answered) or not answered:
        return None

    found = "".join(
        f"リポジトリ{target_repo}に新たに回答されたUWIがある: {', '.join(filenames)}。"
        for target_repo, filenames in sorted(answered.items())
    )
    return f"{found}`agent-toolkit:atk-mode/references/answer-transitions.md`「回答後の状態遷移」に従って反映せよ。"
