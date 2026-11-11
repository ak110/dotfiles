# pylint: disable=unused-import,useless-import-alias  # pytestのautouse fixtureを試験モジュールの名前空間へ登録するため。
# pylint: disable=protected-access
"""termination_evidenceの契約テスト。"""

import pathlib
import shlex

import pytest

from agent_toolkit._hooks import termination_evidence
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.termination_evidence_support import _tool, human_input, public_decision
from agent_toolkit._testing.termination_evidence_support import isolated_session as isolated_session

pytestmark = pytest.mark.usefixtures("_forbid_backend_process")


@pytest.mark.parametrize(
    "case",
    [
        "cd",
        "pipeline",
        "assignment",
        "git-C",
        "commit-wrapper",
        "codex-skill",
        "plan-edit",
        "other-cd",
        "other-edit",
        "other-redirect",
        "unknown",
        "unknown-write",
        "unknown-git-C",
    ],
)
def test_chained_bash_work_is_attributed_to_cwd_repository(tmp_path, monkeypatch, case):
    """公開省略CLIまで通し、単一の所属・複数所属・不明を区別する。"""
    policy = "完了報告は指示まで省く。\n"
    repo = git_repository.init_repository(tmp_path / "repo", files={"CLAUDE.md": policy})
    other = git_repository.init_repository(tmp_path / "other", files={"README.md": "別の成果"})
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "evidence-test")
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    human_input(tmp_path, "request", "成果を作成する")
    # 同一作業に別リポジトリへの書込を混ぜた場合も省略を拒否する。
    if case.startswith("other-"):
        _tool(repo, "initial-edit", "Edit", {"file_path": str(repo / "CLAUDE.md")}, {"exit_code": 0}, codex=False)
    commands = {
        "cd": f"cd {shlex.quote(str(repo))} && git add file && git commit -m 変更",
        "pipeline": "git add file && git commit -m 変更 2>&1 | tail -3",
        "assignment": "GIT_EDITOR=true git commit -m 変更",
        "git-C": f"git -C {shlex.quote(str(repo))} commit -m 変更",
        "commit-wrapper": "atk run-script commit-with-timeout -- --timeout 3 -- -m 変更",
        "codex-skill": f"cd {shlex.quote(str(repo))} && cat plugin/skills/process-wi/SKILL.md",
        "other-cd": f"cd {shlex.quote(str(other))} && git commit -m 変更",
        "other-redirect": f"echo x > {shlex.quote(str(other / 'README.md'))}",
        "unknown": 'cd "$D" && git commit -m 変更',
        "unknown-write": 'cd "$D" && echo x > out.txt; git commit -m 変更',
        "unknown-git-C": 'git -C "$D" commit -m 変更',
    }
    if case in {"plan-edit", "other-edit"}:
        tool, data = (
            "Write",
            {
                "file_path": str(
                    (pathlib.Path.home() / ".claude/plans/1_test.md") if case == "plan-edit" else other / "README.md"
                )
            },
        )
    else:
        tool, data = "Bash", {"command": commands[case]}
    response = "---\nname: process-wi\n---\n" if case == "codex-skill" else {"exit_code": 0}
    if case == "codex-skill":
        skill = repo / "plugin/skills/process-wi/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("---\nname: process-wi\n---\n", encoding="utf-8")
        manifest = repo / "plugin/.claude-plugin/plugin.json"
        manifest.parent.mkdir()
        manifest.write_text('{"name":"agent-toolkit"}', encoding="utf-8")
    _tool(repo, "action", tool, data, response, codex=case == "codex-skill")
    document = {
        "session_id": "evidence-test",
        "action": "omit-completion",
        "work_id": "work-1",
        "input_id": "request",
        "reason": "適用規範に従う",
        "policy_file": str(repo / "CLAUDE.md"),
        "quote": policy,
        "target_repo": str(repo),
    }
    status, reason = public_decision(tmp_path, document)
    rejected = case.startswith("other-") or case.startswith("unknown")
    assert status == (2 if rejected else 0), reason
    work = termination_evidence.session_works({"session_id": "evidence-test"})[0][1]
    assert bool(termination_evidence.missing_completion(work)) is rejected
    termination_evidence.unscoped_self_edits({"session_id": "evidence-test"}, work)
    if case.startswith("unknown"):
        assert "特定できなかった成果作業" in reason and "commit" in reason
        assert "report-completion" in reason and "ユーザーへ報告" in reason
        assert "同じコマンドで再実行" not in reason


@pytest.mark.parametrize("tool", ["Bash", "PowerShell"])
@pytest.mark.parametrize("launcher", ["atk", "C:/tools/atk.cmd", r"'C:\tools\atk.cmd'", "atk.py"])
@pytest.mark.parametrize(
    ("arguments", "label"),
    [("wi add --type awi --body-file note.md", "WIの保存"), ("run-script plan-create -- --title 計画", "計画の保存")],
)
def test_atk_launcher_deliverables_are_recorded_for_both_shell_tools(tmp_path, tool, launcher, arguments, label):
    """配布するランチャーと両シェルツールの起動を同じ成果作業へ記録する。"""
    repo = git_repository.init_repository(tmp_path / "repo", files={"README.md": "成果"})
    prefix = "& " if tool == "PowerShell" else ""
    _tool(repo, "launcher", tool, {"command": prefix + launcher + " " + arguments}, {"exit_code": 0}, codex=False)
    works = termination_evidence.session_works({"session_id": "evidence-test"})
    assert len(works) == 1 and label in works[0][1]["deliverables"]
    assert works[0][1]["repositories"] == [str(repo)]
