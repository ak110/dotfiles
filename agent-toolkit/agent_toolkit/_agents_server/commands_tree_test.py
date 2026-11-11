"""commandsのtreeの契約を検証する。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import datetime
import json
import pathlib
from typing import Any

import pytest

from agent_toolkit import atk
from agent_toolkit._agents_server import commands, resume_waits, shared_layout
from agent_toolkit._common import state_paths
from agent_toolkit._common.next_action import NEXT_ACTION_PREFIX
from agent_toolkit._testing.agents_server_commands_support import _human_environment, _write_root_sessions


@pytest.mark.usefixtures("session_environment")
def test_agents_list_shows_status_line_right_of_session_id(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """人の端末ではsession IDの右側へ、名前と実行モデル、直近の行動、経過時間、状態をstatusLineの順で並べる。"""
    _human_environment(monkeypatch)

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "list"])

    line = next(line for line in capsys.readouterr().out.splitlines() if "session-1" in line)
    assert line.startswith("└─ session-1  ")
    positions = [
        line.index(fragment) for fragment in ("session-1", "調査レーン (codex:model/medium)", "Bash: git status", " · running")
    ]
    assert positions == sorted(positions)
    assert line.rstrip().endswith(" · running")


def test_human_tree_keeps_session_id_and_shortens_description_on_narrow_terminal() -> None:
    """狭い端末でもsession IDを省略せず、右側の説明を短くして端末幅に収める。"""
    now = datetime.datetime(2026, 9, 13, 0, 5, tzinfo=datetime.UTC)
    session = {
        "session_id": "01a10606-6bb6-78e1-88cb-daaa70c15281",
        "status": "running",
        "label": "r01-add-wi",
        "engine": "codex",
        "model": "gpt-6-sol",
        "effort": "medium",
        "last_action": "Bash: " + "長い説明" * 20,
        "started_at": "2026-09-13T00:00:00+00:00",
    }

    output = commands._human_tree([("root-a", [session])], columns=100, now=now)  # pylint: disable=protected-access

    line = output.splitlines()[1]
    assert "01a10606-6bb6-78e1-88cb-daaa70c15281" in line
    assert commands._display_width(line) <= 100  # pylint: disable=protected-access
    assert "…" in line
    assert line.endswith("5m0s · running")


def test_human_tree_leaves_description_width_on_narrow_terminal() -> None:
    """100桁の端末で入れ子の委譲先が並んでも、名前を先に短くして行動・進捗の欄へ下限幅を残す。"""
    now = datetime.datetime(2026, 9, 13, 0, 30, tzinfo=datetime.UTC)
    parent_id = "272b7915-54c1-4060-8a76-230591c9e1bd"
    base: dict[str, Any] = {
        "status": "running",
        "engine": "claude",
        "model": "opus[1m]",
        "effort": "high",
        "last_action": "Bash: cd /home/user/project && uv run --frozen pyfltr run agent-toolkit/agent_toolkit",
        "started_at": "2026-09-13T00:11:26+00:00",
    }
    sessions = [
        {**base, "session_id": parent_id, "label": "lane-01-exec"},
        {
            **base,
            "session_id": "9ccd12ff-c38a-48fa-a762-8316adc49bd5",
            "label": "lane01-gA",
            "owner_status_file": f"{parent_id}.json",
        },
    ]

    lines = commands._human_tree([("root-a", sessions)], columns=100, now=now).splitlines()  # pylint: disable=protected-access

    for line in lines[1:]:
        assert commands._display_width(line) <= 100  # pylint: disable=protected-access
        description = line[line.index("Bash: ") : line.index("18m34s · running")].rstrip()
        assert commands._display_width(description) >= commands._MIN_DESCRIPTION_WIDTH  # pylint: disable=protected-access


def test_human_tree_prefers_usage_limit_and_api_retry_over_progress() -> None:
    """実行中sessionの利用上限の解除待ちとAPI再試行を、通常の行動や進捗より優先して区別する。"""
    now = datetime.datetime(2026, 9, 13, 1, 0, tzinfo=datetime.UTC)
    base: dict[str, Any] = {
        "status": "running",
        "label": "lane",
        "engine": "claude",
        "model": "opus",
        "last_action": "Bash: 実行中",
    }
    sessions = [
        {
            **base,
            "session_id": "limit",
            "started_at": "2026-09-13T00:00:00+00:00",
            "api_error": {
                "type": resume_waits.USAGE_LIMIT_ERROR_TYPE,
                "http_status": 429,
                "first_at": "2026-09-13T00:30:00+00:00",
                "count": 1,
                "limit_type": "five_hour",
                "resets_at": "2026-09-13T02:00:00+00:00",
            },
        },
        {
            **base,
            "session_id": "retry",
            "started_at": "2026-09-13T00:00:01+00:00",
            "api_error": {"type": "overloaded", "http_status": 529, "first_at": "2026-09-13T00:59:00+00:00", "count": 3},
        },
        {**base, "session_id": "normal", "started_at": "2026-09-13T00:00:02+00:00", "progress": "進捗"},
    ]

    lines = commands._human_tree([("root-a", sessions)], columns=200, now=now).splitlines()  # pylint: disable=protected-access

    limit, retry, normal = (next(line for line in lines if session_id in line) for session_id in ("limit", "retry", "normal"))
    assert "利用上限の解除待ち five_hour" in limit
    assert limit.endswith("解除まで1h0m · 30m0s")
    assert "API再試行 overloaded" in retry
    assert retry.endswith("HTTP 529 · 1m0s · 3回")
    assert "Bash: 実行中" in normal
    assert normal.endswith("59m58s · running")


def test_human_tree_reports_empty_list() -> None:
    """表示対象のrootが無ければ、sessionが無いことを示す。"""
    now = datetime.datetime(2026, 9, 13, tzinfo=datetime.UTC)

    assert commands._human_tree([], columns=80, now=now) == "sessionはありません"  # pylint: disable=protected-access


@pytest.mark.parametrize("include_terminated", [False, True])
def test_agents_list_watch_redraws_until_interrupted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    include_terminated: bool,
) -> None:
    """`--watch`は周期ごとに一覧を取得し直して画面を描き替え、Ctrl-Cで終了コード0を返す。"""
    _human_environment(monkeypatch)
    for key in ("CLAUDE_CODE_SESSION_ID", "AGENT_TOOLKIT_OWNER_SESSION", "CODEX_THREAD_ID"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    timestamps = {"started_at": "2026-09-30T00:00:00+00:00", "updated_at": "2026-09-30T00:00:00+00:00"}
    first = {"session_id": "session-first", "status": "running", **timestamps}
    done = {"session_id": "session-done", "status": "completed", **timestamps}
    _write_root_sessions(tmp_path, "root-a", [first, done])
    monkeypatch.setattr(commands.sys.stdout, "isatty", lambda: True)
    intervals: list[float] = []

    def fake_sleep(seconds: float) -> None:
        intervals.append(seconds)
        if len(intervals) == 1:
            added = {"session_id": "session-added", "status": "running", **timestamps}
            path = shared_layout.status_directory("root-a", tmp_path) / "root.json"
            path.write_text(json.dumps({"version": 1, "sessions": [first, done, added]}), encoding="utf-8")
            return
        raise KeyboardInterrupt

    monkeypatch.setattr(commands.time, "sleep", fake_sleep)
    argv = ["agents", "list", "--watch", *(["--include-terminated"] if include_terminated else [])]

    with pytest.raises(SystemExit, match="0"):
        atk.main(argv)

    frames = capsys.readouterr().out.split(commands._CLEAR_SCREEN)[1:]  # pylint: disable=protected-access
    assert intervals == [2.0, 2.0]
    assert len(frames) == 2
    assert "session-added" not in frames[0]
    assert "session-added" in frames[1]
    assert all("Ctrl-Cで終了" in frame for frame in frames)
    assert all(("session-done" in frame) is include_terminated for frame in frames)


@pytest.mark.parametrize("agent_environment", [True, False])
def test_agents_list_watch_rejects_agent_environment_and_non_terminal(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    agent_environment: bool,
) -> None:
    """エージェント環境と端末以外への出力では、継続表示を始めず理由と次の操作を返す。"""
    _human_environment(monkeypatch)
    if agent_environment:
        monkeypatch.setenv("AI_AGENT", "1")
    monkeypatch.setattr(commands.sys.stdout, "isatty", lambda: False)

    def fail_sleep(_seconds: float) -> None:
        raise AssertionError("継続表示を開始した")

    monkeypatch.setattr(commands.time, "sleep", fail_sleep)

    with pytest.raises(SystemExit, match="2"):
        atk.main(["agents", "list", "--watch"])

    captured = capsys.readouterr()
    assert captured.out == ""
    assert ("エージェント環境では使えない" in captured.err) is agent_environment
    assert ("端末へ出力する場合だけ使える" in captured.err) is not agent_environment
    assert "--watchを外した`atk agents list`" in captured.err
    assert NEXT_ACTION_PREFIX in captured.err
