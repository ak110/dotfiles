"""agent-toolkit/scripts/permissionrequest.py の判定ロジックテスト。"""

import json
import pathlib
import subprocess

import permissionrequest as hook
import pytest

_SCRIPT_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "permissionrequest.py"


@pytest.fixture(name="_disable_tmp_root_allow", autouse=True)
def _disable_tmp_root_allow(monkeypatch: pytest.MonkeyPatch) -> None:
    """`/tmp` 全許可判定を無効化する。

    `tmp_path` fixture 由来の `home`・`repo` は `/tmp` 配下に配置される。
    実装の `/tmp` 全許可判定と衝突すると、対象外パスであっても自動的に許可され
    既存テストの意図が損なわれる。テスト時のみ判定基準を存在しないパスへ差し替える。
    `/tmp` 全許可を検証する個別テストではローカルに `_TMP_ROOT_STR` を復元する。

    サブプロセス経由（`TestEndToEnd`）で `permissionrequest.py` を別プロセスとして
    起動するテストには本 fixture の差し替えが届かない。当該プロセス内の
    `_TMP_ROOT_STR` は既定値 `"/tmp"` のままとなるため、`TestEndToEnd` で
    「拒否されるはず」を検証するテストでは `home`・`repo`（`/tmp` 配下）由来のパスを
    使わず、`/tmp` 配下でない絶対パスを個別に指定する必要がある。
    """
    monkeypatch.setattr(hook, "_TMP_ROOT_STR", "/__tmp_root_disabled__")


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

    @pytest.mark.parametrize(
        ("relative_path", "expected"),
        [
            # AGENTS.md はリポジトリ直下・サブディレクトリのいずれも許可。
            ("AGENTS.md", True),
            ("subdir/AGENTS.md", True),
            # `.agents/` 配下はパス構成要素一致で許可。
            (".agents/skill.md", True),
            (".agents/skills/foo.md", True),
            ("subdir/.agents/foo.md", True),
            # 名前が完全一致しないファイルは拒否（大文字小文字差異の境界）。
            ("agents.md", False),
            ("Agents.md", False),
            ("AGENTS.MD", False),
            ("AGENTS.md.bak", False),
            ("MY_AGENTS.md", False),
            # ディレクトリ名が完全一致しないものは拒否。
            ("agents/foo.md", False),
            (".agent/foo.md", False),
        ],
    )
    def test_repo_path_allowance(self, home: pathlib.Path, repo: pathlib.Path, relative_path: str, expected: bool) -> None:
        del home
        assert hook.should_allow(str(repo / relative_path)) is expected

    def test_home_claude_agents_md_not_allowed(self, home: pathlib.Path) -> None:
        # `~/.claude/AGENTS.md` は配布先誤編集の警告経路維持のため拒否する。
        assert hook.should_allow(str(home / ".claude" / "AGENTS.md")) is False

    def test_home_claude_dot_agents_not_allowed(self, home: pathlib.Path) -> None:
        # `~/.claude/.agents/` 配下も同様に拒否する。
        assert hook.should_allow(str(home / ".claude" / ".agents" / "x.md")) is False

    def test_agents_md_outside_git_worktree_not_allowed(self, home: pathlib.Path, tmp_path: pathlib.Path) -> None:
        del home
        target = tmp_path / "no_git" / "AGENTS.md"
        target.parent.mkdir(parents=True)
        assert hook.should_allow(str(target)) is False

    def test_dot_agents_outside_git_worktree_not_allowed(self, home: pathlib.Path, tmp_path: pathlib.Path) -> None:
        del home
        target = tmp_path / "no_git" / ".agents" / "x.md"
        target.parent.mkdir(parents=True)
        assert hook.should_allow(str(target)) is False

    def test_scratchpad_component_under_tmp_allowed(self, home: pathlib.Path) -> None:
        del home
        assert hook.should_allow("/tmp/claude-1000/xxx/scratchpad/foo.md") is True

    def test_scratchpad_component_under_home_allowed(self, home: pathlib.Path) -> None:
        assert hook.should_allow(str(home / ".claude" / "scratchpad" / "bar.md")) is True

    def test_arbitrary_path_under_tmp_allowed(self, home: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # `/tmp/` 配下は scratchpad 構成要素の有無を問わず自動許可対象に含める
        del home
        monkeypatch.setattr(hook, "_TMP_ROOT_STR", "/tmp")
        assert hook.should_allow("/tmp/random/foo.md") is True

    def test_scratchpad_outside_tmp_and_home_not_allowed(self) -> None:
        assert hook.should_allow("/var/scratchpad/foo.md") is False

    def test_scratchpad_in_filename_only_not_allowed(self, home: pathlib.Path) -> None:
        assert hook.should_allow(str(home / "scratchpad-but-not-dir.md")) is False


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
        # `_tokenize` 内の `shlex.shlex` が ValueError を送出する形
        assert hook.should_allow_bash('rm "unterminated', str(home)) is False

    def test_arbitrary_command_with_no_space_redirect_allowed(self, home: pathlib.Path) -> None:
        # `shlex.shlex(punctuation_chars=True)` は空白なしリダイレクトも独立トークン化する。
        # 対象配下パスへのリダイレクトなら任意コマンドも許容される。
        cmd = f"some-unknown-cmd arg1 arg2>{home}/.claude/plans/x.md"
        assert hook.should_allow_bash(cmd, str(home)) is True

    def test_and_composed_cp_and_wc_allowed(self, home: pathlib.Path) -> None:
        plans = home / ".claude" / "plans"
        cmd = f"cp {plans}/a.md {plans}/b.md && wc -l {plans}/b.md"
        assert hook.should_allow_bash(cmd, str(home)) is True

    def test_and_composed_without_spaces_allowed(self, home: pathlib.Path) -> None:
        plans = home / ".claude" / "plans"
        cmd = f"cp {plans}/a.md {plans}/b.md&&wc -l {plans}/b.md"
        assert hook.should_allow_bash(cmd, str(home)) is True

    def test_or_composed_allowed(self, home: pathlib.Path) -> None:
        plans = home / ".claude" / "plans"
        cmd = f"rm {plans}/a.md || rm {plans}/b.md"
        assert hook.should_allow_bash(cmd, str(home)) is True

    def test_and_composed_second_subcommand_outside_rejected(self, home: pathlib.Path) -> None:
        plans = home / ".claude" / "plans"
        cmd = f"rm {plans}/a.md && rm {home}/elsewhere.md"
        assert hook.should_allow_bash(cmd, str(home)) is False

    def test_wc_bool_options_allowed(self, home: pathlib.Path) -> None:
        plans = home / ".claude" / "plans"
        assert hook.should_allow_bash(f"wc -l -w {plans}/a.md", str(home)) is True

    def test_wc_long_bool_option_allowed(self, home: pathlib.Path) -> None:
        plans = home / ".claude" / "plans"
        assert hook.should_allow_bash(f"wc --lines {plans}/a.md", str(home)) is True

    def test_wc_long_bool_option_with_equals_rejected_when_unknown(self, home: pathlib.Path) -> None:
        plans = home / ".claude" / "plans"
        assert hook.should_allow_bash(f"wc --unknown=value {plans}/a.md", str(home)) is False

    def test_wc_files0_from_option_rejected(self, home: pathlib.Path) -> None:
        plans = home / ".claude" / "plans"
        assert hook.should_allow_bash(f"wc --files0-from={plans}/list.txt", str(home)) is False

    def test_wc_no_options_allowed(self, home: pathlib.Path) -> None:
        plans = home / ".claude" / "plans"
        assert hook.should_allow_bash(f"wc {plans}/a.md", str(home)) is True

    def test_wc_outside_path_rejected(self, home: pathlib.Path) -> None:
        assert hook.should_allow_bash(f"wc -l {home}/elsewhere.md", str(home)) is False

    def test_background_ampersand_rejected(self, home: pathlib.Path) -> None:
        plans = home / ".claude" / "plans"
        assert hook.should_allow_bash(f"rm {plans}/a.md &", str(home)) is False

    def test_quoted_ampersand_pair_not_split(self, home: pathlib.Path) -> None:
        plans = home / ".claude" / "plans"
        # 引用符内の `&&` は `shlex.shlex(posix=True)` で外側の引用符が外れ、
        # トークン列に `a && b` として現れる。単独 `&`・`|` を含む複合文字列は
        # 「`&&` と `||` 以外で `&` または `|` を含むトークン」として拒否される。
        cmd = f'echo "a && b" > {plans}/a.md'
        assert hook.should_allow_bash(cmd, str(home)) is False

    def test_unsafe_metachar_pipe_rejected_still(self, home: pathlib.Path) -> None:
        plans = home / ".claude" / "plans"
        cmd = f"rm {plans}/a.md && rm {plans}/b.md | cat"
        assert hook.should_allow_bash(cmd, str(home)) is False

    def test_unsafe_metachar_semicolon_rejected_still(self, home: pathlib.Path) -> None:
        plans = home / ".claude" / "plans"
        cmd = f"rm {plans}/a.md && rm {plans}/b.md; echo done"
        assert hook.should_allow_bash(cmd, str(home)) is False

    def test_unknown_read_op_rejected(self, home: pathlib.Path) -> None:
        # head は `_BASH_READ_OPS` 未収載のため、既存の対象外コマンド拒否と同様に拒否する
        plans = home / ".claude" / "plans"
        assert hook.should_allow_bash(f"head -l {plans}/a.md", str(home)) is False

    def test_unmatched_quote_in_shlex_returns_false(self, home: pathlib.Path) -> None:
        # punctuation_chars 対応の shlex でも不正クォートは ValueError を送出し False を返す
        plans = home / ".claude" / "plans"
        cmd = f'rm "unterminated && wc -l {plans}/a.md'
        assert hook.should_allow_bash(cmd, str(home)) is False

    def test_pipe_ampersand_operator_rejected(self, home: pathlib.Path) -> None:
        # `|&`（stdout+stderrパイプ）は複合演算子として1トークンで扱われ、拒否対象。
        plans = home / ".claude" / "plans"
        cmd = f"rm {plans}/a.md |& malicious_tool"
        assert hook.should_allow_bash(cmd, str(home)) is False

    def test_gt_ampersand_operator_rejected(self, home: pathlib.Path) -> None:
        # `>&`は複合演算子として1トークンで扱われ、拒否対象。
        # bash仕様上、対象がfd番号の場合はfd複製、パス等の非fd番号対象なら結合リダイレクトとして解釈される。
        # ここでは`2>& path`の形で検証する。bash実行時は曖昧なリダイレクトエラーとなる形だが、
        # 本テストはトークン化と拒否判定のみを対象とし実行成否は問わない。
        plans = home / ".claude" / "plans"
        cmd = f"echo hi 2>& {plans}/log.txt"
        assert hook.should_allow_bash(cmd, str(home)) is False

    def test_ampersand_gt_operator_rejected(self, home: pathlib.Path) -> None:
        # `&>`はstdout+stderr結合リダイレクトの複合演算子として1トークンで扱われ、拒否対象。
        plans = home / ".claude" / "plans"
        cmd = f"echo hi &> {plans}/out.txt"
        assert hook.should_allow_bash(cmd, str(home)) is False

    def test_rm_in_tmp_allowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # `/tmp/` 配下は一時ファイル領域として自動許可対象に含める
        monkeypatch.setattr(hook, "_TMP_ROOT_STR", "/tmp")
        assert hook.should_allow_bash("rm /tmp/foo.txt", "/tmp") is True

    def test_wc_in_tmp_allowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(hook, "_TMP_ROOT_STR", "/tmp")
        assert hook.should_allow_bash("wc -l /tmp/foo.txt", "/tmp") is True

    @pytest.mark.parametrize(
        ("command_template", "expected"),
        [
            # AGENTS.md・`.agents/` 配下への安全コマンドはすべて許可。
            ("rm {repo}/AGENTS.md", True),
            ("rm -f {repo}/AGENTS.md", True),
            ("touch {repo}/AGENTS.md", True),
            ("mv {repo}/AGENTS.md {repo}/sub/AGENTS.md", True),
            ("echo hello > {repo}/AGENTS.md", True),
            ("echo hello >> {repo}/AGENTS.md", True),
            ("rm {repo}/.agents/skill.md", True),
            ("touch {repo}/.agents/new.md", True),
            ("mv {repo}/.agents/a.md {repo}/.agents/b.md", True),
            # 一方が Git ワークツリー外（境界外）の場合は拒否。
            ("mv {repo}/AGENTS.md {home}/AGENTS.md", False),
            ("mv {repo}/.agents/a.md {home}/a.md", False),
            ("cp {repo}/AGENTS.md {home}/AGENTS.md", False),
        ],
    )
    def test_bash_agents_paths(
        self,
        home: pathlib.Path,
        repo: pathlib.Path,
        command_template: str,
        expected: bool,
    ) -> None:
        cmd = command_template.format(home=home, repo=repo)
        assert hook.should_allow_bash(cmd, str(repo)) is expected

    def test_rm_in_scratchpad_under_tmp_allowed(self, home: pathlib.Path) -> None:
        del home
        assert hook.should_allow_bash("rm /tmp/claude-1000/xxx/scratchpad/foo.md", "/tmp") is True

    def test_mv_within_scratchpad_under_tmp_allowed(self, home: pathlib.Path) -> None:
        del home
        cmd = "mv /tmp/claude-1000/xxx/scratchpad/a.md /tmp/claude-1000/xxx/scratchpad/b.md"
        assert hook.should_allow_bash(cmd, "/tmp") is True

    def test_mv_scratchpad_to_outside_rejected(self, home: pathlib.Path) -> None:
        cmd = f"mv /tmp/claude-1000/xxx/scratchpad/a.md {home}/dotfiles/other.md"
        assert hook.should_allow_bash(cmd, "/tmp") is False

    def test_rm_scratchpad_outside_tmp_and_home_rejected(self) -> None:
        assert hook.should_allow_bash("rm /var/scratchpad/foo.md", "/var") is False


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

    def test_write_to_agents_md_returns_allow(self, home: pathlib.Path, repo: pathlib.Path) -> None:
        del home
        payload = {
            "tool_name": "Write",
            "tool_input": {"file_path": str(repo / "AGENTS.md"), "content": "x"},
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

    def test_bash_cp_and_wc_in_scratchpad_and_plans_returns_allow(self, home: pathlib.Path) -> None:
        scratchpad = home / ".claude" / "scratchpad"
        scratchpad.mkdir(parents=True)
        plans = home / ".claude" / "plans"
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": f"cp {scratchpad}/x.md {plans}/y.md && wc -l {plans}/y.md"},
            "cwd": str(home),
        }
        code, stdout = self._run(payload)
        assert code == 0
        assert json.loads(stdout)["hookSpecificOutput"]["decision"]["behavior"] == "allow"

    def test_bash_ls_emits_nothing(self) -> None:
        # ls は対象外コマンドのため自動許可しない
        payload = {"tool_name": "Bash", "tool_input": {"command": "ls"}}
        code, stdout = self._run(payload)
        assert code == 0
        assert stdout == ""

    def test_unrelated_path_emits_nothing(self, home: pathlib.Path) -> None:
        payload = {
            "tool_name": "Write",
            # `home` は `tmp_path`（`/tmp` 配下）に配置されるため、`/tmp` 全許可判定と
            # 衝突する。フックが自動許可の対象と判定しないパスを検証するため
            # `/tmp` 配下でない絶対パスを直接指定する。
            "tool_input": {"file_path": "/nonexistent/src/main.py", "content": "x"},
        }
        del home
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

    @pytest.mark.parametrize("tool_name", ["Write", "Edit", "MultiEdit"])
    def test_file_tools_scratchpad_return_allow(self, home: pathlib.Path, tool_name: str) -> None:
        del home
        payload = {
            "tool_name": tool_name,
            "tool_input": {"file_path": "/tmp/claude-1000/xxx/scratchpad/foo.md"},
        }
        code, stdout = self._run(payload)
        assert code == 0
        assert json.loads(stdout) == {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {"behavior": "allow"},
            }
        }
