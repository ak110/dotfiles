r"""多段終了手順の起動順をStopフックで検査する。

`agent-toolkit:process-wi`・`agent-toolkit:add-awi`・`agent-toolkit:plan-and-add-awi`は、
本体の作業を終える際に固定の終了スキル列（`agent-toolkit:completion-report`、
`agent-toolkit:process-wi`だけはこれに続けて`agent-toolkit:exit-session`）を順に起動する契約を持つ。
本フックは対象スキルの最新の起動以後に、要求される終了スキルが要求順で起動されたかを
transcriptのSkillツール起動記録（`tool_use`ブロック）から判定する。判定の正本は
`agent-toolkit:completion-report`が禁じる新規の起動追跡フラグではなく、当該`tool_use`ブロックそのものとする。

対象スキルの起動が無いセッションは検査対象外として常時approveする。
最新の対象スキル起動より前の終了スキル起動は充足の判定へ流用しない。
終了スキルの起動順が要求と逆である場合も未充足として扱う。

多段終了手順の検査は`stop_hook_active`が真の回だけ遮断する。
偽の回で遮断すると、対象スキルの起動後の通常のターン終了を毎回阻止する
（既存の`agents_server_session_advisor.py`も、他の判定が既にターン継続を強制している
再入回であることを`stop_hook_active`で確認したうえで自身の判定を重ねる前提を用いる）。

継続中の非同期作業がある場合は`is_pending_async_work`の判定を維持し、遮断しない。
セッション記録（transcript）を読み取れない場合も遮断せず、Stop判定ログへ検査不能を記録する。

委譲先での実行可否: 委譲先は最上位セッションが起動する終了手順を検査対象としないため、環境変数による除外が必要である。
"""

import json
import os
import pathlib

from agent_toolkit._hooks.notice import block_formatter as _block_notice_formatter
from agent_toolkit._hooks.stop_gate import (
    _iter_assistant_blocks,  # noqa: E402  # pylint: disable=protected-access
    append_stop_log,
    is_pending_async_work,
    read_transcript_entries_cached,
)
from agent_toolkit._hooks.stop_gate import parse_stop_session as _parse_stop_session

_HOOK_ID = "agent-toolkit/termination_order_advisor"

# agents_serverから起動された委譲先セッションであることを示す環境変数名。
_ENV_DELEGATED_SESSION = "AGENT_TOOLKIT_DELEGATED_SESSION"

# 表示用の代表名（`agent-toolkit:`修飾つき）と、プレフィックス付き・素の両表記を受理する名前集合の対。
# 代表名はアルファベット順による自動選出ではなく明示指定とする
# （`add-awi`は`agent-toolkit:add-awi`より辞書順で先に位置するため、自動選出では修飾を除いた表記を選んでしまう）。
_PROCESS_WI = ("agent-toolkit:process-wi", frozenset({"agent-toolkit:process-wi", "process-wi"}))
_ADD_AWI = ("agent-toolkit:add-awi", frozenset({"agent-toolkit:add-awi", "add-awi"}))
_PLAN_AND_ADD_AWI = (
    "agent-toolkit:plan-and-add-awi",
    frozenset({"agent-toolkit:plan-and-add-awi", "plan-and-add-awi"}),
)
_COMPLETION_REPORT = (
    "agent-toolkit:completion-report",
    frozenset({"agent-toolkit:completion-report", "completion-report"}),
)
_EXIT_SESSION = ("agent-toolkit:exit-session", frozenset({"agent-toolkit:exit-session", "exit-session"}))

# 検査対象スキルの(代表名, 名前集合)と、その最新起動以後に要求順で起動される必要がある終了スキル列。
_TERMINATION_SEQUENCES: tuple[tuple[tuple[str, frozenset[str]], tuple[tuple[str, frozenset[str]], ...]], ...] = (
    (_PROCESS_WI, (_COMPLETION_REPORT, _EXIT_SESSION)),
    (_ADD_AWI, (_COMPLETION_REPORT,)),
    (_PLAN_AND_ADD_AWI, (_COMPLETION_REPORT,)),
)

_MISSING_STEP_TEMPLATE = "{target}の終了手順が未完了である。次の順で残りの工程を実行する: {remaining}"

_block_notice = _block_notice_formatter(_HOOK_ID)


def _approve() -> None:
    """空のapprove応答を返す。"""
    print(json.dumps({}, ensure_ascii=False))


def _skill_invocations(entries: list[dict]) -> list[str]:
    """非sidechainのassistantエントリから、Skillツール起動のスキル名を時系列順で返す。"""
    invocations: list[str] = []
    for block in _iter_assistant_blocks(entries):
        if block.get("type") != "tool_use" or block.get("name") != "Skill":
            continue
        tool_input = block.get("input")
        skill_name = tool_input.get("skill") if isinstance(tool_input, dict) else None
        if isinstance(skill_name, str) and skill_name:
            invocations.append(skill_name)
    return invocations


def _last_index_matching(invocations: list[str], names: frozenset[str]) -> int | None:
    """`names`のいずれかに一致する最後の起動の位置を返す。一致が無ければ`None`。"""
    for index in range(len(invocations) - 1, -1, -1):
        if invocations[index] in names:
            return index
    return None


def _missing_step_index(
    invocations: list[str],
    target_index: int,
    required_sequence: tuple[tuple[str, frozenset[str]], ...],
) -> int:
    """`target_index`より後で、`required_sequence`を要求順に満たせた個数を返す。

    満たせた個数が`len(required_sequence)`と等しければ全充足を意味する。
    """
    pointer = 0
    for invocation in invocations[target_index + 1 :]:
        if pointer >= len(required_sequence):
            break
        _, names = required_sequence[pointer]
        if invocation in names:
            pointer += 1
    return pointer


def evaluate(payload_text: str) -> tuple[str, str]:
    """終了手順順序の判定結果と、遮断する場合の理由を返す。"""
    resolved = _parse_stop_session(payload_text, lambda: None)
    if resolved is None:
        return "approve", ""
    session_id, payload = resolved

    if payload.get("stop_hook_active") is not True:
        append_stop_log(session_id, "approve_not_reentrant", {})
        return "approve", ""

    if os.environ.get(_ENV_DELEGATED_SESSION) == "1":
        append_stop_log(session_id, "approve_delegated_session", {})
        return "approve", ""

    raw_transcript = payload.get("transcript_path", "")
    transcript_path = raw_transcript if isinstance(raw_transcript, str) else ""
    if not transcript_path or not pathlib.Path(transcript_path).is_file():
        append_stop_log(session_id, "approve_transcript_unreadable", {"reason": "検査不能"})
        return "approve", ""

    if is_pending_async_work(
        transcript_path,
        session_id,
        background_tasks=payload.get("background_tasks"),
    ):
        append_stop_log(session_id, "approve_pending_async", {})
        return "approve", ""

    entries = read_transcript_entries_cached(transcript_path)
    invocations = _skill_invocations(entries)

    missing_bodies: list[str] = []
    for (target_label, target_names), required_sequence in _TERMINATION_SEQUENCES:
        target_index = _last_index_matching(invocations, target_names)
        if target_index is None:
            continue
        pointer = _missing_step_index(invocations, target_index, required_sequence)
        if pointer >= len(required_sequence):
            continue
        remaining = "→".join(label for label, _ in required_sequence[pointer:])
        missing_bodies.append(_MISSING_STEP_TEMPLATE.format(target=target_label, remaining=remaining))

    if not missing_bodies:
        append_stop_log(session_id, "approve_termination_order_satisfied", {})
        return "approve", ""

    append_stop_log(session_id, "block_termination_order", {"count": len(missing_bodies)})
    reason = _block_notice(
        "\n\n".join(missing_bodies),
        fix="列挙した終了スキルを指定順で起動してから終了する。",
    )
    return "block", reason


def main(payload_text: str) -> int:
    """終了手順順序の不足・順序違反を検知し再促するエントリポイント。"""
    decision, body = evaluate(payload_text)
    if decision == "block":
        print(json.dumps({"decision": "block", "reason": body}, ensure_ascii=False))
    else:
        _approve()
    return 0
