"""plugins/agent-toolkit/scripts/pretooluse.py のテスト。

PreToolUse 統合フック (mojibake / ps1 EOL 等) のテスト。
独立スクリプトなので subprocess で起動し exit code・stderr・stdout を検証する。
"""

import json
import os
import pathlib
import subprocess
import sys

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "pretooluse.py"
_PLUGIN_MANIFEST = pathlib.Path(__file__).resolve().parents[1] / ".claude-plugin" / "plugin.json"
_MARKETPLACE_MANIFEST = pathlib.Path(__file__).resolve().parents[3] / ".claude-plugin" / "marketplace.json"


def _run(payload: object, env_overrides: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        [sys.executable, str(_SCRIPT)],
        input=text,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _write_session_state(state_dir: pathlib.Path, session_id: str, state: dict) -> None:
    path = state_dir / f"claude-agent-toolkit-{session_id}.json"
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


class TestMojibakeCheck:
    """文字化け (U+FFFD) 検出。"""

    def test_write_with_mojibake(self):
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "/tmp/a.txt", "content": "hello \ufffd world"}})
        assert result.returncode == 2
        assert "U+FFFD" in result.stderr
        # LLM 宛てメッセージ規約: プレフィックスとサフィックスが付与されていること。
        assert "[auto-generated: agent-toolkit/pretooluse]" in result.stderr
        assert "Auto-generated hook notice" in result.stderr

    def test_edit_with_mojibake(self):
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {"file_path": "/tmp/a.txt", "old_string": "foo", "new_string": "bar\ufffd"},
            }
        )
        assert result.returncode == 2

    def test_multiedit_with_mojibake(self):
        result = _run(
            {
                "tool_name": "MultiEdit",
                "tool_input": {
                    "file_path": "/tmp/a.txt",
                    "edits": [
                        {"old_string": "a", "new_string": "b"},
                        {"old_string": "c", "new_string": "\ufffd"},
                    ],
                },
            }
        )
        assert result.returncode == 2

    def test_old_string_mojibake_is_allowed(self):
        """old_string 内の文字化けは既存修復を妨げないため通す。"""
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {"file_path": "/tmp/a.txt", "old_string": "壊れた\ufffd文字", "new_string": "壊れた文字"},
            }
        )
        assert result.returncode == 0


class TestPs1EolCheck:
    """PowerShell ファイルへの LF-only 書き込み検出。"""

    def test_ps1_with_lf_only_blocks(self):
        content = "Set-StrictMode\nWrite-Host 'x'\n"
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "C:/x/a.ps1", "content": content}})
        assert result.returncode == 2
        assert "LF-only" in result.stderr

    def test_ps1_tmpl_edit_with_lf_only_allowed(self):
        """Edit は内部的に CRLF を維持するため、LF-only でもブロックしない。"""
        content = "Set-StrictMode\n{{ .chezmoi.homeDir }}\n"
        result = _run({"tool_name": "Edit", "tool_input": {"file_path": "./a.ps1.tmpl", "new_string": content}})
        assert result.returncode == 0

    def test_ps1_tmpl_write_with_lf_only_blocks(self):
        """Write は LF のまま書き込むためブロックする。"""
        content = "Set-StrictMode\n{{ .chezmoi.homeDir }}\n"
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "./a.ps1.tmpl", "content": content}})
        assert result.returncode == 2

    def test_ps1_with_crlf_allowed(self):
        content = "Set-StrictMode\r\nWrite-Host 'x'\r\n"
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "a.ps1", "content": content}})
        assert result.returncode == 0

    def test_non_ps1_with_lf_only_allowed(self):
        """対象拡張子でなければ LF-only は関知しない。"""
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "a.txt", "content": "hello\nworld\n"}})
        assert result.returncode == 0

    def test_ps1_single_line_edit_allowed(self):
        """改行を含まない 1 行の Edit は誤検出を避けて通す。"""
        result = _run({"tool_name": "Edit", "tool_input": {"file_path": "a.ps1", "old_string": "Old", "new_string": "New"}})
        assert result.returncode == 0


class TestLockfilesCheck:
    """lockfile / 生成物ディレクトリの直接編集ブロック。"""

    @pytest.mark.parametrize(
        "file_path",
        [
            "uv.lock",
            "/home/user/proj/uv.lock",
            "pnpm-lock.yaml",
            "sub/pnpm-lock.yaml",
            "package-lock.json",
            "yarn.lock",
            "Cargo.lock",
            "crates/sub/Cargo.lock",
            "mise.lock",
            ".venv/lib/python3.12/site-packages/x.py",
            "node_modules/pkg/index.js",
        ],
    )
    def test_write_blocked(self, file_path: str):
        result = _run({"tool_name": "Write", "tool_input": {"file_path": file_path, "content": "x"}})
        assert result.returncode == 2
        assert "direct edit" in result.stderr

    def test_edit_cargo_lock_blocked(self):
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {"file_path": "Cargo.lock", "old_string": "a", "new_string": "b"},
            }
        )
        assert result.returncode == 2
        assert "cargo add" in result.stderr

    def test_normal_file_allowed(self):
        """lockfile 名を部分的に含むだけのパスは通す (例: uv.lock.bak)。"""
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "uv.lock.bak", "content": "x"}})
        assert result.returncode == 0


class TestSecretsCheck:
    """シークレット/鍵ファイルの直接編集ブロック。"""

    @pytest.mark.parametrize(
        "file_path",
        [
            ".env",
            ".env.local",
            "app/.env.production",
            ".encrypt_key",
            ".secret_key",
            "github_action",
            "keys/github_action.pub",
            "certs/server.pem",
            "private.key",
        ],
    )
    def test_blocked(self, file_path: str):
        result = _run({"tool_name": "Write", "tool_input": {"file_path": file_path, "content": "x"}})
        assert result.returncode == 2
        assert "secret" in result.stderr

    @pytest.mark.parametrize(
        "file_path",
        [
            ".env.example",
            ".env.sample",
            "config.env-example",
            "private-sample",
        ],
    )
    def test_example_allowed(self, file_path: str):
        result = _run({"tool_name": "Write", "tool_input": {"file_path": file_path, "content": "x"}})
        assert result.returncode == 0


class TestManifestCheck:
    """manifest 手編集の警告 (warn のみ、exit code は 0)。"""

    def test_pyproject_toml_warns(self):
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {"file_path": "pyproject.toml", "old_string": "a", "new_string": "b"},
            }
        )
        assert result.returncode == 0
        assert "pyproject.toml" in result.stderr
        assert "uv add" in result.stderr

    def test_package_json_warns(self):
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": "app/package.json", "content": "{}"},
            }
        )
        assert result.returncode == 0
        assert "package.json" in result.stderr
        assert "pnpm add" in result.stderr

    def test_normal_file_no_warn(self):
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "foo.txt", "content": "x"}})
        assert result.returncode == 0
        assert result.stderr == ""


class TestHomePathCheck:
    """ホームディレクトリ絶対パス混入の警告 (warn のみ)。"""

    _HOME = str(pathlib.Path.home())

    def test_home_path_in_content_warns(self):
        content = f"config_path = '{self._HOME}/myproj/config.yaml'\n"
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "src/app.py", "content": content}})
        assert result.returncode == 0
        assert "home directory" in result.stderr

    def test_home_path_in_local_md_skipped(self):
        content = f"See {self._HOME}/proj for details."
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "CLAUDE.local.md", "content": content}})
        assert result.returncode == 0
        assert result.stderr == ""

    def test_home_path_in_settings_local_json_skipped(self):
        content = f'{{"path": "{self._HOME}/x"}}'
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": ".claude/settings.local.json", "content": content},
            }
        )
        assert result.returncode == 0
        assert result.stderr == ""

    def test_no_home_path_no_warn(self):
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": "src/app.py", "content": "x = '/other/path'\n"},
            }
        )
        assert result.returncode == 0
        assert result.stderr == ""

    def test_home_path_does_not_block(self):
        """warn なので exit code は 0 のまま (block にならない)。"""
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {"file_path": "README.md", "old_string": "a", "new_string": f"{self._HOME}/x"},
            }
        )
        assert result.returncode == 0


class TestPlanSkillInvokedCheck:
    """plan file 書き込み時の plan-mode スキル先行呼び出し未確認警告 (warn のみ)。"""

    @staticmethod
    def _setup_plan_env(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path, dict[str, str]]:
        home = tmp_path / "home"
        plans = home / ".claude" / "plans"
        plans.mkdir(parents=True)
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        env = {
            "HOME": str(home),
            "TMPDIR": str(state_dir),
            "TEMP": str(state_dir),
            "TMP": str(state_dir),
        }
        return home, plans, env

    @staticmethod
    def _has_warn(result: subprocess.CompletedProcess[str], keyword: str) -> bool:
        if not result.stdout.strip():
            return False
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return False
        ctx = data.get("hookSpecificOutput", {}).get("additionalContext", "")
        return keyword in ctx

    def test_warns_when_state_absent(self, tmp_path: pathlib.Path):
        _, plans, env = self._setup_plan_env(tmp_path)
        plan_path = plans / "sample.md"
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan_path), "content": "# t\n"},
                "session_id": "no-skill",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        assert self._has_warn(result, "plan-mode skill")
        assert self._has_warn(result, "[auto-generated: agent-toolkit/pretooluse][warn]")

    def test_skipped_when_skill_invoked(self, tmp_path: pathlib.Path):
        _, plans, env = self._setup_plan_env(tmp_path)
        sid = "skill-ok"
        _write_session_state(pathlib.Path(env["TMPDIR"]), sid, {"plan_mode_skill_invoked": True})
        plan_path = plans / "sample.md"
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan_path), "content": "# t\n"},
                "session_id": sid,
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        assert result.stdout == ""

    def test_non_plan_path_not_warned(self, tmp_path: pathlib.Path):
        _, _, env = self._setup_plan_env(tmp_path)
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(tmp_path / "other.md"), "content": "# t\n"},
                "session_id": "non-plan",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        assert result.stdout == ""

    def test_review_md_not_warned(self, tmp_path: pathlib.Path):
        """`*.review.md` は副次ファイルのため警告対象外。"""
        _, plans, env = self._setup_plan_env(tmp_path)
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plans / "sample.review.md"), "content": "# t\n"},
                "session_id": "review",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        assert result.stdout == ""


class TestGeneralBehavior:
    """統合スクリプト共通の振る舞い。"""

    @pytest.mark.parametrize(
        "payload",
        [
            # Write/Edit/MultiEdit 以外は全て通す
            {"tool_name": "Bash", "tool_input": {"command": "echo \ufffd"}},
            # tool_input が欠落していても通す
            {"tool_name": "Write"},
            # 正常な日本語は通す
            {"tool_name": "Write", "tool_input": {"file_path": "a.txt", "content": "こんにちは世界"}},
        ],
    )
    def test_allowed(self, payload: dict):
        result = _run(payload)
        assert result.returncode == 0

    def test_invalid_json(self):
        """不正 JSON はフックを無効化 (安全側)。"""
        result = _run("this is not json")
        assert result.returncode == 0


class TestManifestSsot:
    """plugin.json と marketplace.json の SSOT 整合性。

    version / description / name を 2 箇所で重複管理しているため、
    片方だけ更新して配布されない事故を防ぐためのハード チェック。
    """

    def test_plugin_manifest_matches_marketplace(self):
        plugin_manifest = json.loads(_PLUGIN_MANIFEST.read_text(encoding="utf-8"))
        marketplace = json.loads(_MARKETPLACE_MANIFEST.read_text(encoding="utf-8"))

        entries = [p for p in marketplace["plugins"] if p["name"] == plugin_manifest["name"]]
        assert len(entries) == 1, f"marketplace.json に {plugin_manifest['name']} のエントリが 1 件ではない"
        entry = entries[0]

        # SSOT の 3 フィールドが完全一致することを要求する。
        # 不一致が出たら .claude/rules/plugins.md を参照して両側を揃えること。
        assert entry["version"] == plugin_manifest["version"], (
            f"version 不一致: plugin.json={plugin_manifest['version']} marketplace.json={entry['version']}"
        )
        assert entry["description"] == plugin_manifest["description"], (
            "description 不一致: plugin.json と marketplace.json を揃えること"
        )
        assert entry["name"] == plugin_manifest["name"]


class TestBashGitCommitWarning:
    """git commit 未検証警告。

    セッション状態の test_executed を参照し、テスト未実行時に警告する。
    """

    @pytest.fixture(name="state_dir")
    def _state_dir(self, tmp_path: pathlib.Path) -> dict[str, str]:
        return {"TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path)}

    def _write_state(self, tmp_path: pathlib.Path, session_id: str, state: dict) -> None:
        path = tmp_path / f"claude-agent-toolkit-{session_id}.json"
        path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    def _invoke(
        self,
        command: str,
        session_id: str,
        env: dict[str, str],
        cwd: str = "",
    ) -> subprocess.CompletedProcess[str]:
        payload: dict = {
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "session_id": session_id,
        }
        if cwd:
            payload["cwd"] = cwd
        return _run(payload, env_overrides=env)

    @staticmethod
    def _make_repo_with_staged(tmp_path: pathlib.Path, files: dict[str, str]) -> pathlib.Path:
        """staged 状態のファイルを含む git リポジトリを作成する。"""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=str(repo), capture_output=True, check=True)
        (repo / "seed.txt").write_text("seed")
        subprocess.run(["git", "add", "seed.txt"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), capture_output=True, check=True)
        for name, content in files.items():
            target = repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
            subprocess.run(["git", "add", name], cwd=str(repo), capture_output=True, check=True)
        return repo

    def _has_additional_context(self, result: subprocess.CompletedProcess[str], keyword: str) -> bool:
        if not result.stdout.strip():
            return False
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return False
        ctx = data.get("hookSpecificOutput", {}).get("additionalContext", "")
        return keyword in ctx

    def test_warns_when_test_not_executed(self, state_dir: dict[str, str], tmp_path: pathlib.Path):
        sid = "commit-warn"
        self._write_state(tmp_path, sid, {"test_executed": False})
        result = self._invoke("git commit -m 'test'", sid, state_dir)
        assert result.returncode == 0
        assert self._has_additional_context(result, "[auto-generated: agent-toolkit/pretooluse][warn]")
        assert self._has_additional_context(result, "Auto-generated hook notice")

    def test_warns_when_state_file_absent(self, state_dir: dict[str, str]):
        """状態ファイル不在時もテスト未実行として警告する。"""
        result = self._invoke("git commit -m 'test'", "no-state", state_dir)
        assert result.returncode == 0
        assert self._has_additional_context(result, "[auto-generated: agent-toolkit/pretooluse]")

    def test_skips_when_test_executed(self, state_dir: dict[str, str], tmp_path: pathlib.Path):
        sid = "commit-ok"
        self._write_state(tmp_path, sid, {"test_executed": True})
        result = self._invoke("git commit -m 'test'", sid, state_dir)
        assert result.returncode == 0
        assert result.stdout == ""

    def test_non_commit_command_unaffected(self, state_dir: dict[str, str]):
        result = self._invoke("git status", "x", state_dir)
        assert result.returncode == 0
        assert result.stdout == ""

    def test_skips_when_staged_is_docs_only(self, state_dir: dict[str, str], tmp_path: pathlib.Path):
        """staged ファイルが全て .md ならテスト未実行でも警告しない。"""
        repo = self._make_repo_with_staged(tmp_path, {"docs/a.md": "# a", "README.md": "# r"})
        result = self._invoke("git commit -m 'docs'", "docs-only", state_dir, cwd=str(repo))
        assert result.returncode == 0
        assert result.stdout == ""

    def test_warns_when_staged_mixes_non_md(self, state_dir: dict[str, str], tmp_path: pathlib.Path):
        """staged に .md 以外が混ざる場合は従来どおり警告する。"""
        repo = self._make_repo_with_staged(tmp_path, {"a.md": "# a", "b.py": "print(1)"})
        result = self._invoke("git commit -m 'mix'", "mix", state_dir, cwd=str(repo))
        assert result.returncode == 0
        assert self._has_additional_context(result, "[auto-generated: agent-toolkit/pretooluse]")

    def test_docs_only_with_commit_all_uses_worktree(self, state_dir: dict[str, str], tmp_path: pathlib.Path):
        """git commit -a の場合は作業ツリー側の変更も対象に含めて判定する。"""
        repo = tmp_path / "repo-a"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=str(repo), capture_output=True, check=True)
        (repo / "doc.md").write_text("# v1")
        subprocess.run(["git", "add", "doc.md"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), capture_output=True, check=True)
        # tracked .md を作業ツリー上でのみ変更 (index には反映しない)
        (repo / "doc.md").write_text("# v2")
        result = self._invoke("git commit -am 'update'", "commit-all", state_dir, cwd=str(repo))
        assert result.returncode == 0
        assert result.stdout == ""


class TestBashGitLogDecorate:
    """git log --decorate 自動付与。"""

    def test_adds_decorate(self):
        result = _run({"tool_name": "Bash", "tool_input": {"command": "git log --oneline -5"}})
        assert result.returncode == 0
        data = json.loads(result.stdout)
        updated = data["hookSpecificOutput"]["updatedInput"]["command"]
        assert "--decorate" in updated

    def test_skips_when_decorate_present(self):
        result = _run({"tool_name": "Bash", "tool_input": {"command": "git log --oneline --decorate -5"}})
        assert result.returncode == 0
        assert result.stdout == ""

    def test_compound_command(self):
        cmd = "git status 2>/dev/null; echo ---; git log --oneline -5"
        result = _run({"tool_name": "Bash", "tool_input": {"command": cmd}})
        assert result.returncode == 0
        data = json.loads(result.stdout)
        updated = data["hookSpecificOutput"]["updatedInput"]["command"]
        assert "git log --decorate" in updated
        # git status 部分は変更されない
        assert updated.startswith("git status")

    def test_non_log_git_command_unaffected(self):
        result = _run({"tool_name": "Bash", "tool_input": {"command": "git status"}})
        assert result.returncode == 0
        assert result.stdout == ""


class TestBashCodexExecNudge:
    """codex exec 未決事項の念押し。"""

    def test_nudge_on_initial_exec(self):
        result = _run({"tool_name": "Bash", "tool_input": {"command": "codex exec --dangerously-bypass plan.md prompt"}})
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "additionalContext" in data.get("hookSpecificOutput", {})

    def test_no_nudge_on_resume(self):
        result = _run({"tool_name": "Bash", "tool_input": {"command": "codex exec resume --dangerously-bypass abc prompt"}})
        assert result.returncode == 0
        assert result.stdout == ""

    def test_no_nudge_on_unrelated_command(self):
        result = _run({"tool_name": "Bash", "tool_input": {"command": "echo codex"}})
        assert result.returncode == 0
        assert result.stdout == ""


class TestBashAmendRebaseBlock:
    """git amend / rebase の log 未確認ブロック。"""

    @pytest.fixture(name="state_dir")
    def _state_dir(self, tmp_path: pathlib.Path) -> dict[str, str]:
        return {"TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path)}

    def _write_state(self, tmp_path: pathlib.Path, session_id: str, state: dict) -> None:
        path = tmp_path / f"claude-agent-toolkit-{session_id}.json"
        path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    def _invoke(self, command: str, session_id: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        return _run(
            {"tool_name": "Bash", "tool_input": {"command": command}, "session_id": session_id},
            env_overrides=env,
        )

    def test_amend_blocked_without_log(self, state_dir: dict[str, str]):
        cmd = "git commit " + "--amend --no-edit"
        result = self._invoke(cmd, "no-log", state_dir)
        assert result.returncode == 2
        assert "amend" in result.stderr

    def test_rebase_blocked_without_log(self, state_dir: dict[str, str]):
        result = self._invoke("GIT_SEQUENCE_EDITOR=: git rebase -i HEAD~2", "no-log", state_dir)
        assert result.returncode == 2
        assert "rebase" in result.stderr

    def test_amend_allowed_with_log(self, state_dir: dict[str, str], tmp_path: pathlib.Path):
        self._write_state(tmp_path, "with-log", {"git_log_checked": True})
        cmd = "git commit " + "--amend --no-edit"
        result = self._invoke(cmd, "with-log", state_dir)
        assert result.returncode == 0

    def test_rebase_allowed_with_log(self, state_dir: dict[str, str], tmp_path: pathlib.Path):
        self._write_state(tmp_path, "with-log-rb", {"git_log_checked": True})
        result = self._invoke("GIT_SEQUENCE_EDITOR=: git rebase -i HEAD~2", "with-log-rb", state_dir)
        assert result.returncode == 0

    def test_normal_commit_not_blocked(self, state_dir: dict[str, str]):
        """通常の git commit は amend/rebase ブロックの対象外。"""
        result = self._invoke("git commit -m 'test'", "normal", state_dir)
        assert result.returncode == 0
