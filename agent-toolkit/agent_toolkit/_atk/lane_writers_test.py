"""lane writersの契約を検証する。"""

from __future__ import annotations

import datetime
import json
import pathlib
import sys

import pytest

from agent_toolkit._common import session_registry
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.lane_integration_support import (
    _SHARE,
    _caller,
    _finalization_manifest,
    _integration_args,
    _integration_selection,
    _running,
    _write_args,
)
from agent_toolkit._testing.lane_support import _create, _invoke


@pytest.mark.parametrize("case", ["non-writer-role", "explore", "caller-self", "launcher", "two-level", "codex-caller"])
def test_integrate_does_not_count_non_writers_or_caller_chain(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], case: str
) -> None:
    """書き込まない担当と呼出元の委譲元の連鎖は、--writer-sessionなしでも統合を止めない。"""
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    _integration_selection(selected, monkeypatch, repo)
    worktree = pathlib.Path(record["worktree"])
    _caller(monkeypatch)
    if case == "non-writer-role":
        _running("adder", repo, role="add-wi")
    elif case == "explore":
        _running("explorer", repo, kind="explore")
    elif case == "caller-self":
        _caller(monkeypatch, claude="lane-caller")
        _running("lane-caller", worktree)
    elif case == "launcher":
        _caller(monkeypatch, claude="lane-caller")
        _running("lane-caller", worktree, role="exec", launcher="main")
        _running("main", repo)
    elif case == "two-level":
        _caller(monkeypatch, claude="caller")
        _running("caller", repo, role="lane-integration", launcher="middle")
        _running("middle", worktree, role="exec", launcher="top")
        _running("top", repo)
    else:
        _caller(monkeypatch, codex="codex-caller")
        _running("codex-caller", worktree, role="exec", launcher="main")
        _running("main", repo)

    code, result, stderr = _invoke(
        capsys, *_integration_args(record, repo, _finalization_manifest(tmp_path), record["initial_head"])
    )

    assert code == 0 and result["phase"] == "finalized", stderr
    # 回収側は書込宣言と呼出元の除外を適用せず、レーンworktreeの使用者がいれば資源を保持する。
    code, deleted, stderr = _invoke(capsys, "delete")
    assert ("lane-01" in deleted.get("removed", [])) is (case in {"non-writer-role", "explore"}), stderr


def test_integrate_first_call_accepts_all_agreed_distinct_sessions(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """所有者・レーン担当・メインが別sessionでも、初回の全要素指定でGit統合と最終化を完了する。"""
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    _integration_selection(selected, monkeypatch, repo)
    worktree = pathlib.Path(record["worktree"])
    (worktree / "feature.txt").write_text("統合する成果", encoding="utf-8")
    head = git_repository.commit_all(worktree, "統合する成果")
    sessions = [record["session_id"], "lane-writer", "integration-main"]
    assert len(set(sessions)) == 3
    _caller(monkeypatch)
    _running(sessions[0], worktree)
    _running(sessions[1], worktree, role="exec")
    _running(sessions[2], repo)
    assert all(session_registry.resolve(identity).state is session_registry.Resolution.RUNNING for identity in sessions)
    finalized = tmp_path / "finalized"
    manifest = _finalization_manifest(tmp_path)
    value = json.loads(manifest.read_text(encoding="utf-8"))
    value["wis"][0]["commands"] = [
        [sys.executable, "-c", "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text('finalized')", str(finalized)]
    ]
    manifest.write_text(json.dumps(value), encoding="utf-8")
    writer_input = json.dumps(sessions)
    writers = [argument for identity in json.loads(writer_input) for argument in ("--writer-session", identity)]
    assert "integration" not in json.loads(pathlib.Path(record["record_path"]).read_text(encoding="utf-8"))

    code, result, stderr = _invoke(
        capsys, *_integration_args(record, repo, manifest, head), "--session-id", record["session_id"], *writers
    )

    assert code == 0, stderr
    assert result["phase"] == "finalized" and result["integrated_head"] == head
    assert git_repository.git_output(repo, "rev-parse", "HEAD") == head
    assert (repo / "feature.txt").read_text(encoding="utf-8") == "統合する成果"
    assert finalized.read_text(encoding="utf-8") == "finalized"
    assert "統合済み" in selected.read_text(encoding="utf-8")
    persisted = json.loads(pathlib.Path(record["record_path"]).read_text(encoding="utf-8"))
    assert persisted["session_id"] == sessions[0]
    assert persisted["integration"]["phase"] == "finalized"


@pytest.mark.parametrize("no_merge", [False, True])
@pytest.mark.parametrize("spread", [False, True])
def test_integrate_reports_all_unowned_writers_then_resumes_with_agreement(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], spread: bool, no_merge: bool
) -> None:
    """書き込む担当を両worktreeから全件診断し、合意した担当を指定した再実行で統合へ進む。"""
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    _integration_selection(selected, monkeypatch, repo)
    worktree = pathlib.Path(record["worktree"])
    nested = repo / ".claude/worktrees/other"
    git_repository.run_git(repo, "worktree", "add", "-b", "other", str(nested))
    _caller(monkeypatch, claude="caller")
    _running("caller", worktree, role="exec", launcher="main")
    _running("main", repo)
    counted = {
        "exec-writer": repo,
        "free-writer": worktree if spread else repo,
        "sibling": repo,
        # 役割文書へ言及するだけの自由本文は、その役割の起動として扱わない。
        "mention": worktree if spread else repo,
    }
    _running("exec-writer", counted["exec-writer"], role="exec")
    _running("free-writer", counted["free-writer"])
    _running("sibling", counted["sibling"], role="exec", launcher="main")
    _running("mention", counted["mention"], prompt=f"次の文書を読んで比べよ: {_SHARE / 'add-wi.subagent.md'}")
    _running("agreed", repo, role="exec")
    _running("finished", repo, role="exec", terminal=True)
    _running("elsewhere", nested, role="exec")
    # 読み取りだけの補助の担当は、役割文書が無くても作業ディレクトリが両worktreeの外なら数えない。
    outside = tmp_path / "managed-temp" / "reader"
    outside.mkdir(parents=True)
    _running("outside-reader", outside)
    args = (
        *_integration_args(record, repo, _finalization_manifest(tmp_path), record["initial_head"]),
        "--writer-session",
        "agreed",
    )
    if no_merge:
        args = (*args, "--no-merge")
    target_head = git_repository.git_output(repo, "rev-parse", "HEAD")

    code, _result, stderr = _invoke(capsys, *args)

    assert code == 2
    assert f"現在の書込所有に含まれない担当が稼働中: {len(counted)}件" in stderr
    for session_id, cwd in counted.items():
        tree = worktree if cwd == worktree else repo.resolve()
        assert f"session_id={session_id}, label={session_id}-label" in stderr
        assert f"cwd={cwd}, 所属worktree={tree}" in stderr
    assert f"役割文書={_SHARE / 'exec.subagent.md'}" in stderr
    assert "--writer-sessionへ反復指定" in stderr
    assert "作業ディレクトリを両worktreeの外にして起動し直してもよい" in stderr
    for excluded in ("caller", "main", "agreed", "finished", "elsewhere", "outside-reader"):
        assert f"session_id={excluded}," not in stderr
    assert git_repository.git_output(repo, "rev-parse", "HEAD") == target_head
    assert "統合済み" not in selected.read_text(encoding="utf-8")
    assert "integration" not in json.loads(pathlib.Path(record["record_path"]).read_text(encoding="utf-8"))

    agreed = [value for session_id in counted for value in ("--writer-session", session_id)]
    code, result, stderr = _invoke(capsys, *args, *agreed)

    assert code == 0 and result["phase"] == "finalized", stderr


def test_integrate_rejects_unresolvable_registration(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """解決できない担当登録は許可扱いにせず統合を拒否する。"""
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    _integration_selection(selected, monkeypatch, repo)
    _caller(monkeypatch)
    directory = session_registry.registry_directory()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "broken.json").write_text("{", encoding="utf-8")

    code, _result, stderr = _invoke(
        capsys, *_integration_args(record, repo, _finalization_manifest(tmp_path), record["initial_head"])
    )

    assert code == 2 and "担当登録を読めない" in stderr
    assert str((directory / "broken.json").resolve()) in stderr and "atk agents show broken" in stderr


@pytest.mark.parametrize("no_merge", [False, True])
def test_integrate_passes_confirmed_values_to_every_consumer(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], no_merge: bool
) -> None:
    """計画2件の保存とAWI本文の追記へ、同じ統合後HEAD・完了時刻・稼働秒数を渡す。"""
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    _integration_selection(selected, monkeypatch, repo)
    worktree = pathlib.Path(record["worktree"])
    _caller(monkeypatch)
    if not no_merge:
        (worktree / "feature.txt").write_text("成果", encoding="utf-8")
        git_repository.commit_all(worktree, "成果")
    head = git_repository.git_output(worktree, "rev-parse", "HEAD")
    template = tmp_path / "record-template.md"
    template.write_text("予測補正記録: {{integrated_head_short}} {{completed_at}} {{lane_seconds}} {{other}}", encoding="utf-8")
    outputs = [tmp_path / name for name in ("plan-a", "plan-b", "awi-body")]
    copy = [sys.executable, "-c", "import shutil,sys;shutil.copy(sys.argv[1],sys.argv[2])"]
    started = datetime.datetime.now().astimezone() - datetime.timedelta(seconds=100)
    manifest = tmp_path / "integration.json"
    manifest.write_text(
        json.dumps(
            {
                "plans": [
                    {"name": "a.md", "commands": [_write_args(outputs[0])]},
                    {"name": "b.md", "commands": [_write_args(outputs[1])]},
                ],
                "wis": [
                    {
                        "name": "20261023-000000-001.md",
                        "commands": [[*copy, "{{expand-file:" + str(template) + "}}", str(outputs[2])]],
                    }
                ],
                "lane_started_at": started.isoformat(),
            }
        ),
        encoding="utf-8",
    )
    args = _integration_args(record, repo, manifest, head)
    if no_merge:
        args = (*args, "--no-merge")

    code, result, stderr = _invoke(capsys, *args)

    assert code == 0 and result["phase"] == "finalized", stderr
    values = result["finalization_values"]
    short = values["integrated_head_short"]
    assert git_repository.git_output(repo, "rev-parse", short) == git_repository.git_output(repo, "rev-parse", "HEAD")
    completed = datetime.datetime.fromisoformat(values["completed_at"])
    assert completed.tzinfo is not None
    # 完了時刻は秒精度で表示するため、開始時刻からの経過との差は1秒未満の切り捨て分に限る。
    assert abs(int(values["lane_seconds"]) - (completed - started).total_seconds()) <= 1
    assert int(values["lane_seconds"]) >= 100
    expected = f"{short} {values['completed_at']} {values['lane_seconds']}"
    assert outputs[0].read_text(encoding="utf-8") == outputs[1].read_text(encoding="utf-8") == expected
    assert outputs[2].read_text(encoding="utf-8") == f"予測補正記録: {expected} {{{{other}}}}"


def test_integrate_reuses_confirmed_values_after_failed_finalization(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """途中失敗の再開では確定値を再計算せず、成功済み操作を繰り返さず、開始時刻の変更を拒否する。"""
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    _integration_selection(selected, monkeypatch, repo)
    worktree = pathlib.Path(record["worktree"])
    _caller(monkeypatch)
    git_repository.commit_all(worktree, "成果")
    head = git_repository.git_output(worktree, "rev-parse", "HEAD")
    first, second, calls, permit = (tmp_path / name for name in ("plan-a", "plan-b", "calls", "permit"))
    count = [
        sys.executable,
        "-c",
        "import pathlib,sys;p=pathlib.Path(sys.argv[1]);p.write_text(p.read_text()+'s' if p.exists() else 's')",
        str(calls),
    ]
    gated = [
        sys.executable,
        "-c",
        "import pathlib,sys;p=pathlib.Path;a=sys.argv;sys.exit(4) if not p(a[1]).exists() else p(a[2]).write_text(a[3])",
        str(permit),
        str(second),
        "{{completed_at}} {{lane_seconds}}",
    ]
    started = (datetime.datetime.now().astimezone() - datetime.timedelta(seconds=30)).isoformat()

    def manifest(lane_started_at: str) -> pathlib.Path:
        path = tmp_path / "integration.json"
        path.write_text(
            json.dumps(
                {
                    "plans": [{"name": "a.md", "commands": [_write_args(first), count]}, {"name": "b.md", "commands": [gated]}],
                    "wis": [{"name": "20261023-000000-001.md", "commands": []}],
                    "lane_started_at": lane_started_at,
                }
            ),
            encoding="utf-8",
        )
        return path

    args = _integration_args(record, repo, manifest(started), head)
    code, _result, stderr = _invoke(capsys, *args)
    assert code == 2 and "終了4" in stderr
    stored = json.loads(pathlib.Path(record["record_path"]).read_text(encoding="utf-8"))
    assert stored["integration"]["phase"] == "git-integrated"
    assert "統合済み" not in selected.read_text(encoding="utf-8")
    values = stored["integration"]["finalization_values"]

    other = (datetime.datetime.fromisoformat(started) - datetime.timedelta(hours=1)).isoformat()
    code, _result, stderr = _invoke(capsys, *_integration_args(record, repo, manifest(other), head))
    assert code == 2 and "最初のレーン開始時刻と異なる" in stderr

    permit.touch()
    code, result, stderr = _invoke(capsys, *_integration_args(record, repo, manifest(started), head))
    assert code == 0 and result["phase"] == "finalized", stderr
    assert result["finalization_values"] == values
    assert calls.read_text(encoding="utf-8") == "s"
    assert second.read_text(encoding="utf-8") == f"{values['completed_at']} {values['lane_seconds']}"
    assert first.read_text(encoding="utf-8").endswith(f"{values['completed_at']} {values['lane_seconds']}")


@pytest.mark.parametrize(
    ("extra", "command", "reason"),
    [
        (
            {"lane_started_at": "2026-10-19T00:00:00+09:00"},
            "{{unknown}}",
            "最終化argvの参照が不明: {{unknown}}。受理する参照は",
        ),
        (
            {"lane_started_at": "2999-01-01T00:00:00+09:00"},
            "{{lane_seconds}}",
            "lane_started_atが現在時刻より後にある: 受け取った値=2999-01-01T00:00:00+09:00",
        ),
        ({"lane_started_at": "2026-10-19T00:00:00"}, "{{lane_seconds}}", "時差付きISO 8601"),
        ({}, "{{lane_seconds}}", "lane_started_atを指定する"),
        ({"lane_started_at": "2026-10-19T00:00:00+09:00"}, "{{expand-file:relative.md}}", "実在する絶対パス"),
    ],
)
def test_integrate_rejects_invalid_finalization_references(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    extra: dict[str, str],
    command: str,
    reason: str,
) -> None:
    """未知の参照、時差の無い開始時刻、未来の開始時刻、開始時刻の欠落、相対パスのテンプレートはGit統合前に拒否する。"""
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    _integration_selection(selected, monkeypatch, repo)
    _caller(monkeypatch)
    manifest = tmp_path / "integration.json"
    manifest.write_text(
        json.dumps(
            {
                "plans": [{"name": "a.md", "commands": [["true", command]]}],
                "wis": [{"name": "20261023-000000-001.md", "commands": []}],
                **extra,
            }
        ),
        encoding="utf-8",
    )
    target_head = git_repository.git_output(repo, "rev-parse", "HEAD")

    code, _result, stderr = _invoke(capsys, *_integration_args(record, repo, manifest, record["initial_head"]))

    assert code == 2 and reason in stderr
    assert git_repository.git_output(repo, "rev-parse", "HEAD") == target_head
    if "現在時刻より後" in reason:
        assert "現在時刻=" in stderr and "起動時刻を時差付きISO 8601" in stderr
        assert "integration" not in json.loads(pathlib.Path(record["record_path"]).read_text(encoding="utf-8"))
