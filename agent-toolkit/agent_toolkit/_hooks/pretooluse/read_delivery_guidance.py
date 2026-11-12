"""現在の宣言資料へ取得手段と復旧手順を案内する。"""

# 公開入口の差し替えを呼出し時に参照し、モジュール初期化中の逆向きimportを避ける。
# pylintの依存グラフは関数内importも含むため、その参照行だけ循環検査から除く。

from __future__ import annotations

import json
import os
import pathlib
from typing import TYPE_CHECKING

from agent_toolkit._common import delegated_session, read_triggers, transcript
from agent_toolkit._common.plugin_roots import (
    PluginRootError,
)
from agent_toolkit._hooks import (
    agent_id,
    document_reads,
    plugin_resources,
    rules_context,
)
from agent_toolkit._hooks import notice as notice_module
from agent_toolkit._hooks.host import is_codex_payload
from agent_toolkit._hooks.notice import _WARN_TAG
from agent_toolkit._hooks.pretooluse import operation_skills
from agent_toolkit._hooks.pretooluse.decision import Decision
from agent_toolkit._hooks.pretooluse.notices import _block_notice, _llm_notice
from agent_toolkit._hooks.pretooluse.read_acquisition import codex_acquisition
from agent_toolkit._hooks.pretooluse.read_delivery_conditions import _work_record_only
from agent_toolkit._hooks.pretooluse.read_delivery_matching import _artifact_written_paths, _temporary_path
from agent_toolkit._hooks.pretooluse.read_delivery_paths import _acquiring, _document_owner, _read_paths, _row_operation_kinds
from agent_toolkit._hooks.pretooluse.read_delivery_roles import _subagent_task_documents
from agent_toolkit._hooks.pretooluse.read_delivery_selection import _declaration_identity, _unmark
lazy from agent_toolkit._hooks.pretooluse import (
    read_delivery_paths,
)

if TYPE_CHECKING:
    pass  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import


def acquisition_step(target: read_triggers.ReadTarget, subject: str, *, reference: bool = False, cwd: str = "") -> str:
    """主体に合う取得手段と、必要な範囲の容量を示す。"""
    from agent_toolkit._hooks.pretooluse import read_delivery  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import

    if read_triggers.is_normative_document(target.path):
        target = read_triggers.ReadTarget(target.path)
    path = target.path
    target.body()
    focus = f"。指定節`{target.heading}`と、その成立に必要な前提・例外を読め" if target.heading else ""
    skill = read_delivery.skill_name(path)
    if (
        subject != operation_skills.SUBJECT_CODEX
        and not reference
        and skill
        and document_reads.skill_loadable(path, cwd=cwd, owner=_document_owner)
        and (
            subject == operation_skills.SUBJECT_SUBAGENT
            or read_delivery_paths.requires_skill_invocation(path, host="claude", subject=subject, cwd=cwd)
        )
    ):
        step = f"`Skill`で`{skill}`を起動せよ"
        if subject == operation_skills.SUBJECT_SUBAGENT:
            step += f"。`Skill`を使えない場合は`{path}`をReadで全文読め"
        return step + focus
    if subject == operation_skills.SUBJECT_CODEX:
        ranges = target.line_ranges() if target.heading and path.suffix == ".md" else ()
        return codex_acquisition(path, ranges).removesuffix("。") + focus
    return f"Readで`{path}`を取得せよ" + focus


def _question_material_guidance(payload: dict, tool_name: str) -> str:
    """拒否した質問を出し直す時の、判断材料の送り方の案内を返す。

    拒否で失われるのは質問本文の作り直しである。送達ツールを持つClaude Codeの確認ツールに限り、
    材料を先に送って質問を短く保つ手順を示す。他のホストは質問本文へ材料を書く規定のため案内しない。
    """
    if tool_name != "AskUserQuestion" or operation_skills.resolve_subject(payload) == operation_skills.SUBJECT_CODEX:
        return ""
    return "質問の前に、`rules-main.claude-code.md`「ユーザー確認（`AskUserQuestion`）」の判断材料の送り方に従え。"


def _declarations_unavailable(error: Exception) -> Decision:
    """読込表を取得できず、担当が読込表か依存を直せる場合の警告を返す。"""
    return Decision(
        notices=(
            _llm_notice(
                f"現在の操作の前に読む資料を定める読込表を取得できない: {error}",
                tag=_WARN_TAG,
                fix=(
                    "診断が示すファイルとプラグインの導入状態を確かめ、読込表か不足する依存を正規の手順で直せ。"
                    "必要な資料を取得してから、判断とその他の変更へ進め。"
                ),
                removable_cause=True,
                escalate_on_repeat=False,
            ),
        )
    )


def _deliver_documents(payload: dict, tool_name: str, tool_input: dict, session_id: str) -> Decision:
    """現在の操作に関係する宣言資料を配送または案内する。"""
    from agent_toolkit._hooks.pretooluse import read_delivery  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import

    # pylint: disable=import-outside-toplevel,cyclic-import
    from agent_toolkit._hooks.pretooluse.read_delivery import (
        _declaration_recovery_operation,  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import
        _project_skill_rows,  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import
        _select_targets,  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import
    )
    # pylint: enable=import-outside-toplevel,cyclic-import

    if not session_id or delegated_session.is_lightweight_delegated(os.environ):
        return Decision()
    cwd = payload.get("cwd", "")
    cwd = cwd if isinstance(cwd, str) else ""
    written = operation_skills.written_paths(tool_name, tool_input, cwd)
    temporary_code = bool(written) and all(_temporary_path(path) for path in written)
    if temporary_code:
        written = []
    if _work_record_only(written, payload, session_id):
        return Decision()
    diagnostic = operation_skills.is_diagnostic_operation(tool_name, tool_input, cwd)
    warning_only = tool_name in {"AskUserQuestion", "ExitPlanMode", "request_user_input", "request_user_input_async"}
    # 確認ツールは、確認の前に読む資料の未取得だけを表示前に拒否する。宣言や記録を読めない場合は警告にとどめ、質問を止めない。
    confirmation = warning_only and tool_name != "ExitPlanMode"
    confirmation_pending: list[read_triggers.ReadTarget] = []
    try:
        rows = read_delivery.declarations(cwd, host="codex" if is_codex_payload(payload) else "claude", session_id=session_id)
    except (OSError, UnicodeError, ValueError) as error:
        if warning_only:
            return Decision(
                notices=(
                    _llm_notice(
                        f"現在の操作の前に読む資料を定める読込表を取得できない: {error}",
                        tag=_WARN_TAG,
                        fix="診断が示すファイルの読込表と参照先を確かめて直し、回答に依存する次の操作の前に、必要な資料を読んで質問の前提を確かめ直せ。",
                        removable_cause=True,
                        escalate_on_repeat=False,
                    ),
                )
            )
        if _declaration_recovery_operation(tool_name, tool_input, error):
            return _declarations_unavailable(error)
        recovery_paths = operation_skills.written_paths(tool_name, tool_input, cwd) + [
            str(path) for path in _read_paths(tool_name, tool_input, cwd)
        ]
        if any(str(error).startswith(path + ":") for path in recovery_paths):
            return Decision(
                notices=(
                    _llm_notice(
                        f"読込表の取得に失敗したファイルを直す操作のため、今回の操作は通す: {error}",
                        tag=_WARN_TAG,
                        fix="今回の操作の結果を確かめて読込表を直し、必要な資料を取得してから元の判断・操作へ戻れ。",
                        removable_cause=True,
                        escalate_on_repeat=False,
                    ),
                )
            )
        return Decision(
            notices=(
                _llm_notice(
                    f"現在の操作の前に読む資料を定める読込表を取得できない: {error}",
                    tag=_WARN_TAG,
                    fix="診断が示すファイルの読込表と参照先を確かめ、誤った記述や所在を直してから必要な資料を読め。",
                    removable_cause=True,
                    escalate_on_repeat=False,
                ),
            )
        )
    agent = agent_id.resolve_hook_agent_id(payload)
    records = read_delivery.read_state(session_id).get(rules_context.OPERATION_SKILL_READY_KEY, {})
    records = records if isinstance(records, dict) else {}
    roles = read_delivery.active_roles(payload, session_id, records, agent) | _subagent_task_documents(payload)
    records = read_delivery.read_state(session_id).get(rules_context.OPERATION_SKILL_READY_KEY, {})
    records = records if isinstance(records, dict) else {}
    try:
        rows = list(
            dict.fromkeys(
                [
                    *rows,
                    *_project_skill_rows(
                        records,
                        agent,
                        tuple(_read_paths(tool_name, tool_input, cwd)),
                        known_sources={row.source for row in rows},
                        host="codex" if is_codex_payload(payload) else "claude",
                        project=cwd,
                    ),
                ]
            )
        )
        # 現在の役割で除外した宣言は、選択履歴の保存・再接続にも使用しない。
        rows = [row for row in rows if not row.source.name.endswith(".subagent.md") or row.source in roles]
        active_rows = [
            row
            for row in rows
            if read_delivery.condition_confirmed(row, tool_name, tool_input, cwd)
            and (
                read_delivery.row_matches(row, tool_name, tool_input, cwd)
                or row.selector == "artifact-standards"
                and read_delivery.target_ready(records, read_triggers.ReadTarget(row.source), agent)
                and bool(_artifact_written_paths(tool_name, tool_input, cwd))
                or row.source in roles
                and (row.kind == "起動時" or any(operation.startswith("role:") for operation in row.operations))
            )
        ]
        selected_origins = [
            origin
            for key, owners in records.items()
            if key.startswith("selected-read-target:") and agent in owners
            for origin in json.loads(key.removeprefix("selected-read-target:")).get("declarations", [])
        ]
        selection_rows = list(
            dict.fromkeys([*active_rows, *(row for row in rows if _declaration_identity(row) in selected_origins)])
        )
        host = "codex" if is_codex_payload(payload) else "claude"
        subject = operation_skills.resolve_subject(payload)
        active_targets = {
            row: (*row.targets, *read_delivery.dynamic_targets(row, payload, tool_name, tool_input, records, agent, session_id))
            for row in active_rows
        }
        invocation_paths = {
            target.path
            for row in selection_rows
            if not document_reads.reference_only([_row_operation_kinds(row)])
            for target in active_targets.get(row, row.targets)
            if not target.heading
            and read_delivery_paths.requires_skill_invocation(target.path, host=host, subject=subject, cwd=cwd)
        }
        # 動的な起動対象は従来どおりパスで持ち越す。名指し参照の選択元がある場合だけ資料参照として扱う。
        invocation_paths.update(
            target.path
            for target in read_delivery.selected_targets(
                records, agent, rows=[row for row in rows if read_delivery.condition_confirmed(row, tool_name, tool_input, cwd)]
            )
            if not target.heading
            and read_delivery_paths.requires_skill_invocation(target.path, host=host, subject=subject, cwd=cwd)
            and not document_reads.reference_only(
                [_row_operation_kinds(row) for row in selection_rows if target in row.targets]
            )
        )

        def ready(target: read_triggers.ReadTarget) -> bool:
            from agent_toolkit._hooks.pretooluse import read_delivery  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import

            return read_delivery.target_ready(records, target, agent, invocation=target.path in invocation_paths)

        def guidance_key(target: read_triggers.ReadTarget) -> str:
            from agent_toolkit._hooks.pretooluse import read_delivery  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import

            return read_delivery.read_guidance_key(target, invocation=target.path in invocation_paths)

        pending = list(dict.fromkeys(target for row in active_rows for target in active_targets[row] if not ready(target)))
        if confirmation:
            confirmation_pending = list(
                dict.fromkeys(
                    target
                    for row in active_rows
                    if "confirmation" in row.operations
                    for target in active_targets[row]
                    if not ready(target)
                )
            )
        # 取得操作で保留した初回案内だけを次の依存操作へ渡す。実配送後は未取得でも再掲しない。
        carried = [
            target
            for target in read_delivery.selected_targets(
                records, agent, rows=[row for row in rows if read_delivery.condition_confirmed(row, tool_name, tool_input, cwd)]
            )
            if not temporary_code
            and target not in pending
            and not ready(target)
            and agent not in records.get(rules_context.carried_read_target_key(target.path), [])
            and agent not in records.get(guidance_key(target), [])
        ]
        pending.extend(carried)
        method_sources = dict.fromkeys([*_read_paths(tool_name, tool_input, cwd), *sorted(roles)])
        for path in method_sources:
            if path.suffix == ".md" and path.is_file():
                pending.extend(
                    target
                    for target in read_triggers.reference_method_targets(
                        path, plugin_resources.plugin_root(), host, project=cwd
                    )
                    if target not in pending and not ready(target)
                )
        # スキル本文とともに届く共有の初動資料を、同じ受領単位へ含める。
        cursor = 0
        while cursor < len(pending):
            target = pending[cursor]
            cursor += 1
            pending.extend(
                method
                for method in read_triggers.reference_method_targets(
                    target.path, plugin_resources.plugin_root(), host, project=cwd
                )
                if method not in pending and not ready(method)
            )
            if not target.heading:
                rows = list(
                    dict.fromkeys(
                        [
                            *rows,
                            *_project_skill_rows(
                                records,
                                agent,
                                (target.path,),
                                known_sources={row.source for row in rows},
                                host="codex" if is_codex_payload(payload) else "claude",
                                project=cwd,
                            ),
                        ]
                    )
                )
            target_identity = document_reads.skill_document(str(target.path))
            for row in rows:
                if (
                    row.kind == "起動時"
                    and row.source == target.path
                    and target_identity is not None
                    and target_identity[1] == "SKILL.md"
                    and not target.heading
                ):
                    selection_rows.append(row)
                    pending.extend(item for item in row.targets if item not in pending and not ready(item))
        # 選択資料の解析で追加した宣言にも、同じ役割境界を適用する。
        rows = [row for row in rows if not row.source.name.endswith(".subagent.md") or row.source in roles]
    except (OSError, ValueError) as error:
        if isinstance(error, PluginRootError):
            return _declarations_unavailable(error)
        if isinstance(error, FileNotFoundError) and isinstance(error.filename, str):
            missing = read_triggers.ReadTarget(pathlib.Path(error.filename))
            return Decision(
                notices=(
                    _llm_notice(
                        f"資料`{missing.path}`は存在しない。",
                        tag=_WARN_TAG,
                        fix=("資料と、読込表の参照先を直せ。直せない場合、委譲先は委譲元へ、それ以外はユーザーへ報告せよ"),
                        removable_cause=True,
                        escalate_on_repeat=False,
                    ),
                )
            )
        if warning_only:
            return Decision(
                notices=(
                    _llm_notice(
                        f"読込表が指す資料を、現在の入力から特定できない: {error}",
                        tag=_WARN_TAG,
                        fix="診断が示す入力と参照先を確かめ、回答に依存する次の操作の前に取得対象を特定して読み、質問の前提を確かめ直せ。",
                        removable_cause=True,
                        escalate_on_repeat=False,
                    ),
                )
            )
        return Decision(
            notices=(
                _llm_notice(
                    f"読込表が指す資料を、現在の入力から特定できない: {error}",
                    tag=_WARN_TAG,
                    fix="診断が示す入力と参照先を確かめ、実在する取得対象を特定して読め。",
                    removable_cause=True,
                    escalate_on_repeat=False,
                ),
            )
        )
    if not pending:
        return Decision()
    # 現在の操作が改めて対象にした資料は、圧縮前の候補から現在の案内対象へ戻す。
    for target in pending:
        carried_key = rules_context.carried_read_target_key(target.path)
        if target not in carried and agent in records.get(carried_key, []):
            read_delivery.update_state(session_id, _unmark(carried_key, agent))
    records = read_delivery.read_state(session_id).get(rules_context.OPERATION_SKILL_READY_KEY, {})
    records = records if isinstance(records, dict) else {}
    if _acquiring(tool_name, tool_input, cwd):
        _select_targets(session_id, pending, agent, rows=selection_rows)
        return Decision()
    acquisition_paths = _read_paths(tool_name, tool_input, cwd)
    if any(row.selector == "project-skill" and read_delivery.row_matches(row, tool_name, tool_input, cwd) for row in rows):
        # 選択したSKILLの初動資料を案内し、取得操作は遮らずPostToolUseで観測する。
        _select_targets(session_id, pending, agent, rows=selection_rows)
        pending = [target for target in pending if target.path not in acquisition_paths]
        if not pending:
            return Decision()
    if any(target.path in acquisition_paths for target in pending):
        _select_targets(session_id, pending, agent, rows=selection_rows)
        pending = [target for target in pending if target.path not in acquisition_paths]
        if not pending:
            # 取得操作を許可し、PostToolUseで対象パスへの読取の成功を観測する。
            # 次の依存操作では取得完了を確かめ、未取得なら関連資料を案内する。
            return Decision()
    if confirmation:
        if not agent_id.is_main_agent_context(payload):
            return Decision()
        history_path = payload.get("transcript_path")
        if isinstance(history_path, str) and history_path and transcript.read_transcript_lines(history_path) is None:
            return Decision()
        if confirmation_pending:
            # 拒否が案内する資料だけを選択済みにする。ともに届く共有の資料まで選択済みにすると、
            # 案内した資料を読んだ後の再発行で、その資料の未取得の警告が質問と一緒に届く。
            _select_targets(session_id, confirmation_pending, agent, rows=selection_rows)
            try:
                steps = _merged_steps(
                    confirmation_pending,
                    {},
                    operation_skills.resolve_subject(payload),
                    invocation_paths=invocation_paths,
                    cwd=cwd,
                )
            except OSError, UnicodeError, ValueError:
                # 取得手段を示せない資料では拒否を解除できないため、後続の警告で宣言の復旧を案内する。
                steps = []
            if steps:
                # 警告は回答と一緒に届き、基準を適用しないまま質問する害を防げない。未取得が解消するまで毎回拒否する。
                return Decision(
                    block=_block_notice(
                        "この質問はまだユーザーへ表示していない。確認の前に全文を読むと定めた資料のうち、"
                        "この文脈で全文の取得を確認できないものがある（一部だけの読取と、行を除いた表示は取得に数えない）。",
                        fix="。\n".join(steps)
                        + "。読んだ基準で、質問するか自分で判断するかを決め直せ。質問する場合は同じ呼び出しを出し直せ。"
                        + _question_material_guidance(payload, tool_name),
                    )
                )
    _select_targets(session_id, pending, agent, rows=selection_rows)
    pending = [target for target in pending if agent not in records.get(guidance_key(target), [])]
    if not pending:
        return Decision()
    # 同じ資料の節違いは取得案内をまとめる。
    uninvoked = [
        target for target in pending if target.path in invocation_paths and read_delivery.target_ready(records, target, agent)
    ]
    label = "未起動スキル" if len(uninvoked) == len(pending) else "未取得資料・未起動スキル" if uninvoked else "未取得資料"
    subject = operation_skills.resolve_subject(payload)
    if subject == operation_skills.SUBJECT_CODEX:
        methods = "指定されたシェルコマンド"
    else:
        has_skill = any(target.path in invocation_paths for target in pending)
        has_read = any(target.path not in invocation_paths for target in pending)
        methods = "指定されたSkill・Read" if has_skill and has_read else "指定されたSkill" if has_skill else "指定されたRead"
    followup = "読んだ後に、書いた内容をその基準で見直し、必要なら直せ。" if written and not diagnostic else ""
    # 受け取る側は「既に読んだ」と考えていることがある。取得に数えない読み方を示し、誤判定との取り違えを防ぐ。
    instruction = (
        f"{label}がある。この文脈では全文の取得（スキルは起動）を確認できていない。"
        "過去に取得済みでも、会話圧縮で本文が文脈から失われた場合は再取得が必要である。"
        "一部だけの読取、行を除いた表示、パイプや変数を使った表示は取得に数えない。"
        f"この操作の結果を使う前に{methods}で必要な基準を取得せよ。"
    )
    failures: dict[read_triggers.ReadTarget, str] = {}
    for target in pending:
        try:
            acquisition_step(target, subject, reference=target.path not in invocation_paths, cwd=cwd)
        except (OSError, UnicodeError, ValueError) as error:
            repairs = {row.source.resolve() for row in selection_rows if target in row.targets} | {target.path}
            writes = operation_skills.written_paths(tool_name, tool_input, cwd)
            if not warning_only and writes and all(pathlib.Path(path).resolve() in repairs for path in writes):
                return Decision(
                    notices=(
                        _llm_notice(
                            f"読込表が指す節と実際の見出しが一致しない。読込表か資料を直す操作のため、今回は通す: {error}",
                            tag=_WARN_TAG,
                            fix="読込表と実際の見出しを整合させ、必要な本文を取得してから元の判断・操作へ戻れ。",
                            removable_cause=True,
                            escalate_on_repeat=False,
                        ),
                    )
                )
            failures[target] = rules_context.acquisition_failure(
                target,
                error,
                shell=subject == operation_skills.SUBJECT_CODEX,
                agent=subject == operation_skills.SUBJECT_SUBAGENT,
                reference=target.path not in invocation_paths,
                cwd=cwd,
            )
            if not target.path.is_file():
                # 欠けているのが宣言元と同じ版の資料か、別版を解決した結果かを区別できるよう宣言元を示す。
                origins = sorted({str(row.source) for row in selection_rows if target in row.targets})
                if origins:
                    failures[target] += f"。宣言元: {'、'.join(f'`{origin}`' for origin in origins)}"
    acquisition = "。\n".join(_merged_steps(pending, failures, subject, invocation_paths=invocation_paths, cwd=cwd)) + "。"
    if subject == operation_skills.SUBJECT_CODEX:
        acquisition += "内外の出力枠を確保し、収まらない範囲は分割して取得せよ。"
    if warning_only:
        acquisition += "質問の前提と選択肢を資料の基準で確かめ直せ。"
    notice = _llm_notice(
        instruction,
        tag=_WARN_TAG,
        removable_cause=True,
        escalate_on_repeat=False,
        fix=acquisition + followup,
        subject=json.dumps([guidance_key(target) for target in pending]),
    )
    for target in pending:
        notice_module.stage_delivery(notice, session_id, rules_context.OPERATION_SKILL_READY_KEY, guidance_key(target), agent)
    return Decision(notices=(notice,))


def _merged_steps(
    targets: list[read_triggers.ReadTarget],
    failures: dict[read_triggers.ReadTarget, str],
    subject: str,
    *,
    invocation_paths: set[pathlib.Path] | None = None,
    cwd: str = "",
) -> list[str]:
    """同じ資料の取得手段を1件へまとめ、注目する節を併記する。

    節ごとに取得案内を並べると、共通の前提まで繰り返し読ませる。
    読取または見出しの解決に失敗した対象は、復旧の案内を個別に残す。
    """
    groups: dict[pathlib.Path | read_triggers.ReadTarget, list[str]] = {}
    for target in targets:
        if target in failures:
            groups[target] = []
        else:
            groups.setdefault(target.path, []).append(target.heading)
    steps = []
    for key, headings in groups.items():
        if isinstance(key, read_triggers.ReadTarget):
            steps.append(failures[key])
            continue
        named = [heading for heading in dict.fromkeys(headings) if heading]
        heading = (
            "" if "" in headings else "".join(value if "「" in value or value == "冒頭" else f"「{value}」" for value in named)
        )
        step = acquisition_step(
            read_triggers.ReadTarget(key, heading),
            subject,
            reference=invocation_paths is not None and key not in invocation_paths,
            cwd=cwd,
        )
        steps.append(step)
    return steps
