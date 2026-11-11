"""lane_integrationの分割試験で共有する準備と補助処理。"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import time
from typing import Any, Literal

import pytest
import yaml

from agent_toolkit import atk
from agent_toolkit._atk import lane, run_command
from agent_toolkit._common import session_registry, task_documents
from agent_toolkit._testing import git_repository, wi_entry_files
from agent_toolkit._testing.lane_support import _create, _invoke


def _cleanup_integration_manifest(tmp_path: pathlib.Path, *, handoff_text: str) -> pathlib.Path:
    """回収試験の実成果に対応する引継ぎと、WI最終化のmanifestを保存する。"""
    handoff = tmp_path / "handoff.md"
    handoff.write_text(handoff_text, encoding="utf-8")
    manifest = tmp_path / "integration.json"
    manifest.write_text(
        json.dumps(
            {
                "plans": [],
                "wis": [{"name": "20261023-000000-001.md", "commands": []}],
                "verification": [],
                "handoff": str(handoff),
            }
        ),
        encoding="utf-8",
    )
    return manifest


def _finalize_lane_for_cleanup(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *,
    feature_name: str,
    feature_text: str,
) -> tuple[pathlib.Path, dict[str, Any], pathlib.Path, str]:
    """指定した成果を公開integrateで最終化し、回収試験へ資源と入力HEADを渡す。"""
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    _integration_selection(selected, monkeypatch, repo)
    worktree = pathlib.Path(record["worktree"])
    (worktree / feature_name).write_text(feature_text, encoding="utf-8")
    head = git_repository.commit_all(worktree, feature_text)
    manifest = _cleanup_integration_manifest(tmp_path, handoff_text="検収済み")
    code, integrated, stderr = _invoke(capsys, *_integration_args(record, repo, manifest, head))
    assert code == 0 and integrated["phase"] == "finalized", stderr
    return repo, record, worktree, head


def _integration_args(record: dict[str, Any], repo: pathlib.Path, manifest: pathlib.Path, head: str) -> tuple[str, ...]:
    return (
        "integrate",
        "--record",
        record["record_path"],
        "--reviewed-head",
        head,
        "--target-worktree",
        str(repo),
        "--target-branch",
        "main",
        "--manifest",
        str(manifest),
    )


def _plan_operation(capsys: pytest.CaptureFixture[str], script: str, *arguments: str, expected: int = 0) -> tuple[str, str]:
    with pytest.raises(SystemExit) as ended:
        atk.main(["run-script", script, "--", *arguments])
    observed = capsys.readouterr()
    assert ended.value.code == expected, observed.err
    return observed.out, observed.err


def _record_wi_commit(
    capsys: pytest.CaptureFixture[str],
    handoff: pathlib.Path,
    worktree: pathlib.Path,
    wi: str,
    commit: str,
    previous_head: str,
    *,
    step: str,
    result: str,
) -> None:
    _plan_operation(
        capsys,
        "plan-progress",
        str(handoff),
        "--worktree",
        str(worktree),
        "--handoff",
        "--allowed-awi",
        wi,
        "--awi",
        wi,
        "--commit",
        commit,
        "--previous-head",
        previous_head,
        "--completed-step",
        step,
        "--result",
        result,
    )


def _adopt_argv(repo: pathlib.Path, wi: str, commits: list[str], *, note_file: str | None = None) -> list[str]:
    note = ["--note-file", note_file] if note_file is not None else ["--note=統合検収済み"]
    return lane._atk_script(  # pylint: disable=protected-access
        "wi",
        "adopt",
        wi,
        "--target-repo",
        str(repo),
        "--skip-push",
        *note,
        *(argument for commit in commits for argument in ("--commit", commit)),
    )


def _integration_selection(path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, repo: pathlib.Path) -> None:
    # WIの所属と統合先の識別子を揃え、模擬remoteへの外部fetchは行わない。
    git_repository.run_git(repo, "remote", "add", "origin", "https://github.com/test/repo.git")
    git_repository.run_git(repo, "config", "remote.origin.skipFetchAll", "true")
    notes = path.parent / "notes"
    wi_entry_files.entry_path(notes, "20261023-000000-001.md", state="processing").write_text(
        "---\ntype: awi\nstate: processing\nsource: test\ntarget_repo: github.com/test/repo\n---\n\n# 観測\n\n"
        "## 反映内容と反映先\n\n観測を保存する。\n\n## 完成条件\n\n観測を完了する。\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
    document = {
        "選定": [
            {
                "WI": "20261023-000000-001.md",
                "レーン": "lane-01",
                "書込対象": [],
                "鮮度": {"status": "current", "later_commit_count": 0},
            }
        ],
        "見込みの補正": {"係数": 1, "参照した記録": [], "除外した記録": []},
        "レーンの所要時間": [
            {
                "レーン": "lane-01",
                "統合状態": "未統合",
                "実装秒数": 60,
                "統合秒数": 60,
                "補正前の実装秒数": 60,
                "補正前の統合秒数": 60,
                "根拠": "隔離した観測試験",
            }
        ],
        "初回配分": {
            "候補WI": ["20261023-000000-001.md"],
            "レーン割当": {"20261023-000000-001.md": "lane-01"},
            "不可分成分": [{"WI": ["20261023-000000-001.md"], "結合条件": []}],
        },
    }
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")


def prepare_lane_verification(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    verification_argv: list[str],
) -> list[str]:
    """共有検証枠の競合試験へ、公開createで準備したlane integrateのargvを渡す。"""
    tmp_path.mkdir(parents=True, exist_ok=True)
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    _integration_selection(selected, monkeypatch, repo)
    head = git_repository.git_output(pathlib.Path(record["worktree"]), "rev-parse", "HEAD")
    handoff = tmp_path / "handoff.md"
    handoff.write_text("共有検証枠の観測", encoding="utf-8")
    manifest = tmp_path / "integration.json"
    manifest.write_text(
        json.dumps(
            {
                "plans": [],
                "wis": [{"name": "20261023-000000-001.md", "commands": []}],
                "verification": [verification_argv],
                "handoff": str(handoff),
            }
        ),
        encoding="utf-8",
    )
    return [
        sys.executable,
        str(pathlib.Path(atk.__file__).resolve()),
        "lane",
        *_integration_args(record, repo, manifest, head),
        "--session-id",
        record["session_id"],
    ]


def _write_empty_finalization_manifest(tmp_path: pathlib.Path) -> pathlib.Path:
    """最終化操作なし・単純検証ありの統合入力を作成する。"""
    handoff = tmp_path / "handoff.md"
    handoff.touch()
    manifest = tmp_path / "integration.json"
    manifest.write_text(
        json.dumps(
            {
                "plans": [],
                "wis": [{"name": "20261023-000000-001.md", "commands": []}],
                "handoff": str(handoff),
                "verification": [[sys.executable, "-c", "pass"]],
            }
        ),
        encoding="utf-8",
    )
    return manifest


def _rebased_integration(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *,
    failed: bool = False,
    record_commits: bool = False,
) -> tuple[pathlib.Path, dict[str, Any], pathlib.Path, dict[str, Any], dict[str, Any]]:
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    _integration_selection(selected, monkeypatch, repo)
    worktree = pathlib.Path(record["worktree"])
    (worktree / "feature").write_text("成果", encoding="utf-8")
    reviewed = git_repository.commit_all(worktree, "成果")
    (repo / "upstream").write_text("先行統合", encoding="utf-8")
    git_repository.commit_all(repo, "先行統合")
    handoff = tmp_path / "handoff.md"
    handoff.touch()
    if record_commits:
        _record_wi_commit(
            capsys,
            handoff,
            worktree,
            "20261023-000000-001.md",
            reviewed,
            git_repository.git_output(worktree, "rev-parse", "HEAD^"),
            step="実装",
            result="元のWI対応を記録",
        )
    manifest = tmp_path / "integration.json"
    value: dict[str, Any] = {
        "plans": [],
        "wis": [{"name": "20261023-000000-001.md", "commands": []}],
        "handoff": str(handoff),
        "verification": [[sys.executable, "-c", "raise SystemExit(3)" if failed else "pass"]],
    }
    manifest.write_text(json.dumps(value), encoding="utf-8")
    code, _result, stderr = _invoke(capsys, *_integration_args(record, repo, manifest, reviewed))
    assert code == (2 if failed else 1), stderr
    if failed:
        assert "verification-0" in stderr
    state = json.loads(pathlib.Path(record["record_path"]).read_text(encoding="utf-8"))["integration"]
    return repo, record, manifest, value, state


def _timed_integration(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], *, rebased: bool
) -> tuple[pathlib.Path, dict[str, Any], pathlib.Path, str, list[str]]:
    # 実Gitの準備の余裕は保ち、対象argvだけ準備通知後に短い期限を開始する。
    repo, selected, record = _create(tmp_path, monkeypatch, capsys, "--timeout", "5")
    _integration_selection(selected, monkeypatch, repo)
    worktree = pathlib.Path(record["worktree"])
    (worktree / "feature").write_text("成果", encoding="utf-8")
    head = git_repository.commit_all(worktree, "成果")
    if rebased:
        (repo / "upstream").write_text("先行統合", encoding="utf-8")
        git_repository.commit_all(repo, "先行統合")
    handoff = tmp_path / "handoff.md"
    handoff.touch()
    ready, release = tmp_path / "ready", tmp_path / "release"
    script = (
        "import pathlib,sys,time; ready,release=map(pathlib.Path,sys.argv[1:]); ready.touch(); "
        "deadline=time.monotonic()+30; "
        "exec('while not release.exists():\\n assert time.monotonic()<deadline\\n time.sleep(0.01)')"
    )
    argv = [sys.executable, "-c", script, str(ready), str(release)]
    execute = run_command.execute

    class ReadyPopen(subprocess.Popen):
        def wait(self, timeout=None):
            if self.args == argv and not ready.exists():
                deadline = time.monotonic() + 30
                while not ready.exists():
                    assert self.poll() is None and time.monotonic() < deadline
                    time.sleep(0.01)
            if self.args == argv and timeout is None:
                release.touch()
            return super().wait(timeout=timeout)

    def timed_execute(command, cwd, timeout, **kwargs):
        if command == argv and timeout is not None:
            assert timeout == 5
            timeout = 0.05
        return execute(command, cwd, timeout, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", ReadyPopen)
    monkeypatch.setattr(run_command, "execute", timed_execute)
    return repo, record, handoff, head, argv


def _selection_with(path: pathlib.Path, wis: list[str], *, integrated: bool = True) -> None:
    """選定の項目と統合状態を持つ選定YAMLを書く。"""
    path.write_text(
        yaml.safe_dump(
            {
                "選定": [{"WI": name, "レーン": "lane-01"} for name in wis],
                "レーンの所要時間": [{"レーン": "lane-01", "統合状態": "統合済み" if integrated else "未統合"}],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )


def _legacy(record: dict[str, Any]) -> None:
    """選定情報を持たない旧形式の登録を再現する（選定原本も失効させる）。"""
    path = pathlib.Path(record["record_path"])
    stored = json.loads(path.read_text(encoding="utf-8"))
    stored.pop("selection", None)
    path.write_text(json.dumps(stored), encoding="utf-8")
    pathlib.Path(record["selection_file"]).unlink(missing_ok=True)


_SHARE = pathlib.Path(lane.__file__).resolve().parents[2] / "share"


def _caller(monkeypatch: pytest.MonkeyPatch, *, claude: str | None = None, codex: str | None = None) -> None:
    # 実行ホストのsessionで結果が変わらないよう、呼出元の識別子を明示する。
    for name, value in (("CLAUDE_CODE_SESSION_ID", claude), ("CODEX_THREAD_ID", codex)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)


def _running(
    session_id: str,
    cwd: pathlib.Path,
    *,
    role: str | None = None,
    prompt: str = "自由本文の依頼",
    kind: Literal["delegate", "explore", "shell", "write"] = "delegate",
    launcher: str | None = None,
    terminal: bool = False,
) -> None:
    """agents_serverが`start`で公開するのと同じ形の担当記録を登録簿へ置く。"""
    if role is not None:
        prompt = task_documents.task_prompt_header(_SHARE / f"{role}.subagent.md") + "\n本文"
    session_registry.publish(
        session_id,
        state=session_registry.PublicationState.RESULT_COMPLETED
        if terminal
        else session_registry.PublicationState.PENDING_RUNNING,
        cwd=str(cwd),
        launch_kind=kind,
        launch_info=session_registry.LaunchInfo(label=f"{session_id}-label", prompt=prompt),
        launcher_session_id=launcher,
    )


def _finalization_manifest(tmp_path: pathlib.Path, **extra: Any) -> pathlib.Path:
    handoff = tmp_path / "handoff.md"
    handoff.write_text("検収済み採否", encoding="utf-8")
    manifest = tmp_path / "integration.json"
    manifest.write_text(
        json.dumps(
            {"plans": [], "wis": [{"name": "20261023-000000-001.md", "commands": []}], "handoff": str(handoff), **extra}
        ),
        encoding="utf-8",
    )
    return manifest


def _write_args(output: pathlib.Path) -> list[str]:
    return [
        sys.executable,
        "-c",
        "import pathlib,sys;pathlib.Path(sys.argv[1]).write_text(' '.join(sys.argv[2:]),encoding='utf-8')",
        str(output),
        "{{integrated_head_short}}",
        "{{completed_at}}",
        "{{lane_seconds}}",
    ]
