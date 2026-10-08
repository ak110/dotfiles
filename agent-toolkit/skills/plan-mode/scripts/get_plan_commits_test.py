"""計画で確定した公開名から同じ実装commit対応を取得する。"""

import argparse
import json
import pathlib

import append_progress_log
import pytest

from agent_toolkit._atk import run_script
from agent_toolkit._plan import commit_mapping
from agent_toolkit._testing import git_repository


def _prepare_two_commits(worktree: pathlib.Path) -> str:
    """対応記録の親確認に使う連続した2 commitを作成し、変更前HEADを返す。"""
    git_repository.init_repository(worktree)

    def commit(message: str) -> None:
        git_repository.run_git(
            worktree, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-m", message
        )

    commit("base")
    previous_head = git_repository.run_git(worktree, "rev-parse", "HEAD").stdout.strip()
    commit("change")
    return previous_head


@pytest.mark.parametrize("handoff", [False, True])
@pytest.mark.parametrize(
    "case", ["missing-input", "invalid-input", "outside-input", "missing-record", "broken-record", "record-wi", "missing-git"]
)
def test_public_commit_diagnostics_distinguish_input_record_and_git(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    handoff: bool,
    case: str,
) -> None:
    """原因別の案内を確認し、入力または保存記録を補った同じ入口で回復する。"""
    previous_head = _prepare_two_commits(tmp_path)
    wi = "20261004-044311-001.md"
    record = tmp_path / "record.md"
    record.write_text(
        "# 引き継ぎ\n" if handoff else "# 計画\n\n## 概要\n\n### 計画メタ情報\n\n- 関連WI:\n  - " + wi + ": 対応\n",
        encoding="utf-8",
    )
    event = commit_mapping.commit_event(tmp_path, "HEAD", previous_head, [wi], {wi})
    attachment = commit_mapping.mapping_path(record)
    if case != "missing-record":
        commit_mapping.append_event(record, event)
    if case == "broken-record":
        attachment.write_text("{\n", encoding="utf-8")
    elif case == "record-wi":
        attachment.write_text(json.dumps({"commits": ["HEAD"], "awi": []}), encoding="utf-8")
    elif case == "missing-git":
        attachment.write_text(json.dumps({"commits": ["deadbee"], "awi": [wi]}), encoding="utf-8")
    base = [str(record), "--worktree", str(tmp_path)]
    if handoff:
        base += ["--handoff", "--allowed-awi", wi]
    request = (
        []
        if case == "missing-input"
        else ["--awi", "invalid" if case == "invalid-input" else "20261004-044311-002.md" if case == "outside-input" else wi]
    )
    args = argparse.Namespace(script_name="plan-commits", script_args=[*base, *request])
    assert run_script.dispatch(args) == 1
    diagnostic = capsys.readouterr().err
    assert "commit対応を取得できません" in diagnostic
    action = diagnostic.split("次の操作:", 1)[1]
    if case.endswith("input"):
        assert "--awi" in action and "再記録" not in action
    elif case == "missing-git":
        assert "Git" in action and "--worktree" in action and "再記録" not in action
    else:
        assert "対応記録" in action and "再記録" in action
    attachment.write_text(json.dumps(event) + "\n", encoding="utf-8")
    args.script_args = [*base, "--awi", wi]
    assert run_script.dispatch(args) == 0
    assert json.loads(capsys.readouterr().out)["awi"] == wi


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
    short = git_repository.run_git(tmp_path, "rev-parse", "--short", "HEAD").stdout.strip()
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
