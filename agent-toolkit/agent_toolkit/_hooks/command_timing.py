"""シェル実行の全体区間を観測し、長い完了が続いた区間の見直しを促す。

Codexのwall_time_secondsは各pollの待機時間なので全体時間へ使わない。
Claudeのduration_msは公式HooksのPostToolUse/Failure inputが定める実行時間を使う。
背景通知までの区間は通知配送の遅延も含む観測経過時間である。
"""

from __future__ import annotations

import math
import re
import time

from agent_toolkit._common.session_state import read_state, update_state
from agent_toolkit._common.shell_tokens import ATK_SHELL_TOOLS, is_agents_wait_command, is_atk_command, split_words
from agent_toolkit._hooks.agent_id import resolve_hook_agent_id
from agent_toolkit._hooks.host import is_codex_payload
from agent_toolkit._hooks.notice import formatter

STATE_KEY = "command_timing"
_notice = formatter("command-timing")
_NOTIFICATIONS = re.compile(r"<task-notification>.*?</task-notification>", re.DOTALL)
_SHELL_SYNTAX = frozenset(";&|<>(){}*?[]#~!")
_POWERSHELL_LITERAL_WORDS = re.compile(r"[-A-Za-z0-9_./:= \t]+")


def _wait_only(tool_input: object, *, powershell: bool) -> bool:
    """既知の待機を直接1回起動すると確定できる場合だけ集計から除く。"""
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str) or ("wait" not in command and "watch" not in command):
        return False
    if "\n" in command or "\r" in command:
        return False
    if powershell:
        # PowerShellの引用・展開はBashの分割器で推測しない。
        return _POWERSHELL_LITERAL_WORDS.fullmatch(command) is not None and _wait_program(tuple(command.split()))
    try:
        words = split_words(command)
    except ValueError:
        return False
    if any(word.expansion or not _SHELL_SYNTAX.isdisjoint(word.unquoted) for word in words):
        return False
    return _wait_program(tuple(words))


def _wait_program(tokens: tuple[str, ...]) -> bool:
    if not tokens or "-h" in tokens or "--help" in tokens:
        return False
    if is_agents_wait_command(tokens):
        return True
    if is_atk_command(tokens) and tokens[1:3] == ("run-script", "wait-ci"):
        options = {token.split("=", 1)[0] for token in tokens[3:]}
        return "--write-baseline" not in options and bool(options & {"--baseline", "--wait-sha"})
    name = tokens[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
    return name in {"gh", "gh.exe"} and _gh_wait(tokens[1:])


def _gh_wait(subcommands: tuple[str, ...]) -> bool:
    """サブコマンドより前のリポジトリ指定を除き、待機を要求する実行形を比べる。"""
    while subcommands:
        option = subcommands[0]
        if option in {"-R", "--repo"}:
            if len(subcommands) < 2:
                return False
            subcommands = subcommands[2:]
        elif option.startswith(("--repo=", "-R")):
            subcommands = subcommands[1:]
        else:
            break
    operation = subcommands[:2]
    if operation == ("run", "watch"):
        return True
    if operation != ("pr", "checks"):
        return False
    arguments = subcommands[2:]
    if "--" in arguments:
        arguments = arguments[: arguments.index("--")]
    for word in reversed(arguments):
        key, assignment, value = word.partition("=")
        if key == "--watch":
            return not assignment or value.lower() in {"true", "t", "1"}
    return False


def start(payload: dict) -> None:
    session = payload.get("session_id")
    identifier = payload.get("tool_use_id")
    if payload.get("tool_name") not in ATK_SHELL_TOOLS or not isinstance(session, str) or not session:
        return
    if not isinstance(identifier, str) or not identifier:
        return
    actor = resolve_hook_agent_id(payload)
    now = time.time()
    wait_only = _wait_only(payload.get("tool_input"), powershell=payload.get("tool_name") == "PowerShell")

    def record(state: dict) -> dict:
        actors = state.setdefault(STATE_KEY, {})
        current = actors.setdefault(actor, {"calls": {}, "recent": [], "kind": "", "notified": False})
        calls = current["calls"]
        calls.setdefault(identifier, {"started": now, "wait_only": wait_only})
        return state

    update_state(session, record)


def observe(payload: dict, *, background_task_id: str | None = None) -> list[str]:
    if payload.get("tool_name") not in ATK_SHELL_TOOLS:
        return []
    identifier = payload.get("tool_use_id")
    session = payload.get("session_id")
    if not isinstance(session, str) or not session or not isinstance(identifier, str) or not identifier:
        return []
    response = payload.get("tool_response")
    if background_task_id:
        actor = resolve_hook_agent_id(payload)

        def retain(state: dict) -> dict | None:
            call = state.get(STATE_KEY, {}).get(actor, {}).get("calls", {}).get(identifier)
            if not isinstance(call, dict) or call.get("done"):
                return None
            call["task_id"] = background_task_id
            return state

        update_state(session, retain)
        return []
    if isinstance(response, dict) and response.get("session_id") is not None and response.get("exit_code") is None:
        return []
    milliseconds = payload.get("duration_ms") if not is_codex_payload(payload) else None
    duration = None
    if isinstance(milliseconds, (float, int)) and not isinstance(milliseconds, bool):
        try:
            seconds = float(milliseconds) / 1000
        except OverflowError:
            seconds = math.nan
        if math.isfinite(seconds) and seconds >= 0:
            duration = seconds
    return _complete(session, resolve_hook_agent_id(payload), identifier, duration)


def observe_notifications(payload: dict) -> list[str]:
    """Claudeの背景完了通知を、起動時に保持した所有記録へ対応付ける。"""
    session = payload.get("session_id")
    prompt = payload.get("prompt")
    if not isinstance(session, str) or not session or not isinstance(prompt, str) or is_codex_payload(payload):
        return []
    notices: list[str] = []
    for notification in _NOTIFICATIONS.findall(prompt):
        status = re.search(r"<status>([^<]+)</status>", notification)
        task = re.search(r"<task-id>([^<]+)</task-id>", notification)
        if not status or status[1] not in {"completed", "failed", "cancelled", "killed"} or not task:
            continue

        owned = [
            (actor, identifier)
            for actor, current in read_state(session).get(STATE_KEY, {}).items()
            for identifier, call in current.get("calls", {}).items()
            if call.get("task_id") == task[1] and not call.get("done")
        ]
        for actor, identifier in owned:
            notices.extend(_complete(session, actor, identifier, None))
    return notices


REVIEW_SECONDS = 10
"""同じ計測種別で3件続いた時に見直しを促す、各完了の下限（2026年11月16日のユーザー回答）。"""


def _complete(session: str, actor: str, identifier: str, duration: float | None) -> list[str]:
    now = time.time()
    result: list[tuple[str, list[float]]] = []

    def finish(state: dict) -> dict | None:
        current = state.setdefault(STATE_KEY, {}).setdefault(actor, {"calls": {}, "recent": [], "kind": "", "notified": False})
        calls = current["calls"]
        call = calls.setdefault(identifier, {})
        if call.get("done"):
            return None
        started = call.get("started")
        seconds = duration if isinstance(started, (float, int)) else None
        kind = "実行時間"
        if seconds is None:
            seconds = now - started if isinstance(started, (float, int)) and now >= started else None
            kind = "観測経過時間（権限待ち等を含み得る）"
        wait_only = call.get("wait_only") is True
        call.clear()
        call["done"] = True
        review = []
        if not wait_only:
            slow = seconds is not None and seconds >= REVIEW_SECONDS
            if current["kind"] != kind or not slow:
                current.update({"kind": kind, "recent": [], "notified": False})
            if slow:
                current["recent"] = [*current["recent"], seconds][-3:]
            if len(current["recent"]) == 3 and not current["notified"]:
                current["notified"] = True
                review = list(current["recent"])
        if review:
            result.append((kind, review))
        return state

    update_state(session, finish)
    return [
        _notice(
            f"同じ計測種別で{REVIEW_SECONDS}秒以上の完了が3件続いた。{kind}: "
            + "、".join(f"{value:.1f}秒" for value in review)
            + "。",
            tag="warn",
            removable_cause=False,
            subject=f"{actor}:{identifier}:review",
            fix=(
                "現行の目的、直接影響範囲、直近の結果から、"
                "範囲の限定・結果の再利用・重複実行の省略・短い確認手段を比べてください。"
                "必要な全体検証、修正後の再検証、CI待機は省かない。"
            ),
        )
        for kind, review in result
    ]
