"""明示された現在時刻と、同じ呼出主体の時刻観測を比較する。"""

from __future__ import annotations

import datetime
import pathlib
import re
from collections.abc import Iterable

from agent_toolkit._common import shell_cwd
from agent_toolkit._common.bash_invocations import is_unknown_word
from agent_toolkit._common.session_state import read_state, update_state
from agent_toolkit._common.shell_tokens import is_atk_command
from agent_toolkit._hooks.agent_id import resolve_hook_agent_id
from agent_toolkit._hooks.full_reads import response_text
from agent_toolkit._hooks.pretooluse.notices import _llm_notice

_STATE = "recent_clock_observation"
_CLOCK = re.compile(r"date (?:-I(?:seconds|ns)|--iso-8601=(?:seconds|ns))")
_ISO = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:[.,]\d{1,9})?(?:Z|[+-]\d\d:\d\d)")
_MASKED = re.compile(
    r"(?<![0-9A-Za-z_])(?:(?:[01]?\d|2[0-3]):(?:[0-5][xX]|[xX][0-9xX])"
    r"|(?:[01][xX]|2[xX]|[xX][0-9xX]):(?:[0-5][0-9]|[0-5][xX]|[xX][0-9xX]))(?![0-9A-Za-z_])"
)
_MINUTES = re.compile(r"(2[0-3]|[01]?\d)(?::|時)([0-5]\d)分?")
_CLAIM = re.compile(r"^(?:現在時刻|dateで確認)[:：]\s*(.+)$")


def observe(payload: dict, command: object, session_id: str) -> None:
    if not isinstance(command, str) or _CLOCK.fullmatch(command.strip()) is None:
        return
    if payload.get("hook_event_name") == "PostToolUseFailure" or payload.get("is_error"):
        return
    text = response_text(payload.get("tool_response")).strip()
    if _ISO.fullmatch(text) is None:
        return
    actor = resolve_hook_agent_id(payload)

    def save(state: dict) -> dict:
        existing = state.get(_STATE)
        return {**state, _STATE: {**(existing if isinstance(existing, dict) else {}), actor: text}}

    update_state(session_id, save)


def _visible_lines(text: str) -> Iterable[str]:
    fenced = ""
    for line in text.splitlines():
        line = line.strip()
        marker = re.match(r"^`{3,}|^~{3,}", line)
        if marker:
            if not fenced:
                fenced = marker[0]
            elif marker[0].startswith(fenced):
                fenced = ""
        elif not fenced and not line.startswith(">"):
            yield re.sub(r"`[^`\n]+`", "", line)


def warning(texts: Iterable[str], payload: dict | None = None, *, skip_masked: bool = False) -> str | None:
    for text in texts:
        for line in _visible_lines(text):
            if not skip_masked and _MASKED.search(line):
                return _llm_notice(
                    "時刻の一部が伏せ字になっている。観測値か推定と分かる時間帯を示す。",
                    tag="warn",
                    removable_cause=False,
                    fix="根拠とする時計の出力を確かめてください。",
                )
            match = _CLAIM.fullmatch(line)
            if not match or payload is None:
                continue
            value = match[1].rstrip("。")
            short = _MINUTES.fullmatch(value)
            if short is None and _ISO.fullmatch(value) is None:
                continue
            session_id = payload.get("session_id")
            if not isinstance(session_id, str) or not session_id:
                continue
            observations = read_state(session_id).get(_STATE)
            observed = observations.get(resolve_hook_agent_id(payload)) if isinstance(observations, dict) else None
            matches_clock = observed == value
            if short is not None and isinstance(observed, str):
                matches_clock = observed[11:16] == f"{int(short[1]):02d}:{short[2]}"
            if line.startswith("dateで確認") and not matches_clock:
                return _llm_notice(
                    "同じ呼出主体の対応する日時取得結果を確認できない。観測した出力を確かめる。",
                    tag="warn",
                    removable_cause=False,
                    fix="根拠とする時計の出力を確かめてください。",
                )
            now = datetime.datetime.now(datetime.UTC)
            try:
                if short is None:
                    delta = abs((now - datetime.datetime.fromisoformat(value)).total_seconds())
                elif line.startswith("dateで確認") and isinstance(observed, str):
                    delta = abs((now - datetime.datetime.fromisoformat(observed)).total_seconds())
                else:
                    local_now = now.astimezone()
                    current_minute = local_now.hour * 60 + local_now.minute
                    caption_minute = int(short[1]) * 60 + int(short[2])
                    minutes = abs(current_minute - caption_minute)
                    delta = min(minutes, 1440 - minutes) * 60
            except ValueError:
                continue
            if delta > 120:
                return _llm_notice(
                    "明示された現在時刻が時計の値と離れている。取得し直して時点を確認する。",
                    tag="warn",
                    removable_cause=False,
                    fix="根拠とする時計の出力を確かめてください。",
                )
    return None


def wi_bodies(command: str, cwd: str) -> Iterable[str]:
    if "wi" not in command or "--body-file" not in command:
        return
    for invocation, current in shell_cwd.bash_invocation_cwds(command, cwd):
        tokens = invocation.segment.tokens
        if not current.resolved or not is_atk_command(tokens) or tuple(tokens[1:3]) not in {("wi", "add"), ("wi", "edit")}:
            continue
        args = iter(tokens[3:])
        for token in args:
            if token == "--":
                break
            flag, separator, value = token.partition("=")
            if flag != "--body-file":
                continue
            path = value if separator else next(args, "")
            if not path or is_unknown_word(path):
                continue
            try:
                source = pathlib.Path(current.path, path)
                if source.is_file() and source.stat().st_size <= 262_144:
                    yield source.read_text(encoding="utf-8")
            except OSError, ValueError:
                continue
