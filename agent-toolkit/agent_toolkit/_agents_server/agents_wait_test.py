# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""待機CLIの終了結果と配送案内を検証する。"""

import json
import pathlib
from typing import Any

import pytest

from agent_toolkit import atk
from agent_toolkit._agents_server import agents_wait, result_projection, shared_layout, state
from agent_toolkit._atk import managed_temp
from agent_toolkit._common.next_action import NEXT_ACTION_PREFIX
from agent_toolkit._testing.agents_wait_support import _running_body, _wait_for_session_1_result, _write_own_status
from agent_toolkit._testing.agents_wait_support import _short_wait as _short_wait
from agent_toolkit._testing.managed_temp_support import setattr_in_managed_temp_modules


@pytest.mark.parametrize("agent", [False, True], ids=["human", "agent"])
@pytest.mark.parametrize("status", ["completed", "failed"])
def test_cli_wait_reports_waiting_state_to_human_terminal(monkeypatch, wait_environment, capsys, agent, status):
    """終了を公開する前に対象を表示し、公開後はJSON Linesを保つ。"""
    _write_own_status(wait_environment, [{"session_id": "session-1"}])
    monkeypatch.setattr(agents_wait, "get_cli_wait_timeout", lambda _bucket: 30)
    monkeypatch.setattr(agents_wait, "is_human_terminal", lambda: not agent)
    observed = []

    def release_result(_seconds):
        observed.append(capsys.readouterr())
        wait_environment.mkdir(parents=True, exist_ok=True)
        (wait_environment / "session-1.json").write_text(json.dumps({"status": status}), encoding="utf-8")

    monkeypatch.setattr(agents_wait.time, "sleep", release_result)
    assert _wait_for_session_1_result(wait_environment) == 0
    assert observed and not observed[0].out
    if agent:
        assert not observed[0].err
    else:
        assert "待機中" in observed[0].err and "session-1" in observed[0].err
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == status and result["session_id"] == "session-1"
    if status == "failed":
        assert result["next_action"]


def test_agents_wait_outputs_every_retained_result(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """未回収の終了時の結果を識別子順のJSON Linesで全件返し、回収した結果ファイルを残さない。

    作業の失敗で終了した結果には、同じsessionへの継続か巻き取りの次の操作が付く。
    欠けると委譲元は失敗の結果だけを受け取り、同じ候補での再起動を繰り返し得る。
    """
    wait_environment.mkdir(parents=True)
    first = {"session_id": "session-1", "status": "completed", "turn_seq": 2}
    second = {"session_id": "session-2", "status": "failed"}
    (wait_environment / "session-1.json").write_text(json.dumps(first), encoding="utf-8")
    (wait_environment / "session-2.json").write_text(json.dumps(second), encoding="utf-8")

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "wait"])

    captured = capsys.readouterr()
    completed, failed = [json.loads(line) for line in captured.out.splitlines()]
    assert completed == {"session_id": "session-1", "status": "completed"}
    assert "turn_seq" not in completed
    next_action = failed.pop("next_action")
    assert failed == second
    assert "`send_message`" in next_action
    assert "作業を巻き取る" in next_action
    assert "handoff_from" not in next_action
    assert not captured.err
    assert not (wait_environment / "session-1.json").exists()
    assert not (wait_environment / "session-2.json").exists()


@pytest.mark.parametrize("turn_seq", [1, 2, 4], ids=["initial-turn", "followup-turn", "auto-resumed-turn"])
@pytest.mark.parametrize(
    ("engine", "error", "handoff"),
    [
        ("codex", {"codexErrorInfo": "usageLimitExceeded"}, True),
        ("codex", {"codexErrorInfo": "serverOverloaded"}, True),
        ("codex", {"codexErrorInfo": "rateLimitExceeded"}, True),
        ("claude", {"apiErrorStatus": 429}, True),
        ("claude", {"apiErrorStatus": 401}, True),
        ("claude", {"apiErrorStatus": 403}, True),
        ("claude", {"apiErrorStatus": 529}, True),
        ("claude", {"apiErrorStatus": 429, "usageLimit": {"type": "five_hour"}}, False),
        ("agy", {"stderr": "capacity exhausted"}, True),
        ("codex", {"message": "作業の検証失敗"}, False),
    ],
)
def test_agents_wait_handoff_guidance_matches_backend_availability(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    engine: str,
    error: dict[str, object],
    handoff: bool,
    turn_seq: int,
) -> None:
    """全turnの可用性失敗で前任ID付きstartを案内し、作業失敗と解除待ちは継続・巻き取りを保つ。"""
    wait_environment.mkdir(parents=True)
    payload = {"status": "failed", "agent_message": "進捗全文", "engine": engine, "error": error, "turn_seq": turn_seq}
    (wait_environment / "predecessor.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "wait"])
    result = json.loads(capsys.readouterr().out)
    action = result["next_action"]
    if handoff:
        assert '{"handoff_from": "predecessor"}' in action
        assert "send_message" not in action
    else:
        assert "send_message" in action
        assert "handoff_from" not in action
    assert result["agent_message"] == "進捗全文"


def test_wait_result_preserves_started_model() -> None:
    """終了回収でモデル指定と実際の起動属性を保持する。"""
    session = state.SessionState(
        "session-1", "/workspace", engine="claude", model="opus", effort="high", model_type="claude:opus/high"
    )
    session.status = "completed"
    session.agent_message = "完了"
    payload = state.terminal_result_payload(session)
    assert agents_wait._public_wait_response(payload) == {  # pylint: disable=protected-access
        "status": "completed",
        "agent_message": "完了",
        "engine": "claude",
        "model": "opus",
        "effort": "high",
        "model_type": "claude:opus/high",
    }


def test_agents_wait_rows_carry_launch_label_and_body_file(
    monkeypatch: pytest.MonkeyPatch,
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """終了行と`status: running`の行が起動時の`label`を持ち、終了行の本文ファイルは`agent_message`と一致する。

    `label`が無いと、呼び出し元は`session_id`から依頼名への対応表を自前で持つ。
    本文がJSON文字列にだけあると、呼び出し元はエスケープを解く処理を自前で持つ。
    """
    wait_environment.mkdir(parents=True)
    message = '## 結果\n\n- `a`と"b"を確認した\n'
    (wait_environment / "session-1.json").write_text(
        json.dumps({"status": "completed", "agent_message": message, "turn_seq": 3, "session": {"label": "依頼A"}}),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "wait"])
    terminal = json.loads(capsys.readouterr().out)
    assert terminal["label"] == "依頼A"
    body_path = pathlib.Path(terminal["agent_message_path"])
    assert body_path.name == "session-1.md"
    assert body_path.read_text(encoding="utf-8") == message == terminal["agent_message"]

    _write_own_status(wait_environment, [{"session_id": "session-2", "status": "running", "label": "依頼B"}])
    monkeypatch.setattr(agents_wait, "get_cli_wait_timeout", lambda _bucket: 0.0)
    with pytest.raises(SystemExit, match="3"):
        atk.main(["agents", "wait"])
    running = _running_body(json.loads(capsys.readouterr().out))
    assert running["session_id"] == "session-2"
    assert running["label"] == "依頼B"


def test_agents_wait_redelivers_result_after_body_file_failure(
    monkeypatch: pytest.MonkeyPatch,
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """本文ファイルを書けない待機は非0で終わり、再発行した待機が同じ終了行を配送する。

    配送前に終えずに失敗を無視すると本文ファイルの無い行が届き、退避物を削除してから失敗すると結果が失われる。
    """
    _write_own_status(wait_environment, [{"session_id": "session-1", "status": "running"}])
    wait_environment.mkdir(parents=True, exist_ok=True)
    terminal = {"status": "completed", "agent_message": "結果本文", "turn_seq": 1}
    (wait_environment / "session-1.json").write_text(json.dumps(terminal), encoding="utf-8")
    original_resolve = managed_temp.resolve_session_temp
    original_create = managed_temp.create_managed_temp

    def fail_create(*_args: Any, **_kwargs: Any) -> pathlib.Path:
        raise managed_temp.ManagedTempError("容量不足")

    setattr_in_managed_temp_modules(monkeypatch, "resolve_session_temp", fail_create)
    setattr_in_managed_temp_modules(monkeypatch, "create_managed_temp", fail_create)
    with pytest.raises(SystemExit) as failed:
        atk.main(["agents", "wait"])
    first = capsys.readouterr()
    assert failed.value.code not in (0, 3)
    assert not first.out
    assert "結果本文のファイルを書けません" in first.err
    assert NEXT_ACTION_PREFIX in first.err

    setattr_in_managed_temp_modules(monkeypatch, "resolve_session_temp", original_resolve)
    setattr_in_managed_temp_modules(monkeypatch, "create_managed_temp", original_create)
    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "wait"])
    delivered = json.loads(capsys.readouterr().out)
    assert pathlib.Path(delivered.pop("agent_message_path")).read_text(encoding="utf-8") == "結果本文"
    assert delivered == {"session_id": "session-1", "status": "completed", "agent_message": terminal["agent_message"]}


@pytest.mark.parametrize(
    ("label", "status", "expected"),
    [
        ("slides-review", "completed", "review"),
        ("lane-01-exec-review", "completed", "review"),
        ("reader-fit-review", "completed", "reader-fit-review"),
        ("lane-01-reader-fit-review", "completed", "reader-fit-review"),
        ("reader-fit-review", "failed", "failed"),
        ("reader-fit-review", "interrupted", "interrupted"),
        ("explore-pyfltr", "completed", None),
        ("slides-review", "failed", "failed"),
        ("slides-review", "interrupted", "interrupted"),
        (None, "completed", None),
    ],
)
def test_agents_wait_guides_review_result_before_deciding_findings(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    label: str | None,
    status: str,
    expected: str | None,
) -> None:
    """labelが`-review`で終わる完了結果だけへ、review-standardsで採否を確定する次の操作を加える。

    失敗と中断の結果は既存の次の操作のままとし、レビュー以外の完了結果へは次の操作を加えない。
    """
    wait_environment.mkdir(parents=True)
    payload: dict[str, Any] = {"status": status, "agent_message": "指摘1件"}
    if label is not None:
        payload["session"] = {"session_id": "session-1", "label": label}
    (wait_environment / "session-1.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "wait"])

    result = json.loads(capsys.readouterr().out.splitlines()[0])
    assert "session" not in result
    next_action = result.get("next_action")
    if expected is None:
        assert next_action is None
    elif expected == "review":
        assert next_action == result_projection.REVIEW_RESULT_NEXT_ACTION
        for fragment in ("`agent-basics:review-standards`", "`references/reviewee.md`", "合意を示すユーザー発話を特定"):
            assert fragment in next_action
    elif expected == "reader-fit-review":
        assert next_action == result_projection.READER_FIT_REVIEW_RESULT_NEXT_ACTION
        assert "`agent-basics:review-standards`" in next_action
        assert "`reader-fit-review.parent.md`「受領」" in next_action
        assert "reviewee.md" not in next_action
    else:
        assert next_action is not None
        assert next_action != result_projection.REVIEW_RESULT_NEXT_ACTION


@pytest.mark.parametrize("label", ["add-wi", "lane-01-exec-review", "lane-01-reader-fit-review"])
@pytest.mark.parametrize("status", ["completed", "failed", "interrupted"])
@pytest.mark.parametrize(
    "message",
    [
        "完了",
        "本文中の気付いた改善点: は標識ではない",
        "完了\n  気付いた改善点: 反復した\n気付いた改善点: 再読した",
        "完了\n- 気付いた改善点: 箇条書きで報告した",
        "完了\n2) 気付いた改善点: 番号付きで報告した",
        "例:\n```text\n- 気付いた改善点: コード例\n```",
        "例:\n> - 気付いた改善点: 引用した例\n    気付いた改善点: 字下げしたコード例",
    ],
)
def test_agents_wait_relays_improvements_with_existing_guidance(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    label: str,
    status: str,
    message: str,
) -> None:
    """公開waitの出力が標識行の案内と既存の案内を保持し、結果本文と保存本文を変えない。"""
    wait_environment.mkdir(parents=True)
    payload = {"status": status, "agent_message": message, "next_action": "既存の案内", "session": {"label": label}}
    (wait_environment / "session-1.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "wait"])

    captured = capsys.readouterr()
    result = json.loads(captured.out)
    action = result["next_action"]
    assert "既存の案内" in action
    marked = message.startswith("完了\n")
    assert (result_projection.IMPROVEMENT_RESULT_NEXT_ACTION in action) is marked
    if marked:
        assert action.count(result_projection.IMPROVEMENT_RESULT_NEXT_ACTION) == 1
        for fragment in (
            "メインエージェントは次のユーザーへの発話へ",
            "委譲先は受領時点で上流へ逐語で中継し、完了返却の末尾にも残す",
            "字下げを除く行頭に`気付いた改善点:`を原文のまま置き",
            "標識とコロンの間へ報告元などの語を入れない",
            "標識行の原文の後ろか別の行に添える",
            "`agent-toolkit:delegation`の`references/receiving.md`「受領後の扱い」",
        ):
            assert fragment in action
    if status == "completed" and label.endswith("-review"):
        if label.endswith("reader-fit-review"):
            assert result_projection.READER_FIT_REVIEW_RESULT_NEXT_ACTION in action
            assert "reviewee.md" not in action
        else:
            assert result_projection.REVIEW_RESULT_NEXT_ACTION in action
    elif status == "failed":
        assert "失敗の種類を確かめる" in action
    elif status == "interrupted":
        assert "中断を要求していない場合" in action
    assert result["agent_message"] == message
    assert pathlib.Path(result["agent_message_path"]).read_text(encoding="utf-8") == message
    assert not captured.err


def test_agents_wait_uses_explicit_root_without_environment(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """起動応答のルートを明示すれば環境の別名なしで終了時の結果を回収できる。"""
    results = shared_layout.results_directory("mcp-root", tmp_path)
    _write_own_status(results, [{"session_id": "session-1"}])
    results.mkdir(parents=True, exist_ok=True)
    (results / "session-1.json").write_text(json.dumps({"status": "completed"}), encoding="utf-8")

    assert agents_wait.wait_for_result(environment={}, state_root=tmp_path, root_session_id="mcp-root") == 0

    assert json.loads(capsys.readouterr().out) == {"session_id": "session-1", "status": "completed"}


def test_agents_wait_delivers_collected_results_before_reporting_read_failure(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """解釈できない結果ファイルがある巡回でも、回収済みの本文を保持したまま配送する。"""
    wait_environment.mkdir(parents=True)
    payload = {"session_id": "session-1", "status": "completed"}
    (wait_environment / "session-1.json").write_text(json.dumps(payload), encoding="utf-8")
    unreadable = wait_environment / "session-2.json"
    unreadable.write_text("{不正なJSON", encoding="utf-8")

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "wait"])

    captured = capsys.readouterr()
    assert [json.loads(line) for line in captured.out.splitlines()] == [payload]
    assert not captured.err
    assert not (wait_environment / "session-1.json").exists()
    assert unreadable.exists()

    assert _wait_for_session_1_result(wait_environment) == 6
    error = capsys.readouterr().err
    assert "終了時の結果ファイルを読めません" in error
    assert f"\n{NEXT_ACTION_PREFIX}" in error
    assert "`show`" in error


def test_agents_wait_outputs_terminal_result_with_notice_only_session(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """終了時の結果を持つsessionと通知だけを持つsessionが同時にある場合は双方の行を返す。"""
    wait_environment.mkdir(parents=True)
    terminal = {"session_id": "session-1", "status": "completed"}
    (wait_environment / "session-1.json").write_text(json.dumps(terminal), encoding="utf-8")
    _write_own_status(wait_environment, [{"session_id": "session-2"}])
    notices = wait_environment.parent / "notices"
    notices.mkdir(parents=True)
    sent_at = "2026-09-15T00:00:01+00:00"
    (notices / "session-2.1.json").write_text(
        json.dumps({"version": 1, "session_id": "session-2", "sent_at": sent_at, "body": "検査コマンドが未導入"}),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "wait"])

    captured = capsys.readouterr()
    collected_terminal, running = [json.loads(line) for line in captured.out.splitlines()]
    assert collected_terminal == terminal
    assert "atk agents wait" in running.pop("next_action")
    assert running == {
        "session_id": "session-2",
        "status": "running",
        "notices": [{"body": "検査コマンドが未導入"}],
    }
    assert not (wait_environment / "session-1.json").exists()
    assert not any(notices.iterdir())
