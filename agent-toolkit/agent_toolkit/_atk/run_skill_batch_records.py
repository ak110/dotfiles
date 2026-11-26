from __future__ import annotations

import pathlib
from typing import Any

from agent_toolkit._common import session_record_paths


def _registered_codex_home(target: dict[str, Any]) -> pathlib.Path | None:
    raw = target.get("transcript_path")
    if target["host"] != "codex" or not isinstance(raw, str):
        return None
    path = pathlib.Path(raw)
    if not (path.is_absolute() and path.name.startswith("rollout-") and path.name.endswith(f"-{target['session_id']}.jsonl")):
        return None
    if path.parent.name == "archived_sessions":
        return path.parent.parent
    if len(path.parents) > 4 and path.parents[3].name == "sessions":
        return path.parents[4]
    return None


def lacks_record(target: dict[str, Any]) -> bool:
    if target["host"] not in {"claude", "codex"} or target.get("lane_parent_session_id"):
        return False
    path = target.get("transcript_path")
    if not isinstance(path, str) or not path:
        return False
    try:
        pathlib.Path(path).lstat()
    except FileNotFoundError:
        if target["host"] == "claude":
            return True
        home = _registered_codex_home(target)
        return not (
            (home is not None and session_record_paths.find_codex_records(target["session_id"], codex_home=home))
            or session_record_paths.find_codex_records(target["session_id"])
        )
    except OSError:
        # 読取権限や形式の問題は、不在として待ちを消さずprepareの診断へ渡す。  # pyfltr: allow-comment
        return False
    return False


def waiting_for_rollout(target: dict[str, Any]) -> bool:
    return (
        target["host"] == "codex"
        and not target.get("transcript_path")
        and not session_record_paths.find_codex_records(target["session_id"])
    )


def prepare_arguments(target: dict[str, Any]) -> list[str]:
    arguments = (
        ["--agents-session-id", target["session_id"]]
        if target.get("lane_parent_session_id")
        else ["--transcript", target["transcript_path"]]
        if target["host"] == "claude"
        else ["--codex-thread-id", target["session_id"]]
    )
    if (home := _registered_codex_home(target)) is not None:
        try:
            pathlib.Path(target["transcript_path"]).lstat()
        except FileNotFoundError:
            if not session_record_paths.find_codex_records(target["session_id"], codex_home=home):
                home = None
        except OSError:
            pass
        if home is not None:
            arguments.extend(["--codex-home", str(home)])
    return arguments
