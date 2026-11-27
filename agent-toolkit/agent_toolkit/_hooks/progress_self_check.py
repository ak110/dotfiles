"""長時間の作業で、進捗と進め方の点検を一定間隔でエージェント自身へ促す。

PostToolUseとUserPromptSubmitの両フックから呼ばれ、ClaudeとCodex（Antigravityはアダプター経由）の
メインと委譲先の全セッションを対象とする。委譲先のレビューや修正の往復が長時間続いても、
ユーザーが介入するまで誰も時間と進捗を見比べない事象を防ぐ。

状態はセッション状態ファイルの`progress_self_check`へ、呼出主体（メインは`main`、
in-processのサブエージェントはpayloadの`agent_id`）ごとに次の値をepoch秒で保持する。

- `started_at`: その主体を最初に観測した時刻。経過時間の起点に使い、セッションや巡回の開始とは区別する
- `last_notified_at`: 通知が最終出力へ採用された最後の時刻。最初の観測では`started_at`と同じ値にする
- `reserved_at`: 期日に達して通知を生成した時刻。並列なフックが同じ通知を重ねないための予約に使う

最初の観測では記録だけを行い通知しない。前回の通知から`SELF_CHECK_INTERVAL_SECONDS`以上たった
観測で通知を1回返し、予約を記録する。`last_notified_at`は通知本文がホストへの最終出力へ採用された時点で
`notice.record_delivery`が記録する時刻から進める。通知を生成しただけの時点で期日を消費すると、
未採用・整形失敗・最終出力の作成失敗のときに通知が届かないまま次の期日まで遅れるためである。
予約は`RESERVATION_SECONDS`で失効し、hook処理の終了時に未採用のまま残った予約は`release_reservations`が解除する。
古い試行の配送記録は、より新しい確定時刻を巻き戻さない（確定時刻は最大値で扱う）。
実ユーザーの発話の有無や無操作期間の長さによらず、前回通知を起点に判定する。
フックは最初のツール実行か発話で初めて観測するため、`started_at`はセッション開始よりわずかに遅れる。
"""

from __future__ import annotations

import json
import os
import time

from agent_toolkit._common import process_loop_session
from agent_toolkit._common.delegated_session import is_delegated
from agent_toolkit._common.json_lines import split_lines
from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._common.session_state import update_state
from agent_toolkit._hooks import notice as _notice
from agent_toolkit._hooks.agent_id import MAIN_AGENT_ID, is_main_agent_context, resolve_hook_agent_id
from agent_toolkit._hooks.notice import formatter as _notice_formatter

SELF_CHECK_INTERVAL_SECONDS = 30 * 60
"""点検を通知する間隔（秒）。"""

STATE_KEY = "progress_self_check"
"""セッション状態で呼出主体ごとの時刻を保持するキー。"""

SOURCE = "progress-self-check"
"""通知の生成元として`atk-auto`要素の`source`属性へ入れる名前。"""

_llm_notice = _notice_formatter(SOURCE)

DELIVERED_KEY = "progress_self_check_delivered"
"""最終出力へ採用された通知の生成時刻を、呼出主体ごとに受け取るセッション状態のキー。"""

RESERVATION_SECONDS = 60
"""期日到達後の予約が並列claimを抑止する秒数。同じ時点に並列で動くフックの処理時間より十分長い値にする。"""

_STARTED_AT_KEY = "started_at"
_LAST_NOTIFIED_AT_KEY = "last_notified_at"
_RESERVED_AT_KEY = "reserved_at"

_reservations: list[tuple[str, str, float]] = []

_SELF_CHECK_NOTICE_BODY = (
    "通知の時刻は点検の結果ではない。"
    "`main-reporting.md`「進捗点検」に従って作業契約を参照し、完成条件の根拠と残工程を照合してください。"
    "照合した結果から、予定を維持するか組み直すかを判断してください。"
    "確認済みの根拠は再掲せず、未達と処置を報告してください。"
)
_DELEGATED_NOTICE_BODY = (
    "完成条件の根拠と残工程を照合してください。"
    "差・未達があれば既存の引き継ぎ記録を更新し、委譲元の判断が必要な差は直上へ通知してください。"
)


def _format_elapsed(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes}分"
    hours, rest = divmod(minutes, 60)
    return f"{hours}時間{rest}分" if rest else f"{hours}時間"


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _delivered_at(state: dict, agent_id: str) -> float | None:
    delivered = state.get(DELIVERED_KEY)
    values = delivered.get(agent_id) if isinstance(delivered, dict) else None
    times = [number for value in values if (number := _number(_parse(value))) is not None] if isinstance(values, list) else []
    return max(times) if times else None


def _parse(value: object) -> object:
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return value


_RECENT_HOOK_ROWS = 50
"""hook所要の平均に使う直近の計測件数。"""


def _saturation_text(session_id: str) -> str:
    """マシンの飽和を担当の進め方と分けて判定できるよう、同時稼働数と直近のhook所要を既存の記録から読む。

    新しい常駐の監視は作らず、agents_serverの状態ファイルとセッションの`hook-timings.jsonl`だけを読む。
    読めない値は不明と示し、点検の通知自体は止めない。
    """
    from agent_toolkit._agents_server import admission  # pylint: disable=import-outside-toplevel
    from agent_toolkit._atk import managed_temp  # pylint: disable=import-outside-toplevel
    from agent_toolkit._common import state_paths  # pylint: disable=import-outside-toplevel

    try:
        observation = admission.observe()
        limit = admission.configured_limit()
        limit_text = "上限なし" if limit.value is None else f"上限{limit.value}"
        working = f"作業中の委譲先はマシン全体で{observation.working}（{limit_text}、起動待ち{observation.waiting}）。"
        if observation.failures:
            base = state_paths.state_dir() / "agents-server"
            lines = [f"{base}/{failure}" for failure in observation.failures]
            saved = ""
            directory = managed_temp.find_session_temp(session_id)
            if directory is not None:
                try:
                    report = directory / "agents-concurrency-failures.txt"
                    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
                    saved = f"全{len(lines)}件の所在と診断: {report}。"
                except OSError:
                    saved = ""
            shown = "、".join(lines[:3]) + (f"（他{len(lines) - 3}件）" if len(lines) > 3 else "")
            if len(lines) > 3 and not saved:
                shown = "、".join(lines)
            working += (
                f"ただし状態ファイル{len(lines)}件を読めず、この数は部分観測である。"
                f"読めない記録: {shown}。{saved}各ファイルの内容と書き込み主体の稼働を確認してください。"
            )
    except Exception as error:  # pylint: disable=broad-exception-caught
        working = (
            f"作業中の委譲先の数を取得できない（{type(error).__name__}: {error}）。"
            "`atk config get state_dir`が示すディレクトリの`agents-server`配下の"
            "状態ファイルの読み取り権限と内容を確認してください。"
        )
    seconds: list[float] = []
    hooks_problem = ""
    directory = None
    try:
        directory = managed_temp.find_session_temp(session_id)
        if directory is not None:
            lines = split_lines((directory / "hook-timings.jsonl").read_text(encoding="utf-8"))
            for line in lines[-_RECENT_HOOK_ROWS * 4 :]:
                row = json.loads(line)
                if isinstance(row, dict) and row.get("stage") == "total" and isinstance(row.get("seconds"), (int, float)):
                    seconds.append(float(row["seconds"]))
    except FileNotFoundError:
        seconds = []
    except (OSError, UnicodeError, ValueError) as error:
        seconds = []
        hooks_problem = (
            f"hook所要時間の記録を読めない（{type(error).__name__}）。`{directory}/hook-timings.jsonl`を確認してください。"
        )
    seconds = seconds[-_RECENT_HOOK_ROWS:]
    if hooks_problem:
        hooks = hooks_problem
    elif seconds:
        hooks = f"直近{len(seconds)}回のhook所要は平均{sum(seconds) / len(seconds):.1f}秒・最大{max(seconds):.1f}秒。"
    else:
        hooks = "直近のhook所要の記録は無い。"
    return working + hooks


def _new_wi_text(session_id: str, recipient: str) -> tuple[str, str | None]:
    from agent_toolkit._atk.wi.listing import progress_new_wis  # pylint: disable=import-outside-toplevel

    try:
        rows = progress_new_wis(session_id, track_merges=True)
    except ActionableError as error:
        return f"新着WIは未確認。{error.message}", None
    except (Exception, SystemExit) as error:  # pylint: disable=broad-exception-caught
        return (
            f"新着WIは未確認（{type(error).__name__}）。`atk wi list`で確かめてください。",
            None,
        )
    if rows is None:
        return "新着WIは未確認（開始時集合なし）。", None
    tracked = [row for row in rows if row.merge_pending == "processing"]
    text, snapshot = _notice.new_wi_preview(session_id, recipient, [row for row in rows if row.merge_pending != "processing"])
    if tracked:
        text += " 取り込み結果待ち（再割当不要）: " + "、".join(row.filename for row in tracked) + "。"
    return text, snapshot


def claim_notice(payload: dict, *, now: float | None = None, periodic_recheck: bool = False) -> str | None:
    """点検の期日に達していれば通知本文を返し、配送時に通知時刻を確定する予約をする。達していなければ`None`を返す。

    状態の読み書きは`update_state`の排他区間で行い、同じ主体の並列なフックが同じ通知を重ねて返さないようにする。
    """
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return None
    agent_id = resolve_hook_agent_id(payload)
    current_time = time.time() if now is None else now
    elapsed: float | None = None
    since_notice: float | None = None

    def _mutate(state: dict) -> dict | None:
        nonlocal elapsed, since_notice
        entries = state.get(STATE_KEY)
        entries = dict(entries) if isinstance(entries, dict) else {}
        entry = entries.get(agent_id)
        entry = dict(entry) if isinstance(entry, dict) else {}
        started_at = _number(entry.get(_STARTED_AT_KEY))
        if started_at is None:
            entries[agent_id] = {_STARTED_AT_KEY: current_time, _LAST_NOTIFIED_AT_KEY: current_time}
            state[STATE_KEY] = entries
            return state
        candidates = [
            value for value in (_number(entry.get(_LAST_NOTIFIED_AT_KEY)), _delivered_at(state, agent_id)) if value is not None
        ]
        confirmed = max(candidates) if candidates else started_at
        delivered = state.get(DELIVERED_KEY)
        if isinstance(delivered, dict) and agent_id in delivered:
            state[DELIVERED_KEY] = {key: value for key, value in delivered.items() if key != agent_id}
            entry[_LAST_NOTIFIED_AT_KEY] = confirmed
        reserved_at = _number(entry.get(_RESERVED_AT_KEY))
        due = current_time - confirmed >= SELF_CHECK_INTERVAL_SECONDS - (60 if periodic_recheck else 0)
        reserved = reserved_at is not None and reserved_at > confirmed and current_time - reserved_at < RESERVATION_SECONDS
        if due and not reserved:
            elapsed = current_time - started_at
            if confirmed > started_at:
                since_notice = current_time - confirmed
            entry[_RESERVED_AT_KEY] = current_time
        elif not (isinstance(delivered, dict) and agent_id in delivered):
            return None
        entries[agent_id] = entry
        state[STATE_KEY] = entries
        return state

    if not update_state(session_id, _mutate) or elapsed is None:
        return None

    _reservations.append((session_id, agent_id, current_time))
    elapsed_text = (
        f"この主体の初回観測から{_format_elapsed(elapsed)}たった。"
        if elapsed >= 0
        else "初回観測の保存時刻が通知生成時刻より後にあるため、経過時間は不明。"
    )
    main_agent = is_main_agent_context(payload)
    instruction = _SELF_CHECK_NOTICE_BODY if main_agent and not is_delegated(os.environ) else _DELEGATED_NOTICE_BODY
    skipped_text, skipped_receipts = _skipped_notices(session_id, agent_id)
    previous = f"前回の通知から{_format_elapsed(since_notice)}たった。" if since_notice is not None else ""
    new_wis, wi_snapshot = (
        _new_wi_text(session_id, agent_id)
        if main_agent and process_loop_session.is_identified_process_loop_session(session_id, os.environ)
        else ("", None)
    )
    body = (
        elapsed_text
        + previous
        + _saturation_text(session_id)
        + new_wis
        + skipped_text
        + _contract_text(payload, session_id)
        + instruction
    )
    text = _llm_notice(body, tag="notice")
    if not text:
        return None
    _notice.stage_delivery(text, session_id, DELIVERED_KEY, agent_id, repr(current_time))
    if wi_snapshot is not None:
        _notice.stage_new_wi_preview(text, session_id, agent_id, wi_snapshot)
    for field, value in skipped_receipts:
        _notice.stage_delivery(text, session_id, field, agent_id, value)
    return text


def _contract_text(payload: dict, session_id: str) -> str:
    from agent_toolkit._hooks import work_contract  # pylint: disable=import-outside-toplevel

    if resolve_hook_agent_id(payload) != MAIN_AGENT_ID or not work_contract.exists(session_id):
        return ""
    return f"自身の作業契約`{work_contract.record_path(session_id)}`を読み直し、目的、訂正と残る要求を確かめてください。"


def _skipped_notices(session_id: str, agent_id: str) -> tuple[str, list[tuple[str, str]]]:
    from agent_toolkit._hooks.pretooluse import operation_skills  # pylint: disable=import-outside-toplevel

    passed = _notice.pending_resend_passed(session_id, agent_id)
    skills = operation_skills.unconfirmed_skills(session_id, agent_id)
    items = [
        *dict.fromkeys(entry.partition("\n")[0] for entry in passed),
        *(f"スキル`{name}`の起動の案内" for name in skills),
    ]
    if not items:
        return "", []
    receipts = [
        *((_notice.RESEND_ACKED_KEY, entry) for entry in passed),
        *((operation_skills.SELF_CHECKED_KEY, name) for name in skills),
    ]
    return (
        "前回の点検以後に、対応を確認できていない通知がある: "
        + "、".join(items)
        + "。それぞれ必要な対応（スキルの起動、資料の取得、報告の訂正）を済ませたか確かめ、未対応なら今行ってください。"
    ), receipts


def release_reservations() -> None:
    pending = list(_reservations)
    _reservations.clear()
    for session_id, agent_id, reserved_at in pending:

        def _release(state: dict, agent_id: str = agent_id, reserved_at: float = reserved_at) -> dict | None:
            current = state.get(STATE_KEY)
            if not isinstance(current, dict):
                return None
            entry = current.get(agent_id)
            if not isinstance(entry, dict) or _number(entry.get(_RESERVED_AT_KEY)) != reserved_at:
                return None
            entries = dict(current)
            entries[agent_id] = {key: value for key, value in entry.items() if key != _RESERVED_AT_KEY}
            state[STATE_KEY] = entries
            return state

        update_state(session_id, _release)
