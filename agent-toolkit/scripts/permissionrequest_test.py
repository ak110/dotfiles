"""agent-toolkit/scripts/permissionrequest.py の判定ロジックテスト。"""

import json
import pathlib

import _fork_runner
import _managed_temp
import permissionrequest as hook
import pytest

_SCRIPT_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "claude_hook.py"


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

    @pytest.mark.parametrize(
        ("command_template", "expected"),
        [
            (("rm {plans}/x.md", "{home}"), True),
            (("rm -rf {plans}/sub", "{home}"), True),
            (("rm --recursive --force {plans}/sub", "{home}"), True),
            (("rm -- {plans}/-draft.md", "{home}"), True),
            (("rm {repo}/.claude/rules/x.md", "{repo}"), True),
            (("mkdir -p {plans}/sub", "{home}"), True),
            (("mkdir --parents {plans}/sub", "{home}"), True),
            (("mv {plans}/a.md {plans}/b.md", "{home}"), True),
            (("cp -r {plans}/a {plans}/b", "{home}"), True),
            (("cp --recursive {plans}/a {plans}/b", "{home}"), True),
            (("cp --parents {plans}/a {plans}/backup", "{home}"), True),
            (("ln -s {plans}/a {plans}/b", "{home}"), True),
            (("chmod 600 {plans}/a", "{home}"), True),
            (("chmod -x {plans}/a", "{home}"), True),
            (("chmod -R 600 {plans}/a", "{home}"), True),
            (("chmod -- -x {plans}/a", "{home}"), True),
            (("chown aki {plans}/a", "{home}"), True),
            (("chown -R aki:staff {plans}/a", "{home}"), True),
            (("touch {plans}/x.md", "{home}"), True),
            (("echo hello > {plans}/x.md", "{home}"), True),
            (("echo hello >> {plans}/log.md", "{home}"), True),
            (("some-unknown-cmd arg1 arg2 > {plans}/x.md", "{home}"), False),
            (("rm x.md", "{plans}"), True),
            (('rm "{plans}/a b.md"', "{home}"), True),
            (("mv {plans}/a.md {home}/elsewhere.md", "{home}"), False),
            (("rm {home}/elsewhere.md", "{home}"), False),
            (("rm {plans}/a.md | rm {plans}/b.md", "{home}"), False),
            (("rm {plans}/a.md; rm {plans}/b.md", "{home}"), True),
            (("rm $HOME/.claude/plans/a.md", "{home}"), False),
            (("rm `echo a.md`", "{home}"), False),
            (("find {plans} -delete", "{home}"), False),
            (("echo foo >", "{home}"), False),
            (("", ""), False),
            (("rm x.md", ""), False),
            (('rm "unterminated', "{home}"), False),
            (("some-unknown-cmd arg1 arg2>{plans}/x.md", "{home}"), False),
            (("echo $(touch /outside)", "{home}"), False),
            (("echo '$(touch /outside)'", "{home}"), False),
            (('echo "$(touch /outside)"', "{home}"), False),
            (('echo "$var"', "{home}"), True),
            (("git reset --hard > {plans}/log", "{home}"), False),
            (("cp --target-directory=/outside {plans}/source", "{home}"), False),
            (("cp --target-directory /outside {plans}/source", "{home}"), False),
            (("cp --target-directory={plans}/out {plans}/source", "{home}"), False),
            (("cp -t {plans}/out {plans}/source", "{home}"), False),
            (("mv --target-directory={plans}/out {plans}/source", "{home}"), False),
            (("ln --target-directory={plans}/out {plans}/source", "{home}"), False),
            (("touch --reference={plans}/ref {plans}/target", "{home}"), False),
            (("chmod --reference={plans}/ref {plans}/target", "{home}"), False),
            (("chown --reference={plans}/ref {plans}/target", "{home}"), False),
            (("chmod 600 {home}/outside", "{home}"), False),
            (("chown aki {home}/outside", "{home}"), False),
            (("chmod 600", "{home}"), False),
            (("chown aki", "{home}"), False),
            (("cp {plans}/a.md {plans}/b.md && wc -l {plans}/b.md", "{home}"), True),
            (("cp {plans}/a.md {plans}/b.md&&wc -l {plans}/b.md", "{home}"), True),
            (("rm {plans}/a.md || rm {plans}/b.md", "{home}"), True),
            (("rm {plans}/a.md && rm {home}/elsewhere.md", "{home}"), False),
            (("wc -l -w {plans}/a.md", "{home}"), True),
            (("wc --lines {plans}/a.md", "{home}"), True),
            (("wc --unknown=value {plans}/a.md", "{home}"), False),
            (("wc --files0-from={plans}/list.txt", "{home}"), False),
            (("wc {plans}/a.md", "{home}"), True),
            (("wc -l {home}/elsewhere.md", "{home}"), False),
            (("rm {plans}/a.md &", "{home}"), False),
            (('echo "a && b" > {plans}/a.md', "{home}"), False),
            (("rm {plans}/a.md && rm {plans}/b.md | cat", "{home}"), False),
            (("rm {plans}/a.md && rm {plans}/b.md; echo done", "{home}"), True),
            (("rm {plans}/a.md && rm {plans}/b.md; rm {home}/elsewhere.md", "{home}"), False),
            (
                ("mkdir -p {plans}\ncp /tmp/scratchpad/x.md {plans}/x.md\nwc -l {plans}/x.md", "{home}"),
                True,
            ),
            (
                ("mkdir -p {plans}; cp /tmp/scratchpad/x.md {plans}/x.md; wc -l {plans}/x.md", "{home}"),
                True,
            ),
            (("cd {plans}", "{home}"), True),
            (("cd /etc", "{home}"), False),
            (("mkdir -p {plans}\nuv run x.py", "{home}"), False),
            (("mkdir -p {plans}\n\n", "{home}"), True),
            (("head -l {plans}/a.md", "{home}"), False),
            (('rm "unterminated && wc -l {plans}/a.md', "{home}"), False),
            (("rm {plans}/a.md |& malicious_tool", "{home}"), False),
            (("echo hi 2>& {plans}/log.txt", "{home}"), False),
            (("echo hi &> {plans}/out.txt", "{home}"), False),
            # 対象配下と対象外パスへの操作が混在するコマンド列は全体を拒否する。
            (("cp {repo}/AGENTS.md {home}/elsewhere.md && rm {repo}/.agents/new.md", "{repo}"), False),
        ],
    )
    def test_bash_command_allowance(
        self,
        home: pathlib.Path,
        repo: pathlib.Path,
        command_template: tuple[str, str],
        expected: bool,
    ) -> None:
        plans = home / ".claude" / "plans"
        command_text_template, cwd_template = command_template
        format_args = {"home": home, "repo": repo, "plans": plans}
        cmd = command_text_template.format(**format_args)
        cwd = cwd_template.format(**format_args)
        assert hook.should_allow_bash(cmd, cwd) is expected

    @pytest.mark.parametrize("command", [";", "\n", ";\n"])
    def test_separator_only_command_rejected(self, command: str, home: pathlib.Path) -> None:
        assert hook.should_allow_bash(command, str(home)) is False

    @pytest.mark.parametrize(
        "command",
        [
            'echo "任意文字列"',
            'echo "$var"',
            'echo "a; b"',
        ],
    )
    def test_echo_standalone_allowed(self, command: str, home: pathlib.Path) -> None:
        assert hook.should_allow_bash(command, str(home)) is True

    def test_rm_in_tmp_allowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # `/tmp/` 配下は一時ファイル領域として自動許可対象に含める
        monkeypatch.setattr(hook, "_TMP_ROOT_STR", "/tmp")
        assert hook.should_allow_bash("rm /tmp/foo.txt", "/tmp") is True

    def test_managed_temp_create_and_cleanup_allowed(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """完全一致するcreateと真正性検証済みcleanupだけを許可する。"""
        temp_root = tmp_path / "temp"
        temp_root.mkdir()
        monkeypatch.setattr(_managed_temp.tempfile, "gettempdir", lambda: str(temp_root))
        monkeypatch.setattr(_managed_temp, "_state_root_path", lambda: tmp_path / "external-state")
        target = _managed_temp.create_managed_temp("permission-test")

        assert hook.should_allow_bash("atk managed-temp create --prefix agent-work", str(tmp_path)) is True
        assert hook.should_allow_bash(f"atk managed-temp cleanup --path {target}", str(tmp_path)) is True
        assert hook.should_allow_bash("atk managed-temp list --prefix agent-work", str(tmp_path)) is True
        assert hook.should_allow_bash("atk managed-temp list --prefix agent-work extra", str(tmp_path)) is False

    @pytest.mark.parametrize(
        "command",
        [
            "atk managed-temp create --prefix UPPER",
            "atk managed-temp create --prefix agent-work extra",
            "atk managed-temp cleanup --path relative",
            "atk managed-temp cleanup --path /tmp/unmanaged",
            "atk managed-temp validate --path /tmp/unmanaged",
            "atk managed-temp",
            "atk mq rm --all",
            "rm -rf /tmp/unmanaged",
            "atk managed-temp create --prefix foo#; printf permission-bypass-observed",
            "atk managed-temp create --prefix agent-work > /tmp/log",
            "atk managed-temp create --prefix agent-work extra > /tmp/log",
            "atk --verbose managed-temp create --prefix agent-work",
            "atk managed-temp validate --path /tmp/unmanaged > /tmp/log",
            "echo ok; atk managed-temp validate --path /tmp/unmanaged > /tmp/log",
            "echo ok && atk managed-temp create --prefix agent-work extra > /tmp/log",
            "echo ok || atk managed-temp create --prefix agent-work > /tmp/log",
            "echo ok | atk managed-temp create --prefix agent-work",
            "atk managed-temp create --prefix agent-work && echo ok",
            "(atk managed-temp create --prefix agent-work)",
            "env atk managed-temp create --prefix agent-work",
            "/opt/agent-toolkit/bin/atk managed-temp create --prefix agent-work",
            "./agent-toolkit/bin/atk managed-temp create --prefix agent-work",
            "echo ok && /opt/agent-toolkit/bin/atk managed-temp create --prefix agent-work",
            "echo ok; ./agent-toolkit/bin/atk managed-temp create --prefix agent-work",
            'printf "%s\\n" "atk managed-temp create --prefix agent-work"',
        ],
    )
    def test_managed_temp_noncanonical_command_not_allowed(self, command: str, tmp_path: pathlib.Path) -> None:
        """不正入力と生の削除コマンドを管理一時領域経路では許可しない。"""
        assert hook.should_allow_bash(command, str(tmp_path)) is False

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
        result = _fork_runner.run_script(
            _SCRIPT_PATH,
            argv=("permissionrequest",),
            input=json.dumps(payload),
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

    def test_bash_managed_temp_create_returns_allow(self) -> None:
        """canonical launcherのcreate確認を自動許可する。"""
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "atk managed-temp create --prefix agent-work"},
            "cwd": "/tmp",
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
        result = _fork_runner.run_script(
            _SCRIPT_PATH,
            argv=("permissionrequest",),
            input="not-json",
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
