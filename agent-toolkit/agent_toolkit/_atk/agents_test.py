"""`atk agents list/show/wait`の共有状態診断と公開説明を検証する。"""

from __future__ import annotations

import json
import pathlib

import pytest

from agent_toolkit import _atk_agents, atk
from agent_toolkit._atk import config, environment

status_file = _atk_agents.status_file


def test_agents_wait_help_requires_reissue_after_running(capsys: pytest.CaptureFixture[str]) -> None:
    """回収できた全件の出力形式と、待機の成立判定および再発行の条件を説明する。"""
    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "wait", "--help"])

    output = _without_wrapping(capsys.readouterr().out)
    expected_fragments = (
        "1回の巡回で回収できた全件",
        "1件1行のJSON Lines",
        "最初の待機で起動中sessionと未回収結果を登録簿へ固定",
        "通知だけを回収した場合",
        "待機対象の行が現れない応答は当該対象が未終端であることを示す",
        "同じターン内に同じコマンドを再発行",
        "結果を保持しない`stop`とsession登録簿での喪失確定",
        "待機対象登録が破損している場合",
        "終端statusでは追加の結果受領操作は不要",
        "`--output-file`を指定した場合",
        "回収した本文は当該保存先に残る",
        "MCPの`list`を1回呼び出してから同じコマンドを再実行",
        "--root-session-id",
        "次の逐次待機へ再配送しない",
    )
    assert all(_without_wrapping(fragment) in output for fragment in expected_fragments)


def test_agents_wait_passes_explicit_root_to_waiter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLIの明示ルートを環境変数へ変換せず待機処理へ渡す。"""
    received: list[str | None] = []

    def fake_wait(**kwargs) -> int:
        received.append(kwargs["root_session_id"])
        return 0

    monkeypatch.setattr(_atk_agents.agents_wait, "wait_for_result", fake_wait)

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "wait", "--root-session-id", "mcp-root"])

    assert received == ["mcp-root"]


def _without_wrapping(text: str) -> str:
    """端末幅で変わる折り返しに依存せず本文を照合するため、空白文字を取り除いた文字列を返す。"""
    return "".join(text.split())


def test_agents_list_help_states_prompt_is_obtained_from_show(capsys: pytest.CaptureFixture[str]) -> None:
    """一覧が起動文を含まないことと、起動文の取得先を説明する。"""
    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "list", "--help"])

    output = _without_wrapping(capsys.readouterr().out)
    assert _without_wrapping("各sessionへ起動文を含めず、起動文は`atk agents show`が返す。") in output
    assert _without_wrapping("MCPの`list`を1回呼び出してから再実行") in output


@pytest.fixture
def session_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> pathlib.Path:
    """1件の実行中sessionを持つ共有状態を準備する。"""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "root-session")
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    monkeypatch.setattr(config, "state_dir", lambda: tmp_path)
    directory = status_file.status_directory("root-session", tmp_path)
    directory.mkdir(parents=True)
    (directory / "root.json").write_text(
        json.dumps(
            {
                "version": 1,
                "sessions": [
                    {
                        "session_id": "session-1",
                        "status": "running",
                        "cwd": "/worktree",
                        "prompt": "調査せよ",
                        "label": "調査レーン",
                        "model_type": "execute",
                        "launch_kind": "delegate",
                        "started_at": "2026-09-13T00:00:00+00:00",
                        "updated_at": "2026-09-13T00:01:00+00:00",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return directory


@pytest.mark.usefixtures("session_environment")
def test_agents_list_returns_diagnostic_fields_without_prompt(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """listは診断用の項目を返し、起動文だけを除く。"""
    monkeypatch.setenv("AI_AGENT", "1")
    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "list"])

    payload = json.loads(capsys.readouterr().out)
    session = payload["sessions"][0]
    assert session == {
        "session_id": "session-1",
        "status": "running",
        "cwd": "/worktree",
        "label": "調査レーン",
        "model_type": "execute",
        "launch_kind": "delegate",
        "started_at": "2026-09-13T00:00:00+00:00",
        "updated_at": "2026-09-13T00:01:00+00:00",
        "owner_status_file": "root.json",
        "result_available": False,
        "seconds_since_activity": session["seconds_since_activity"],
        "output_updated_at": None,
        "seconds_since_output": session["seconds_since_output"],
    }


@pytest.mark.usefixtures("session_environment")
def test_agents_wait_saves_collected_lines_to_the_output_file(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """保存先を指定した待機は、回収したJSON Linesを当該ファイルへ残し、標準出力を2行に保つ。

    保存先を持たない待機では、回収と同時に原本が削除されて本文が標準出力にだけ現れ、
    後続の工程と後続のセッションが当該本文を取得できない。
    """
    results = status_file.results_directory("root-session", tmp_path)
    results.mkdir(parents=True, exist_ok=True)
    (results / "session-1.json").write_text(
        json.dumps({"status": "completed", "owner_status_file": "root.json", "agent_message": "完了"}, ensure_ascii=False),
        encoding="utf-8",
    )
    destination = tmp_path / "wait-result.jsonl"

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "wait", f"--output-file={destination}"])

    output_lines = capsys.readouterr().out.splitlines()
    assert output_lines == [f"保存先: {destination}", "行数: 1"]
    saved = json.loads(destination.read_text(encoding="utf-8").strip())
    assert saved["session_id"] == "session-1"
    assert saved["agent_message"] == "完了"


@pytest.mark.usefixtures("session_environment")
def test_agents_show_selects_one_session(capsys: pytest.CaptureFixture[str]) -> None:
    """showは完全識別子で指定した1件だけを返す。"""
    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "show", "session-1"])

    payload = json.loads(capsys.readouterr().out)
    assert payload["session_id"] == "session-1"
    assert payload["prompt"] == "調査せよ"


@pytest.mark.usefixtures("session_environment")
def test_agents_show_finds_uncollected_result_after_status_expires(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """期限後に状態一覧から消えたsessionも保存済みの起動情報と結果を返す。"""
    root = status_file.status_directory("root-session", tmp_path)
    (root / "root.json").write_text('{"version": 1, "sessions": []}', encoding="utf-8")
    results = status_file.results_directory("root-session", tmp_path)
    results.mkdir(exist_ok=True)
    (results / "nested.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "agent_message": "完了",
                "turn_seq": 3,
                "owner_status_file": "writer.json",
                "session": {"session_id": "nested", "prompt": "調査せよ", "cwd": "/worktree"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "show", "nested"])

    payload = json.loads(capsys.readouterr().out)
    assert payload["session_id"] == "nested"
    assert payload["status"] == "completed"
    assert payload["prompt"] == "調査せよ"
    assert payload["agent_message"] == "完了"
    assert payload["owner_status_file"] == "writer.json"

    (results / "nested.json").write_text(
        json.dumps({"status": "completed", "agent_message": "完了", "owner_status_file": "writer.json"}),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "show", "nested"])
    prior_result = json.loads(capsys.readouterr().out)
    assert prior_result["session_id"] == "nested"
    assert prior_result["status"] == "completed"
    assert "prompt" not in prior_result

    (results / "nested.json").unlink()
    with pytest.raises(SystemExit, match="2"):
        atk.main(["agents", "show", "nested"])
    assert capsys.readouterr().err == "unknown session: nested\n"


@pytest.mark.usefixtures("session_environment")
def test_agents_list_shows_tree_outside_agent_environment(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """人間が読む環境ではrootとsessionの関係を表示し、起動文を除く。"""
    for name in environment.AGENT_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "list"])

    output = capsys.readouterr().out
    assert len(output.splitlines()) > 1
    assert "root root-session" in output
    assert "└─ session-1" in output
    assert "調査レーン" in output
    assert "調査せよ" not in output
    assert "running" in output


@pytest.mark.parametrize("environment_name", environment.AGENT_ENVIRONMENT_VARIABLES)
@pytest.mark.usefixtures("session_environment")
def test_agents_show_keeps_single_line_in_agent_environment(
    environment_name: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """エージェント環境では1件の状態を空白を含めない1行で書き、非ASCII文字をそのまま残す。"""
    for name in environment.AGENT_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(environment_name, "")

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "show", "session-1"])

    output = capsys.readouterr().out
    assert len(output.splitlines()) == 1
    assert '"session_id":"session-1"' in output
    assert "調査せよ" in output
    assert json.loads(output)["prompt"] == "調査せよ"


@pytest.mark.usefixtures("session_environment")
def test_agents_show_rejects_unknown_session(capsys: pytest.CaptureFixture[str]) -> None:
    """未知の識別子は非0と理由で拒否する。"""
    with pytest.raises(SystemExit, match="2"):
        atk.main(["agents", "show", "missing"])

    assert capsys.readouterr().err == "unknown session: missing\n"


def test_agents_list_without_conversation_root_shows_all_roots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """会話識別子のない直接端末では全rootのsessionを表示する。"""
    for key in ("CLAUDE_CODE_SESSION_ID", "AGENT_TOOLKIT_OWNER_SESSION", "CODEX_THREAD_ID"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(config, "state_dir", lambda: tmp_path)
    for root_session_id, remote_session_id in (("root-a", "session-a"), ("root-b", "session-b")):
        directory = status_file.status_directory(root_session_id, tmp_path)
        directory.mkdir(parents=True)
        (directory / "root.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "sessions": [
                        {
                            "session_id": remote_session_id,
                            "status": "running",
                            "started_at": "2026-09-14T00:00:00+00:00",
                            "updated_at": "2026-09-14T00:00:00+00:00",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "list"])

    output = capsys.readouterr().out
    assert "root root-a" in output
    assert "root root-b" in output
    assert "session-a" in output
    assert "session-b" in output


def test_agents_list_reports_unconfirmed_conversation_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """対応未確認の空一覧は成功扱いせず、MCP一覧による復旧を案内する。"""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "current-session")
    monkeypatch.setenv("AI_AGENT", "1")
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    monkeypatch.setattr(config, "state_dir", lambda: tmp_path)

    with pytest.raises(SystemExit, match="4"):
        atk.main(["agents", "list"])

    captured = capsys.readouterr()
    assert not captured.out
    assert "CLIが解決したroot=current-session" in captured.err
    assert "MCPの`list`を1回" in captured.err
    assert "`atk agents list`を再実行" in captured.err


def test_agents_list_returns_empty_for_confirmed_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """現行識別子自身の状態ディレクトリを確認できれば空一覧を通常結果として返す。"""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "root-session")
    monkeypatch.setenv("AI_AGENT", "1")
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    monkeypatch.setattr(config, "state_dir", lambda: tmp_path)
    status_file.status_directory("root-session", tmp_path).mkdir(parents=True)

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "list"])

    assert json.loads(capsys.readouterr().out) == {"sessions": []}


def test_agents_list_uses_explicit_alias_and_isolates_other_roots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """明示的な別名索引のrootだけを読み、別rootのsessionを混在させない。"""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "current-session")
    monkeypatch.setenv("AI_AGENT", "1")
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    monkeypatch.setattr(config, "state_dir", lambda: tmp_path)
    for root_session_id, remote_session_id in (("root-a", "session-a"), ("root-b", "session-b")):
        directory = status_file.status_directory(root_session_id, tmp_path)
        directory.mkdir(parents=True)
        (directory / "root.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "sessions": [{"session_id": remote_session_id, "status": "running"}],
                }
            ),
            encoding="utf-8",
        )
    status_file.write_root_alias("current-session", "root-a", tmp_path)

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "list"])

    payload = json.loads(capsys.readouterr().out)
    assert [session["session_id"] for session in payload["sessions"]] == ["session-a"]


def test_agents_logs_reads_claude_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """一覧の識別子から既存のClaude Code記録を時系列で読める。"""
    project = tmp_path / "projects" / "sample"
    project.mkdir(parents=True)
    (project / "session-1.jsonl").write_text(
        json.dumps(
            {
                "type": "user",
                "timestamp": "2026-09-24T00:00:00Z",
                "message": {"content": [{"type": "text", "text": "調査して"}]},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(_atk_agents.session_records, "default_claude_home", lambda: tmp_path)
    monkeypatch.setattr(_atk_agents.session_records, "default_codex_home", lambda: tmp_path)

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "logs", "session-1"])

    assert "[2026-09-24T00:00:00Z] user: 調査して" in capsys.readouterr().out


def test_agents_logs_reports_missing_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """存在しない記録は識別子を添えて報告する。"""
    monkeypatch.setattr(_atk_agents.session_records, "default_claude_home", lambda: tmp_path)
    monkeypatch.setattr(_atk_agents.session_records, "default_codex_home", lambda: tmp_path)

    with pytest.raises(SystemExit, match="2"):
        atk.main(["agents", "logs", "missing"])

    assert "missing" in capsys.readouterr().err


def test_agents_logs_reads_and_follows_antigravity_events(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Antigravityの保存済み出力と、その後に追記された行を順に表示する。"""
    monkeypatch.setattr(_atk_agents.session_records, "default_claude_home", lambda: tmp_path)
    monkeypatch.setattr(_atk_agents.session_records, "default_codex_home", lambda: tmp_path)
    monkeypatch.setattr(config, "state_dir", lambda: tmp_path)
    path = status_file.session_log_path("root-1", "agy-1", tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"event": "init", "conversation_id": "agy-1"}) + "\n", encoding="utf-8")
    sleeps = 0

    def append_then_stop(_seconds: float) -> None:
        nonlocal sleeps
        sleeps += 1
        if sleeps == 1:
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"event": "step_update", "step_update": {"text_delta": "調査中"}}) + "\n")
        else:
            raise KeyboardInterrupt

    monkeypatch.setattr(_atk_agents.time, "sleep", append_then_stop)

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "logs", "agy-1", "--follow"])

    output = capsys.readouterr().out
    assert "init: agy-1" in output
    assert "step_update: 調査中" in output
