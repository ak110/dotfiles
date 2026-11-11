# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""待機CLIの所有主体とルート解決を検証する。"""

import json
import pathlib

import pytest

from agent_toolkit import atk
from agent_toolkit._agents_server import agents_wait, shared_layout, shared_roots, wait_targets
from agent_toolkit._common import state_paths
from agent_toolkit._common.next_action import NEXT_ACTION_PREFIX
from agent_toolkit._testing.agents_wait_support import _CODEX_ROOT, _codex_top_level_state
from agent_toolkit._testing.agents_wait_support import _short_wait as _short_wait
from agent_toolkit._testing.agents_wait_support_contention import _wait_while_owner_is_locked


def test_agents_wait_does_not_consume_another_writer_result(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """同じroot配下でも別の状態書込主体が公開した結果は回収しない。"""
    root = shared_layout.status_directory("root-session", tmp_path)
    root.mkdir(parents=True)
    (root / "delegate-session.json").write_text(
        json.dumps({"version": 1, "sessions": [{"session_id": "owned-session"}]}),
        encoding="utf-8",
    )
    results = shared_layout.results_directory("root-session", tmp_path)
    results.mkdir()
    foreign_result = results / "foreign-session.json"
    foreign_result.write_text(
        json.dumps({"status": "completed", "owner_status_file": "root.json"}),
        encoding="utf-8",
    )

    assert (
        agents_wait.wait_for_result(
            environment={
                "AGENT_TOOLKIT_OWNER_SESSION": "root-session",
                "AGENT_TOOLKIT_STATUS_HOST_SESSION": "delegate-session",
            },
            state_root=tmp_path,
        )
        == 3
    )

    timed_out = json.loads(capsys.readouterr().out)
    assert "atk agents wait" in timed_out.pop("next_action")
    assert timed_out == {"session_id": "owned-session", "status": "running"}
    assert foreign_result.exists()


def test_root_wait_does_not_consume_delegate_writer_result(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """ルート待機は所有者を持つ委譲先の結果を旧形式として回収しない。"""
    root = shared_layout.status_directory("root-session", tmp_path)
    root.mkdir(parents=True)
    (root / "root.json").write_text(json.dumps({"version": 1, "sessions": []}), encoding="utf-8")
    results = shared_layout.results_directory("root-session", tmp_path)
    results.mkdir()
    delegate_result = results / "delegate-result.json"
    delegate_result.write_text(
        json.dumps({"status": "completed", "owner_status_file": "delegate-session.json"}),
        encoding="utf-8",
    )

    assert (
        agents_wait.wait_for_result(
            environment={"CLAUDE_CODE_SESSION_ID": "root-session"},
            state_root=tmp_path,
        )
        == 10
    )

    captured = capsys.readouterr()
    assert not captured.out
    assert "待機対象の登録が0件" in captured.err
    assert f"\n{NEXT_ACTION_PREFIX}" in captured.err
    assert "atk agents wait" in captured.err
    assert delegate_result.exists()


def test_delegate_wait_consumes_own_writer_result(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """委譲先待機は結果ファイルの所有者が自身と一致する結果を回収する。"""
    root = shared_layout.status_directory("root-session", tmp_path)
    root.mkdir(parents=True)
    (root / "delegate-session.json").write_text(
        json.dumps({"version": 1, "sessions": []}),
        encoding="utf-8",
    )
    results = shared_layout.results_directory("root-session", tmp_path)
    results.mkdir()
    own_result = results / "delegate-result.json"
    own_result.write_text(
        json.dumps(
            {
                "status": "completed",
                "agent_message": "委譲先の結果",
                "owner_status_file": "delegate-session.json",
            }
        ),
        encoding="utf-8",
    )

    assert (
        agents_wait.wait_for_result(
            environment={
                "AGENT_TOOLKIT_OWNER_SESSION": "root-session",
                "AGENT_TOOLKIT_STATUS_HOST_SESSION": "delegate-session",
            },
            state_root=tmp_path,
        )
        == 0
    )

    delivered = json.loads(capsys.readouterr().out)
    assert pathlib.Path(delivered.pop("agent_message_path")).read_text(encoding="utf-8") == "委譲先の結果"
    assert delivered == {
        "session_id": "delegate-result",
        "status": "completed",
        "agent_message": "委譲先の結果",
    }
    assert not own_result.exists()


def test_codex_delegate_wait_resolves_writer_alias_and_releases_target(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Codex threadと異なる書込主体の稼働状態と終了時の結果を同じ所有単位で回収する。"""
    root = shared_layout.status_directory("root-session", tmp_path)
    root.mkdir(parents=True)
    (root / "writer-session.json").write_text(
        json.dumps({"version": 1, "host_session_id": "codex-thread", "sessions": [{"session_id": "remote-session"}]}),
        encoding="utf-8",
    )
    shared_roots.write_host_alias("root-session", "writer-session", "codex-thread", tmp_path)
    environment = {"AGENT_TOOLKIT_OWNER_SESSION": "root-session", "CODEX_THREAD_ID": "codex-thread"}

    assert agents_wait.wait_for_result(environment=environment, state_root=tmp_path) == 3
    timed_out = json.loads(capsys.readouterr().out)
    timed_out.pop("next_action")
    assert timed_out == {"session_id": "remote-session", "status": "running"}

    results = shared_layout.results_directory("root-session", tmp_path)
    results.mkdir(exist_ok=True)
    result_path = results / "remote-session.json"
    result_path.write_text(
        json.dumps({"status": "completed", "owner_status_file": "writer-session.json"}),
        encoding="utf-8",
    )
    (shared_layout.hosts_directory("root-session", tmp_path) / "writer-session.json").unlink()

    assert agents_wait.wait_for_result(environment=environment, state_root=tmp_path) == 0
    assert json.loads(capsys.readouterr().out) == {"session_id": "remote-session", "status": "completed"}
    assert not result_path.exists()
    retained, error = wait_targets.read_wait_targets("root-session", "writer-session.json", tmp_path)
    assert retained == set()
    assert error is None


@pytest.mark.parametrize("root_session_id", ["../outside", "missing-root"], ids=["invalid-format", "missing-directory"])
def test_agents_wait_rejects_unusable_explicit_root_with_next_action(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    root_session_id: str,
) -> None:
    """使えない明示ルートは終了コード4で拒否し、`list`の値を渡すか指定を外す操作を示す。"""
    assert agents_wait.wait_for_result(environment={}, state_root=tmp_path, root_session_id=root_session_id) == 4

    captured = capsys.readouterr()
    assert not captured.out
    assert "--root-session-id" in captured.err.split(NEXT_ACTION_PREFIX, 1)[1]


def test_codex_delegate_wait_reports_ambiguous_writer_aliases(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """複数の書込主体へ対応するCodex threadは診断を返して待機しない。"""
    shared_roots.write_host_alias("root-session", "writer-a", "codex-thread", tmp_path)
    shared_roots.write_host_alias("root-session", "writer-b", "codex-thread", tmp_path)

    assert (
        agents_wait.wait_for_result(
            environment={"AGENT_TOOLKIT_OWNER_SESSION": "root-session", "CODEX_THREAD_ID": "codex-thread"},
            state_root=tmp_path,
        )
        == 4
    )

    error = capsys.readouterr().err
    assert "書込主体を一意に解決できません" in error
    assert f"\n{NEXT_ACTION_PREFIX}" in error
    assert "atk agents list" in error


def test_agents_wait_rejects_disjoint_sets_for_same_owner(tmp_path: pathlib.Path) -> None:
    """対象集合が別でも同じ書込主体の重複待機を許可しない。"""
    assert _wait_while_owner_is_locked(tmp_path, "session-2") == 8


def test_agents_wait_resolves_changed_conversation_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """会話のsession識別子が変わった後も索引先の終了時の結果を回収する。"""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "current-session")
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    results = shared_layout.results_directory("root-session", tmp_path)
    results.mkdir(parents=True)
    payload = {"session_id": "session-1", "status": "completed"}
    (results / "session-1.json").write_text(json.dumps(payload), encoding="utf-8")
    shared_roots.write_root_alias("current-session", "root-session", tmp_path)

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "wait"])

    assert json.loads(capsys.readouterr().out) == payload
    assert not (results / "session-1.json").exists()


def test_codex_top_level_lists_and_waits_through_hook_alias(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """PostToolUseと同じ索引の書込を経て、引数なしの`list`と`wait`がCodex最上位の会話のsessionだけを扱う。

    hookが索引を書く処理は`_hooks/agents_server_observations_test.py`がCodexの会話識別子で確かめる。
    """
    results = _codex_top_level_state(monkeypatch, tmp_path)
    shared_roots.write_root_alias("codex-thread", _CODEX_ROOT, tmp_path)

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "list"])
    listed = json.loads(capsys.readouterr().out)
    assert [session["session_id"] for session in listed["sessions"]] == ["codex-child"]

    payload = {"session_id": "codex-child", "status": "completed"}
    (results / "codex-child.json").write_text(json.dumps(payload), encoding="utf-8")
    # エージェント環境の待機は本文を保存先へ書くため、結果のJSONを標準出力で比べる人間の実行形にする。
    monkeypatch.delenv("AI_AGENT")
    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "wait"])
    assert json.loads(capsys.readouterr().out) == payload


def test_codex_top_level_without_alias_guides_root_session_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """索引が無いCodex最上位の会話では、MCPの`list`応答の`root_session_id`を明示する回復操作を案内する。"""
    _codex_top_level_state(monkeypatch, tmp_path)

    with pytest.raises(SystemExit, match="4"):
        atk.main(["agents", "wait"])

    err = capsys.readouterr().err
    assert "--root-session-id" in err
    assert "MCPの`list`" in err
