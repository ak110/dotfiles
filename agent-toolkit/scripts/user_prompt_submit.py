#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["platformdirs>=4.0"]
# ///
"""Claude Code plugin agent-toolkit: UserPromptSubmit セッション状態記録。

スラッシュコマンド形式（`/agent-toolkit:<name>`または`/<name>`）でのスキル起動を検出し、
対応するセッション状態フラグを立てる。
既存のPostToolUse(Skill)経由の記録では捕捉できないケース（スラッシュコマンド起動）を補完する。

検出対象スキルと対応フラグ:

- plan-mode → `plan_mode_skill_invoked`
- session-review → `session_review_invoked`（辞書。キーは`agent-toolkit:session-review`で正規化）
- process-feedbacks → `process_feedbacks_skill_invoked`
- plan-and-add-feedback → `plan_and_add_feedback_skill_invoked`

加えて、非スラッシュコマンド入力（先頭行が`/`で始まらない発話）に対しては
規範照会・是正要求の兆候（`_norm_inquiry_escalation.py`の`_match_norm_inquiry_escalation`）を検出し、
クールダウン判定を経て`hookSpecificOutput.additionalContext`でメタ視点点検・恒久化検討の実施を促す
リマインダーを注入する（`agent-toolkit/rules/02-collaboration.md`「メタ視点」バレット項の対象事象）。

例外時はfail-openで exit 0 を返す。
"""

from __future__ import annotations

import json
import pathlib
import re
import sys
import traceback

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from _norm_inquiry_escalation import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    _match_norm_inquiry_escalation,
)
from _session_state import update_state  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from posttooluse import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    _PLAN_AND_ADD_FEEDBACK_SKILL_NAMES,
    _PLAN_MODE_SKILL_NAMES,
    _PROCESS_FEEDBACKS_SKILL_NAMES,
    _SESSION_REVIEW_SKILL_NAMES,
)

# `agent-toolkit/skills/agent-standards/references/claude-hooks.md`
# 「コーディングエージェント宛てメッセージの標識」節が定める自動生成標識。
_MESSAGE_PREFIX = "[auto-generated: agent-toolkit/user_prompt_submit]"
_MESSAGE_SUFFIX = "(Auto-generated hook notice; evaluate relevance against the conversation context before acting.)"

# クールダウン間隔（UserPromptSubmit呼び出し回数）。
# 同一指摘対応の作業中に再注入しない一方、別トピックの指摘では再注入され得る目安値。
_NORM_INQUIRY_COOLDOWN_TURNS = 5

_NORM_INQUIRY_REMINDER_BODY = (
    "Norm-inquiry or correction-request phrasing was detected in the user's message. "
    "Per agent-toolkit/rules/02-collaboration.md's meta-perspective checkpoint, inspect why the "
    "existing norm failed to prevent this, and drive the permanent-fix decision (adopt/reject with "
    "reason) to completion within this session instead of only logging a one-line report."
)


def _extend_with_short_names(names: frozenset[str]) -> frozenset[str]:
    """フルスキル名`agent-toolkit:<name>`から短縮名`<name>`を追加した拡張集合を返す。

    posttooluse.py側の集合定数には短縮名が未登録のスキル
    （session-review）が存在するため、
    UserPromptSubmit経路のスラッシュコマンド検出用に補完する。
    """
    extended = set(names)
    for name in names:
        if ":" in name:
            _, short = name.split(":", 1)
            if short:
                extended.add(short)
    return frozenset(extended)


# スラッシュコマンド起動時にも検出できるように、フルネームと短縮名の両方を含む拡張集合を組み立てる。
_PLAN_MODE_NAMES_EXTENDED = _extend_with_short_names(_PLAN_MODE_SKILL_NAMES)
_SESSION_REVIEW_NAMES_EXTENDED = _extend_with_short_names(_SESSION_REVIEW_SKILL_NAMES)
_PROCESS_FEEDBACKS_NAMES_EXTENDED = _extend_with_short_names(_PROCESS_FEEDBACKS_SKILL_NAMES)
_PLAN_AND_ADD_FEEDBACK_NAMES_EXTENDED = _extend_with_short_names(_PLAN_AND_ADD_FEEDBACK_SKILL_NAMES)

# `/agent-toolkit:<name>`または`/<name>`形式のスラッシュコマンドから<name>を抽出する。
# 先頭の`/`直後に`agent-toolkit:`prefixがある場合と無い場合の両方を許容する。
# スキル名として妥当な文字（英数・ハイフン・アンダースコア）のみを対象とする。
_SLASH_COMMAND_PATTERN = re.compile(r"\A/(?:agent-toolkit:)?([A-Za-z0-9][A-Za-z0-9_-]*)\b")


def _resolve_canonical_name(name: str, extended: frozenset[str], canonical: frozenset[str]) -> str | None:
    """<name>が拡張集合に含まれる場合、正規名（フルスキル名優先）を返す。

    セッション状態フラグの辞書キー正規化に使う。
    フルスキル名が候補にあればそれを、無ければ短縮名をそのまま返す。
    """
    if name not in extended:
        return None
    for candidate in canonical:
        if candidate == name or candidate.endswith(":" + name):
            return candidate
    return name


def _set_plan_mode_invoked(state: dict) -> dict | None:
    if state.get("plan_mode_skill_invoked", False):
        return None
    state["plan_mode_skill_invoked"] = True
    return state


def _make_session_review_mutator(canonical_name: str):
    def _mutator(state: dict) -> dict | None:
        invoked = state.get("session_review_invoked")
        if not isinstance(invoked, dict):
            invoked = {}
        if invoked.get(canonical_name) is True:
            return None
        invoked[canonical_name] = True
        state["session_review_invoked"] = invoked
        return state

    return _mutator


def _set_process_feedbacks_invoked(state: dict) -> dict | None:
    if state.get("process_feedbacks_skill_invoked", False):
        return None
    state["process_feedbacks_skill_invoked"] = True
    return state


def _set_plan_and_add_feedback_invoked(state: dict) -> dict | None:
    if state.get("plan_and_add_feedback_skill_invoked", False):
        return None
    state["plan_and_add_feedback_skill_invoked"] = True
    return state


def _record_norm_inquiry_detection(session_id: str, detected: bool) -> bool:
    """カウンターを加算し、検出時のクールダウン判定を行う。

    UserPromptSubmitの呼び出しごとに単調増加カウンター`user_prompt_counter`を加算する。
    `detected`が真の場合、現在カウンターと`norm_inquiry_last_injected`の記録値
    （キー不在時は`0`とみなす）の差が`_NORM_INQUIRY_COOLDOWN_TURNS`以上であれば
    注入対象と判定し、現在値を`norm_inquiry_last_injected`へ記録する。
    戻り値は注入対象と判定したかどうか。
    """
    outcome = {"should_inject": False}

    def _mutator(state: dict) -> dict | None:
        counter_raw = state.get("user_prompt_counter", 0)
        counter = counter_raw if isinstance(counter_raw, int) else 0
        counter += 1
        state["user_prompt_counter"] = counter
        if detected:
            last_raw = state.get("norm_inquiry_last_injected", 0)
            last = last_raw if isinstance(last_raw, int) else 0
            if counter - last >= _NORM_INQUIRY_COOLDOWN_TURNS:
                state["norm_inquiry_last_injected"] = counter
                outcome["should_inject"] = True
        return state

    update_state(session_id, _mutator)
    return outcome["should_inject"]


def _emit_norm_inquiry_reminder() -> None:
    """規範照会・是正要求検出時のリマインダーを`hookSpecificOutput`として標準出力へ書く。"""
    body = f"{_MESSAGE_PREFIX} {_NORM_INQUIRY_REMINDER_BODY} {_MESSAGE_SUFFIX}"
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": body,
                }
            },
            ensure_ascii=False,
        )
    )


def main() -> int:
    """エントリポイント。終了コードは常に0（fail-open原則）。"""
    try:
        payload = json.loads(sys.stdin.read())
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

    # 先頭行のみを取り出して照合する（先頭行以外は無視）。
    first_line = prompt.split("\n", 1)[0].strip()
    if not first_line.startswith("/"):
        # 規範照会・是正要求の検出処理。局所的に例外を捕捉し、失敗時も
        # fail-open原則（exit 0・追加出力なし）で既存の非スラッシュ入力の扱いを維持する。
        try:
            match_result = _match_norm_inquiry_escalation(prompt)
            should_inject = _record_norm_inquiry_detection(session_id, match_result is not None)
            if should_inject:
                _emit_norm_inquiry_reminder()
        except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught  # hook fail-open原則
            pass
        return 0

    match = _SLASH_COMMAND_PATTERN.match(first_line)
    if match is None:
        return 0

    name = match.group(1)
    full_name = f"agent-toolkit:{name}"

    # 対応スキル別にフラグを設定する。
    if name in _PLAN_MODE_NAMES_EXTENDED or full_name in _PLAN_MODE_SKILL_NAMES:
        update_state(session_id, _set_plan_mode_invoked)
    if name in _SESSION_REVIEW_NAMES_EXTENDED or full_name in _SESSION_REVIEW_SKILL_NAMES:
        canonical = _resolve_canonical_name(name, _SESSION_REVIEW_NAMES_EXTENDED, _SESSION_REVIEW_SKILL_NAMES) or full_name
        update_state(session_id, _make_session_review_mutator(canonical))
    if name in _PROCESS_FEEDBACKS_NAMES_EXTENDED or full_name in _PROCESS_FEEDBACKS_SKILL_NAMES:
        update_state(session_id, _set_process_feedbacks_invoked)
    if name in _PLAN_AND_ADD_FEEDBACK_NAMES_EXTENDED or full_name in _PLAN_AND_ADD_FEEDBACK_SKILL_NAMES:
        update_state(session_id, _set_plan_and_add_feedback_invoked)

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught  # hook fail-open原則
        traceback.print_exc(file=sys.stderr)
        sys.exit(0)
