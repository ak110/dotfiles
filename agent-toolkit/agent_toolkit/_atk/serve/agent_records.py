import pathlib
from collections.abc import Iterable

from agent_toolkit._atk import session_record_format


def agent_ids(roots: list[dict]) -> set[str]:
    return {row["id"] for root in roots for row in (root, *root["children"])}


def locate_agent_records(
    claude_home: pathlib.Path,
    codex_home: pathlib.Path,
    requested: Iterable[str],
) -> dict[str, list[dict[str, str]]]:
    wanted = set(requested)
    records: dict[str, list[dict[str, str]]] = {}
    if not wanted:
        return records

    def add(engine: str, session_id: str, path: pathlib.Path) -> None:
        if session_id in wanted:
            records.setdefault(session_id, []).append({"engine": engine, "path": str(path)})

    for path in session_record_format.claude_session_records(claude_home):
        add("claude", path.stem, path)
        for child_id, child_path, _ in session_record_format.claude_subagent_records(path):
            add("claude", child_id, child_path)
    for path in session_record_format.codex_session_records(codex_home):
        add("codex", session_record_format.codex_session_id(path), path)
    return records
