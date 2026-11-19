"""登録簿の索引が、レコードの書き換えに追従しつつ、変わらないレコードを解析し直さないことを確かめる。"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time
from typing import Any

import pytest

from agent_toolkit._common import registry_index, session_layout, session_registry
from agent_toolkit._common.process_identity import ProcessIdentity

_SETTLED = time.time() - 3600


def _publish(state_root: pathlib.Path, session_id: str, **kwargs: Any) -> pathlib.Path:
    kwargs.setdefault("state", session_registry.PublicationState.RESULT_COMPLETED)
    session_registry.publish(session_id, state_root=state_root, **kwargs)
    return session_registry.registry_directory(state_root) / f"{session_id}.json"


def _settle(*paths: pathlib.Path) -> None:
    """書込から間もないファイルは索引へ保存されないため、更新時刻を過去へ戻して保存の対象にする。"""
    for path in paths:
        os.utime(path, (_SETTLED, _SETTLED))


def _count_parses(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    parsed: list[str] = []
    derive = registry_index._derive_record  # pylint: disable=protected-access

    def counting(session_id: str, path: pathlib.Path) -> registry_index.Entry | None:
        parsed.append(session_id)
        return derive(session_id, path)

    monkeypatch.setattr(registry_index, "_derive_record", counting)
    return parsed


def test_parent_child_queries_follow_record_rewrites(tmp_path: pathlib.Path) -> None:
    """子・子孫の照会は保存済みの親子関係を返し、索引の保存後に書き換えたレコードの新しい親を反映する。"""
    paths = [
        _publish(tmp_path, "child", launcher_session_id="parent"),
        _publish(tmp_path, "grandchild", launcher_session_id="child"),
        _publish(tmp_path, "unrelated", launcher_session_id="other"),
    ]
    _settle(*paths)
    assert registry_index.child_session_ids("parent", state_root=tmp_path) == ("child",)
    assert registry_index.descendant_session_ids({"parent"}, state_root=tmp_path) == {"parent", "child", "grandchild"}
    # 保存済みの索引と同じ名前のレコードを、別の親で置き換える（中断した起動を別の親が作り直す場合）。
    paths[2].unlink()
    _publish(tmp_path, "unrelated", launcher_session_id="parent")
    assert registry_index.child_session_ids("parent", state_root=tmp_path) == ("child", "unrelated")
    assert "unrelated" in registry_index.descendant_session_ids({"parent"}, state_root=tmp_path)


@pytest.mark.parametrize("count", [5, 50])
def test_unchanged_records_are_not_parsed_again(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, count: int) -> None:
    """2回目以降の照会で解析するレコードは書き換えたものだけで、登録簿の件数に比例しない。"""
    paths = [_publish(tmp_path, f"session-{index}", launcher_session_id="parent") for index in range(count)]
    _settle(*paths)
    parsed = _count_parses(monkeypatch)
    assert len(registry_index.child_session_ids("parent", state_root=tmp_path)) == count
    assert len(parsed) == count
    parsed.clear()
    assert len(registry_index.child_session_ids("parent", state_root=tmp_path)) == count
    assert registry_index.recorded_server_instances(tmp_path) == set()
    assert not parsed
    _publish(tmp_path, "session-0", launcher_session_id="parent", state=session_registry.PublicationState.RESULT_FAILED)
    assert len(registry_index.child_session_ids("parent", state_root=tmp_path)) == count
    assert parsed == ["session-0"]


def test_records_are_parsed_again_after_the_parsing_code_changes(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """派生値を計算するコードが変わると（版を上げない開発中の編集を含む）、変わらないレコードも解析し直す。"""
    paths = [_publish(tmp_path, f"session-{index}", launcher_session_id="parent") for index in range(3)]
    _settle(*paths)
    parsed = _count_parses(monkeypatch)
    for _ in range(2):
        assert len(registry_index.child_session_ids("parent", state_root=tmp_path)) == 3
    assert sorted(parsed) == ["session-0", "session-1", "session-2"]
    monkeypatch.setattr(registry_index.derived_index, "code_version", lambda _module: "edited")
    parsed.clear()
    for _ in range(2):
        assert len(registry_index.child_session_ids("parent", state_root=tmp_path)) == 3
    assert sorted(parsed) == ["session-0", "session-1", "session-2"]


def test_recently_written_record_is_parsed_until_it_settles(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """更新時刻の刻みの中で内容だけが変わり得る新しいレコードは、保存せずに照会のたびに解析する。"""
    path = _publish(tmp_path, "fresh", launcher_session_id="parent")
    parsed = _count_parses(monkeypatch)
    for _ in range(2):
        assert registry_index.child_session_ids("parent", state_root=tmp_path) == ("fresh",)
    assert parsed == ["fresh", "fresh"]
    _settle(path)
    for _ in range(2):
        assert registry_index.child_session_ids("parent", state_root=tmp_path) == ("fresh",)
    assert parsed == ["fresh", "fresh", "fresh"]


def test_pending_sessions_follow_states_results_waits_and_ancestors(tmp_path: pathlib.Path) -> None:
    """実行中・所有側の喪失による中断・未回収の結果・回収待ちの対象とその委譲元を保留とし、終了済みと解放済みは除く。"""
    root = "root-session"
    paths = [
        _publish(tmp_path, "running", state=session_registry.PublicationState.PENDING_RUNNING, launcher_session_id="lead"),
        _publish(
            tmp_path,
            "stopped-owner",
            state=session_registry.PublicationState.RESULT_INTERRUPTED,
            interruption=session_registry.OWNER_SERVER_STOPPED,
        ),
        _publish(tmp_path, "finished", launcher_session_id="idle-parent"),
        _publish(tmp_path, "released"),
        _publish(tmp_path, "lead", launcher_session_id="top"),
        _publish(tmp_path, "uncollected"),
        _publish(tmp_path, "awaited"),
        _publish(tmp_path, "collected"),
    ]
    assert session_registry.release("released", reason="stopped", state_root=tmp_path)
    paths.append(_publish(tmp_path, "claimed"))
    payload = json.loads(paths[-1].read_text(encoding="utf-8"))
    process = ProcessIdentity.of(os.getpid())
    assert process is not None
    payload["resume_claim"] = {"turn_seq": payload["turn_seq"], "processes": [session_registry.process_payload(process)]}
    paths[-1].write_text(json.dumps(payload), encoding="utf-8")
    results = session_layout.results_directory(root, tmp_path)
    results.mkdir(parents=True)
    (results / "uncollected.json").write_text("{}", encoding="utf-8")
    waits = session_layout.status_directory(root, tmp_path) / "wait-results" / "root.json"
    waits.mkdir(parents=True)
    (waits / "run-a.json").write_text(json.dumps({"status": "running", "targets": ["awaited"]}), encoding="utf-8")
    (waits / "run-b.json").write_text(json.dumps({"status": "collected", "targets": ["collected"]}), encoding="utf-8")
    _settle(*session_registry.registry_directory(tmp_path).glob("*.json"), *waits.glob("*.json"))
    expected = {"running", "lead", "top", "stopped-owner", "uncollected", "awaited", "claimed"}
    # 1回目は全件を解析し、2回目は保存した索引から同じ結果を返す。
    assert registry_index.pending_session_ids(tmp_path) == expected
    assert registry_index.pending_session_ids(tmp_path) == expected
    with subprocess.Popen([sys.executable, "-c", "pass"]) as exited:
        gone = ProcessIdentity.of(exited.pid)
    assert gone is not None
    payload["resume_claim"]["processes"] = [session_registry.process_payload(gone)]
    paths[-1].write_text(json.dumps(payload), encoding="utf-8")
    assert registry_index.pending_session_ids(tmp_path) == expected - {"claimed"}


def test_unreadable_and_corrupt_index_are_handled(tmp_path: pathlib.Path) -> None:
    """解決できないレコードは親子関係だけを残し、壊れた索引は保存されていなかったものとして扱う。"""
    registry = session_registry.registry_directory(tmp_path)
    registry.mkdir(parents=True)
    (registry / "broken.json").write_text(json.dumps({"launcher_session_id": "parent"}), encoding="utf-8")
    (registry / "garbage.json").write_text("{", encoding="utf-8")
    _settle(*registry.glob("*.json"))
    entries = registry_index.entries(tmp_path)
    assert not entries["broken"].readable and entries["broken"].launcher == "parent"
    assert not entries["garbage"].readable
    assert registry_index.child_session_ids("parent", state_root=tmp_path) == ("broken",)
    index = tmp_path / "agents-server" / "sessions-index.json"
    saved = json.loads(index.read_text(encoding="utf-8"))
    saved["entries"]["broken"] = [*saved["entries"]["broken"][:3], "too-short"]
    index.write_text(json.dumps(saved), encoding="utf-8")
    assert registry_index.entries(tmp_path)["broken"].launcher == "parent"
