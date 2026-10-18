"""成果作業と`agent-toolkit:completion-report`の起動証拠を作業単位で保持する。

各イベントはこの所有モジュールへ観測を渡す。成果作業は成功した対象スキルの開始、リポジトリへの自編集、
計画の保存、WIの保存、commit・pushの実行記録から、起動証拠はClaude Codeの成功したSkill呼び出しと、
Codexの実際の入力と結果が示す`SKILL.md`の読取から取得する。完了の自己申告と報告本文は受理しない。
意味上の中止・開始はメインが原入力と対象を結び付け、ここでは識別子から実体を読み、由来と対象を確かめる。
"""

from __future__ import annotations

import json
import os
import pathlib

# 関数の実行時にだけ使う依存は、使用時まで読み込まない。
lazy import argparse
lazy import shlex
lazy import subprocess
lazy from typing import Any

lazy from agent_toolkit._agents_server import tool_names
lazy from agent_toolkit._atk.wi import uwi_scan
lazy from agent_toolkit._atk.wi.constants import WI_PROCESSABLE_STATES
lazy from agent_toolkit._atk.wi.frontmatter import parse_frontmatter
lazy from agent_toolkit._common import automated_prompt, next_action, runtime_inserted, session_state, shell_cwd
lazy from agent_toolkit._common import delegated_session as _delegated_session
lazy from agent_toolkit._common.bash_invocations import extract_bash_invocations
lazy from agent_toolkit._common.process_loop_session import is_process_loop_session
lazy from agent_toolkit._common.shell_segments import extract_execution_segments
lazy from agent_toolkit._common.shell_tokens import ATK_SHELL_TOOLS, is_atk_command
lazy from agent_toolkit._git import command as git_command
lazy from agent_toolkit._hooks import agent_id, agents_server_observations
lazy from agent_toolkit._hooks import tool_input as _hook_tool_input
lazy from agent_toolkit._hooks.host import is_codex_payload
lazy from agent_toolkit._hooks.pretooluse.operation_skills import written_paths
lazy from agent_toolkit._hooks.pretooluse.shell_checks import _git_subcommand_tokens
lazy from agent_toolkit._hooks.stop_session import append_stop_log
lazy from agent_toolkit._plan.path_kinds import is_plan_main_file

STATE_KEY = "termination_evidence"

COMPLETION_REPORT = "completion-report"
"""起動証拠を確かめるスキルの短縮名。"""

TARGET_SKILLS = ("process-wi", "add-awi-by-user")
"""開始した時点で成果作業となるスキルの短縮名。適用条件は`completion-report/SKILL.md`「Stop hookの判定」の成果作業に従う。"""

_UNRESOLVED_REPOSITORY_FIX = (
    "この作業では省略を記録できないため、`agent-toolkit:completion-report`を起動して終了工程を進める。"
    "規範がその起動を禁じる場合は、その旨をユーザーへ報告する。"
)


class _UnresolvedRepositoryError(ValueError):
    """所属不明の作業には、省略の再申請と異なる終了操作を案内する。"""


_READ_PROGRAMS = frozenset({"cat", "sed", "head", "nl", "less", "more", "bat", "type", "get-content", "gc"})
"""Codexが`SKILL.md`を表示する読取コマンド。検索・抽出のコマンドは本文全体を返さないため含めない。"""

_GIT_VALUE_OPTIONS = frozenset(
    {
        "-m",
        "--message",
        "-F",
        "--file",
        "-C",
        "--reuse-message",
        "-c",
        "--reedit-message",
        "--author",
        "--date",
        "--cleanup",
        "-t",
        "--template",
        "--trailer",
        "--fixup",
        "--squash",
        "--pathspec-from-file",
        "--repo",
        "--receive-pack",
        "--exec",
        "-o",
        "--push-option",
    }
)
"""`git commit`・`git push`の値を伴うオプション。値を除外語の判定へ混ぜないため、値のトークンを判定の対象から除く。"""


def _parse(payload_text: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError, ValueError:
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("session_id"), str) or not payload["session_id"]:
        return None
    return payload if agent_id.is_main_agent_context(payload) else None


def _data(state: dict[str, Any], session_id: str) -> dict[str, Any]:
    current = state.get(STATE_KEY)
    if (
        not isinstance(current, dict)
        or current.get("session_id") != session_id
        or current.get("version") != 1
        or not all(isinstance(current.get(key), dict) for key in ("works", "calls", "inputs"))
        or not isinstance(current.get("sequence"), int)
    ):
        if current is not None:
            append_stop_log(session_id, "termination_evidence_unavailable", {"reason": "状態の版または形式が異なる"})
        current = {"version": 1, "session_id": session_id, "sequence": 0, "works": {}, "calls": {}, "inputs": {}}
        state[STATE_KEY] = current
    return current


def _compact(data: dict[str, Any]) -> None:
    """現在の証拠と遅延応答に必要な参照だけを保持する。"""
    referenced: set[str] = {data.get("last_input", ""), data.get("last_human_input", "")}
    for work in data["works"].values():
        if work.get("publish_scope") or work.get("completion_report"):
            work.pop("edits", None)
        referenced.add(work.get("publish_scope", {}).get("input_id", ""))
        decisions = work.get("decisions", [])
        if decisions:
            work["decisions"] = decisions[-1:]
            referenced.add(decisions[-1].get("input_id", ""))
        referenced.add(work["reference"])
        retained = set(work.get("async_targets", {}).values())
        if decisions:
            retained.add(decisions[-1].get("call_id", ""))
        retained.add(work.get("failure_call", ""))
        for call_id in list(work["calls"]):
            records = data["calls"].get(call_id, [])
            # 所属不明の操作の種類を省略拒否へ示すため、起動証拠を得るまで既存の呼出し記録を保つ。
            unresolved = any(record.get("kind") == "deliverable" and record.get("repository") is None for record in records)
            if unresolved and not work.get("completion_report"):
                continue
            if call_id not in retained and all(record.get("observed") for record in records):
                data["calls"].pop(call_id, None)
                work["calls"].remove(call_id)
    data["inputs"] = {key: value for key, value in data["inputs"].items() if key in referenced}


def _new_work(data: dict[str, Any], reference: str) -> tuple[str, dict[str, Any]]:
    data["sequence"] += 1
    work_id = f"work-{data['sequence']}"
    work = {
        "reference": reference,
        "calls": [],
        "decisions": [],
        "deliverables": [],
        "async_targets": {},
        "offset": data["inputs"].get(reference, {}).get("offset", 0),
        "input_at_last_call": data.get("last_human_input", data.get("last_input")),
    }
    data["works"][work_id] = work
    data["current_work"] = work_id
    return work_id, work


def _current_work(data: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    work_id = data.get("current_work")
    work = data["works"].get(work_id)
    return (work_id, work) if isinstance(work_id, str) and isinstance(work, dict) else None


def _last_decision(work: dict[str, Any]) -> str:
    decisions = work.get("decisions", [])
    return decisions[-1]["action"] if decisions else ""


def _finished(data: dict[str, Any], work: dict[str, Any]) -> bool:
    """起動・省略の証拠を得た作業の後に新しい入力が届いたかを返す。"""
    return bool(work.get("completion_report") or work.get("omit_completion")) and data.get(
        "last_human_input", data.get("last_input")
    ) != work.get("input_at_last_call")


def _position(payload: dict[str, Any]) -> int:
    path = payload.get("transcript_path")
    try:
        return pathlib.Path(path).stat().st_size if isinstance(path, str) else 0
    except OSError:
        return 0


def _skill_name(value: object) -> str | None:
    """Skillの名前から`agent-toolkit:`の修飾を除いた短縮名を返す。"""
    if not isinstance(value, str) or not value:
        return None
    return value.removeprefix("agent-toolkit:")


def published_git_operations(command: str) -> list[str]:
    """Bashのコマンドが実行する`git commit`・`git push`を、試行・ヘルプ・背景実行を除いて返す。"""
    operations: list[str] = []
    for invocation in extract_bash_invocations(command):
        parsed = _git_subcommand_tokens(invocation.segment)
        if invocation.background or parsed is None or parsed[0] not in {"commit", "push"}:
            continue
        arguments = parsed[1]
        if any("\x00" in argument for argument in arguments):
            continue
        options = iter(arguments)
        excluded = False
        for option in options:
            if option == "--":
                break
            if option in _GIT_VALUE_OPTIONS:
                next(options, None)
            elif option in {"--dry-run", "--help", "-h"} or parsed[0] == "push" and option == "-n":
                excluded = True
                break
        prefix = invocation.segment.tokens[: len(invocation.segment.tokens) - len(arguments)]
        if not excluded and "--help" not in prefix:
            operations.append(parsed[0])
    return operations


def _bash_operations(command: str) -> tuple[list[str], list[str]]:
    """Bashのコマンドから、WI・計画を保存する操作と`SKILL.md`の読取対象のスキル名を返す。"""
    saved: list[str] = []
    read_skills: list[str] = []
    for segment in extract_execution_segments(command):
        if not segment.resolved or not segment.tokens:
            continue
        tokens = list(segment.tokens)
        name = pathlib.PurePosixPath(tokens[0].replace("\\", "/")).name
        if name.startswith("python") and len(tokens) > 1 and not tokens[1].startswith("-"):
            tokens.pop(0)
            name = pathlib.PurePosixPath(tokens[0].replace("\\", "/")).name
        if "--help" in tokens or "-h" in tokens:
            continue
        if is_atk_command(tokens):
            if tokens[1:2] == ["wi"] and tokens[2:3] in (["add"], ["edit"]):
                saved.append("WIの保存")
            elif tokens[1:3] == ["run-script", "plan-create"]:
                saved.append("計画の保存")
            elif tokens[1:2] == ["read-file"]:
                read_skills.extend(_skill_paths(tokens[2:]))
        elif name == "create_plan_files.py":
            saved.append("計画の保存")
        elif name.lower().removesuffix(".exe") in _READ_PROGRAMS:
            read_skills.extend(_skill_paths(tokens[1:]))
    return saved, read_skills


def _skill_paths(arguments: list[str]) -> list[str]:
    """引数のうち、起動証拠と成果作業の対象スキルの`SKILL.md`を指すパスのスキル名を返す。"""
    names = []
    for argument in arguments:
        parts = pathlib.PurePosixPath(argument.replace("\\", "/")).parts
        if (
            len(parts) >= 3
            and parts[-1] == "SKILL.md"
            and parts[-3] == "skills"
            and parts[-2] in {COMPLETION_REPORT, *TARGET_SKILLS}
        ):
            names.append(parts[-2])
    return names


def _read_succeeded(skill: str, text: str | None) -> bool:
    """読取の結果が、そのスキルの`SKILL.md`の本文を返したかを返す。パスの文字列だけを根拠にしない。"""
    return text is not None and f"name: {skill}" in _returned_text(text)


def _invocations(payload: dict[str, Any], *, after: bool, failed: bool) -> list[dict[str, Any]]:
    """ツール呼び出しから、成果作業・起動証拠・非同期の待機対象の記録を返す。

    成果作業と起動証拠は成功した応答の後だけで返す。非同期の待機対象は開始時点の作業へ対応付けるため、
    開始前にも返す。
    """
    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return []
    if tool_name in {
        namespace + operation for namespace in tool_names.MCP_NAMESPACES for operation in tool_names.RECORDED_START_OPERATIONS
    }:
        return [{"kind": "async"}]
    if not after or failed:
        return []
    codex = is_codex_payload(payload)
    if tool_name == "Skill" and not codex:
        skill = _skill_name(tool_input.get("skill"))
        if skill == COMPLETION_REPORT:
            return [{"kind": "completion"}]
        if skill in TARGET_SKILLS:
            record: dict[str, Any] = {"kind": "deliverable", "labels": [f"agent-toolkit:{skill}の開始"]}
            if isinstance(payload.get("cwd"), str) and (repository := _git_root(payload["cwd"])) is not None:
                record["repository"] = str(repository)
            return [record]
        return []
    labels: list[str] = []
    records: list[dict[str, Any]] = []
    cwd = payload.get("cwd")
    if isinstance(cwd, str):
        paths = written_paths(tool_name, tool_input, cwd)
        grouped: dict[pathlib.Path, list[str]] = {}
        private = uwi_scan.private_notes_root() if paths else None
        for value in paths:
            path = pathlib.Path(value)
            if not path.is_absolute() or private is not None and path.is_relative_to(private):
                continue
            repository = _git_root(str(path.parent))
            if repository is not None:
                grouped.setdefault(repository, []).append(path.relative_to(repository).as_posix())
        for repository, relative in grouped.items():
            if relative:
                records.append(
                    {"kind": "deliverable", "labels": ["リポジトリの編集"], "repository": str(repository), "paths": relative}
                )
    operations = _hook_tool_input.parse_operations(tool_name, tool_input, cwd if isinstance(cwd, str) else "")
    if any(operation.exists_after_apply and is_plan_main_file(operation.display_path) for operation in operations or []):
        labels.append("計画の保存")
    command = tool_input.get("command")
    if tool_name in ATK_SHELL_TOOLS and isinstance(command, str):
        for invocation, current in shell_cwd.bash_invocation_cwds(command, cwd if isinstance(cwd, str) else ""):
            single = shlex.join(invocation.segment.tokens)
            saved, read_skills = _bash_operations(single)
            item_labels = [*saved, *([] if invocation.background else published_git_operations(single))]
            if codex:
                text = _response_text(payload.get("tool_response"))
                for skill in dict.fromkeys(read_skills):
                    if not _read_succeeded(skill, text):
                        continue
                    if skill == COMPLETION_REPORT:
                        records.append({"kind": "completion"})
                    else:
                        item_labels.append(f"agent-toolkit:{skill}の開始")
            if item_labels:
                repository = _invocation_repository(invocation, current)
                records.append(
                    {"kind": "deliverable", "labels": item_labels, "repository": str(repository) if repository else None}
                )
    if labels:
        record = {"kind": "deliverable", "labels": list(dict.fromkeys(labels))}
        repository = _git_root(cwd) if isinstance(cwd, str) else None
        record["repository"] = str(repository) if repository is not None else None
        records.append(record)
    return records


def _invocation_repository(invocation: shell_cwd.BashInvocation, cwd: shell_cwd.CwdResolution) -> pathlib.Path | None:
    """操作ごとの実行位置とGitの位置指定から所属先を求める。"""
    if not cwd.resolved or not invocation.arguments_known:
        return None
    tokens = invocation.segment.tokens
    parsed = _git_subcommand_tokens(invocation.segment)
    options = tokens[1 : len(tokens) - len(parsed[1]) - 1] if parsed is not None else ()
    return _git_root(cwd.path, options=options)


def _git_root(cwd: str, *, options: tuple[str, ...] = ()) -> pathlib.Path | None:
    """書込時の作業ディレクトリが属するGitリポジトリだけを解決する。"""
    try:
        result = git_command.run(
            [*options, "rev-parse", "--show-toplevel"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except OSError, subprocess.TimeoutExpired:
        return None
    return pathlib.Path(result.stdout.strip()).resolve() if result.returncode == 0 and result.stdout.strip() else None


def _uncommitted_paths(repository: str, paths: list[str]) -> list[str] | None:
    """成功した自編集の対象だけを現在のGit状態と比べる。"""
    try:
        result = git_command.run(
            ["status", "--porcelain=v1", "-z", "--untracked-files=all", "--", *paths],
            cwd=repository,
            capture_output=True,
            check=False,
            timeout=5,
        )
    except OSError, subprocess.TimeoutExpired:
        return None
    if result.returncode:
        return None
    fields = result.stdout.decode("utf-8", errors="surrogateescape").split("\0")
    changed = []
    index = 0
    while index < len(fields):
        field = fields[index]
        index += 1
        if len(field) < 4:
            continue
        if field[3:] in paths:
            changed.append(field[3:])
        if "R" in field[:2] or "C" in field[:2]:
            index += 1
    return sorted(set(changed))


def unscoped_self_edits(payload: dict[str, Any], work: dict[str, Any]) -> list[str]:
    """協調Claudeメインの作業が残す、公開範囲が未確定の未コミットの自編集を返す。"""
    if is_codex_payload(payload) or not agent_id.is_main_agent_context(payload):
        return []
    state = session_state.read_state(payload["session_id"])
    if state.get("process_wi_skill_invoked") or is_process_loop_session(payload["session_id"], os.environ):
        return []
    data = state.get(STATE_KEY)
    reference = data.get("inputs", {}).get(work.get("reference"), {}) if isinstance(data, dict) else {}
    if reference.get("human") is not True or work.get("publish_scope"):
        return []
    changed: list[str] = []
    for repository, paths in work.get("edits", {}).items():
        current = _uncommitted_paths(repository, paths)
        if current is None:
            append_stop_log(payload["session_id"], "self_edit_state_unavailable", {"repository": repository})
            continue
        changed.extend(str(pathlib.Path(repository) / path) for path in current)
    return changed


# 両ホストのPostToolUseの`tool_response`の形（CodexのBashでは終了コードを含まない出力文字列）と、
# Stopの出力契約を公式Hooks仕様で確かめた監査記録は`docs/development/audit-records.md`の
# 「agent-toolkit/agent_toolkit/_hooks/termination_evidence.py：終了工程の証拠のStop判定：2026年10月3日」にある。
def _response_text(response: object) -> str | None:
    if isinstance(response, str):
        try:
            decoded = json.loads(response)
        except json.JSONDecodeError, ValueError:
            return response
        if isinstance(decoded, dict):
            return _response_text(decoded) or response
        return response
    if isinstance(response, dict):
        for key in ("stdout", "output", "text"):
            value = response.get(key)
            if isinstance(value, str):
                return value
    return None


def _returned_text(text: str) -> str:
    # atkが出力を保存先へ書いた場合は保存先の全量を読み、欠落したpreviewを読取結果の代わりにしない。
    paths = [line.removeprefix("保存先: ").strip() for line in text.splitlines() if line.startswith("保存先: ")]
    if len(paths) == 1:
        path = pathlib.Path(paths[0])
        if path.is_absolute():
            try:
                return path.read_text(encoding="utf-8")
            except OSError, UnicodeError:
                return ""
    return text


def observe_user(payload_text: str) -> None:
    """実際のUserPromptSubmit本文を、生成入力と区別して保存する。"""
    payload = _parse(payload_text)
    if payload is None:
        return
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt:
        return
    human = (
        payload.get("source", "user") == "user"
        and not runtime_inserted.is_runtime_generated(payload)
        and not runtime_inserted.is_runtime_inserted_text(prompt)
        and not automated_prompt.contains(prompt)
    )
    if not human and STATE_KEY not in session_state.read_state(payload["session_id"]):
        return

    def update(state: dict[str, Any]) -> dict[str, Any]:
        data = _data(state, payload["session_id"])
        reference = payload.get("turn_id") or payload.get("prompt_id")
        if not isinstance(reference, str) or not reference:
            data["sequence"] += 1
            reference = f"input-{data['sequence']}"
        data["inputs"][reference] = {"text": prompt, "human": human, "offset": _position(payload)}
        data["last_input"] = reference
        if human:
            data["last_human_input"] = reference
        _compact(data)
        return state

    session_state.update_state(payload["session_id"], update)


def observe_tool(payload_text: str, *, after: bool) -> None:
    """同じtool_use_idの実行位置と応答を同じ作業へ対応付ける。"""
    resolved_payload = _parse(payload_text)
    if resolved_payload is None:
        return
    payload: dict[str, Any] = resolved_payload
    tool_id = payload.get("tool_use_id")
    if not isinstance(tool_id, str) or not tool_id:
        return
    response = payload.get("tool_response")
    code = response.get("exit_code") if isinstance(response, dict) else None
    failed = payload.get("hook_event_name") == "PostToolUseFailure" or (
        isinstance(code, int) and not isinstance(code, bool) and code != 0
    )
    invocations = _invocations(payload, after=after, failed=failed)
    if not invocations and not (after and failed):
        # 工程外の成功した呼び出しは状態を読み書きせず、全ツール呼び出しの排他更新を避ける。
        return

    def update(state: dict[str, Any]) -> dict[str, Any]:
        if not invocations and STATE_KEY not in state:
            return state
        data = _data(state, payload["session_id"])
        records: Any = data["calls"].get(tool_id)
        if not isinstance(records, list):
            current = _current_work(data)
            if not invocations:
                # 工程外の呼び出しは現在の作業の失敗証拠に限って採取する。
                if current is None:
                    return state
                work_id, work = current
                records = [{"kind": "failure", "work_id": work_id, "observed": True, "response": response}]
                data["calls"][tool_id] = records
                work["calls"].append(tool_id)
                work["failure_call"] = tool_id
                _compact(data)
                return state
            if current is None or _last_decision(current[1]) in {"cancel", "replace"} or _finished(data, current[1]):
                if invocations[0]["kind"] == "async":
                    return state
                current = _new_work(data, data.get("last_human_input", data.get("last_input", tool_id)))
            work_id, work = current
            work["input_at_last_call"] = data.get("last_human_input", data.get("last_input"))
            data["sequence"] += 1
            records = [{**invocation, "work_id": work_id, "sequence": data["sequence"]} for invocation in invocations]
            data["calls"][tool_id] = records
            work["calls"].append(tool_id)
        if not after:
            return state
        for record in records:
            work = data["works"].get(record["work_id"])
            if not isinstance(work, dict):
                continue
            record["observed"] = True
            if record["kind"] == "completion":
                work["completion_report"] = True
            elif record["kind"] == "deliverable":
                known_labels = work.setdefault("deliverables", [])
                known_labels.extend(label for label in record["labels"] if label not in known_labels)
                if "repository" in record:
                    repositories = work.setdefault("repositories", [])
                    if record["repository"] not in repositories:
                        repositories.append(record["repository"])
                    if "paths" in record:
                        known = work.setdefault("edits", {}).setdefault(record["repository"], [])
                        known.extend(path for path in record["paths"] if path not in known)
            elif record["kind"] == "async":
                started: Any = response
                if isinstance(started, dict) and isinstance(started.get("structuredContent"), dict):
                    started = started["structuredContent"]
                if isinstance(started, str):
                    try:
                        started = json.loads(started)
                    except ValueError:
                        started = None
                if isinstance(started, dict) and isinstance(started.get("session_id"), str):
                    work["async_targets"][started["session_id"]] = tool_id
        _compact(data)
        return state

    session_state.update_state(payload["session_id"], update)


def evidence_available(payload: dict[str, Any]) -> bool:
    """現在の会話の証拠を読み取れるかを返す。別会話の継承値と旧版の状態は読み取れないとして記録する。"""
    data = session_state.read_state(payload["session_id"]).get(STATE_KEY)
    if data is None:
        return True
    if isinstance(data, dict) and data.get("session_id") == payload["session_id"] and data.get("version") == 1:
        return True
    append_stop_log(payload["session_id"], "termination_evidence_unavailable", {"reason": "状態の版または形式が異なる"})
    return False


def missing_completion(work: dict[str, Any]) -> list[str]:
    """起動証拠と有効な省略の双方を欠く成果作業の種類を返す。"""
    if work.get("completion_report"):
        return []
    omission = work.get("omit_completion")
    if isinstance(omission, dict) and omission.get("input_id") == work.get("input_at_last_call"):
        try:
            if _omission_policy(omission, work) == omission.get("policy_text"):
                return []
        except OSError, UnicodeError, ValueError:
            pass
    return list(work.get("deliverables", []))


def _omission_policy(document: dict[str, Any], work: dict[str, Any], *, unresolved_labels: list[str] | None = None) -> str:
    """観測した作業の単一リポジトリ内の規範を読み、対象への適用範囲を確かめる。"""
    target = document.get("target_repo")
    policy = document.get("policy_file")
    if not isinstance(target, str) or not pathlib.Path(target).is_absolute():
        raise ValueError("省略する作業のtarget_repoを絶対パスで渡す")
    repository = pathlib.Path(target).resolve(strict=True)
    observed = set(work.get("repositories", [])) | set(work.get("edits", {}))
    if None in observed:
        raise _UnresolvedRepositoryError(
            "対象リポジトリを特定できなかった成果作業: " + "、".join(unresolved_labels or ["種類の内訳を保持しない旧記録"])
        )
    if not work.get("deliverables") or observed != {str(repository)} or _git_root(str(repository)) != repository:
        raise ValueError("省略は観測済みの成果作業が属する単一リポジトリのrootを指定する")
    if not isinstance(policy, str) or not pathlib.Path(policy).is_absolute():
        raise ValueError("プロジェクト規範のpolicy_fileを絶対パスで渡す")
    path = pathlib.Path(policy).resolve(strict=True)
    if not path.is_relative_to(repository):
        raise ValueError("policy_fileは対象リポジトリ内の適用するプロジェクト規範を指定する")
    return path.read_text(encoding="utf-8")


def _waiting_uwi(document: dict[str, Any], session_id: str) -> pathlib.Path:
    """現在の会話が投入した実在の未回答UWIを確かめる。"""
    path_text = document.get("uwi_file")
    root = uwi_scan.private_notes_root()
    if root is None or not isinstance(path_text, str):
        raise ValueError("確認待ちには未回答UWIの絶対パスを渡す")
    path = pathlib.Path(path_text)
    if not path.is_absolute() or path.parent not in {root / name for name in WI_PROCESSABLE_STATES}:
        raise ValueError("確認待ちのUWIはactive状態のキューから取得する")
    text = path.read_text(encoding="utf-8")
    parsed = parse_frontmatter(text)
    if parsed is None or parsed[0].get("type") != "uwi" or parsed[0].get("submitter_session") != session_id:
        raise ValueError("現在の会話が投入したUWIを指定する")
    if uwi_scan.is_uwi_answered(text):
        raise ValueError("未回答のUWIを指定する")
    if "quote" in document and document["quote"] != text:
        raise ValueError("quoteは指定した未回答UWIの全文と一致させるか省略する")
    return path


def record_decision(document: dict[str, Any]) -> str:
    """実在する原入力と作用対象を持つ判断を記録し、対象作業の識別子を返す。"""
    session_id = document.get("session_id")
    action = document.get("action")
    if (
        not isinstance(session_id, str)
        or not session_id
        or action not in {"start", "cancel", "replace", "resume", "wait", "blocked", "publish-scope", "omit-completion"}
    ):
        raise ValueError("session_idと受理する判断のactionを指定する")
    if not agent_id.is_main_agent_context({"agent_id": document.get("agent_id", "main")}):
        raise ValueError("終了工程の判断はメインが記録する")
    owner = _delegated_session.owner_session_id(os.environ)
    if owner and owner != session_id:
        raise ValueError("現在の会話のsession_idを指定する")
    if action == "omit-completion":
        current_session = os.environ.get("CODEX_THREAD_ID") or os.environ.get("CLAUDE_CODE_SESSION_ID")
        if not agent_id.is_main_agent_context({}) or current_session != session_id:
            raise ValueError("省略の判断は現在の会話のメインが記録する")
    if not isinstance(document.get("reason"), str) or not document["reason"].strip():
        raise ValueError("判断の理由を指定する")
    selected = ""

    def update(state: dict[str, Any]) -> dict[str, Any]:
        nonlocal selected
        data = _data(state, session_id)
        evidence: dict[str, Any] = {}
        if action == "omit-completion":
            work = data["works"].get(document.get("work_id"))
            input_id = document.get("input_id", data.get("last_human_input"))
            reference = data["inputs"].get(input_id)
            if not isinstance(work, dict):
                raise ValueError("実在するwork_idを指定する")
            if (
                not isinstance(reference, dict)
                or reference.get("human") is not True
                or input_id != data.get("last_human_input")
                or input_id != work.get("input_at_last_call")
            ):
                raise ValueError("省略する観測済み作業の現在の人間入力のinput_idを渡す")
            unresolved_labels = list(
                dict.fromkeys(
                    label
                    for call_id in work.get("calls", [])
                    for record in data["calls"].get(call_id, [])
                    if record.get("kind") == "deliverable" and record.get("repository") is None
                    for label in record["labels"]
                )
            )
            text = _omission_policy(document, work, unresolved_labels=unresolved_labels)
            if "quote" in document and text != document["quote"]:
                raise ValueError("quoteは適用するプロジェクト規範の全文と一致させるか省略する")
            evidence = {
                "input_id": input_id,
                "policy_file": document["policy_file"],
                "target_repo": document["target_repo"],
                "policy_text": text,
            }
        elif action == "wait":
            target = document.get("target_session_id")
            child = agents_server_observations.session_record(state, target)
            work_for_wait = data["works"].get(document.get("work_id"), {})
            if target is not None:
                if not isinstance(target, str) or target not in work_for_wait.get("async_targets", {}):
                    raise ValueError(f"委譲先待機の対象が作業に対応しません: {target}。実際のwork_idと対象sessionを指定する")
                if not isinstance(child, dict) or child.get("owner_agent_id") != "main":
                    raise ValueError(
                        f"委譲先待機の対象を所有していません: {target}。この作業が起動した所有済みの対象を指定する"
                    )
                active = agents_server_observations.actively_waited_session_ids([target])
                if child.get("pending_observation") is not True and target not in active:
                    raise ValueError(
                        f"対象sessionの有効なCLI待機がありません: {target}。実際の待機を開始するか回収後の残工程へ戻る"
                    )
                evidence["target_session_id"] = target
            else:
                evidence["uwi_file"] = str(_waiting_uwi(document, session_id))
        elif action == "blocked":
            call_id = document.get("call_id")
            records = data["calls"].get(call_id)
            if (
                not isinstance(records, list)
                or len(records) != 1
                or records[0].get("kind") != "failure"
                or records[0].get("work_id") != document.get("work_id")
            ):
                raise ValueError("対象作業の実際の失敗call_idを指定する")
            if (
                "quote" in document
                and json.dumps(records[0]["response"], ensure_ascii=False, sort_keys=True) != document["quote"]
            ):
                raise ValueError("quoteは実際の失敗応答全体のJSONと一致させるか省略する")
            evidence["call_id"] = call_id
        elif action == "publish-scope" and document.get("origin") == "standing-authorization":
            policy = document.get("policy_file")
            reference = data["inputs"].get(document.get("input_id"))
            if not isinstance(reference, dict) or reference.get("human") is not True:
                raise ValueError("適用済み認可を使う作業の人間入力のinput_idを渡す")
            if not isinstance(policy, str) or not pathlib.Path(policy).is_absolute():
                raise ValueError("適用済み認可の実在するpolicy_fileを絶対パスで渡す")
            text = pathlib.Path(policy).read_text(encoding="utf-8")
            if "quote" in document and text != document["quote"]:
                raise ValueError("quoteは適用済み認可の全文と一致させるか省略する")
            evidence = {"policy_file": policy, "input_id": document.get("input_id"), "policy_text": text}
        else:
            reference = data["inputs"].get(document.get("input_id"))
            if not isinstance(reference, dict) or reference.get("human") is not True:
                raise ValueError("原入力のinput_idを渡す。生成入力は人間の判断の根拠にできない")
            if "quote" in document and reference.get("text") != document["quote"]:
                raise ValueError("quoteは指定した原入力の全文と一致させるか省略する")
            evidence["input_id"] = document["input_id"]
        if action == "publish-scope":
            if document.get("scope") not in {
                "既存の判断基準どおり",
                "push・CIまで",
                "commitまで",
                "commitしない",
            } or document.get("origin") not in {"instruction", "answer", "standing-authorization"}:
                raise ValueError("公開範囲のscopeと原入力のoriginを指定する")
            selected = document.get("work_id", "")
            if selected:
                work = data["works"].get(selected)
                if not isinstance(work, dict):
                    raise ValueError("実在するwork_idを指定する")
            else:
                selected, work = _new_work(data, document.get("input_id", data.get("last_input", "")))
            work["publish_scope"] = {
                "scope": document["scope"],
                "origin": document["origin"],
                "reason": document["reason"],
                **evidence,
            }
            work["input_at_last_call"] = data.get("last_human_input", data.get("last_input"))
        elif action == "start":
            selected, work = _new_work(data, document["input_id"])
        else:
            selected = document.get("work_id", "")
            work = data["works"].get(selected)
            if not isinstance(work, dict):
                raise ValueError("実在するwork_idを指定する")
        if action != "publish-scope":
            work["decisions"].append(
                {
                    "action": action,
                    **{key: value for key, value in evidence.items() if key != "policy_text"},
                    "reason": document["reason"],
                }
            )
        if action == "omit-completion":
            work["omit_completion"] = evidence
        else:
            work.pop("omit_completion", None)
        if action == "resume":
            data["current_work"] = selected
        _compact(data)
        return state

    if not session_state.update_state(session_id, update):
        raise ValueError("判断の証拠を読み書きできない。対象の実体と状態の保存先を確かめる")
    return selected


def _session_works(state: dict[str, Any], session_id: str) -> dict[str, Any]:
    """現在の会話が記録した作業を返し、別会話の継承値と旧版の状態は空とする。"""
    data = state.get(STATE_KEY)
    if not isinstance(data, dict) or data.get("session_id") != session_id or data.get("version") != 1:
        return {}
    works = data.get("works")
    return works if isinstance(works, dict) else {}


def _async_target_alive(state: dict[str, Any], work: dict[str, Any], target: object) -> bool:
    """作業が起動して所有する委譲先が、実行中か未回収の結果を持つ場合に真を返す。"""
    child = agents_server_observations.session_record(state, target)
    return (
        isinstance(target, str)
        and target in work.get("async_targets", {})
        and isinstance(child, dict)
        and child.get("owner_agent_id") == "main"
        and (
            child.get("pending_observation") is True
            or target in agents_server_observations.actively_waited_session_ids([target])
        )
    )


def session_works(payload: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """現在の会話の全作業を、中止・置換した作業も含めて記録順に返す。"""
    works = _session_works(session_state.read_state(payload["session_id"]), payload["session_id"])
    return [(work_id, work) for work_id, work in works.items() if isinstance(work, dict)]


def waits_on_delegated_session(payload: dict[str, Any], work: dict[str, Any]) -> bool:
    """作業が起動したagents_serverのsessionのうち、実行中か未回収の結果を持つものがあれば真を返す。"""
    state = session_state.read_state(payload["session_id"])
    return any(_async_target_alive(state, work, target) for target in work.get("async_targets", {}))


def pending_work(payload: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """現在の会話の観測済み作業のうち、中止・置換・技術的不成立・正当な待機に当たらないものを返す。"""
    state = session_state.read_state(payload["session_id"])
    pending: list[tuple[str, dict[str, Any]]] = []
    for work_id, work in _session_works(state, payload["session_id"]).items():
        if not isinstance(work, dict) or _last_decision(work) in {"cancel", "replace", "blocked"}:
            continue
        if _last_decision(work) == "wait":
            decision = work["decisions"][-1]
            if _async_target_alive(state, work, decision.get("target_session_id")):
                continue
            path = decision.get("uwi_file")
            if isinstance(path, str):
                try:
                    if not uwi_scan.is_uwi_answered(pathlib.Path(path).read_text(encoding="utf-8")):
                        continue
                except (OSError, UnicodeError) as error:
                    append_stop_log(
                        payload["session_id"], "termination_wait_unknown", {"work_id": work_id, "reason": str(error)}
                    )
                    continue
        pending.append((work_id, work))
    return pending


def decision_hint(payload: dict[str, Any]) -> str:
    """判断記録に必要な会話と直近のユーザー入力の識別子を、不足の通知へ添える本文として返す。"""
    data = session_state.read_state(payload["session_id"]).get(STATE_KEY)
    last = data.get("last_human_input", data.get("last_input")) if isinstance(data, dict) else None
    entry = data.get("inputs", {}).get(last) if isinstance(data, dict) else None
    lines = [f"判断記録の`session_id`: {payload['session_id']}"]
    if isinstance(entry, dict) and entry.get("human") is True:
        lines.append(f"直近のユーザー入力の`input_id`: {last}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """判断記録用のJSONファイルを既存run-scriptから受理する。"""
    parser = argparse.ArgumentParser(description=__doc__)
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument(
        "--decision-file",
        type=pathlib.Path,
        metavar="PATH",
        help="判断記録のJSONオブジェクトを保存したファイルのパス（JSON文字列そのものは受け取らない）",
    )
    operation.add_argument(
        "--context",
        action="store_true",
        help="現在のClaude Codeメインの会話・作業の識別子と、観測済みなら人間入力をJSONで取得する",
    )
    args = parser.parse_args(argv)
    try:
        if args.context:
            session_id = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
            if not session_id or not agent_id.is_main_agent_context({}):
                raise ValueError("--contextは現在のClaude Codeメインで実行する")
            data = session_state.read_state(session_id).get(STATE_KEY)
            if (
                not isinstance(data, dict)
                or data.get("session_id") != session_id
                or data.get("version") != 1
                or not isinstance(data.get("works"), dict)
            ):
                raise ValueError("現在の会話の終了工程の証拠をまだ観測していない")
            # 作業・会話の識別子と人間入力由来の項目を分けて返す。実行結果を根拠とする判断（blocked、委譲先のwait）は
            # 人間入力を要さないため、自動起動の会話でも識別子の取得を止めない。人間の判断を要する記録の検証は
            # record_decisionが担い、生成入力は人間入力として返さない。
            input_id = data.get("last_human_input", data.get("last_input"))
            entry = data.get("inputs", {}).get(input_id) if isinstance(data.get("inputs"), dict) else None
            human = isinstance(entry, dict) and entry.get("human") is True
            current = _current_work(data)
            work_id = (
                current[0]
                if current is not None
                and not _finished(data, current[1])
                and _last_decision(current[1]) not in {"cancel", "replace"}
                else None
            )
            if not human and work_id is None:
                raise ValueError("現在の会話の作業と人間入力をまだ観測していない")
            print(
                json.dumps(
                    {
                        "session_id": session_id,
                        "input_id": input_id if human else None,
                        "quote": entry["text"] if human and isinstance(entry, dict) else None,
                        "work_id": work_id,
                    },
                    ensure_ascii=False,
                )
            )
            return 0
        document = json.loads(args.decision_file.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise ValueError("判断記録はJSONオブジェクトで渡す")
        work_id = record_decision(document)
    except (OSError, UnicodeError, ValueError) as error:
        next_action.report(
            str(error),
            next_action=_UNRESOLVED_REPOSITORY_FIX
            if isinstance(error, _UnresolvedRepositoryError)
            else "原入力と対象を確かめ、判断のJSONファイルを直して同じコマンドで再実行する",
        )
        return 2
    print(json.dumps({"work_id": work_id}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
