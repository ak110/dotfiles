from __future__ import annotations

import json
import pathlib
import shlex
import stat
import subprocess
import sys
from typing import Any

from agent_toolkit._agents_server import backends, handoff_next_action, plugin_root, record_paths
from agent_toolkit._agents_server.state import SessionResumeState, SessionState
from agent_toolkit._atk import managed_temp
from agent_toolkit._common import delegated_session, plugin_environment, process_tree, runtime_environment
from agent_toolkit._common.diagnostic_redaction import redact_secrets
from agent_toolkit._common.next_action import ActionableError, recover_from_failure

result_next_action = handoff_next_action.result_next_action


def availability_reason(engine: str, status: str, error: Any) -> str | None:
    """候補除外と同じbackendの判定で、終了時の結果の可用性失敗を判定する。"""
    backend = backends.backend_class(engine)
    if backend is None:
        return None
    return backend.unavailable_reason(SessionState(session_id="handoff", cwd="", engine=engine, status=status, error=error))


def predecessor_error(session: SessionState | SessionResumeState) -> Any:
    if session.error is not None:
        return session.error
    return json.loads(session.terminal_error) if session.terminal_error is not None else None


def prepare_prompt(session: SessionState | SessionResumeState) -> str:
    """会話の流れを生成し、追送と各turnの最終応答全文を元の起動本文へ加える。

    会話が分かる場合はセッションのmanaged-tempの子へ保存し、既存managed-tempと同じ保持期間で掃引される。
    成功時はconversation.mdだけを残し、抽出失敗時だけ両出力の診断を保存して後任を起動せず、記録の回復を案内する。
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
    directory = managed_temp.create_output_temp("session-handoff")
    entry = plugin_root.SERVER_PLUGIN_ROOT / "agent_toolkit" / "atk.py"
    if session.engine in {"agy", "opencode"}:
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
        completed = process_tree.run_with_timeout(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=270,
            check=False,
            env=plugin_environment.direct_environment(plugin_root.SERVER_PLUGIN_ROOT, runtime_environment.environment()),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        (directory / "prepare.stderr").write_text(redact_secrets(str(error)), encoding="utf-8")
        if isinstance(error, subprocess.TimeoutExpired):
            for name, output in (("stdout", error.stdout), ("stderr", error.stderr)):
                text = output.decode("utf-8", errors="replace") if isinstance(output, bytes) else (output or "")
                (directory / f"prepare.{name}").write_text(redact_secrets(text), encoding="utf-8")
        raise ActionableError(
            f"前任の会話抽出を完了できません: session_id={session.session_id}, 診断={directory}",
            next_action="保存したprepare.stderrを読み、抽出が成立してから引き継ぎ起動を再発行する",
        ) from error
    if completed.returncode != 0:
        # 診断は失敗時だけ保存する。成功時の両出力は会話の流れの生成に使い終え、後任へ渡すのはconversation.mdだけである。
        (directory / "prepare.stdout").write_text(redact_secrets(completed.stdout), encoding="utf-8")
        (directory / "prepare.stderr").write_text(redact_secrets(completed.stderr), encoding="utf-8")
        raise ActionableError(
            f"前任の会話抽出が失敗しました: session_id={session.session_id}, "
            f"exit_code={completed.returncode}, 診断={directory}",
            next_action="保存したprepare.stderrとprepare.stdoutを読み、抽出が成立してから引き継ぎ起動を再発行する",
        )
    if session.engine in {"agy", "opencode"}:
        conversation.write_text(completed.stdout, encoding="utf-8")
    if not conversation.is_file():
        (directory / "prepare.stdout").write_text(redact_secrets(completed.stdout), encoding="utf-8")
        (directory / "prepare.stderr").write_text(redact_secrets(completed.stderr), encoding="utf-8")
        raise ActionableError(
            f"前任の会話抽出がconversation.mdを生成しませんでした: {directory}",
            next_action=recover_from_failure(
                component="会話抽出",
                evidence="保存したprepare.stdoutとprepare.stderr",
                first_step="保存出力に必要な範囲の会話が含まれているか確認する",
                retry="原本を保持し、抽出だけを再実行する",
            ),
        )
    return compose_prompt(session, conversation, record.paths)


def _record_instruction(path: pathlib.Path) -> str:
    try:
        if not stat.S_ISREG(path.stat().st_mode):
            reason = "記録先が通常ファイルではない"
        else:
            with path.open("rb"):
                pass
            return (
                "この引き継ぎ記録を最初に読み、確定事項と残工程を復元する。"
                "読めない場合は、前任が書いた値を失った可能性があるものとして以下の資料と成果物の現物から復元し、"
                "復元できない値を委譲元へ返す。"
            )
    except FileNotFoundError:
        return "この引き継ぎ記録は存在しないため、以下の資料から復元する。"
    except OSError as error:
        reason = str(error)
    return (
        f"この引き継ぎ記録を読めない（{reason}）。前任が書いた値を失った可能性があるため、"
        "以下の資料と成果物の現物から確定事項と残工程を復元し、復元できない値を委譲元へ返す。"
    )


def compose_prompt(
    session: SessionState | SessionResumeState,
    conversation: pathlib.Path,
    records: tuple[pathlib.Path, ...],
) -> str:
    path = delegated_session.prompt_handoff_path(session.prompt)
    original = session.prompt
    if path is not None:
        original = "\n".join(
            f"引き継ぎ記録先: {path}（継続）" if line.startswith(("引き継ぎ記録先:", "引き継ぎ記録先：")) else line
            for line in original.split("\n")
        )
    lines = [
        original,
        "",
        "## 前任からの引き継ぎ",
        "上の起動本文は前任へ配送した原文の記録先行を継続へ更新したものである。以下の引き継ぎ節を現在の開始条件として扱い、既存の記録を読む。",
    ]
    if path is not None:
        lines += [_record_instruction(pathlib.Path(path))]
    lines += [
        f"前任session: {session.session_id}",
        "同じ管理サービスで引き継いだ直属の子の通知と終了結果はMCPの`collect_handoff`で回収する。"
        "引き継ぎ起動が委譲元へ返った後に使う。回収結果のsession_idに限り`show`と`send_message`を使える。"
        "後任自身が起動した子には通常どおり`atk agents wait`を使う。",
        f"前任engine: {session.engine}",
        f"終了状態: {session.status}",
        f"終了理由: {json.dumps(predecessor_error(session), ensure_ascii=False)}",
        f"会話の流れ: {conversation.resolve()}",
        "会話の流れ・追送・各turnの最終応答を読み、引き継ぎ記録を読めた場合はその記述とも比べ、現在の確定事項と残工程を復元する。",
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
    if session.engine not in {"agy", "opencode"}:
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
