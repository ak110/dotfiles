"""`atk run-skill`が起動したセッションの報告保存Stopフック。  pyfltr: allow-comment

環境変数`AGENT_TOOLKIT_RUN_SKILL_ID`と起動入力（`AGENT_TOOLKIT_SESSION_START_INPUT`のskill）を持つ最上位のセッションを対象とする。
対象セッションで、起動入力のskillとargumentsから求めた起動単位の報告用UWIが、
起動元の検収と同じ基準（`has_synced_run_report`）で保存されていなければ、
ターンの終了を遮断して報告の保存を促す。
完了まで続行させる働きは本判定が担う。

判定順序は以下のとおり。

1. run-skillが起動したセッションでない、または起動入力からskillを得られない: approve
2. hookの呼出主体が最上位でない（`Agent`ツールの委譲先、agents_serverの委譲先）: approve
3. `is_pending_async_work`が真: 非同期処理または未回収の結果が残るためapprove
   （再開後のStopで改めて判定する。Codexでは判定せず偽とする）
4. 報告用UWIの保存を確認できる: approve
5. 週次の画面巡回が証拠の引き渡し失敗を明示した: approve（完了にはせず、起動元の失敗判定へ戻す）
6. 上記いずれでもない（保存確認の失敗を含む）: blockし、報告の保存を促す

連続blockの上限は`stop.py`が管理する。上限に達した場合はCLIが終了し、起動元が既存の「報告用UWI未保存」の失敗を報告する。

委譲先での実行可否: 委譲先は最上位セッションの報告を保存しないため、hook入力と環境印で除外する。
"""

import json
import os
import subprocess

from agent_toolkit._hooks.notice import block_formatter as _block_notice_formatter
lazy from agent_toolkit._atk.wi import add as _wi_add
lazy from agent_toolkit._common import delegated_session as _delegated_session
lazy from agent_toolkit._common import mcp_environment as _mcp_environment
lazy from agent_toolkit._common import private_notes as _private_notes
lazy from agent_toolkit._common import skill_modes as _skill_modes
lazy from agent_toolkit._common import weekly_work as _weekly_work
lazy from agent_toolkit._common.runtime_inserted import report_lines
lazy from agent_toolkit._common.session_state import read_state
lazy from agent_toolkit._hooks.agent_id import is_main_agent_context
lazy from agent_toolkit._hooks.background_tasks import is_pending_async_work_from_payload
lazy from agent_toolkit._hooks.stop_session import append_stop_log
lazy from agent_toolkit._hooks.stop_session import parse_stop_session as _parse_stop_session
lazy from agent_toolkit._hooks.termination_evidence_state import completion_playbook_path

_HOOK_ID = "run_skill_report_gate"

# run-skillの起動元が渡す実行識別子の環境変数。
RUN_ID_ENV = "AGENT_TOOLKIT_RUN_SKILL_ID"

_REASON_BODY = """\
`atk run-skill`が起動したこのセッションで、この実行の報告用UWIの保存（コミットと必要なプッシュ）を確認できず、終了を止めた。
作業の完了・未完了は判定していない。"""

_block_notice = _block_notice_formatter(_HOOK_ID)
_EVIDENCE_DELIVERY_FAILED = "画面の証拠を引き渡せなかった:"


def _run_target(environ: dict[str, str] | os._Environ[str]) -> tuple[str, str] | None:
    run_id = environ.get(RUN_ID_ENV)
    if not run_id:
        return None
    try:
        startup = json.loads(environ.get(_mcp_environment.SESSION_START_INPUT_ENV) or "")
    except ValueError:
        return None
    skill = startup.get("skill") if isinstance(startup, dict) else None
    if not isinstance(skill, str) or not skill:
        return None
    arguments = startup.get("arguments")
    return run_id, _skill_modes.invocation_scope(skill, arguments if isinstance(arguments, str) else None)


def evaluate(payload_text: str) -> tuple[str, str]:
    resolved = _parse_stop_session(payload_text, lambda: None)
    if resolved is None:
        return "approve", ""
    session_id, payload = resolved

    target = _run_target(os.environ)
    if target is None:
        return "approve", ""
    run_id, scope = target

    if not is_main_agent_context(payload) or _delegated_session.is_delegated(os.environ):
        append_stop_log(session_id, "approve_run_skill_delegated_session", {})
        return "approve", ""

    if is_pending_async_work_from_payload(payload, session_id, session_state=read_state(session_id)):
        append_stop_log(session_id, "approve_run_skill_pending_async", {})
        return "approve", ""

    try:
        saved = _wi_add.has_synced_run_report(_private_notes.default_private_notes(), run_id=run_id, scope=scope)
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        append_stop_log(session_id, "run_skill_report_check_error", {"error": f"{type(error).__name__}: {error}"})
        saved = False
    if saved:
        append_stop_log(session_id, "approve_run_skill_report_saved", {})
        return "approve", ""

    if scope == _weekly_work.DEFINITIONS["ui-sweep"].scope:
        last_message = payload.get("last_assistant_message")
        if isinstance(last_message, str) and any(
            line.startswith(_EVIDENCE_DELIVERY_FAILED) and line.removeprefix(_EVIDENCE_DELIVERY_FAILED).strip()
            for line in report_lines(last_message)
        ):
            append_stop_log(session_id, "approve_run_skill_evidence_delivery_failed", {"run_id": run_id, "scope": scope})
            return "approve", ""

    append_stop_log(session_id, "block_run_skill_report_unsaved", {"run_id": run_id, "scope": scope})
    reason = _block_notice(
        _REASON_BODY,
        fix=(
            f"残る工程を進めた上で、完了報告の手順書`{completion_playbook_path()}`を全文読み、"
            "その手順で報告用UWIを保存してコミット・プッシュしてください。"
            "前提不足などで完了できない場合も、未完了の内容を報告用UWIへ保存してから終えてください。"
        ),
    )
    return "block", reason
