"""実行主体ごとの規範を起動時の文脈へ追加するhandler。

委譲先かどうかは`_common.delegated_session`が判定する。

出力する自動挿入本文は`atk-auto`で囲む。process-loopが渡したユーザー自身の入力は
`forwarded-user-input`で囲む。読み手のエージェントが区分ごとに生成主体と種別を
判別できるようにするためであり、区分ごとに囲んだ本文を全体で重ねて囲まない。

Claude CodeはJSONのadditionalContextを10,000文字で保存先とプレビューへ置き換える。
条文をそのまま届けるため、最大構成の追加文脈を同じ上限へ収める契約テストを置く。
"""

from __future__ import annotations

import json
import os
import pathlib
lazy import re
lazy import shlex
lazy import subprocess
lazy import sys

# 関数の実行時にだけ使う依存は、使用時まで読み込まない。
lazy import urllib.parse
lazy from typing import Any

from agent_toolkit._atk import managed_temp
from agent_toolkit._common import message_format, plugin_environment, read_triggers
from agent_toolkit._hooks import plugin_resources
from agent_toolkit._hooks.notice import formatter as _notice_formatter
lazy from agent_toolkit._common import host_homes, process_tree
lazy from agent_toolkit._common.delegated_session import is_delegated
lazy from agent_toolkit._common.session_state import update_state
lazy from agent_toolkit._hooks import document_reads, work_contract
lazy from agent_toolkit._hooks.agent_id import MAIN_AGENT_ID
lazy from agent_toolkit._hooks.message_format import xml_message

_HOOK_ID = "rules_context"
_llm_notice = _notice_formatter(_HOOK_ID)

RESPONSE_LANGUAGE_NOTICE = (
    "思考を除いてエージェントが書く全ての文は、最初の応答の1文目から日本語で書け。"
    "ツール呼び出しの前後の短い状態行、送達後の本文、委譲プロンプト、返却、成果物も含む。"
    "英語の文は1文も書くな。識別子、コマンド、引用はこの禁止の対象外とする。"
)
QUALITY_CHECKPOINT_NOTICE = "会話圧縮後の復元は`01-agent.md`「行動と手順の目的」に従え。会話限定の指示を成果物へ混入させるな。"

SHARE_DIR = plugin_resources.plugin_root() / "share"
MAIN_RULES_PATH = SHARE_DIR / "rules-main.md"
MAIN_RULES_CLAUDE_CODE_PATH = SHARE_DIR / "rules-main.claude-code.md"
MAIN_RULES_CODEX_PATH = SHARE_DIR / "rules-main.codex.md"
SUBAGENT_RULES_PATH = SHARE_DIR / "rules-subagent.md"
SUBAGENT_RULES_CLAUDE_CODE_PATH = SHARE_DIR / "rules-subagent.claude-code.md"
CLAUDE_CODE_OUTPUT_LIMIT = 10_000
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

    使用言語の規定は`rules-main.md`「ユーザー向け発話ルール」が定めるが、この条文は本文の末尾寄りに
    位置するため、最初の応答を生成する時点では冒頭の記述より参照されにくい。同じ規定を本文の冒頭の1文にも
    置き、最初の応答を生成する前に読まれるようにする。委譲先はこの規定の対象外のため追加しない。

    process-loopが渡した追加指示も、メインだけが受け取る入力として先頭へ置く。委譲先は元の作業の一部を
    担うに過ぎず、この指示の宛先ではない。

    ホスト別のメイン向け規範は`rules-main.md`の後へ置く。Codexの全主体へ届く差分は`~/.codex/AGENTS.md`が
    配送するため、ここではCodexのメインだけに適用する`rules-main.codex.md`を加える。
    """
    parts: list[str] = []
    if not delegated:
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
    if not delegated:
        parts.append(_llm_notice(RESPONSE_LANGUAGE_NOTICE))
    if source == "compact":
        parts.append(_llm_notice(QUALITY_CHECKPOINT_NOTICE))
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

PostToolUseが成功したSkill起動・資料全文の読取を観測した時点で記録する。
分割読取は全文被覆を確認し、資料の実体・内容hash・主体に結び付ける。選択した資料、入力と文脈境界も同じ対応表へ保持する。
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


def reset_main_operation_skill_ready(
    session_id: str, *, retain_for_compact: bool = False
) -> list[tuple[pathlib.Path, str, str]]:
    """メイン会話の起動済みの記録を除く。

    `clear`と`compact`ではスキルの本文が文脈から外れるため、次の操作で再び取得を求める。
    サブエージェントの記録は親の会話圧縮と独立した文脈のため変えない。
    """
    recovery: list[tuple[pathlib.Path, str, str]] = []

    def _reset(current: dict) -> dict | None:
        recovery.clear()
        recorded = current.get(OPERATION_SKILL_READY_KEY)
        if not isinstance(recorded, dict):
            recorded = {}
        if retain_for_compact:
            for name, agents in recorded.items():
                if not name.startswith("selected-read-target:") or not isinstance(agents, list) or MAIN_AGENT_ID not in agents:
                    continue
                try:
                    selected = json.loads(name.removeprefix("selected-read-target:"))
                    path = pathlib.Path(selected["path"]).resolve()
                    heading = selected["heading"]
                    fingerprint = document_reads.content_fingerprint(path)
                except KeyError, OSError, TypeError, ValueError:
                    continue
                # 同じ内容の写しを別の場所で読んだ取得も、選択済み資料の取得として候補に含める。
                if document_reads.content_ready(recorded, fingerprint, MAIN_AGENT_ID):
                    recovery.append((path, heading, fingerprint))
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
                path = pathlib.Path(json.loads(name.removeprefix("selected-read-target:"))["path"]).resolve()
                fingerprint = document_reads.content_fingerprint(path)
            except KeyError, OSError, TypeError, ValueError:
                continue
            updated[CARRIED_READ_TARGET_PREFIX + document_reads.read_trigger_key(path, fingerprint)] = [MAIN_AGENT_ID]
        # 最初の操作より前に圧縮された場合も、旧起動本文を取得済みへ戻さない。
        updated[READ_CONTEXT_RESET_KEY] = [MAIN_AGENT_ID]
        if updated == recorded:
            return None
        current[OPERATION_SKILL_READY_KEY] = updated
        return current

    update_state(session_id, _reset)
    return recovery


def acquisition_failure(target: read_triggers.ReadTarget, error: Exception, *, shell: bool, agent: bool = False) -> str:
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
        return prefix + f"全文を`cat -- {shlex.quote(str(path))}`で読め"
    skill = document_reads.skill_document(str(path))
    if skill and skill[1] == "SKILL.md":
        owner = next((parent for parent in path.parents if (parent / ".claude-plugin/plugin.json").is_file()), None)
        name = f"{document_reads.document_plugin(str(path))}:{skill[0]}" if owner else skill[0]
        step = f"`Skill`で`{name}`を起動せよ（資料: `{path}`）"
        if agent:
            step += f"。`Skill`を使えない場合は`{path}`をReadで全文読め"
        return prefix + step
    return prefix + f"`{path}`をReadで全文読め"


def _compact_recovery_context(host: str, budget: int, recovery_targets: list[tuple[pathlib.Path, str, str]]) -> str | None:
    """圧縮前に取得した選択資料を、ホスト本来の取得手段で案内する。"""
    steps = []
    for path, heading, fingerprint in recovery_targets:
        target = read_triggers.ReadTarget(path, heading)
        try:
            if not path.is_file():
                raise FileNotFoundError(str(path))
            if document_reads.content_fingerprint(path) != fingerprint:
                continue
            if heading:
                target.body()
            skill = document_reads.skill_document(str(path))
            if host not in {"codex", "agy"} and skill and skill[1] == "SKILL.md":
                owner = next((parent for parent in path.parents if (parent / ".claude-plugin/plugin.json").is_file()), None)
                name = f"{document_reads.document_plugin(str(path))}:{skill[0]}" if owner else skill[0]
                steps.append(f"`Skill`で`{name}`を起動せよ（資料: `{path}`）")
                continue
            focus = f"。特に`{heading}`に注目せよ" if heading else ""
            if host in {"codex", "agy"}:
                steps.append(f"`cat -- {shlex.quote(str(path))}`で全文読め" + focus)
            else:
                steps.append(f"Readで`{path}`を全文読め" + focus)
        except (OSError, UnicodeError, ValueError) as error:
            steps.append(acquisition_failure(target, error, shell=host in {"codex", "agy"}))
    if not steps:
        return None

    def notice(items: list[str], remaining: int) -> str:
        # 候補は操作ごとに再掲しないため、通知へ収まらない候補は件数だけを示す。
        # 残る工程で必要な資料は、その操作の宣言が改めて選んだ時点で案内する。
        suffix = f"。ほかに{remaining}件の候補を省いた" if remaining else ""
        # 圧縮前の選択資料は候補として示し、全件の一括再取得は求めない。
        # 残る工程で使わない資料まで読み直すと、圧縮直後に出力上限を超える複合取得が起きるため。
        return _llm_notice(
            "会話圧縮前に選んだ資料の候補を示す。残る工程の判断に使う資料だけを、使う前に次の手段で全文再取得せよ: "
            + "。".join(items)
            + suffix
            + "。"
        )

    included: list[str] = []
    for step in steps:
        candidate = [*included, step]
        if len(notice(candidate, len(steps) - len(candidate))) > budget:
            break
        included = candidate
    return notice(included, len(steps) - len(included)) if included else None


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


def session_temp_notice(session_temp: pathlib.Path | str) -> str:
    """セッションのmanaged-tempの所在と、一時ファイルの置き場所を選ぶ時点で従う行動を対にした通知本文を返す。

    所在だけを示す通知では、一時ファイルを作成する時点で`/tmp`が選ばれ、後始末の削除が権限判定に拒否された。
    置き場所の選択と結び付く語（`/tmp`）と、委譲先へ所在を渡す行動を同じ本文へ置く。
    """
    return (
        f"このセッションのmanaged-temp: {session_temp}\n"
        "一時ファイルはこの領域の直下へ置け。`/tmp`は使うな。"
        "委譲時は、委譲先自身のmanaged-tempまたは専用一時領域の通知があればそちらの直下、"
        "通知が無い場合だけこの絶対パスの直下へ置くよう伝えよ。"
        "通知の有無が親から分からない場合は、委譲先が受信した通知から選ぶ条件付きの案内にせよ。"
        "入力や出力の個別ファイルを指定した場合は、そのファイルの読み書きを行ってよい。"
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
    """Claudeメイン自身の会話の所在を、SessionStartの入力値から通知する。"""
    lines = [
        f"この会話のセッションID: {session_id}"
        if isinstance(session_id, str) and session_id
        else "この会話のセッションID: 入力から取得できません。シェルのCLAUDE_CODE_SESSION_IDを使える場合はその値を使え。"
    ]
    resolved = _resolve_transcript(session_id, transcript_path)
    if resolved is not None:
        lines.append(f"この会話のtranscript: {resolved}")
    else:
        lines.append(
            "この会話のtranscript: 絶対パスを入力から取得できません。特定済みIDをsession-review-evidenceの"
            "--claude-session-idへ渡して記録を取得せよ。"
        )
    return "\n".join(lines)


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


def _compaction_notice(session_id: str, transcript_path: object, session_temp: pathlib.Path | None, *, delegated: bool) -> str:
    """既存の証拠抽出CLIで全人間入力を保存し、会話の流れの取得先も通知する。"""
    resolved = _resolve_transcript(session_id, transcript_path)
    if resolved is None:
        return "圧縮後の原文を抽出できない。特定済みIDをsession-review-evidenceの--claude-session-idへ渡して記録を解決せよ。"
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
        ]
    )
    lines = [f"会話全体の流れが必要な場合は`{conversation}`でconversation.mdとconversation_rangesを取得せよ。"]
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
                str(SHARE_DIR.parent / "skills/session-review/scripts/session_review_evidence.py"),
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
            for line in result.stdout.splitlines()
            if isinstance(event := json.loads(line), dict) and event.get("kind") == "user"
        ]
        data = "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in users)
        output.write_text(data, encoding="utf-8")
        lines.append(
            f"ユーザー発話の原文{len(users)}件: {output}。圧縮の要約で文言や範囲が変わり得るため、"
            "ユーザーの要求と判断はこのファイルの原文で確かめよ。"
        )
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        if output is not None:
            try:
                output.unlink(missing_ok=True)
            except OSError:
                lines.append("前回の抽出ファイルを削除できないため、そのファイルは使用するな。手動で原文を再取得せよ。")
        lines.append(f"ユーザー発話の抽出に失敗した（{type(error).__name__}）。手動で`{shlex.join(command)}`を実行せよ。")
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
    if event_name == "SessionStart":
        source = payload.get("source")
        if not isinstance(source, str):
            raise ValueError("sourceは文字列である必要がある")
        delegated = is_delegated(os.environ)
        content = compose_session_start(source, delegated=delegated, host=host)
        from agent_toolkit._atk import run_skill_batch  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

        review_notice = run_skill_batch.hook_request(payload, host=host) if host in {"claude", "codex"} else None
        if review_notice is not None:
            content = f"{content}\n\n{_llm_notice(review_notice)}" if content else _llm_notice(review_notice)
        session_id = payload.get("session_id")
        if not delegated and host == "claude":
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
            session_temp = None
            recovery_targets: list[tuple[pathlib.Path, str, str]] = []
            if source in _OPERATION_SKILL_RESET_SOURCES:
                recovery_targets = reset_main_operation_skill_ready(session_id, retain_for_compact=source == "compact")
            if not delegated and source in _CONTEXT_LOSING_SOURCES:
                mark_user_confirmation_pending(session_id)
            if source == "compact" and not delegated:
                work_contract.reset_notice(session_id)
            try:
                session_temp = managed_temp.resolve_session_temp(session_id)
            except managed_temp.ManagedTempError, OSError:
                pass
            else:
                temp_context = _llm_notice(session_temp_notice(session_temp))
                content = f"{content}\n\n{temp_context}" if content else temp_context
                if source == "compact" and not delegated:
                    contract_notice = work_contract.notice(session_id)
                    if contract_notice is not None:
                        content += f"\n\n{_llm_notice(contract_notice)}"
            if source == "compact" and host == "claude":
                compact = _llm_notice(
                    _compaction_notice(session_id, payload.get("transcript_path"), session_temp, delegated=delegated)
                )
                content = f"{content}\n\n{compact}" if content else compact
            if source == "compact":
                # PreToolUseを経ない最初の判断にも、圧縮前の選択資料の取得を案内する。
                recovery = _compact_recovery_context(
                    host,
                    CLAUDE_CODE_OUTPUT_LIMIT - len(content or "") - 2,
                    recovery_targets,
                )
                if recovery is not None:
                    content = f"{content}\n\n{recovery}" if content else recovery
    elif event_name == "SubagentStart":
        content = compose_subagent_start(host=host)
        session_temp = _existing_session_temp(payload.get("session_id"))
        if session_temp is not None:
            content = f"{content}\n\n{_llm_notice(_subagent_temp_notice(session_temp, payload.get('agent_id')))}"
    else:
        raise ValueError("hook_event_nameはSessionStartまたはSubagentStartである必要がある")

    if content is not None:
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
