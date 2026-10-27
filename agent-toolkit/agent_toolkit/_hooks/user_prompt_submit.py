"""Claude Code・Codex plugin agent-toolkit: UserPromptSubmitでセッション状態を記録し、応答の契約を注入する。

ホスト別コマンド形式（Claude Codeは`/agent-toolkit:<name>`・`/<name>`、
Codexは`$agent-toolkit:<name>`・`$<name>`）で発話した手動のスキル起動を検出し、
対応するセッション状態フラグを立てる。
既存のPostToolUse(Skill)経由の記録では捕捉できない手動起動を補完する。

検出対象スキルと対応フラグ:

- Codexのplan-mode → `plan_mode_skill_invoked`
- process-wi → `process_wi_skill_invoked`

セッション名はUserPromptSubmitのコマンドhookが通常状態から設定する。

セッション開始後と会話圧縮後で`agent-toolkit:user-confirmation-and-report`がまだ起動されていない間は、
最初の実ユーザー発話へ同スキルの起動を促す注記を一度返す。
発話の内容を現物で確かめる一般手順は同スキルが定め、経過時間による固定本文の再配送は行わない。
全角の連続した感嘆符を含む実ユーザー発話では、経過時間によらず`agent-toolkit:user-confirmation-and-report`の「認識合わせ」を促す。
例示の語（「例えば」「たとえば」「例として」）と調査を求める語（「確認」「調査」「見直」など）を併せ持つ
実ユーザー発話では、経過時間と状態によらず、`agent-toolkit:user-confirmation-and-report`の
`SKILL.md`「原文の保持と取り込み」の範囲語か開放列挙と例示を併せた調査依頼の項と、
`agent-toolkit:delegation`の`references/routing.md`の所在を示す。判定はコードブロック、インラインコードおよび
引用行を除いた本文の部分文字列の一致だけで行い、範囲語の有無は判定に含めない（範囲語は「など」「とか」のような
一般的な語で表され、条件に加えても発火がほとんど減らないため）。発話がその項に当たるかの判断は受領側に残し、
本フックは発話の意味を判断しない。
一定間隔ごとの進捗のセルフチェック通知（`progress_self_check`が判定する）は、機械注入ターンを含む全ての発話に付ける。
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
import sys

# pylint: disable-next=wrong-import-position,import-error
from agent_toolkit._hooks.notice import formatter as _notice_formatter  # noqa: E402
from agent_toolkit._hooks.posttooluse import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    _PLAN_MODE_SKILL_NAMES,
    _PROCESS_WI_SKILL_NAMES,
    USER_CONFIRMATION_PENDING_KEY,
    USER_CONFIRMATION_SKILL_NAMES,
    clear_user_confirmation_pending,
    content_notices,
)
lazy from agent_toolkit._common import (
    automated_prompt,  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    next_action,
)
lazy from agent_toolkit._common.delegated_session import (
    is_delegated,  # noqa: E402  # pylint: disable=wrong-import-position,import-error
)
lazy from agent_toolkit._common.periodic_recheck import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    LEGACY_PERIODIC_RECHECK_MARKER,
    PERIODIC_RECHECK_MARKER,
)
lazy from agent_toolkit._common.session_state import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    read_state,
    update_state,
)
lazy from agent_toolkit._hooks import message_format, session_title, termination_evidence, work_contract
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
_USER_CONFIRMATION_NOTICE_BODY = (
    "この発話は、セッション開始後か会話圧縮後で、`agent-toolkit:user-confirmation-and-report`の内容が文脈に無い状態で届いた。"
)
_USER_CONFIRMATION_NOTICE_FIX = (
    "応答と作業の着手より前に`agent-toolkit:user-confirmation-and-report`をスキル機能で起動せよ。"
    "是正の認可は`agent-toolkit:user-confirmation-and-report`の「起動契機」から「認可を要する操作」へ進んで判定せよ。"
)
_BUGFIX_NOTICE_FIX = (
    "不具合の有無・原因・直し方を回答や確認の案へ含める場合は、診断より前に`agent-toolkit:bugfix`を起動せよ。"
    "説明の補足や委譲・AWIの方針も対象とする。"
)


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


def _claim_user_confirmation_notice(session_id: str) -> bool:
    """`agent-toolkit:user-confirmation-and-report`の起動を促す注記を返す場合に真を返す。

    SessionStartが記録した配送待ちを消費する。両ホストで文脈1つにつき一度返し、
    取得や起動の不足は関連操作のread_deliveryとスキルの起動契機で扱う。
    """
    if read_state(session_id).get(USER_CONFIRMATION_PENDING_KEY) is not True:
        return False
    update_state(session_id, clear_user_confirmation_pending)
    return True


def _user_confirmation_notice() -> str:
    """文脈ごとの既存pending状態で一度だけ選んだ案内を共通の書式へ渡す。"""
    body = next_action.with_next_action(_USER_CONFIRMATION_NOTICE_BODY, _USER_CONFIRMATION_NOTICE_FIX + _BUGFIX_NOTICE_FIX)
    return message_format.llm_notice(body, "user_prompt_submit", tag="notice")


def _emit_hook_output(*, additional_context: str | None, title: str | None = None) -> None:
    """UserPromptSubmitの値を持つ応答欄を1つのJSONとして出力する。"""
    hook_specific_output: dict[str, str] = {"hookEventName": "UserPromptSubmit"}
    if additional_context is not None:
        hook_specific_output["additionalContext"] = additional_context
    if title is not None:
        hook_specific_output["sessionTitle"] = title
    print(json.dumps({"hookSpecificOutput": hook_specific_output}, ensure_ascii=False))


def main(payload_text: str) -> int:
    """エントリポイント。終了コードは常に0（fail-open原則）。"""
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

    try:
        termination_evidence.observe_user(payload_text)
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(f"終了工程の原入力を取得できない: {error}", file=sys.stderr)

    # 進捗のセルフチェックは発話の出所によらず経過時間だけで判定し、機械注入ターンにも付ける。
    self_check_notice = _progress_self_check.claim_notice(payload)

    # 公式契約では`prompt`はユーザーの送信本文である。実装版2.1.221で観測した
    # `<task-notification>`通知がユーザー発話として届く場合だけを防御的に除外し、一般的な入力契約とは扱わない。
    if _is_harness_message(prompt):
        harness_notices: list[str] = []
        if not is_codex_payload(payload):
            harness_notices.append(
                _llm_notice(
                    "このターンは`Claude Code`のバックグラウンドタスク完了の自動通知で始まった。"
                    "ユーザーの発話ではない。応答と成果物は日本語で書け。",
                    tag="notice",
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
    notices: list[str] = []
    if is_normal_prompt and first_line.startswith(command_prefix):
        command = first_line[len(command_prefix) :].split(maxsplit=1)
        if command and command[0] in USER_CONFIRMATION_SKILL_NAMES:
            # ユーザー自身がスキルを起動した発話では、その起動で内容が文脈へ入る。
            update_state(session_id, clear_user_confirmation_pending)
    if is_normal_prompt and _claim_user_confirmation_notice(session_id):
        notices.append(_user_confirmation_notice())
    if is_normal_prompt:
        try:
            contract_notice = work_contract.observe_input(session_id)
        except (OSError, ValueError) as error:
            print(f"現行の作業契約の入力位置を記録できない: {error}", file=sys.stderr)
        else:
            if contract_notice is not None:
                notices.append(_llm_notice(contract_notice))
        notices.extend(content_notices(prompt, "user_prompt_submit"))
    if self_check_notice is not None:
        notices.append(self_check_notice)
    additional_context = "\n".join(notice for notice in notices if notice) or None

    if first_line.startswith(command_prefix):
        match = _SKILL_COMMAND_PATTERN.match(first_line[len(command_prefix) :])
        if match is not None:
            name = match.group(1)
            full_name = f"agent-toolkit:{name}"
            if not is_codex:
                session_title.observe_skill(session_id, full_name)

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
