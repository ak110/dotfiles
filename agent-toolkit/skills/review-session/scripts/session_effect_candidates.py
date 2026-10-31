"""元セッションの適用記録から採用済みAWIの変更ファイルの利用位置を取り出す。"""

import datetime
import json
import pathlib
import re
import subprocess
from typing import Any

import session_evidence_execution as execution
import session_evidence_hook_notices as hooks
import session_evidence_tool_calls as calls

from agent_toolkit._atk.wi import effect_check, entries, frontmatter, repo, uwi_scan
from agent_toolkit._common import session_family

_PATH = re.compile(r"`([^`\n]+)`|(?<![\w/])(/[^\s<>\"'`、。]+)")
_PLUGIN = re.compile(r"(?:agent-toolkit|agent-basics) plugin root:\s*([^\s<>]+)")


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for child in value.values() for text in _strings(child)]
    if isinstance(value, list):
        return [text for child in value for text in _strings(child)]
    return []


def _timestamp(value: object) -> datetime.datetime | None:
    if not isinstance(value, str):
        return None
    try:
        result = datetime.datetime.fromisoformat(value)
    except ValueError:
        return None
    return result.astimezone(datetime.UTC) if result.tzinfo is not None else None


def normalize_path(value: str, roots: dict[str, str], worktree: pathlib.Path | None) -> str | None:
    """記録のrootとcache版を除いてリポジトリ相対パスへ揃える。"""
    value = value.replace("\\", "/")
    for root, plugin in roots.items():
        if value.startswith(root.rstrip("/") + "/"):
            return plugin + "/" + value[len(root.rstrip("/")) + 1 :]
    for plugin in ("agent-toolkit", "agent-basics"):
        match = re.search(rf"(?:^|/){plugin}/(?:(?:\d+\.)+\d+/)?((?:skills|share|rules|scripts|agent_toolkit)/.+)$", value)
        if match:
            return plugin + "/" + match[1]
    if worktree is not None:
        try:
            return pathlib.Path(value).relative_to(worktree).as_posix()
        except ValueError:
            pass
    return None


def _read_operands(executable: str, args: tuple[str, ...]) -> tuple[list[str], str | None]:
    """本文を出す読取の対象と、対象境界を決められない理由を返す。"""
    if not execution.prints_file_body(executable, args):
        return [], None
    if executable == "get-content":
        return execution.get_content_targets(args)
    return execution.command_file_inputs(executable, args), None


def _observations(records: list[Any], record_id: str) -> list[dict[str, Any]]:
    """元のツール入力とhook本文から適用位置を取り出す。要約出力を使用しない。"""
    observed: list[dict[str, Any]] = []
    by_line = {row.line: row.entry for row in records}
    read_commands = ("cat", "sed", "head", "tail", "less", "more", "awk", "gawk", "rg", "grep", "get-content")
    file_commands = (*read_commands, "python", "python3", "uv", "bash", "sh")
    for call in calls._record_tool_calls(records):  # pylint: disable=protected-access
        name = call.tool.rsplit("__", 1)[-1].rsplit(".", 1)[-1]
        inputs = calls._recorded_input(by_line[call.line], call.call_id)  # pylint: disable=protected-access
        source = {"record": record_id, "line": call.line}
        if name in ("Read", "read_file", "read"):
            target = inputs.get("file_path", inputs.get("filePath", inputs.get("path", inputs.get("Path"))))
            if isinstance(target, str):
                observed.append({**source, "path": target, "kind": "read"})
        elif name in ("Skill", "skill"):
            target = inputs.get("skill", inputs.get("name"))
            if isinstance(target, str):
                observed.append({**source, "path": target, "kind": "skill"})
        elif name in ("Bash", "bash", "shell", "exec_command", "exec", "CommandExecution", "run_command"):
            for operation in execution.command_input_observations(call.tool, call.text):
                command = operation["command"]
                if command is None:
                    observed.append({**source, "path": operation["input"], "reason": operation["reason"]})
                    continue
                for segment in execution.shell_simple_commands(command):
                    if not segment.parsed:
                        observed.append({**source, "path": command, "reason": "シェル区間の構文を静的に解析できない"})
                        continue
                    words = segment.words
                    if not words:
                        continue
                    executable = pathlib.PurePosixPath(words[0]).name.casefold()
                    # 静的な読取・起動の引数だけを対象とし、コマンドの説明文字列は探索しない。
                    if executable not in file_commands and not words[0].startswith("/"):
                        continue
                    operands, reason = _read_operands(executable, tuple(words[1:]))
                    if reason:
                        observed.append({**source, "path": command, "reason": reason})
                        continue
                    if words[0].startswith("/") and executable not in file_commands:
                        operands.append(words[0])
                    for word in operands:
                        if (
                            executable in read_commands
                            or word.startswith("/")
                            or "/" in word
                            or pathlib.PurePosixPath(word).suffix in (".py", ".sh", ".md")
                        ):
                            observed.append({**source, "path": word, "kind": "read-or-launch"})
    for row in records:
        for hook in hooks._hook_records(row.entry):  # pylint: disable=protected-access
            for body in hooks._hook_notice_bodies(hook):  # pylint: disable=protected-access
                if not any(word in body for word in ("未取得資料", "全文読", "全文取得", "読込指示")):
                    continue
                for match in _PATH.finditer(body):
                    value = match[1] or match[2]
                    if value.startswith("/"):
                        observed.append({"record": record_id, "line": row.line, "path": value, "kind": "hook-instruction"})
    return observed


def prepare_effect_candidates(
    scope_path: pathlib.Path,
    timeline: list[dict[str, Any]],
    worktree: pathlib.Path | None,
    output_dir: pathlib.Path,
    *,
    main_record_id: str,
) -> dict[str, object]:
    """通常・batch共通のprepareへ候補と効果専用の未確認ファイルを返す。"""
    unconfirmed: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    scope = json.loads(scope_path.read_text(encoding="utf-8"))
    main_id = next(
        (row["record"] for row in timeline if row.get("kind") == "record-provenance" and row.get("role") == "main"),
        main_record_id,
    )
    start = None
    observations: list[dict[str, Any]] = []
    roots: dict[str, str] = {}
    for record_id, entry in scope["records"].items():
        loaded = session_family._load_records_checked(entry["path"])  # pylint: disable=protected-access
        if loaded.records is None:
            unconfirmed.append({"record": record_id, "reason": "元記録を再取得できない"})
            continue
        records = [row for row in loaded.records if row.line <= entry["through_line"]]
        if record_id == main_id:
            times = [_timestamp(row.entry.get("timestamp")) for row in records]
            start = next((time for time in times if time is not None), None)
        for row in records:
            for match in _PLUGIN.finditer("\n".join(_strings(row.entry))):
                root = match[1]
                plugin = "agent-basics" if "agent-basics" in match[0].split(" plugin root:", 1)[0] else "agent-toolkit"
                roots[root] = plugin
        observations.extend(_observations(records, record_id))
    normalized: dict[str, list[dict[str, Any]]] = {}
    for item in observations:
        value = item["path"]
        if item.get("reason"):
            unconfirmed.append(item)
            continue
        if any(char in value for char in "$*?{}"):
            unconfirmed.append({**item, "reason": "読込・起動パスに動的な展開がある"})
            continue
        if item["kind"] == "skill" and re.fullmatch(r"(agent-toolkit|agent-basics):[\w-]+", value):
            plugin, name = value.split(":", 1)
            relative = f"{plugin}/skills/{name}/SKILL.md"
        else:
            relative = normalize_path(value, roots, worktree)
        if relative is None:
            unconfirmed.append({**item, "reason": "読込・起動パスに対応するリポジトリ相対パスを特定できない"})
        else:
            normalized.setdefault(relative, []).append(item)
    notes = uwi_scan.private_notes_root()
    if start is None:
        unconfirmed.append({"reason": "元セッションの開始日時がない"})
    elif notes is not None:
        for path, _repo_id, text, _state, _kind in entries.iter_entries(notes, ("adopted",), None, entry_type="awi"):
            parsed = frontmatter.parse_frontmatter(text)
            if parsed is None or "effect_check" in parsed[0]:
                continue
            times = effect_check.result_values(text, "処理日時")
            adopted = _timestamp(times[-1]) if times else None
            if adopted is None or not start - datetime.timedelta(days=14) <= adopted <= start:
                continue
            if worktree is None:
                unconfirmed.append({"awi": path.name, "reason": "対応commitの変更ファイルを確かめる対象worktreeがない"})
                continue
            try:
                if repo.resolve_repo_id(str(worktree)) != _repo_id:
                    continue
                changed = effect_check.changed_files(text, worktree)
                checked = effect_check.checked_files(text)
            except (ValueError, OSError, subprocess.SubprocessError) as error:
                unconfirmed.append({"awi": path.name, "reason": str(error)})
                continue
            for file in sorted(changed - checked.keys()):
                uses = normalized.get(file, [])
                rules = "/rules/" in f"/{file}"
                if uses or rules:
                    candidates.append(
                        {
                            "awi": path.name,
                            "awi_path": str(path),
                            "file": file,
                            "adopted_at": times[-1],
                            "uses": uses,
                            "always_rules": rules,
                        }
                    )
    paths: dict[str, object] = {}
    for label, items in (("candidates", candidates), ("unconfirmed", unconfirmed)):
        path = output_dir / f"effect-{label}.jsonl"
        path.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in items), encoding="utf-8")
        paths[f"effect_{label}_path"] = str(path)
        paths[f"effect_{label}_count"] = len(items)
    return paths
