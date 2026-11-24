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
lazy import hashlib
lazy from collections.abc import Iterable

lazy from markdown_it import MarkdownIt

lazy from agent_toolkit._atk.wi import operation_permissions
lazy from agent_toolkit._common import delegated_session
lazy from agent_toolkit._common import host_homes as _host_homes
lazy from agent_toolkit._common import response_language_check as _response_language_check
lazy from agent_toolkit._common import transcript as _transcript
lazy from agent_toolkit._common.agents_server_tools import is_send_to_user_tool
lazy from agent_toolkit._common.markdown_headings import without_literal_blocks
lazy from agent_toolkit._common.runtime_identity import RuntimeIdentity, co_author_trailer, identity_observations
lazy from agent_toolkit._common.session_state import read_state, update_state
lazy from agent_toolkit._hooks import agent_id as _agent_id
lazy from agent_toolkit._hooks import (
    background_task_outputs as _background_task_outputs,
)
lazy from agent_toolkit._hooks import (
    bugfix_gate,
    clock_claims,
    command_timing,
    delivered_repost_gate,
    norm_excuse_gate,
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
lazy from agent_toolkit._hooks.pretooluse import operation_skills as _operation_skills
lazy from agent_toolkit._hooks.pretooluse import publication_gate
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
    _warn_mojibake,
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


def _settings_commit_attribution(path: pathlib.Path) -> tuple[bool, str | None]:
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
    except OSError, UnicodeError, json.JSONDecodeError, ValueError:
        return False, None
    if not isinstance(settings, dict):
        return False, None
    attribution = settings.get("attribution")
    if not isinstance(attribution, dict) or not isinstance(attribution.get("commit"), str):
        return False, None
    return True, attribution["commit"]


def _claude_commit_attribution_disabled(cwd: str) -> bool:
    config_dir = _host_homes.claude_config_dir()
    project_raw = os.environ.get("CLAUDE_PROJECT_DIR") or cwd
    paths = [config_dir / "settings.json"]
    if project_raw:
        project_dir = pathlib.Path(project_raw)
        paths.extend((project_dir / ".claude" / "settings.json", project_dir / ".claude" / "settings.local.json"))
    effective: str | None = None
    defined = False
    for path in paths:
        current_defined, current = _settings_commit_attribution(path)
        if current_defined:
            defined = True
            effective = current
    return defined and effective == ""


_USER_FACING_TEXT_TOOL_NAMES: frozenset[str] = frozenset({"AskUserQuestion", "ExitPlanMode"})
_MARKDOWN_HEADING = re.compile(r"^ {0,3}(#{1,6})\s+(.+)$")
_STRONG_HEADING = re.compile(r"^\s*(\*\*|__)([^:：]+?)([:：]?)\1\s*([:：]?)(.*)$")
_UNDERSTANDING_HEADING = re.compile(r"^発話の理解\s*(?:[:：]\s*(.*)|$)")
_ORIGINAL_TEXT_LABEL = re.compile(r"^\s*(?:[-*+]\s+)?[*_]*(?:原文|[^:：\n]*?[（(]原文[）)])[*_]*\s*(?:[:：]|$)")


_ALTERNATIVE_LABEL = re.compile(r"[^\S\n]*(?:[-*+][^\S\n]+)?[*_]*別案(?:[*_]*[^\S\n]*[:：]|[:：][*_]+)")
_INLINE_ALTERNATIVE_LABEL = re.compile(r"[^\S\n]*[*_]*別案[*_]*[^\S\n]*[:：]")
_ALTERNATIVE_BOUNDARY = re.compile(r"[「」『』。\n]")
_QUOTE_END = {"「": "」", "『": "』"}
_POLICY_APPROVAL = re.compile(r"方針[^？?\n]{0,80}で(?:進めて|起草して|実施して)(?:よい|いい)")
_SOURCE_URL = re.compile(r"https?://[^\s<>｜|\x60]+")
_SOURCE_REVIEW_FILE = "external-source-review.md"
_SOURCE_REVIEW_WORK = re.compile(r"^\s*-?\s*作業[:：]\s*`?(?:add-awi-by-user|process-user-comments)`?\s*$")


def _understanding_section_lines(message: str) -> Iterable[tuple[str, bool]]:
    section_level = 0
    plain_paragraph = False
    paragraph_break = False
    for line in without_literal_blocks(message).splitlines():
        heading = _MARKDOWN_HEADING.match(line)
        strong = _STRONG_HEADING.match(line)
        if strong and strong[5] and not (strong[3] or strong[4]):
            strong = None
        if heading:
            level = len(heading[1])
            title = heading[2].strip(" #*_")
        elif strong:
            level = 6
            title = strong[2].strip() + (strong[3] or strong[4]) + strong[5]
        else:
            level = 0
            title = line.strip()
        if (target := _UNDERSTANDING_HEADING.fullmatch(title)) is not None:
            section_level = level or 6
            paragraph_break = False
            plain_paragraph = not level and target[1] is not None
            if target[1] or plain_paragraph:
                yield target[1] or "", plain_paragraph
            continue
        if (heading and level <= section_level) or (plain_paragraph and not line.strip()):
            section_level = 0
            continue
        if not section_level:
            continue
        if (
            strong
            and (paragraph_break or not strong[5].strip())
            and not (_ORIGINAL_TEXT_LABEL.match(line) or _ALTERNATIVE_LABEL.match(line))
        ):
            section_level = 0
            continue
        paragraph_break = not line.strip()
        yield (title if heading else line), plain_paragraph


def _has_original_text_label_in_understanding(message: str) -> bool:
    return any(_ORIGINAL_TEXT_LABEL.match(line) for line, _ in _understanding_section_lines(message))


def _has_alternative(lines: list[str]) -> bool:
    text = "\n".join(lines)
    if "`" in text:
        code_span_parser = MarkdownIt("zero").enable(["backticks", "escape"])
        paragraphs = re.split(r"(\n[^\S\n]*\n)", text)
        for index in range(0, len(paragraphs), 2):
            tokens = code_span_parser.parseInline(paragraphs[index])[0].children or []
            paragraphs[index] = "".join("\0" if token.type == "code_inline" else token.content for token in tokens)
        text = "".join(paragraphs)
    if _ALTERNATIVE_LABEL.match(text):
        return True
    quote_ends: list[str] = []
    for boundary in _ALTERNATIVE_BOUNDARY.finditer(text):
        mark = boundary[0]
        if mark in _QUOTE_END:
            quote_ends.append(_QUOTE_END[mark])
        elif quote_ends:
            if mark == quote_ends[-1]:
                quote_ends.pop()
        elif (
            mark == "。"
            and _INLINE_ALTERNATIVE_LABEL.match(text, boundary.end())
            or mark == "\n"
            and _ALTERNATIVE_LABEL.match(text, boundary.end())
        ):
            return True
    return False


def _missing_alternative(message: str) -> bool:
    section = list(_understanding_section_lines(message))
    lines = [line for line, _ in section]
    required = sum(bool(line.strip()) for line in lines) > 1 or any(plain for _, plain in section)
    return required and not _has_alternative(lines)


def _external_source_urls(lines: Iterable[str]) -> set[str]:
    urls = set()
    within_sources = False
    for line in lines:
        if heading := _MARKDOWN_HEADING.match(line):
            if len(heading[1]) <= 2:
                within_sources = heading[2].strip() in {"原本", "入力"}
            continue
        if not within_sources and not re.match(r"\s*-?\s*(?:外部資料|入力)[:：]", line):
            continue
        for match in _SOURCE_URL.finditer(line):
            url = match[0]
            if line[: match.start()].endswith("](") and url.endswith(")"):
                url = url[:-1]
            urls.add(url)
    return urls


def _external_source_review_needed(session_id: str, tool_input: dict) -> bool:
    fields = _user_facing_text_fields("AskUserQuestion", tool_input)
    if not any(
        field.endswith((".header", ".question")) and (value == "方針の承認" or _POLICY_APPROVAL.search(value))
        for field, value in fields
    ):
        return False
    contract = work_contract.record_path(session_id)
    if contract is None:
        return False
    try:
        body = contract.read_text(encoding="utf-8")
    except OSError, UnicodeError:
        return False
    lines = without_literal_blocks(body).splitlines()
    if not any(_SOURCE_REVIEW_WORK.fullmatch(line) for line in lines):
        return False
    urls = _external_source_urls(lines)
    if not urls:
        return False
    try:
        table = without_literal_blocks(contract.with_name(_SOURCE_REVIEW_FILE).read_text(encoding="utf-8"))
    except OSError, UnicodeError:
        return True
    covered = set()
    for line in table.splitlines():
        if not line.startswith("|") or not line.rstrip().endswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip()[1:-1].split("|")]
        if len(cells) >= 2 and cells[0].strip("`<>") in urls and cells[1].strip("`*_ -—"):
            covered.add(cells[0].strip("`<>"))
    return not urls <= covered


def _recommendation_review_needed(payload: dict, session_id: str, tool_input: dict) -> bool:
    if not session_id or not any(
        ".options[" in field and ("推奨" in value or re.search(r"\brecommended\b", value, re.IGNORECASE))
        for field, value in _user_facing_text_fields("AskUserQuestion", tool_input)
    ):
        return False
    return not _notice.consume_resend(
        session_id,
        _agent_id.resolve_hook_agent_id(payload),
        "recommendation_review_by_actor",
        json.dumps(tool_input, sort_keys=True, ensure_ascii=False),
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
        if tool_name not in _USER_FACING_TEXT_TOOL_NAMES:
            _language_retry(payload, tool_name, tool_input, remember=False)
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
        if playbook := missing_completion_before_report(payload, session_id, message):
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
            _scope_review_text(tool_name, tool_input),
            actor=_agent_id.resolve_hook_agent_id(payload),
        )
    ):
        return Decision(
            block=_block_notice(
                f"決定の原文と適用範囲を見直すため、まだ送信していない。\n\n{reminder}",
                fix="今回の対象と除外を確かめ、必要なら本文を直して再送してください。",
            )
        )
    question_tool = "request_user_input" if is_codex else "AskUserQuestion"
    if tool_name == question_tool and _agent_id.is_main_agent_context(payload):
        if not is_codex and _external_source_review_needed(session_id, tool_input):
            return Decision(
                block=_block_notice(
                    "外部資料の対応表が不足しているため、方針の承認はまだ求めていない。",
                    fix=(
                        f"セッション一時領域の`{_SOURCE_REVIEW_FILE}`に、各資料のURLと配布規範での定義元か非該当の理由を書いてください。"
                        "`agent-basics:writing-standards/references/design-heuristics.md`"
                        "「外部資料に基づく改善案の評価」の配布範囲も確かめてください。"
                    ),
                )
            )
        if _recommendation_review_needed(payload, session_id, tool_input):
            return Decision(
                block=_block_notice(
                    "推奨のある質問はまだ送っていない。",
                    fix=(
                        "`agent-toolkit:atk-mode/playbooks/confirmation.md`「確認要否の判定」に従ってください。"
                        "事前承認・方針全体の合意・認可・本人固有の入力は確認し、それ以外の技術判断は質問から外して方針へ書いてください。"
                        "見直して必要と判断した問いは再発行できます。"
                    ),
                )
            )
    if publication_block := publication_gate.check(payload, session_id, tool_name, tool_input):
        return Decision(block=_block_notice(publication_block[0], fix=publication_block[1]))
    if tool_name == "ExitPlanMode":
        from agent_toolkit._hooks.plan_presentation import check_before_presentation  # pylint: disable=import-outside-toplevel

        if invalid_plan := check_before_presentation(session_id, str(payload.get("cwd", "")), tool_input.get("plan")):
            return Decision(block=_block_notice(invalid_plan, fix="指摘を直し、同じ計画を提示してください。"))
    if tool_name in _USER_FACING_TEXT_TOOL_NAMES:
        return _decide_user_facing_text_tool(payload, tool_name, tool_input)
    if is_send_to_user_tool(tool_name):
        from agent_toolkit._hooks.agents_server_session_advisor import outstanding_before_completion  # pylint: disable=import-outside-toplevel

        message = tool_input.get("message")
        if isinstance(message, str) and _has_original_text_label_in_understanding(message):
            return Decision(
                block=_block_notice(
                    "「発話の理解」に「原文」の見出しがある。",
                    fix="見出しから「原文」を外し、発話を自分の言葉で述べてから再送してください。",
                )
            )
        if isinstance(message, str) and _missing_alternative(message):
            return Decision(
                block=_block_notice(
                    "「発話の理解」に「別案」の項目がない。",
                    fix="比較した手段と採否、または別案がないと判断した観点と理由を「別案」に書いてください。",
                )
            )
        if (
            session_id
            and isinstance(message, str)
            and message.lstrip().partition("\n")[0].strip() == "## 作業完了報告"
            and (pending := outstanding_before_completion(session_id, _agent_id.resolve_hook_agent_id(payload)))
        ):
            return Decision(
                block=_block_notice(
                    f"完了報告の前に委譲先の結果と続行を確認してください。{pending}",
                    fix="未回収の結果を`atk agents wait`で受け取り、応答待ちの担当へ指示を返してください。",
                )
            )
    if (
        tool_name.endswith(_transcript.SEND_TO_USER_TOOL_SUFFIX)
        and (bugfix_block := bugfix_gate.send_block(payload, tool_input.get("message"))) is not None
    ):
        return Decision(block=bugfix_block)
    if tool_name.endswith(_transcript.SEND_TO_USER_TOOL_SUFFIX) and (
        excuse_block := norm_excuse_gate.text_block(payload, tool_input.get("message"), "ユーザーへの送達")
    ):
        return Decision(block=excuse_block)
    if tool_name in _MAIN_ONLY_MCP_TOOLS:
        message = tool_input.get("message")
        timing = (
            clock_claims.warning([message], payload) if is_send_to_user_tool(tool_name) and isinstance(message, str) else None
        )
        return Decision(allow=True, context=timing)
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
    if tool_name == "Bash":
        return _decide_bash_tool(payload, tool_input, session_id, is_codex=is_codex)
    if tool_name == "TaskStop":
        return Decision(block=_check_task_stop(session_id, tool_input))
    cwd_raw = payload.get("cwd", "")
    cwd = cwd_raw if isinstance(cwd_raw, str) else ""
    notices: tuple[str, ...] = ()
    if record_warning := session_record_reference_warning(tool_name, tool_input, cwd):
        notices += (record_warning,)
    file_path = tool_input.get("file_path")
    if (
        tool_name == "Read"
        and isinstance(file_path, str)
        and _reads_commit_message_rules((pathlib.Path(cwd, file_path),))
        and (trailer_notice := _commit_trailer_notice(payload, cwd, is_codex=is_codex))
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
    elif force_allow or decision.updated_input is not None:
        hook_output["permissionDecision"] = "allow"
        if decision.updated_input is not None:
            hook_output["updatedInput"] = decision.updated_input
    contexts = [decision.context] if decision.context else []
    contexts.extend(notice for notice in pending_notices if notice)
    pending_notices.clear()
    if contexts:
        hook_output["additionalContext"] = format_warning_context(contexts)
    if decision.block is not None or force_allow or decision.updated_input is not None or contexts:
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
        checks_attribution = is_codex or not _claude_commit_attribution_disabled(cwd)
        identity = _hook_observed_identity(payload, is_codex=is_codex) if checks_attribution else None
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
    if _reads_commit_message_rules(
        path for paths in bash_read_paths(command, cwd, include_partial=True) for path in paths
    ) and (trailer_notice := _commit_trailer_notice(payload, cwd, is_codex=is_codex)):
        warnings.append(trailer_notice)
    return Decision(context=format_warning_context(warnings) if warnings else None)


_COMMIT_MESSAGE_RULES = "/skills/atk-mode/references/commit-message.md"
"""帰属行の方針を定める資料の、plugin rootより下のパス。"""


def _reads_commit_message_rules(paths: Iterable[pathlib.Path]) -> bool:
    return any(path.as_posix().endswith(_COMMIT_MESSAGE_RULES) and path.is_file() for path in paths)


def _commit_trailer_notice(payload: dict, cwd: str, *, is_codex: bool) -> str | None:
    trailer = observed_commit_trailer(payload, cwd, is_codex=is_codex)
    if trailer is None:
        return None
    return _llm_notice(
        f"読取時点に観測したモデルと推論量の帰属行: {trailer}。通常のコミットのメッセージへこの1行を入れてください。"
        "明示指定と無効化の方針は`agent-toolkit:atk-mode/references/commit-message.md`「`Co-Authored-By:`」で確かめ、"
        "通常のコミットの直前にも現在の観測を確かめてください。",
        tag="notice",
    )


def observed_commit_trailer(payload: dict, cwd: str, *, is_codex: bool) -> str | None:
    if not is_codex and _claude_commit_attribution_disabled(cwd):
        return None
    identity = _hook_observed_identity(payload, is_codex=is_codex)
    if identity is None or identity.source != "observed":
        return None
    return co_author_trailer(identity)


def _hook_observed_identity(payload: dict, *, is_codex: bool) -> RuntimeIdentity | None:
    """Hook payloadとそのturnの実行主体の記録から、このturnの観測identityを返す。

    Claude Codeの推論量はhook入力の`effort.level`（ホストがturnへ適用した値）、モデルは記録の最後の観測値から取る。
    初出順に重複を除いた一覧の末尾は、turnの途中でモデルを切り替えて戻した記録で古い値を選ぶため使わない。
    hook入力が`agent_id`を持つ場合は、`transcript_path`がメインの記録を指すため、そのsubagentの記録
    （`<transcript_pathのディレクトリ>/<session_id>/subagents/agent-<agent_id>.jsonl`）を読む。
    記録を一意に解決できない場合は判定しない。Codexはhook入力の`model`と`reasoning_effort`を優先し、
    両方が無い場合は記録の`turn_context`の最後の観測値を使う。
    """
    runtime = "agy" if is_antigravity_payload(payload) else "codex" if is_codex else "claude"
    model = payload.get("model")
    effort_input = payload.get("effort")
    effort = effort_input.get("level") if isinstance(effort_input, dict) else effort_input or payload.get("reasoning_effort")
    if isinstance(model, str) and model and isinstance(effort, str) and effort:
        return RuntimeIdentity(runtime, model, effort, "observed")
    record_path = _hook_record_path(payload)
    if record_path is None:
        return None
    lines = _transcript.read_transcript_lines(record_path)
    if lines is None:
        return None
    entries: list[dict] = []
    for raw in lines:
        if not raw.strip():
            continue
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    observations = identity_observations(entries, runtime)
    if not observations:
        return None
    latest = observations[-1][1]
    if not is_codex and isinstance(effort, str) and effort:
        return RuntimeIdentity(runtime, latest.model, effort, "observed")
    return latest


def _hook_record_path(payload: dict) -> str | None:
    transcript_path = payload.get("transcript_path")
    if not isinstance(transcript_path, str) or not transcript_path:
        return None
    agent_id = payload.get("agent_id")
    if agent_id is None:
        return transcript_path
    session_id = payload.get("session_id")
    if not isinstance(agent_id, str) or not agent_id or not isinstance(session_id, str) or not session_id:
        return None
    subagent_path = pathlib.Path(transcript_path).parent / session_id / "subagents" / f"agent-{agent_id}.jsonl"
    return str(subagent_path) if subagent_path.is_file() else None


def _user_facing_text_fields(tool_name: str, tool_input: dict) -> list[tuple[str, str]]:
    if tool_name == "ExitPlanMode":
        plan = tool_input.get("plan")
        return [("plan", plan)] if isinstance(plan, str) else []
    questions = tool_input.get("questions")
    if not isinstance(questions, list):
        return []
    fields: list[tuple[str, str]] = []
    for question_index, question in enumerate(questions):
        if not isinstance(question, dict):
            continue
        for name in ("question", "header"):
            value = question.get(name)
            if isinstance(value, str):
                fields.append((f"questions[{question_index}].{name}", value))
        options = question.get("options")
        if not isinstance(options, list):
            continue
        for option_index, option in enumerate(options):
            if not isinstance(option, dict):
                continue
            for name in ("label", "description"):
                value = option.get(name)
                if isinstance(value, str):
                    fields.append((f"questions[{question_index}].options[{option_index}].{name}", value))
    return fields


def _scope_review_text(tool_name: str, tool_input: dict) -> str:
    if tool_name == "AskUserQuestion":
        return "\n".join(value for _, value in _user_facing_text_fields(tool_name, tool_input))
    values = [tool_input.get(name) for name in ("message", "prompt", "instruction")]
    if isinstance(extra := tool_input.get("extra_params"), dict):
        values.extend(extra.values())
    return "\n".join(value for value in values if isinstance(value, str))


def _language_retry(payload: dict, tool_name: str, tool_input: dict, *, remember: bool) -> bool:
    """同じ主体の直前入力だけを保持し、別操作による回復権限の持越しを防ぐ。"""
    session_id = payload.get("session_id", "")
    if not isinstance(session_id, str) or not session_id:
        return False
    actor = _agent_id.resolve_hook_agent_id(payload)
    fingerprint = hashlib.sha256(json.dumps([tool_name, tool_input], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    repeated = False

    def update(state: dict) -> dict:
        nonlocal repeated
        prior = state.get("response_language_retry_by_actor", {})
        prior = dict(prior) if isinstance(prior, dict) else {}
        repeated = prior.get(actor) == fingerprint
        if remember:
            prior[actor] = fingerprint
        else:
            prior.pop(actor, None)
        state["response_language_retry_by_actor"] = prior
        return state

    return update_state(session_id, update) and repeated


def _decide_user_facing_text_tool(_payload: dict, tool_name: str, tool_input: dict) -> Decision:
    """質問・計画本文の記述言語と文字化けを確かめる。

    ユーザーへ直接到達する本文はユーザー自身が読んで誤りを指摘できるため、復元できない結果に当たらない。
    遮断するとそのターンの入力と作業を失い、同じ確認を再発行する必要があるため、警告で返す。
    判定の根拠は`agent-basics:writing-standards`の`references/claude-hooks-block-warn.md`
    「遮断と警告の選択」が定める。記述言語の判定は同節の例外により表示前に遮断し、日本語での書き直しを求める。他の2つの判定は、成立した警告を同じ追加コンテキストへ並べる。
    """
    fields = _user_facing_text_fields(tool_name, tool_input)
    if excuse_block := norm_excuse_gate.text_block(
        _payload, "\n".join(text for _, text in fields), "ユーザーへの質問・計画の提示"
    ):
        return Decision(block=excuse_block)
    recoverable = all(_response_language_check.explanation_is_japanese(text) for _, text in fields)
    repeated = _language_retry(_payload, tool_name, tool_input, remember=recoverable)
    for name, text in fields:
        # `atk-mode`の`playbooks/host-claude-code.md`「ユーザー確認（AskUserQuestion）」がlabel末尾へ求める固定表記。
        # 本文・説明の英語判定と文字化け判定には、この除外を適用しない。
        if tool_name == "AskUserQuestion" and name.endswith(".label"):
            text = text.removesuffix("(Recommended)").rstrip()
        outcome, _ = _response_language_check.check_text(text)
        if outcome is _response_language_check.CheckOutcome.WARN:
            if recoverable and repeated:
                continue
            return Decision(
                block=_block_notice(
                    f"対象の入力欄: `{name}`。この入力本文を日本語の説明として判定できないため、呼び出しを止めた。",
                    fix=(
                        _response_language_check.WARNING_FIX
                        + "引用・入力素材だけが英語で、説明が日本語なら、同じ主体から同じツールへ同一入力を再発行してください。"
                    ),
                )
            )
    warnings = [warning for warning in (_warn_mojibake(tool_name, fields),) if warning is not None]
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
