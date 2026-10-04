"""`atk agents list/show/wait`の共有状態診断と公開説明を検証する。"""

from __future__ import annotations

import datetime
import json
import pathlib
from typing import Any

import pytest

from agent_toolkit import atk
from agent_toolkit._agents_server import commands, state
from agent_toolkit._atk import config, environment, managed_temp
from agent_toolkit._common.next_action import NEXT_ACTION_PREFIX

status_file = commands.status_file


def _write_jsonl(path: pathlib.Path, records: list[dict]) -> None:
    """公開CLI用の保存済み記録を作成する。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n", encoding="utf-8")


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
        "待機対象の行が現れない応答は、その対象が未終端であることを示す",
        "同じターン内に同じコマンドを再発行",
        "結果を保持しない`stop`とsession登録簿での喪失確定",
        "待機対象登録が破損している場合",
        "終端statusでは追加の結果受領操作は不要",
        "エージェント環境では出力するJSON Linesを生成側の保存先へ全量で保存",
        "通知件数と送信元session ID",
        "回収した本文はその保存先に残る",
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

    monkeypatch.setattr(commands.agents_wait, "wait_for_result", fake_wait)

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "wait", "--root-session-id", "mcp-root"])

    assert received == ["mcp-root"]


@pytest.mark.usefixtures("session_environment")
def test_public_wait_save_failure_keeps_unreceived_result(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """公開waitの保存先を開けない場合、実際の未回収結果を消費しない。"""
    monkeypatch.setenv("CLAUDECODE", "1")
    results = status_file.results_directory("root-session", tmp_path)
    results.mkdir(parents=True, exist_ok=True)
    result_file = results / "session-1.json"
    original = json.dumps({"status": "completed", "owner_status_file": "root.json", "agent_message": "保持する結果"})
    result_file.write_text(original, encoding="utf-8")

    def fail(_prefix: str) -> pathlib.Path:
        raise OSError("保存準備に失敗")

    monkeypatch.setattr(managed_temp, "create_managed_temp", fail)
    with pytest.raises(SystemExit, match="1"):
        atk.main(["agents", "wait"])
    assert result_file.read_text(encoding="utf-8") == original
    assert "呼び出しを開始しない" in capsys.readouterr().err


def _without_wrapping(text: str) -> str:
    """端末幅による折り返しの違いを除いて本文を比較するため、空白文字を取り除いた文字列を返す。"""
    return "".join(text.split())


def test_agents_list_help_states_prompt_is_obtained_from_show(capsys: pytest.CaptureFixture[str]) -> None:
    """一覧が委譲プロンプトを含まないことと、委譲プロンプトの取得先を説明する。"""
    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "list", "--help"])

    output = _without_wrapping(capsys.readouterr().out)
    assert _without_wrapping("実行条件と委譲プロンプトは`atk agents show`が返す。") in output
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
                        "engine": "codex",
                        "model": "model",
                        "effort": "medium",
                        "progress": "進捗",
                        "last_action": "Bash: git status",
                        "created_at": "2026-09-12T00:00:00+00:00",
                        "future_internal": "内部診断",
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
    """listは稼働と稼働時間に使う項目へ限定し、内部追加と詳細診断を返さない。"""
    monkeypatch.setenv("AI_AGENT", "1")
    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "list"])

    payload = json.loads(capsys.readouterr().out)
    session = payload["sessions"][0]
    assert session == {
        "session_id": "session-1",
        "status": "running",
        "label": "調査レーン",
        "last_action": "Bash: git status",
        "created_at": "2026-09-12T00:00:00+00:00",
        "started_at": "2026-09-13T00:00:00+00:00",
        "result_available": False,
        "seconds_since_activity": session["seconds_since_activity"],
    }


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
    root = status_file.status_directory("root-session", tmp_path) / "root.json"
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
def test_agents_wait_saves_small_result_without_output_option(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """短い回収結果も生成側のファイルへ残し、内訳とJSON Linesの内容を一致させる。"""
    results = status_file.results_directory("root-session", tmp_path)
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
    destination = pathlib.Path(output_lines[0].removeprefix("保存先: "))
    saved = json.loads(destination.read_text(encoding="utf-8").strip())
    body_path = pathlib.Path(saved["agent_message_path"])
    assert output_lines == [
        f"保存先: {destination}",
        "行数: 1",
        "終端: 1件",
        f"終端行: session_id=session-1 label=調査レーンA status=completed agent_message_path={body_path}",
    ]
    assert saved["session_id"] == "session-1"
    assert saved["label"] == "調査レーンA"
    assert saved["agent_message"] == payload["agent_message"]
    assert body_path.is_absolute()
    assert body_path.read_text(encoding="utf-8") == payload["agent_message"]


@pytest.mark.usefixtures("session_environment")
def test_agents_wait_auto_saves_long_result_for_agent_and_keeps_collection_readable(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """エージェント環境の引数なしの待機は、長い結果を自動保存して要約行を返し、回収判定もその保存先を読む。

    自動保存が無いと、待機のたびに保存先の組み立てを呼び出し側へ残す。要約行から保存先を読めないと、
    委譲元は回収済みの孫sessionを追跡し続け、受け取り済みの結果を理由に自動再開する。
    """
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-app-data"))
    results = status_file.results_directory("root-session", tmp_path)
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
    saved_line, count_line, terminal_line, terminal_row = output.splitlines()
    saved = pathlib.Path(saved_line.removeprefix("保存先: "))
    assert saved_line.startswith("保存先: ")
    assert (count_line, terminal_line) == ("行数: 1", "終端: 1件")
    assert terminal_row.startswith("終端行: session_id=session-1 label=なし status=completed agent_message_path=")
    assert json.loads(saved.read_text(encoding="utf-8"))["agent_message"] == long_message

    session = state.SessionState("parent-1", "/tmp")
    state.consume_claude_agents_server_message(
        session,
        {"content": [{"id": "toolu_1", "name": "mcp__agents_server__start", "input": {"mode": "explore", "prompt": "調査"}}]},
    )
    state.consume_claude_agents_server_message(
        session, {"content": [{"tool_use_id": "toolu_1", "content": {"session_id": "session-1", "status": "running"}}]}
    )
    state.consume_claude_agents_server_message(
        session, {"content": [{"id": "toolu_2", "name": "Bash", "input": {"command": "atk agents wait"}}]}
    )
    state.consume_claude_agents_server_message(session, {"content": [{"tool_use_id": "toolu_2", "content": output}]})
    assert not session.live_child_session_ids


@pytest.mark.usefixtures("session_environment")
def test_agents_wait_saves_notice_summary(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """通知だけの待機では、送信元と通知件数を保存先を読む前に示す。"""
    notices = status_file.notices_directory("root-session", tmp_path)
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
    destination = pathlib.Path(output_lines[0].removeprefix("保存先: "))
    assert output_lines == [
        f"保存先: {destination}",
        "行数: 1",
        "通知: 1件（session_id: session-1）",
    ]
    saved = json.loads(destination.read_text(encoding="utf-8"))
    assert saved["status"] == "running"
    assert saved["notices"][0]["body"] == "警告"


@pytest.mark.usefixtures("session_environment")
def test_agents_wait_saves_notice_and_terminal_summary(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """通知を伴う終端結果では、通知数と終端数をともに示す。"""
    results = status_file.results_directory("root-session", tmp_path)
    results.mkdir(parents=True)
    (results / "session-1.json").write_text(
        json.dumps({"status": "completed", "owner_status_file": "root.json", "agent_message": "完了"}),
        encoding="utf-8",
    )
    notices = status_file.notices_directory("root-session", tmp_path)
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

    output_lines = capsys.readouterr().out.splitlines()
    destination = pathlib.Path(output_lines[0].removeprefix("保存先: "))
    saved = json.loads(destination.read_text(encoding="utf-8"))
    assert output_lines == [
        f"保存先: {destination}",
        "行数: 1",
        "通知: 2件（session_id: session-1）",
        "終端: 1件",
        f"終端行: session_id=session-1 label=なし status=completed agent_message_path={saved['agent_message_path']}",
    ]
    assert saved["status"] == "completed"
    assert len(saved["notices"]) == 2


@pytest.mark.usefixtures("session_environment")
def test_agents_show_selects_one_session(capsys: pytest.CaptureFixture[str]) -> None:
    """showは完全識別子で指定した1件だけを返す。"""
    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "show", "session-1"])

    payload = json.loads(capsys.readouterr().out)
    assert payload["session_id"] == "session-1"
    assert payload["prompt"] == "調査せよ"
    assert payload["cwd"] == "/worktree"
    assert payload["engine"] == "codex"
    assert payload["model"] == "model"
    assert payload["effort"] == "medium"
    assert payload["progress"] == "進捗"
    assert {"future_internal", "owner_status_file", "updated_at", "output_updated_at", "seconds_since_output"}.isdisjoint(
        payload
    )


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
    assert "owner_status_file" not in payload

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
    assert capsys.readouterr().err.startswith(f"unknown session: nested\n{NEXT_ACTION_PREFIX}")


@pytest.mark.asyncio
@pytest.mark.usefixtures("session_environment")
@pytest.mark.parametrize("engine", ["codex", "claude", "agy"])
@pytest.mark.parametrize("fast_mode", [True, False, None])
async def test_public_show_preserves_latest_speed_in_live_and_retained_results(
    engine: str,
    fast_mode: bool | None,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """実際の共有状態と保持結果を公開showへ渡し、他engineと旧形式へ速度項目を加えない。"""
    session = state.SessionState(
        "fast-session",
        str(tmp_path),
        engine=engine,
        model="model",
        effort="medium",
        model_type="high_tier",
        fast_mode=fast_mode,
        announced=True,
    )
    writer = status_file.StatusFileWriter(
        {session.session_id: session},
        status_file.StatusFileIdentity("root-session", "fast.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    writer.activate()
    try:
        with pytest.raises(SystemExit, match="0"):
            atk.main(["agents", "show", session.session_id])
        visible = json.loads(capsys.readouterr().out)
        if engine == "codex" and fast_mode is not None:
            assert visible["fast_mode"] is fast_mode
        else:
            assert "fast_mode" not in visible
        session.status = "completed"
        session.turn_completed = True
        session.agent_message = "完了"
        session.touch()
        writer.flush()
        writer.deactivate()
        with pytest.raises(SystemExit, match="0"):
            atk.main(["agents", "show", session.session_id])
        retained = json.loads(capsys.readouterr().out)
        assert retained.get("fast_mode") == visible.get("fast_mode")
        assert retained["agent_message"] == "完了"
    finally:
        writer.deactivate()


@pytest.mark.usefixtures("session_environment")
def test_agents_list_shows_tree_outside_agent_environment(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """人間が読む環境ではrootとsessionの関係を表示し、委譲プロンプトを除く。"""
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
    """未知の識別子は非0と理由で拒否し、一覧で識別子を確かめる操作を示す。"""
    with pytest.raises(SystemExit, match="2"):
        atk.main(["agents", "show", "missing"])

    error = capsys.readouterr().err
    assert error.startswith(f"unknown session: missing\n{NEXT_ACTION_PREFIX}")
    assert "atk agents list" in error


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


def _write_root_sessions(state_root: pathlib.Path, root_session_id: str, sessions: list[dict]) -> None:
    directory = status_file.status_directory(root_session_id, state_root)
    directory.mkdir(parents=True)
    (directory / "root.json").write_text(json.dumps({"version": 1, "sessions": sessions}), encoding="utf-8")


def test_agents_list_human_tree_omits_roots_without_listed_sessions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """人の端末の一覧は表示するsessionを持つrootだけを見出しにし、管理用ディレクトリをrootとして出力しない。"""
    for key in ("CLAUDE_CODE_SESSION_ID", "AGENT_TOOLKIT_OWNER_SESSION", "CODEX_THREAD_ID"):
        monkeypatch.delenv(key, raising=False)
    for name in environment.AGENT_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, "state_dir", lambda: tmp_path)
    timestamps = {"started_at": "2026-09-30T00:00:00+00:00", "updated_at": "2026-09-30T00:00:00+00:00"}
    _write_root_sessions(tmp_path, "root-running", [{"session_id": "session-running", "status": "running", **timestamps}])
    _write_root_sessions(tmp_path, "root-empty", [])
    _write_root_sessions(tmp_path, "root-done", [{"session_id": "session-done", "status": "completed", **timestamps}])
    base = tmp_path / "agents-server"
    for reserved in ("aliases", "compaction", "sessions"):
        (base / reserved).mkdir()
        (base / reserved / "entry.json").write_text(json.dumps({"version": 1, "sessions": []}), encoding="utf-8")

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "list"])
    default_output = capsys.readouterr().out
    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "list", "--include-terminated"])
    terminated_output = capsys.readouterr().out

    headings = {line for line in default_output.splitlines() if line.startswith("root ")}
    assert headings == {"root root-running"}
    headings = {line for line in terminated_output.splitlines() if line.startswith("root ")}
    assert headings == {"root root-running", "root root-done"}
    assert "session-done" in terminated_output


def test_agents_list_human_tree_reports_no_sessions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """人の端末で表示するsessionが無ければ見出しを出力せず、sessionが無いことを示して正常終了する。"""
    for key in ("CLAUDE_CODE_SESSION_ID", "AGENT_TOOLKIT_OWNER_SESSION", "CODEX_THREAD_ID"):
        monkeypatch.delenv(key, raising=False)
    for name in environment.AGENT_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, "state_dir", lambda: tmp_path)
    _write_root_sessions(tmp_path, "root-empty", [])
    (tmp_path / "agents-server" / "aliases").mkdir()

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "list"])

    assert capsys.readouterr().out == "sessionはありません\n"


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
    monkeypatch.setattr(commands.session_records, "default_claude_home", lambda: tmp_path)
    monkeypatch.setattr(commands.session_records, "default_codex_home", lambda: tmp_path)

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "logs", "session-1"])

    assert "[2026-09-24T00:00:00Z] user: 調査して" in capsys.readouterr().out


def test_agents_logs_markdown_keeps_turns_and_tool_result_together(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """公開CLIがClaudeのメタデータ、会話、思考、ツール結果を同じ形式で出力する。"""
    path = tmp_path / "projects" / "sample" / "session-1.jsonl"
    _write_jsonl(
        path,
        [
            {
                "type": "user",
                "timestamp": "2026-09-24T00:00:00Z",
                "sessionId": "session-1",
                "cwd": "/work/sample",
                "gitBranch": "develop",
                "slug": "調査記録",
                "message": {"content": [{"type": "text", "text": "調査して"}]},
            },
            {
                "type": "assistant",
                "timestamp": "2026-09-24T00:00:01Z",
                "message": {
                    "content": [
                        {"type": "text", "text": "調べます"},
                        {"type": "thinking", "thinking": "確認中"},
                        {"type": "tool_use", "id": "toolu_a", "name": "Bash", "input": {"command": "echo hi"}},
                    ]
                },
            },
            {
                "type": "user",
                "timestamp": "2026-09-24T00:00:02Z",
                "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_a", "content": [{"text": "hi"}]}]},
            },
        ],
    )
    monkeypatch.setattr(commands.session_records, "default_claude_home", lambda: tmp_path)
    monkeypatch.setattr(commands.session_records, "default_codex_home", lambda: tmp_path)

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "logs", "session-1", "--format", "markdown"])
    output = capsys.readouterr().out
    assert "# Session: 調査記録" in output
    assert "| セッションID | session-1 |" in output
    assert "| ブランチ | develop |" in output
    assert "| 期間 | 2026-09-24 00:00:00 UTC 〜 2026-09-24 00:00:02 UTC |" in output
    assert "## Human\n\n調査して" in output
    assert "## Assistant\n\n調べます" in output
    assert "<summary>Tool: Bash" in output
    assert "**Result:**\n\n```\nhi\n```" in output
    assert "<summary>Thinking</summary>" not in output

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "logs", "session-1", "--format", "markdown", "--include-thinking", "--no-tool-details"])
    concise = capsys.readouterr().out
    assert "<summary>Thinking</summary>" in concise
    assert "> Tool: Bash" in concise
    assert "**Result:**" not in concise


def test_agents_logs_markdown_renders_codex_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Codexの公開記録もClaudeと同じ見出し・表・ツール詳細で出力する。"""
    thread_id = "55555555-5555-4555-8555-555555555555"
    path = tmp_path / "sessions" / "2026" / "09" / "24" / f"rollout-2026-09-24T00-00-00-{thread_id}.jsonl"
    _write_jsonl(
        path,
        [
            {
                "type": "session_meta",
                "timestamp": "2026-09-24T00:00:00Z",
                "payload": {"cwd": "/work/codex", "timestamp": "2026-09-24T00:00:00Z", "git": {"branch": "develop"}},
            },
            {
                "type": "response_item",
                "timestamp": "2026-09-24T00:00:01Z",
                "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "調査して"}]},
            },
            {
                "type": "response_item",
                "timestamp": "2026-09-24T00:00:02Z",
                "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "調べます"}]},
            },
            {
                "type": "response_item",
                "timestamp": "2026-09-24T00:00:03Z",
                "payload": {
                    "type": "function_call",
                    "name": "shell",
                    "call_id": "call-1",
                    "arguments": '{"command": "echo hi"}',
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-09-24T00:00:04Z",
                "payload": {"type": "function_call_output", "call_id": "call-1", "output": "hi"},
            },
        ],
    )
    monkeypatch.setattr(commands.session_records, "default_claude_home", lambda: tmp_path / "claude")
    monkeypatch.setattr(commands.session_records, "default_codex_home", lambda: tmp_path)

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "logs", thread_id, "--format", "markdown"])
    output = capsys.readouterr().out
    assert "| プロジェクト | /work/codex |" in output
    assert "| ブランチ | develop |" in output
    assert "## Human\n\n調査して" in output
    assert "## Assistant\n\n調べます" in output
    assert "<summary>Tool: shell</summary>" in output
    assert "**Result:**\n\n```\nhi\n```" in output


def test_agents_logs_reads_subagent_and_appends_it_to_parent_markdown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """サブエージェントのIDと親からの双方で同じ保存記録へ到達する。"""
    parent = tmp_path / "projects" / "sample" / "parent.jsonl"
    _write_jsonl(parent, [{"type": "user", "timestamp": "2026-09-24T00:00:00Z", "message": {"content": "親の依頼"}}])
    child_id = "agent-a7157f79d55caab81"
    child = parent.with_suffix("") / "subagents" / f"{child_id}.jsonl"
    _write_jsonl(child, [{"type": "user", "timestamp": "2026-09-24T00:00:01Z", "message": {"content": "子の依頼"}}])
    child.with_name(f"{child_id}.meta.json").write_text(
        json.dumps({"description": "記録調査", "agentType": "Explore"}), encoding="utf-8"
    )
    monkeypatch.setattr(commands.session_records, "default_claude_home", lambda: tmp_path)
    monkeypatch.setattr(commands.session_records, "default_codex_home", lambda: tmp_path)

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "logs", child_id])
    assert "子の依頼" in capsys.readouterr().out

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "logs", "parent", "--format", "markdown", "--include-subagents"])
    output = capsys.readouterr().out
    assert "## Subagent: 記録調査" in output
    assert "Type: Explore" in output
    assert "### Human\n\n子の依頼" in output


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["one", "--all"],
        ["one", "--project-dir", "/work"],
        ["one", "--latest", "1"],
        ["one", "--format", "markdown", "--follow"],
        ["--all", "--format", "markdown", "--follow"],
        ["one", "--include-thinking"],
    ],
)
def test_agents_logs_rejects_incompatible_scopes(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    """対象の排他、件数、追尾とmarkdown専用オプションの受理条件を確かめ、受理される指定を次の操作で示す。"""
    with pytest.raises(SystemExit, match="2"):
        atk.main(["agents", "logs", *argv])
    error = capsys.readouterr().err
    assert "error:" in error
    assert f"\n{NEXT_ACTION_PREFIX}" in error


def test_agents_logs_bulk_export_filters_projects_and_preserves_existing_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """ClaudeとCodexを全件変換し、同日時名の衝突と既存ファイルを安全に扱う。"""
    project = tmp_path / "work" / "sample"
    encoded = str(project).replace("/", "-").replace(".", "-")
    claude_root = tmp_path / "claude" / "projects" / encoded
    for name in ("claude-a", "claude-b"):
        _write_jsonl(
            claude_root / f"{name}.jsonl",
            [{"type": "user", "timestamp": "2026-09-24T00:00:00Z", "cwd": str(project), "message": {"content": name}}],
        )
    for day, cwd in (("25", str(project)), ("26", "/other")):
        thread_id = f"55555555-5555-4555-8555-5555555555{day}"
        path = tmp_path / "codex" / "sessions" / "2026" / "09" / day / f"rollout-2026-09-{day}T00-00-00-{thread_id}.jsonl"
        _write_jsonl(
            path,
            [
                {"type": "session_meta", "timestamp": f"2026-09-{day}T00:00:00Z", "payload": {"cwd": cwd}},
                {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"text": cwd}]}},
            ],
        )
    monkeypatch.setattr(commands.session_records, "default_claude_home", lambda: tmp_path / "claude")
    monkeypatch.setattr(commands.session_records, "default_codex_home", lambda: tmp_path / "codex")
    output_dir = tmp_path / "exports"

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "logs", "--all", "--format", "markdown", "--output-dir", str(output_dir)])
    assert len(list(output_dir.glob("*.md"))) == 4
    assert (output_dir / "20260924_000000.md").is_file()
    assert len(list(output_dir.glob("20260924_000000_*.md"))) == 1
    first_contents = {path.name: path.read_text(encoding="utf-8") for path in output_dir.glob("*.md")}
    capsys.readouterr()

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "logs", "--project-dir", str(project), "--latest", "1", "--format", "markdown"])
    selected = capsys.readouterr().out
    assert "/work" in selected
    assert "2026-09-25" in selected
    assert "2026-09-26" not in selected
    assert "claude-a" not in selected

    with pytest.raises(SystemExit, match="2"):
        atk.main(["agents", "logs", "--all", "--format", "markdown", "--output-dir", str(output_dir)])
    assert "--output-dir" in capsys.readouterr().err.split(NEXT_ACTION_PREFIX, 1)[1]
    assert {path.name: path.read_text(encoding="utf-8") for path in output_dir.glob("*.md")} == first_contents


def test_agents_logs_reports_missing_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """存在しない記録は識別子を添えて報告し、一覧で識別子を確かめる操作を示す。"""
    monkeypatch.setattr(commands.session_records, "default_claude_home", lambda: tmp_path)
    monkeypatch.setattr(commands.session_records, "default_codex_home", lambda: tmp_path)

    for argv in (["missing"], ["missing", "--format", "markdown"]):
        with pytest.raises(SystemExit, match="2"):
            atk.main(["agents", "logs", *argv])

        error = capsys.readouterr().err
        assert "missing" in error
        assert "atk agents list --include-terminated" in error.split(NEXT_ACTION_PREFIX, 1)[1]


def test_agents_logs_shows_first_of_ambiguous_codex_records(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Codexの記録が複数一致する識別子でも、従来どおり先頭の記録を表示する。"""
    thread_id = "55555555-5555-4555-8555-555555555555"
    for day, text in (("01", "先頭の記録"), ("02", "後続の記録")):
        rollout = tmp_path / "sessions" / "2026" / "09" / day / f"rollout-2026-09-{day}T00-00-00-{thread_id}.jsonl"
        rollout.parent.mkdir(parents=True)
        rollout.write_text(
            json.dumps(
                {
                    "type": "response_item",
                    "timestamp": f"2026-09-{day}T00:00:00Z",
                    "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]},
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
    monkeypatch.setattr(commands.session_records, "default_claude_home", lambda: tmp_path / "claude")
    monkeypatch.setattr(commands.session_records, "default_codex_home", lambda: tmp_path)
    monkeypatch.setattr(config, "state_dir", lambda: tmp_path / "state")

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "logs", thread_id])

    output = capsys.readouterr().out
    assert "先頭の記録" in output
    assert "後続の記録" not in output


def test_agents_logs_reads_and_follows_antigravity_events(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Antigravityの保存済み出力と、その後に追記された行を順に表示する。"""
    monkeypatch.setattr(commands.session_records, "default_claude_home", lambda: tmp_path)
    monkeypatch.setattr(commands.session_records, "default_codex_home", lambda: tmp_path)
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

    monkeypatch.setattr(commands.time, "sleep", append_then_stop)

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "logs", "agy-1", "--follow"])

    output = capsys.readouterr().out
    assert "init: agy-1" in output
    assert "step_update: 調査中" in output


def _human_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in environment.AGENT_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(name, raising=False)


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
                "type": state.USAGE_LIMIT_ERROR_TYPE,
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
    """表示対象のsessionが無いrootだけなら、見出しを出力せずsessionが無いことを示す。"""
    now = datetime.datetime(2026, 9, 13, tzinfo=datetime.UTC)

    assert commands._human_tree([("root-empty", [])], columns=80, now=now) == "sessionはありません"  # pylint: disable=protected-access


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
    monkeypatch.setattr(config, "state_dir", lambda: tmp_path)
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
            path = status_file.status_directory("root-a", tmp_path) / "root.json"
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
