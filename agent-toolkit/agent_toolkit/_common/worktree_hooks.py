"""対象プロジェクトのWorktreeCreate/Removeを同じ入力契約で実行する。"""

from __future__ import annotations

import json
import math
import os
import pathlib
import re
import subprocess
import typing
import urllib.request

from agent_toolkit._common import atomic_file, claude_settings_sources, process_tree


def _hooks(repo: pathlib.Path, event: str) -> list[dict[str, typing.Any]]:
    result = []
    environment = {}
    for path in claude_settings_sources.paths(repo):
        if not path.exists():
            continue
        settings = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(settings, dict):
            raise ValueError(f"hook設定はobjectで指定する: {path}")
        configured_env = settings.get("env", {})
        if not isinstance(configured_env, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in configured_env.items()
        ):
            raise ValueError(f"設定envは文字列のobjectで指定する: {path}")
        environment.update(configured_env)
        hooks = settings.get("hooks", {})
        if not isinstance(hooks, dict) or not isinstance(hooks.get(event, []), list):
            raise ValueError(f"hooksと{event}の設定形式が不正である: {path}")
        for group in hooks.get(event, []):
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                raise ValueError(f"{event}の登録はhooks配列を持つobjectで指定する: {path}")
            for hook in group["hooks"]:
                if not isinstance(hook, dict):
                    raise ValueError(f"{event}のhookはobjectで指定する: {path}")
                if hook.get("type") not in {"command", "http"} or hook.get("async"):
                    raise ValueError(f"{event}は同期commandまたはHTTP hookで指定する: {path}")
                result.append(hook)
    return [dict(hook, _environment=environment) for hook in result]


def configured(repo: pathlib.Path, event: str = "WorktreeCreate") -> bool:
    """対象の設定に同期作成・撤去hookがあるか返す。不正設定は拒否する。"""
    return bool(_hooks(repo, event))


def _execute(
    repo: pathlib.Path,
    event: str,
    session_id: str,
    timeout: float,
    diagnostics_directory: pathlib.Path,
    **fields: str,
) -> list[str]:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", session_id):
        raise ValueError("hook所有sessionは英数字・ハイフン・アンダースコアで指定する")
    payload = {"session_id": session_id, "transcript_path": "", "cwd": str(repo), "hook_event_name": event, **fields}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    output = []
    if not diagnostics_directory.is_absolute():
        raise ValueError("hook診断の保存先は呼出側が所有する絶対パスで指定する")
    diagnostics_directory.mkdir(parents=True, exist_ok=True)
    for index, hook in enumerate(_hooks(repo, event)):
        record: dict[str, typing.Any] = {"input": payload, "type": hook["type"]}
        path = diagnostics_directory / f"{event}-{index}.json"
        try:
            # hook定義のtimeoutはhook自身の上限であり、呼出側の値は定義が無いhookだけに使う。
            seconds = float(hook["timeout"]) if "timeout" in hook else timeout
            if not math.isfinite(seconds) or seconds <= 0:
                raise ValueError("hookのtimeoutは有限の正の値で指定する")
            if hook["type"] == "command":
                argv = ["cmd", "/d", "/s", "/c", hook["command"]] if os.name == "nt" else ["sh", "-c", hook["command"]]
                env = {**os.environ, **hook["_environment"], "CLAUDE_PROJECT_DIR": str(repo)}
                completed = process_tree.run_with_timeout(
                    argv, cwd=repo, env=env, input=body, capture_output=True, timeout=seconds, check=False
                )
                stdout = completed.stdout.decode("utf-8", errors="replace")
                record.update(
                    exit_code=completed.returncode, stdout=stdout, stderr=completed.stderr.decode("utf-8", errors="replace")
                )
                if completed.returncode:
                    raise ValueError(f"{event} hookが終了{completed.returncode}: {path}")
                clean = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", stdout).strip()
                value = clean.splitlines()[-1] if clean else ""
            else:
                headers = {"Content-Type": "application/json", **hook.get("headers", {})}
                for key, value in headers.items():
                    for name in hook.get("allowedEnvVars", []):
                        value = value.replace("${" + name + "}", hook["_environment"].get(name, os.environ.get(name, "")))
                    headers[key] = value
                request = urllib.request.Request(hook["url"], data=body, headers=headers, method="POST")
                with urllib.request.urlopen(request, timeout=seconds) as response:
                    stdout = response.read().decode("utf-8")
                record.update(exit_code=0, stdout=stdout, stderr="")
                decoded = json.loads(stdout) if stdout.strip() else {}
                if not isinstance(decoded, dict):
                    raise ValueError("HTTP hookの応答はJSON objectで指定する")
                specific = decoded.get("hookSpecificOutput", {})
                if not isinstance(specific, dict):
                    raise ValueError("HTTP hookのhookSpecificOutputはJSON objectで指定する")
                value = specific.get("worktreePath", "") if specific.get("hookEventName") == event else ""
                if not isinstance(value, str):
                    raise ValueError("HTTP hookのworktreePathは文字列で指定する")
            output.append(value)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            record["error"] = str(error)
            raise ValueError(f"{event} hookが失敗した。途中資源を保持した: {path}: {error}") from error
        finally:
            atomic_file.atomic_write(path, json.dumps(record, ensure_ascii=False) + "\n")
    return output


def create(
    repo: pathlib.Path,
    name: str,
    session_id: str,
    timeout: float = 600,
    *,
    diagnostics_directory: pathlib.Path,
) -> pathlib.Path:
    """WorktreeCreate hookが返した作成先を解決する。hookが失敗してもGitによる作成へ切り替えない。"""
    values = _execute(repo, "WorktreeCreate", session_id, timeout, diagnostics_directory, name=name)
    if not values or not values[-1]:
        raise ValueError("WorktreeCreate hookが作成先を返さなかった")
    path = pathlib.Path(values[-1])
    if path.is_absolute() and any(part in {".", ".."} for part in values[-1].replace("\\", "/").split("/")):
        raise ValueError(f"WorktreeCreate hookの絶対作成先が正規化されていない: {path}")
    if not path.is_absolute():
        path = repo / path
    if path.is_relative_to(repo):
        current = repo
        for part in path.relative_to(repo).parts:
            current /= part
            if current.is_symlink():
                raise ValueError(f"WorktreeCreate hookの作成先がrepo内のsymlinkを経由している: {current}")
    if not path.is_dir():
        raise ValueError(f"WorktreeCreate hookの返却先がディレクトリではない: {path}")
    return path.resolve()


def remove(
    repo: pathlib.Path,
    path: pathlib.Path,
    session_id: str,
    timeout: float = 600,
    *,
    diagnostics_directory: pathlib.Path,
) -> None:
    """WorktreeRemove hookによる撤去の完了を確認する。hookが非0で終了しても作成先が無ければ成功とし、診断は保存する。"""
    if not path.exists():
        return
    try:
        _execute(repo, "WorktreeRemove", session_id, timeout, diagnostics_directory, worktree_path=str(path))
    except ValueError:
        if path.exists():
            raise
    if path.exists():
        raise ValueError(f"WorktreeRemove hookの後も作成先が残っている: {path}")
