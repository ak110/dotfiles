"""`_hooks/pretooluse/`のテストが共有する、PreToolUse hookの起動とpayload・作業場所の組み立て。

内容と状態遷移の試験は公開hookを同一プロセスで呼び、exit code・stderr・stdoutを検証する。
"""

import ast
import contextlib
import functools
import hashlib
import io
import json
import os
import pathlib
import shlex
import subprocess
import sys
from collections.abc import Generator

import pytest

from agent_toolkit import hook as _hook
from agent_toolkit._common import message_format as _message_format
from agent_toolkit._common import persisted_output_reference, read_triggers, state_paths
from agent_toolkit._common.session_state import state_path
from agent_toolkit._testing import git_repository
lazy from agent_toolkit._common.session_state import read_state
lazy from agent_toolkit._hooks import agent_id, notice, rules_context
lazy from agent_toolkit._hooks.pretooluse import operation_skills, read_delivery

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "hook.py"


_PLUGIN_MANIFEST = pathlib.Path(__file__).resolve().parents[2] / ".claude-plugin" / "plugin.json"


_MARKETPLACE_MANIFEST = pathlib.Path(__file__).resolve().parents[3] / ".claude-plugin" / "marketplace.json"


_SHARE_DIR = pathlib.Path(__file__).resolve().parents[2] / "share"


_SECRETS_COPY_GUIDANCE = "Bashの`cp`で原本を複製"


_EXECUTE_REVIEW_TASK_NAMES: tuple[str, ...] = ("exec-review.subagent.md",)


_NOTICE_PREFIX = _message_format.opening_tag(_message_format.AUTO_INSERTED_ELEMENT, {"source": "pretooluse", "kind": "warn"})


_NOTICE_SUFFIX = f"</{_message_format.AUTO_INSERTED_ELEMENT}>"


def _run_hook_in_process(
    subcommand: str,
    payload: object,
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """公開hookを同一プロセスで呼び、終了状態と標準出力・標準エラーを返す。"""
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    original_environ = os.environ.copy()
    original_stdin = sys.stdin
    output = io.StringIO()
    errors = io.StringIO()
    stdin = io.TextIOWrapper(io.BytesIO(text.encode("utf-8")), encoding="utf-8")
    try:
        os.environ.clear()
        os.environ.update(env)
        sys.stdin = stdin
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            returncode = _hook.main([subcommand])
    finally:
        sys.stdin = original_stdin
        stdin.close()
        os.environ.clear()
        os.environ.update(original_environ)
    return subprocess.CompletedProcess(
        args=(str(_SCRIPT), subcommand),
        returncode=returncode,
        stdout=output.getvalue(),
        stderr=errors.getvalue(),
    )


def _run(payload: object, env_overrides: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return run_hook_with_notice_details("pretooluse", payload, env_overrides)


def run_hook_with_notice_details(
    subcommand: str, payload: object, env_overrides: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """通知本文の意味を検証する消費側として、短い参照が指す実ファイルを読む。

    生JSON・容量・配送記録の検証には_run_hook_in_processを使う。
    この読取では元の資料本文の取得記録を追加しない。
    """
    result = _run_hook_in_process(subcommand, payload, env_overrides)
    if not result.stdout:
        return result
    response = json.loads(result.stdout)
    specific = response.get("hookSpecificOutput", {})
    context = specific.get("additionalContext", "")
    path = persisted_output_reference.reference_path(context)
    if path is None:
        return result
    specific["additionalContext"] = path.read_text(encoding="utf-8")
    return subprocess.CompletedProcess(
        result.args, result.returncode, json.dumps(response, ensure_ascii=False) + "\n", result.stderr
    )


def _run_posttooluse(payload: object, env_overrides: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """PostToolUse hookを同じ一時状態ディレクトリで実行する。"""
    return _run_hook_in_process("posttooluse", payload, env_overrides)


def codex_execution_payload(payload: dict, directory: pathlib.Path, *, exit_code: int) -> dict:
    """元の呼出しと同一のnative終了記録を添え、成功・失敗を本文と独立に指定する。"""
    payload = {"tool_use_id": "native-read", **payload}
    transcript = pathlib.Path(payload.get("transcript_path", directory / "native-read.jsonl"))
    turn = payload["turn_id"]
    records = [
        {"type": "event_msg", "payload": {"type": "task_started", "turn_id": turn, "model_context_window": 1000000}},
        {"type": "token_usage_record", "payload": {"usage": {"input_tokens": 0, "output_tokens": 0}}},
        {
            "type": "turn_context",
            "payload": {"turn_id": turn, "cwd": str(directory), "permission_profile": {"type": "disabled"}},
        },
        {
            "type": "event_msg",
            "payload": {
                "type": "item_completed",
                "turn_id": turn,
                "thread_id": payload["session_id"],
                "item": {
                    "type": "CommandExecution",
                    "id": payload["tool_use_id"],
                    "status": "completed" if exit_code == 0 else "failed",
                    "exit_code": exit_code,
                    "cwd": directory.as_uri(),
                    "command": ["/bin/bash", "-lc", payload["tool_input"]["command"]],
                },
            },
        },
    ]
    with transcript.open("a", encoding="utf-8") as stream:
        stream.writelines(json.dumps(record) + "\n" for record in records)
    return {**payload, "transcript_path": str(transcript)}


def codex_delivered_payload(payload: dict, directory: pathlib.Path | None = None) -> dict:
    """合成分岐試験の応答を外側rolloutへ保存し、後続Preが読む実体を返す。

    実ホストの形と配送時機は`../_hooks/pretooluse/read_delivery_host_samples_test.py`の標本で別に検収する。
    """
    actor = payload.get("agent_id", "main")
    identity = json.dumps([payload.get("session_id", "test"), actor]).encode("utf-8")
    directory = directory or state_paths.state_dir() / "test-deliveries"
    path = pathlib.Path(
        payload.get(
            "agent_transcript_path",
            payload.get("transcript_path", directory / (hashlib.sha256(identity).hexdigest() + ".jsonl")),
        )
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "type": "response_item",
        "payload": {
            "type": "custom_tool_call_output",
            "call_id": "synthetic",
            "output": [{"type": "input_text", "text": json.dumps(payload.get("tool_response"), ensure_ascii=False)}],
        },
    }
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    return {**payload, "transcript_path": str(path), **({"agent_transcript_path": str(path)} if actor != "main" else {})}


def _additional_context(result: subprocess.CompletedProcess[str]) -> str:
    """stdoutのJSONから`hookSpecificOutput.additionalContext`を取り出す。"""
    stdout = result.stdout.strip()
    if not stdout:
        return ""
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return ""
    context = payload.get("hookSpecificOutput", {}).get("additionalContext")
    return context if isinstance(context, str) else ""


def denial_reason(result: subprocess.CompletedProcess[str]) -> str:
    """公開hookの正常拒否と、主体へ届く理由を検証する。"""
    assert result.returncode == 0 and not result.stderr
    specific = json.loads(result.stdout)["hookSpecificOutput"]
    assert specific["permissionDecision"] == "deny"
    reason = specific["permissionDecisionReason"]
    assert isinstance(reason, str) and reason
    return reason


def _notice_body(context: str) -> str:
    """標準XML境界を検証し、通知本文を返す。"""
    assert context.startswith(_NOTICE_PREFIX)
    assert context.endswith(_NOTICE_SUFFIX)
    opening_end = context.index(">\n") + 2
    return context[opening_end:].removesuffix("\n" + _NOTICE_SUFFIX)


def _agent_messages(result: subprocess.CompletedProcess[str]) -> str:
    """コーディングエージェントへ届く本文を連結して返す。

    Claude Codeの公式仕様では、exit 0で終了したフックのstderrはデバッグログだけに送られ
    コーディングエージェントへ渡らない。そのためstderrはexit 2（ブロック）の場合だけ含める。
    """
    stderr = result.stderr if result.returncode == 2 else ""
    specific = json.loads(result.stdout).get("hookSpecificOutput", {}) if result.stdout.strip() else {}
    reason = specific.get("permissionDecisionReason", "") if specific.get("permissionDecision") == "deny" else ""
    return f"{stderr}\n{reason}\n{_additional_context(result)}"


def _stderr_warn_offenders(source: str) -> list[int]:
    """warn通知をstderrへ出力する箇所の行番号を返す。

    検出するのは`print(..., file=sys.stderr)`の引数へwarn通知が現れる2形とする。
    1つは`tag="warn"`の呼び出しを引数へ直接書く形、
    もう1つはwarn通知を変数へ束縛してから引数へ渡す形である。
    後者には束縛元がwarn通知を返す関数の呼び出しである場合も含め、
    定義順によらず収集できるよう関数名と変数名の収集を不動点まで繰り返す。
    warn通知を関数の引数として別のヘルパーへ渡し、渡された先のヘルパーがstderrへ出力する
    間接的な受け渡しは検出しない。仮引数へ渡る値の由来を追跡しないためである。
    """
    tree = ast.parse(source)

    def has_warn_tag(node: ast.AST) -> bool:
        return any(
            isinstance(inner, ast.keyword) and inner.arg == "tag" and getattr(inner.value, "value", None) == "warn"
            for inner in ast.walk(node)
        )

    # warn通知を保持する変数名と、warn通知を返す関数名。
    warn_names: set[str] = set()
    warn_functions: set[str] = set()

    def is_warn_value(node: ast.expr) -> bool:
        if has_warn_tag(node):
            return True
        if isinstance(node, ast.Name) and node.id in warn_names:
            return True
        return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in warn_functions

    while True:
        before = (len(warn_names), len(warn_functions))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and is_warn_value(node.value):
                warn_names.update(target.id for target in node.targets if isinstance(target, ast.Name))
            elif (
                isinstance(node, ast.AnnAssign)
                and node.value is not None
                and is_warn_value(node.value)
                and isinstance(node.target, ast.Name)
            ):
                warn_names.add(node.target.id)
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and any(
                isinstance(inner, ast.Return) and inner.value is not None and is_warn_value(inner.value)
                for inner in ast.walk(node)
            ):
                warn_functions.add(node.name)
        if (len(warn_names), len(warn_functions)) == before:
            break

    offenders: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id != "print":
            continue
        if not any(keyword.arg == "file" and ast.unparse(keyword.value) == "sys.stderr" for keyword in node.keywords):
            continue
        if has_warn_tag(node) or any(is_warn_value(arg) for arg in node.args):
            offenders.append(node.lineno)
    return sorted(set(offenders))


def _write_session_state(state_dir: pathlib.Path, session_id: str, state: dict) -> None:
    """隔離した実行環境が解決する固定保存先へ状態を準備する。"""
    del state_dir
    path = state_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def _read_session_state(state_dir: pathlib.Path, session_id: str) -> dict:
    """hookと同じ固定保存先から状態を取得する。"""
    del state_dir
    path = state_path(session_id)
    return json.loads(path.read_text(encoding="utf-8"))


def _home_path() -> str:
    """テストが起動するフックのプロセスが解決するホームディレクトリを実行時に返す。

    `_run`が起動するフックは呼び出し時点の環境変数からホームを解決するため、
    テスト側も同じ時点で解決する。収集時に評価したクラス変数は、実行環境のホームを
    差し替えるfixtureの適用前の値を保持するため、フックが解決する値と一致しない。
    """
    return str(pathlib.Path.home())


def _stage_model_env(tmp_path: pathlib.Path, value: str) -> dict[str, str]:
    """実行レビューが使うreview用途のengine順を隔離したXDG設定ディレクトリへ保存する。"""
    config_home = tmp_path / "config"
    config_dir = config_home / "agent-toolkit"
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text(
        json.dumps({"review_engine": value}),
        encoding="utf-8",
    )
    return {"XDG_CONFIG_HOME": str(config_home)}


def _user_facing_payload(field: str, value: str) -> dict:
    """指定したユーザー向け本文欄だけへ値を設定したpayloadを返す。"""
    if field == "plan":
        return {"tool_name": "ExitPlanMode", "tool_input": {"plan": value}}
    option = {"label": "選択肢", "description": "説明文"}
    question = {"question": "質問文", "header": "見出し", "options": [option]}
    if field in {"question", "header"}:
        question[field] = value
    else:
        option[field] = value
    return {"tool_name": "AskUserQuestion", "tool_input": {"questions": [question]}}


def _plan_file_state_env(
    tmp_path: pathlib.Path,
    home_dir: pathlib.Path | None = None,
) -> dict[str, str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    env = {"TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path)}
    if home_dir is not None:
        env["HOME"] = str(home_dir)
        env["USERPROFILE"] = str(home_dir)
    return env


def _make_plan_file(home_dir: pathlib.Path, name: str = "test.md") -> pathlib.Path:
    plans = home_dir / ".claude" / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    plan = plans / name
    plan.write_text("# t\n", encoding="utf-8")
    return plan


def _make_private_notes_plan_file(private_notes: pathlib.Path, name: str = "test.md") -> pathlib.Path:
    """新しいprivate-notes計画root配下の計画ファイルを作成する。"""
    plans = private_notes / "plans" / "2026" / "08"
    plans.mkdir(parents=True, exist_ok=True)
    plan = plans / name
    plan.write_text("# t\n", encoding="utf-8")
    return plan


def _write_tmp_file(tmp_path: pathlib.Path, relative_path: str, content: str) -> pathlib.Path:
    path = tmp_path / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


_VALID_H2_PLAN_CONTENT = (
    "## 概要\n\nx\n\n"
    "### 計画メタ情報\n\n"
    f"- ベースコミット: `{'a' * 40}`\n\n"
    "## 実装資料\n\n### 変更説明\n\nREADMEを更新する。\n\n"
    "## 完了条件\n\nx\n\n"
    "## 進捗ログ\n\nx\n"
)


def _make_repo_with_optional_remote(path: pathlib.Path, remote_url: str | None) -> str:
    """判定を省略する条件の確認用に、remote設定の有無を選べるgit repoを作成する。

    `remote_url`が`None`の場合は`git remote`が空リストを返すrepoになる。
    """
    return str(git_repository.init_repository(path, origin=remote_url))


def _process_loop_log_env(tmp_path: pathlib.Path) -> dict[str, str]:
    return {
        "AGENT_TOOLKIT_PROCESS_LOOP_SESSION": "1",
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "LOCALAPPDATA": str(tmp_path / "state"),
    }


def _path_section_build_content(recorded_path: str) -> str:
    """撤去した末尾パス節の判定処理へ与える計画本文を組み立てる。"""
    return (
        "## 概要\n\nx\n\n"
        "## 実装資料\n\n### 変更説明\n\nREADMEを更新する。\n\n"
        "## 完了条件\n\nx\n\n"
        "## 進捗ログ\n\nx\n\n"
        "## 計画ファイル（本ファイル）のパス\n\n"
        f"`{recorded_path}`\n"
    )


_HANGUL_SAMPLE = "\uac00"  # ハングル音節1文字（エスケープ表記で構成する）


_CYRILLIC_SAMPLE = "\u0430"  # キリル小文字1文字（同上）


def _patch(*sections: str) -> str:
    """Codexの`apply_patch`入力本文を組み立てる。"""
    body = "".join(sections)
    return f"*** Begin Patch\n{body}*** End Patch\n"


def _codex_payload(patch_text: str, cwd: pathlib.Path, session_id: str = "codex-edit") -> dict:
    """Codexの編集payloadを組み立てる。"""
    return {
        "tool_name": "apply_patch",
        "tool_input": {"command": patch_text},
        "cwd": str(cwd),
        "session_id": session_id,
        "turn_id": "turn-1",
    }


@contextlib.contextmanager
def read_delivery_preparation_scope() -> Generator[None]:
    """資料・導入情報・解決環境が不変の試験区間だけ、静的な解析を共有する。

    各入力の選択・配送・主体別の取得と公開hookの呼出しは毎回行う。
    資料や環境を変える前にこの区間を終了し、次の区間で解析し直す。
    """
    with read_triggers.resolution_scope():
        yield


def prepare_read_delivery(payload: object, env_overrides: dict[str, str] | None = None) -> None:
    """他のチェックの前提として、宣言資料を主体の取得操作で読む。"""
    if not isinstance(payload, dict) or not payload.get("session_id"):
        return

    with pytest.MonkeyPatch.context() as patch:
        for name, value in (env_overrides or {}).items():
            patch.setenv(name, value)
        # 準備中の操作は資料を変更しないため、同じhost・cwdの宣言を取得イベントごとに再解析しない。
        patch.setattr(read_delivery, "declarations", functools.lru_cache(maxsize=None)(read_delivery.declarations))
        session_id = payload["session_id"]
        for _attempt in range(12):
            decision = read_delivery.deliver(payload, payload.get("tool_name", ""), payload.get("tool_input", {}), session_id)
            # ここで採用した案内を配送済みにし、未配送の試作結果とは区別する。
            notice.record_delivery("\n".join(decision.notices))
            records = read_state(session_id).get(rules_context.OPERATION_SKILL_READY_KEY, {})
            agent = agent_id.resolve_hook_agent_id(payload)
            targets = [
                target
                for target in read_delivery.selected_targets(records, agent)
                if not read_delivery.target_ready(records, target, agent)
            ]
            # 他のチェックの通過試験では、条件付き候補も取得済みの前提にそろえる。
            # 候補の未取得通知そのものは、準備を使わないread_deliveryの試験で検証する。
            if not targets and any(
                marker in notice
                for notice in decision.notices
                for marker in ("操作前に読む資料が未取得", "現在の操作に関連する読込条件の候補")
            ):
                targets = [
                    target
                    for row in read_delivery.declarations(str(payload.get("cwd", "")))
                    if read_delivery.row_matches(
                        row, payload.get("tool_name", ""), payload.get("tool_input", {}), str(payload.get("cwd", ""))
                    )
                    for target in row.targets
                    if not read_delivery.target_ready(records, target, agent)
                ]
            if not targets:
                return
            subject = operation_skills.resolve_subject(payload)
            if subject == operation_skills.SUBJECT_CODEX:
                paths = list(dict.fromkeys(target.path for target in targets))
                event = {
                    **payload,
                    "hook_event_name": "PostToolUse",
                    "tool_name": "Bash",
                    "tool_input": {"command": "cat -- " + shlex.join(map(str, paths))},
                    "tool_response": {
                        "stdout": "".join(path.read_text(encoding="utf-8") for path in paths),
                        "exit_code": 0,
                    },
                }
                assert _run_posttooluse(event, env_overrides or {}).returncode == 0
                delivered = codex_delivered_payload(event)
                assert (
                    _run(
                        {**delivered, "hook_event_name": "PreToolUse", "tool_name": "ObserveDelivery", "tool_input": {}},
                        env_overrides,
                    ).returncode
                    == 0
                )
                continue
            for target in targets:
                subject = operation_skills.resolve_subject(payload)
                skill = read_delivery.skill_name(target.path)
                if subject != operation_skills.SUBJECT_CODEX and skill:
                    tool_name, tool_input = "Skill", {"skill": skill}
                else:
                    tool_name, tool_input = "Read", {"file_path": str(target.path)}
                event = {
                    **payload,
                    "hook_event_name": "PostToolUse",
                    "tool_name": tool_name,
                    "tool_input": tool_input,
                    "tool_response": {"stdout": target.path.read_text(encoding="utf-8"), "exit_code": 0},
                }
                assert _run_posttooluse(event, env_overrides or {}).returncode == 0
        raise AssertionError("読込済み前提を構築できなかった")


def run_after_read_delivery(payload: object, env_overrides: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """宣言読込の前提を満たして、試験対象のチェックへ公開hookから到達する。"""
    prepare_read_delivery(payload, env_overrides)
    return _run(payload, env_overrides)
