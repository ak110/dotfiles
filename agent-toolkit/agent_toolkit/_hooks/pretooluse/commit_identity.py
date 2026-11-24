from __future__ import annotations

import json
import os
import pathlib
from collections.abc import Iterable

from agent_toolkit._common import host_homes as _host_homes
from agent_toolkit._common import transcript as _transcript
from agent_toolkit._common.runtime_identity import RuntimeIdentity, co_author_trailer, identity_observations
from agent_toolkit._hooks.host import is_antigravity_payload
from agent_toolkit._hooks.pretooluse.notices import _llm_notice


def _settings_commit_attribution(path: pathlib.Path) -> tuple[bool, str | None]:
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
    except OSError, UnicodeError, json.JSONDecodeError, ValueError:
        return False, None
    if not isinstance(settings, dict):
        return False, None
    attribution = settings.get("attribution")
    if not isinstance(attribution, dict) or not isinstance(attribution.get("commit"), str):
        return False, None
    return True, attribution["commit"]


def claude_commit_attribution_disabled(cwd: str) -> bool:
    config_dir = _host_homes.claude_config_dir()
    project_raw = os.environ.get("CLAUDE_PROJECT_DIR") or cwd
    paths = [config_dir / "settings.json"]
    if project_raw:
        project_dir = pathlib.Path(project_raw)
        paths.extend((project_dir / ".claude" / "settings.json", project_dir / ".claude" / "settings.local.json"))
    effective: str | None = None
    defined = False
    for path in paths:
        current_defined, current = _settings_commit_attribution(path)
        if current_defined:
            defined = True
            effective = current
    return defined and effective == ""


_COMMIT_MESSAGE_RULES = "/skills/atk-mode/references/commit-message.md"


def reads_commit_message_rules(paths: Iterable[pathlib.Path]) -> bool:
    return any(path.as_posix().endswith(_COMMIT_MESSAGE_RULES) and path.is_file() for path in paths)


def commit_trailer_notice(payload: dict, cwd: str, *, is_codex: bool) -> str | None:
    trailer = observed_commit_trailer(payload, cwd, is_codex=is_codex)
    if trailer is None:
        return None
    return _llm_notice(
        f"読取時点に観測したモデルと推論量の帰属行: {trailer}。通常のコミットのメッセージへこの1行を入れてください。"
        "明示指定と無効化の方針は`agent-toolkit:atk-mode/references/commit-message.md`「`Co-Authored-By:`」で確かめ、"
        "通常のコミットの直前にも現在の観測を確かめてください。",
        tag="notice",
    )


def observed_commit_trailer(payload: dict, cwd: str, *, is_codex: bool) -> str | None:
    if not is_codex and claude_commit_attribution_disabled(cwd):
        return None
    identity = hook_observed_identity(payload, is_codex=is_codex)
    if identity is None or identity.source != "observed":
        return None
    return co_author_trailer(identity)


def hook_observed_identity(payload: dict, *, is_codex: bool) -> RuntimeIdentity | None:
    # モデルを切り替えて戻す場合に備え、初出順でなく最後の観測値を使う。  # pyfltr: allow-comment
    runtime = "agy" if is_antigravity_payload(payload) else "codex" if is_codex else "claude"
    model = payload.get("model")
    effort_input = payload.get("effort")
    effort = effort_input.get("level") if isinstance(effort_input, dict) else effort_input or payload.get("reasoning_effort")
    if isinstance(model, str) and model and isinstance(effort, str) and effort:
        return RuntimeIdentity(runtime, model, effort, "observed")
    record_path = _hook_record_path(payload)
    if record_path is None:
        return None
    lines = _transcript.read_transcript_lines(record_path)
    if lines is None:
        return None
    entries: list[dict] = []
    for raw in lines:
        if not raw.strip():
            continue
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    observations = identity_observations(entries, runtime)
    if not observations:
        return None
    latest = observations[-1][1]
    if not is_codex and isinstance(effort, str) and effort:
        return RuntimeIdentity(runtime, latest.model, effort, "observed")
    return latest


def _hook_record_path(payload: dict) -> str | None:
    transcript_path = payload.get("transcript_path")
    if not isinstance(transcript_path, str) or not transcript_path:
        return None
    agent_id = payload.get("agent_id")
    if agent_id is None:
        return transcript_path
    session_id = payload.get("session_id")
    if not isinstance(agent_id, str) or not agent_id or not isinstance(session_id, str) or not session_id:
        return None
    subagent_path = pathlib.Path(transcript_path).parent / session_id / "subagents" / f"agent-{agent_id}.jsonl"
    return str(subagent_path) if subagent_path.is_file() else None
