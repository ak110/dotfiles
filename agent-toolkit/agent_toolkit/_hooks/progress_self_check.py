"""長時間の作業で、進捗と進め方の点検を一定間隔でエージェント自身へ促す。

PostToolUseとUserPromptSubmitの両フックから呼ばれ、ClaudeとCodex（Antigravityはアダプター経由）の
メインと委譲先の全セッションを対象とする。委譲先のレビューや修正の往復が長時間続いても、
ユーザーが介入するまで誰も時間と進捗を見比べない事象を防ぐ。

状態はセッション状態ファイルの`progress_self_check`へ、呼出主体（メインは`main`、
in-processのサブエージェントはpayloadの`agent_id`）ごとに次の2値をepoch秒で保持する。

- `started_at`: その主体を最初に観測した時刻。セッション開始時刻として経過時間の起点に使う
- `last_notified_at`: 最後に点検を通知した時刻。最初の観測では`started_at`と同じ値にする

最初の観測では記録だけを行い通知しない。前回の通知から`SELF_CHECK_INTERVAL_SECONDS`以上たった
観測で通知を1回返し、`last_notified_at`を更新する。
実ユーザーの発話の有無や無操作期間の長さによらず、前回通知を起点に判定する。
フックは最初のツール実行か発話で初めて観測するため、`started_at`はセッション開始よりわずかに遅れる。
"""

from __future__ import annotations

import time

from agent_toolkit._common.session_state import update_state
from agent_toolkit._hooks.agent_id import resolve_hook_agent_id
from agent_toolkit._hooks.notice import formatter as _notice_formatter

SELF_CHECK_INTERVAL_SECONDS = 30 * 60
"""点検を通知する間隔（秒）。"""

STATE_KEY = "progress_self_check"
"""セッション状態で呼出主体ごとの時刻を保持するキー。"""

SOURCE = "progress-self-check"
"""通知の生成元として`atk-auto`要素の`source`属性へ入れる名前。"""

_llm_notice = _notice_formatter(SOURCE)

_STARTED_AT_KEY = "started_at"
_LAST_NOTIFIED_AT_KEY = "last_notified_at"

_SELF_CHECK_NOTICE_BODY = (
    "進捗の定期セルフチェックの時刻になった。作業を続ける前に次を点検せよ。\n"
    "1. 現在の工程と開始時点を手元の記録で確かめ、経過時間を目標や同種処理の実績と比較せよ。"
    "工程開始時点や比較基準が不足するなら、記録や担当者への照会で取得せよ。\n"
    "2. 前回以降の工程の変化と再試行（レビューのラウンド、修正や検証のやり直し）を確かめ、"
    "収束しているか比較せよ。`HEAD`や最終活動時刻の変化だけで妥当と判断するな。\n"
    "3. 作業範囲を広げすぎていないか。より費用の小さい方式は無いか。\n"
    "4. 委譲先から受け取った想定外事象に、同じ種類の繰り返し"
    "（中断、接続失敗、フックの警告、再実行など実行基盤の異常）は無いか。あれば原因を調べよ。\n"
    "5. 会話圧縮の後に読むべき資料を読み残していないか。\n"
    "6. 配下に委譲先がいれば、活動の有無と工程の所要・反復の妥当性を分けて点検せよ。"
    "待機中の詳細な比較は`agent-toolkit:delegation`の`references/waiting-and-monitoring.md`「成果物側の観測」に従え。\n"
    "改善策があれば、その場で進め方を変えよ。時間を理由に作業を止めたり品質の確認を省いたりするな。"
    "気付いた改善点は`気付いた改善点:`の行で報告せよ。"
    "問題の有無にかかわらず、何をどう点検したかと結果を既存の報告契約に従って報告せよ。"
    "経過時間と目標に対する進み具合、余計な作業の有無、当初目的との一致を含めよ。"
)


def _format_elapsed(seconds: float) -> str:
    """経過秒数を「N分」か「N時間M分」の表記へ変える。"""
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes}分"
    hours, rest = divmod(minutes, 60)
    return f"{hours}時間{rest}分" if rest else f"{hours}時間"


def _number(value: object) -> float | None:
    """状態に保存した時刻を数値として返す。数値でなければ`None`を返す。"""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def claim_notice(payload: dict, *, now: float | None = None) -> str | None:
    """点検の時刻に達していれば通知本文を返し、通知時刻を更新する。達していなければ`None`を返す。

    状態の読み書きは`update_state`の排他区間で行い、同じ主体の並列なフックが同じ通知を重ねて返さないようにする。
    """
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return None
    agent_id = resolve_hook_agent_id(payload)
    current_time = time.time() if now is None else now
    elapsed: float | None = None

    def _mutate(state: dict) -> dict | None:
        nonlocal elapsed
        entries = state.get(STATE_KEY)
        entries = dict(entries) if isinstance(entries, dict) else {}
        entry = entries.get(agent_id)
        entry = dict(entry) if isinstance(entry, dict) else {}
        started_at = _number(entry.get(_STARTED_AT_KEY))
        last_notified_at = _number(entry.get(_LAST_NOTIFIED_AT_KEY))
        if started_at is None:
            entries[agent_id] = {_STARTED_AT_KEY: current_time, _LAST_NOTIFIED_AT_KEY: current_time}
            state[STATE_KEY] = entries
            return state
        reference = started_at if last_notified_at is None else last_notified_at
        if current_time - reference < SELF_CHECK_INTERVAL_SECONDS:
            return None
        elapsed = current_time - started_at
        entry[_LAST_NOTIFIED_AT_KEY] = current_time
        entries[agent_id] = entry
        state[STATE_KEY] = entries
        return state

    if not update_state(session_id, _mutate) or elapsed is None:
        return None
    body = f"セッション開始から{_format_elapsed(elapsed)}たった。{_SELF_CHECK_NOTICE_BODY}"
    return _llm_notice(body, tag="notice") or None
