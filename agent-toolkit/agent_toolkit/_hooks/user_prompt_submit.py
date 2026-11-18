"""Claude Code・Codex plugin agent-toolkit: UserPromptSubmitでセッション状態を記録し、応答の契約を注入する。

ホスト別コマンド形式（Claude Codeは`/agent-toolkit:<name>`・`/<name>`、
Codexは`$agent-toolkit:<name>`・`$<name>`）で発話した手動のスキル起動を検出し、
対応するセッション状態フラグを立てる。
既存のPostToolUse(Skill)経由の記録では捕捉できない手動起動を補完する。

検出対象スキルと対応フラグ:

- Codexのwrite-plan → `plan_mode_skill_invoked`
- process-wi → `process_wi_skill_invoked`

セッション名はUserPromptSubmitのコマンドhookが通常状態から設定する。

例示の語（「例えば」「たとえば」「例として」）を含む実ユーザー発話では、経過時間と状態によらず、
`agent-toolkit:user-confirmation-and-report`の`references/user-utterance.md`「原文の保持と取り込み」の所在を示し、
原文の対象と例示の関係を判断させる。調査を求める語（「確認」「調査」「見直」など）も含む発話では、
`agent-toolkit:delegation`の`references/routing.md`の所在も加える。判定はコードブロック、インラインコードおよび
引用行を除いた本文の部分文字列の一致だけで行い、範囲語の有無は判定に含めない（範囲語は「など」「とか」のような
一般的な語で表され、条件に加えても発火がほとんど減らないため）。発話が範囲判断を要するかの判断は受領側に残し、
本フックは発話の意味を判断しない。
一定間隔ごとの進捗のセルフチェック通知（`progress_self_check`が判定する）は、
実ユーザー発話と機械注入ターンの両方で前回通知からの経過時間に基づいて付ける。
成立した注記を1つの`additionalContext`へまとめる。

注記の対象は、自動的なプロンプトを除く全てのユーザー発話とする。ユーザー自身が入力した発話は、
スラッシュコマンド（Claude Codeは`/`、Codexは`$`）で始まるものも対象に含める。
機械注入ターンでは、実ユーザー発話向けの起動案内を返さず、配送待ちの状態も消費しない。
機械注入ターンの判定入力は次の5系統とし、いずれかが成立したターンを対象とする。

1. payloadの`source`が存在し、値が`user`以外であること
2. `prompt`の1行目が`PERIODIC_RECHECK_MARKER`だけの行であること
3. 委譲先として起動されていること
4. `prompt`が`<task-notification`で始まること
5. `prompt`の1行目が新旧の自動挿入要素の開始タグを含むこと

例外時はfail-openで exit 0 を返す。
"""

from __future__ import annotations

import json
import os
import re

from agent_toolkit._hooks.notice import begin_delivery, emit_response, informational, resolve_notice, set_warning_session_id

# pylint: disable-next=wrong-import-position,import-error
from agent_toolkit._hooks.notice import formatter as _notice_formatter  # noqa: E402
from agent_toolkit._hooks.posttooluse import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    _PLAN_MODE_SKILL_NAMES,
    _PROCESS_WI_SKILL_NAMES,
    content_notices,
)
lazy from agent_toolkit._common import (
    automated_prompt,  # noqa: E402  # pylint: disable=wrong-import-position,import-error
)
lazy from agent_toolkit._common.delegated_session import (
    is_delegated,  # noqa: E402  # pylint: disable=wrong-import-position,import-error
)
lazy from agent_toolkit._common.periodic_recheck import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    LEGACY_PERIODIC_RECHECK_MARKER,
    PERIODIC_RECHECK_MARKER,
)
lazy from agent_toolkit._common.session_state import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    update_state,
)
lazy from agent_toolkit._hooks import command_timing, session_title, termination_evidence, work_contract
lazy from agent_toolkit._hooks import progress_self_check as _progress_self_check
lazy from agent_toolkit._hooks.host import is_antigravity_payload, is_codex_payload  # noqa: E402  # pylint: disable=wrong-import-position,import-error


def _extend_with_short_names(names: frozenset[str]) -> frozenset[str]:
    """フルスキル名`agent-toolkit:<name>`から短縮名`<name>`を追加した拡張集合を返す。"""
    extended = set(names)
    for name in names:
        if ":" in name:
            _, short = name.split(":", 1)
            if short:
                extended.add(short)
    return frozenset(extended)


# スラッシュコマンド起動時にも検出できるように、フルネームと短縮名の両方を含む拡張集合を組み立てる。
_PLAN_MODE_NAMES_EXTENDED = _extend_with_short_names(_PLAN_MODE_SKILL_NAMES)
_PROCESS_WI_NAMES_EXTENDED = _extend_with_short_names(_PROCESS_WI_SKILL_NAMES)

# ホスト判定後の手動コマンドから<name>を抽出する。
# 先頭記号の直後に`agent-toolkit:`prefixがある場合と無い場合の両方を許容する。
# スキル名として妥当な文字（英数・ハイフン・アンダースコア）のみを対象とする。
_SKILL_COMMAND_PATTERN = re.compile(r"\A(?:agent-toolkit:)?([A-Za-z0-9][A-Za-z0-9_-]*)\b")
_HARNESS_MESSAGE_RE = re.compile(r"^\s*(?:\[SYSTEM NOTIFICATION - NOT USER INPUT\]\s*)?<task-notification\b")
_USER_PROMPT_SOURCE_KEY = "source"
_USER_PROMPT_SOURCE_USER = "user"
_llm_notice = _notice_formatter("user_prompt_submit")


def _is_harness_message(prompt: str) -> bool:
    """ハーネスが挿入したメッセージかを判定する。

    ハーネス通知をユーザーのスラッシュコマンドとして扱わないために用いる。
    """
    return _HARNESS_MESSAGE_RE.search(prompt) is not None


def _is_machine_injected(payload: dict, prompt: str) -> bool:
    """ユーザーが発話していないターンかを判定する。

    判定入力は次の5系統とし、いずれかが成立したターンを機械注入とする。

    1. payloadの`source`が存在し、値が`user`以外であること
    2. `prompt`の1行目が`PERIODIC_RECHECK_MARKER`だけの行であること
    3. 委譲先として起動されていること
    4. `prompt`がハーネスの挿入する包みで始まること
    5. `prompt`の1行目が機械生成の本文を示す`atk-auto`（旧形式を含む）の開始タグを含むこと

    Claude Code 2.1.274の時点で`source`は配送されないため、残る4系統で判定する。
    監査記録は`docs/development/audit-records.md`の
    「agent-toolkit/agent_toolkit/_hooks/user_prompt_submit.py：UserPromptSubmitの出所欄：2026年9月17日」にある。
    出所を判定入力に持たないと、機械が投入したターンが通常発話として処理され、
    実ユーザー発話が受け取るべき発話の内容を現物で確かめる手順を示す注記をそのターンが消費する。
    第5の系統は、process-loopが子セッションの最初の入力として渡す本文を対象とする。
    生成側が付ける`atk-auto`の開始タグだけを判定入力とし、本文の文言を写した別の判定を持たない。
    """
    source = payload.get(_USER_PROMPT_SOURCE_KEY)
    if isinstance(source, str) and source and source != _USER_PROMPT_SOURCE_USER:
        return True
    if prompt.split("\n", 1)[0].strip() in {PERIODIC_RECHECK_MARKER, LEGACY_PERIODIC_RECHECK_MARKER}:
        return True
    if is_delegated(os.environ):
        return True
    if automated_prompt.contains(prompt):
        return True
    return _is_harness_message(prompt)


def _set_plan_mode_invoked(state: dict) -> dict | None:
    if state.get("plan_mode_skill_invoked", False):
        return None
    state["plan_mode_skill_invoked"] = True
    return state


def _set_process_wi_invoked(state: dict) -> dict | None:
    if state.get("process_wi_skill_invoked", False):
        return None
    state["process_wi_skill_invoked"] = True
    return state


def _emit_hook_output(*, additional_context: str | None, title: str | None = None) -> None:
    """UserPromptSubmitの値を持つ応答欄を1つのJSONとして出力する。"""
    hook_specific_output: dict[str, str] = {"hookEventName": "UserPromptSubmit"}
    if additional_context is not None:
        hook_specific_output["additionalContext"] = additional_context
    if title is not None:
        hook_specific_output["sessionTitle"] = title
    emit_response({"hookSpecificOutput": hook_specific_output})


def _work_contract_notice(session_id: str, body: str) -> str:
    """作業契約の本文を、実ユーザー入力ごとに届ける。

    同じ本文でも入力ごとに読み直す機会を与えるため、前回の入力で記録した重複抑止を同じ原因だけ解除してから整形する。
    記録はこの原因の1件だけを保つため、入力が続いても状態は増えない。
    """
    resolve_notice(session_id, "user_prompt_submit", "_work_contract_notice")
    # 入力ごとに同じ本文を届ける配送であり、ユーザーの画面へは写さない。
    return informational(_llm_notice(body))


def _clock_notice(session_id: str) -> str:
    """実ユーザー入力ごとに、受付時の壁時計の日時を届ける。

    時刻を推測で記録しないためで、点検通知の生成時刻とは区別する。
    入力ごとに本文が変わるため、前回の重複抑止の記録を同じ原因だけ解除して状態の増加を防ぐ。
    """
    resolve_notice(session_id, "user_prompt_submit", "_clock_notice")
    # 入力ごとに出る定型の情報であり、ユーザーの画面へは写さない。
    return informational(_llm_notice(f"ユーザー入力の受付時刻: `{work_contract.local_time()}`"))


def main(payload_text: str) -> int:
    """エントリポイント。終了コードは常に0（fail-open原則）。"""
    begin_delivery()
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError, ValueError:
        return 0

    if not isinstance(payload, dict):
        return 0

    session_id = payload.get("session_id", "")
    if not isinstance(session_id, str) or not session_id:
        return 0

    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt:
        return 0

    set_warning_session_id(session_id)
    initial_notices: list[str] = []
    try:
        termination_evidence.observe_user(payload_text)
    except (OSError, ValueError, TypeError, KeyError) as error:
        initial_notices.append(
            _llm_notice(
                f"今回のユーザー発話を、作業終了時の確認に使う記録へ残せなかった: {error}",
                tag="warn",
                removable_cause=False,
                fix="会話の原文から現在の要求を確認して保持せよ。記録の診断は委譲元またはユーザーへ報告せよ。",
            )
        )

    self_check_notice = _progress_self_check.claim_notice(payload)

    # 公式契約では`prompt`はユーザーの送信本文である。実装版2.1.221で観測した
    # `<task-notification>`通知がユーザー発話として届く場合だけを防御的に除外し、一般的な入力契約とは扱わない。
    if _is_harness_message(prompt):
        harness_notices: list[str] = list(initial_notices)
        harness_notices.extend(command_timing.observe_notifications(payload))
        if not is_codex_payload(payload):
            # 完了通知のたびに同じ文面が出る定型の案内であり、ユーザーの画面へは写さない。
            harness_notices.append(
                informational(
                    _llm_notice(
                        "このターンは`Claude Code`のバックグラウンドタスク完了の自動通知で始まった。"
                        "ユーザーの発話ではない。"
                        "対処は`agent-toolkit:delegation`の`references/waiting-and-monitoring-intervals.md`"
                        "「完了通知で始まったターンの対処」に従え。",
                        tag="notice",
                    )
                )
            )
        if self_check_notice is not None:
            harness_notices.append(self_check_notice)
        if harness_notices := [notice for notice in harness_notices if notice]:
            _emit_hook_output(additional_context="\n".join(harness_notices))
        return 0

    machine_injected = _is_machine_injected(payload, prompt)
    is_codex = is_codex_payload(payload)
    first_line = prompt.split("\n", 1)[0].strip()
    command_prefix = "$" if is_codex else "/"
    # スラッシュコマンドで始まる発話もユーザー自身の入力であり、注記の対象に含める。
    # 除外するのは機械が生成してユーザー入力欄へ入る本文だけとする。
    is_normal_prompt = not machine_injected
    # 発火条件は受領側が除去できないため、いずれも情報提示の区分で配送する。
    notices: list[str] = list(initial_notices)
    if is_normal_prompt:
        try:
            contract_notice = work_contract.observe_input(session_id)
        except (OSError, ValueError) as error:
            notices.append(
                _llm_notice(
                    f"今回のユーザー発話と現行の作業契約との対応を記録できない: {error}",
                    tag="warn",
                    removable_cause=False,
                    fix="会話の原文から現在の要求を確認して保持せよ。記録の診断は委譲元またはユーザーへ報告せよ。",
                )
            )
        else:
            if contract_notice is not None:
                notices.append(_work_contract_notice(session_id, contract_notice))
        notices.extend(content_notices(prompt, "user_prompt_submit"))
        notices.append(_clock_notice(session_id))
    if self_check_notice is not None:
        notices.append(self_check_notice)
    additional_context = "\n".join(notice for notice in notices if notice) or None

    if first_line.startswith(command_prefix):
        match = _SKILL_COMMAND_PATTERN.match(first_line[len(command_prefix) :])
        if match is not None:
            name = match.group(1)
            full_name = f"agent-toolkit:{name}"
            session_title.observe_add_awi(session_id, full_name)
            if not is_codex:
                session_title.observe_skill(session_id, full_name, prompt[len(command_prefix) + match.end() :])

            # 対応スキル別にフラグを設定する。
            if (is_codex or is_antigravity_payload(payload)) and (
                name in _PLAN_MODE_NAMES_EXTENDED or full_name in _PLAN_MODE_SKILL_NAMES
            ):
                update_state(session_id, _set_plan_mode_invoked)
            if name in _PROCESS_WI_NAMES_EXTENDED or full_name in _PROCESS_WI_SKILL_NAMES:
                update_state(session_id, _set_process_wi_invoked)

    title = None if is_codex else session_title.proposed_title(session_id, payload.get("session_title"))
    if additional_context is not None or title is not None:
        _emit_hook_output(additional_context=additional_context, title=title)

    return 0
