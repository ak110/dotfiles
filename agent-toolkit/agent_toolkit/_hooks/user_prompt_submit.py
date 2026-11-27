"""UserPromptSubmitでセッション状態と応答の案内を扱う。  pyfltr: allow-comment

ホスト別コマンド形式（Claude Codeは`/agent-toolkit:<name>`・`/<name>`、
Codexは`$agent-toolkit:<name>`・`$<name>`）で発話した手動のスキル起動を検出し、
対応するセッション状態フラグを立てる。
入口の起動（`/atk-mode <作業> [入力]`）は、作業の名前を持つスキルの手動起動として扱う。

検出対象スキルと対応フラグ:

- 入口の作業process-wi（`/atk-mode process-wi`）→ `process_wi_skill_invoked`

計画の工程の開始（`plan_mode_skill_invoked`）は、全ホストでPostToolUseが`atk run-script plan-create`の成功から記録する。

セッション名はUserPromptSubmitのコマンドhookが通常状態から設定する。

例示の語（「例えば」「たとえば」「例として」）を含む実ユーザー発話では、経過時間と状態によらず、
`agent-toolkit:atk-mode/playbooks/user-utterance.md`「原文の保持と取り込み」の所在を示し、
原文の対象と例示の関係を判断させる。調査を求める語（「確認」「調査」「見直」など）も含む発話では、
調査を分担するかの比較も求める。判定はコードブロック、インラインコードおよび
引用行を除いた本文の部分文字列の一致だけで行い、範囲語の有無は判定に含めない（範囲語は「など」「とか」のような
一般的な語で表され、条件に加えても発火がほとんど減らないため）。発話が範囲判断を要するかの判断は受領側に残し、
本フックは発話の意味を判断しない。
一定間隔ごとの進捗のセルフチェック通知（`progress_self_check`が判定する）は、
実ユーザー発話と機械注入ターンの両方で前回通知からの経過時間に基づいて付ける。
成立した注記を1つの`additionalContext`へまとめる。

発話ごとの初動（「発話の理解」）の案内は、指摘かどうかを受領側に判定させず、文脈ごとに最初の実ユーザー発話へ届ける。
時刻は毎回の案内に含めない。要る時は、受領側が現在時刻を`date`で、過去の送信や操作の時刻を会話の記録の記録時刻で観測する。
ユーザー発話向けの注記の対象は、自動的なプロンプトを除く全てのユーザー発話とする。ユーザー自身が入力した発話は、
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

from agent_toolkit._common.next_action import recover_from_failure
from agent_toolkit._hooks.notice import begin_delivery, emit_response, informational, resolve_notice, set_warning_session_id

# pylint: disable-next=wrong-import-position,import-error
from agent_toolkit._hooks.notice import formatter as _notice_formatter  # noqa: E402
from agent_toolkit._hooks.posttooluse import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    UTTERANCE_UNDERSTANDING_NOTICE_BODY,
    content_notices,
)
lazy from agent_toolkit._common import (
    automated_prompt,  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    skill_modes,  # noqa: E402  # pylint: disable=wrong-import-position,import-error
)
lazy from agent_toolkit._common.delegated_session import (
    is_delegated,  # noqa: E402  # pylint: disable=wrong-import-position,import-error
)
lazy from agent_toolkit._common.periodic_recheck import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    LEGACY_PERIODIC_RECHECK_MARKER,
    PERIODIC_RECHECK_MARKER,
)
lazy from agent_toolkit._common.session_state import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    set_process_wi_invoked,
    update_state,
)
lazy from agent_toolkit._hooks import command_timing, session_title, termination_evidence, work_contract
lazy from agent_toolkit._hooks import progress_self_check as _progress_self_check
lazy from agent_toolkit._hooks.host import is_antigravity_payload, is_codex_payload  # noqa: E402  # pylint: disable=wrong-import-position,import-error
lazy from agent_toolkit._hooks.pretooluse import intake_edit, user_facing_checks

_SKILL_COMMAND_PATTERN = re.compile(r"\A(?:agent-toolkit:)?([A-Za-z0-9][A-Za-z0-9_-]*)\b")
_HARNESS_MESSAGE_RE = re.compile(r"^\s*(?:\[SYSTEM NOTIFICATION - NOT USER INPUT\]\s*)?<task-notification\b")
_USER_PROMPT_SOURCE_KEY = "source"
_USER_PROMPT_SOURCE_USER = "user"
_llm_notice = _notice_formatter("user_prompt_submit")


def _is_harness_message(prompt: str) -> bool:
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


def _emit_hook_output(*, additional_context: str | None, title: str | None = None) -> None:
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

    return informational(_llm_notice(body))


def _utterance_notice() -> str:
    """発話への初動の案内を、同じ文脈（セッションの開始か会話の圧縮の後）で最初の実ユーザー入力にだけ届ける。

    同じ文面を入力ごとに届けると読み飛ばされやすくなり、警告を含む通知全体が軽く扱われた（2026年11月25日の分析）。
    初動が守られたかは、送達時の「分類」と「別案」の遮断（`bugfix_gate`・`pretooluse/dispatch.py`）が入力ごとに確かめる。
    重複抑止の記録は、文脈を失うSessionStartで`rules_context`が消すため、圧縮の後は再び届く。 pyfltr: allow-comment
    """
    return informational(_llm_notice(UTTERANCE_UNDERSTANDING_NOTICE_BODY))


def _entry_work_notice(session_id: str, full_name: str, arguments: str | None) -> str:
    resolve_notice(session_id, "user_prompt_submit", "_entry_work_notice")
    work = full_name.removeprefix("agent-toolkit:")
    mode, _ = skill_modes.split_mode(full_name, arguments)
    mode_notice = f"続く `{mode}` はモードの指定である。" if mode else ""
    return informational(
        _llm_notice(
            f"起動入力の先頭の語 `{work}` は作業の指定である。"
            f"`agent-toolkit:atk-mode/playbooks/{work}.md` を読み、その手順に従ってください。"
            f"{mode_notice}指定の後ろにある本文だけを入力として扱ってください。"
        )
    )


def main(payload_text: str) -> int:
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

    is_codex = is_codex_payload(payload)
    is_antigravity = is_antigravity_payload(payload)
    delegated = is_delegated(os.environ)
    is_normal_prompt = not _is_machine_injected(payload, prompt)
    command_text = prompt.lstrip(" \t")
    first_line = command_text.split("\n", 1)[0].strip()
    prefix = "$" if is_codex else "/"
    match = _SKILL_COMMAND_PATTERN.match(first_line[1:]) if first_line.startswith(prefix) else None
    typed_name = f"agent-toolkit:{match.group(1)}" if match is not None else ""
    arguments = command_text[1 + match.end() :] if match is not None else None
    full_name, arguments = skill_modes.resolve_work(typed_name, arguments) if not delegated else (typed_name, arguments)
    named_by_entry = full_name != typed_name
    work = full_name.removeprefix("agent-toolkit:")

    if is_codex:
        user_facing_checks.observe_async_policy_reply(session_id, prompt)

    set_warning_session_id(session_id)
    initial_notices: list[str] = []
    try:
        if is_normal_prompt and named_by_entry and (is_codex or is_antigravity) and not payload.get("agent_id"):
            termination_evidence.observe_user(payload_text, entry_work=work)
        else:
            termination_evidence.observe_user(payload_text)
    except (OSError, ValueError, TypeError, KeyError) as error:
        initial_notices.append(
            _llm_notice(
                f"今回のユーザー発話を、作業終了時の確認に使う記録へ残せなかった: {error}",
                tag="warn",
                removable_cause=False,
                fix=recover_from_failure(
                    component="発話の記録",
                    evidence="会話の原文と記録の診断",
                    first_step="原文から現在の要求を確認して保持する",
                    retry="発話を再送せず、欠けた記録だけを回復する",
                ),
            )
        )

    periodic_recheck = (
        not is_codex_payload(payload)
        and not payload.get("agent_id")
        and prompt.split("\n", 1)[0].strip() == PERIODIC_RECHECK_MARKER
    )
    self_check_notice = _progress_self_check.claim_notice(payload, periodic_recheck=periodic_recheck)

    # 公式契約では`prompt`はユーザーの送信本文である。実装版2.1.221で観測した
    # `<task-notification>`通知がユーザー発話として届く場合だけを防御的に除外し、一般的な入力契約とは扱わない。
    if _is_harness_message(prompt):
        harness_notices: list[str] = list(initial_notices)
        harness_notices.extend(command_timing.observe_notifications(payload))
        if not is_codex_payload(payload):
            harness_notices.append(
                informational(
                    _llm_notice(
                        "このターンは`Claude Code`のバックグラウンドタスク完了の自動通知で始まった。"
                        "ユーザーの発話ではない。"
                        "通知が示す保存出力と終了状態を回収し、未終了の担当には有効な待機か自動再開を保ってください。"
                        "前のターンの終了は、このターンのツール操作を禁じない。",
                        tag="notice",
                    )
                )
            )
        if self_check_notice is not None:
            harness_notices.append(self_check_notice)
        if harness_notices := [notice for notice in harness_notices if notice]:
            _emit_hook_output(additional_context="\n".join(harness_notices))
        return 0

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
                    fix=recover_from_failure(
                        component="作業契約の記録",
                        evidence="会話の原文と記録の診断",
                        first_step="原文から現在の要求を確認して保持する",
                        retry="発話を再送せず、欠けた記録だけを回復する",
                    ),
                )
            )
        else:
            if contract_notice is not None:
                notices.append(_work_contract_notice(session_id, contract_notice))
        notices.extend(content_notices(prompt, "user_prompt_submit"))
        notices.append(_utterance_notice())
        if (is_codex or is_antigravity) and (boundary := intake_edit.prompt_notice(payload, session_id)):
            notices.append(boundary)
    if self_check_notice is not None:
        notices.append(self_check_notice)

    if match is not None:
        if named_by_entry:
            session_title.observe_add_awi(session_id, full_name)
            if is_normal_prompt:
                notices.append(_entry_work_notice(session_id, full_name, arguments))
        if not is_codex:
            session_title.observe_skill(session_id, full_name, arguments)

        if named_by_entry and full_name == skill_modes.PROCESS_WI:
            update_state(session_id, set_process_wi_invoked)

    additional_context = "\n".join(notice for notice in notices if notice) or None
    title = None if is_codex else session_title.proposed_title(session_id, payload.get("session_title"))
    if additional_context is not None or title is not None:
        _emit_hook_output(additional_context=additional_context, title=title)

    return 0
