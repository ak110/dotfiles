"""構造化診断を保存時の版・条件・本文へ対応付け、多重集合の差を返す。"""

from __future__ import annotations

import collections
import difflib
import json
import pathlib
import re
from typing import Any

from agent_toolkit._git import command as git_command


def path_value(value: object) -> pathlib.Path:
    """外部入力の保存ファイルを絶対パスとして検証する。"""
    if not isinstance(value, str) or not pathlib.Path(value).is_absolute():
        raise ValueError(f"実在する通常ファイルの絶対パスが必要です: {value!r}")
    path = pathlib.Path(value)
    if not path.is_file():
        raise ValueError(f"保存ファイルがありません: {path}")
    return path


def run_record(value: object) -> tuple[pathlib.Path, dict[str, Any]]:
    """診断と試験に共通する終了状態・実行版・両出力を確認する。"""
    path = path_value(value)
    record = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(record, dict):
        raise ValueError(f"実行記録はオブジェクトが必要です: {path}")
    if not isinstance(record.get("cwd"), str) or not pathlib.Path(record["cwd"]).is_absolute():
        raise ValueError(f"実行cwdが不足しています: {path}")
    if not isinstance(record.get("argv"), list) or not record["argv"] or any(not isinstance(v, str) for v in record["argv"]):
        raise ValueError(f"実行argvが不足しています: {path}")
    if not isinstance(record.get("git_head"), str) or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", record["git_head"]) is None:
        raise ValueError(f"取得版の完全OIDが不足しています: {path}")
    if not isinstance(record.get("git_status"), list) or any(not isinstance(v, str) for v in record["git_status"]):
        raise ValueError(f"作業ツリー状態が不足しています: {path}")
    if (
        not isinstance(record.get("child_exit_code"), int)
        or isinstance(record["child_exit_code"], bool)
        or record.get("timed_out") is not False
        or "signal" not in record
        or record["signal"] is not None
    ):
        raise ValueError(f"子の終了状態を確定できません: {path}")
    for stream in ("stdout_path", "stderr_path"):
        path_value(record.get(stream))
    return path, record


def _load(spec: object) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not isinstance(spec, dict) or set(spec) != {"path", "run_record", "head", "conditions", "sources"}:
        raise ValueError("診断の組にはpath・run_record・head・conditions・sourcesを指定する")
    path = path_value(spec["path"])
    record_path, record = run_record(spec["run_record"])
    if spec["head"] != record["git_head"]:
        raise ValueError(f"診断と実行記録の版が一致しません: {path}, {record_path}")
    conditions = spec["conditions"]
    if not isinstance(conditions, dict) or set(conditions) != {
        "scope",
        "options",
        "dependencies",
        "parallelism",
        "environment",
    }:
        raise ValueError("診断conditionsにはscope・options・dependencies・parallelism・environmentを全て指定する")
    if any(value is None for value in conditions.values()):
        raise ValueError("欠けた診断条件を一致と扱えません")
    if (
        any(
            not isinstance(conditions[key], list) or any(not isinstance(v, str) for v in conditions[key])
            for key in ("scope", "options")
        )
        or any(not isinstance(conditions[key], dict) for key in ("dependencies", "environment"))
        or not isinstance(conditions["parallelism"], int)
        or isinstance(conditions["parallelism"], bool)
        or conditions["parallelism"] < 1
    ):
        raise ValueError(
            "診断条件はscope・optionsの文字列配列、dependencies・environmentのオブジェクトと正のparallelismを指定する"
        )
    if not isinstance(spec["sources"], dict) or any(not isinstance(key, str) for key in spec["sources"]):
        raise ValueError("sourcesは相対ファイルから保存時本文の絶対パスへのオブジェクトが必要です")
    sources = {name: path_value(value).read_text(encoding="utf-8") for name, value in spec["sources"].items()}
    text = path.read_text(encoding="utf-8")
    try:
        decoded = json.loads(text)
        if not isinstance(decoded, (dict, list)):
            raise ValueError(f"構造化診断のオブジェクトか配列が必要です: {path}")
        entries = decoded if isinstance(decoded, list) else decoded.get("diagnostics", [decoded])
    except json.JSONDecodeError:
        entries = [json.loads(line) for line in text.splitlines() if line.strip()]
    if not isinstance(entries, list):
        raise ValueError(f"構造化診断配列が必要です: {path}")
    diagnostics = []
    for position, entry in enumerate(entries, 1):
        if not isinstance(entry, dict):
            raise ValueError(f"構造化診断の行が不正です: {path}: {position}")
        if entry.get("kind") == "command":
            continue
        if entry.get("kind") not in {None, "diagnostic"}:
            raise ValueError(f"未知の構造化診断種別です: {path}: {position}")
        messages = entry.get("messages")
        if not isinstance(messages, list):
            raise ValueError(f"構造化診断のmessagesが不足しています: {path}: {position}")
        for message_position, message in enumerate(messages, 1):
            if not isinstance(message, dict) or not isinstance(message.get("msg"), str):
                raise ValueError(f"診断本文が不足しています: {path}: {position}")
            if any(
                value is not None and not isinstance(value, str)
                for value in (entry.get("command"), message.get("severity"), message.get("rule"))
            ):
                raise ValueError("診断command・severity・ruleは文字列かnullを指定する")
            filename = entry.get("file")
            if filename is not None and not isinstance(filename, str):
                raise ValueError("診断fileは文字列かnullを指定する")
            if filename:
                file_path = pathlib.Path(filename)
                if file_path.is_absolute():
                    try:
                        filename = file_path.relative_to(record["cwd"]).as_posix()
                    except ValueError as error:
                        raise ValueError(f"診断fileが実行cwdの外です: {filename}") from error
                if ".." in pathlib.Path(filename).parts:
                    raise ValueError(f"診断fileが実行cwdの外です: {filename}")
            line = message.get("line")
            if line is not None and (not isinstance(line, int) or isinstance(line, bool) or line < 1):
                raise ValueError("診断lineは正の整数かnullを指定する")
            diagnostics.append(
                {
                    "file": filename or None,
                    "line": line,
                    "col": message.get("col"),
                    "emitter": entry.get("command"),
                    "type": message.get("severity"),
                    "rule": message.get("rule"),
                    "message": message["msg"],
                    "path": str(path),
                    "entry": position,
                    "message_position": message_position,
                    "run_record": str(record_path),
                    "git_head": record["git_head"],
                    "git_status": record["git_status"],
                }
            )
    return {**spec, "sources_text": sources, "record": record}, diagnostics


def _git(cwd: str, *args: str) -> str:
    result = git_command.run(["-C", cwd, *args], capture_output=True, text=True, check=False, timeout=30)
    if result.returncode:
        raise ValueError(f"診断の版を対応付けられません: git {' '.join(args)}: {result.returncode}: {result.stderr.strip()}")
    return result.stdout


def _renames(before: dict[str, Any], after: dict[str, Any]) -> dict[str, str]:
    cwd = after["record"]["cwd"]
    for spec in (before, after):
        _git(cwd, "rev-parse", "--verify", "--end-of-options", spec["head"] + "^{commit}")
    output = _git(cwd, "diff", "--name-status", "-z", "--find-renames", before["head"], after["head"], "--")
    fields = output.split("\0")
    mappings: dict[str, str] = {}
    index = 0
    while index < len(fields) and fields[index]:
        status = fields[index]
        if status.startswith(("R", "C")):
            mappings[fields[index + 1]] = fields[index + 2]
            index += 3
        else:
            index += 2
    return mappings


def _line_map(before: str, after: str) -> dict[int, int]:
    """内容がそのまま残る行だけを対応付け、変更行を同じ位置とみなさない。"""
    mapping: dict[int, int] = {}
    matcher = difflib.SequenceMatcher(a=before.splitlines(), b=after.splitlines(), autojunk=False)
    for old, new, size in matcher.get_matching_blocks():
        mapping.update((old + offset + 1, new + offset + 1) for offset in range(size))
    return mapping


def _key(diagnostic: dict[str, Any], spec: dict[str, Any], *, file: str | None = None, line: int | None = None) -> tuple:
    filename = file if file is not None else diagnostic["file"]
    position = line if line is not None else diagnostic["line"]
    message = diagnostic["message"]
    if filename is None:
        message = message.replace(spec["record"]["cwd"], "<worktree>")
        message = re.sub(r"\b(?:PID|pid)[=: ]+\d+\b", "PID=<実行値>", message)
    return filename, position, diagnostic["rule"], message, diagnostic["emitter"], diagnostic["type"]


def compare(current: object, baseline: object | None) -> dict[str, Any]:
    """比較不能を独立した選択肢として返し、一致の根拠へ混ぜない。"""
    after, new = _load(current)
    result: dict[str, Any] = {}
    if baseline is None:
        return {f"observed:{index}": {"change": "observed", "after": item} for index, item in enumerate(new, 1)}
    before, old = _load(baseline)
    if before["conditions"] != after["conditions"]:
        return {
            "incomparable:1": {
                "change": "incomparable",
                "reason": "実行条件が一致しない",
                "before": before["path"],
                "after": after["path"],
            }
        }
    try:
        renames = _renames(before, after)
    except ValueError as error:
        return {
            "incomparable:1": {"change": "incomparable", "reason": str(error), "before": before["path"], "after": after["path"]}
        }
    buckets: dict[tuple, list[dict[str, Any]]] = collections.defaultdict(list)
    maps: dict[str, dict[int, int]] = {}
    incomparable: list[dict[str, Any]] = []
    for item in old:
        filename = item["file"]
        renamed = renames.get(filename, filename)
        if filename is not None:
            if filename not in before["sources_text"] or renamed not in after["sources_text"]:
                incomparable.append({"change": "incomparable", "reason": "保存時本文が不足している", "before": item})
                continue
            maps.setdefault(filename, _line_map(before["sources_text"][filename], after["sources_text"][renamed]))
            position = maps[filename].get(item["line"]) if item["line"] is not None else None
            if item["line"] is not None and position is None:
                key = ("changed-old-line", filename, item["line"], item["rule"], item["message"])
            else:
                key = _key(item, before, file=renamed, line=position)
        else:
            key = _key(item, before)
        buckets[key].append(item)
    changes: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    changes["incomparable"].extend(incomparable)
    unknown_files = {renames.get(item["before"]["file"], item["before"]["file"]) for item in incomparable}
    for item in new:
        if item["file"] in unknown_files or item["file"] is not None and item["file"] not in after["sources_text"]:
            changes["incomparable"].append({"change": "incomparable", "reason": "保存時本文が不足している", "after": item})
            continue
        key = _key(item, after)
        matches = buckets.get(key)
        if matches:
            changes["unchanged"].append({"change": "unchanged", "before": matches.pop(), "after": item})
        else:
            changes["added"].append({"change": "added", "after": item})
    for matches in buckets.values():
        changes["deleted"].extend({"change": "deleted", "before": item} for item in matches)
    for change, items in changes.items():
        result.update((f"{change}:{index}", item) for index, item in enumerate(items, 1))
    return result
