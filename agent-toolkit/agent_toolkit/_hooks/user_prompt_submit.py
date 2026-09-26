"""Claude Code・Codex plugin agent-toolkit: UserPromptSubmitセッション状態記録と応答契約注入。

ホスト別コマンド形式（Claude Codeは`/agent-toolkit:<name>`・`/<name>`、
Codexは`$agent-toolkit:<name>`・`$<name>`）でのスキル起動を検出し、
対応するセッション状態フラグを立てる。
既存のPostToolUse(Skill)経由の記録では捕捉できない手動起動を補完する。

検出対象スキルと対応フラグ:

- plan-mode → `plan_mode_skill_invoked`
- process-wi → `process_wi_skill_invoked`

Claude CodeのsessionTitleは、process-loop起動セッションでは`process-loop`、
process-wi手動起動セッションでは`process-wi`の固定値を優先する
（両条件が真の場合はprocess-loopを優先する）。
いずれにも該当しないセッションは計画ファイルのstemを一度だけ反映する。
固定値の判定は、当該呼び出しでのスキル起動フラグ更新の後に行う
（同一呼び出しで検出したスラッシュコマンド起動を、その場でsessionTitleへ反映するため）。

通常発話へ返す注記は照合注記の1種とする。照合の手順を本文へ持ち、
直前の通常発話からの経過時間が閾値以上の場合だけ`additionalContext`へ返す。

注記の対象は、自動的なプロンプトを除く全てのユーザー発話とする。ユーザー自身が入力した発話は、
スラッシュコマンド（Claude Codeは`/`、Codexは`$`）で始まるものも対象に含める。
機械注入ターンでは、照合注記を返さず経過時間の記録も更新しない。
機械注入ターンの判定入力は次の5系統とし、いずれかが成立したターンを対象とする。

1. payloadの`source`が存在し、値が`user`以外であること
2. `prompt`の1行目が`PERIODIC_RECHECK_MARKER`だけの行であること
3. 委譲先として起動されていること
4. `prompt`が`<task-notification`で始まること
5. `prompt`の1行目が`<agent-toolkit-auto-inserted`要素の開始タグを含むこと

例外時はfail-openで exit 0 を返す。
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import time

from agent_toolkit._common import automated_prompt  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from agent_toolkit._common.delegated_session import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    is_delegated,
)

# pylint: disable-next=wrong-import-position,import-error
from agent_toolkit._hooks import background_task_outputs as _background_task_outputs  # noqa: E402

# pylint: disable-next=wrong-import-position,import-error
from agent_toolkit._hooks.notice import formatter as _notice_formatter  # noqa: E402
from agent_toolkit._hooks.posttooluse import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    _PLAN_MODE_SKILL_NAMES,
    _PROCESS_WI_SKILL_NAMES,
)
from agent_toolkit._hooks.session_state import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    claim_session_title,
    read_state,
    update_state,
)
from agent_toolkit._hooks.tool_input import is_codex_payload  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from agent_toolkit._plan.locations import is_plan_main_file  # noqa: E402  # pylint: disable=wrong-import-position,import-error


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

# process-loop起動セッションであることを示す環境変数名（`autonomous_exit.py`と同じ）。
_ENV_PROCESS_LOOP_SESSION = "AGENT_TOOLKIT_PROCESS_LOOP_SESSION"
_LEGACY_ENV_PROCESS_LOOP_SESSION = "DOTFILES_AUTONOMOUS_EXIT_REQUIRED"

# ホスト判定後の手動コマンドから<name>を抽出する。
# 先頭記号の直後に`agent-toolkit:`prefixがある場合と無い場合の両方を許容する。
# スキル名として妥当な文字（英数・ハイフン・アンダースコア）のみを対象とする。
_SKILL_COMMAND_PATTERN = re.compile(r"\A(?:agent-toolkit:)?([A-Za-z0-9][A-Za-z0-9_-]*)\b")
_HARNESS_MESSAGE_RE = re.compile(r"^\s*<task-notification\b")
PERIODIC_RECHECK_MARKER = '<agent-toolkit-auto-inserted source="agent-toolkit/periodic-recheck" kind="periodic-recheck">'
"""定期再確認のpromptの1行目へ置く役割標識。

`agent-toolkit:delegation`の`references/claude-code-runtime.md`「Cronによる定期再確認」が
同じリテラルを持ち、装着するpromptの1行目をこの標識だけの行と定める。
"""
_USER_PROMPT_SOURCE_KEY = "source"
_USER_PROMPT_SOURCE_USER = "user"
_VERIFICATION_NOTICE_INTERVAL_SECONDS = 180.0
_LAST_USER_PROMPT_AT_KEY = "last_user_prompt_at"
_VERIFICATION_NOTICE_TAG = "notice"
_VERIFICATION_NOTICE_BODY = (
    "直前の発話から、当該発話が主張する事実と是正を求めている対象を列挙し、"
    "それぞれを現物（原文・実装・規範・実行結果）で照合してから応答する。"
    "照合できない場合は同意も変更もしない。"
    "いずれも含まないと判定した発話では、照合を要さないと判断して次の工程へ進む。"
    "稼働中の依頼がある場合は、元の依頼の目的と未完了工程を照合してから次に実行する工程を確定する。"
    "同じ論点で修正が続く場合は意図と要件への影響を照合し、確定できないときだけ確認経路へ送る。"
)
"""照合要求の注記の本文。

照合すべき対象は発話ごとに異なるため、対象の列挙を受領側の手順として本文に持たせる。
当該列挙をフック側の判定で代替しない。本フックの入力は発話本文だけであり、
規則による分類の誤りは、照合を最も要する発話で注記を無音のまま欠落させるためである。
"""
_llm_notice = _notice_formatter("agent-toolkit/user_prompt_submit")


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
    5. `prompt`の1行目が機械生成の本文を示す境界標識を含むこと

    Claude Code 2.1.274の時点で`source`は配送されないため、残る4系統で判定する。
    出所を判定入力に持たないと、機械が投入したターンが通常発話として処理され、
    実ユーザー発話が受け取るべき照合注記をそのターンが消費する。
    第5の系統は、常駐処理が子セッションの最初の入力として渡す本文を対象とする。
    生成側が付ける境界標識だけを判定入力とし、本文の文言を写した別の判定を持たない。
    """
    source = payload.get(_USER_PROMPT_SOURCE_KEY)
    if isinstance(source, str) and source and source != _USER_PROMPT_SOURCE_USER:
        return True
    if prompt.split("\n", 1)[0].strip() == PERIODIC_RECHECK_MARKER:
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


def _fixed_session_title(session_id: str) -> str | None:
    """process-loop起動またはprocess-wi手動起動セッションの固定sessionTitleを返す。

    両条件が真の場合はprocess-loopを優先する。計画ファイルstemと同じく、
    `claim_session_title`を介してセッションごとに一度だけ確定する。
    """
    if os.environ.get(_ENV_PROCESS_LOOP_SESSION) == "1" or os.environ.get(_LEGACY_ENV_PROCESS_LOOP_SESSION) == "1":
        title = "process-loop"
    elif read_state(session_id).get("process_wi_skill_invoked") is True:
        title = "process-wi"
    else:
        return None
    return title if claim_session_title(session_id, title) else None


def _plan_session_title(session_id: str) -> str | None:
    """計画ファイルのstemをClaude CodeのsessionTitleへ一度だけ反映する。"""
    raw_plan_path = read_state(session_id).get("current_plan_file_path")
    if not isinstance(raw_plan_path, str) or not raw_plan_path or not is_plan_main_file(raw_plan_path):
        return None
    plan_stem = pathlib.Path(raw_plan_path).stem
    if not plan_stem or not claim_session_title(session_id, plan_stem):
        return None
    return plan_stem


def _claim_verification_notice(session_id: str, now: float) -> bool:
    """通常発話の時刻を更新し、照合指示を注入する場合に真を返す。"""
    claimed = False

    def update_timestamp(state: dict) -> dict:
        nonlocal claimed
        previous = state.get(_LAST_USER_PROMPT_AT_KEY)
        if isinstance(previous, (int, float)) and not isinstance(previous, bool):
            claimed = now - previous >= _VERIFICATION_NOTICE_INTERVAL_SECONDS
        state[_LAST_USER_PROMPT_AT_KEY] = now
        return state

    update_state(session_id, update_timestamp)
    return claimed


def _emit_hook_output(*, session_title_output: str | None, additional_context: str | None) -> None:
    """UserPromptSubmitの値を持つ応答欄を1つのJSONとして出力する。"""
    hook_specific_output: dict[str, str] = {"hookEventName": "UserPromptSubmit"}
    if session_title_output is not None:
        hook_specific_output["sessionTitle"] = session_title_output
    if additional_context is not None:
        hook_specific_output["additionalContext"] = additional_context
    print(json.dumps({"hookSpecificOutput": hook_specific_output}, ensure_ascii=False))


def main(payload_text: str) -> int:
    """エントリポイント。終了コードは常に0（fail-open原則）。"""
    try:
        payload = json.loads(payload_text)
    except (json.JSONDecodeError, ValueError):
        return 0

    if not isinstance(payload, dict):
        return 0

    session_id = payload.get("session_id", "")
    if not isinstance(session_id, str) or not session_id:
        return 0

    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt:
        return 0

    # 公式契約では`prompt`はユーザーの送信本文である。実装版2.1.221で観測した
    # `<task-notification>`通知の混入経路だけを防御的に除外し、一般的な入力契約とは扱わない。
    if _is_harness_message(prompt):
        _background_task_outputs.consume_completed_task_outputs(session_id, prompt)
        return 0

    machine_injected = _is_machine_injected(payload, prompt)
    is_codex = "model" in payload or is_codex_payload(payload)
    first_line = prompt.split("\n", 1)[0].strip()
    command_prefix = "$" if is_codex else "/"
    # スラッシュコマンドで始まる発話もユーザー自身の入力であり、注記の対象に含める。
    # 除外するのは機械が生成してユーザー入力欄へ入る本文だけとする。
    is_normal_prompt = not machine_injected
    # 発火条件は受領側が除去できないため、いずれも是正を求める区分ではなく情報提示として配送する。
    notices: list[str] = []
    if is_normal_prompt and _claim_verification_notice(session_id, time.time()):
        notices.append(_llm_notice(_VERIFICATION_NOTICE_BODY, tag=_VERIFICATION_NOTICE_TAG))
    additional_context = "\n".join(notices) if notices else None

    if first_line.startswith(command_prefix):
        match = _SKILL_COMMAND_PATTERN.match(first_line[len(command_prefix) :])
        if match is not None:
            name = match.group(1)
            full_name = f"agent-toolkit:{name}"

            # 対応スキル別にフラグを設定する。sessionTitleの固定値判定より先に行い、
            # 当該呼び出しでの起動を同じ応答へ反映できるようにする。
            if name in _PLAN_MODE_NAMES_EXTENDED or full_name in _PLAN_MODE_SKILL_NAMES:
                update_state(session_id, _set_plan_mode_invoked)
            if name in _PROCESS_WI_NAMES_EXTENDED or full_name in _PROCESS_WI_SKILL_NAMES:
                update_state(session_id, _set_process_wi_invoked)

    # Claude CodeのUserPromptSubmitだけがsessionTitleを出力する。
    # Codexはスキル起動の状態記録だけを行い、sessionTitleを出力しない。
    session_title = None
    if not is_codex:
        session_title = _fixed_session_title(session_id)
        if session_title is None:
            session_title = _plan_session_title(session_id)

    if session_title is not None or additional_context is not None:
        _emit_hook_output(session_title_output=session_title, additional_context=additional_context)

    return 0
