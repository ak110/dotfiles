# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
# pylint: disable=protected-access
"""shell_checks_gitの契約テスト。"""

import pathlib

import pytest

from agent_toolkit._hooks.pretooluse import shell_checks
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.pretooluse_support import _additional_context, _run
from agent_toolkit._testing.shell_checks_support import _existing_work_contract as _existing_work_contract


class TestBashGitRevParseShortMultiple:
    """通常modeの単一revision検証へ複数revisionを渡す操作の遮断。"""

    @pytest.mark.parametrize(
        "command",
        [
            "git rev-parse --short=7 af6889b HEAD",
            "git rev-parse --short origin/develop origin/master",
            "git -C /tmp/repo rev-parse --short=7 HEAD~1 HEAD",
            "cd /tmp/repo && git rev-parse --verify --short=7 main develop",
            "git rev-parse --short --prefix sub A B",
            'git rev-parse --short=7 A B && git status; echo "rc=$?"',
            "git rev-parse --short --since 2026-01-01 HEAD",
            "git rev-parse --short --sq-quote a b",
            "git rev-parse --verify HEAD HEAD~1",
            "git rev-parse --verify --quiet HEAD HEAD~1",
            "for item in one; do git rev-parse --short HEAD HEAD~1; done",
            "if test -f marker; then git rev-parse --verify HEAD HEAD~1; fi",
        ],
    )
    @pytest.mark.parametrize("extra_payload", [{}, {"turn_id": "codex-turn"}])
    def test_warns_multiple_revisions(self, command: str, extra_payload: dict):
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}, **extra_payload})
        assert result.returncode == 0
        assert "単一リビジョン検証へ2つのリビジョン" in _additional_context(result)
        assert "<元のオプション> <revision>" in _additional_context(result)
        assert "終了コード128とは限らない" in _additional_context(result)

    def test_redirection_is_not_counted_as_revision(self):
        result = _run({"tool_name": "Bash", "tool_input": {"command": "git rev-parse --short=7 HEAD~1 HEAD > /tmp/x"}})
        assert result.returncode == 0
        assert "2つのリビジョン（HEAD~1、HEAD）" in _additional_context(result)

    @pytest.mark.parametrize(
        "command",
        [
            "git rev-parse --short=7 HEAD",
            "git rev-parse --short --prefix sub HEAD",
            "git rev-parse --short --default master HEAD",
            "git rev-parse --short --git-path HEAD HEAD",
            "git rev-parse --short --resolve-git-dir .git HEAD",
            "git rev-parse HEAD~1 HEAD",
            "git rev-parse --short=7 HEAD -- path/file",
            "git log --oneline HEAD~1 HEAD",
            "git rev-parse --short=7 HEAD > /tmp/x",
            "git rev-parse --short HEAD >/tmp/x",
            "git rev-parse --short HEAD 2>/dev/null",
            "git rev-parse --short HEAD < /dev/null",
            "git rev-parse --short=7 HEAD >> f",
            "git rev-parse --short $(git rev-parse HEAD)",
            'git rev-parse --short "$(git rev-parse HEAD)"',
            "git rev-parse --short `git rev-parse HEAD`",
            "git rev-parse --short $(printf '%s' $(git rev-parse HEAD))",
            "git rev-parse --short $REVISION",
            "git rev-parse --sq-quote --short a b",
            "git rev-parse --parseopt --short a b",
            "git rev-parse HEAD --short HEAD~1",
            "git rev-parse --short HEAD~1..HEAD",
            "git rev-parse --short -n 1 HEAD",
            "git rev-parse --abbrev-ref HEAD HEAD~1",
            "printf '%s' 'git rev-parse --short A B'",
        ],
    )
    def test_single_revision_or_other_command_not_warned(self, command: str):
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        assert result.returncode == 0
        assert "git rev-parse --short" not in result.stdout

    @pytest.mark.parametrize("options", [["--short"], ["--short=12"], ["--verify"], ["--verify", "--quiet"]])
    def test_individual_revision_fix_succeeds(self, options: list[str], tmp_path: pathlib.Path):
        """履歴を自ら準備し、案内した入力分割で元のoptionを保ったGit実行が成功する。"""
        repository = git_repository.init_repository(tmp_path / "repo", commit_message="最初のcommit")
        git_repository.commit_all(repository, "次のcommit")
        for revision in ["HEAD", "HEAD~1"]:
            result = git_repository.run_git(repository, "rev-parse", *options, revision, check=False)
            assert result.returncode == 0, result.stderr
            assert result.stdout.strip()


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("git -C sub commit -m text", ("commit", ("-m", "text"))),
        ("atk run-script commit-with-timeout -- --timeout 3 -- -m text", ("commit", ("-m", "text"))),
        ("/tools/atk run-script commit-with-timeout -- --timeout=3 -- -F -", ("commit", ("-F", "-"))),
        (
            "env FLAG=1 atk run-script commit-with-timeout -- --timeout 3 -- --amend --no-edit",
            ("commit", ("--amend", "--no-edit")),
        ),
        ("atk run-script other -- --timeout 3 -- -m text", None),
        ("atk run-script commit-with-timeout -- --help", None),
        ("atk run-script commit-with-timeout -- --timeout 3 -m text", None),
        ("echo atk run-script commit-with-timeout -- --timeout 3 -- -m text", None),
    ],
)
def test_git_subcommand_tokens_recognizes_only_the_fixed_commit_wrapper(
    command: str, expected: tuple[str, tuple[str, ...]] | None
) -> None:
    """専用起動の固定構文だけを通常commitへ対応付け、データと別scriptを除外する。"""
    segments = shell_checks.extract_execution_segments(command)
    assert len(segments) == 1
    assert shell_checks.git_subcommand_tokens(segments[0]) == expected
