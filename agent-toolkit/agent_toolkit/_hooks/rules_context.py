"""起動時と圧縮後の文脈へ、入口の起動の案内とその場で決まる値を追加するhandler。

規範の本文は載せない。メインへは入口のスキルの起動を、委譲先へは起動の本文の原本の所在を案内する。
委譲先かどうかは`_hooks.agent_id`が、`agents_server`の環境変数と組み込みの委譲機能の子が入力に持つ`agent_id`で判定する。

出力する自動挿入本文は`atk-auto`で囲む。process-loopが渡したユーザー自身の入力は
`forwarded-user-input`で囲む。読み手のエージェントが区分ごとに生成主体と種別を
判別できるようにするためであり、区分ごとに囲んだ本文を全体で重ねて囲まない。

Claude CodeはJSONのadditionalContextを10,000文字で保存先とプレビューへ置き換える。
通知をそのまま届けるため、最大構成の追加文脈を同じ上限へ収める契約テストを置く。
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
from agent_toolkit._common import delegated_session, mcp_environment, message_format, plugin_environment, skill_modes
from agent_toolkit._hooks import plugin_resources
from agent_toolkit._hooks.notice import forget_subject_notices
from agent_toolkit._hooks.notice import formatter as _notice_formatter
lazy from agent_toolkit._common import host_homes, process_tree, session_state, wait_schedule
lazy from agent_toolkit._common.process_loop_session import claim_process_loop_session, is_process_loop_session
lazy from agent_toolkit._common.session_state import update_state
lazy from agent_toolkit._hooks import repository_protection, session_title, timing, work_contract
lazy from agent_toolkit._hooks.agent_id import MAIN_AGENT_ID, is_main_agent_context, resolve_hook_agent_id
lazy from agent_toolkit._hooks.message_format import xml_message
lazy from agent_toolkit._hooks.pretooluse import operation_skills
lazy from agent_toolkit._hooks.termination_evidence_state import completion_playbook_path, forget_completion_context

_HOOK_ID = "rules_context"
_llm_notice = _notice_formatter(_HOOK_ID)

ENTRY_START_NOTICE = "作業を始める前に、スキル`agent-toolkit:atk-mode`を起動せよ。"
ENTRY_COMPACT_NOTICE = (
    "圧縮後に読む資料の限定とは別に、作業を再開する前にスキル`agent-toolkit:atk-mode`を起動し直せ。"
    "進めていた作業の手順書は、入口の表から選び、次に行う操作・判断の条件に該当した時に読み直せ。"
)
"""入口のスキルの起動を求める案内。起動と読み直しを求める1行だけを持ち、規範の本文を載せない。

文脈を引き継がずに始まる会話のメインへ届ける。委譲先へは起動の本文が入口を届けるため、案内しない。
"""
BACKGROUND_TASKS_DISABLED_NOTICE = (
    "この`Claude Code`では`Bash`の背景実行が無効で、`run_in_background`は使えない。背景の指定を試さない。"
    "`agents_server`の待機は単独の`atk agents wait`へ`Bash`の`timeout=600000`を指定して前景で実行し、"
    "結果と終了コードを回収せよ。未終了で返った場合は同じコマンドを再発行せよ。"
    "`sleep`や独自の待機ループで代替しない。"
)
"""背景実行を無効にした起動で、使える待機方法をメインと委譲先へ伝える本文。

委譲先は親の環境変数を継承するため、同じ条件でメインに限らず通知する。
"""

PLUGIN_ROOT = plugin_resources.plugin_root()
SUBAGENT_PLAYBOOK_PATH = PLUGIN_ROOT / "skills" / "atk-mode" / "playbooks" / "subagent.md"
"""委譲先の入口。`agents_server`が起動の本文を切り出す原本と同じファイルである。"""
SUBAGENT_ENTRY_NOTICE = f"委譲を受けた担当である。作業の前に、委譲先の入口`{SUBAGENT_PLAYBOOK_PATH}`を全文読め。"
"""起動の本文を受け取らない委譲先（ホストの組み込みの委譲機能で起動した担当）へ、入口の所在を示す1行。"""
DELEGATED_COMPACT_NOTICE = "圧縮後に読む資料の限定とは別に、作業を再開する前に、次を読み直せ: "
"""委譲先の圧縮後に、起動の本文の原本と役割文書、起動の入力が渡した記録の読み直しを求める1行の書き出し。"""


def delegated_compact_notice(launch: work_contract.DelegatedLaunch | None) -> str:
    """委譲先の圧縮後に読み直す資料を、`agents_server`の登録簿にある起動入力から1行で示す。

    登録簿に無い委譲先（組み込みの委譲機能の子など）へは、入口と役割文書だけを示す。
    """
    targets = [f"委譲先の入口`{SUBAGENT_PLAYBOOK_PATH}`", "起動の入力が示した役割文書"]
    if launch is not None and launch.whole_contract is not None:
        targets.append(f"全体作業契約`{launch.whole_contract}`")
    if launch is not None and launch.handoff is not None:
        targets.append(f"自身の引き継ぎ記録`{launch.handoff}`")
    return DELEGATED_COMPACT_NOTICE + "、".join(targets) + "。"


CLAUDE_CODE_OUTPUT_LIMIT = 10_000
# 圧縮後に作業契約の後へ続く通知（原文の抽出先、資料の再取得案内）へ残す文字数。
_COMPACTION_RESERVE = 1_500
# 作業契約の通知の囲みと固定文の文字数の見込み。本文を載せるかの判定に使う。
_CONTRACT_NOTICE_OVERHEAD = 600
# 要約の作成が失敗した時に、子の標準エラーから通知へ載せる文字数の上限。
_DIGEST_FAILURE_CAUSE_CHARS = 400
# `atk wi process-loop instruct`が保持し、process-loopがセッション起動時に渡す本文。
PROCESS_LOOP_INSTRUCTION_ENV = "AGENT_TOOLKIT_PROCESS_LOOP_INSTRUCTION"
PROCESS_LOOP_INSTRUCTION_ELEMENT = message_format.FORWARDED_USER_INPUT_ELEMENT


def _resumed_for_original_goal_only() -> bool:
    """再開予約のpickerで統括でない会話が選ばれ、元の目的の続行だけを許可された再開かを返す。"""
    from agent_toolkit._atk.wi import process_loop_identity as startup_identity  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

    response = startup_identity.read(os.environ)
    return response is not None and bool(response.get("ack")) and bool(response.get("resume_only"))


def _launch_input_process_wi(data: dict[str, Any], session_id: str) -> tuple[bool, str | None]:
    """起動入力がこのセッションへ作業`process-wi`を名指しするかと、その作業の引数を返す。

    process-loopの統括は常に作業`process-wi all-repos`を担う。
    起動入力は統括が起動した入れ子のCLIへも継承されるため、統括は会話IDで識別する。
    再開予約で統括でない会話を選んだ再開は、元の目的を続けるだけで統括を担わない。
    """
    if data.get("source") == "process-loop":
        return is_process_loop_session(session_id, os.environ) and not _resumed_for_original_goal_only(), "all-repos"
    skill, arguments = data.get("skill"), data.get("arguments")
    if not isinstance(skill, str):
        return False, None
    work, work_arguments = skill_modes.resolve_work(skill, arguments if isinstance(arguments, str) else None)
    return skill_modes.entry_work(work) == "process-wi", work_arguments


def compose_session_start(
    source: str, *, delegated: bool, host: str, launch: work_contract.DelegatedLaunch | None = None
) -> str | None:
    """SessionStartへ追加する本文を構成する。

    メインには文脈を引き継がずに始まる時に入口の起動を、委譲先には圧縮後に入口と役割文書の読み直しを案内する。
    規範の本文は載せない。

    process-loopが渡した追加指示も、メインだけが受け取る入力として先頭へ置く。委譲先は元の作業の一部を
    担うに過ぎず、この指示の宛先ではない。
    """
    parts: list[str] = []
    if not delegated:
        startup_instruction = ""
        if raw := os.environ.get(mcp_environment.SESSION_START_INPUT_ENV):
            data = json.loads(raw)
            if not isinstance(data, dict) or data.get("source") not in ("process-loop", "run-skill"):
                raise ValueError("起動入力はsourceがprocess-loopまたはrun-skillのJSON objectで指定する")
            data.pop("identity", None)
            resume_only = _resumed_for_original_goal_only()
            if resume_only:
                parts.append(
                    message_format.auto_message(
                        "再開予約から同じ会話を再開した。保存済みの要求・認可・成果と元の目的を確認し、"
                        "最初の未完了工程から続行せよ。未達の目的を達成扱いに変えず、成功済みの操作を繰り返すな。",
                        source="process-loop",
                        kind="resume",
                    )
                )
                manifest = data.get("manifest")
                if not isinstance(manifest, str) or not pathlib.Path(manifest).is_absolute():
                    raise ValueError("再開時のmanifestは絶対パスで指定する")
                startup_instruction = json.loads(pathlib.Path(manifest).read_text(encoding="utf-8")).get("instruction", "")
                if not isinstance(startup_instruction, str):
                    raise ValueError("再開時の追加指示には文字列が必要")
            # 参照先ごとに適用する時機が異なる。過去の実行のUWIの照会は作業開始前の契約であり、
            # 終了契約と同じ案内に束ねると照会が報告工程まで遅れる。
            # 作業の始め方は目的文が示す。ここでは起動を差し込まず、終了契約の所在だけを示す。
            if data["source"] == "process-loop":
                guidance = (
                    "次の終了契約を読め: `agent-toolkit:atk-mode/playbooks/process-wi-all-repos-results.md`「結果と終了」"
                )
            else:
                guidance = (
                    "作業を始める前に、`agent-toolkit:atk-mode/references/answer-transitions.md`を全文読み、"
                    "「`atk run-skill`の過去の実行のUWI」に従って過去の実行のUWIを照会せよ。"
                )
                if data.get("batch_manifest"):
                    # batchの手順書は起動から保存までの手順全体であり、終了契約に含めない。
                    guidance += (
                        "\nbatchの起動から保存までは、"
                        "`agent-toolkit:atk-mode/playbooks/review-session-batch.md`を全文読んで従え。"
                    )
                guidance += f"\n次の終了契約を読め: 完了報告の手順書`{completion_playbook_path()}`"
            if not resume_only:
                parts.append(
                    message_format.auto_message(
                        "起動入力: " + html.escape(json.dumps(data, ensure_ascii=False)) + "\n" + guidance,
                        source=data["source"],
                        kind="startup-input",
                    )
                )
        instruction = os.environ.get(PROCESS_LOOP_INSTRUCTION_ENV, "").strip() or startup_instruction
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
    if delegated:
        if source == "compact":
            parts.append(_llm_notice(delegated_compact_notice(launch)))
    elif source in _CONTEXT_LOSING_SOURCES:
        parts.append(_llm_notice(ENTRY_COMPACT_NOTICE if source == "compact" else ENTRY_START_NOTICE))
    if host == "claude" and not delegated:
        parts.append(
            _llm_notice(
                "その場で回答を求める質問・選択には、ツール一覧にあれば`AskUserQuestion`を使え。"
                "現在の入力に端末`mod`が添えた指定か、この会話のツール説明の指定がある時だけ、発話本文を`send_to_user`で送れ。"
                "指定があり直接の一覧に無い時は、`REPL`の`searchTools`か遅延公開ツールの検索で完全名"
                "`mcp__plugin_agent-toolkit_agents_server__send_to_user`を探せ。指定が無いか呼び出せない時は通常の応答本文で伝えよ。"
            )
        )
    if host == "claude" and wait_schedule.background_tasks_disabled():
        parts.append(_llm_notice(BACKGROUND_TASKS_DISABLED_NOTICE))
    parts.append(_llm_notice(plugin_environment.cli_instructions(plugin_resources.plugin_root())))
    return "\n\n".join(parts) or None


_CONTEXT_LOSING_SOURCES = frozenset({"startup", "clear", "compact"})
"""会話の文脈を引き継がずに始まるSessionStartの`source`。`resume`と`fork`は文脈を引き継ぐため含めない。"""


def _compact_recovery_context() -> str:
    """要約を作らないホストへ、圧縮後に読む範囲を伝える。"""
    return _llm_notice(
        "会話が圧縮された。圧縮後に読むのは作業契約と会話の要約だけである。"
        "ほかの資料は、次に行う操作・判断の条件に該当した時に読め。"
    )


def session_temp_notice(session_temp: pathlib.Path | str, *, contract: pathlib.Path | None = None) -> str:
    """セッションのmanaged-tempの所在と、一時ファイルの置き場所を選ぶ時点で従う行動を対にした通知本文を返す。

    所在だけを示す通知では、一時ファイルを作成する時点で`/tmp`が選ばれ、後始末の削除が権限判定に拒否された。
    置き場所の選択と結び付く語（`/tmp`）と、委譲先へ所在を渡す行動を同じ本文へ置く。
    この規定はエージェント自身が作る一時ファイルに限る。テスト実行の`TMPDIR`をこの領域へ向けた担当が、
    対象プロジェクトの除外設定に一致する祖先のパスでテストを失敗させ、無関係なテストを改変したため、
    テスト・ツールの一時領域は変えないことを同じ本文で示す。
    """
    if contract is not None and contract.parent == pathlib.Path(session_temp):
        location = "このセッションのmanaged-tempは、次の原本の親ディレクトリである。\n"
    else:
        location = f"このセッションのmanaged-temp: {session_temp}\n"
    if contract is not None:
        location += work_contract.short_notice(contract) + "\n"
    return location + (
        "一時ファイルは直下へ置け。`/tmp`は使うな。"
        "テスト・ツールは`TMPDIR`などの一時領域の環境変数を変えずに実行せよ。"
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
    arguments = [str(PLUGIN_ROOT / "scripts/atk"), "run-script", "session-review-evidence", "--"]
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


def _discard_stale_digest(output: pathlib.Path | None) -> list[str]:
    """要約の作成に失敗した時に前回の要約を消し、消せない場合は使用を禁じる行を返す。

    保存先は圧縮のたびに同じファイルなので、残すと前回の圧縮までの要約を今回の復元材料と取り違える。
    """
    if output is None:
        return []
    try:
        output.unlink(missing_ok=True)
    except OSError:
        return [f"前回の要約ファイル`{output}`を削除できないため、そのファイルは使用するな。"]
    return []


def _compaction_notice(session_id: str, transcript_path: object, session_temp: pathlib.Path | None) -> str:
    """発話だけの要約を作り、圧縮後に読む対象を作業契約とその1ファイルに限って通知する。"""
    scope = (
        "ほかの資料（工程のスキル、役割文書、規範）は、次に行う操作・判断の条件に該当した時に読め。"
        "委譲先の返却、背景待機の完了、定期の再確認、ユーザーの回答への対応でも同じである。"
    )
    resolved = _resolve_transcript(session_id, transcript_path)
    if resolved is None:
        arguments = ["run-script", "session-review-evidence", "--", "--claude-session-id", session_id, "--user-events"]
        return f"会話が圧縮された。ユーザー発話の原文は起動部分へ`{shlex.join(arguments)}`を続けて取得せよ。{scope}"
    # 保存先を固定し、圧縮のたびに同じファイルを置き換える。
    output = session_temp / "compaction-digest" / "conversation.md" if session_temp is not None else None
    options = ["--transcript", resolved, "--conversation-only", "--conversation-digest"]

    def manual(*extra: str) -> str:
        command = shlex.join(["run-script", "session-review-prepare", "--", *options, *extra])
        return f"起動部分へ`{command}`を続けて実行し、出力のconversation_pathが示すファイルを読め。{scope}"

    try:
        if output is None:
            raise ValueError("セッションのmanaged-tempを取得できない")
        output.parent.mkdir(exist_ok=True)
        # 同じ依存環境のPythonで公開スクリプトを呼び、起動ごとの環境解決を重ねない。
        result = process_tree.run_with_timeout(
            [
                sys.executable,
                str(PLUGIN_ROOT / "skills/atk-mode/scripts/session_review_prepare.py"),
                *options,
                "--work-dir",
                str(output.parent),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=True,
            env=plugin_environment.direct_environment(PLUGIN_ROOT, os.environ),
        )
        counts = json.loads(result.stdout)["conversation_window"]
        users, assistants = int(counts["user_count"]), int(counts["assistant_count"])
        requests = int(counts.get("request_count", 0))
        breakdown = f"ユーザー発話{users}件、エージェント発話{assistants}件" + (
            f"、委譲元の依頼{requests}件" if requests else ""
        )
        return (
            f"会話が圧縮された。圧縮後に読むのは、作業契約と、Readのfile_pathに`{output}`を指定して読む会話の要約"
            f"（{breakdown}）だけである。{scope}"
        )
    except process_tree.ReclaimError as error:
        # hookは同じ所有対象を次の起動まで保持できないため、その場で回収し直し、残れば対象を示す。
        remaining = error.retry()
        lines = _discard_stale_digest(output)
        if remaining:
            lines.append(
                f"会話の要約の子プロセスを回収できない: {remaining}。"
                "各PIDの起動コマンドと自身の所有対象との一致を確認し、一致したプロセスだけを終了させよ。"
            )
        lines.append(
            f"会話が圧縮された。会話の要約の作成に失敗した（{type(error).__name__}: "
            f"context={error.context}、残存={remaining}）。{manual()}"
        )
        return "\n".join(lines)
    except subprocess.CalledProcessError as error:
        # 終了コードだけでは原因が分からないため、子が標準エラーへ出した原因を通知へ渡す。
        cause = " ".join(str(error.stderr or "").split())[:_DIGEST_FAILURE_CAUSE_CHARS]
        # 予算の超過は同じ上限の再実行では解消しないため、子が示した収まる上限を付けたコマンドを示す。
        needed = re.search(r"--conversation-max-bytes (\d+)", cause)
        extra = ("--conversation-max-bytes", needed.group(1)) if needed else ()
        lines = _discard_stale_digest(output)
        lines.append(f"会話が圧縮された。会話の要約の作成に失敗した（終了コード{error.returncode}: {cause}）。{manual(*extra)}")
        return "\n".join(lines)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        lines = _discard_stale_digest(output)
        lines.append(f"会話が圧縮された。会話の要約の作成に失敗した（{type(error).__name__}: {error}）。{manual()}")
        return "\n".join(lines)


def _subagent_temp_notice(session_temp: pathlib.Path | str, agent_id: object) -> str:
    """担当ごとの置き場所と、親の領域でのファイルの受渡しを通知する。"""
    if not isinstance(agent_id, str) or not agent_id:
        return session_temp_notice(session_temp)
    # IDを単一のパス要素に保ち、同じIDの再開でも同じ場所へ到達する。
    child = pathlib.Path(session_temp) / f"agent-{urllib.parse.quote(agent_id, safe='')}"
    child.mkdir(exist_ok=True)
    return (
        f"このサブエージェント専用の一時領域: {child}\n"
        "一時ファイルはこの領域の直下へ置け。`/tmp`は使うな。"
        "テスト・ツールは`TMPDIR`などの一時領域の環境変数を変えずに実行せよ。"
        f"親のセッションのmanaged-temp: {session_temp}\n"
        "親の領域は、委譲元が個別に指定した入力や出力のファイルの読み書きに使ってよい。"
    )


def compose_subagent_start() -> str:
    """SubagentStartへ追加する本文を返す。入口の所在とCLIの起動部分だけを示し、規範の本文を載せない。"""
    return f"{_llm_notice(SUBAGENT_ENTRY_NOTICE)}\n\n{_llm_notice(plugin_environment.cli_instructions(PLUGIN_ROOT))}"


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
    # 組み込みの委譲機能の子のSessionStartは、親と同じsession_idと自分のagent_idを持つ。
    # 親の会話の記録と要約を変えないよう、メインの状態を扱う処理は主体がメインの時に限る。
    subject = resolve_hook_agent_id(payload)
    delegated = not is_main_agent_context(payload)
    if event_name == "SessionStart":
        if isinstance(session_id, str):
            claim_process_loop_session(session_id, os.environ)
        source = payload.get("source")
        if not isinstance(source, str):
            raise ValueError("sourceは文字列である必要がある")
        if source == "resume" and not delegated:
            from agent_toolkit._atk.wi import process_loop_identity as startup_identity  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

            try:
                startup_identity.capture(payload, host, os.environ)
            except (OSError, ValueError) as error:
                reason = (
                    f"{error}。起動側workerのログを確認し、atk wi process-loop statusで予約を確認する。"
                    "原因の解消後にatk wi process-loop resume IDで再予約する。"
                    "予約を取り消す場合はatk wi process-loop resume-cancelを使う。"
                )
                print(json.dumps({"continue": False, "stopReason": reason}, ensure_ascii=False))
                return 1
        session_temp = None
        if isinstance(session_id, str) and session_id:
            with contextlib.suppress(managed_temp.ManagedTempError, OSError):
                session_temp = managed_temp.resolve_session_temp(session_id)
        if source in _CONTEXT_LOSING_SOURCES and isinstance(session_id, str) and session_id:
            # 同じセッションIDでも受信文脈は失われているため、既送の通知を本文の構成前に未送へ戻す。
            forget_subject_notices(session_id, subject)
        launch = (
            work_contract.delegated_launch(session_id)
            if source == "compact"
            and isinstance(session_id, str)
            and not payload.get("agent_id")
            and delegated_session.is_delegated(os.environ)
            else None
        )
        started = time.monotonic()
        content = compose_session_start(source, delegated=delegated, host=host, launch=launch)
        timing.record(payload, "rules_context", started, "ok", stage="normative-content")
        if (
            not delegated
            and source != "compact"
            and isinstance(session_id, str)
            and session_id
            and (raw := os.environ.get(mcp_environment.SESSION_START_INPUT_ENV))
            and (launch := _launch_input_process_wi(json.loads(raw), session_id))[0]
        ):
            # 自律モードの状態と表示名の種別は起動入力から立てる。エージェントによる入口の起動からは立てない。
            # 圧縮後の再開では再び設定しない（終了工程で戻した状態を復活させないため）。
            update_state(session_id, session_state.set_process_wi_invoked)
            if host == "claude":
                session_title.observe_skill(session_id, skill_modes.PROCESS_WI, launch[1])
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
            if source in {"clear", "compact"}:
                # 起動したスキルと案内の本文は文脈から外れるため、次の操作で再び案内する。
                operation_skills.forget_subject(session_id, subject)
                if subject == MAIN_AGENT_ID:
                    forget_completion_context(session_id)
            if source == "compact" and subject == MAIN_AGENT_ID:
                work_contract.reset_notice(session_id)
            if session_temp is not None:
                temp_text = _subagent_temp_notice(session_temp, payload.get("agent_id"))
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
            if source == "compact":
                # ツールを使わない返却・回答の判断にも、圧縮後に読む範囲を1通で伝える。
                recovery = _compact_recovery_context()
                if host == "claude" and subject == MAIN_AGENT_ID:
                    started = time.monotonic()
                    compact_text = _compaction_notice(session_id, payload.get("transcript_path"), session_temp)
                    timing.record(payload, "rules_context", started, "ok", stage="compaction-recovery")
                    if compact_text:
                        recovery = _llm_notice(compact_text)
                content = f"{content}\n\n{recovery}" if content else recovery
    elif event_name == "SubagentStart":
        content = compose_subagent_start()
        session_temp = _existing_session_temp(payload.get("session_id"))
        if session_temp is not None:
            content = f"{content}\n\n{_llm_notice(_subagent_temp_notice(session_temp, payload.get('agent_id')))}"
    else:
        raise ValueError("hook_event_nameはSessionStartまたはSubagentStartである必要がある")

    try:
        repository_notice = repository_protection.start_notice(str(payload.get("cwd", "")))
    except (OSError, ValueError) as error:
        repository_notice = (
            f"repository設定を確認できない: {error}。`atk config repos list`で診断を確認せよ。"
            "編集可否が確定するまで直接編集するな。"
        )
    if repository_notice:
        content = f"{content}\n\n{_llm_notice(repository_notice)}" if content else _llm_notice(repository_notice)
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
