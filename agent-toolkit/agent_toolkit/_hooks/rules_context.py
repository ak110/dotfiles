"""実行主体ごとの規範を起動時の文脈へ追加するhandler。

委譲先かどうかは`_common.delegated_session`が判定する。

出力する自動挿入本文は`atk-auto`で囲む。process-loopが渡したユーザー自身の入力は
`forwarded-user-input`で囲む。読み手のエージェントが区分ごとに生成主体と種別を
判別できるようにするためであり、区分ごとに囲んだ本文を全体で重ねて囲まない。

Claude CodeはJSONのadditionalContextを10,000文字で保存先とプレビューへ置き換える。
条文をそのまま届けるため、最大構成の追加文脈を同じ上限へ収める契約テストを置く。
"""

from __future__ import annotations

import contextlib
import html
import json
import os
import pathlib
import time
lazy import re
lazy import shlex
lazy import subprocess
lazy import sys

# 関数の実行時にだけ使う依存は、使用時まで読み込まない。
lazy import urllib.parse
lazy from typing import Any

from agent_toolkit._atk import managed_temp
from agent_toolkit._common import mcp_environment, message_format, plugin_environment, read_triggers
from agent_toolkit._hooks import plugin_resources
from agent_toolkit._hooks.notice import forget_subject_notices
from agent_toolkit._hooks.notice import formatter as _notice_formatter
lazy from agent_toolkit._common import host_homes, process_tree, wait_schedule
lazy from agent_toolkit._common.delegated_session import is_delegated
lazy from agent_toolkit._common.json_lines import split_lines
lazy from agent_toolkit._common.session_state import read_state, update_state
lazy from agent_toolkit._hooks import document_reads, timing, work_contract
lazy from agent_toolkit._hooks.agent_id import MAIN_AGENT_ID
lazy from agent_toolkit._hooks.message_format import xml_message

_HOOK_ID = "rules_context"
_llm_notice = _notice_formatter(_HOOK_ID)

RESPONSE_LANGUAGE_NOTICE = "言語の基準は、同時配送された`01-agent.md`「使用言語」を参照せよ。"
QUALITY_CHECKPOINT_NOTICE = "圧縮後の復元は`01-agent.md`「行動と手順の目的」に従え。"
BACKGROUND_TASKS_DISABLED_NOTICE = (
    "この`Claude Code`では`Bash`の背景実行が無効（`CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`）で、`run_in_background`は使えない。"
    "`atk agents wait`は単独の前景実行とし、`Bash`へ`timeout=600000`を指定して、"
    "待機コマンド自身の期限で返る結果と終了コードを回収せよ。未終了なら同じコマンドを再発行せよ。"
)
"""背景実行を無効にした起動で、使える待機方法をメインと委譲先へ伝える本文。

委譲先は親の環境変数を継承するため、同じ条件でメインに限らず通知する。
"""

SHARE_DIR = plugin_resources.plugin_root() / "share"
# SHARE_DIRはplugin rootの算出にも使うため基点を保ち、役割別の規範だけ`share/rules/`から読む。
RULES_DIR = SHARE_DIR / "rules"
MAIN_RULES_PATH = RULES_DIR / "rules-main.md"
MAIN_RULES_CLAUDE_CODE_PATH = RULES_DIR / "rules-main.claude-code.md"
MAIN_RULES_CODEX_PATH = RULES_DIR / "rules-main.codex.md"
SUBAGENT_RULES_PATH = RULES_DIR / "rules-subagent.md"
SUBAGENT_RULES_CLAUDE_CODE_PATH = RULES_DIR / "rules-subagent.claude-code.md"
CLAUDE_CODE_OUTPUT_LIMIT = 10_000
# 圧縮後に作業契約の後へ続く通知（原文の抽出先、資料の再取得案内）へ残す文字数。
_COMPACTION_RESERVE = 1_500
# 作業契約の通知の囲みと固定文の文字数の見込み。本文を載せるかの判定に使う。
_CONTRACT_NOTICE_OVERHEAD = 600
# `atk wi process-loop instruct`が保持し、process-loopがセッション起動時に渡す本文。
PROCESS_LOOP_INSTRUCTION_ENV = "AGENT_TOOLKIT_PROCESS_LOOP_INSTRUCTION"
PROCESS_LOOP_INSTRUCTION_ELEMENT = message_format.FORWARDED_USER_INPUT_ELEMENT
# 実行主体へ常時読み込ませる規範の境界。種別で読み手を区別する。
NORMATIVE_ELEMENT = message_format.AUTO_INSERTED_ELEMENT
NORMATIVE_SOURCE = "agent-toolkit"
NORMATIVE_KIND_MAIN = "rules-main"
NORMATIVE_KIND_SUBAGENT = "rules-subagent"


def compose_session_start(source: str, *, delegated: bool, host: str) -> str | None:
    """SessionStartへ追加する本文を構成する。

    冒頭には常時規範の使用言語への参照を置き、メインの最初の応答前にも定義元を示す。
    圧縮時の復元基準への参照も同じ通知へまとめ、文字数上限内で必要な参照を届けるために本文や標識を重ねない。
    委譲先にはメイン向けの言語通知を追加しない。

    process-loopが渡した追加指示も、メインだけが受け取る入力として先頭へ置く。委譲先は元の作業の一部を
    担うに過ぎず、この指示の宛先ではない。

    ホスト別のメイン向け規範は`rules-main.md`の後へ置く。Codexの全主体へ届く差分は`~/.codex/AGENTS.md`が
    配送するため、ここではCodexのメインだけに適用する`rules-main.codex.md`を加える。
    """
    parts: list[str] = []
    if not delegated:
        if raw := os.environ.get(mcp_environment.SESSION_START_INPUT_ENV):
            data = json.loads(raw)
            if not isinstance(data, dict) or data.get("source") not in ("process-loop", "run-skill"):
                raise ValueError("起動入力はsourceがprocess-loopまたはrun-skillのJSON objectで指定する")
            references = (
                "agent-toolkit:process-wiのreferences/all-repos.md「結果と終了」"
                if data["source"] == "process-loop"
                else "agent-toolkit:user-confirmation-and-reportのreferences/answer-transitions.md"
                "「`atk run-skill`の過去の実行のUWI」とagent-toolkit:report-completion"
            )
            if data["source"] == "run-skill" and data.get("batch_manifest"):
                references += "、agent-toolkit:review-sessionのreferences/batch.md"
            parts.append(
                message_format.auto_message(
                    "起動入力: "
                    + html.escape(json.dumps(data, ensure_ascii=False))
                    + "\n指定スキルを起動し、次の終了契約を読め: "
                    + references,
                    source=data["source"],
                    kind="startup-input",
                )
            )
        instruction = os.environ.get(PROCESS_LOOP_INSTRUCTION_ENV, "").strip()
        if instruction:
            parts.append(
                xml_message(
                    PROCESS_LOOP_INSTRUCTION_ELEMENT,
                    instruction,
                    {
                        "from": "agent-toolkit/process-loop",
                        "origin": "user",
                        "source": "atk wi process-loop instruct",
                        "scope": "element body",
                    },
                )
            )
    normative_parts: list[str] = []
    rule_notices: list[str] = []
    if not delegated:
        rule_notices.append(RESPONSE_LANGUAGE_NOTICE)
    if source == "compact":
        rule_notices.append(QUALITY_CHECKPOINT_NOTICE)
    if rule_notices:
        parts.append(_llm_notice("\n".join(rule_notices)))
    if host == "claude" and wait_schedule.background_tasks_disabled():
        parts.append(_llm_notice(BACKGROUND_TASKS_DISABLED_NOTICE))
    if not delegated:
        normative_parts.append(MAIN_RULES_PATH.read_text(encoding="utf-8").rstrip("\n"))
        if host == "claude":
            normative_parts.append(MAIN_RULES_CLAUDE_CODE_PATH.read_text(encoding="utf-8").rstrip("\n"))
        elif host == "codex":
            normative_parts.append(MAIN_RULES_CODEX_PATH.read_text(encoding="utf-8").rstrip("\n"))
    if normative_parts:
        parts.append(_normative_context("\n\n".join(normative_parts), kind=NORMATIVE_KIND_MAIN))
    parts.append(_llm_notice(plugin_environment.cli_instructions(plugin_resources.plugin_root())))
    return "\n\n".join(parts) or None


USER_CONFIRMATION_PENDING_KEY = "user_confirmation_skill_pending"
"""文脈ごとに`agent-toolkit:user-confirmation-and-report`の起動案内を一度届ける配送待ちのキー。"""
_CONTEXT_LOSING_SOURCES = frozenset({"startup", "clear", "compact"})
"""会話の文脈を引き継がずに始まるSessionStartの`source`。`resume`と`fork`は文脈を引き継ぐため含めない。"""
OPERATION_SKILL_READY_KEY = "operation_skill_delivered_agents"
"""操作を起動の契機とするスキルの必須資料の識別子から、取得済みの呼び出し主体の一覧への対応を持つセッション状態のキー。

PostToolUseが成功したSkill起動・対象パスの読取を観測した時点で記録する。
資料の実体・内容hash・主体に結び付け、選択した資料、入力と文脈境界も同じ対応表へ保持する。
キー名は既存の読み取り側との互換を保つ。本文を返さない未取得警告は取得済みとして記録しない。
"""
_OPERATION_SKILL_RESET_SOURCES = frozenset({"clear", "compact"})
"""メイン会話の起動済みの記録を除くSessionStartの`source`。新しいセッションの`startup`は記録を持たない。"""
READ_CONTEXT_RESET_KEY = "startup-context-reset"
"""最初の操作より前の圧縮でも、履歴上の起動本文の取得を再計上しないための主体識別子。"""
CARRIED_READ_TARGET_PREFIX = "read-guidance-carried:"
"""文脈境界の前に選んだ資料を、境界の後に改めて案内するまで再書込の遮断根拠から外す記録キーの接頭辞。

境界で案内の記録は消えるが選択の記録は残るため、持ち越した候補を境界後の初回書込で案内済みとすると、
現在の操作と関係しない過去の候補だけを根拠に次の書込が止まる。
"""


def carried_read_target_key(path: pathlib.Path) -> str:
    """持ち越しの印の記録キーを返す。

    境界の後に資料が変わっても境界前の選択を持ち越しとして扱い続けるため、本文ハッシュを含めない。
    """
    return CARRIED_READ_TARGET_PREFIX + str(path.resolve())


def reset_main_operation_skill_ready(session_id: str) -> None:
    """メイン会話の起動済みの記録を除く。

    `clear`と`compact`ではスキルの本文が文脈から外れるため、次の操作で再び取得を求める。
    サブエージェントの記録は親の会話圧縮と独立した文脈のため変えない。
    """

    def _reset(current: dict) -> dict | None:
        recorded = current.get(OPERATION_SKILL_READY_KEY)
        if not isinstance(recorded, dict):
            recorded = {}
        updated = {
            name: (
                [agent for agent in agents if agent != MAIN_AGENT_ID]
                if isinstance(agents, list)
                and not name.startswith(
                    (
                        "modified-agent-document:",
                        "review-reference:",
                        "read-trigger-role:",
                        "selected-read-target:",
                        "startup-input-target:",
                        "startup-context-observed",
                        "resolved-project-skill:",
                        "review-output:",
                        "review-output-parsed:",
                    )
                )
                else agents
            )
            for name, agents in recorded.items()
        }
        for name, agents in recorded.items():
            if not name.startswith("selected-read-target:") or not isinstance(agents, list) or MAIN_AGENT_ID not in agents:
                continue
            try:
                path = pathlib.Path(json.loads(name.removeprefix("selected-read-target:"))["path"])
            except KeyError, TypeError, ValueError:
                continue
            updated[carried_read_target_key(path)] = [MAIN_AGENT_ID]
        # 最初の操作より前に圧縮された場合も、旧起動本文を取得済みへ戻さない。
        updated[READ_CONTEXT_RESET_KEY] = [MAIN_AGENT_ID]
        # 取得マッピングが同じでも、各文脈境界で候補とrollout位置を初期化する。
        current[OPERATION_SKILL_READY_KEY] = updated
        current.get("document_read_candidates", {}).pop(MAIN_AGENT_ID, None)
        current.get("document_read_pending", {}).pop(MAIN_AGENT_ID, None)
        current.get("document_read_spools", {}).pop(MAIN_AGENT_ID, None)
        current.get("result_read_pending", {}).pop(MAIN_AGENT_ID, None)
        evidence = current.get("termination_evidence")
        if isinstance(evidence, dict):
            evidence.pop("pending_reads", None)
        for key in current.get("document_read_offsets", {}):
            actor, path = json.loads(key)
            if actor == MAIN_AGENT_ID:
                with contextlib.suppress(OSError):
                    current["document_read_offsets"][key] = pathlib.Path(path).stat().st_size
        return current

    update_state(session_id, _reset)


def shell_acquisition_conditions() -> str:
    """取得観測が受理するシェル実行と、取得後の再試行を案内する。"""
    return (
        "必要範囲の本文を実出力として返せ。省略・退避した部分は実際に回収してから進め。"
        "取得後は、止められた元の書き込みを再発行できる"
    )


def acquisition_failure(
    target: read_triggers.ReadTarget,
    error: Exception,
    *,
    shell: bool,
    agent: bool = False,
    reference: bool = False,
    cwd: str = "",
) -> str:
    """資料読取または見出し解決の失敗を、主体に応じた取得または復旧へ導く。"""
    path = target.path
    if not path.is_file():
        return (
            f"資料`{path}`は存在しない。資料と読込宣言の参照先を復旧せよ。"
            "復旧できない場合、委譲先は委譲元へ、それ以外はユーザーへ報告せよ"
        )
    prefix = f"`{target}`の読取または注目見出しの解決に失敗: {error}。"
    if target.heading and isinstance(error, ValueError) and not isinstance(error, UnicodeError):
        prefix += "読込宣言の見出しと資料を整合させよ。"
    if shell:
        return prefix + f"全文を`cat -- {shlex.quote(str(path))}`で読め。" + shell_acquisition_conditions()
    skill = document_reads.skill_document(str(path))
    if (
        not reference
        and skill
        and skill[1] == "SKILL.md"
        and (
            agent
            and document_reads.skill_loadable(path, cwd=cwd)
            or document_reads.requires_skill_invocation(path, host="claude", subject="main", cwd=cwd)
        )
    ):
        owner = next((parent for parent in path.parents if (parent / ".claude-plugin/plugin.json").is_file()), None)
        name = f"{document_reads.document_plugin(str(path))}:{skill[0]}" if owner else skill[0]
        step = f"`Skill`で`{name}`を起動せよ（資料: `{path}`）"
        if agent:
            step += f"。`Skill`を使えない場合は`{path}`をReadで全文読め"
        return prefix + step
    step = f"`{path}`をReadで全文読め"
    if skill and not document_reads.skill_loadable(path, cwd=cwd):
        step += f"。Bashでは`cat -- {shlex.quote(str(path))}`で取得せよ"
    return prefix + step


def _compact_recovery_context() -> str:
    """次の判断前に、現在の工程を定める文書から必要な知識を回復させる。"""
    return _llm_notice(
        "会話が圧縮され、以前取得した資料の本文は文脈から失われている。次の判断より前に、"
        "圧縮前に進めていた工程を定めるスキルと役割文書を全文読み直し、"
        "その読込表から次の判断に必要な資料を選んで読め。"
        "委譲先の返却、背景待機の完了、定期の再確認、ユーザーの回答への対応も含む。"
        "全体作業契約と引き継ぎ記録が次工程の資料を指す場合は、それも読め。"
        "圧縮前の読取履歴を一括再取得の対象にせず、現在の工程と判断から選べ。"
    )


def mark_user_confirmation_pending(session_id: str) -> None:
    """文脈ごとの`agent-toolkit:user-confirmation-and-report`の案内待ちを記録する。

    同スキルは内容が文脈に無い状態で受けた実ユーザー発話の前に起動する契機を持つ。
    UserPromptSubmitが実入力へ一度案内して解除する。先行したSkill起動でも解除する。
    """

    def _mark(current: dict) -> dict | None:
        if current.get(USER_CONFIRMATION_PENDING_KEY) is True:
            return None
        current[USER_CONFIRMATION_PENDING_KEY] = True
        return current

    update_state(session_id, _mark)


def _normative_context(body: str, *, kind: str) -> str:
    """規範本文へ生成主体と種別を持つ境界を付ける。"""
    return message_format.auto_message(body, source=NORMATIVE_SOURCE, kind=kind)


def session_temp_notice(session_temp: pathlib.Path | str, *, contract: pathlib.Path | None = None) -> str:
    """セッションのmanaged-tempの所在と、一時ファイルの置き場所を選ぶ時点で従う行動を対にした通知本文を返す。

    所在だけを示す通知では、一時ファイルを作成する時点で`/tmp`が選ばれ、後始末の削除が権限判定に拒否された。
    置き場所の選択と結び付く語（`/tmp`）と、委譲先へ所在を渡す行動を同じ本文へ置く。
    """
    if contract is not None and contract.parent == pathlib.Path(session_temp):
        location = "このセッションのmanaged-tempは、次の原本の親ディレクトリである。\n"
    else:
        location = f"このセッションのmanaged-temp: {session_temp}\n"
    if contract is not None:
        location += work_contract.short_notice(contract) + "\n"
    return location + (
        "一時ファイルは直下へ置け。`/tmp`は使うな。"
        "委譲先のmanaged-temp・専用一時領域の通知を優先せよ。"
        "未通知ならこの領域を指定せよ。指定の入出力ファイルは読み書きしてよい。"
    )


def _resolve_transcript(session_id: object, transcript_path: object) -> str | None:
    """入力値の実在か同じIDのメイン記録の一意性からClaude記録を解決する。"""
    if not isinstance(transcript_path, str) or not (
        pathlib.PurePosixPath(transcript_path).is_absolute() or pathlib.PureWindowsPath(transcript_path).is_absolute()
    ):
        return None
    try:
        if pathlib.Path(transcript_path).is_file():
            return transcript_path
        if not isinstance(session_id, str) or re.fullmatch(r"[A-Za-z0-9_-]+", session_id) is None:
            return transcript_path
        projects = host_homes.claude_config_dir() / "projects"
        candidates = (
            [path for project in projects.iterdir() if project.is_dir() and (path := project / f"{session_id}.jsonl").is_file()]
            if projects.is_dir()
            else []
        )
        return str(candidates[0]) if len(candidates) == 1 else transcript_path if not candidates else None
    except OSError:
        return None


def _conversation_identity_notice(session_id: object, transcript_path: object) -> str:
    """Claudeメイン自身の原文を取得する操作へ、入力から解決した所在を束縛する。"""
    arguments = [str(SHARE_DIR.parent / "scripts/atk"), "run-script", "session-review-evidence", "--"]
    resolved = _resolve_transcript(session_id, transcript_path)
    if resolved is not None:
        arguments.extend(["--transcript", resolved])
    elif isinstance(session_id, str) and session_id:
        arguments.extend(["--claude-session-id", session_id])
    else:
        return (
            "会話の原文の所在を入力から取得できない。CLAUDE_CODE_SESSION_IDを確認し、"
            "`atk run-script session-review-evidence -- --claude-session-id <確認したID> --user-events`で取得せよ。"
        )
    return f"この会話の原文が必要な場合は`{shlex.join([*arguments, '--user-events'])}`で取得せよ。"


def _existing_session_temp(session_id: object) -> str | None:
    """SessionStartが作成済みのセッションのmanaged-tempを、新たに作成せずに解決する。

    サブエージェントは親と同じsession_idを受け取るため、親のセッションのmanaged-tempを解決できる。
    SubagentStartでは領域を作成しない。親のSessionStartが作成していない場合は通知しない。
    """
    if not isinstance(session_id, str) or not session_id:
        return None
    try:
        path = managed_temp.find_session_temp(session_id)
    except managed_temp.ManagedTempError, OSError:
        return None
    return str(path) if path is not None else None


def _compaction_notice(
    session_id: str, transcript_path: object, session_temp: pathlib.Path | None, *, delegated: bool
) -> str | None:
    """既存の証拠抽出CLIで全人間入力を保存し、直近の会話の取得先も通知する。"""
    resolved = _resolve_transcript(session_id, transcript_path)
    if resolved is None:
        arguments = ["run-script", "session-review-evidence", "--", "--claude-session-id", session_id, "--user-events"]
        return f"原文は起動部分へ`{shlex.join(arguments)}`を続けて取得せよ。"
    command = [
        str(SHARE_DIR.parent / "scripts" / "atk"),
        "run-script",
        "session-review-evidence",
        "--",
        "--transcript",
        resolved,
        "--user-events",
    ]
    conversation = shlex.join(
        [
            str(SHARE_DIR.parent / "scripts" / "atk"),
            "run-script",
            "session-review-prepare",
            "--",
            "--transcript",
            resolved,
            "--conversation-only",
            "--conversation-window",
            "--conversation-recent",
            "30",
        ]
    )
    # 復元で先に読むのは発話の原文と作業契約・計画・引き継ぎ記録である。会話は進行中の操作を補う直近の少量表示を案内し、
    # 全期間の通常表示（数万文字）は不足する場合の手段にとどめる。
    lines = [
        f"圧縮前の直近の操作は`{conversation}`でconversation.mdを取得して確かめよ。"
        "それでも目的や経緯を復元できない場合だけ、"
        "同じコマンドから`--conversation-window --conversation-recent 30`を外して全期間の流れを取得せよ。"
        "原文・結果と継続取得はskills/review-session/references/session-records.md「会話の少量取得」に従え。"
    ]
    if delegated:
        return "\n".join(lines)
    output = session_temp / "compaction-user-events.jsonl" if session_temp is not None else None
    try:
        if output is None:
            raise ValueError("セッションのmanaged-tempを取得できない")
        # 同じ依存環境のPythonで公開スクリプトを呼び、起動ごとの環境解決を重ねない。
        result = process_tree.run_with_timeout(
            [
                sys.executable,
                str(SHARE_DIR.parent / "skills/review-session/scripts/session_review_evidence.py"),
                "--transcript",
                resolved,
                "--user-events",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=True,
            env=plugin_environment.direct_environment(SHARE_DIR.parent, os.environ),
        )
        users = [
            event
            for line in split_lines(result.stdout)
            if isinstance(event := json.loads(line), dict) and event.get("kind") == "user"
        ]
        data = "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in users)
        output.write_text(data, encoding="utf-8")
        key = document_reads.selected_target_key(read_triggers.ReadTarget(output))

        def select_original(state: dict) -> dict:
            records = state.setdefault(OPERATION_SKILL_READY_KEY, {})
            actors = records.setdefault(key, [])
            if MAIN_AGENT_ID not in actors:
                actors.append(MAIN_AGENT_ID)
            return state

        update_state(session_id, select_original)
        lines.append(
            f"ユーザー発話の原文{len(users)}件をReadのfile_pathに`{output}`を指定して読め。"
            "ユーザーの要求・判断・行動の訂正を原文と作業契約で比較し、有効な指示を統合して元の未完了工程へ戻れ。"
        )
    except process_tree.ReclaimError as error:
        # hookは同じ所有対象を次の起動まで保持できないため、その場で回収し直し、残れば対象を示す。
        remaining = error.retry()
        if remaining:
            lines.append(f"ユーザー発話の抽出の子プロセスを回収できない: {remaining}。PIDと起動コマンドを確かめて終了させよ。")
        lines.append(
            f"ユーザー発話の抽出に失敗した（{type(error).__name__}: {error}）。手動で`{shlex.join(command)}`を実行せよ。"
        )
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        if output is not None:
            try:
                output.unlink(missing_ok=True)
            except OSError:
                lines.append(
                    f"前回の抽出ファイル`{output}`を削除できないため、そのファイルは使用するな。次のコマンドで原文を再取得せよ。"
                )
        lines.append(
            f"ユーザー発話の抽出に失敗した（{type(error).__name__}: {error}）。手動で`{shlex.join(command)}`を実行せよ。"
        )
    return "\n".join(lines)


def _subagent_temp_notice(session_temp: str, agent_id: object) -> str:
    """担当ごとの置き場所と、親の領域でのファイルの受渡しを通知する。"""
    if not isinstance(agent_id, str) or not agent_id:
        return session_temp_notice(session_temp)
    # IDを単一のパス要素に保ち、同じIDの再開でも同じ場所へ到達する。
    child = pathlib.Path(session_temp) / f"agent-{urllib.parse.quote(agent_id, safe='')}"
    child.mkdir(exist_ok=True)
    return (
        f"このサブエージェント専用の一時領域: {child}\n"
        "一時ファイルはこの領域の直下へ置け。`/tmp`は使うな。"
        f"親のセッションのmanaged-temp: {session_temp}\n"
        "親の領域は、委譲元が個別に指定した入力や出力のファイルの読み書きに使ってよい。"
    )


def compose_subagent_start(*, host: str) -> str:
    """SubagentStartへ追加する本文を返す。"""
    parts = [SUBAGENT_RULES_PATH.read_text(encoding="utf-8").rstrip("\n")]
    if host == "claude":
        parts.append(SUBAGENT_RULES_CLAUDE_CODE_PATH.read_text(encoding="utf-8").rstrip("\n"))
    return (
        _normative_context("\n\n".join(parts), kind=NORMATIVE_KIND_SUBAGENT)
        + "\n\n"
        + _llm_notice(plugin_environment.cli_instructions(SHARE_DIR.parent))
    )


def _parse_payload(payload_text: str) -> dict[str, Any]:
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        raise ValueError("フック入力JSONの解析に失敗した") from exc
    if not isinstance(payload, dict):
        raise ValueError("フック入力はJSON objectである必要がある")
    return payload


def main(payload_text: str, *, host: str = "claude") -> int:
    """SessionStartまたはSubagentStartのpayloadを処理する。"""
    payload = _parse_payload(payload_text)
    event_name = payload.get("hook_event_name")
    session_id = payload.get("session_id")
    delegated = is_delegated(os.environ)
    if event_name == "SessionStart":
        source = payload.get("source")
        if not isinstance(source, str):
            raise ValueError("sourceは文字列である必要がある")
        session_temp = None
        if isinstance(session_id, str) and session_id:
            with contextlib.suppress(managed_temp.ManagedTempError, OSError):
                session_temp = managed_temp.resolve_session_temp(session_id)
        if source in _CONTEXT_LOSING_SOURCES and isinstance(session_id, str) and session_id:
            # 同じセッションIDでも受信文脈は失われているため、既送の通知を本文の構成前に未送へ戻す。
            forget_subject_notices(session_id, MAIN_AGENT_ID)
        started = time.monotonic()
        content = compose_session_start(source, delegated=delegated, host=host)
        timing.record(payload, "rules_context", started, "ok", stage="normative-content")
        from agent_toolkit._atk import run_skill_batch  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

        started = time.monotonic()
        review_notice = run_skill_batch.hook_request(payload, host=host) if host in {"claude", "codex"} else None
        timing.record(payload, "rules_context", started, "ok", stage="review-session-request")
        if review_notice is not None:
            content = f"{content}\n\n{_llm_notice(review_notice)}" if content else _llm_notice(review_notice)
        if not delegated and host == "claude" and source != "compact":
            identity_context = _llm_notice(_conversation_identity_notice(session_id, payload.get("transcript_path")))
            content = f"{content}\n\n{identity_context}" if content else identity_context
        if isinstance(session_id, str) and session_id:
            if host == "claude":
                # registry/MDM変更も同IDのresume・再起動で反映する。配送記録など他の状態は保つ。
                update_state(
                    session_id,
                    lambda state: (
                        {key: value for key, value in state.items() if key != "plugin_inventory"}
                        if "plugin_inventory" in state
                        else None
                    ),
                )
            if source in _OPERATION_SKILL_RESET_SOURCES:
                reset_main_operation_skill_ready(session_id)
            if not delegated and source in _CONTEXT_LOSING_SOURCES:
                mark_user_confirmation_pending(session_id)
            if source == "compact":
                work_contract.reset_notice(session_id)
                if delegated:
                    state = read_state(session_id)
                    contract_update = work_contract.delegated_notice(
                        session_id,
                        state.get(OPERATION_SKILL_READY_KEY, {}),
                        state,
                    )
                    if contract_update:
                        content = f"{content}\n\n{_llm_notice(contract_update)}"
            if session_temp is not None:
                temp_text = session_temp_notice(session_temp)
                contract_context = None
                if source == "compact" and not delegated and work_contract.exists(session_id):
                    # 圧縮後の通知と資料の再取得案内にも上限内の余白を残す。契約の本文と案内が収まらなければ、
                    # 一時領域の通知へ契約の所在の1行だけを加え、最終出力全体を上限へ収める。
                    used = len(content or "") + len(_llm_notice(temp_text)) + 4
                    budget = CLAUDE_CODE_OUTPUT_LIMIT - used - _COMPACTION_RESERVE
                    contract_notice = work_contract.notice(
                        session_id, budget=max(0, min(work_contract.NOTICE_BODY_LIMIT, budget - _CONTRACT_NOTICE_OVERHEAD))
                    )
                    if contract_notice is not None and len(contract_notice) + _CONTRACT_NOTICE_OVERHEAD <= budget:
                        contract_context = _llm_notice(contract_notice)
                    else:
                        temp_text = session_temp_notice(session_temp, contract=work_contract.record_path(session_id))
                temp_context = _llm_notice(temp_text)
                content = f"{content}\n\n{temp_context}" if content else temp_context
                if contract_context:
                    content += f"\n\n{contract_context}"
            if source == "compact" and host == "claude":
                started = time.monotonic()
                compact_text = _compaction_notice(session_id, payload.get("transcript_path"), session_temp, delegated=delegated)
                timing.record(payload, "rules_context", started, "ok", stage="compaction-recovery")
                if compact_text:
                    compact = _llm_notice(compact_text)
                    content = f"{content}\n\n{compact}" if content else compact
            if source == "compact":
                # ツールを使わない返却・回答の判断にも、工程文書からの復元を先に案内する。
                recovery = _compact_recovery_context()
                content = f"{content}\n\n{recovery}" if content else recovery
    elif event_name == "SubagentStart":
        content = compose_subagent_start(host=host)
        session_temp = _existing_session_temp(payload.get("session_id"))
        if session_temp is not None:
            content = f"{content}\n\n{_llm_notice(_subagent_temp_notice(session_temp, payload.get('agent_id')))}"
    else:
        raise ValueError("hook_event_nameはSessionStartまたはSubagentStartである必要がある")

    if content is not None:
        if (
            host == "claude"
            and event_name == "SessionStart"
            and not delegated
            and len(content) > CLAUDE_CODE_OUTPUT_LIMIT
            and (raw := os.environ.get(mcp_environment.SESSION_START_INPUT_ENV))
        ):
            # 可変引数でhook全体がプレビューに置換されると、終了契約も失われる。
            # 大きい実行データだけをセッションのmanaged-tempへ保存し、参照と契約の導線を配送する。
            if not isinstance(session_id, str) or not session_id:
                raise ValueError("大きい起動入力の保存にはSessionStartのsession_idが必要")
            path = managed_temp.resolve_session_temp(session_id) / "startup-input.json"
            path.write_text(raw, encoding="utf-8")
            encoded = html.escape(json.dumps(json.loads(raw), ensure_ascii=False))
            content = content.replace(
                "起動入力: " + encoded,
                "起動入力ファイル: " + html.escape(str(path)) + "。全文を読め。",
                1,
            )
        # 本文は区分ごとに境界を持つため、全体を重ねて囲まない。
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": event_name,
                        "additionalContext": content,
                    }
                },
                ensure_ascii=False,
            )
        )
    return 0
