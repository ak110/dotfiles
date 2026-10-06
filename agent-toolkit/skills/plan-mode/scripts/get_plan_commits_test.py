"""計画で確定した公開名から同じ実装commit対応を取得する。"""

import argparse
import json
import pathlib
import subprocess

import append_progress_log
import pytest

from agent_toolkit._atk import run_script
from agent_toolkit._plan import commit_mapping


def _prepare_two_commits(worktree: pathlib.Path) -> str:
    """対応記録の親確認に使う連続した2 commitを作成し、変更前HEADを返す。"""
    subprocess.run(["git", "init"], cwd=worktree, check=True, capture_output=True, timeout=30)

    def commit(message: str) -> None:
        subprocess.run(
            ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-m", message],
            cwd=worktree,
            check=True,
            capture_output=True,
            timeout=30,
        )

    commit("base")
    previous_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=worktree, check=True, capture_output=True, text=True, timeout=30
    ).stdout.strip()
    commit("change")
    return previous_head


def test_public_plan_commits_reads_existing_handoff(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """引き継ぎ記録と同じstemの対応記録ファイルを公開登録から取得し、取得時点で一意な長さの短縮OIDを返す。"""
    previous_head = _prepare_two_commits(tmp_path)
    wi = "20261004-044311-001.md"
    event = commit_mapping.commit_event(tmp_path, "HEAD", previous_head, [wi], {wi})
    record = tmp_path / "handoff.md"
    record.write_text("# 引き継ぎ\n", encoding="utf-8")
    commit_mapping.append_event(record, event)
    args = argparse.Namespace(
        script_name="plan-commits",
        script_args=[str(record), "--worktree", str(tmp_path), "--awi", wi, "--handoff", "--allowed-awi", wi],
    )
    assert run_script.dispatch(args) == 0
    short = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=tmp_path, check=True, capture_output=True, text=True, timeout=30
    ).stdout.strip()
    assert json.loads(capsys.readouterr().out) == {"awi": wi, "commits": [short]}


def test_public_plan_commits_reads_saved_plan_by_old_working_path(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """保存後も公開別名から旧作業パスで、保存済み計画と同じstemの対応記録ファイルを読んで対応commitを取得できる。"""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(tmp_path / "private-notes"))
    previous_head = _prepare_two_commits(tmp_path)
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
                f"--worktree={tmp_path}",
                f"--awi={wi}",
                "--commit=HEAD",
                f"--previous-head={previous_head}",
                "--completed-step=実装",
                "--result=成功",
            ]
        )
        == 0
    )
    saved = tmp_path / "private-notes" / "plans" / "2026" / "10" / working.name
    saved.parent.mkdir(parents=True)
    working.rename(saved)
    attachment = working.with_name(working.stem + ".wi-commits.jsonl")
    attachment.rename(saved.with_name(attachment.name))
    args = argparse.Namespace(
        script_name="plan-commits",
        script_args=[str(working), "--worktree", str(tmp_path), "--awi", wi],
    )
    assert run_script.dispatch(args) == 0
    assert json.loads(capsys.readouterr().out)["awi"] == wi
