"""scripts/claude_hook_permissionrequest.py の判定ロジックテスト。"""

import json
import pathlib
import subprocess

import claude_hook_permissionrequest as hook
import pytest

_SCRIPT_PATH = pathlib.Path(__file__).resolve().parent / "claude_hook_permissionrequest.py"


@pytest.fixture(name="home")
def _home(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """`Path.home()` をテスト用一時ディレクトリへ差し替える。"""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".claude" / "plans").mkdir(parents=True)
    return tmp_path


@pytest.fixture(name="repo")
def _repo(tmp_path: pathlib.Path) -> pathlib.Path:
    """擬似 Git ワークツリーを作成 (`.git/` ディレクトリと `.claude/` 配下を持つ)。"""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".claude" / "rules").mkdir(parents=True)
    return repo


class TestShouldAllow:
    """`should_allow` の判定動作。"""

    def test_home_claude_plans_file(self, home: pathlib.Path) -> None:
        assert hook.should_allow(str(home / ".claude" / "plans" / "x.md")) is True

    def test_home_claude_plans_subdir(self, home: pathlib.Path) -> None:
        assert hook.should_allow(str(home / ".claude" / "plans" / "sub" / "x.md")) is True

    def test_home_claude_other_subtree_not_allowed(self, home: pathlib.Path) -> None:
        assert hook.should_allow(str(home / ".claude" / "projects" / "x.md")) is False

    def test_home_claude_settings_not_allowed(self, home: pathlib.Path) -> None:
        assert hook.should_allow(str(home / ".claude" / "settings.json")) is False

    def test_repo_claude_file(self, home: pathlib.Path, repo: pathlib.Path) -> None:
        del home  # fixture を有効化するためだけに受け取る
        assert hook.should_allow(str(repo / ".claude" / "rules" / "test.md")) is True

    def test_repo_claude_top_level(self, home: pathlib.Path, repo: pathlib.Path) -> None:
        del home
        assert hook.should_allow(str(repo / ".claude" / "settings.json")) is True

    def test_outside_git_worktree_not_allowed(self, home: pathlib.Path, tmp_path: pathlib.Path) -> None:
        del home
        # `.git` を持たないディレクトリ配下の `.claude/` は対象外
        target = tmp_path / "no_git" / ".claude" / "x.md"
        target.parent.mkdir(parents=True)
        assert hook.should_allow(str(target)) is False

    def test_non_claude_path_not_allowed(self, home: pathlib.Path, repo: pathlib.Path) -> None:
        del home
        assert hook.should_allow(str(repo / "src" / "main.py")) is False

    def test_relative_path_rejected(self) -> None:
        assert hook.should_allow(".claude/plans/x.md") is False

    def test_empty_path_rejected(self) -> None:
        assert hook.should_allow("") is False


class TestShouldAllowBash:
    """`should_allow_bash` の判定動作。"""

    def test_rm_in_plans(self, home: pathlib.Path) -> None:
        assert hook.should_allow_bash(f"rm {home}/.claude/plans/x.md", str(home)) is True

    def test_rm_with_options_in_plans(self, home: pathlib.Path) -> None:
        assert hook.should_allow_bash(f"rm -rf {home}/.claude/plans/sub", str(home)) is True

    def test_rm_in_repo_claude(self, home: pathlib.Path, repo: pathlib.Path) -> None:
        del home
        assert hook.should_allow_bash(f"rm {repo}/.claude/rules/x.md", str(repo)) is True

    def test_mkdir_p_in_plans(self, home: pathlib.Path) -> None:
        assert hook.should_allow_bash(f"mkdir -p {home}/.claude/plans/sub", str(home)) is True

    def test_mv_within_plans(self, home: pathlib.Path) -> None:
        cmd = f"mv {home}/.claude/plans/a.md {home}/.claude/plans/b.md"
        assert hook.should_allow_bash(cmd, str(home)) is True

    def test_cp_within_plans(self, home: pathlib.Path) -> None:
        cmd = f"cp -r {home}/.claude/plans/a {home}/.claude/plans/b"
        assert hook.should_allow_bash(cmd, str(home)) is True

    def test_touch_in_plans(self, home: pathlib.Path) -> None:
        assert hook.should_allow_bash(f"touch {home}/.claude/plans/x.md", str(home)) is True

    def test_redirect_to_plans(self, home: pathlib.Path) -> None:
        cmd = f"echo hello > {home}/.claude/plans/x.md"
        assert hook.should_allow_bash(cmd, str(home)) is True

    def test_append_redirect_to_plans(self, home: pathlib.Path) -> None:
        cmd = f"echo hello >> {home}/.claude/plans/log.md"
        assert hook.should_allow_bash(cmd, str(home)) is True

    def test_redirect_with_arbitrary_command(self, home: pathlib.Path) -> None:
        # コマンド本体は問わずリダイレクト先パスのみで判定
        cmd = f"some-unknown-cmd arg1 arg2 > {home}/.claude/plans/x.md"
        assert hook.should_allow_bash(cmd, str(home)) is True

    def test_relative_path_with_cwd(self, home: pathlib.Path) -> None:
        cmd = "rm x.md"
        assert hook.should_allow_bash(cmd, str(home / ".claude" / "plans")) is True

    def test_quoted_path_with_space(self, home: pathlib.Path) -> None:
        target = home / ".claude" / "plans" / "a b.md"
        assert hook.should_allow_bash(f'rm "{target}"', str(home)) is True

    def test_mv_with_dst_outside_rejected(self, home: pathlib.Path) -> None:
        cmd = f"mv {home}/.claude/plans/a.md {home}/elsewhere.md"
        assert hook.should_allow_bash(cmd, str(home)) is False

    def test_rm_outside_target_rejected(self, home: pathlib.Path) -> None:
        assert hook.should_allow_bash(f"rm {home}/elsewhere.md", str(home)) is False

    def test_unsafe_metachar_pipe_rejected(self, home: pathlib.Path) -> None:
        cmd = f"rm {home}/.claude/plans/a.md | rm {home}/.claude/plans/b.md"
        assert hook.should_allow_bash(cmd, str(home)) is False

    def test_unsafe_metachar_semicolon_rejected(self, home: pathlib.Path) -> None:
        cmd = f"rm {home}/.claude/plans/a.md; rm {home}/.claude/plans/b.md"
        assert hook.should_allow_bash(cmd, str(home)) is False

    def test_unsafe_metachar_dollar_rejected(self, home: pathlib.Path) -> None:
        assert hook.should_allow_bash("rm $HOME/.claude/plans/a.md", str(home)) is False

    def test_unsafe_metachar_backtick_rejected(self, home: pathlib.Path) -> None:
        assert hook.should_allow_bash("rm `echo a.md`", str(home)) is False

    def test_unknown_command_rejected(self, home: pathlib.Path) -> None:
        # find は対象外コマンド
        cmd = f"find {home}/.claude/plans -delete"
        assert hook.should_allow_bash(cmd, str(home)) is False

    def test_redirect_without_target_rejected(self, home: pathlib.Path) -> None:
        assert hook.should_allow_bash("echo foo >", str(home)) is False

    def test_empty_command_rejected(self) -> None:
        assert hook.should_allow_bash("", "") is False

    def test_relative_path_without_cwd_rejected(self) -> None:
        assert hook.should_allow_bash("rm x.md", "") is False

    def test_unmatched_quote_rejected(self, home: pathlib.Path) -> None:
        # shlex.split が ValueError を送出する形
        assert hook.should_allow_bash('rm "unterminated', str(home)) is False


class TestEndToEnd:
    """サブプロセス経由で stdin / stdout の応答を検証する。"""

    def _run(self, payload: dict) -> tuple[int, str]:
        result = subprocess.run(
            ["uv", "run", "--script", str(_SCRIPT_PATH)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        return result.returncode, result.stdout

    def test_write_to_plans_returns_allow(self, home: pathlib.Path) -> None:
        payload = {
            "tool_name": "Write",
            "tool_input": {"file_path": str(home / ".claude" / "plans" / "x.md"), "content": "x"},
        }
        code, stdout = self._run(payload)
        assert code == 0
        assert json.loads(stdout) == {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {"behavior": "allow"},
            }
        }

    def test_bash_rm_in_plans_returns_allow(self, home: pathlib.Path) -> None:
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": f"rm {home}/.claude/plans/x.md"},
            "cwd": str(home),
        }
        code, stdout = self._run(payload)
        assert code == 0
        assert json.loads(stdout) == {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {"behavior": "allow"},
            }
        }

    def test_bash_ls_emits_nothing(self) -> None:
        # ls は対象外コマンドのため自動許可しない
        payload = {"tool_name": "Bash", "tool_input": {"command": "ls"}}
        code, stdout = self._run(payload)
        assert code == 0
        assert stdout == ""

    def test_unrelated_path_emits_nothing(self, home: pathlib.Path) -> None:
        payload = {
            "tool_name": "Write",
            "tool_input": {"file_path": str(home / "src" / "main.py"), "content": "x"},
        }
        code, stdout = self._run(payload)
        assert code == 0
        assert stdout == ""

    def test_invalid_json_input_emits_nothing(self) -> None:
        result = subprocess.run(
            ["uv", "run", "--script", str(_SCRIPT_PATH)],
            input="not-json",
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        assert result.returncode == 0
        assert result.stdout == ""
