# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""待機CLIの対象登録と入力の検証を行う。"""

import json
import pathlib

import pytest

from agent_toolkit import atk
from agent_toolkit._agents_server import agents_wait, shared_layout, wait_targets
from agent_toolkit._common import session_registry
from agent_toolkit._common.next_action import NEXT_ACTION_PREFIX
from agent_toolkit._testing.agents_server_support import exited_process_identity
from agent_toolkit._testing.agents_wait_support import (
    _publish_result_on_sleep,
    _running_body,
    _wait_for_session_1_result,
    _write_own_status,
)
from agent_toolkit._testing.agents_wait_support import _short_wait as _short_wait


def test_agents_wait_reports_absent_targets_as_failure(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """root対応を確認できない空状態はMCP一覧による復旧を案内する。"""
    assert not wait_environment.exists()

    with pytest.raises(SystemExit, match="4"):
        atk.main(["agents", "wait"])

    captured = capsys.readouterr()
    assert not captured.out
    assert "CLIが解決したroot=root-session" in captured.err
    assert "MCPの`list`を1回" in captured.err
    assert "`atk agents wait`を再実行" in captured.err
    assert "待機対象の登録が0件" not in captured.err


def test_agents_wait_reports_confirmed_empty_root_as_absent_targets(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """確認済みrootで待機対象も保持sessionも無い場合は既存の終了区分を保つ。"""
    _write_own_status(wait_environment, [])

    with pytest.raises(SystemExit, match="10"):
        atk.main(["agents", "wait"])

    captured = capsys.readouterr()
    assert not captured.out
    assert "待機対象の登録が0件" in captured.err
    assert "保持中のsessionも0件" in captured.err


def test_agents_wait_keeps_waiting_once_a_target_is_registered(
    monkeypatch: pytest.MonkeyPatch,
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """待機対象を1件でも取得した待機へは対象不在の上限を適用しない。"""
    _write_own_status(wait_environment, [{"session_id": "session-1"}])
    monkeypatch.setattr(agents_wait, "get_cli_wait_timeout", lambda _bucket: 0.0)

    with pytest.raises(SystemExit, match="3"):
        atk.main(["agents", "wait"])

    captured = capsys.readouterr()
    assert _running_body(json.loads(captured.out)) == {"session_id": "session-1", "status": "running"}
    assert not captured.err


def test_agents_wait_keeps_waiting_for_starting_session(
    monkeypatch: pytest.MonkeyPatch,
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """startingのsessionが保持中なら対象不在として即時終了しない。"""
    _write_own_status(wait_environment, [{"session_id": "session-1", "status": "starting"}])
    monkeypatch.setattr(agents_wait, "get_cli_wait_timeout", lambda _bucket: 0.0)

    with pytest.raises(SystemExit, match="3"):
        atk.main(["agents", "wait"])

    captured = capsys.readouterr()
    assert _running_body(json.loads(captured.out)) == {"session_id": "session-1", "status": "running"}
    assert not captured.err


def test_agents_wait_keeps_captured_session_after_projection_disappears(
    monkeypatch: pytest.MonkeyPatch,
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """待機開始後に状態投影が消えても取得済みsessionを未終了として返す。"""
    status_path = _write_own_status(wait_environment, [{"session_id": "session-1"}])
    monotonic_values = iter([0.0, 0.0, 2.0])
    monkeypatch.setattr(agents_wait, "get_cli_wait_timeout", lambda _bucket: 1.0)
    monkeypatch.setattr(agents_wait.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(agents_wait.time, "sleep", lambda _seconds: status_path.unlink())

    with pytest.raises(SystemExit, match="3"):
        atk.main(["agents", "wait"])

    captured = capsys.readouterr()
    assert _running_body(json.loads(captured.out)) == {"session_id": "session-1", "status": "running"}
    assert not captured.err


def test_agents_wait_keeps_waiting_for_retained_session(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """状態ファイルが保持するsessionを消失として返さない。"""
    _write_own_status(wait_environment, [{"session_id": "session-1"}])

    with pytest.raises(SystemExit, match="3"):
        atk.main(["agents", "wait"])

    captured = capsys.readouterr()
    assert _running_body(json.loads(captured.out)) == {"session_id": "session-1", "status": "running"}
    assert not captured.err


def test_agents_wait_reports_absence_without_own_status_file(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """自身の書込主体がsessionを保持しない場合は別主体の投影を待機対象にしない。"""
    nested_status = wait_environment.parent / "child-session.json"
    nested_status.parent.mkdir(parents=True)
    nested_status.write_text(
        json.dumps({"version": 1, "sessions": [{"session_id": "session-1"}]}),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="10"):
        atk.main(["agents", "wait"])

    captured = capsys.readouterr()
    assert not captured.out
    assert "保持中のsessionも0件" in captured.err


@pytest.mark.parametrize("root_body", ["{", "[]"])
def test_agents_wait_ignores_unreadable_root_status(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    root_body: str,
) -> None:
    """状態ファイルを解釈できない場合は従来の待機を継続する。"""
    root_status = wait_environment.parent / "root.json"
    root_status.parent.mkdir(parents=True)
    root_status.write_text(root_body, encoding="utf-8")

    with pytest.raises(SystemExit, match="3"):
        atk.main(["agents", "wait"])

    captured = capsys.readouterr()
    assert _running_body(json.loads(captured.out)) == {"status": "running"}
    assert not captured.err


@pytest.mark.parametrize("result_body", ["[]", "{"])
def test_agents_wait_rejects_corrupted_result(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    result_body: str,
) -> None:
    """破損した終了時の結果は待機上限と別の終了コードで停止する。"""
    wait_environment.mkdir(parents=True)
    (wait_environment / "session-1.json").write_text(result_body, encoding="utf-8")

    with pytest.raises(SystemExit, match="6"):
        atk.main(["agents", "wait"])

    captured = capsys.readouterr()
    assert not captured.out
    assert str(wait_environment / "session-1.json") in captured.err


@pytest.mark.parametrize("session_id", ["../outside", ""])
def test_agents_wait_rejects_invalid_session_in_status_file(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    session_id: str,
) -> None:
    """形式外のsession識別子を結果ファイルの解決前に拒否する。"""
    _write_own_status(wait_environment, [{"session_id": session_id}])

    with pytest.raises(SystemExit, match="5"):
        atk.main(["agents", "wait"])

    captured = capsys.readouterr()
    assert not captured.out
    assert "session_idの形式が不正" in captured.err
    assert f"\n{NEXT_ACTION_PREFIX}" in captured.err
    assert "`list`" in captured.err


def test_agents_wait_rejects_turn(capsys: pytest.CaptureFixture[str]) -> None:
    """撤去したturn指定はargparseの終了コード2で拒否する。"""
    with pytest.raises(SystemExit, match="2"):
        atk.main(["agents", "wait", "--" + "turn=1"])
    assert not capsys.readouterr().out


def test_agents_wait_rejects_positional_session(capsys: pytest.CaptureFixture[str]) -> None:
    """撤去した待機対象の位置引数はargparseの終了コード2で拒否する。"""
    with pytest.raises(SystemExit, match="2"):
        atk.main(["agents", "wait", "session-1"])
    assert not capsys.readouterr().out


@pytest.mark.parametrize(
    "environment",
    [{}, {"AGENT_TOOLKIT_OWNER_SESSION": "root-session"}],
    ids=["no-root-session", "owner-without-host-session"],
)
def test_missing_state_dir_reports_alternative(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    environment: dict[str, str],
) -> None:
    """状態ファイルの書込主体を解決できない場合は終了コード4を返す。"""
    assert agents_wait.wait_for_result(environment=environment, state_root=tmp_path) == 4

    captured = capsys.readouterr()
    assert not captured.out
    assert "状態ディレクトリを解決できません" in captured.err
    assert f"\n{NEXT_ACTION_PREFIX}" in captured.err
    assert "`atk agents wait --root-session-id <値>`" in captured.err


def test_agents_wait_returns_notices_while_running(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """終了前の通知を回収し、running状態とともに1行で返す。"""
    _write_own_status(wait_environment, [{"session_id": "session-1"}])
    notices = wait_environment.parent / "notices"
    notices.mkdir(parents=True)
    for sequence, sent_at, body in (
        (2, "2026-09-07T00:00:02+00:00", "後の通知"),
        (1, "2026-09-07T00:00:01+00:00", "先の通知"),
    ):
        payload = {"version": 1, "session_id": "session-1", "sent_at": sent_at, "body": body}
        (notices / f"session-1.{sequence}.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "wait"])

    response = _running_body(json.loads(capsys.readouterr().out))
    assert response == {
        "session_id": "session-1",
        "status": "running",
        "notices": [
            {"body": "先の通知"},
            {"body": "後の通知"},
        ],
    }
    assert not any(notices.iterdir())


def test_agents_wait_reissues_for_registered_session_after_notice(
    monkeypatch: pytest.MonkeyPatch,
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """通知後に状態投影が消えても、再発行した待機が同じsessionの終了時の結果を回収する。"""
    session_registry.publish(
        "session-1", state=session_registry.PublicationState.PENDING_RUNNING, state_root=wait_environment.parents[2]
    )
    status_path = _write_own_status(wait_environment, [{"session_id": "session-1"}])
    notices = wait_environment.parent / "notices"
    notices.mkdir(parents=True)
    (notices / "session-1.1.json").write_text(
        json.dumps(
            {
                "version": 1,
                "session_id": "session-1",
                "sent_at": "2026-09-14T00:00:00+00:00",
                "body": "通知",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "wait"])
    assert json.loads(capsys.readouterr().out)["status"] == "running"
    status_path.unlink()

    monkeypatch.setattr(agents_wait, "get_cli_wait_timeout", lambda _bucket: 10.0)
    monkeypatch.setattr(agents_wait.time, "sleep", _publish_result_on_sleep(wait_environment))

    assert _wait_for_session_1_result(wait_environment) == 0
    assert json.loads(capsys.readouterr().out) == {"status": "completed", "session_id": "session-1"}


@pytest.mark.parametrize("released", [False, True], ids=["missing", "released"])
def test_agents_wait_releases_registered_session_missing_from_registry(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    released: bool,
) -> None:
    """状態・結果・session登録簿から失われた待機対象を再発行時に解放する。

    所有側が解放した解放済みレコードも、レコードの不在と同じく解放の対象とする。
    """
    wait_targets.retain_wait_targets("root-session", "root.json", ["session-1"], wait_environment.parents[2])
    if released:
        session_registry.release("session-1", reason="retention_expired", state_root=wait_environment.parents[2])

    with pytest.raises(SystemExit, match="10"):
        atk.main(["agents", "wait"])

    captured = capsys.readouterr()
    assert not captured.out
    assert "待機対象の登録が0件" in captured.err
    retained, error = wait_targets.read_wait_targets(
        "root-session",
        "root.json",
        wait_environment.parents[2],
    )
    assert retained == set()
    assert error is None


@pytest.mark.parametrize("record", ["terminal", "processes-exited"])
def test_agents_wait_releases_registered_session_terminal_in_registry(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    record: str,
) -> None:
    """登録簿が終了を示し、結果も状態ファイルの行も無い待機対象を解放して待たずに終える。

    所有プロセスと委譲先CLIがともに終了した`running`の記録も、解決が示す中断の終了として同じく扱う。
    """
    state_root = wait_environment.parents[2]
    wait_targets.retain_wait_targets("root-session", "root.json", ["session-1"], state_root)
    if record == "terminal":
        session_registry.publish("session-1", state=session_registry.PublicationState.RESULT_INTERRUPTED, state_root=state_root)
    else:
        processes = (exited_process_identity(), exited_process_identity())
        session_registry.publish(
            "session-1", state=session_registry.PublicationState.PENDING_RUNNING, processes=processes, state_root=state_root
        )

    with pytest.raises(SystemExit, match="10"):
        atk.main(["agents", "wait"])

    captured = capsys.readouterr()
    assert not captured.out
    assert "待機対象の登録が0件" in captured.err
    retained, error = wait_targets.read_wait_targets("root-session", "root.json", state_root)
    assert retained == set()
    assert error is None


@pytest.mark.parametrize("record", ["terminal", "processes-exited"])
def test_agents_wait_ends_when_only_target_becomes_terminal_without_result(
    monkeypatch: pytest.MonkeyPatch,
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    record: str,
) -> None:
    """待機中に唯一の対象が結果を残さず終了した場合は、待機上限を待たずに対象不在で終える。

    所有プロセスと委譲先CLIがともに終了して`running`のまま残った記録も、同じく終了として扱う。
    """
    state_root = wait_environment.parents[2]
    wait_targets.retain_wait_targets("root-session", "root.json", ["session-1"], state_root)
    session_registry.publish("session-1", state=session_registry.PublicationState.PENDING_RUNNING, state_root=state_root)
    # 上限到達による終了と区別するため、待機上限をテストの実行時間より十分大きくする。
    monkeypatch.setattr(agents_wait, "get_cli_wait_timeout", lambda _bucket: 3600.0)
    sleeps: list[float] = []

    def terminate_on_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if record == "terminal":
            session_registry.publish(
                "session-1", state=session_registry.PublicationState.RESULT_INTERRUPTED, state_root=state_root
            )
        else:
            processes = (exited_process_identity(),)
            session_registry.publish(
                "session-1", state=session_registry.PublicationState.PENDING_RUNNING, processes=processes, state_root=state_root
            )

    monkeypatch.setattr(agents_wait.time, "sleep", terminate_on_sleep)

    with pytest.raises(SystemExit, match="10"):
        atk.main(["agents", "wait"])

    captured = capsys.readouterr()
    assert not captured.out
    assert "待機対象の登録が0件" in captured.err
    assert len(sleeps) == 1
    retained, error = wait_targets.read_wait_targets("root-session", "root.json", state_root)
    assert retained == set()
    assert error is None


def test_agents_wait_collects_registered_terminal_result_and_releases_target(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """PostToolUseが保持した対象の終了時の結果を回収し、待機対象登録を解除する。"""
    state_root = wait_environment.parents[2]
    wait_targets.retain_wait_targets("root-session", "root.json", ["session-1"], state_root)
    wait_environment.mkdir(parents=True)
    (wait_environment / "session-1.json").write_text(
        json.dumps({"status": "completed"}),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "wait"])

    assert json.loads(capsys.readouterr().out) == {"status": "completed", "session_id": "session-1"}
    retained, error = wait_targets.read_wait_targets("root-session", "root.json", state_root)
    assert retained == set()
    assert error is None


def test_agents_wait_releases_corrupted_wait_target(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """読取不能な待機対象登録を解放し、理由を伴う終了コード9で返す。"""
    directory = shared_layout.wait_targets_directory("root-session", "root.json", wait_environment.parents[2])
    directory.mkdir(parents=True)
    marker = directory / "session-1.json"
    marker.write_text("{", encoding="utf-8")

    with pytest.raises(SystemExit, match="9"):
        atk.main(["agents", "wait"])

    captured = capsys.readouterr()
    assert not captured.out
    assert "待機対象登録簿を読めません" in captured.err
    assert f"\n{NEXT_ACTION_PREFIX}" in captured.err
    assert "`list`" in captured.err
    assert not marker.exists()
