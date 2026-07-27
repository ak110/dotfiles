"""SubagentStop hook: 完了報告の本文を空/Skill単独報告と縮退表明辞書で検査する。

公式仕様の`last_assistant_message`を直参照し、
当該サブエージェント自身の`transcript_path`に未消化のbackground起動（`has_pending_background_launches`）が
構造的に実在する場合は、完了報告本文の内容によらず無条件で承認する。まだ自身配下の作業が
構造的に残っている以上、続行の是非を本文の言い回しで判定する必要が無いためである。
未消化のbackground起動が無い場合に限り、`is_empty_completion_report`で実質空またはSkill呼び出し
単独の構造的欠落を検出し、続いて`_STOP_FOCUS_CATEGORIES_EXTENDED`と同一SSOTで縮退表明フレーズを照合する。
`stop_hook_active`真の再呼び出し時は判定処理をせず無条件approveを返し、
連続ブロック上限による強制終了を回避する。

named subagent（`agent_name`非空）でtranscript内のtool_use数が閾値以上ある場合、
起動元宛のSendMessage送付履歴（`name == "SendMessage"`かつ`input.to`が非空文字列。
宛先の具体的な識別子は起動プロンプト依存のため`to`の値は問わない）が
無い時にblockを返し、完了報告の能動送付を促す。
当該判定結果は`agent_name`・tool_use数・送付有無を含めて`append_stop_log`で常時ログ化する。

Explore named background起動（`posttooluse.py`が記録した名前リストに`agent_name`が
含まれる場合）は、`_NAMED_SUBAGENT_MIN_TOOL_USES`閾値を適用せず同一水準の能動送付検査を行う
（`_inspect_explore_named_background_send`。Explore短命終了時の未発火事象を解消するため）。

`plan-impl-executor`完了報告（`transcript_path`から抽出した`agentId`が
`plan_impl_executor_active_subagent_sessions`辞書のキーと一致する場合のみ発火）は、
主要欄ラベルの欠落検査と、background並列起動宣言・`changed`欄未消化項目の矛盾検査（FB[3]）を行う。
書式不備・矛盾を検出しblockした場合はエントリを保持し、是正後の再試行でも検査を再発火させる。
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from _message_format import llm_notice as _llm_notice_base  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _scope_escalation import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    _STOP_FOCUS_CATEGORIES_EXTENDED,
    _match_scope_escalation,
    is_empty_completion_report,
)
from _session_state import read_state, update_state  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _stop_gate import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    append_stop_log,
    has_pending_background_launches,
)

_HOOK_ID = "agent-toolkit/subagent-stop"

# `posttooluse.py`の同名定数と同一集合を保つ。
_PLAN_IMPL_EXECUTOR_ACTIVE_KEY = "plan_impl_executor_active_subagent_sessions"
# `transcript_path`のファイル名（`agent-<agentId>.jsonl`形式。Claude Codeがサブエージェント
# セッションごとに採番する`agentId`を含む。`posttooluse.py`はこの`agentId`を
# `plan_impl_executor_active_subagent_sessions`辞書のキーとして登録するのみで、
# ファイル名自体は生成しない）からファイル名先頭一致で`agentId`を抽出する正規表現。
_TRANSCRIPT_AGENT_ID_RE = re.compile(r"^agent-([^/\\]+)\.jsonl$")

# Explore named background起動時の`name`集合を記録する状態辞書キー名。
# `posttooluse.py`の同名定数と同一値を保つ。
_EXPLORE_NAMED_BACKGROUND_ACTIVE_KEY = "explore_named_background_active_names"

# `plan-impl-executor`完了報告本文の主要欄ラベル集合。
# SSOTは`agent-toolkit/references/plan-impl/caller-reception.md`手順0および
# `agent-toolkit/agents/plan-impl-executor.md`「出力」節。
# ラベル定義変更時は本定数と両ファイルを同時に更新する。
_PLAN_IMPL_EXECUTOR_REQUIRED_LABELS: tuple[str, ...] = (
    "status",
    "summary",
    "changed",
    "verification",
    "commit_sha",
    "review_handoff",
    "pending_confirmations",
    "plan_gaps",
    "applied_instructions",
)
_PLAN_IMPL_EXECUTOR_NEEDS_ESCALATION_LABEL = "blockers"
_PLAN_IMPL_EXECUTOR_NEEDS_ESCALATION_RE = re.compile(r"^status:\s*needs_escalation\b", re.MULTILINE)

# `plan-impl-executor`が自身の判断でbackground並列起動した宣言と、
# `changed`欄の未消化項目（`- [ ]`）が共起するかの判定パターン（FB[3]）。
# `plan-impl-executor.md`「停止禁止」節が禁止するbackground並列起動の再発検出用。
_PLAN_IMPL_EXECUTOR_BACKGROUND_LAUNCH_RE = re.compile(r"run_in_background\s*=\s*true|バックグラウンドで?並列起動")
_PLAN_IMPL_EXECUTOR_UNCHECKED_CHANGED_ITEM_RE = re.compile(r"^-\s*\[\s\]", re.MULTILINE)
_PLAN_IMPL_EXECUTOR_STATUS_COMPLETED_RE = re.compile(r"^status:\s*completed\b", re.MULTILINE)

# `changed:`欄本文（次の主要ラベル行直前まで）を抽出する境界パターン（FB[3]）。
# `_PLAN_IMPL_EXECUTOR_REQUIRED_LABELS`・`_PLAN_IMPL_EXECUTOR_NEEDS_ESCALATION_LABEL`と同じラベル集合を
# 境界として使い、`verification`・`blockers`等の他欄に含まれるチェックボックス様の記述を誤検出しない。
_PLAN_IMPL_EXECUTOR_ALL_LABELS: tuple[str, ...] = _PLAN_IMPL_EXECUTOR_REQUIRED_LABELS + (
    _PLAN_IMPL_EXECUTOR_NEEDS_ESCALATION_LABEL,
)
_PLAN_IMPL_EXECUTOR_CHANGED_SECTION_RE = re.compile(
    r"^changed:\s*\n((?:(?!^(?:" + "|".join(re.escape(label) for label in _PLAN_IMPL_EXECUTOR_ALL_LABELS) + r"):).*\n?)*)",
    re.MULTILINE,
)


def _extract_changed_section_body(text: str) -> str:
    """完了報告本文の`changed:`欄本文（次の主要ラベル行直前まで）を抽出する（FB[3]）。

    `changed:`欄が存在しない場合は空文字列を返す。
    """
    match = _PLAN_IMPL_EXECUTOR_CHANGED_SECTION_RE.search(text)
    return match.group(1) if match else ""


def _detect_plan_impl_executor_background_parallel_violation(text: str) -> bool:
    """`plan-impl-executor`完了報告のbackground並列起動宣言と`changed`欄未消化項目の共起を検出する（FB[3]）。

    `status: completed`かつ`run_in_background=true`相当の宣言があり、
    `changed`欄本文に限定して未チェック項目（`- [ ]`）が残る場合を違反として`True`を返す。
    `changed`欄本文への限定は`verification`・`blockers`等の他欄に現れるチェックボックス様の
    記述による誤検出を防ぐため。
    """
    if not _PLAN_IMPL_EXECUTOR_STATUS_COMPLETED_RE.search(text):
        return False
    if not _PLAN_IMPL_EXECUTOR_BACKGROUND_LAUNCH_RE.search(text):
        return False
    changed_body = _extract_changed_section_body(text)
    return bool(_PLAN_IMPL_EXECUTOR_UNCHECKED_CHANGED_ITEM_RE.search(changed_body))


# tool_use数がこの閾値未満のnamed subagentは短命扱いで送信検査対象外とする。
# 起動直後のOSエラー・単一ツール失敗など、能動送付を求めるほど作業が進んでいない
# ケースの誤検出を防ぐため、経験則的な下限として設定する。
_NAMED_SUBAGENT_MIN_TOOL_USES = 3


def _llm_notice(body: str, *, tag: str = "") -> str:
    """LLM宛て通知メッセージを標準プレフィックス付きで整形する。"""
    return _llm_notice_base(body, _HOOK_ID, tag=tag)


@dataclasses.dataclass(frozen=True)
class _NamedSubagentSendCheck:
    """`_inspect_named_subagent_send`の判定内訳。"""

    agent_name: str
    tool_use_count: int
    has_main_send: bool
    missing_main_send: bool


def _fail_open_check(agent_name: str) -> _NamedSubagentSendCheck:
    """判定不能（`agent_name`未指定・transcript読み取り不能）時のfail-open結果を返す。"""
    return _NamedSubagentSendCheck(agent_name=agent_name, tool_use_count=-1, has_main_send=False, missing_main_send=False)


def _scan_transcript_tool_uses(transcript_path: str) -> tuple[int, bool] | None:
    """transcriptを走査し`(tool_use総数, 起動元宛SendMessage送付有無)`を返す。

    宛先識別子は起動プロンプトで指定された任意の値を取り得るため、`to`が非空文字列であれば
    送付済みと判定する（`main`固定判定は起動元が`main`以外の中間層である場合を検出できないため）。
    読み取り不能時は`None`を返す（fail-open判定は呼び出し元に委ねる）。
    `_inspect_named_subagent_send`・`_inspect_explore_named_background_send`が共用する。
    """
    try:
        raw = pathlib.Path(transcript_path).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    tool_use_count = 0
    sent_completion_report = False
    for line in raw.splitlines():
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if entry.get("type") != "assistant":
            continue
        message = entry.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            tool_use_count += 1
            if block.get("name") == "SendMessage":
                inp = block.get("input")
                if isinstance(inp, dict) and isinstance(inp.get("to"), str) and inp.get("to"):
                    sent_completion_report = True
    return tool_use_count, sent_completion_report


def _inspect_named_subagent_send(payload: dict) -> _NamedSubagentSendCheck:
    """Named subagentの起動元宛SendMessage送付有無を判定内訳付きで返す。

    判定条件:
    - `agent_name`フィールドが非空文字列（named subagent起動）
    - `transcript_path`が読み取り可能
    - 当該subagentのtranscript内assistant `tool_use`ブロック総数が閾値以上
    - `name == "SendMessage"`かつ`input.to`が非空文字列のtool_use呼び出しが1件も存在しない
      （宛先識別子は起動プロンプト依存のため値は問わない）

    `missing_main_send`は上記全てを満たす場合に真。foregroundの短命subagent等で
    tool_use数が閾値未満の場合、または既に起動元宛SendMessage送付済みの場合は偽。
    `agent_name`未指定・transcript読み取り失敗時は`tool_use_count=-1`・`missing_main_send=False`
    で返す（fail-open）。
    """
    agent_name = payload.get("agent_name")
    agent_name = agent_name if isinstance(agent_name, str) else ""
    if not agent_name:
        return _fail_open_check("")
    transcript_path = payload.get("transcript_path")
    if not isinstance(transcript_path, str) or not transcript_path:
        return _fail_open_check(agent_name)
    scan_result = _scan_transcript_tool_uses(transcript_path)
    if scan_result is None:
        return _fail_open_check(agent_name)
    tool_use_count, sent_to_main = scan_result
    missing_main_send = tool_use_count >= _NAMED_SUBAGENT_MIN_TOOL_USES and not sent_to_main
    return _NamedSubagentSendCheck(
        agent_name=agent_name, tool_use_count=tool_use_count, has_main_send=sent_to_main, missing_main_send=missing_main_send
    )


def _log_send_check(session_id: object, check: _NamedSubagentSendCheck, *, label_stem: str) -> None:
    """Named subagent送付判定結果を常時ログへ1行追記する。

    `_inspect_named_subagent_send`・`_inspect_explore_named_background_send`が共用する。
    `decision`は`missing_main_send`が真なら`block_{label_stem}_missing_send`、
    偽なら`allow_{label_stem}_send`（`stop_advisor.py`の複合ラベル命名規約に揃える）。
    `session_id`が非文字列・空文字列の場合は`append_stop_log`の既定挙動でスキップする。
    """
    append_stop_log(
        session_id if isinstance(session_id, str) else "",
        f"block_{label_stem}_missing_send" if check.missing_main_send else f"allow_{label_stem}_send",
        {
            "agent_name": check.agent_name or "-",
            "tool_use_count": check.tool_use_count,
            "has_main_send": check.has_main_send,
        },
    )


# 判定内訳のフィールド構造が`_NamedSubagentSendCheck`と完全一致するため型エイリアスとして統合する
# （dataclass二重定義とlogger二重定義のSRP違反を解消）。
_ExploreNamedBackgroundSendCheck = _NamedSubagentSendCheck


def _inspect_explore_named_background_send(payload: dict) -> _ExploreNamedBackgroundSendCheck | None:
    """登録済みExplore named background起動のメイン宛SendMessage送付有無を判定する。

    判定条件:
    - `session_id`・`agent_name`が非空文字列
    - `posttooluse.py`が記録した`_EXPLORE_NAMED_BACKGROUND_ACTIVE_KEY`名前リストに`agent_name`を含む
      （含まない場合は本ゲートの対象外として`None`を返す。`_inspect_named_subagent_send`と異なり
      `_NAMED_SUBAGENT_MIN_TOOL_USES`閾値は適用しない。Explore短命終了時の未発火事象を解消するため）

    一致した場合は`transcript_path`をtool_use走査する。
    走査成功時のみ該当名を状態から1件消費（削除）し、判定結果を確定させる。
    走査失敗時（`transcript_path`欠落・読み取り不能）は状態を変更せず、fail-open（非block）で返す。
    エントリを未消費のまま残すことで、後続のSubagentStop再発火時に再判定できる
    （codexレビュー指摘: 走査失敗時の消費により再試行不能になる不備を是正する）。

    同名の並行起動を複数登録した場合、消費対象の同定は`agent_name`一致のみに依拠し、
    どの物理起動に対応するかは区別しない（`list.remove`は先頭一致1件を消費する）。
    これは既知の受容済み制約とする。理由: `SendMessage(to=<name>)`によるteammate宛送付も
    同名衝突時は宛先解決が本質的に曖昧であり、運用上は同名重複起動を避けることが前提となる。
    `session_id`・`agent_name`未指定時、または登録名前リストに`agent_name`が含まれない場合は`None`を返す
    （本ゲートの対象外を意味し、`main()`側は判定・ログ出力をスキップする）。
    """
    session_id = payload.get("session_id")
    agent_name = payload.get("agent_name")
    if not isinstance(session_id, str) or not session_id:
        return None
    if not isinstance(agent_name, str) or not agent_name:
        return None
    state = read_state(session_id)
    active = state.get(_EXPLORE_NAMED_BACKGROUND_ACTIVE_KEY)
    if not isinstance(active, list) or agent_name not in active:
        return None

    transcript_path = payload.get("transcript_path")
    scan_result = _scan_transcript_tool_uses(transcript_path) if isinstance(transcript_path, str) and transcript_path else None

    if scan_result is None:
        return _ExploreNamedBackgroundSendCheck(
            agent_name=agent_name, tool_use_count=-1, has_main_send=False, missing_main_send=False
        )

    def _consume_entry(current_state: dict, name: str = agent_name) -> dict | None:
        current_active = current_state.get(_EXPLORE_NAMED_BACKGROUND_ACTIVE_KEY)
        if not isinstance(current_active, list) or name not in current_active:
            return None
        current_active.remove(name)
        current_state[_EXPLORE_NAMED_BACKGROUND_ACTIVE_KEY] = current_active
        return current_state

    update_state(session_id, _consume_entry)

    tool_use_count, sent_to_main = scan_result
    return _ExploreNamedBackgroundSendCheck(
        agent_name=agent_name,
        tool_use_count=tool_use_count,
        has_main_send=sent_to_main,
        missing_main_send=not sent_to_main,
    )


def _extract_transcript_agent_id(transcript_path: object) -> str | None:
    """`transcript_path`のファイル名から`agentId`（`agent-<id>.jsonl`のid部分）を抽出する。

    ファイル名の先頭（`os.path.basename`相当）からの一致のみを許可し、
    `not-agent-alpha.jsonl`のような文字列中の部分一致による誤抽出を防ぐ。
    `posttooluse.py`が`plan_impl_executor_active_subagent_sessions`辞書へ登録する
    `tool_response["agentId"]`と同一の値を、停止した当のサブエージェントの識別に用いる。
    抽出できない場合は`None`を返す。
    """
    if not isinstance(transcript_path, str) or not transcript_path:
        return None
    basename = pathlib.PurePath(transcript_path).name
    match = _TRANSCRIPT_AGENT_ID_RE.match(basename)
    return match.group(1) if match else None


def _inspect_plan_impl_executor_report_format(payload: dict) -> tuple[list[str], bool]:
    """`plan-impl-executor`完了報告本文の主要欄ラベル存在検査とbackground並列起動宣言矛盾検査を実施する。

    `transcript_path`のファイル名から抽出した`agentId`が、`posttooluse.py`が親セッション状態へ
    書き込む`plan_impl_executor_active_subagent_sessions`辞書のキーと一致する場合のみ発火する。
    抽出・突合に失敗した場合は対象外として`([], False)`を返す（安全側。他種別のサブエージェント
    停止時の誤発火と、他インスタンスの登録の巻き添え消去を防ぐ）。
    戻り値は「欠落ラベルのリスト」と「background並列起動宣言と`changed`欄未消化項目の矛盾有無」の組とする。
    ラベル欠落とbackground並列起動宣言矛盾は原因が異なるため、呼び出し元で別々のblock理由文を組み立てる（FB[3]）。
    いずれも該当なしの場合または対象外の場合は`([], False)`を返す。
    検査で欠落ラベル・矛盾のいずれも検出しなかった場合のみ、当該エントリを状態辞書から削除する
    （当該サブエージェントの完了検知としての消費）。block判定時はエントリを保持し、
    是正後の再試行でも同一エントリに対する検査が再度発火できるようにする。
    """
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return [], False
    agent_id = _extract_transcript_agent_id(payload.get("transcript_path"))
    if agent_id is None:
        return [], False
    state = read_state(session_id)
    active = state.get(_PLAN_IMPL_EXECUTOR_ACTIVE_KEY)
    if not isinstance(active, dict) or agent_id not in active:
        return [], False

    text = payload.get("last_assistant_message")
    if not isinstance(text, str):
        return [], False
    required = list(_PLAN_IMPL_EXECUTOR_REQUIRED_LABELS)
    if _PLAN_IMPL_EXECUTOR_NEEDS_ESCALATION_RE.search(text):
        required.append(_PLAN_IMPL_EXECUTOR_NEEDS_ESCALATION_LABEL)
    missing: list[str] = []
    for label in required:
        pattern = re.compile(rf"^{re.escape(label)}:", re.MULTILINE)
        if not pattern.search(text):
            missing.append(label)
    violation = _detect_plan_impl_executor_background_parallel_violation(text)

    if not missing and not violation:

        def _drop_entry(current_state: dict, aid: str = agent_id) -> dict | None:
            current_active = current_state.get(_PLAN_IMPL_EXECUTOR_ACTIVE_KEY)
            if not isinstance(current_active, dict) or aid not in current_active:
                return None
            del current_active[aid]
            current_state[_PLAN_IMPL_EXECUTOR_ACTIVE_KEY] = current_active
            return current_state

        update_state(session_id, _drop_entry)

    return missing, violation


def main() -> int:
    """SubagentStop hookのエントリポイント。"""
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return 0

    # Stop/SubagentStopフックの再帰呼び出し対策:
    # `stop_hook_active`真は直前の本hook呼び出しがブロックした再呼び出しを示す。
    # 連続ブロック上限到達による強制終了を避けるため、判定処理をせず無条件approveを返す。
    if payload.get("stop_hook_active") is True:
        print(json.dumps({"decision": "approve"}, ensure_ascii=False))
        return 0

    text = payload.get("last_assistant_message")
    transcript_path = payload.get("transcript_path")
    session_id = payload.get("session_id")
    if isinstance(transcript_path, str) and has_pending_background_launches(
        transcript_path, session_id if isinstance(session_id, str) else ""
    ):
        # 当該サブエージェント自身の配下に未消化のbackground起動が構造的に実在する場合、
        # 完了報告本文の内容（空判定・縮退表明照合を含む）によらず無条件で承認する。
        # Main側`is_pending_async_work`はMain自身のtranscriptのみを走査するため、
        # サブエージェントが自身の配下でさらに起動した孫エージェントの状態を観測できない。
        # ここでの構造判定がその唯一の観測点である。
        return 0
    if is_empty_completion_report(text):
        reason = _llm_notice(
            "blocked: the subagent completion report is effectively empty or consists only of a `Skill` invocation."
            " Either re-delegate the task or append the full completion body."
            " When resubmitting, restate the entire original completion report along with the added/corrected"
            " content (the main agent does not retain the body across this hook's block).",
            tag="block",
        )
        print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
        return 0
    # `is_empty_completion_report`が非文字列・実質空を既に捕捉するため、
    # ここではtypeガードのみを残す。
    if not isinstance(text, str):
        return 0

    match_result = _match_scope_escalation(text, categories=_STOP_FOCUS_CATEGORIES_EXTENDED)
    if match_result is not None:
        category, _matched = match_result
        reason = _llm_notice(
            f"blocked: subagent completion report matched scope-escalation category `{category}`."
            " Either revise the flagged text or continue the work as unfinished."
            " When resubmitting, restate the entire original completion report and rewrite only the flagged"
            " passage (the main agent does not retain the body across this hook's block)."
            " For investigation/review reports that must quote a scope-escalation phrase as a normative"
            " reference, follow `agent-toolkit:agent-standards` 'Avoiding context contamination' section and"
            " use the category identifier or section name for indirect reference instead of the raw phrase.",
            tag="block",
        )
        print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
        return 0

    missing_labels, has_background_parallel_violation = _inspect_plan_impl_executor_report_format(payload)
    if missing_labels:
        reason = _llm_notice(
            "blocked: `plan-impl-executor` completion report is missing required labels:"
            f" {', '.join(missing_labels)}."
            " See `agent-toolkit/agents/plan-impl-executor.md` '出力' section for the required format."
            " When resubmitting, restate the entire original completion report with the missing labels added"
            " (the main agent does not retain the body across this hook's block).",
            tag="block",
        )
        print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
        return 0
    if has_background_parallel_violation:
        reason = _llm_notice(
            "blocked: `plan-impl-executor` completion report declares a self-initiated background parallel"
            " subagent launch (`run_in_background=true`) while the `changed` section still has unchecked"
            " (`- [ ]`) items. This violates `agent-toolkit/agents/plan-impl-executor.md` '停止禁止' section,"
            " which prohibits self-judged background parallel launches. Complete the unfinished work"
            " (directly or via a single non-parallel background delegation) before reporting completion, unless the"
            " caller's launch prompt explicitly authorized the parallel launch.",
            tag="block",
        )
        print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
        return 0

    named_subagent_check = _inspect_named_subagent_send(payload)
    _log_send_check(payload.get("session_id"), named_subagent_check, label_stem="named_subagent")
    if named_subagent_check.missing_main_send:
        reason = _llm_notice(
            "blocked: this named subagent finished without ever calling `SendMessage` to the launcher."
            " Named subagents launched with `run_in_background=true` must actively deliver the completion"
            " report to the launcher (the identifier specified in the launch prompt, or `main` if none was"
            " specified) via `SendMessage(to=<launcher>, message=<full body>)`; waiting for the launcher"
            " to poll is treated as incomplete."
            " Send the completion report body to the launcher now and then stop."
            " If this subagent was launched in the foreground and the launcher already received the return"
            " value directly, ignore this notice.",
            tag="block",
        )
        print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
        return 0

    explore_check = _inspect_explore_named_background_send(payload)
    if explore_check is not None:
        _log_send_check(payload.get("session_id"), explore_check, label_stem="explore_named_background")
        if explore_check.missing_main_send:
            reason = _llm_notice(
                "blocked: this named background Explore subagent finished without ever calling"
                " `SendMessage` to the launcher. Explore subagents launched with `name` and"
                " `run_in_background=true` must actively deliver their findings to the launcher (the"
                " identifier specified in the launch prompt, or `main` if none was specified) via"
                " `SendMessage(to=<launcher>, message=<full body>)`; waiting for the launcher to poll is"
                " treated as incomplete."
                " Send the findings to the launcher now and then stop.",
                tag="block",
            )
            print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
            return 0

    return 0
