"""前任の保持情報と既存の記録抽出から、後任へ配送する引き継ぎ本文を組み立てる。"""

from __future__ import annotations

import json
import pathlib
import re
import shlex
import subprocess
import sys
from collections.abc import Mapping
from typing import Any

from agent_toolkit._agents_server import backends, plugin_root, record_paths
from agent_toolkit._agents_server.state import SessionResumeState, SessionState
from agent_toolkit._atk import managed_temp
from agent_toolkit._common.next_action import ActionableError


def availability_reason(engine: str, status: str, error: Any) -> str | None:
    """候補除外と同じbackendの判定で、終端結果の可用性失敗を判定する。"""
    backend = backends.backend_class(engine)
    if backend is None:
        return None
    return backend.unavailable_reason(SessionState(session_id="handoff", cwd="", engine=engine, status=status, error=error))


def result_next_action(result: Mapping[str, Any]) -> str | None:
    """可用性失敗の結果だけに、前任IDを使った明示的な引き継ぎ起動を案内する。"""
    if availability_reason(str(result.get("engine", "")), str(result.get("status", "")), result.get("error")) is None:
        return None
    identifier = json.dumps(result.get("session_id"), ensure_ascii=False)
    return f'`start`へ`{{"handoff_from": {identifier}}}`だけを渡し、前任の進捗を後任へ引き継ぐ'


def predecessor_error(session: SessionState | SessionResumeState) -> Any:
    """結果回収後と再起動後も、保持した終端理由を使う。"""
    if session.error is not None:
        return session.error
    return json.loads(session.terminal_error) if session.terminal_error is not None else None


def prepare_prompt(session: SessionState | SessionResumeState) -> str:
    """会話の流れを生成し、追送と各turnの最終応答全文を元の起動本文へ加える。

    ファイルは既存managed-tempと同じ保持期間で掃引される。抽出失敗時は後任を起動せず、記録の回復を案内する。
    """
    if not session.prompt or not session.model_type:
        raise ActionableError(
            f"前任の起動入力が保持されていません: {session.session_id}",
            next_action="前任の記録を読み、確認できた起動条件と残工程を通常の`start`へ渡す",
        )
    record = record_paths.find_session_record(session.session_id)
    if record is None or len(record.paths) != 1 or record.engine != session.engine:
        raise ActionableError(
            f"前任の記録を一意に解決できません: {session.session_id}",
            next_action="`atk agents logs <前任session_id>`で記録の所在を確かめ、解消後に同じ引き継ぎ起動を再発行する",
        )
    directory = managed_temp.create_managed_temp("session-handoff")
    entry = plugin_root.SERVER_PLUGIN_ROOT / "agent_toolkit" / "atk.py"
    if session.engine == "agy":
        command = [sys.executable, str(entry), "agents", "logs", session.session_id, "--format", "markdown"]
        conversation = directory / "conversation.md"
    else:
        selector = (
            ["--codex-thread-id", session.session_id] if session.engine == "codex" else ["--transcript", str(record.paths[0])]
        )
        command = [
            sys.executable,
            str(entry),
            "run-script",
            "session-review-prepare",
            "--",
            *selector,
            "--work-dir",
            str(directory),
            "--conversation-only",
        ]
        conversation = directory / "conversation.md"
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=270, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        (directory / "prepare.stderr").write_text(str(error), encoding="utf-8")
        raise ActionableError(
            f"前任の会話抽出を完了できません: session_id={session.session_id}, 診断={directory}",
            next_action="保存したprepare.stderrを読み、抽出が成立してから引き継ぎ起動を再発行する",
        ) from error
    (directory / "prepare.stdout").write_text(completed.stdout, encoding="utf-8")
    (directory / "prepare.stderr").write_text(completed.stderr, encoding="utf-8")
    if completed.returncode != 0:
        raise ActionableError(
            f"前任の会話抽出が失敗しました: session_id={session.session_id}, "
            f"exit_code={completed.returncode}, 診断={directory}",
            next_action="保存したprepare.stderrとprepare.stdoutを読み、抽出が成立してから引き継ぎ起動を再発行する",
        )
    if session.engine == "agy":
        conversation.write_text(completed.stdout, encoding="utf-8")
    if not conversation.is_file():
        raise ActionableError(
            f"前任の会話抽出がconversation.mdを生成しませんでした: {directory}",
            next_action="保存したprepare.stdoutとprepare.stderrを読み、抽出の不具合を報告する",
        )
    return compose_prompt(session, conversation, record.paths)


def compose_prompt(
    session: SessionState | SessionResumeState,
    conversation: pathlib.Path,
    records: tuple[pathlib.Path, ...],
) -> str:
    """抽出した資料と保持する全文から、全mode共通の引き継ぎ節を組み立てる。"""
    lines = [
        session.prompt,
        "",
        "## 前任からの引き継ぎ",
        "上の起動本文は前任へ配送した原文である。以下の引き継ぎ節を現在の開始条件として扱い、既存の記録を読む。",
    ]
    handoff = re.search(r"^引き継ぎ記録先[:：]\s*(.+)$", session.prompt, re.MULTILINE)
    if handoff is not None:
        path = re.sub(r"[（(](?:新規|継続)[）)]\s*$", "", handoff[1]).strip()
        lines += [f"引き継ぎ記録先: {path}（継続）", "この引き継ぎ記録を最初に読み、確定事項と残工程を復元する。"]
    lines += [
        f"前任session: {session.session_id}",
        f"前任engine: {session.engine}",
        f"終端状態: {session.status}",
        f"終端理由: {json.dumps(predecessor_error(session), ensure_ascii=False)}",
        f"会話の流れ: {conversation.resolve()}",
        "会話の流れ・追送・各turnの最終応答を読み、引き継ぎ記録がある場合はその記述とも比べ、現在の確定事項と残工程を復元する。",
        "完了済みの操作と作成済みの成果物を現物で確かめ、最新の指示で中止された範囲と既に完了・続行済みの範囲は再実行せず、"
        "残る認可内の工程だけを続ける。",
        "",
        "### 委譲元から前任へ配送した追送（全文）",
    ]
    for index, prompt in enumerate(session.followup_prompts, 1):
        lines += [f"#### 追送{index}", prompt]
    lines += ["", "### 前任の各turnの最終応答（全文）"]
    responses = dict(session.final_responses)
    if session.agent_message:
        responses[session.turn_seq] = session.agent_message
    for turn, message in responses.items():
        lines += [f"#### turn {turn}", message]
    selector = "--codex-thread-id" if session.engine == "codex" else "--claude-session-id"
    if session.engine != "agy":
        lines += [
            "",
            "詳細照会: "
            + shlex.join(
                [
                    "atk",
                    "run-script",
                    "session-review-evidence",
                    "--",
                    selector,
                    session.session_id,
                ]
            )
            + " --detail <記録位置>",
        ]
    lines += ["コマンドを実行できない後任は、次の記録ファイルを必要な範囲で読む。"]
    lines.extend(str(path.resolve()) for path in records)
    return "\n".join(lines)
