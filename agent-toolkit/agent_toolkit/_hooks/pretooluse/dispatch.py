r"""Claude Code plugin agent-toolkit: PreToolUse統合フック。

任意ツールの実行前に以下のチェックを順に実行する。
block系checkは1プロセスで直列実行し、最初の違反を終了0の構造化denyで返す。
warn種別のcheckはstdoutの`hookSpecificOutput.additionalContext`へ警告を載せつつ処理を継続する
（exit 0で終了したフックのstderrはコーディングエージェントへ届かないため）。
通常の遮断は不可逆な操作とデータ破損を生む入力に限定し、後続の編集で復元できる結果は警告する。
記述言語の書き直しは`agent-basics:writing-standards`の`references/claude-hooks-block-warn.md`「遮断と警告の選択」の例外として遮断する。

統合しているチェック:

AskUserQuestion / ExitPlanMode:

- ユーザーが直接読む質問本文・計画本文の文字化けの警告 (warn)
- ユーザーが直接読む英語主体の質問本文・計画本文の書き直し要求 (block)

mcp__plugin_agent-toolkit_agents_server__start / send_message / kill / list:

- 自由本文が役割文書の実行を指す起動の形式を案内 (warn)
- `send_message`・`kill`の空でない`session_id`が指す対象sessionの保存済み`cwd`の欠落は
  所有を確認できないため、入力形式によらず遮断 (block)。
  入力形式（`prompt`・`instruction`・`session_id`の欠落・併用）の検証と修正案内はツールが返す
- 全チェック通過時はClaude Codeで強制承認、Codexで通常の権限フローへ通過

Agent / Task:

- 宣言外の説明を含む役割起動の形式を案内 (warn)。必須入力が欠ける定型起動は契約不成立として遮断 (block)
- 組み込みの汎用定義を遮断し、同じ主体の同一入力の再発行を一度通す (block)
- 汎用定義の再発行の記録を保存できない場合は警告だけで通す (warn)

Bash:

- 規範資料を見出しだけに限定して抽出する読取の警告 (warn)
- 区切り語を引用しないheredocの本文にあるコマンド置換の遮断 (block)
- パターン一致によるプロセス終了（`pkill`・`killall`等）の遮断 (block)
- 読取の引数・診断破棄、atk出力の接続とwaitの背景化の警告 (warn)
- 未完了のバックグラウンドタスクが書き込む出力ファイルの読取の警告 (warn)
- commitメッセージの規則（`atk-mode`の`references/commit-message.md`）の読取時に、観測した帰属行を先行配送 (warn)

TaskStop:

- 停滞検知完了記録または自セッション起動記録との対象一致による通過と、それ以外の遮断 (block)

Write / Edit / MultiEdit / apply_patch:

- 文字化け（U+FFFD）検出 (warn)
- `.ps1` / `.ps1.tmpl`へのLF-only書き込み検出 (warn)
- lockfile / 生成物ディレクトリの直接編集 (warn)
- Pythonと計画Markdownの末尾へ混入したツール境界タグ (warn)
- manifestファイルの手編集 (warn)

全ツール共通:

- 操作の直前に起動を求めるスキル（`operation_skills.OPERATION_SKILLS`の検索・原因分析の記述・本文の作成と更新・
  検証の実行）の、主体と文脈ごとにスキル1件につき1回の案内 (notice)
- 委譲先の全体作業契約が起動後に更新された場合の、更新後の版ごとに1回の案内 (warn)

各チェックの詳細仕様（対象パターン・エラー文言・例外条件）は対応する実装関数のdocstringを参照する。

ホスト差の扱い:

- 編集入力は`_hook_tool_input`が共通の操作記録へ正規化し、各判定処理はホストを区別しない
- 空でない文字列の`turn_id`を持つ入力をCodexと判定し、この判定はpayloadの読込直後に一度だけ行う
- PowerShellの改行を確かめる処理はClaudeの`Write`へ限定する
- 警告は1つの`hookSpecificOutput.additionalContext`へ結合し、遮断は最初の違反を`permissionDecisionReason`へ返す
"""

from __future__ import annotations

import json
import os
import pathlib
import re
lazy import dataclasses

lazy from agent_toolkit._atk.config_settings import APPLY_PRESET_SHOW, CONNECTION_ORDER_KEYS, MODEL_DEFAULTS
lazy from agent_toolkit._atk.send_to_user_recovery import resend_guidance
lazy from agent_toolkit._atk.wi import operation_permissions
lazy from agent_toolkit._common import delegated_session, send_to_user
lazy from agent_toolkit._common.agents_server_tools import is_send_to_user_tool
lazy from agent_toolkit._common.heredocs import heredoc_bodies
lazy from agent_toolkit._common.session_state import read_state
lazy from agent_toolkit._common.shell_segments import extract_execution_segments
lazy from agent_toolkit._common.shell_tokens import is_atk_command
lazy from agent_toolkit._hooks import agent_id as _agent_id
lazy from agent_toolkit._hooks import (
    background_task_outputs as _background_task_outputs,
)
lazy from agent_toolkit._hooks import (
    bugfix_gate,
    clock_claims,
    command_timing,
    delivered_repost_gate,
    hook_notice_response_gate,
    norm_excuse_gate,
    permission_denial_notice,
    repository_protection,
    work_contract,
)
lazy from agent_toolkit._hooks import notice as _notice
lazy from agent_toolkit._hooks import (
    tool_input as _hook_tool_input,
)
lazy from agent_toolkit._hooks.agents_server_observations import start_observation_input
lazy from agent_toolkit._hooks.background_tasks import observe_repl_bash
lazy from agent_toolkit._hooks.host import is_antigravity_payload, is_codex_payload
lazy from agent_toolkit._hooks.notice import _WARN_TAG, begin_delivery, emit_response, notice_cycle, set_warning_session_id
lazy from agent_toolkit._hooks.pretooluse import commit_identity, intake_edit, publication_gate, user_facing_checks
lazy from agent_toolkit._hooks.pretooluse import operation_skills as _operation_skills
lazy from agent_toolkit._hooks.pretooluse.agent_checks import (
    _AGENTS_SERVER_KILL_TOOLS,
    _AGENTS_SERVER_SEND_TOOLS,
    _AGENTS_SERVER_TOOL_NAMES,
    _check_agents_server_continuation_target,
    _check_generic_agent_launch,
    _check_task_stop,
    _record_iss_sidechain_probe,
)
lazy from agent_toolkit._hooks.pretooluse.content_checks import (
    _check_lockfiles,
    _collect_edit_operation_warnings,
)
lazy from agent_toolkit._hooks.pretooluse.decision import Decision
lazy from agent_toolkit._hooks.pretooluse.large_reads import (
    bash_read_paths,
    check_bundled_read,
    check_large_bash_read,
    session_record_reference_warning,
)
lazy from agent_toolkit._hooks.pretooluse.notices import _block_notice, _llm_notice
lazy from agent_toolkit._hooks.pretooluse.shell_checks import (
    _check_bash_atk_output_loss,
    _check_bash_option_after_terminator,
    _check_bash_process_kill_by_pattern,
    _check_bash_self_matching_pgrep,
    _check_bash_unquoted_heredoc_substitution,
    _check_read_predicate_stderr_discard,
    _git_commit_block,
    _git_commit_messages,
    _warn_container_root_bind_mount,
    _warn_git_rev_parse_short_multiple,
    _warn_windows_drive_letter_path,
)
lazy from agent_toolkit._hooks.pretooluse.shell_checks_git import duplicate_autosquash_subject
lazy from agent_toolkit._hooks.pretooluse.task_document_launch import AGENT_TOOL_NAMES, check_task_document_launch
lazy from agent_toolkit._hooks.pretooluse.warning_context import (
    format_warning_context,
)

_SEARCH_TOOL_NAMES: frozenset[str] = frozenset({"Grep", "Glob"})
_MAIN_ONLY_MCP_TOOLS = frozenset(
    f"{prefix}{name}"
    for prefix in ("mcp__plugin_agent-toolkit_agents_server__", "mcp__agents_server__")
    for name in ("send_to_user", "compact_conversation", "apply_runtime_changes")
)
_SCOPE_REVIEW_MCP_TOOLS = frozenset(
    f"{prefix}{name}"
    for prefix in ("mcp__plugin_agent-toolkit_agents_server__", "mcp__agents_server__")
    for name in ("start", "send_message", "send_to_user")
)


def main(payload_text: str, *, initial_notices: tuple[str, ...] = ()) -> int:
    """エントリポイント。

    exit code契約:

    - exit 0: 通過（違反なし / スキップ対象ツール / 想定外入力 / warnのみ）
    - exit 0 + permissionDecision=deny: 正常な拒否（専用の理由欄で返す）

    予期せぬ例外は0にフォールバックする（pluginのhookが破損して編集できなくなることを避けるため）。
    各判定は結果を`Decision`で返し、標準出力・標準エラーへの書き込みは本関数と`_emit`だけが行う。
    """
    begin_delivery()
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError, ValueError:
        # 想定外入力ではフックを無効化（実処理の破損を避ける安全側の判定）
        return 0

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return 0
    session_id_raw = payload.get("session_id", "")
    session_id = session_id_raw if isinstance(session_id_raw, str) else ""
    set_warning_session_id(session_id)

    is_codex = is_codex_payload(payload)

    with notice_cycle(session_id, "pretooluse:" + str(tool_name), _agent_id.resolve_hook_agent_id(payload)):
        pending_notices: list[str] = list(initial_notices)
        user_facing = tool_name == "AskUserQuestion" or is_send_to_user_tool(tool_name)
        if denial_notice := permission_denial_notice.next_notice(payload, session_id, block=user_facing):
            if user_facing:
                return _emit(Decision(block=denial_notice), pending_notices, is_codex=is_codex)
            pending_notices.append(denial_notice)
        if tool_name not in user_facing_checks.USER_FACING_TEXT_TOOL_NAMES:
            user_facing_checks.language_retry(payload, tool_name, tool_input, remember=False)
        decision = _decide(payload, tool_name, tool_input, session_id, is_codex=is_codex)
        protection = repository_protection.check(tool_name, tool_input, str(payload.get("cwd", "")))
        if protection is not None:
            decision = dataclasses.replace(decision, block="\n\n".join(filter(None, (decision.block, protection))))
        if decision.block is None and not decision.allow and not is_codex:
            authorization = operation_permissions.pretooluse(payload)
            if authorization is not None:
                decision = dataclasses.replace(
                    decision,
                    allow=authorization.allow,
                    notices=decision.notices + ((authorization.context,) if authorization.context else ()),
                )
        if warning := delivered_repost_gate.pretooluse_notice(payload, session_id):
            pending_notices.append(warning)
        if decision.block is None:
            pending_notices.extend(_unread_material_notices(payload, tool_name, tool_input, session_id))
        if warning := bugfix_gate.verification_notice(payload, tool_name, tool_input, session_id):
            pending_notices.append(warning)
        if decision.block is None:
            pending_notices.extend(_operation_skills.notices(payload, tool_name, tool_input, session_id))
            if (
                not is_codex
                and not is_antigravity_payload(payload)
                and (boundary := intake_edit.notice(payload, tool_name, tool_input, session_id))
            ):
                pending_notices.append(boundary)
        contract_update = work_contract.delegated_notice(session_id, agent=_agent_id.resolve_hook_agent_id(payload))
        if contract_update:
            pending_notices.append(
                _llm_notice(
                    contract_update[0],
                    tag="warn",
                    fix=contract_update[1],
                    removable_cause=True,
                    escalate_on_repeat=False,
                )
            )
        if decision.block is None:
            command_timing.start(payload)
            if not is_codex and tool_name == "Skill":
                bugfix_gate.observe_repl_skill_start(payload, session_id)
            if not is_codex and tool_name == "Bash" and str(payload.get("tool_use_id", "")).startswith("repl_"):
                observe_repl_bash(payload)
        return _emit(decision, pending_notices, is_codex=is_codex)


def _unread_material_notices(payload: dict, tool_name: str, tool_input: dict, session_id: str) -> list[str]:
    """資料の未取得は遮断せず警告し、未対応のまま進めた操作は進捗点検で列挙して確認させる。  pyfltr: allow-comment

    読取を観測できないホスト（Antigravityなど）でも作業を止めないためである。
    """
    found: list[tuple[str, str, str]] = []
    if tool_name in {"Bash", "PowerShell", "exec_command", "functions.exec_command"}:
        from agent_toolkit._hooks.review_receipt import missing_before_response  # pylint: disable=import-outside-toplevel

        if missing := missing_before_response(payload, session_id, tool_input):
            found.append(
                (
                    "レビュー返却が指定した資料の取得を確認できないまま、採否を記録する操作である。",
                    f"{'、'.join(missing)}を取得し、採否が変わるなら記録を直してください。",
                    "レビュー返却が指定した資料の取得を確認できない採否の記録",
                )
            )
    if is_send_to_user_tool(tool_name) and isinstance(message := tool_input.get("message"), str):
        from agent_toolkit._hooks.persisted_output import unread_evidence  # pylint: disable=import-outside-toplevel
        from agent_toolkit._hooks.termination_order_advisor import missing_completion_before_report  # pylint: disable=import-outside-toplevel

        if unread := unread_evidence(payload, session_id, message):
            found.append(
                (
                    "画面へ送る根拠に未取得の退避出力がある。",
                    f"`{unread}`の全文を取得し、根拠が変わるなら報告を訂正してください。",
                    "未取得の退避出力を根拠にした報告",
                )
            )
        if not send_to_user.from_repl(payload.get("tool_use_id")) and (
            playbook := missing_completion_before_report(payload, session_id, message, tool_input.get("kind"))
        ):
            found.append(
                (
                    "作業完了報告の手順書の取得を確認できないまま、完了報告を送る操作である。",
                    f"`{playbook}`を全文読み、欠けた項目があれば報告を補ってください。",
                    "完了報告の手順書の取得を確認できない完了報告",
                )
            )
    actor = _agent_id.resolve_hook_agent_id(payload)
    notices = []
    for body, fix, label in found:
        _notice.record_skipped(session_id, actor, label)
        notices.append(_llm_notice(body, tag="warn", fix=fix, removable_cause=True, escalate_on_repeat=False))
    return [text for text in notices if text]


def _send_understanding_block(message: object) -> str | None:
    if isinstance(message, str) and user_facing_checks.has_original_text_label_in_understanding(message):
        return _block_notice(
            "「発話の理解」に「原文」の見出しがある。",
            fix="見出しから「原文」を外し、発話を自分の言葉で述べてから再送してください。",
        )
    if isinstance(message, str) and user_facing_checks.missing_alternative(message):
        return _block_notice(
            "「発話の理解」に「別案」の項目がない。",
            fix="比較した手段と採否、または別案がないと判断した観点と理由を「別案」に書いてください。",
        )
    return None


def _retry_send(
    payload: dict, tool_name: str, tool_input: dict, session_id: str, reason: str, *, edit_body: bool = True
) -> Decision:
    guidance = resend_guidance(
        session_id,
        tool_input.get("message"),
        tool_name=tool_name,
        tool_use_id=payload.get("tool_use_id"),
        kind=tool_input.get("kind"),
        edit_body=edit_body,
    )
    return Decision(block=f"{reason}\n\n{guidance}")


def _decide(payload: dict, tool_name: str, tool_input: dict, session_id: str, *, is_codex: bool) -> Decision:
    # Code Modeも個々のMCP呼出しをこの設定hookへ通す。MCP起動環境には無い組込みagent_idもここで判定する。
    if tool_name in _MAIN_ONLY_MCP_TOOLS and (
        payload.get("agent_id") is not None or delegated_session.is_delegated(os.environ)
    ):
        return Decision(
            block=_block_notice(
                "メイン専用ツールのため実行していない。表示・圧縮・反映の成功として扱わない。",
                fix="結果本文を委譲元へ最終応答で返してください。",
            )
        )
    if (tool_name == "AskUserQuestion" or tool_name in _SCOPE_REVIEW_MCP_TOOLS) and (
        reminder := work_contract.scope_review_before_send(
            session_id,
            payload.get("transcript_path"),
            user_facing_checks.scope_review_text(tool_name, tool_input),
            actor=_agent_id.resolve_hook_agent_id(payload),
        )
    ):
        reason = _block_notice(
            f"決定の原文と適用範囲を見直すため、まだ送信していない。\n\n{reminder}",
            fix="今回の対象と除外を確かめ、必要なら本文を直して再送してください。",
        )
        if isinstance(reason, str) and is_send_to_user_tool(tool_name):
            return _retry_send(payload, tool_name, tool_input, session_id, reason)
        return Decision(block=reason)
    question_tool = "request_user_input" if is_codex else "AskUserQuestion"
    if tool_name == question_tool and _agent_id.is_main_agent_context(payload):
        if user_facing_checks.policy_approval_form_invalid(tool_input):
            return Decision(
                block=_block_notice(
                    "方針の承認の質問が決まった形でないため、まだ送っていない。再発行しても同じ形では送れない。",
                    fix=(
                        "質問は1つだけにし、見出し「方針の承認」、問い「送った方針で進めてよいですか？」、"
                        "選択肢「この方針で進める」（説明「送った方針のとおりに着手する」）と"
                        "「修正して進める」（説明「修正点を自由記述で伝える」）の2つにしてください（推奨の付記は付けない）。"
                        f"方針の識別に修飾が必要なら、「2件目の」など{user_facing_checks.POLICY_APPROVAL_MODIFIER_LIMIT}文字以内にしてください。"
                        "修飾は文字と数字だけで書き、改行、読点、列挙記号、括弧、述語を含む文は入れないでください。"
                        "方針の項目と技術の選択は、質問の前に送る材料（`send_to_user`など）に書いてください。"
                        "本人固有の論点は、承認より前に別の呼び出しで尋ねてください。"
                    ),
                )
            )
        if not is_codex and (
            error := user_facing_checks.external_source_review_needed(session_id, tool_input, cwd=payload.get("cwd"))
        ):
            return Decision(
                block=_block_notice(
                    f"外部資料の対応表が不足しているため、方針の承認はまだ求めていない。\n{error}",
                    fix=(
                        f"セッション一時領域の`{user_facing_checks.SOURCE_REVIEW_FILE}`に、各主張の必須列と既存との差分を書いてください。差分がなければ既存の対応箇所を`path:行`で示してください。"
                        "`agent-basics:writing-standards/references/design-heuristics.md`"
                        "「外部資料に基づく改善案の評価」の配布範囲も確かめてください。"
                    ),
                )
            )
        if user_facing_checks.recommendation_review_needed(payload, session_id, tool_input):
            return Decision(
                block=_block_notice(
                    "推奨のある質問はまだ送っていない。",
                    fix=(
                        "`agent-toolkit:atk-mode/playbooks/confirmation.md`「確認要否の判定」に従ってください。"
                        "事前承認・方針全体の合意・認可・本人固有の入力は確認してください。"
                        "全体として受け入れられた方針で、確定した対象・結果・範囲を保つ実現方式の変更は技術判断です。"
                        "有効な指定手段の変更は確認し、それ以外の技術判断は質問から外して方針へ書いてください。"
                        "見直して必要と判断した問いは再発行できます。"
                    ),
                )
            )
    if (is_send_to_user_tool(tool_name) or tool_name == "AskUserQuestion") and (
        notice_block := hook_notice_response_gate.send_block(payload, session_id, tool_name, tool_input)
    ):
        return Decision(block=notice_block)
    if publication_block := publication_gate.check(payload, session_id, tool_name, tool_input):
        return Decision(block=_block_notice(publication_block[0], fix=publication_block[1]))
    if tool_name == "ExitPlanMode":
        from agent_toolkit._hooks.plan_presentation import check_before_presentation  # pylint: disable=import-outside-toplevel

        if invalid_plan := check_before_presentation(session_id, str(payload.get("cwd", "")), tool_input.get("plan")):
            return Decision(block=_block_notice(invalid_plan, fix="指摘を直し、同じ計画を提示してください。"))
    if tool_name in user_facing_checks.USER_FACING_TEXT_TOOL_NAMES:
        return user_facing_checks.decide_user_facing_text_tool(payload, tool_name, tool_input)
    send_warnings: list[str] = []
    if is_send_to_user_tool(tool_name):
        from agent_toolkit._hooks.agents_server_session_advisor import outstanding_before_completion  # pylint: disable=import-outside-toplevel
        from agent_toolkit._hooks.termination_order_advisor import missing_completion_before_report  # pylint: disable=import-outside-toplevel

        message = tool_input.get("message")
        kind = tool_input.get("kind")
        reason = _send_understanding_block(message)
        edit_body = True
        if reason is None and session_id and send_to_user.is_completion(message, kind):
            if pending := outstanding_before_completion(session_id, _agent_id.resolve_hook_agent_id(payload)):
                reason = _block_notice(
                    f"完了報告の前に委譲先の結果と続行を確認してください。{pending}",
                    fix="未回収の結果を`atk agents wait`で受け取り、応答待ちの担当へ指示を返してください。",
                )
            elif send_to_user.from_repl(payload.get("tool_use_id")) and (
                playbook := missing_completion_before_report(payload, session_id, message, kind)
            ):
                reason = _block_notice(
                    "完了報告の手順書を読んだ証拠がないため、まだ表示していない。",
                    fix=(
                        f"`Read`で`{playbook}`を全文読んでください。`Bash`で全文を表示しても構いません。"
                        "`file(...).text()`は証拠にならない。"
                    ),
                )
            edit_body = reason is None
        if reason is None:
            reason = bugfix_gate.send_block(payload, message, kind=kind, warnings=send_warnings) or (
                norm_excuse_gate.text_block(payload, message, "ユーザーへの送達")
            )
        if reason is not None:
            return _retry_send(payload, tool_name, tool_input, session_id, reason, edit_body=edit_body)
    if tool_name in _MAIN_ONLY_MCP_TOOLS:
        message = tool_input.get("message")
        timing = (
            clock_claims.warning([message], payload) if is_send_to_user_tool(tool_name) and isinstance(message, str) else None
        )
        return Decision(allow=True, context=timing, notices=tuple(send_warnings))
    if tool_name == "Skill":
        return Decision()
    if tool_name in _AGENTS_SERVER_TOOL_NAMES:
        return _decide_agents_server_tool(payload, tool_name, tool_input, session_id)
    if tool_name in AGENT_TOOL_NAMES:
        launch = check_task_document_launch(tool_name, tool_input)
        if launch.block is not None:
            return launch
        generic_block, generic_notice = _check_generic_agent_launch(payload, tool_name, tool_input)
        if generic_block:
            return dataclasses.replace(launch, block=generic_block)
        return dataclasses.replace(launch, notices=(*launch.notices, generic_notice)) if generic_notice else launch
    if tool_name in {"Bash", "PowerShell", "exec_command", "functions.exec_command"}:
        shell = _decide_bash_tool(payload, tool_input, session_id, is_codex=is_codex) if tool_name == "Bash" else Decision()
        if shell.block is not None:
            return shell
        command = tool_input.get("command")
        if isinstance(command, str) and _writes_model_settings(command):
            if is_codex:
                return Decision(
                    block="モデル・接続設定または一時指定を書き換えるには、変更内容と理由を伝え、"
                    "ユーザー本人に atk config または atk serve の設定画面での操作を依頼してください。"
                )
            return dataclasses.replace(shell, ask="モデル・接続設定または一時指定の変更を許可しますか。")
        if tool_name == "Bash":
            return shell
    if tool_name == "TaskStop":
        return Decision(block=_check_task_stop(session_id, tool_input))
    cwd_raw = payload.get("cwd", "")
    cwd = cwd_raw if isinstance(cwd_raw, str) else ""
    notices = tuple(send_warnings)
    if record_warning := session_record_reference_warning(tool_name, tool_input, cwd):
        notices += (record_warning,)
    file_path = tool_input.get("file_path")
    if (
        tool_name == "Read"
        and isinstance(file_path, str)
        and commit_identity.reads_commit_message_rules((pathlib.Path(cwd, file_path),))
        and (trailer_notice := commit_identity.commit_trailer_notice(payload, cwd, is_codex=is_codex))
    ):
        notices += (trailer_notice,)
    if tool_name in _SEARCH_TOOL_NAMES:
        return Decision(notices=notices)
    return dataclasses.replace(_decide_edit_tool(tool_name, tool_input, cwd, payload), notices=notices)


def _emit(decision: Decision, pending_notices: list[str], *, is_codex: bool) -> int:
    pending_notices.extend(decision.notices)
    hook_output: dict[str, object] = {"hookEventName": "PreToolUse"}
    # Codexは入力書換えを伴うallowだけを受理する。通常通過の権限判定はホストへ委ねる。
    force_allow = decision.allow and not is_codex
    if decision.block is not None:
        hook_output["permissionDecision"] = "deny"
        hook_output["permissionDecisionReason"] = decision.block
    elif decision.ask is not None and not is_codex:
        hook_output["permissionDecision"] = "ask"
        hook_output["permissionDecisionReason"] = decision.ask
    elif force_allow or decision.updated_input is not None:
        hook_output["permissionDecision"] = "allow"
        if decision.updated_input is not None:
            hook_output["updatedInput"] = decision.updated_input
    contexts = [decision.context] if decision.context else []
    contexts.extend(notice for notice in pending_notices if notice)
    pending_notices.clear()
    if contexts:
        hook_output["additionalContext"] = format_warning_context(contexts)
    if len(hook_output) > 1:
        emit_response({"hookSpecificOutput": hook_output})
    return 0


def _decide_agents_server_tool(payload: dict, tool_name: str, tool_input: dict, session_id: str) -> Decision:
    _record_iss_sidechain_probe(session_id, tool_name, payload)
    launch = check_task_document_launch(tool_name, tool_input)
    if launch.block is not None:
        return launch
    if tool_name in _AGENTS_SERVER_SEND_TOOLS | _AGENTS_SERVER_KILL_TOOLS:
        return _check_agents_server_continuation_target(
            session_id, tool_input, tool_name, actor=_agent_id.resolve_hook_agent_id(payload)
        )
    if tool_name.endswith("__start") and not is_codex_payload(payload):
        updated = start_observation_input(session_id, tool_input, _agent_id.resolve_hook_agent_id(payload))
        return dataclasses.replace(launch, allow=True, updated_input=updated)
    return dataclasses.replace(launch, allow=True)


def _writes_model_settings(command: str) -> bool:
    segments = extract_execution_segments(command)
    for segment in segments:
        tokens = segment.tokens
        if not segment.resolved or not tokens or not is_atk_command(tokens) or tokens[1:2] != ("config",):
            continue
        arguments = tokens[2:]
        if "--help" in arguments or "-h" in arguments:
            continue
        if arguments[:2] in {("temporary", "set"), ("temporary", "clear")}:
            return True
        if (
            arguments[:1] == ("apply-preset",)
            and len(arguments) > 1
            and arguments[1] != APPLY_PRESET_SHOW
            and not arguments[1].startswith("-")
        ):
            return True
        if (
            arguments[:1] == ("set",)
            and len(arguments) > 1
            and (arguments[1] in MODEL_DEFAULTS or arguments[1] in CONNECTION_ORDER_KEYS)
        ):
            return True
        if arguments[:1] == ("update",) and (len(segments) != 1 or _protect_config_update(command)):
            return True
    return False


def _protect_config_update(command: str) -> bool:
    bodies = heredoc_bodies(command)
    if len(bodies) != 1 or bodies[0].expands:
        return True
    body = bodies[0]
    # 別のリダイレクトが入力を置き換える形式は、heredocの本文から判定しない。  # pyfltr: allow-comment
    header = command[: body.start]
    if not re.fullmatch(r"""\s*atk\s+config\s+update\s+<<\s*(['"])[A-Za-z0-9_]+\1\s*""", header):
        return True
    if body.end == len(command):
        return True
    try:
        operation = json.loads(command[body.start : body.end])
    except ValueError:
        return True
    if not isinstance(operation, dict):
        return True
    action = operation.get("action")
    if action in ("repo", "array-add", "array-remove"):
        return False
    key = operation.get("key")
    return action != "set" or not isinstance(key, str) or key in MODEL_DEFAULTS or key in CONNECTION_ORDER_KEYS


def _decide_bash_tool(payload: dict, tool_input: dict, session_id: str, *, is_codex: bool) -> Decision:
    """Bashコマンドの遮断と警告を判定する。

    Codexの大量読取の警告、引用符なしheredoc本文の置換の遮断、パターン一致によるプロセス終了の遮断、
    自身に一致する`pgrep -f`の遮断、未起動のスキルの操作の警告、
    未完了のバックグラウンドタスクが書き込む出力ファイルの読取の警告を扱う。
    遮断の判定は警告の判定より前に行い、遮断した呼び出しでは警告の記録（文脈ごとに1回の警告など）を進めない。
    """
    command = tool_input.get("command")
    if not isinstance(command, str):
        return Decision()
    cwd_raw = payload.get("cwd", "")
    cwd = cwd_raw if isinstance(cwd_raw, str) else ""
    for block in (
        lambda: _check_bash_unquoted_heredoc_substitution(command),
        lambda: _check_bash_process_kill_by_pattern(command),
        lambda: _check_bash_self_matching_pgrep(command),
    ):
        message = block()
        if message is not None:
            return Decision(block=message)
    if duplicate_subject := duplicate_autosquash_subject(command, cwd):
        return Decision(block=duplicate_subject)
    messages = tuple(_git_commit_messages(command, cwd=cwd))
    if messages:
        checks_attribution = is_codex or not commit_identity.claude_commit_attribution_disabled(cwd)
        identity = commit_identity.hook_observed_identity(payload, is_codex=is_codex) if checks_attribution else None
        commit_block = _git_commit_block(messages, identity)
        if commit_block is not None:
            return Decision(block=commit_block)
    warnings: list[str] = []
    if timing := clock_claims.warning(clock_claims.wi_bodies(command, cwd), payload):
        warnings.append(timing)
    for check in (
        _check_bash_option_after_terminator,
        _check_bash_atk_output_loss,
        _check_read_predicate_stderr_discard,
        _warn_git_rev_parse_short_multiple,
        _warn_container_root_bind_mount,
    ):
        if message := check(command):
            warnings.append(message)
    record_warning = session_record_reference_warning("Bash", tool_input, cwd)
    if record_warning is not None:
        if record_warning:
            warnings.append(record_warning)
    elif read_warning := check_large_bash_read(command, cwd):
        warnings.append(read_warning)
    if bundled_warning := check_bundled_read(
        command,
        cwd,
        codex=is_codex,
        environment=dict(os.environ),
        max_output_tokens=tool_input.get("max_output_tokens"),
    ):
        warnings.append(bundled_warning)
    drive_letter_path_warning = _warn_windows_drive_letter_path(command, is_codex=is_codex)
    if drive_letter_path_warning is not None:
        warnings.append(drive_letter_path_warning)
    transcript_path = payload.get("transcript_path")

    if isinstance(transcript_path, str) and transcript_path and read_state(session_id).get("background_task_ids"):
        pending_paths = _background_task_outputs.pending_task_output_paths(transcript_path, session_id)
        if _background_task_outputs.command_reads_path(command, pending_paths):
            warnings.append(
                _llm_notice(
                    "未完了のバックグラウンドタスクが書き込む出力ファイルを読み取ろうとしている。",
                    tag=_WARN_TAG,
                    fix=(
                        "読み取った途中経過は進捗の材料に限ってください。"
                        "完了通知を受けた後に同じ出力ファイルを全量読み直してから結果として使ってください。"
                    ),
                    removable_cause=True,
                    subject=command,
                    escalate_on_repeat=False,
                )
            )
    if commit_identity.reads_commit_message_rules(
        path for paths in bash_read_paths(command, cwd, include_partial=True) for path in paths
    ) and (trailer_notice := commit_identity.commit_trailer_notice(payload, cwd, is_codex=is_codex)):
        warnings.append(trailer_notice)
    return Decision(context=format_warning_context(warnings) if warnings else None)


def _decide_edit_tool(tool_name: str, tool_input: dict, cwd: str, payload: dict | None = None) -> Decision:
    """共通編集単位ごとに警告が必要か判定する。

    ClaudeのWrite・Edit・MultiEditとCodexの`apply_patch`を`_hook_tool_input`が
    同一の操作記録へ変換するため、各判定処理はホストを区別しない。
    複数の対象と判定処理が返す警告は1つの`additionalContext`へ結合する。
    """
    operations = _hook_tool_input.parse_operations(tool_name, tool_input, cwd)
    if operations is None:
        return Decision()
    images: dict[int, _hook_tool_input.MaterializedEdit | None] = {}
    warnings = _check_lockfiles(tool_name, [path for operation in operations for path in operation.display_paths])
    for index, operation in enumerate(operations):
        warnings.extend(_collect_edit_operation_warnings(tool_name, operation, index, images))
    if timing := clock_claims.warning(
        (fragment.after for operation in operations for fragment in operation.fragments if isinstance(fragment.after, str)),
        payload,
    ):
        warnings.append(timing)
    return Decision(context=format_warning_context(warnings) if warnings else None)
