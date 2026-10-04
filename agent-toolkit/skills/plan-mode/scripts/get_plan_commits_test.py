"""計画で確定した公開名から同じ実装commit対応を取得する。"""

import argparse
import json
import pathlib
import subprocess

import append_progress_log
import pytest

from agent_toolkit._atk import run_script
from agent_toolkit._plan import commit_mapping


def test_public_plan_commits_reads_existing_handoff(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """記録済みの対応を公開登録から取得し、実在する完全OIDを返す。"""
    for args in [
        ["init"],
        ["-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-m", "test"],
    ]:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True, timeout=30)
    wi = "20261004-044311-001.md"
    event = commit_mapping.commit_event(tmp_path, "HEAD", [wi], {wi})
    record = tmp_path / "handoff.md"
    record.write_text(commit_mapping.encode_event(event), encoding="utf-8")
    args = argparse.Namespace(
        script_name="plan-commits",
        script_args=[str(record), "--worktree", str(tmp_path), "--awi", wi, "--handoff", "--allowed-awi", wi],
    )
    assert run_script.dispatch(args) == 0
    assert json.loads(capsys.readouterr().out) == {"awi": wi, "commits": [event["commit"]]}


def test_public_plan_commits_reads_saved_plan_by_old_working_path(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """保存後も公開別名から旧作業パスで対応commitを取得できる。"""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(tmp_path / "private-notes"))
    for command in [
        ["init"],
        ["-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-m", "test"],
    ]:
        subprocess.run(["git", *command], cwd=tmp_path, check=True, capture_output=True, timeout=30)
    wi = "20261004-044311-001.md"
    working = tmp_path / ".claude" / "plans" / "04-example-1a2b.md"
    working.parent.mkdir(parents=True)
    working.write_text(
        "# 計画\n\n## 概要\n\n### 計画メタ情報\n\n- 関連WI:\n  - "
        + wi
        + ": 対応\n\n## 進捗ログ\n\n| 日時 | 完了した工程 | 結果・特記事項 |\n| --- | --- | --- |\n",
        encoding="utf-8",
    )
    assert (
        append_progress_log.main(
            [
                str(working),
                "--worktree",
                str(tmp_path),
                "--awi",
                wi,
                "--commit",
                "HEAD",
                "--completed-step",
                "実装",
                "--result",
                "成功",
            ]
        )
        == 0
    )
    saved = tmp_path / "private-notes" / "plans" / "2026" / "10" / working.name
    saved.parent.mkdir(parents=True)
    working.rename(saved)
    args = argparse.Namespace(
        script_name="plan-commits",
        script_args=[str(working), "--worktree", str(tmp_path), "--awi", wi],
    )
    assert run_script.dispatch(args) == 0
    assert json.loads(capsys.readouterr().out)["awi"] == wi
