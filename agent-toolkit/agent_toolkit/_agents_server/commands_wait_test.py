"""commandsのwaitの契約を検証する。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import argparse
import json
import os
import pathlib
import sys
from typing import Any

import pytest

from agent_toolkit import atk
from agent_toolkit._agents_server import (
    commands,
    result_projection,
    shared_layout,
    state,
    wait_output_tracking,
    wait_run,
    wait_summary,
)
from agent_toolkit._atk import managed_temp, output_file
from agent_toolkit._common import session_activity, session_registry, state_paths
from agent_toolkit._testing.agents_server_commands_support import _write_root_sessions


def test_agents_wait_passes_explicit_root_to_waiter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLIの明示ルートを環境変数へ変換せず待機処理へ渡す。"""
    received: list[str | None] = []

    def fake_wait(**kwargs) -> int:
        received.append(kwargs["root_session_id"])
        return 0

    monkeypatch.setattr(commands.agents_wait, "wait_for_result", fake_wait)

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "wait", "--root-session-id", "mcp-root"])

    assert received == ["mcp-root"]


def test_recover_published_wait_drops_legacy_review_receipt(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """旧版が公開して未配送だった待機表明も、通常のレビュー完了として案内し直さない。"""
    monkeypatch.setenv("CLAUDECODE", "1")
    old = {
        "session_id": "session-1",
        "label": "scope-review",
        "status": "completed",
        "agent_message": "待機中: child",
        "error": {"heldResultFinalized": True},
        "next_action": "既存の補足\n" + result_projection.REVIEW_RESULT_NEXT_ACTION,
        "read_requirements": [
            "agent-basics:review-standards/SKILL.md",
            "agent-basics:review-standards/references/reviewee.md",
            "agent-basics:bugfix/SKILL.md",
        ],
    }
    run = wait_run.RunningRun(run_id="published-run", targets=["session-1"]).publish(json.dumps(old), 0, "stdout")
    path = tmp_path / "published-run.json"
    path.write_text(run.model_dump_json(exclude_unset=True), encoding="utf-8")

    assert commands.agents_wait._consume_wait_result(path) == 0
    output = capsys.readouterr().out
    assert "unfinished_waits=0" in output
    assert "既存の補足" in output
    assert "`show`" in output
    assert "agent-basics:bugfix/SKILL.md" in output
    assert "レビュー結果を受領しました" not in output
    assert "review-standards" not in output


@pytest.mark.usefixtures("session_environment")
def test_agents_wait_without_target_returns_no_empty_saved_summary(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """待機対象の無い非0終了では、標準出力へ空の保存先と行数を返さず、生成した保存先の領域も残さない。

    空の要約を返すと、呼び出し元はヘルプが示す対象不在の動作と異なる出力を受け取り、
    読む結果の無いファイルを開いてから委譲元の応答の確認へ進む。
    """
    root = shared_layout.status_directory("root-session", tmp_path) / "root.json"
    document = json.loads(root.read_text(encoding="utf-8"))
    document["sessions"] = []
    root.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-app-data"))

    with pytest.raises(SystemExit) as raised:
        atk.main(["agents", "wait"])

    captured = capsys.readouterr()
    assert raised.value.code == 10
    assert not captured.out
    assert "待機対象の登録が0件" in captured.err
    assert not managed_temp.list_managed_temp("atk-output")


@pytest.mark.usefixtures("session_environment")
def test_agents_wait_writes_summary_directly_with_one_body_file(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """短い回収結果は要約を標準出力へ直接書き、本文はセッションのmanaged-tempの`agents-wait/<session_id>.md`だけが持つ。

    出力の保存物とrun記録へ本文を重ねると、同じ本文の複製が待機のたびに増える。
    """
    results = shared_layout.results_directory("root-session", tmp_path)
    results.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": "completed",
        "owner_status_file": "root.json",
        "agent_message": "完了\n\n- 詳細は`報告.md`",
        "session": {"session_id": "session-1", "label": "調査レーンA"},
    }
    (results / "session-1.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "wait"])

    output_lines = capsys.readouterr().out.splitlines()
    body_path = managed_temp.session_temp_path("root-session") / "agents-wait" / "session-1.md"
    assert output_lines == [
        "終了: 1件",
        f"終了行: session_id=session-1 label=調査レーンA status=completed agent_message_path={body_path}",
        "本文開始: session_id=session-1",
        "完了",
        "",
        "- 詳細は`報告.md`",
        "本文終了: session_id=session-1",
    ]
    assert body_path.read_text(encoding="utf-8") == payload["agent_message"]
    assert not managed_temp.list_managed_temp("atk-output")
    session_area = managed_temp.session_temp_path("root-session")
    assert sorted(path.name for path in session_area.iterdir()) == ["agents-wait"]
    runs = [
        path
        for path in (shared_layout.status_directory("root-session", tmp_path) / "wait-results").rglob("*.json")
        if path.is_file()
    ]
    assert runs
    for run in runs:
        assert payload["agent_message"] not in run.read_text(encoding="utf-8")


@pytest.mark.usefixtures("session_environment")
def test_agents_wait_omits_long_body_and_keeps_collection_readable(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-app-data"))
    results = shared_layout.results_directory("root-session", tmp_path)
    results.mkdir(parents=True, exist_ok=True)
    long_message = "完了報告" * 5000
    (results / "session-1.json").write_text(
        json.dumps(
            {"status": "completed", "owner_status_file": "root.json", "agent_message": long_message}, ensure_ascii=False
        ),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "wait"])

    output = capsys.readouterr().out
    terminal_line, terminal_row = output.splitlines()
    assert terminal_line == "終了: 1件"
    assert terminal_row.startswith("終了行: session_id=session-1 label=なし status=completed agent_message_path=")
    body = pathlib.Path(terminal_row.split("agent_message_path=", 1)[1].split(" ", 1)[0])
    assert body.read_text(encoding="utf-8") == long_message
    assert not managed_temp.list_managed_temp("atk-output")

    session = state.SessionState("parent-1", "/tmp")
    wait_output_tracking.consume_claude_agents_server_message(
        session,
        {"content": [{"id": "toolu_1", "name": "mcp__agents_server__start", "input": {"mode": "explore", "prompt": "調査"}}]},
    )
    wait_output_tracking.consume_claude_agents_server_message(
        session, {"content": [{"tool_use_id": "toolu_1", "content": {"session_id": "session-1", "status": "running"}}]}
    )
    wait_output_tracking.consume_claude_agents_server_message(
        session, {"content": [{"id": "toolu_2", "name": "Bash", "input": {"command": "atk agents wait"}}]}
    )
    wait_output_tracking.consume_claude_agents_server_message(
        session, {"content": [{"tool_use_id": "toolu_2", "content": output}]}
    )
    assert not session.live_child_session_ids


@pytest.mark.usefixtures("session_environment")
def test_agents_wait_writes_notice_summary_and_running_line(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """通知だけの待機では、送信元と通知件数と本文に続け、通知を除いた`status: running`の行を返す。"""
    notices = shared_layout.notices_directory("root-session", tmp_path)
    notices.mkdir(parents=True)
    (notices / "session-1.1.json").write_text(
        json.dumps({"version": 1, "session_id": "session-1", "sent_at": "2026-09-28T00:00:00Z", "body": "警告"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "wait"])

    output_lines = capsys.readouterr().out.splitlines()
    notice_line = output_lines.pop(1)
    assert notice_line.startswith("通知本文ファイル: ")
    assert pathlib.Path(notice_line.removeprefix("通知本文ファイル: ")).read_text(encoding="utf-8") == (
        "本文開始: session_id=session-1\n警告\n本文終了: session_id=session-1\n"
    )
    assert output_lines[:4] == [
        "通知: 1件（session_id: session-1）",
        "本文開始: session_id=session-1",
        "警告",
        "本文終了: session_id=session-1",
    ]
    running = json.loads(output_lines[4])
    assert running["session_id"] == "session-1" and running["status"] == "running"
    assert "notices" not in running
    assert len(output_lines) == 5


@pytest.mark.usefixtures("session_environment")
def test_agents_wait_writes_notice_and_terminal_summary(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    results = shared_layout.results_directory("root-session", tmp_path)
    results.mkdir(parents=True)
    (results / "session-1.json").write_text(
        json.dumps({"status": "completed", "owner_status_file": "root.json", "agent_message": "完了"}),
        encoding="utf-8",
    )
    notices = shared_layout.notices_directory("root-session", tmp_path)
    notices.mkdir(parents=True)
    for sequence in (1, 2):
        (notices / f"session-1.{sequence}.json").write_text(
            json.dumps({"version": 1, "session_id": "session-1", "sent_at": "2026-09-28T00:00:00Z", "body": f"通知{sequence}"}),
            encoding="utf-8",
        )
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "wait"])

    body_directory = managed_temp.session_temp_path("root-session") / "agents-wait"
    body_path = body_directory / "session-1.md"
    assert capsys.readouterr().out.splitlines() == [
        "通知: 2件（session_id: session-1）",
        f"通知本文ファイル: {body_directory / 'notices.md'}",
        "本文開始: session_id=session-1",
        "通知1",
        "本文終了: session_id=session-1",
        "本文開始: session_id=session-1",
        "通知2",
        "本文終了: session_id=session-1",
        "終了: 1件",
        f"終了行: session_id=session-1 label=なし status=completed agent_message_path={body_path}",
        "本文開始: session_id=session-1",
        "完了",
        "本文終了: session_id=session-1",
    ]


@pytest.mark.parametrize(
    ("error", "suffix"),
    [
        pytest.param(None, "", id="no-record"),
        pytest.param({"message": "失敗"}, ' error={"message":"失敗"}', id="other-error"),
        # 自動再開を消費した後のturnの終了が残った子sessionを記録した結果（`codex.py`の`turn/completed`）。
        # 再開したturnの最終の結果であり、待機表明と取り違えさせない。
        pytest.param(
            {"unobservedSessions": ["child-1"]},
            ' error={"unobservedSessions":["child-1"]}',
            id="resumed-turn-with-unobserved-child",
        ),
        pytest.param(
            {"unfinishedBackgroundTasks": ["buaqxv1xf"], "unobservedSessions": ["child-1"], "heldResultFinalized": True},
            ' error={"unfinishedBackgroundTasks":["buaqxv1xf"],"unobservedSessions":["child-1"],"heldResultFinalized":true}'
            " unfinished_waits=2",
            id="remaining-targets",
        ),
        pytest.param(
            {"heldResultFinalized": True},
            ' error={"heldResultFinalized":true} unfinished_waits=0',
            id="finalized-without-remaining-targets",
        ),
    ],
)
@pytest.mark.usefixtures("session_environment")
def test_agents_wait_terminal_line_marks_result_finalized_with_remaining_waits(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    error: dict[str, Any] | None,
    suffix: str,
) -> None:
    """待機対象が残ったまま確定した結果は、終了行だけから再開したturnの結果でないと分かる。

    委譲元は結果本文のファイルと終了行だけを読んで次の操作を選ぶため、終了行へ残す。
    """
    results = shared_layout.results_directory("root-session", tmp_path)
    results.mkdir(parents=True)
    payload: dict[str, Any] = {"status": "completed", "owner_status_file": "root.json", "agent_message": "待機中: buaqxv1xf"}
    if error is not None:
        payload["error"] = error
    (results / "session-1.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "wait"])

    body_path = managed_temp.session_temp_path("root-session") / "agents-wait" / "session-1.md"
    line = capsys.readouterr().out.splitlines()[1]
    if (error or {}).get("heldResultFinalized"):
        expected = " next_action=これは再開したturnの結果ではなく待機表明。`show`で現在のturnと待機対象の状態を確かめる"
        assert expected in line
        line = line.replace(expected, "")
    elif (error or {}).get("unobservedSessions"):
        expected = (
            ' next_action="未回収の子は`unobservedSessions`にある。agents_serverの子の結果が必要なら、'
            '`send_message`で元の委譲先に`atk agents wait`での回収を依頼する"'
        )
        assert expected in line
        line = line.replace(expected, "")
    assert line == (f"終了行: session_id=session-1 label=なし status=completed agent_message_path={body_path}{suffix}")


@pytest.mark.parametrize(("excess", "shown"), [pytest.param(0, True, id="at-limit"), pytest.param(1, False, id="over-limit")])
def test_wait_summary_budget_covers_whole_output(tmp_path: pathlib.Path, excess: int, shown: bool) -> None:
    body_path = tmp_path / "body.md"
    terminal_line = f"終了行: session_id=session-1 label=なし status=completed agent_message_path={body_path}"

    fixed = f"終了: 1件\n{terminal_line}\n本文開始: session_id=session-1\n\n本文終了: session_id=session-1\n"
    body = "x" * (wait_summary.BODY_BUDGET_BYTES - len(fixed.encode("utf-8")) + excess)
    body_path.write_text(body, encoding="utf-8")

    output = wait_summary.render([{"session_id": "session-1", "status": "completed", "agent_message_path": str(body_path)}])

    assert output.splitlines()[:2] == ["終了: 1件", terminal_line]
    assert (body in output) is shown
    assert len(output.encode("utf-8")) <= wait_summary.BODY_BUDGET_BYTES
    assert wait_summary.BODY_BUDGET_BYTES == output_file.AUTO_SAVE_THRESHOLD_BYTES


def test_wait_public_output_reserves_running_rows_within_limit(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDECODE", "1")
    body_path = tmp_path / "body.md"
    terminal_line = f"終了行: session_id=session-1 label=なし status=completed agent_message_path={body_path}"
    rows: list[dict[str, Any]] = [
        {"session_id": "session-1", "status": "completed", "agent_message_path": str(body_path)},
        {"session_id": "session-2", "status": "running", "label": "別の担当", "notices": [{"body": "n"}]},
    ]
    body_path.write_text("", encoding="utf-8")
    fixed_size = len(wait_summary.render(rows).encode("utf-8")) + 1
    body_path.write_text("x" * (wait_summary.BODY_BUDGET_BYTES - fixed_size), encoding="utf-8")
    assert len(wait_summary.render(rows).encode("utf-8")) == wait_summary.BODY_BUDGET_BYTES

    commands.agents_wait._print_public("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), "stdout")

    output = capsys.readouterr().out
    assert terminal_line in output.splitlines()
    assert '"session_id":"session-2"' in output
    assert len(output.encode("utf-8")) <= wait_summary.BODY_BUDGET_BYTES


@pytest.mark.usefixtures("session_environment")
def test_agents_wait_is_not_resaved_with_stderr(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    summary = "終了: 1件\n" + "x" * (output_file.AUTO_SAVE_THRESHOLD_BYTES - len("終了: 1件\n".encode()) - 1) + "\n"

    def fake_wait(**_kwargs) -> int:
        print(summary, end="")
        print("警告", file=sys.stderr)
        return 0

    monkeypatch.setattr(commands.agents_wait, "wait_for_result", fake_wait)
    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "wait"])

    captured = capsys.readouterr()
    assert captured.out == summary
    assert "警告" not in captured.err
    saved = pathlib.Path(captured.err.split("標準エラー保存先: ", 1)[1].split("\n", 1)[0])
    assert saved.read_text(encoding="utf-8") == "警告\n"


def test_wait_summary_shows_notice_file_and_bodies_within_limit(tmp_path: pathlib.Path) -> None:
    notices_path = tmp_path / "notices.md"
    long_body = "通" * wait_summary.BODY_BUDGET_BYTES
    rows: list[dict[str, Any]] = [
        {
            "session_id": "session-1",
            "status": "running",
            "notices": [{"body": long_body}, {"body": "短い通知"}],
        },
    ]

    assert wait_summary.render(rows, notices_reference=f"通知本文ファイル: {notices_path}").splitlines() == [
        "通知: 2件（session_id: session-1）",
        f"通知本文ファイル: {notices_path}",
        "本文開始: session_id=session-1",
        "短い通知",
        "本文終了: session_id=session-1",
    ]
    assert "短い通知" in wait_summary.notice_text(rows)
    assert long_body in wait_summary.notice_text(rows)


@pytest.mark.parametrize("count", [pytest.param(0, id="none"), pytest.param(1, id="one"), pytest.param(301, id="many")])
def test_wait_summary_required_lines_do_not_grow_with_notices(tmp_path: pathlib.Path, count: int) -> None:
    body_path = tmp_path / "body.md"
    rows: list[dict[str, Any]] = [
        {"session_id": "session-1", "status": "completed", "agent_message_path": str(body_path)},
        {"session_id": "session-2", "status": "running", "notices": [{"body": f"通知{index}" * 40} for index in range(count)]},
    ]
    body_path.write_text("", encoding="utf-8")
    without_body = len(
        wait_summary.render(rows, notices_reference=f"通知本文ファイル: {tmp_path / 'notices.md'}").encode("utf-8")
    )
    body_path.write_text("x" * (wait_summary.BODY_BUDGET_BYTES - without_body - 1), encoding="utf-8")

    output = wait_summary.render(rows, notices_reference=f"通知本文ファイル: {tmp_path / 'notices.md'}")

    assert len(output.encode("utf-8")) <= wait_summary.BODY_BUDGET_BYTES
    assert f"終了行: session_id=session-1 label=なし status=completed agent_message_path={body_path}" in output
    assert output.count("通知本文ファイル: ") == (1 if count else 0)


@pytest.mark.parametrize(
    ("with_run", "run_name"),
    [
        pytest.param(True, "run.json", id="run-record"),
        pytest.param(True, "空白 と ' を含む run.json", id="run-record-with-quotes"),
        pytest.param(False, "", id="no-run-record"),
    ],
)
def test_wait_public_output_when_notice_file_cannot_be_written(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, with_run: bool, run_name: str
) -> None:
    monkeypatch.setenv("CLAUDECODE", "1")

    def fail_write(*_args: Any, **_kwargs: Any) -> pathlib.Path:
        raise OSError("容量不足")

    monkeypatch.setattr(commands.agents_wait._BodyFileWriter, "write", fail_write)
    long_body = "通" * wait_summary.BODY_BUDGET_BYTES
    rows = [
        {"session_id": "session-1", "status": "completed", "agent_message_path": str(tmp_path / "missing.md")},
        {"session_id": "session-2", "status": "running", "notices": [{"body": long_body}]},
    ]
    run_path = tmp_path / run_name if with_run else None

    commands.agents_wait._print_public(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows), "stdout", run_path=run_path
    )

    captured = capsys.readouterr()
    terminal_line = f"終了行: session_id=session-1 label=なし status=completed agent_message_path={tmp_path / 'missing.md'}"
    assert terminal_line in captured.out.splitlines()
    assert "次の操作:" in captured.err
    if with_run:
        assert f"通知本文のrun記録: {run_path}" in captured.out.splitlines()
        assert long_body not in captured.out
        assert len(captured.out.encode("utf-8")) <= wait_summary.BODY_BUDGET_BYTES
    else:
        assert long_body in captured.out


def test_consumed_published_run_points_to_run_when_notice_file_cannot_be_written(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDECODE", "1")

    def fail_write(*_args: Any, **_kwargs: Any) -> pathlib.Path:
        raise OSError("容量不足")

    monkeypatch.setattr(commands.agents_wait._BodyFileWriter, "write", fail_write)
    long_body = "通" * wait_summary.BODY_BUDGET_BYTES
    row = {"session_id": "session-2", "status": "running", "notices": [{"body": long_body}]}
    output = json.dumps(row, ensure_ascii=False)
    run = wait_run.RunningRun(run_id="published-run", targets=["session-2"]).publish(output, 0, "stdout")
    path = tmp_path / "published run.json"
    path.write_text(run.model_dump_json(exclude_unset=True), encoding="utf-8")

    commands.agents_wait._consume_wait_result(path)

    captured = capsys.readouterr()
    assert f"通知本文のrun記録: {path}" in captured.out.splitlines()
    assert long_body not in captured.out
    assert len(captured.out.encode("utf-8")) <= wait_summary.BODY_BUDGET_BYTES
    saved_output = json.loads(path.read_text(encoding="utf-8"))["output"]
    assert json.loads(saved_output)["notices"][0]["body"] == long_body


def test_wait_summary_keeps_earlier_bodies_and_skips_oversized_or_unreadable(tmp_path: pathlib.Path) -> None:
    long_body = tmp_path / "long.md"
    long_body.write_text("長" * wait_summary.BODY_BUDGET_BYTES, encoding="utf-8")
    short_body = tmp_path / "short.md"
    short_body.write_text("短い本文\n", encoding="utf-8")
    missing_body = tmp_path / "missing.md"
    rows: list[dict[str, Any]] = [
        {"session_id": "session-0", "status": "running", "notices": [{"body": "通知本文"}]},
        {"session_id": "session-1", "status": "completed", "agent_message_path": str(long_body)},
        {"session_id": "session-2", "status": "failed", "label": "検証", "agent_message_path": str(short_body)},
        {"session_id": "session-3", "status": "completed", "agent_message_path": str(missing_body)},
    ]

    assert wait_summary.render(rows).splitlines() == [
        "通知: 1件（session_id: session-0）",
        "本文開始: session_id=session-0",
        "通知本文",
        "本文終了: session_id=session-0",
        "終了: 3件",
        f"終了行: session_id=session-1 label=なし status=completed agent_message_path={long_body}",
        f"終了行: session_id=session-2 label=検証 status=failed agent_message_path={short_body}",
        "本文開始: session_id=session-2",
        "短い本文",
        "本文終了: session_id=session-2",
        f"終了行: session_id=session-3 label=なし status=completed agent_message_path={missing_body}",
    ]
    assert wait_summary.terminal_session_ids(wait_summary.render(rows)) == {"session-1", "session-2", "session-3"}


def test_wait_summary_shows_error_and_next_action_for_failed_row(tmp_path: pathlib.Path) -> None:
    """失敗で終了した行の要約は`error`と`next_action`を示し、委譲元が原因と次の操作を読める。

    `error`と`next_action`が要約に無いと、本文が空か作業途中の文である失敗を、委譲元が原因不明として継続を送る。
    """
    body_path = tmp_path / "body.md"
    body_path.write_text("", encoding="utf-8")
    next_action = "利用上限による失敗なら別のmodel_typeでstartし直す（例: model_type=review）"
    row: dict[str, Any] = {
        "session_id": "session-1",
        "label": "WI投入",
        "status": "failed",
        "agent_message": "",
        "engine": "codex",
        "model": "gpt-5.6-sol",
        "effort": "medium",
        "model_type": "execute",
        "error": {
            "codexErrorInfo": "usageLimitExceeded",
            "message": "Your workspace is out of credits. Add credits to continue.",
        },
        "next_action": next_action,
        "agent_message_path": str(body_path),
    }

    terminal_line = wait_summary.render([row]).splitlines()[1]
    assert terminal_line == (
        f"終了行: session_id=session-1 label=WI投入 status=failed agent_message_path={body_path}"
        " engine=codex model=gpt-5.6-sol effort=medium model_type=execute"
        ' error={"codexErrorInfo":"usageLimitExceeded","message":"Your workspace is out of credits. Add credits to continue."}'
        f' next_action="{next_action}"'
    )


def test_wait_summary_shows_all_fields_except_body_and_notices() -> None:
    row: dict[str, Any] = {
        "new_field": "追加の値",
        "agent_message": "本文",
        "notices": [{"body": "通知本文"}],
        "status": "completed",
        "agent_message_path": "/nonexistent/body.md",
        "spaced": "a b",
        "empty": "",
        "count": 3,
        "session_id": "session-1",
        "label": "調査",
    }

    terminal_line = next(line for line in wait_summary.render([row]).splitlines() if line.startswith("終了行: "))
    assert terminal_line == (
        "終了行: session_id=session-1 label=調査 status=completed agent_message_path=/nonexistent/body.md"
        ' new_field=追加の値 spaced="a b" empty="" count=3'
    )
    assert "本文" not in terminal_line
    assert "通知本文" not in terminal_line


def test_agents_list_keeps_long_usage_limit_waits_read_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """古い活動記録の待機rootと利用上限待機中の委譲先を表示し、保存状態を維持する。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    identity = session_registry.ProcessIdentity.of(os.getpid())
    assert identity is not None
    with monkeypatch.context() as activity_clock:
        activity_clock.setattr(session_activity.time, "time", lambda: 1.0)
        for event in ("SessionStart", "Stop"):
            session_activity.record_activity(
                {"session_id": "root-waiting", "hook_event_name": event},
                process=session_activity.HostProcess("claude", identity),
                state_root=tmp_path,
            )
    _write_root_sessions(
        tmp_path,
        "root-with-child",
        [
            {
                "session_id": "waiting-child",
                "status": "running",
                "progress": "進捗",
                "started_at": "2026-09-01T00:00:00+00:00",
                "updated_at": "2026-09-01T00:00:00+00:00",
                "api_error": {
                    "type": "usage_limit",
                    "limit_type": "five_hour",
                    "first_at": "2026-09-01T00:00:00+00:00",
                    "resets_at": "2099-01-01T00:00:00+00:00",
                },
            }
        ],
    )
    before = {
        path.relative_to(tmp_path): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    parser = argparse.ArgumentParser()
    commands.build_parser(parser)
    assert commands.dispatch(parser.parse_args(["list"]), environment={}) == 0
    output = capsys.readouterr().out
    assert "root root-waiting" in output
    assert "root root-with-child" in output
    assert "waiting-child" in output
    assert "利用上限の解除待ち five_hour" in output
    after = {
        path.relative_to(tmp_path): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert after == before
