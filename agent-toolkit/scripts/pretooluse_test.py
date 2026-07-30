"""agent-toolkit/scripts/pretooluse.py のテスト。

subprocessで起動しexit code・stderr・stdoutを検証する。
"""

import json
import os
import pathlib
import re
import subprocess

import _fork_runner
import platformdirs
import pretooluse
import pytest
from _scope_escalation_test_helpers import load_scope_escalation_inputs as _load_scope_escalation_inputs
from pyfltr.colloquial import check as _colloquial_check

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "claude_hook.py"
_PLUGIN_MANIFEST = pathlib.Path(__file__).resolve().parents[1] / ".claude-plugin" / "plugin.json"
_MARKETPLACE_MANIFEST = pathlib.Path(__file__).resolve().parents[2] / ".claude-plugin" / "marketplace.json"


def _run(payload: object, env_overrides: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    return _fork_runner.run_script(_SCRIPT, argv=("pretooluse",), input=text, env=env)


def _write_session_state(state_dir: pathlib.Path, session_id: str, state: dict) -> None:
    path = state_dir / f"claude-agent-toolkit-{session_id}.json"
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def _read_session_state(state_dir: pathlib.Path, session_id: str) -> dict:
    path = state_dir / f"claude-agent-toolkit-{session_id}.json"
    return json.loads(path.read_text(encoding="utf-8"))


class TestMojibakeCheck:
    """文字化け（U+FFFD）検出。"""

    def test_write_with_mojibake(self):
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "/tmp/a.txt", "content": "hello \ufffd world"}})
        assert result.returncode == 2
        assert "U+FFFD" in result.stderr
        # コーディングエージェント宛てメッセージ規約: プレフィックスとサフィックスが付与されていること。
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
        """old_string 内の文字化けは既存修復を妨げないため通過する。"""
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {"file_path": "/tmp/a.txt", "old_string": "破損した\ufffd文字", "new_string": "破損した文字"},
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
        """改行を含まない 1 行の Edit は誤検出を避けて通過する。"""
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
        """lockfile 名を部分的に含むだけのパスは通過する (例: uv.lock.bak)。"""
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


class TestColloquialCheck:
    """口語的な日本語表現の混入警告（warn のみ、exit code は 0）。

    辞書ファイルから動的にサンプルを生成するため、テスト本体には口語表現を直接書かない。
    """

    _DENY_PATH = _colloquial_check.DENY_PATH
    _ALLOW_PATH = _colloquial_check.ALLOW_PATH

    @staticmethod
    def _expand(pattern_str: str) -> str:
        return re.sub(r"\[([^\]]+)\]", lambda m: m.group(1)[0], pattern_str)

    @classmethod
    def _patterns(cls, path: pathlib.Path) -> list[re.Pattern[str]]:
        """辞書ファイルからパターンのみを抽出する。

        本番ロジック`_colloquial_check.load_patterns`と同じ解釈で
        タブ区切りの置換候補列を除外し、パターン部だけを返す。
        """
        return [pat for pat, _ in _colloquial_check.load_patterns(path)]

    @pytest.fixture(name="deny_substring")
    def _deny_substring(self) -> str:
        """allowlistの最初のオーバーラップサンプルから denylist 部分文字列を抽出。"""
        deny_patterns = self._patterns(self._DENY_PATH)
        for raw in self._ALLOW_PATH.read_text(encoding="utf-8").splitlines():
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                continue
            sample = self._expand(stripped)
            for dp in deny_patterns:
                m = dp.search(sample)
                if m:
                    return m.group(0)
        pytest.skip("no overlap between denylist and allowlist; cannot generate test sample")
        return ""  # unreachable

    def test_warns_on_deny(self, deny_substring: str):
        content = f"概要は{deny_substring}該当する。\n"
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "src/note.md", "content": content}})
        assert result.returncode == 0
        assert "colloquial" in result.stderr
        assert "[auto-generated: agent-toolkit/pretooluse][warn]" in result.stderr
        # 検出語そのものは出力に含めない（コンテキスト汚染防止）
        assert deny_substring not in result.stderr

    def test_does_not_block(self, deny_substring: str):
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "x.md", "content": deny_substring}})
        assert result.returncode == 0  # warnのみ

    def test_clean_text_no_warn(self):
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "src/app.py", "content": "x = 1\n"}})
        assert result.returncode == 0
        assert "colloquial" not in result.stderr

    def test_old_string_not_inspected(self, deny_substring: str):
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {"file_path": "x.md", "old_string": deny_substring, "new_string": "ok"},
            }
        )
        assert result.returncode == 0
        assert "colloquial" not in result.stderr


def _plan_file_state_env(
    tmp_path: pathlib.Path,
    home_dir: pathlib.Path | None = None,
) -> dict[str, str]:
    env = {"TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path)}
    if home_dir is not None:
        env["HOME"] = str(home_dir)
    return env


def _make_plan_file(home_dir: pathlib.Path, name: str = "test.md") -> pathlib.Path:
    plans = home_dir / ".claude" / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    plan = plans / name
    plan.write_text("# t\n", encoding="utf-8")
    return plan


def _write_tmp_file(tmp_path: pathlib.Path, relative_path: str, content: str) -> pathlib.Path:
    path = tmp_path / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# H2節順検査も通過する最小限の正規計画ファイル内容。
# `## 変更内容`配下に`### 対象ファイル一覧`を含め、PostToolUseのH3検査も通過させる。
_VALID_H2_PLAN_CONTENT = (
    "# タイトル\n\n"
    "## 変更履歴\n\nx\n\n"
    "## 背景\n\nx\n\n"
    "## 対応方針\n\nx\n\n"
    "## 調査結果\n\nx\n\n"
    "## 変更内容\n\n"
    "### 対象ファイル一覧\n\nx\n\n"
    "## 実行方法\n\nx\n\n"
    "## 進捗ログ\n\nx\n\n"
    "## 計画ファイル（本ファイル）のパス\n\nx\n"
)


class TestPlanModeSkillFirstCheck:
    """plan file編集全般で plan-mode スキル未起動を警告する検査（block降格済み）。

    plan-modeスキル未起動でもplan file以外の操作（Read・Bash・他Skill・通常ファイル編集等）は
    一切ブロックも警告もしない。`~/.claude/plans/`直下の`*.md`に対する
    Write/Edit/MultiEditのみが警告対象となる。`permission_mode`の値には依存しない。
    完成条件を満たさない状態での次工程移行の抑止は`ExitPlanMode`・`plan-impl-executor`起動時の
    ブロックへ集約する。
    """

    _state_env = staticmethod(_plan_file_state_env)
    _make_plan = staticmethod(_make_plan_file)

    def test_warns_plan_file_write_without_skill(self, tmp_path: pathlib.Path):
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": "# t\n"},
                "session_id": "plan-write-block",
                "permission_mode": "plan",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        assert "plan-mode" in result.stderr
        assert "Phase 1" in result.stderr
        assert "[auto-generated: agent-toolkit/pretooluse][warn]" in result.stderr

    def test_warns_plan_file_edit_without_skill(self, tmp_path: pathlib.Path):
        home = tmp_path / "home"
        plan = self._make_plan(home, "edit.md")
        env = self._state_env(tmp_path, home)
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {"file_path": str(plan), "old_string": "a", "new_string": "b"},
                "session_id": "plan-edit-block",
                "permission_mode": "plan",
            },
            env_overrides=env,
        )
        assert result.returncode == 0

    def test_allows_plan_file_when_skill_invoked(self, tmp_path: pathlib.Path):
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "plan-skill-flag"
        _write_session_state(
            tmp_path,
            sid,
            {
                "plan_mode_skill_invoked": True,
                "textlint_violations_read": True,
            },
        )
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": _VALID_H2_PLAN_CONTENT},
                "session_id": sid,
                "permission_mode": "plan",
            },
            env_overrides=env,
        )
        assert result.returncode == 0

    @pytest.mark.parametrize(
        ("tool_name", "tool_input", "allow_plan_mode_skill", "expected_returncode"),
        [
            pytest.param(
                "Write",
                {"file_path": "x.md", "content": "# t\n"},
                False,
                0,
                id="non-plan-file-edit-without-skill",
            ),
            pytest.param("Read", {"file_path": "/etc/hostname"}, False, 0, id="read-without-skill"),
            pytest.param("Bash", {"command": "ls"}, False, 0, id="bash-without-skill"),
            pytest.param(
                "Skill",
                {"skill": "agent-toolkit:process-feedbacks"},
                False,
                0,
                id="other-skill-without-plan-mode-skill",
            ),
        ],
    )
    def test_allows_non_plan_file_operations(
        self,
        tmp_path: pathlib.Path,
        tool_name: str,
        tool_input: dict,
        allow_plan_mode_skill: bool,
        expected_returncode: int,
    ) -> None:
        """計画ファイル以外の操作はplan-modeスキルの起動状態にかかわらず通過する。"""
        env = self._state_env(tmp_path)
        sid = f"plan-pass-{tool_name.lower()}"
        if allow_plan_mode_skill:
            _write_session_state(tmp_path, sid, {"plan_mode_skill_invoked": True})
        result = _run(
            {
                "tool_name": tool_name,
                "tool_input": tool_input,
                "session_id": sid,
                "permission_mode": "plan",
            },
            env_overrides=env,
        )
        assert result.returncode == expected_returncode
        assert result.stdout == ""

    def test_skipped_outside_plan_mode(self, tmp_path: pathlib.Path):
        """plan mode 外でも plan-mode スキル未起動時は plan file 編集を警告する（block降格済み）。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "non-plan-mode"
        # textlint_violations_readを設定して独立checkとの干渉を回避
        _write_session_state(
            tmp_path,
            sid,
            {
                "textlint_violations_read": True,
            },
        )
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": _VALID_H2_PLAN_CONTENT},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        assert "plan-mode" in result.stderr


class TestPlanModeSkillCallSites:
    """plan-modeスキル呼び出しの素通り保証。"""

    _state_env = staticmethod(_plan_file_state_env)

    @pytest.mark.parametrize("skill_name", ["agent-toolkit:plan-mode", "plan-mode"])
    def test_allowed_outside_plan_mode(self, tmp_path: pathlib.Path, skill_name: str):
        env = self._state_env(tmp_path)
        result = _run(
            {
                "tool_name": "Skill",
                "tool_input": {"skill": skill_name},
                "session_id": "outside-plan",
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        assert result.stdout == ""

    def test_allowed_in_plan_mode(self, tmp_path: pathlib.Path):
        env = self._state_env(tmp_path)
        result = _run(
            {
                "tool_name": "Skill",
                "tool_input": {"skill": "agent-toolkit:plan-mode"},
                "session_id": "inside-plan",
                "permission_mode": "plan",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        assert result.stdout == ""

    def test_other_skills_unaffected_outside_plan_mode(self, tmp_path: pathlib.Path):
        env = self._state_env(tmp_path)
        result = _run(
            {
                "tool_name": "Skill",
                "tool_input": {"skill": "agent-toolkit:coding-standards"},
                "session_id": "other-skill",
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        assert result.stdout == ""


class TestPlanFileRequiredReadsFirstCheck:
    """plan file 編集前に必須リファレンス未読の場合の警告検査（block降格済み）。

    `permission_mode`の値に依らず、`~/.claude/plans/`直下の`*.md`に対する
    Write/Edit/MultiEditのみが警告対象となる。plan file以外の操作は
    一切ブロック・警告しない。完成条件を満たさない状態での次工程移行の抑止は
    `ExitPlanMode`・`plan-impl-executor`起動時のブロックへ集約する。
    """

    _state_env = staticmethod(_plan_file_state_env)
    _make_plan = staticmethod(_make_plan_file)

    def test_warns_when_unread(self, tmp_path: pathlib.Path):
        """未読の場合、警告メッセージに参照パスが含まれる。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "req-both-unread"
        _write_session_state(tmp_path, sid, {"plan_mode_skill_invoked": True})
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": "# t\n"},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        assert "textlint-violations.md" in result.stderr
        assert "(already read)" not in result.stderr
        assert "[auto-generated: agent-toolkit/pretooluse][warn]" in result.stderr

    def test_warns_on_edit_when_unread(self, tmp_path: pathlib.Path):
        """Editでも同様に未読を警告する。"""
        home = tmp_path / "home"
        plan = self._make_plan(home, "edit.md")
        env = self._state_env(tmp_path, home)
        sid = "req-textlint-unread"
        _write_session_state(tmp_path, sid, {"plan_mode_skill_invoked": True})
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {"file_path": str(plan), "old_string": "a", "new_string": "b"},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        assert "textlint-violations.md" in result.stderr
        assert "[auto-generated: agent-toolkit/pretooluse][warn]" in result.stderr

    def test_allows_plan_file_when_read(self, tmp_path: pathlib.Path):
        """読了済みの場合は通過する。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "req-read"
        _write_session_state(
            tmp_path,
            sid,
            {
                "plan_mode_skill_invoked": True,
                "textlint_violations_read": True,
            },
        )
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": _VALID_H2_PLAN_CONTENT},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 0

    def test_allows_non_plan_file_edit_without_read(self, tmp_path: pathlib.Path):
        """plan file以外の編集はフラグ未設定でも通過する。"""
        home = tmp_path / "home"
        home.mkdir()
        env = self._state_env(tmp_path, home)
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(tmp_path / "x.md"), "content": "# t\n"},
                "session_id": "req-other-file",
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        assert result.stdout == ""


class TestResponseLanguageCheck:
    """直前メインエージェント応答の日本語文字比率検査の統合動作。"""

    @staticmethod
    def _write_transcript(tmp_path: pathlib.Path, text: str, *, is_sidechain: bool = False) -> pathlib.Path:
        entry: dict = {
            "type": "assistant",
            "message": {
                "id": "m1",
                "role": "assistant",
                "content": [{"type": "text", "text": text}],
                "stop_reason": "end_turn",
            },
        }
        if is_sidechain:
            entry["isSidechain"] = True
        path = tmp_path / "transcript.jsonl"
        path.write_text(json.dumps(entry, ensure_ascii=False) + "\n", encoding="utf-8")
        return path

    @staticmethod
    def _additional_context(result: subprocess.CompletedProcess[str]) -> str:
        if not result.stdout.strip():
            return ""
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return ""
        return data.get("hookSpecificOutput", {}).get("additionalContext", "")

    def test_warns_when_response_is_english(self, tmp_path: pathlib.Path):
        """日本語比率0%・プレーンテキスト50文字以上の応答で警告が乗る。"""
        transcript = self._write_transcript(tmp_path, "A" * 100)
        result = _run(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "ls"},
                "transcript_path": str(transcript),
            }
        )
        assert result.returncode == 0
        ctx = self._additional_context(result)
        assert "[auto-generated: agent-toolkit/pretooluse][warn]" in ctx
        assert "英語主体" in ctx
        assert "evaluate relevance" not in ctx

    def test_no_warn_when_response_is_japanese(self, tmp_path: pathlib.Path):
        """日本語比率高めの応答では日本語比率警告が出ない。"""
        transcript = self._write_transcript(tmp_path, "これは日本語の応答です。" * 5)
        result = _run(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "ls"},
                "transcript_path": str(transcript),
            }
        )
        assert result.returncode == 0
        assert "英語主体" not in self._additional_context(result)

    def test_no_warn_for_sidechain(self, tmp_path: pathlib.Path):
        """payloadのisSidechain=trueは検査対象外。"""
        transcript = self._write_transcript(tmp_path, "A" * 100)
        result = _run(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "ls"},
                "transcript_path": str(transcript),
                "isSidechain": True,
            }
        )
        assert result.returncode == 0
        assert result.stdout == ""

    def test_no_warn_without_transcript_path(self):
        """transcript_path未指定なら検査スキップ。"""
        result = _run({"tool_name": "Bash", "tool_input": {"command": "ls"}})
        assert result.returncode == 0
        assert result.stdout == ""


class TestBlockCheckExecutionOrder:
    """複数のblock系checkが同時に違反する場合の先行check契約を検証する。"""

    def test_direct_edit_block_preempts_retroactive_scan_block(self, tmp_path: pathlib.Path) -> None:
        plan = _write_tmp_file(tmp_path, "home/.claude/plans/current.md", "## 調査結果\n\nなし\n")
        target = _write_tmp_file(tmp_path, "agent-toolkit/rules/new-rule.md", "# 既存\n")
        sid = "block-check-order"
        _write_session_state(
            tmp_path,
            sid,
            {
                "plan_mode_skill_invoked": True,
                "plan_file_written": False,
                "direct_agent_toolkit_edit_count": 3,
                "last_agent_toolkit_edit_path": str(tmp_path / "agent-toolkit/rules/other-rule.md"),
                "current_plan_file_path": str(plan),
            },
        )
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(target), "content": "# 既存\n\n## 新規規範\n"},
                "session_id": sid,
            },
            env_overrides=_plan_file_state_env(tmp_path),
        )
        assert result.returncode == 2
        assert "consecutive Write/Edit/MultiEdit" in result.stderr
        assert "new meta-norm pattern" not in result.stderr
        assert "required items" not in result.stderr


class TestWarnJsonAndLanguageWarningComposition:
    """warn系checkのJSONへ言語警告が末尾合成される契約を検証する。"""

    def test_language_warning_appended_to_warn_json(self, tmp_path: pathlib.Path) -> None:
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "id": "m-language-composition",
                        "role": "assistant",
                        "content": [{"type": "text", "text": "A" * 100}],
                        "stop_reason": "end_turn",
                    },
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        sid = "bash-language-warning-composition"
        _write_session_state(tmp_path, sid, {"test_executed": False})
        result = _run(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "git commit -m 'test'"},
                "transcript_path": str(transcript),
                "session_id": sid,
            },
            env_overrides=_plan_file_state_env(tmp_path),
        )
        assert result.returncode == 0
        output = json.loads(result.stdout)
        context = output["hookSpecificOutput"]["additionalContext"]
        commit_warning = "committing without running tests"
        language_warning = "英語主体"
        assert commit_warning in context
        assert language_warning in context
        assert context.index(commit_warning) < context.index(language_warning)
        assert "\n\n" in context[context.index(commit_warning) : context.index(language_warning)]


class TestLanguageEscalation:
    """言語検査のエスカレーション（連続英語ターン → ブロック）。

    セッション状態を介してexit code 2でツール呼び出しをブロックする。
    """

    _state_env = staticmethod(_plan_file_state_env)

    @staticmethod
    def _write_transcript(tmp_path: pathlib.Path, text: str, msg_id: str = "m1") -> pathlib.Path:
        entry = {
            "type": "assistant",
            "message": {
                "id": msg_id,
                "role": "assistant",
                "content": [{"type": "text", "text": text}],
                "stop_reason": "end_turn",
            },
        }
        path = tmp_path / "transcript.jsonl"
        path.write_text(json.dumps(entry, ensure_ascii=False) + "\n", encoding="utf-8")
        return path

    def _invoke(
        self,
        tmp_path: pathlib.Path,
        env: dict[str, str],
        session_id: str,
        text: str,
        msg_id: str = "m1",
    ) -> subprocess.CompletedProcess[str]:
        transcript = self._write_transcript(tmp_path, text, msg_id)
        return _run(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "ls"},
                "transcript_path": str(transcript),
                "session_id": session_id,
            },
            env_overrides=env,
        )

    @staticmethod
    def _additional_context(result: subprocess.CompletedProcess[str]) -> str:
        if not result.stdout.strip():
            return ""
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return ""
        return data.get("hookSpecificOutput", {}).get("additionalContext", "")

    def test_first_english_warns(self, tmp_path: pathlib.Path):
        """1回目の英語検出はexit 0 + additionalContextで警告する。"""
        env = self._state_env(tmp_path)
        result = self._invoke(tmp_path, env, "esc-first", "A" * 100, msg_id="m1")
        assert result.returncode == 0
        ctx = self._additional_context(result)
        assert "英語主体" in ctx
        assert "evaluate relevance" not in ctx

    def test_second_english_blocks(self, tmp_path: pathlib.Path):
        """2回連続英語でexit 2 + stderrでブロックする。"""
        env = self._state_env(tmp_path)
        sid = "esc-block"
        # 1回目: warn
        r1 = self._invoke(tmp_path, env, sid, "A" * 100, msg_id="m1")
        assert r1.returncode == 0
        # 2回目: block
        r2 = self._invoke(tmp_path, env, sid, "B" * 100, msg_id="m2")
        assert r2.returncode == 2
        assert "2ターン連続" in r2.stderr
        assert "evaluate relevance" not in r2.stderr

    def test_japanese_resets_counter(self, tmp_path: pathlib.Path):
        """日本語応答が間に入るとカウンタがリセットされる。"""
        env = self._state_env(tmp_path)
        sid = "esc-reset"
        # 1回目: 英語 → warn
        self._invoke(tmp_path, env, sid, "A" * 100, msg_id="m1")
        # 2回目: 日本語 → pass（カウンタリセット）
        self._invoke(tmp_path, env, sid, "これは日本語の応答です。" * 5, msg_id="m2")
        # 3回目: 英語 → warn（カウンタは1に戻っているのでブロックではない）
        r3 = self._invoke(tmp_path, env, sid, "C" * 100, msg_id="m3")
        assert r3.returncode == 0
        ctx = self._additional_context(r3)
        assert "英語主体" in ctx

    def test_same_msg_id_no_double_count(self, tmp_path: pathlib.Path):
        """同一message IDの並列ツール呼び出しはカウンタを1回のみ増加する。"""
        env = self._state_env(tmp_path)
        sid = "esc-parallel"
        # 同じmsg_idで2回呼び出し（並列ツール呼び出しのシミュレーション）
        r1 = self._invoke(tmp_path, env, sid, "A" * 100, msg_id="m-same")
        assert r1.returncode == 0
        r2 = self._invoke(tmp_path, env, sid, "A" * 100, msg_id="m-same")
        assert r2.returncode == 0  # 同一IDなのでカウンタ増加なし、ブロックしない

    def test_block_then_next_english_reblocks(self, tmp_path: pathlib.Path):
        """ブロック後の次ターン英語で再ブロックする。"""
        env = self._state_env(tmp_path)
        sid = "esc-reblock"
        # 1回目: warn
        self._invoke(tmp_path, env, sid, "A" * 100, msg_id="m1")
        # 2回目: block
        r2 = self._invoke(tmp_path, env, sid, "B" * 100, msg_id="m2")
        assert r2.returncode == 2
        # 3回目: 再block（カウンタが1に設定されているため、次の英語で再度≧2）
        r3 = self._invoke(tmp_path, env, sid, "C" * 100, msg_id="m3")
        assert r3.returncode == 2
        assert "2ターン連続" in r3.stderr

    def test_warn_no_suffix(self, tmp_path: pathlib.Path):
        """warn時のadditionalContextに共通サフィックスが含まれないことを検証する。"""
        env = self._state_env(tmp_path)
        result = self._invoke(tmp_path, env, "esc-suffix-warn", "A" * 100, msg_id="m1")
        assert result.returncode == 0
        ctx = self._additional_context(result)
        assert ctx  # 警告が出ていること
        assert "Auto-generated hook notice" not in ctx
        assert "evaluate relevance" not in ctx

    def test_block_no_suffix(self, tmp_path: pathlib.Path):
        """block時のstderrに共通サフィックスが含まれないことを検証する。"""
        env = self._state_env(tmp_path)
        sid = "esc-suffix-block"
        self._invoke(tmp_path, env, sid, "A" * 100, msg_id="m1")
        r2 = self._invoke(tmp_path, env, sid, "B" * 100, msg_id="m2")
        assert r2.returncode == 2
        assert "Auto-generated hook notice" not in r2.stderr
        assert "evaluate relevance" not in r2.stderr


class TestGeneralBehavior:
    """統合スクリプト共通の振る舞い。"""

    @pytest.mark.parametrize(
        "payload",
        [
            # Write/Edit/MultiEdit以外は全て通す
            {"tool_name": "Bash", "tool_input": {"command": "echo \ufffd"}},
            # tool_inputが欠落していても通す
            {"tool_name": "Write"},
            # 正常な日本語は通す
            {"tool_name": "Write", "tool_input": {"file_path": "a.txt", "content": "こんにちは世界"}},
        ],
    )
    def test_allowed(self, payload: dict):
        result = _run(payload)
        assert result.returncode == 0

    def test_invalid_json(self):
        """不正JSONはフックを無効化（安全側）。"""
        result = _run("this is not json")
        assert result.returncode == 0


class TestManifestSsot:
    """Claude Code向け正本manifest間のSSOT整合性。

    version / description / nameを2箇所で重複管理しているため、
    片方だけ更新して配布されない事故を防ぐためのハードチェック。
    Codex向け派生manifestはsync_codex_plugin_manifests.pyが検証する。
    """

    def test_plugin_manifest_matches_marketplace(self):
        plugin_manifest = json.loads(_PLUGIN_MANIFEST.read_text(encoding="utf-8"))
        marketplace = json.loads(_MARKETPLACE_MANIFEST.read_text(encoding="utf-8"))

        entries = [p for p in marketplace["plugins"] if p["name"] == plugin_manifest["name"]]
        assert len(entries) == 1, f"marketplace.json に {plugin_manifest['name']} のエントリが 1 件ではない"
        entry = entries[0]

        # SSOTの3フィールドが完全一致することを要求する。
        # 不一致が出たらagent-toolkit/.claude-plugin/plugin.jsonと
        # .claude-plugin/marketplace.json（plugins[]内name == "agent-toolkit"のエントリ）の
        # version／description／nameを両側で揃えること。
        assert entry["version"] == plugin_manifest["version"], (
            f"version 不一致: plugin.json={plugin_manifest['version']} marketplace.json={entry['version']}"
        )
        assert entry["description"] == plugin_manifest["description"], (
            "description 不一致: plugin.json と marketplace.json を揃えること"
        )
        assert entry["name"] == plugin_manifest["name"]


_HOOKS_JSON_PATH = pathlib.Path(__file__).resolve().parents[1] / "hooks" / "hooks.json"
_SCRIPTS_DIR_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts"


def _hook_entry_point_names() -> list[str]:
    """hooks.json の command 文字列から entry point スクリプト名を抽出する。"""
    text = _HOOKS_JSON_PATH.read_text(encoding="utf-8")
    pattern = re.compile(r"\$\{CLAUDE_PLUGIN_ROOT\}/scripts/([^\"\s]+\.py)")
    return sorted(set(pattern.findall(text)))


class TestHookEntryPointsPep723Dependencies:
    """hooks.json 列挙の全 entry point で PEP 723 dependencies が実行時 import を満たす検査。

    hook 間で新規に間接 import を追加した際、依存元 entry point の PEP 723 dependencies
    へ外部パッケージを追記し忘れると本番実行時に ModuleNotFoundError で hook が
    Traceback を伴い異常終了する回帰を機械的に予防する。
    """

    @pytest.mark.parametrize("script_name", _hook_entry_point_names())
    def test_entry_point_starts_without_import_error(self, script_name: str) -> None:
        script = _SCRIPTS_DIR_PATH / script_name
        # 空 JSON 入力（各 hook が最小限の tool_input を要求する場合の共通形）
        result = subprocess.run(
            ["uv", "run", "--no-project", "--script", str(script)],
            input="{}",
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        # ModuleNotFoundError 等の import 系失敗は stderr の Traceback として現れる。
        # hook 実装の内部エラー（キー不足等）は許容し、import 失敗のみを検出する。
        assert "ModuleNotFoundError" not in result.stderr, (
            f"{script_name} が ModuleNotFoundError で失敗した。"
            f" PEP 723 dependencies へ不足パッケージを追記する必要がある。\n"
            f"stderr: {result.stderr[:2000]}"
        )
        assert "ImportError" not in result.stderr, (
            f"{script_name} が ImportError で失敗した。"
            f" PEP 723 dependencies を確認する必要がある。\nstderr: {result.stderr[:2000]}"
        )


class TestBashSleepPollPattern:
    """sleep直後の読み取り専用な状態確認連結を初回warn・再検出blockで扱う。"""

    @pytest.mark.parametrize(
        ("command", "session_id"),
        [
            ("sleep 10; git status --short", "sleep-poll-first-1"),
            ("sleep 5 && gh run view 123", "sleep-poll-first-2"),
            ("sleep 1; systemctl status example.service", "sleep-poll-first-3"),
            ("echo start; sleep 2; atk mq list", "sleep-poll-first-4"),
            ("sleep 3; curl https://example.com/status", "sleep-poll-first-5"),
            ("sleep 3 && curl -D - https://example.com/status", "sleep-poll-first-6"),
            ("sleep 3; curl -XGET https://example.com/status", "sleep-poll-first-7"),
            ("sleep 3 && curl -X GET https://example.com/status", "sleep-poll-first-8"),
            ("sleep 3; curl --request=HEAD https://example.com/status", "sleep-poll-first-9"),
            ("sleep 3 && curl --request HEAD https://example.com/status", "sleep-poll-first-10"),
            ("sleep 3; curl -XPOST -XGET https://example.com/status", "sleep-poll-first-11"),
            ("sleep 3 && curl --request PUT --request HEAD https://example.com/status", "sleep-poll-first-12"),
            (
                "sleep 3; curl -XGET https://example.com/a --next -XHEAD https://example.com/b",
                "sleep-poll-first-13",
            ),
            (
                r"echo foo\ #literal; sleep 1; git status --short",
                "sleep-poll-first-14",
            ),
            (
                "echo $(printf x)#literal; sleep 1; git status --short",
                "sleep-poll-first-15",
            ),
            (
                "sleep 1 \\\n; git status --short",
                "sleep-poll-first-16",
            ),
        ],
    )
    def test_first_detection_warns_and_allows(
        self,
        command: str,
        session_id: str,
        tmp_path: pathlib.Path,
    ) -> None:
        result = _run(
            {"tool_name": "Bash", "tool_input": {"command": command}, "session_id": session_id},
            _plan_file_state_env(tmp_path),
        )
        assert result.returncode == 0
        assert "may cause repeated polling" in result.stderr

    def test_second_detection_in_same_session_blocks(self, tmp_path: pathlib.Path) -> None:
        session_id = "sleep-poll-repeat-test"
        env = _plan_file_state_env(tmp_path)
        first = _run(
            {"tool_name": "Bash", "tool_input": {"command": "sleep 10; git status --short"}, "session_id": session_id},
            env,
        )
        assert first.returncode == 0
        second = _run(
            {"tool_name": "Bash", "tool_input": {"command": "sleep 5; gh run view 123"}, "session_id": session_id},
            env,
        )
        assert second.returncode == 2
        assert "until <condition>" in second.stderr
        assert "[auto-generated: agent-toolkit/pretooluse]" in second.stderr

    @pytest.mark.parametrize(
        ("command", "session_id"),
        [
            ("sleep 1", "sleep-poll-allow-1"),
            ("sleep 1; echo done", "sleep-poll-allow-2"),
            ("until ps -p 123 >/dev/null; do sleep 5; done", "sleep-poll-allow-3"),
            ("printf 'sleep 1; git status'", "sleep-poll-allow-4"),
            ("sleep 1 || git status --short", "sleep-poll-allow-5"),
            ("sleep 1 | cat", "sleep-poll-allow-6"),
            ("sleep 5; gh run cancel 123", "sleep-poll-allow-7"),
            ("sleep 5 && curl -X POST https://example.com/hook", "sleep-poll-allow-8"),
            ("sleep 5; curl --data 'x=1' https://example.com/hook", "sleep-poll-allow-9"),
            ("sleep 5 && curl -F file=@x.txt https://example.com/hook", "sleep-poll-allow-10"),
            ("sleep 5; curl -XPOST https://example.com/hook", "sleep-poll-allow-11"),
            ("sleep 5 && curl -dfoo=bar https://example.com/hook", "sleep-poll-allow-12"),
            ("sleep 5; curl -Tfile.txt https://example.com/hook", "sleep-poll-allow-13"),
            ("sleep 5 && curl --request PUT https://example.com/hook", "sleep-poll-allow-14"),
            ("sleep 5; curl --data=x=1 https://example.com/hook", "sleep-poll-allow-15"),
            ("sleep 5 && curl -XGET -XPOST https://example.com/hook", "sleep-poll-allow-16"),
            ("sleep 5; curl --request HEAD --request PUT https://example.com/hook", "sleep-poll-allow-17"),
            ("sleep 5 && curl --data-ascii 'x=1' https://example.com/hook", "sleep-poll-allow-18"),
            ("sleep 5; curl --form-string 'x=1' https://example.com/hook", "sleep-poll-allow-19"),
            ("sleep 5 && curl --json '{\"x\":1}' https://example.com/hook", "sleep-poll-allow-20"),
            (
                "sleep 5; curl -XPOST https://example.com/a --next -XGET https://example.com/b",
                "sleep-poll-allow-21",
            ),
            (
                "sleep 5 && curl -XGET https://example.com/a --next -XPOST https://example.com/b",
                "sleep-poll-allow-22",
            ),
            ("sleep 0&#comment; git status --short", "sleep-poll-allow-23"),
            (
                "sleep 0 \\\n#comment; git status --short",
                "sleep-poll-allow-24",
            ),
        ],
    )
    def test_allows_non_polling_and_write_forms(
        self,
        command: str,
        session_id: str,
        tmp_path: pathlib.Path,
    ) -> None:
        env = _plan_file_state_env(tmp_path)
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}, "session_id": session_id}, env)
        assert result.returncode == 0
        assert "may cause repeated polling" not in result.stderr
        follow_up = _run(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "sleep 1; git status --short"},
                "session_id": session_id,
            },
            env,
        )
        assert follow_up.returncode == 0
        assert "may cause repeated polling" in follow_up.stderr

    def test_background_execution_is_not_evaluated(self, tmp_path: pathlib.Path) -> None:
        result = _run(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "sleep 10; git status --short", "run_in_background": True},
                "session_id": "sleep-poll-background",
            },
            _plan_file_state_env(tmp_path),
        )
        assert result.returncode == 0


class TestBashGitCommitWarning:
    """git commit未検証警告。

    セッション状態のtest_executedを参照し、テスト未実行時に警告する。
    """

    @pytest.fixture(name="state_dir")
    def _state_dir(self, tmp_path: pathlib.Path) -> dict[str, str]:
        return _plan_file_state_env(tmp_path)

    _write_state = staticmethod(_write_session_state)

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
        """staged状態のファイルを含むgitリポジトリを作成する。"""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        (repo / "seed.txt").write_text("seed", encoding="utf-8")
        subprocess.run(["git", "add", "seed.txt"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), capture_output=True, check=True)
        for name, content in files.items():
            target = repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
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

    @pytest.mark.parametrize(
        ("command", "test_executed", "state_absent", "staged_files", "worktree_files", "expect_warn"),
        [
            pytest.param("git commit -m 't'", False, False, None, None, True, id="test-not-executed"),
            pytest.param("git commit -m 't'", False, True, None, None, True, id="state-file-absent"),
            pytest.param("git commit -m 't'", True, False, None, None, False, id="test-executed"),
            pytest.param("git status", False, False, None, None, False, id="non-commit-command"),
            pytest.param(
                "grep -n 'git commit' agent-toolkit/scripts/pretooluse.py",
                False,
                False,
                None,
                None,
                False,
                id="grep-single-quoted-pattern",
            ),
            pytest.param(
                'grep -rn "git commit" .',
                False,
                False,
                None,
                None,
                False,
                id="grep-double-quoted-pattern",
            ),
            pytest.param(
                "git commit -m 'docs'",
                False,
                False,
                {"docs/a.md": "# a", "README.md": "# r"},
                None,
                False,
                id="staged-docs-only",
            ),
            pytest.param(
                "git commit -m 'mix'",
                False,
                False,
                {"a.md": "# a", "b.py": "print(1)"},
                None,
                True,
                id="staged-mixed",
            ),
            pytest.param(
                "git commit -am 'update'",
                False,
                False,
                None,
                {"doc.md": "# v2"},
                False,
                id="commit-all-docs-worktree",
            ),
        ],
    )
    def test_commit_warning_scenarios(
        self,
        state_dir: dict[str, str],
        tmp_path: pathlib.Path,
        command: str,
        test_executed: bool,
        state_absent: bool,
        staged_files: dict[str, str] | None,
        worktree_files: dict[str, str] | None,
        expect_warn: bool,
    ) -> None:
        """commit前テスト警告の入力条件とメッセージ契約を行列で検証する。"""
        sid = re.sub(r"[^a-z]+", "-", command.lower()).strip("-")
        cwd = ""
        if staged_files is not None:
            cwd = str(self._make_repo_with_staged(tmp_path, staged_files))
        elif worktree_files is not None:
            repo = tmp_path / "repo-a"
            repo.mkdir()
            _init_git_repo(repo)
            _git_commit_initial(repo, {name: "# v1" for name in worktree_files})
            for name, content in worktree_files.items():
                (repo / name).write_text(content, encoding="utf-8")
            cwd = str(repo)

        if not state_absent:
            state: dict[str, object] = {"test_executed": test_executed}
            if worktree_files is not None:
                state["session_edited_files"] = list(worktree_files)
            self._write_state(tmp_path, sid, state)

        result = self._invoke(command, sid, state_dir, cwd=cwd)
        assert result.returncode == 0
        if expect_warn:
            assert self._has_additional_context(result, "[auto-generated: agent-toolkit/pretooluse][warn]")
            assert self._has_additional_context(result, "committing without running tests")
            assert self._has_additional_context(result, "Auto-generated hook notice")
        else:
            assert result.stdout == ""


class TestBashGitLogDecorate:
    """git log --decorate自動付与。"""

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
        # git status部分は変更されない
        assert updated.startswith("git status")

    def test_non_log_git_command_unaffected(self):
        result = _run({"tool_name": "Bash", "tool_input": {"command": "git status"}})
        assert result.returncode == 0
        assert result.stdout == ""


class TestBashCodexExecNudge:
    """codex exec未決事項の念押し。"""

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
    """git amend / rebaseのlog未確認ブロック。

    `git_log_checked`はcwd別辞書`{cwd: True}`で管理する。
    cwd空文字列環境向けに旧形式の単一bool値も後方互換として受け入れる。
    """

    @pytest.fixture(name="state_dir")
    def _state_dir(self, tmp_path: pathlib.Path) -> dict[str, str]:
        return _plan_file_state_env(tmp_path)

    _write_state = staticmethod(_write_session_state)

    def _invoke(
        self,
        command: str,
        session_id: str,
        env: dict[str, str],
        cwd: str = "",
    ) -> subprocess.CompletedProcess[str]:
        payload: dict = {"tool_name": "Bash", "tool_input": {"command": command}, "session_id": session_id}
        if cwd:
            payload["cwd"] = cwd
        return _run(payload, env_overrides=env)

    def test_amend_blocked_without_log(self, state_dir: dict[str, str]):
        cmd = "git commit " + "--amend --no-edit"
        result = self._invoke(cmd, "no-log", state_dir)
        assert result.returncode == 2
        assert "amend" in result.stderr

    def test_rebase_blocked_without_log(self, state_dir: dict[str, str]):
        result = self._invoke("GIT_SEQUENCE_EDITOR=: git rebase -i HEAD~2", "no-log", state_dir)
        assert result.returncode == 2
        assert "rebase" in result.stderr

    def test_amend_allowed_with_legacy_bool_flag(self, state_dir: dict[str, str], tmp_path: pathlib.Path):
        """旧形式bool値`True`はcwd空文字列環境向けの後方互換として受け入れる。"""
        self._write_state(tmp_path, "with-log", {"git_log_checked": True})
        cmd = "git commit " + "--amend --no-edit"
        result = self._invoke(cmd, "with-log", state_dir)
        assert result.returncode == 0

    def test_rebase_allowed_with_legacy_bool_flag(self, state_dir: dict[str, str], tmp_path: pathlib.Path):
        self._write_state(tmp_path, "with-log-rb", {"git_log_checked": True})
        result = self._invoke("GIT_SEQUENCE_EDITOR=: git rebase -i HEAD~2", "with-log-rb", state_dir)
        assert result.returncode == 0

    def test_normal_commit_not_blocked(self, state_dir: dict[str, str]):
        """通常のgit commitはamend/rebaseブロックの対象外。"""
        result = self._invoke("git commit -m 'test'", "normal", state_dir)
        assert result.returncode == 0

    @pytest.mark.parametrize(
        ("label", "recorded_cwd", "payload_cwd", "expected_returncode"),
        [
            # 同cwd: 該当cwdのgit log確認があれば許可
            ("same", "/repo/a", "/repo/a", 0),
            # 別cwd: 別cwdの確認は流用できないためblock
            ("other", "/repo/a", "/repo/b", 2),
            # cwd空文字列のpayloadは辞書キーが取れないためblockに倒す
            ("empty", "/repo/a", "", 2),
        ],
    )
    def test_amend_per_cwd_judgement(
        self,
        state_dir: dict[str, str],
        tmp_path: pathlib.Path,
        label: str,
        recorded_cwd: str,
        payload_cwd: str,
        expected_returncode: int,
    ):
        """`git_log_checked`辞書はcwd別に判定する。"""
        sid = f"per-cwd-{label}"
        self._write_state(tmp_path, sid, {"git_log_checked": {recorded_cwd: True}})
        cmd = "git commit " + "--amend --no-edit"
        result = self._invoke(cmd, sid, state_dir, cwd=payload_cwd)
        assert result.returncode == expected_returncode

    @pytest.mark.parametrize(
        ("label", "command", "payload_cwd", "recorded_cwd", "expected_returncode"),
        [
            # `git -C <dir>` でcwdを切り替えた先のlog確認は当該ディレクトリで判定する
            ("dash_c_absolute_allowed", "git -C /repo/x commit --amend --no-edit", "/elsewhere", "/repo/x", 0),
            ("dash_c_absolute_blocked", "git -C /repo/x commit --amend --no-edit", "/elsewhere", "/repo/y", 2),
            # `cd <dir>` 後のamend
            ("cd_then_amend_allowed", "cd /repo/x && git commit --amend --no-edit", "/elsewhere", "/repo/x", 0),
            ("cd_then_amend_blocked", "cd /repo/x && git commit --amend --no-edit", "/elsewhere", "/repo/y", 2),
            # `cd a; git -C b` の組合せ
            ("cd_and_dash_c_allowed", "cd /repo && git -C x commit --amend --no-edit", "/elsewhere", "/repo/x", 0),
            ("cd_and_dash_c_blocked", "cd /repo && git -C x commit --amend --no-edit", "/elsewhere", "/repo/y", 2),
            # rebaseも同様に判定される
            ("dash_c_rebase_allowed", "git -C /repo/x rebase main", "/elsewhere", "/repo/x", 0),
            ("dash_c_rebase_blocked", "git -C /repo/x rebase main", "/elsewhere", "/repo/y", 2),
        ],
    )
    def test_effective_cwd_resolution(
        self,
        state_dir: dict[str, str],
        tmp_path: pathlib.Path,
        label: str,
        command: str,
        payload_cwd: str,
        recorded_cwd: str,
        expected_returncode: int,
    ) -> None:
        """`git -C`・`cd`・両者併用で実効cwdが切り替わるケースを記録cwdと突合する。"""
        sid = f"effective-{label}"
        self._write_state(tmp_path, sid, {"git_log_checked": {recorded_cwd: True}})
        result = self._invoke(command, sid, state_dir, cwd=payload_cwd)
        assert result.returncode == expected_returncode


def _init_git_repo(path: pathlib.Path) -> None:
    """一括ステージ警告テスト用の最小git repo初期化。"""
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "test"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "commit.gpgsign", "false"], check=True)


def _git_commit_initial(path: pathlib.Path, files: dict[str, str]) -> None:
    """指定ファイルを追加してinitial commitを作成する。"""
    for rel, content in files.items():
        target = path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "init"], check=True)


class TestBashBulkStageWithUneditedFiles:
    """一括ステージ実行時にセッション未編集の変更が含まれる場合の警告。

    - `git add -A/--all/.` は未追跡を含む集合を対象とする
    - `git add -u/--update` と `git commit -a/--all/-am`等 は追跡済みのみを対象とする
    - 実効cwdは `event.cwd`（`cd`・`git -C`の影響を反映）で判定する
    """

    @pytest.fixture(name="state_dir")
    def _state_dir(self, tmp_path: pathlib.Path) -> dict[str, str]:
        return _plan_file_state_env(tmp_path)

    _write_state = staticmethod(_write_session_state)

    def _invoke(
        self,
        command: str,
        session_id: str,
        env: dict[str, str],
        cwd: str,
    ) -> subprocess.CompletedProcess[str]:
        payload: dict = {
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "session_id": session_id,
            "cwd": cwd,
        }
        return _run(payload, env_overrides=env)

    @staticmethod
    def _extract_json(stdout: str) -> dict | None:
        """stdout末尾のJSON行を抽出する。"""
        for line in reversed(stdout.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
        return None

    def _assert_warns(self, result: subprocess.CompletedProcess[str]) -> str:
        assert result.returncode == 0
        data = self._extract_json(result.stdout)
        assert data is not None, f"expected JSON output, got: {result.stdout!r}"
        ctx = data["hookSpecificOutput"]["additionalContext"]
        assert "bulk staging" in ctx
        return ctx

    def _assert_no_warn(self, result: subprocess.CompletedProcess[str]) -> None:
        assert result.returncode == 0
        data = self._extract_json(result.stdout)
        if data is None:
            return
        ctx = data.get("hookSpecificOutput", {}).get("additionalContext", "")
        assert "bulk staging" not in ctx

    def test_warns_when_git_add_all_with_unedited_untracked(
        self,
        state_dir: dict[str, str],
        tmp_path: pathlib.Path,
    ) -> None:
        """`git add -A`実行時、未追跡ファイルがsession外なら warn 返却。"""
        repo = tmp_path / "repo1"
        repo.mkdir()
        _init_git_repo(repo)
        (repo / "unedited.txt").write_text("x", encoding="utf-8")
        self._write_state(tmp_path, "add-all-untracked", {"session_edited_files": []})
        result = self._invoke("git add -A", "add-all-untracked", state_dir, cwd=str(repo))
        ctx = self._assert_warns(result)
        assert "unedited.txt" in ctx

    def test_warns_when_git_add_dot_with_unedited_tracked(
        self,
        state_dir: dict[str, str],
        tmp_path: pathlib.Path,
    ) -> None:
        """`git add .`実行時、追跡済み変更ファイルがsession外なら warn 返却。"""
        repo = tmp_path / "repo2"
        repo.mkdir()
        _init_git_repo(repo)
        _git_commit_initial(repo, {"tracked.txt": "orig\n"})
        (repo / "tracked.txt").write_text("modified\n", encoding="utf-8")
        self._write_state(tmp_path, "add-dot-tracked", {"session_edited_files": []})
        result = self._invoke("git add .", "add-dot-tracked", state_dir, cwd=str(repo))
        ctx = self._assert_warns(result)
        assert "tracked.txt" in ctx

    def test_no_warn_when_git_add_u_with_only_untracked_unedited(
        self,
        state_dir: dict[str, str],
        tmp_path: pathlib.Path,
    ) -> None:
        """`git add -u`実行時、未追跡ファイルは対象外のため warn 無し。"""
        repo = tmp_path / "repo3"
        repo.mkdir()
        _init_git_repo(repo)
        _git_commit_initial(repo, {"kept.txt": "x\n"})
        (repo / "new_untracked.txt").write_text("y", encoding="utf-8")
        self._write_state(tmp_path, "add-u-untracked", {"session_edited_files": []})
        result = self._invoke("git add -u", "add-u-untracked", state_dir, cwd=str(repo))
        self._assert_no_warn(result)

    def test_no_warn_when_git_commit_a_with_only_untracked_unedited(
        self,
        state_dir: dict[str, str],
        tmp_path: pathlib.Path,
    ) -> None:
        """`git commit -a`実行時、未追跡ファイルは対象外のため warn 無し。"""
        repo = tmp_path / "repo4"
        repo.mkdir()
        _init_git_repo(repo)
        _git_commit_initial(repo, {"kept.txt": "x\n"})
        (repo / "new_untracked.txt").write_text("y", encoding="utf-8")
        # `test_executed`を有効化して git_commit warnを回避する
        self._write_state(
            tmp_path,
            "commit-a-untracked",
            {"session_edited_files": [], "test_executed": True},
        )
        result = self._invoke("git commit -a -m x", "commit-a-untracked", state_dir, cwd=str(repo))
        self._assert_no_warn(result)

    def test_no_warn_when_only_edited_files_changed(
        self,
        state_dir: dict[str, str],
        tmp_path: pathlib.Path,
    ) -> None:
        """変更ツリーが session_edited_files と完全一致で warn 無し。"""
        repo = tmp_path / "repo5"
        repo.mkdir()
        _init_git_repo(repo)
        (repo / "edited.txt").write_text("x", encoding="utf-8")
        self._write_state(
            tmp_path,
            "only-edited",
            {"session_edited_files": ["edited.txt"]},
        )
        result = self._invoke("git add -A", "only-edited", state_dir, cwd=str(repo))
        self._assert_no_warn(result)

    def test_no_warn_when_working_tree_clean(
        self,
        state_dir: dict[str, str],
        tmp_path: pathlib.Path,
    ) -> None:
        """`git status --short`出力が空で warn 無し。"""
        repo = tmp_path / "repo6"
        repo.mkdir()
        _init_git_repo(repo)
        _git_commit_initial(repo, {"kept.txt": "x\n"})
        self._write_state(tmp_path, "clean", {"session_edited_files": []})
        result = self._invoke("git add -A", "clean", state_dir, cwd=str(repo))
        self._assert_no_warn(result)

    def test_detects_git_commit_am_flag(
        self,
        state_dir: dict[str, str],
        tmp_path: pathlib.Path,
    ) -> None:
        """`git commit -am`検出でも追跡済みモード判定。"""
        repo = tmp_path / "repo7"
        repo.mkdir()
        _init_git_repo(repo)
        _git_commit_initial(repo, {"tracked.txt": "orig\n"})
        (repo / "tracked.txt").write_text("modified\n", encoding="utf-8")
        self._write_state(
            tmp_path,
            "commit-am",
            {"session_edited_files": [], "test_executed": True},
        )
        result = self._invoke("git commit -am msg", "commit-am", state_dir, cwd=str(repo))
        ctx = self._assert_warns(result)
        assert "tracked.txt" in ctx

    def test_absolute_path_edited_matches_relative_change(
        self,
        state_dir: dict[str, str],
        tmp_path: pathlib.Path,
    ) -> None:
        """session_edited_files の絶対パスが event.cwd 起点で正規化されて一致判定される。"""
        repo = tmp_path / "repo8"
        repo.mkdir()
        _init_git_repo(repo)
        (repo / "edited.txt").write_text("x", encoding="utf-8")
        abs_path = str(repo / "edited.txt")
        self._write_state(
            tmp_path,
            "abs-edited",
            {"session_edited_files": [abs_path]},
        )
        result = self._invoke("git add -A", "abs-edited", state_dir, cwd=str(repo))
        self._assert_no_warn(result)

    def test_detects_cd_subdir_git_add_A(
        self,
        state_dir: dict[str, str],
        tmp_path: pathlib.Path,
    ) -> None:
        """`cd sub && git add -A`実行時、event.cwd = sub 配下の変更ツリーで判定される。"""
        repo = tmp_path / "repo9"
        repo.mkdir()
        _init_git_repo(repo)
        # sub配下に既存トラッキング済みファイルを作成しておく（サブディレクトリを
        # gitに認識させ、`git status --short`のパス表示が`.`へ集約されるのを防ぐ）
        _git_commit_initial(repo, {"sub/kept.txt": "orig\n"})
        sub = repo / "sub"
        (sub / "sub_unedited.txt").write_text("y", encoding="utf-8")
        self._write_state(tmp_path, "cd-sub", {"session_edited_files": []})
        result = self._invoke(
            f"cd {sub} && git add -A",
            "cd-sub",
            state_dir,
            cwd=str(repo),
        )
        ctx = self._assert_warns(result)
        assert "sub_unedited.txt" in ctx

    def test_detects_git_c_subdir_add_A(
        self,
        state_dir: dict[str, str],
        tmp_path: pathlib.Path,
    ) -> None:
        """`git -C sub add -A`実行時、event.cwd = sub 配下の変更ツリーで判定される。"""
        repo = tmp_path / "repo10"
        repo.mkdir()
        _init_git_repo(repo)
        # sub配下に既存トラッキング済みファイルを作成しておく（サブディレクトリを
        # gitに認識させ、`git status --short`のパス表示が`.`へ集約されるのを防ぐ）
        _git_commit_initial(repo, {"sub/kept.txt": "orig\n"})
        sub = repo / "sub"
        (sub / "sub_unedited.txt").write_text("y", encoding="utf-8")
        self._write_state(tmp_path, "git-c-sub", {"session_edited_files": []})
        result = self._invoke(
            f"git -C {sub} add -A",
            "git-c-sub",
            state_dir,
            cwd=str(repo),
        )
        ctx = self._assert_warns(result)
        assert "sub_unedited.txt" in ctx


class TestBashGitPushAfterAmendDirty:
    """`git push`前のamend後dirty状態ブロック検査（fb3）。

    posttooluse側で設定する`amend_pending_status_check`（cwd別辞書）がTrueかつ
    `git status --porcelain`で追跡ファイル差分がある場合、`git push`をブロックする。
    実送出push（`--dry-run`なし）でclean時のみフラグ解除する
    （`git push --dry-run`はdirty時blockは実施しclean時は解除せず状態を保つ）。
    """

    @pytest.fixture(name="state_dir")
    def _state_dir(self, tmp_path: pathlib.Path) -> dict[str, str]:
        return _plan_file_state_env(tmp_path)

    _write_state = staticmethod(_write_session_state)

    def _invoke(
        self,
        command: str,
        session_id: str,
        env: dict[str, str],
        cwd: str,
    ) -> subprocess.CompletedProcess[str]:
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "session_id": session_id,
            "cwd": cwd,
        }
        return _run(payload, env_overrides=env)

    @staticmethod
    def _read_flag(state_dir_path: pathlib.Path, session_id: str, cwd: str) -> bool:
        path = state_dir_path / f"claude-agent-toolkit-{session_id}.json"
        if not path.exists():
            return False
        state = json.loads(path.read_text(encoding="utf-8"))
        flags = state.get("amend_pending_status_check")
        return bool(flags.get(cwd, False)) if isinstance(flags, dict) else False

    def test_blocks_push_when_flag_true_and_dirty(self, state_dir: dict[str, str], tmp_path: pathlib.Path) -> None:
        repo = tmp_path / "repo-dirty"
        repo.mkdir()
        _init_git_repo(repo)
        _git_commit_initial(repo, {"a.txt": "initial"})
        # 追跡済みファイルを編集して未コミット差分を発生させる
        (repo / "a.txt").write_text("modified", encoding="utf-8")
        sid = "push-dirty-block"
        self._write_state(tmp_path, sid, {"amend_pending_status_check": {str(repo): True}})
        result = self._invoke("git push origin master", sid, state_dir, cwd=str(repo))
        assert result.returncode == 2
        assert "amend" in result.stderr
        # ブロック時はフラグ解除されない
        assert self._read_flag(tmp_path, sid, str(repo)) is True

    def test_allows_real_push_when_flag_true_and_clean_resets_flag(
        self, state_dir: dict[str, str], tmp_path: pathlib.Path
    ) -> None:
        repo = tmp_path / "repo-clean"
        repo.mkdir()
        _init_git_repo(repo)
        _git_commit_initial(repo, {"a.txt": "initial"})
        sid = "push-clean-real"
        self._write_state(tmp_path, sid, {"amend_pending_status_check": {str(repo): True}})
        result = self._invoke("git push origin master", sid, state_dir, cwd=str(repo))
        assert result.returncode == 0
        # 実送出pushのclean通過時のみフラグ解除される
        assert self._read_flag(tmp_path, sid, str(repo)) is False

    def test_dry_run_clean_does_not_reset_flag(self, state_dir: dict[str, str], tmp_path: pathlib.Path) -> None:
        repo = tmp_path / "repo-dryclean"
        repo.mkdir()
        _init_git_repo(repo)
        _git_commit_initial(repo, {"a.txt": "initial"})
        sid = "push-clean-dryrun"
        self._write_state(tmp_path, sid, {"amend_pending_status_check": {str(repo): True}})
        result = self._invoke("git push --dry-run origin master", sid, state_dir, cwd=str(repo))
        assert result.returncode == 0
        # dry-runではフラグ解除されない
        assert self._read_flag(tmp_path, sid, str(repo)) is True

    def test_dash_n_clean_does_not_reset_flag(self, state_dir: dict[str, str], tmp_path: pathlib.Path) -> None:
        """`-n`は`--dry-run`の短縮形として扱われ、cleanでもフラグ解除されない。"""
        repo = tmp_path / "repo-dashnclean"
        repo.mkdir()
        _init_git_repo(repo)
        _git_commit_initial(repo, {"a.txt": "initial"})
        sid = "push-clean-dashn"
        self._write_state(tmp_path, sid, {"amend_pending_status_check": {str(repo): True}})
        result = self._invoke("git push -n origin master", sid, state_dir, cwd=str(repo))
        assert result.returncode == 0
        assert self._read_flag(tmp_path, sid, str(repo)) is True

    def test_dry_run_dirty_still_blocks(self, state_dir: dict[str, str], tmp_path: pathlib.Path) -> None:
        repo = tmp_path / "repo-drydirty"
        repo.mkdir()
        _init_git_repo(repo)
        _git_commit_initial(repo, {"a.txt": "initial"})
        (repo / "a.txt").write_text("modified", encoding="utf-8")
        sid = "push-dirty-dryrun"
        self._write_state(tmp_path, sid, {"amend_pending_status_check": {str(repo): True}})
        result = self._invoke("git push --dry-run origin master", sid, state_dir, cwd=str(repo))
        assert result.returncode == 2

    def test_flag_false_bypasses_check(self, state_dir: dict[str, str], tmp_path: pathlib.Path) -> None:
        repo = tmp_path / "repo-noflag"
        repo.mkdir()
        _init_git_repo(repo)
        _git_commit_initial(repo, {"a.txt": "initial"})
        (repo / "a.txt").write_text("modified", encoding="utf-8")
        sid = "push-no-flag"
        # フラグ未設定でもdirtyでも通過（対象外）
        result = self._invoke("git push origin master", sid, state_dir, cwd=str(repo))
        assert result.returncode == 0

    def test_other_cwd_flag_does_not_affect_push(self, state_dir: dict[str, str], tmp_path: pathlib.Path) -> None:
        repo = tmp_path / "repo-othercwd"
        repo.mkdir()
        _init_git_repo(repo)
        _git_commit_initial(repo, {"a.txt": "initial"})
        (repo / "a.txt").write_text("modified", encoding="utf-8")
        sid = "push-other-cwd"
        # 別cwdのフラグはpushに影響しない
        self._write_state(tmp_path, sid, {"amend_pending_status_check": {"/other/repo": True}})
        result = self._invoke("git push origin master", sid, state_dir, cwd=str(repo))
        assert result.returncode == 0

    def test_dash_c_push_uses_dash_c_cwd_flag(self, state_dir: dict[str, str], tmp_path: pathlib.Path) -> None:
        repo = tmp_path / "repo-dashc"
        repo.mkdir()
        _init_git_repo(repo)
        _git_commit_initial(repo, {"a.txt": "initial"})
        (repo / "a.txt").write_text("modified", encoding="utf-8")
        sid = "push-dash-c"
        # payload cwdは別で、`git -C <repo>`で切り替える。フラグは`repo`側に設定する
        self._write_state(tmp_path, sid, {"amend_pending_status_check": {str(repo): True}})
        result = self._invoke(f"git -C {repo} push origin master", sid, state_dir, cwd=str(tmp_path))
        assert result.returncode == 2

    def test_cd_then_push_uses_cd_cwd_flag(self, state_dir: dict[str, str], tmp_path: pathlib.Path) -> None:
        repo = tmp_path / "repo-cd"
        repo.mkdir()
        _init_git_repo(repo)
        _git_commit_initial(repo, {"a.txt": "initial"})
        (repo / "a.txt").write_text("modified", encoding="utf-8")
        sid = "push-cd"
        self._write_state(tmp_path, sid, {"amend_pending_status_check": {str(repo): True}})
        result = self._invoke(f"cd {repo} && git push origin master", sid, state_dir, cwd=str(tmp_path))
        assert result.returncode == 2

    def test_dry_run_dirty_block_range_matches_real_push(self, state_dir: dict[str, str], tmp_path: pathlib.Path) -> None:
        """判定範囲の統一: `--dry-run`でもdirty判定は実施される（再確認）。"""
        repo = tmp_path / "repo-dryrange"
        repo.mkdir()
        _init_git_repo(repo)
        _git_commit_initial(repo, {"a.txt": "initial"})
        (repo / "a.txt").write_text("modified", encoding="utf-8")
        sid = "push-dryrange"
        self._write_state(tmp_path, sid, {"amend_pending_status_check": {str(repo): True}})
        for cmd in ("git push --dry-run origin master", "git push -n origin master"):
            result = self._invoke(cmd, sid, state_dir, cwd=str(repo))
            # `-n`は`--dry-run`の短縮形として同様に扱われ、dirty判定はどちらでも実施されblockになる
            assert result.returncode == 2, f"expected block for {cmd!r}"

    def test_git_status_does_not_clear_flag_then_push_still_blocks(
        self, state_dir: dict[str, str], tmp_path: pathlib.Path
    ) -> None:
        """amend後→`git status`→dirtyのまま`git push`がblockされる（`git status`解除方式でない検証）。"""
        repo = tmp_path / "repo-status-noop"
        repo.mkdir()
        _init_git_repo(repo)
        _git_commit_initial(repo, {"a.txt": "initial"})
        (repo / "a.txt").write_text("modified", encoding="utf-8")
        sid = "push-status-noop"
        self._write_state(tmp_path, sid, {"amend_pending_status_check": {str(repo): True}})
        # `git status`はpretooluse側では何もしない。flagは残ったまま
        self._invoke("git status", sid, state_dir, cwd=str(repo))
        result = self._invoke("git push origin master", sid, state_dir, cwd=str(repo))
        assert result.returncode == 2


class TestBashUvRunPythonBlock:
    """`uv run python <path>`形式の起動ブロック。

    `[tool.uv]`のみで`[project]`セクションが無いcwdで`uv run python <path>`
    を実行すると、uvがcwdをプロジェクト解決対象として扱い`.venv`と`uv.lock`
    を生成する副作用がある。エージェントがPEP 723スクリプトを誤起動する事故を
    予防的にブロックするためのテスト。
    """

    @staticmethod
    def _make_python_project(tmp_path: pathlib.Path) -> str:
        """`[project]`セクション付きpyproject.tomlを作成しcwd文字列を返す。"""
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "demo"\nversion = "0.0.0"\n',
            encoding="utf-8",
        )
        return str(tmp_path)

    @staticmethod
    def _make_non_python_project(tmp_path: pathlib.Path) -> str:
        """`[tool.uv]`のみ持つpyproject.tomlを作成しcwd文字列を返す。"""
        (tmp_path / "pyproject.toml").write_text(
            '[tool.uv]\nexclude-newer = "2025-01-01"\n',
            encoding="utf-8",
        )
        return str(tmp_path)

    @staticmethod
    def _invoke(command: str, cwd: str) -> subprocess.CompletedProcess[str]:
        return _run({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": cwd})

    def test_script_option_allowed(self, tmp_path: pathlib.Path):
        """`--script`経由はcwdの依存解決を行わないため許容する。"""
        cwd = self._make_non_python_project(tmp_path)
        result = self._invoke("uv run --script /tmp/foo.py", cwd)
        assert result.returncode == 0

    def test_no_project_option_allowed(self, tmp_path: pathlib.Path):
        """`--no-project`経由はcwdの依存解決を行わないため許容する。"""
        cwd = self._make_non_python_project(tmp_path)
        result = self._invoke("uv run --no-project python -c 'print(1)'", cwd)
        assert result.returncode == 0

    def test_python_project_allowed(self, tmp_path: pathlib.Path):
        """`[project]`セクション付きcwdでは`uv run python -c '...'`を許容する。"""
        cwd = self._make_python_project(tmp_path)
        result = self._invoke("uv run python -c 'print(1)'", cwd)
        assert result.returncode == 0

    def test_non_python_project_blocked(self, tmp_path: pathlib.Path):
        cwd = self._make_non_python_project(tmp_path)
        result = self._invoke("uv run python /tmp/foo.py", cwd)
        assert result.returncode == 2
        assert "uv run python" in result.stderr
        assert "[auto-generated: agent-toolkit/pretooluse]" in result.stderr

    def test_no_pyproject_blocked(self, tmp_path: pathlib.Path):
        """pyproject.tomlが無いcwdでもblockする（Pythonプロジェクトと認識できないため）。"""
        result = self._invoke("uv run python /tmp/foo.py", str(tmp_path))
        assert result.returncode == 2

    def test_script_after_python_blocked(self, tmp_path: pathlib.Path):
        """`uv run python --script s.py`は`--script`がpythonの引数となるため例外扱いしない。"""
        cwd = self._make_non_python_project(tmp_path)
        result = self._invoke("uv run python --script s.py", cwd)
        assert result.returncode == 2

    def test_no_project_after_python_blocked(self, tmp_path: pathlib.Path):
        """`uv run python --no-project s.py`は同上の理由で例外扱いしない。"""
        cwd = self._make_non_python_project(tmp_path)
        result = self._invoke("uv run python --no-project s.py", cwd)
        assert result.returncode == 2

    def test_cd_then_uv_run_blocked(self, tmp_path: pathlib.Path):
        """payload cwdがPythonプロジェクトでも、先行`cd`で実行時cwdが変わる場合はblock。"""
        cwd = self._make_python_project(tmp_path)
        result = self._invoke("cd /tmp && uv run python /tmp/foo.py", cwd)
        assert result.returncode == 2

    def test_pushd_then_uv_run_blocked(self, tmp_path: pathlib.Path):
        cwd = self._make_python_project(tmp_path)
        result = self._invoke("pushd /tmp && uv run python /tmp/foo.py", cwd)
        assert result.returncode == 2

    def test_uv_directory_option_blocked(self, tmp_path: pathlib.Path):
        """`uv --directory`はプロジェクト解決対象をpayload cwdから外すためblock。"""
        cwd = self._make_python_project(tmp_path)
        result = self._invoke("uv --directory /tmp run python /tmp/foo.py", cwd)
        assert result.returncode == 2

    def test_uv_project_global_option_blocked(self, tmp_path: pathlib.Path):
        cwd = self._make_python_project(tmp_path)
        result = self._invoke("uv --project /tmp run python /tmp/foo.py", cwd)
        assert result.returncode == 2

    def test_uv_run_project_option_blocked(self, tmp_path: pathlib.Path):
        """runサブコマンドオプション位置の`--project=`もblock対象。"""
        cwd = self._make_python_project(tmp_path)
        result = self._invoke("uv run --project=/tmp python /tmp/foo.py", cwd)
        assert result.returncode == 2

    def test_cd_with_no_project_allowed(self, tmp_path: pathlib.Path):
        """cwd変更があっても`--no-project`例外が優先するため許容する。"""
        cwd = self._make_python_project(tmp_path)
        result = self._invoke("cd /tmp && uv run --no-project python -c 'print(1)'", cwd)
        assert result.returncode == 0

    def test_unrelated_command_unaffected(self, tmp_path: pathlib.Path):
        """`uv run pytest`などは対象外（`python`トークンを含まない）。"""
        cwd = self._make_non_python_project(tmp_path)
        result = self._invoke("uv run pytest tests/", cwd)
        assert result.returncode == 0

    def test_uvx_unaffected(self, tmp_path: pathlib.Path):
        """`uvx`は別コマンドのため対象外。"""
        cwd = self._make_non_python_project(tmp_path)
        result = self._invoke("uvx ruff check .", cwd)
        assert result.returncode == 0


class TestCodexExecRouteGate:
    """メインセッションのcodex-exec起動ゲート。"""

    @pytest.fixture(name="state_dir")
    def _state_dir(self, tmp_path: pathlib.Path) -> dict[str, str]:
        return _plan_file_state_env(tmp_path)

    _write_state = staticmethod(_write_session_state)

    def test_blocked_when_skill_not_invoked(self, state_dir: dict[str, str]) -> None:
        """メインセッションでcodex-exec未起動の初回呼び出しをブロックする。"""
        result = _run(
            {"tool_name": "mcp__codex__codex", "tool_input": {"prompt": "hello"}, "session_id": "no-review"},
            env_overrides=state_dir,
        )
        assert result.returncode == 2
        assert "agent-toolkit:codex-exec" in result.stderr

    def test_allowed_when_skill_invoked(self, state_dir: dict[str, str], tmp_path: pathlib.Path) -> None:
        """codex-exec起動後の初回呼び出しを許可する。"""
        self._write_state(tmp_path, "with-review", {"codex_exec_skill_invoked": True})
        result = _run(
            {
                "tool_name": "mcp__codex__codex",
                "tool_input": {"prompt": "hello", "sandbox": "danger-full-access", "cwd": "/tmp/workdir"},
                "session_id": "with-review",
            },
            env_overrides=state_dir,
        )
        assert result.returncode == 0
        out = json.loads(result.stdout)
        assert out["hookSpecificOutput"]["permissionDecision"] == "allow"
        assert out["hookSpecificOutput"]["updatedInput"]["sandbox"] == "danger-full-access"

    def test_allowed_when_sidechain_without_skill_record(self, state_dir: dict[str, str]) -> None:
        """サイドチェーンでは明示Skill起動記録を要求しない。"""
        result = _run(
            {
                "tool_name": "mcp__codex__codex",
                "tool_input": {"prompt": "hello", "sandbox": "danger-full-access", "cwd": "/tmp/workdir"},
                "session_id": "sidechain-invoked",
                "isSidechain": True,
            },
            env_overrides=state_dir,
        )
        assert result.returncode == 0
        out = json.loads(result.stdout)
        assert out["hookSpecificOutput"]["permissionDecision"] == "allow"

    def test_blocked_when_not_sidechain_and_skill_missing(self, state_dir: dict[str, str]) -> None:
        """`isSidechain`が偽の呼び出しへメインセッションゲートを適用する。"""
        result = _run(
            {
                "tool_name": "mcp__codex__codex",
                "tool_input": {"prompt": "hello"},
                "session_id": "not-sidechain",
                "isSidechain": False,
            },
            env_overrides=state_dir,
        )
        assert result.returncode == 2
        assert "agent-toolkit:codex-exec" in result.stderr


class TestCodexMcpExecution:
    """codex MCP sandbox明示指定の強制・approval-policy自動修正（CLI統合テスト）。"""

    @pytest.fixture(name="state_dir")
    def _state_dir(self, tmp_path: pathlib.Path) -> dict[str, str]:
        return _plan_file_state_env(tmp_path)

    _write_state = staticmethod(_write_session_state)

    def test_sandbox_unspecified_blocked(self, state_dir: dict[str, str], tmp_path: pathlib.Path):
        """sandboxが未指定の場合はブロックする。"""
        self._write_state(tmp_path, "fix1", {"codex_exec_skill_invoked": True})
        result = _run(
            {"tool_name": "mcp__codex__codex", "tool_input": {"prompt": "hello"}, "session_id": "fix1"},
            env_overrides=state_dir,
        )
        assert result.returncode == 2
        assert "danger-full-access" in result.stderr

    @pytest.mark.parametrize("sandbox", ["network-only", "read-only", "workspace-write"])
    def test_sandbox_other_values_blocked(self, sandbox: str, state_dir: dict[str, str], tmp_path: pathlib.Path):
        """`danger-full-access`以外のsandbox指定はブロックする。"""
        self._write_state(tmp_path, "fix2", {"codex_exec_skill_invoked": True})
        result = _run(
            {
                "tool_name": "mcp__codex__codex",
                "tool_input": {"prompt": "hello", "sandbox": sandbox},
                "session_id": "fix2",
            },
            env_overrides=state_dir,
        )
        assert result.returncode == 2
        assert "danger-full-access" in result.stderr

    def test_sandbox_blocked_in_sidechain(self, state_dir: dict[str, str], tmp_path: pathlib.Path):
        """サブエージェント内部からの呼び出しでもsandbox検査を適用する。"""
        self._write_state(tmp_path, "fix_side", {"codex_exec_skill_invoked": True})
        result = _run(
            {
                "tool_name": "mcp__codex__codex",
                "tool_input": {"prompt": "hello", "sandbox": "read-only"},
                "session_id": "fix_side",
                "isSidechain": True,
            },
            env_overrides=state_dir,
        )
        assert result.returncode == 2
        assert "danger-full-access" in result.stderr

    def test_sandbox_correct_no_message(self, state_dir: dict[str, str], tmp_path: pathlib.Path):
        """sandbox・approval-policyが共に既定値の場合、updatedInputは返すがsystemMessageを含めない。"""
        self._write_state(tmp_path, "fix3", {"codex_exec_skill_invoked": True})
        result = _run(
            {
                "tool_name": "mcp__codex__codex",
                "tool_input": {
                    "prompt": "hello",
                    "sandbox": "danger-full-access",
                    "approval-policy": "never",
                    "cwd": "/tmp/workdir",
                },
                "session_id": "fix3",
            },
            env_overrides=state_dir,
        )
        assert result.returncode == 0
        out = json.loads(result.stdout)
        assert out["hookSpecificOutput"]["permissionDecision"] == "allow"
        assert out["hookSpecificOutput"]["updatedInput"]["sandbox"] == "danger-full-access"
        assert out["hookSpecificOutput"]["updatedInput"]["approval-policy"] == "never"
        assert "systemMessage" not in out

    def test_approval_policy_wrong_value_auto_fix_with_correct_sandbox(self, state_dir: dict[str, str], tmp_path: pathlib.Path):
        """sandboxが正しい値でもapproval-policyのみ誤りなら単独でneverへ強制修正する。"""
        self._write_state(tmp_path, "fix_ap", {"codex_exec_skill_invoked": True})
        result = _run(
            {
                "tool_name": "mcp__codex__codex",
                "tool_input": {
                    "prompt": "hello",
                    "sandbox": "danger-full-access",
                    "approval-policy": "on-request",
                    "cwd": "/tmp/workdir",
                },
                "session_id": "fix_ap",
            },
            env_overrides=state_dir,
        )
        assert result.returncode == 0
        out = json.loads(result.stdout)
        updated = out["hookSpecificOutput"]["updatedInput"]
        assert updated["sandbox"] == "danger-full-access"
        assert updated["approval-policy"] == "never"
        assert "forced" in out["systemMessage"]


class TestCheckCodexMcpSandbox:
    """`_check_codex_mcp_sandbox`単体テスト（`danger-full-access`明示指定の強制）。"""

    @pytest.mark.parametrize("sandbox", ["read-only", "workspace-write"])
    def test_blocks_other_sandbox_modes(self, sandbox: str, capsys: pytest.CaptureFixture[str]) -> None:
        blocked = pretooluse._check_codex_mcp_sandbox({"prompt": "test", "sandbox": sandbox})  # noqa: SLF001  # pylint: disable=protected-access
        assert blocked is True
        assert sandbox in capsys.readouterr().err

    def test_blocks_unspecified_sandbox(self, capsys: pytest.CaptureFixture[str]) -> None:
        blocked = pretooluse._check_codex_mcp_sandbox({"prompt": "test"})  # noqa: SLF001  # pylint: disable=protected-access
        assert blocked is True
        assert "unspecified" in capsys.readouterr().err

    def test_allows_danger_full_access(self, capsys: pytest.CaptureFixture[str]) -> None:
        blocked = pretooluse._check_codex_mcp_sandbox({"prompt": "test", "sandbox": "danger-full-access"})  # noqa: SLF001  # pylint: disable=protected-access
        assert blocked is False
        assert capsys.readouterr().err == ""


class TestCheckCodexMcpCwd:
    """`mcp__codex__codex`呼び出しの`cwd`絶対パス強制（CLI統合テスト、公開インターフェース経由）。"""

    @pytest.fixture(name="state_dir")
    def _state_dir(self, tmp_path: pathlib.Path) -> dict[str, str]:
        return _plan_file_state_env(tmp_path)

    _write_state = staticmethod(_write_session_state)

    def test_blocks_missing_cwd(self, state_dir: dict[str, str], tmp_path: pathlib.Path) -> None:
        """`cwd`未指定の場合はブロックする。"""
        self._write_state(tmp_path, "cwd-missing", {"codex_exec_skill_invoked": True})
        result = _run(
            {
                "tool_name": "mcp__codex__codex",
                "tool_input": {"prompt": "hello", "sandbox": "danger-full-access"},
                "session_id": "cwd-missing",
            },
            env_overrides=state_dir,
        )
        assert result.returncode == 2
        assert "unspecified" in result.stderr

    def test_blocks_empty_string_cwd(self, state_dir: dict[str, str], tmp_path: pathlib.Path) -> None:
        """`cwd`が空文字列の場合はブロックする。"""
        self._write_state(tmp_path, "cwd-empty", {"codex_exec_skill_invoked": True})
        result = _run(
            {
                "tool_name": "mcp__codex__codex",
                "tool_input": {"prompt": "hello", "sandbox": "danger-full-access", "cwd": ""},
                "session_id": "cwd-empty",
            },
            env_overrides=state_dir,
        )
        assert result.returncode == 2
        assert "unspecified" in result.stderr

    def test_blocks_whitespace_only_cwd(self, state_dir: dict[str, str], tmp_path: pathlib.Path) -> None:
        """`cwd`が空白のみの場合はブロックする。"""
        self._write_state(tmp_path, "cwd-whitespace", {"codex_exec_skill_invoked": True})
        result = _run(
            {
                "tool_name": "mcp__codex__codex",
                "tool_input": {"prompt": "hello", "sandbox": "danger-full-access", "cwd": "   "},
                "session_id": "cwd-whitespace",
            },
            env_overrides=state_dir,
        )
        assert result.returncode == 2
        assert "`   `" in result.stderr

    def test_blocks_relative_path_cwd(self, state_dir: dict[str, str], tmp_path: pathlib.Path) -> None:
        """`cwd`が相対パスの場合はブロックする。"""
        self._write_state(tmp_path, "cwd-relative", {"codex_exec_skill_invoked": True})
        result = _run(
            {
                "tool_name": "mcp__codex__codex",
                "tool_input": {"prompt": "hello", "sandbox": "danger-full-access", "cwd": "relative/path"},
                "session_id": "cwd-relative",
            },
            env_overrides=state_dir,
        )
        assert result.returncode == 2
        assert "relative/path" in result.stderr

    def test_allows_absolute_path_cwd(self, state_dir: dict[str, str], tmp_path: pathlib.Path) -> None:
        """`cwd`が絶対パスの場合は許可する。"""
        self._write_state(tmp_path, "cwd-absolute", {"codex_exec_skill_invoked": True})
        result = _run(
            {
                "tool_name": "mcp__codex__codex",
                "tool_input": {"prompt": "hello", "sandbox": "danger-full-access", "cwd": "/home/aki/dotfiles"},
                "session_id": "cwd-absolute",
            },
            env_overrides=state_dir,
        )
        assert result.returncode == 0
        out = json.loads(result.stdout)
        assert out["hookSpecificOutput"]["permissionDecision"] == "allow"


class TestCheckCodexMcpExecution:
    """`_check_codex_mcp_execution`単体テスト（approval-policyの強制固定）。"""

    def test_forces_approval_policy(self) -> None:
        tool_input = {"prompt": "test", "sandbox": "danger-full-access"}
        result = pretooluse._check_codex_mcp_execution(tool_input)  # noqa: SLF001  # pylint: disable=protected-access
        updated = result["hookSpecificOutput"]["updatedInput"]
        assert updated["approval-policy"] == "never"

    def test_overrides_user_specified_value(self) -> None:
        tool_input = {"prompt": "test", "sandbox": "danger-full-access", "approval-policy": "on-request"}
        result = pretooluse._check_codex_mcp_execution(tool_input)  # noqa: SLF001  # pylint: disable=protected-access
        updated = result["hookSpecificOutput"]["updatedInput"]
        assert updated["approval-policy"] == "never"
        assert "forced" in result["systemMessage"]

    def test_no_system_message_when_already_correct(self) -> None:
        tool_input = {"prompt": "test", "sandbox": "danger-full-access", "approval-policy": "never"}
        result = pretooluse._check_codex_mcp_execution(tool_input)  # noqa: SLF001  # pylint: disable=protected-access
        assert "systemMessage" not in result


class TestCodexMcpReply:
    """mcp__codex__codex-reply強制承認。"""

    @pytest.fixture(name="state_dir")
    def _state_dir(self, tmp_path: pathlib.Path) -> dict[str, str]:
        return _plan_file_state_env(tmp_path)

    _write_state = staticmethod(_write_session_state)

    def test_reply_auto_approved(self, state_dir: dict[str, str], tmp_path: pathlib.Path):
        """codex-exec起動後は`mcp__codex__codex-reply`が強制承認される。"""
        self._write_state(tmp_path, "reply1", {"codex_exec_skill_invoked": True})
        result = _run(
            {
                "tool_name": "mcp__codex__codex-reply",
                "tool_input": {"threadId": "abc", "prompt": "next"},
                "session_id": "reply1",
            },
            env_overrides=state_dir,
        )
        assert result.returncode == 0
        out = json.loads(result.stdout)
        assert out["hookSpecificOutput"]["permissionDecision"] == "allow"


class TestCodexMcpLanguageWarningMerge:
    """codex MCP強制承認時に保留言語警告が単一JSONへ統合されることを検証する。

    `flush_pending_language_warning()`を廃止し`emit_json()`単独で承認とadditionalContextを
    出力する回帰を防ぐ。stdoutが2件のJSONへ分裂しないこと・`additionalContext`に
    警告本文が統合されることを確認する。
    """

    _state_env = staticmethod(_plan_file_state_env)
    _write_state = staticmethod(_write_session_state)

    @staticmethod
    def _write_transcript(tmp_path: pathlib.Path, text: str) -> pathlib.Path:
        entry = {
            "type": "assistant",
            "message": {
                "id": "m-lang",
                "role": "assistant",
                "content": [{"type": "text", "text": text}],
                "stop_reason": "end_turn",
            },
        }
        path = tmp_path / "transcript.jsonl"
        path.write_text(json.dumps(entry, ensure_ascii=False) + "\n", encoding="utf-8")
        return path

    def test_codex_merges_pending_language_warning(self, tmp_path: pathlib.Path):
        """mcp__codex__codex分岐で保留警告が承認JSONへ統合される。"""
        env = self._state_env(tmp_path)
        self._write_state(tmp_path, "codex-lang", {"codex_exec_skill_invoked": True})
        transcript = self._write_transcript(tmp_path, "A" * 100)
        result = _run(
            {
                "tool_name": "mcp__codex__codex",
                "tool_input": {"prompt": "hello", "sandbox": "danger-full-access", "cwd": "/tmp/workdir"},
                "transcript_path": str(transcript),
                "session_id": "codex-lang",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        # stdoutは単一JSONオブジェクトとしてパースできる（2件分裂していない）
        out = json.loads(result.stdout)
        assert out["hookSpecificOutput"]["permissionDecision"] == "allow"
        assert "英語主体" in out["hookSpecificOutput"]["additionalContext"]

    def test_codex_reply_merges_pending_language_warning(self, tmp_path: pathlib.Path):
        """mcp__codex__codex-reply分岐で保留警告が承認JSONへ統合される。"""
        env = self._state_env(tmp_path)
        self._write_state(tmp_path, "reply-lang", {"codex_exec_skill_invoked": True})
        transcript = self._write_transcript(tmp_path, "A" * 100)
        result = _run(
            {
                "tool_name": "mcp__codex__codex-reply",
                "tool_input": {"threadId": "abc", "prompt": "next"},
                "transcript_path": str(transcript),
                "session_id": "reply-lang",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        out = json.loads(result.stdout)
        assert out["hookSpecificOutput"]["permissionDecision"] == "allow"
        assert "英語主体" in out["hookSpecificOutput"]["additionalContext"]


class TestIssSidechainProbe:
    """`_record_iss_sidechain_probe`によるisSidechain実値採取デバッグログ（FB7対応）。"""

    _state_env = staticmethod(_plan_file_state_env)
    _write_state = staticmethod(_write_session_state)

    @staticmethod
    def _log_path(tmp_path: pathlib.Path, session_id: str) -> pathlib.Path:
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", session_id)
        return tmp_path / f"claude-agent-toolkit-issidechain-{safe}.log"

    def test_writes_one_jsonl_line_under_tempdir(self, tmp_path: pathlib.Path):
        """`tempfile.gettempdir()`起点、session_id含むパスへJSONL 1行を追記する。"""
        env = self._state_env(tmp_path)
        self._write_state(tmp_path, "probe1", {"codex_exec_skill_invoked": True})
        result = _run(
            {
                "tool_name": "mcp__codex__codex",
                "tool_input": {"prompt": "hello", "sandbox": "danger-full-access", "cwd": "/tmp/workdir"},
                "session_id": "probe1",
                "isSidechain": False,
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        log_path = self._log_path(tmp_path, "probe1")
        lines = log_path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1

    def test_recorded_fields_include_expected_keys(self, tmp_path: pathlib.Path):
        """記録項目に`isSidechain`・`session_id`・`tool_name`・`transcript_path`・`cwd`・`current_plan_file_path`が含まれる。"""
        env = self._state_env(tmp_path)
        self._write_state(
            tmp_path,
            "probe2",
            {
                "codex_exec_skill_invoked": True,
                "current_plan_file_path": "/tmp/plan.md",
            },
        )
        result = _run(
            {
                "tool_name": "mcp__codex__codex",
                "tool_input": {"prompt": "hello", "sandbox": "danger-full-access", "cwd": "/tmp/workdir"},
                "session_id": "probe2",
                "isSidechain": False,
                "transcript_path": "/tmp/transcript.jsonl",
                "cwd": "/tmp/workdir",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        entry = json.loads(self._log_path(tmp_path, "probe2").read_text(encoding="utf-8").splitlines()[0])
        assert entry["isSidechain"] is False
        assert entry["session_id"] == "probe2"
        assert entry["tool_name"] == "mcp__codex__codex"
        assert entry["transcript_path"] == "/tmp/transcript.jsonl"
        assert entry["cwd"] == "/tmp/workdir"
        assert entry["current_plan_file_path"] == "/tmp/plan.md"

    def test_iss_sidechain_absent_is_recorded_as_null(self, tmp_path: pathlib.Path):
        """`isSidechain`欠落時に`null`が記録される。"""
        env = self._state_env(tmp_path)
        self._write_state(tmp_path, "probe3", {"codex_exec_skill_invoked": True})
        result = _run(
            {
                "tool_name": "mcp__codex__codex",
                "tool_input": {"prompt": "hello", "sandbox": "danger-full-access", "cwd": "/tmp/workdir"},
                "session_id": "probe3",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        entry = json.loads(self._log_path(tmp_path, "probe3").read_text(encoding="utf-8").splitlines()[0])
        assert entry["isSidechain"] is None

    def test_iss_sidechain_non_boolean_is_recorded_as_is(self, tmp_path: pathlib.Path):
        """`isSidechain`が非boolean型（整数・文字列など）でもそのまま記録される。"""
        env = self._state_env(tmp_path)
        self._write_state(tmp_path, "probe4", {"codex_exec_skill_invoked": True})
        result = _run(
            {
                "tool_name": "mcp__codex__codex",
                "tool_input": {"prompt": "hello", "sandbox": "danger-full-access", "cwd": "/tmp/workdir"},
                "session_id": "probe4",
                "isSidechain": "yes",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        entry = json.loads(self._log_path(tmp_path, "probe4").read_text(encoding="utf-8").splitlines()[0])
        assert entry["isSidechain"] == "yes"

    def test_os_error_is_swallowed_and_execution_continues(self, tmp_path: pathlib.Path):
        """ログ出力先の書き込みで`OSError`が発生しても例外を送出せず処理を継続する。"""
        blocked_tmpdir = tmp_path / "not-a-directory"
        blocked_tmpdir.write_text("x", encoding="utf-8")
        env = {"TMPDIR": str(blocked_tmpdir), "TEMP": str(blocked_tmpdir), "TMP": str(blocked_tmpdir)}
        result = _run(
            {
                "tool_name": "mcp__codex__codex",
                "tool_input": {"prompt": "hello", "sandbox": "danger-full-access", "cwd": "/tmp/workdir"},
                "session_id": "probe-oserror",
                "isSidechain": True,
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        out = json.loads(result.stdout)
        assert out["hookSpecificOutput"]["permissionDecision"] == "allow"

    def test_rotates_when_log_exceeds_one_megabyte(self, tmp_path: pathlib.Path):
        """ログファイルが1MB超過時に`_file_lock.rotate_if_needed`経由で`.1`世代ファイルへローテートされる。"""
        env = self._state_env(tmp_path)
        self._write_state(tmp_path, "probe-rotate", {"codex_exec_skill_invoked": True})
        log_path = self._log_path(tmp_path, "probe-rotate")
        log_path.write_text("x" * (1_000_001), encoding="utf-8")
        result = _run(
            {
                "tool_name": "mcp__codex__codex",
                "tool_input": {"prompt": "hello", "sandbox": "danger-full-access", "cwd": "/tmp/workdir"},
                "session_id": "probe-rotate",
                "isSidechain": False,
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        rotated = log_path.with_suffix(log_path.suffix + ".1")
        assert rotated.exists()
        assert len(rotated.read_text(encoding="utf-8")) == 1_000_001
        assert len(log_path.read_text(encoding="utf-8").splitlines()) == 1

    def test_called_for_codex_reply_tool(self, tmp_path: pathlib.Path):
        """`mcp__codex__codex-reply`の呼び出し時にも本ヘルパーが呼ばれる。"""
        env = self._state_env(tmp_path)
        self._write_state(tmp_path, "probe-reply", {"codex_exec_skill_invoked": True})
        result = _run(
            {
                "tool_name": "mcp__codex__codex-reply",
                "tool_input": {"threadId": "abc", "prompt": "next"},
                "session_id": "probe-reply",
                "isSidechain": False,
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        log_path = self._log_path(tmp_path, "probe-reply")
        entry = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
        assert entry["tool_name"] == "mcp__codex__codex-reply"

    def test_called_even_when_iss_sidechain_true(self, tmp_path: pathlib.Path):
        """`isSidechain=True`ケースでも本ヘルパーが呼ばれる（既存ゲートより前で実行される確認）。"""
        env = self._state_env(tmp_path)
        result = _run(
            {
                "tool_name": "mcp__codex__codex",
                "tool_input": {"prompt": "hello", "sandbox": "danger-full-access", "cwd": "/tmp/workdir"},
                "session_id": "probe-sidechain-true",
                "isSidechain": True,
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        log_path = self._log_path(tmp_path, "probe-sidechain-true")
        entry = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
        assert entry["isSidechain"] is True


class TestBashAgentToolkitVersionBump:
    """agent-toolkit/配下コミット時のversion bump漏れ警告。

    pretooluse.pyがsubprocess経由で起動されるため、subprocess.runの差し替えではなく
    実gitリポジトリを構築して判定動作を検証する（既存testパターンと整合する）。
    """

    @staticmethod
    def _init_repo(repo: pathlib.Path) -> None:
        subprocess.run(["git", "init"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=str(repo), capture_output=True, check=True)

    @classmethod
    def _make_repo(cls, tmp_path: pathlib.Path, staged: dict[str, str] | None = None) -> pathlib.Path:
        repo = tmp_path / "repo"
        repo.mkdir()
        cls._init_repo(repo)
        (repo / "seed.txt").write_text("seed")
        subprocess.run(["git", "add", "seed.txt"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), capture_output=True, check=True)
        if staged:
            for name, content in staged.items():
                target = repo / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
                subprocess.run(["git", "add", name], cwd=str(repo), capture_output=True, check=True)
        return repo

    @classmethod
    def _make_repo_with_upstream(
        cls,
        tmp_path: pathlib.Path,
        unpushed_files: dict[str, str],
        staged: dict[str, str],
    ) -> pathlib.Path:
        """upstreamを持ち、unpushed_filesを含む未プッシュコミットがある状態を構築する。"""
        upstream = tmp_path / "upstream.git"
        subprocess.run(["git", "init", "--bare", str(upstream)], capture_output=True, check=True)
        repo = tmp_path / "repo"
        repo.mkdir()
        cls._init_repo(repo)
        (repo / "seed.txt").write_text("seed")
        subprocess.run(["git", "add", "seed.txt"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "remote", "add", "origin", str(upstream)], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "push", "-u", "origin", "HEAD:refs/heads/main"], cwd=str(repo), capture_output=True, check=True)
        for name, content in unpushed_files.items():
            target = repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
            subprocess.run(["git", "add", name], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "unpushed"], cwd=str(repo), capture_output=True, check=True)
        for name, content in staged.items():
            target = repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
            subprocess.run(["git", "add", name], cwd=str(repo), capture_output=True, check=True)
        return repo

    @classmethod
    def _make_repo_with_gone_upstream(
        cls,
        tmp_path: pathlib.Path,
        default_branch_files: dict[str, str],
        staged: dict[str, str],
    ) -> pathlib.Path:
        """`@{u}`解決対象の追跡先refが存在しない（`gone`相当）状態を構築する。

        既定ブランチ（`origin/master`）の`refs/remotes/origin/HEAD`は正常に解決できる状態を保ちつつ、
        現在の作業ブランチの`branch.master.merge`だけ存在しないリモートブランチへ向けることで、
        `@{u}`のみが解決失敗する状態を実gitリポジトリ上に再現する。
        """
        upstream = tmp_path / "upstream.git"
        subprocess.run(["git", "init", "--bare", str(upstream)], capture_output=True, check=True)
        repo = tmp_path / "repo"
        repo.mkdir()
        cls._init_repo(repo)
        (repo / "seed.txt").write_text("seed")
        subprocess.run(["git", "add", "seed.txt"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "branch", "-M", "master"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "remote", "add", "origin", str(upstream)], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "push", "origin", "HEAD:refs/heads/master"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "fetch", "origin"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "remote", "set-head", "origin", "master"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "config", "branch.master.remote", "origin"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(
            ["git", "config", "branch.master.merge", "refs/heads/deleted-branch"],
            cwd=str(repo),
            capture_output=True,
            check=True,
        )
        for name, content in default_branch_files.items():
            target = repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
            subprocess.run(["git", "add", name], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "unpushed-on-default-branch"], cwd=str(repo), capture_output=True, check=True)
        for name, content in staged.items():
            target = repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
            subprocess.run(["git", "add", name], cwd=str(repo), capture_output=True, check=True)
        return repo

    @staticmethod
    def _invoke(command: str, cwd: str) -> subprocess.CompletedProcess[str]:
        return _run({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": cwd, "session_id": "vb-test"})

    @staticmethod
    def _has_version_bump_warning(result: subprocess.CompletedProcess[str]) -> bool:
        if not result.stdout.strip():
            return False
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return False
        ctx = data.get("hookSpecificOutput", {}).get("additionalContext", "")
        return "plugin.json" in ctx and "version" in ctx

    def test_non_commit_command_unaffected(self, tmp_path: pathlib.Path):
        repo = self._make_repo(tmp_path, {"agent-toolkit/skills/x/SKILL.md": "# x\n"})
        result = self._invoke("git status", str(repo))
        assert result.returncode == 0
        assert not self._has_version_bump_warning(result)

    def test_no_staged_no_warn(self, tmp_path: pathlib.Path):
        repo = self._make_repo(tmp_path)
        result = self._invoke("git commit -m 'x'", str(repo))
        assert not self._has_version_bump_warning(result)

    def test_outside_agent_toolkit_no_warn(self, tmp_path: pathlib.Path):
        repo = self._make_repo(tmp_path, {"README.md": "# r\n"})
        result = self._invoke("git commit -m 'docs'", str(repo))
        assert not self._has_version_bump_warning(result)

    def test_only_test_files_no_warn(self, tmp_path: pathlib.Path):
        repo = self._make_repo(tmp_path, {"agent-toolkit/scripts/foo_test.py": "x = 1\n"})
        result = self._invoke("git commit -m 'test'", str(repo))
        assert not self._has_version_bump_warning(result)

    def test_skill_change_warns(self, tmp_path: pathlib.Path):
        repo = self._make_repo(tmp_path, {"agent-toolkit/skills/x/SKILL.md": "# x\n"})
        result = self._invoke("git commit -m 'skill'", str(repo))
        assert result.returncode == 0
        assert self._has_version_bump_warning(result)

    def test_plugin_manifest_in_staged_no_warn(self, tmp_path: pathlib.Path):
        repo = self._make_repo(
            tmp_path,
            {
                "agent-toolkit/skills/x/SKILL.md": "# x\n",
                "agent-toolkit/.claude-plugin/plugin.json": '{"version": "1.0.1"}\n',
            },
        )
        result = self._invoke("git commit -m 'skill+bump'", str(repo))
        assert not self._has_version_bump_warning(result)

    def test_unpushed_plugin_json_change_no_warn(self, tmp_path: pathlib.Path):
        repo = self._make_repo_with_upstream(
            tmp_path,
            unpushed_files={"agent-toolkit/.claude-plugin/plugin.json": '{"version": "1.0.1"}\n'},
            staged={"agent-toolkit/skills/x/SKILL.md": "# x\n"},
        )
        result = self._invoke("git commit -m 'followup'", str(repo))
        assert not self._has_version_bump_warning(result)

    def test_gone_upstream_with_default_branch_bump_no_warn(self, tmp_path: pathlib.Path):
        repo = self._make_repo_with_gone_upstream(
            tmp_path,
            default_branch_files={"agent-toolkit/.claude-plugin/plugin.json": '{"version": "1.0.1"}\n'},
            staged={"agent-toolkit/skills/x/SKILL.md": "# x\n"},
        )
        result = self._invoke("git commit -m 'followup'", str(repo))
        assert not self._has_version_bump_warning(result)

    def test_gone_upstream_without_default_branch_bump_warns(self, tmp_path: pathlib.Path):
        repo = self._make_repo_with_gone_upstream(
            tmp_path,
            default_branch_files={"README.md": "# r\n"},
            staged={"agent-toolkit/skills/x/SKILL.md": "# x\n"},
        )
        result = self._invoke("git commit -m 'skill'", str(repo))
        assert result.returncode == 0
        assert self._has_version_bump_warning(result)


_SCOPE_ESCALATION_INPUTS = _load_scope_escalation_inputs()
_ASKUSERQUESTION_SCOPE_ESCALATION_INPUTS = [
    (text, category) for text, category in _SCOPE_ESCALATION_INPUTS if category != "pattern-conformance"
]


def test_bash_atk_mq_add_tbd_with_scope_escalation_vocabulary_allowed() -> None:
    """判定語彙を選択肢として含む確認事項の投入コマンドは拒否しない。"""
    result = _run(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "atk mq add --type=tbd glatasks '対応案は現状維持または実装変更のどちらかを確認する'"},
        }
    )
    assert result.returncode == 0


class TestBashProcessKillByPattern:
    """`Bash`経由のパターン一致プロセス終了（`pkill`・`killall`）検出（block）。"""

    @pytest.mark.parametrize(
        "command",
        [
            'pkill -f "codex exec"',
            "killall python",
            "pkill node",
        ],
    )
    def test_blocks(self, command: str):
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        assert result.returncode == 2
        assert "[auto-generated: agent-toolkit/pretooluse]" in result.stderr

    def test_kill_by_pid_allowed(self):
        result = _run({"tool_name": "Bash", "tool_input": {"command": "kill 12345"}})
        assert result.returncode == 0

    def test_unrelated_command_allowed(self):
        result = _run({"tool_name": "Bash", "tool_input": {"command": "echo killall-report"}})
        assert result.returncode == 0


class TestBashOutputTruncationWarning:
    """`Bash`経由の検証コマンド出力`tail`/`head`切り詰め検出（warning、非block）。"""

    @pytest.mark.parametrize(
        "command",
        [
            "uvx pyfltr run-for-agent | tail -20",
            "pytest -q | head -5",
        ],
    )
    def test_warns(self, command: str):
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        assert result.returncode == 0
        assert "warn" in result.stderr

    def test_tee_saved_log_silent(self):
        command = "uvx pyfltr run-for-agent 2>&1 | tee /tmp/pyfltr.log"
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        assert result.returncode == 0
        assert "warn" not in result.stderr

    def test_tee_then_tail_extraction_silent(self):
        """`tee`で全量を先に保存してから`tail`で抽出する形は切り詰めに該当しないため警告しない。"""
        command = "pytest -q | tee /tmp/test.log | tail -5"
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        assert result.returncode == 0
        assert "warn" not in result.stderr

    def test_non_verification_command_silent(self):
        result = _run({"tool_name": "Bash", "tool_input": {"command": "git log | head -5"}})
        assert result.returncode == 0
        assert "warn" not in result.stderr


class TestAskUserQuestionScopeEscalationCheck:
    """AskUserQuestion向け縮退誘発フレーズ検出ブロック。

    フレーズ本文の代わりにパターンマッチ最小単位（正規表現の最短一致）を
    隔離フィクスチャから動的に読み込む（`agent-toolkit:agent-standards`「コンテキスト汚染の回避」節）。
    """

    @pytest.mark.parametrize(("text", "category"), _ASKUSERQUESTION_SCOPE_ESCALATION_INPUTS)
    def test_option_label_text_blocks(self, text: str, category: str):
        """`options[].label`に縮退フレーズが含まれる場合はブロックする。"""
        result = _run(
            {
                "tool_name": "AskUserQuestion",
                "tool_input": {
                    "questions": [
                        {
                            "question": "approach?",
                            "header": "header",
                            "options": [{"label": text, "description": "ok"}],
                        }
                    ],
                },
            }
        )
        assert result.returncode == 2
        assert "scope-escalation phrase" in result.stderr
        assert category in result.stderr

    def test_option_label_pattern_conformance_bypassed(self):
        """選択肢labelではpattern-conformanceカテゴリを検出対象外とする。"""
        text = next(text for text, category in _SCOPE_ESCALATION_INPUTS if category == "pattern-conformance")
        result = _run(
            {
                "tool_name": "AskUserQuestion",
                "tool_input": {
                    "questions": [
                        {
                            "question": "approach?",
                            "header": "header",
                            "options": [{"label": text, "description": "ok"}],
                        }
                    ],
                },
            }
        )
        assert result.returncode == 0
        assert "scope-escalation phrase" not in result.stderr

    def test_option_description_pattern_conformance_bypassed(self):
        """選択肢descriptionではpattern-conformanceカテゴリを検出対象外とする。"""
        text = next(text for text, category in _SCOPE_ESCALATION_INPUTS if category == "pattern-conformance")
        result = _run(
            {
                "tool_name": "AskUserQuestion",
                "tool_input": {
                    "questions": [
                        {
                            "question": "approach?",
                            "header": "header",
                            "options": [{"label": "ok", "description": text}],
                        }
                    ],
                },
            }
        )
        assert result.returncode == 0
        assert "scope-escalation phrase" not in result.stderr

    @pytest.mark.parametrize("category", ["context-shortage", "process-omission"])
    def test_option_label_other_categories_still_blocked(self, category: str):
        """選択肢labelではpattern-conformance以外のカテゴリを引き続きブロックする。"""
        text = next(text for text, fixture_category in _SCOPE_ESCALATION_INPUTS if fixture_category == category)
        result = _run(
            {
                "tool_name": "AskUserQuestion",
                "tool_input": {
                    "questions": [
                        {
                            "question": "approach?",
                            "header": "header",
                            "options": [{"label": text, "description": "ok"}],
                        }
                    ],
                },
            }
        )
        assert result.returncode == 2
        assert "scope-escalation phrase" in result.stderr
        assert category in result.stderr

    @pytest.mark.parametrize(
        "text",
        [
            "このゲームのターン数はいくつですか",
            "対話往復の標準的な手順を教えてください",
            "ターン制ストラテジーの設計について相談したい",
            "規範違反しないように気を付けます",
            "規範チェックの結果を共有します",
            "規模感を確認したい",
            "品質維持を継続する方針",
            "次サイクルの作業を計画する",
            "現行設計の見直しを検討する",
            "別の作業と混同しないよう注意する",
            "本タスクの詳細を検討する",
            "工数の見積もりを更新する",
            "セッションの内容を要約する",
        ],
    )
    def test_option_label_does_not_block_unrelated(self, text: str):
        """文脈無関係なフレーズでは縮退誘発フレーズ検出が誤発火しない。"""
        result = _run(
            {
                "tool_name": "AskUserQuestion",
                "tool_input": {
                    "questions": [
                        {
                            "question": "approach?",
                            "header": "header",
                            "options": [{"label": text, "description": "ok"}],
                        }
                    ],
                },
            }
        )
        assert result.returncode == 0
        assert "scope-escalation phrase" not in result.stderr

    def test_question_text_not_checked(self):
        """`question`本文はユーザーへの状況説明性質を持つため縮退フレーズ検査対象外とする。"""
        result = _run(
            {
                "tool_name": "AskUserQuestion",
                "tool_input": {
                    "questions": [
                        {
                            "question": "本セッションの残工程と両立させるため進め方を確認",
                            "header": "header",
                            "options": [{"label": "ok", "description": "ok"}],
                        }
                    ],
                },
            }
        )
        assert result.returncode == 0

    def test_header_not_checked(self):
        """`header`もユーザーへの状況説明性質を持つため縮退フレーズ検査対象外とする。"""
        result = _run(
            {
                "tool_name": "AskUserQuestion",
                "tool_input": {
                    "questions": [
                        {
                            "question": "ok?",
                            "header": "進め方を確認",
                            "options": [{"label": "ok", "description": "ok"}],
                        }
                    ],
                },
            }
        )
        assert result.returncode == 0

    def test_option_label_blocks(self):
        result = _run(
            {
                "tool_name": "AskUserQuestion",
                "tool_input": {
                    "questions": [
                        {
                            "question": "approach?",
                            "options": [
                                {"label": "優先順位について相談してから着手する", "description": "ask first"},
                                {"label": "ok", "description": "ok"},
                            ],
                        }
                    ],
                },
            }
        )
        assert result.returncode == 2
        assert "scope-escalation phrase" in result.stderr

    def test_option_description_blocks(self):
        result = _run(
            {
                "tool_name": "AskUserQuestion",
                "tool_input": {
                    "questions": [
                        {
                            "question": "approach?",
                            "options": [
                                {"label": "ok", "description": "進め方を確認"},
                            ],
                        }
                    ],
                },
            }
        )
        assert result.returncode == 2
        assert "scope-escalation phrase" in result.stderr

    def test_normal_question_allowed(self):
        result = _run(
            {
                "tool_name": "AskUserQuestion",
                "tool_input": {
                    "questions": [
                        {
                            "question": "どのライブラリを採用するか？",
                            "options": [
                                {"label": "ライブラリA", "description": "高速だが学習コストが高い"},
                                {"label": "ライブラリB", "description": "標準的"},
                            ],
                        }
                    ],
                },
            }
        )
        assert result.returncode == 0

    def test_empty_questions_allowed(self):
        result = _run({"tool_name": "AskUserQuestion", "tool_input": {"questions": []}})
        assert result.returncode == 0

    def test_empty_options_allowed(self):
        result = _run(
            {
                "tool_name": "AskUserQuestion",
                "tool_input": {
                    "questions": [{"question": "どうするか？", "options": []}],
                },
            }
        )
        assert result.returncode == 0

    def test_missing_questions_allowed(self):
        result = _run({"tool_name": "AskUserQuestion", "tool_input": {}})
        assert result.returncode == 0


def _build_doc_edit_tool_input(
    tool_name: str,
    file_path: pathlib.Path,
    new_content: str,
    old_string_param: str | None = None,
    prior_edits: tuple[dict[str, str], ...] = (),
) -> dict:
    """文書編集ツール別の入力形式を同一シナリオから組み立てる。"""
    if tool_name == "Write":
        return {"file_path": str(file_path), "content": new_content}
    if tool_name == "Edit":
        return {
            "file_path": str(file_path),
            "old_string": old_string_param or "",
            "new_string": new_content,
        }
    if tool_name == "MultiEdit":
        return {
            "file_path": str(file_path),
            "edits": [
                *prior_edits,
                {"old_string": old_string_param or "", "new_string": new_content},
            ],
        }
    raise AssertionError(f"未対応の文書編集ツール: {tool_name}")


def _prepare_doc_edit_scenario(
    tmp_path: pathlib.Path,
    tool_name: str,
    scenario: str,
    text: str,
) -> tuple[dict, bool]:
    """scope-escalation検出シナリオの既存内容とツール入力を準備する。"""
    target = tmp_path / "agent-toolkit" / "skills" / "x" / "SKILL.md"
    prior_edits: tuple[dict[str, str], ...] = ()
    if scenario == "target-doc":
        old_content = "a\nc\n"
        old_string = "c"
        new_content = f"c {text}"
        if tool_name == "Write":
            new_content = f"# header\n\n{text}\n"
        elif tool_name == "MultiEdit":
            prior_edits = ({"old_string": "a", "new_string": "b"},)
        expect_warning = True
    elif scenario == "unreadable-existing-file":
        old_content = None
        old_string = "" if tool_name == "MultiEdit" else "old"
        new_content = text
        expect_warning = True
    elif scenario == "preserved-existing-phrase":
        old_string = f"既存記述。{text}。末尾A"
        old_content = f"{old_string}\n"
        new_content = f"既存記述。{text}。末尾B"
        expect_warning = tool_name == "Write"
    elif scenario == "new-phrase-addition":
        old_string = "既存記述のみ"
        old_content = f"{old_string}\n"
        new_content = f"{old_string}。{text}"
        expect_warning = True
    else:
        raise AssertionError(f"未対応の文書編集シナリオ: {scenario}")

    if old_content is not None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(old_content, encoding="utf-8")
    return (
        _build_doc_edit_tool_input(
            tool_name,
            target,
            new_content,
            old_string_param=old_string,
            prior_edits=prior_edits,
        ),
        expect_warning,
    )


_DOC_EDIT_SCENARIOS = [
    *(pytest.param("target-doc", text, category, id=f"target-doc-{category}") for text, category in _SCOPE_ESCALATION_INPUTS),
    pytest.param(
        "unreadable-existing-file",
        _SCOPE_ESCALATION_INPUTS[0][0],
        _SCOPE_ESCALATION_INPUTS[0][1],
        id="unreadable-existing-file",
    ),
    *(
        pytest.param("preserved-existing-phrase", text, category, id=f"preserved-{category}")
        for text, category in _SCOPE_ESCALATION_INPUTS
    ),
    *(
        pytest.param("new-phrase-addition", text, category, id=f"addition-{category}")
        for text, category in _SCOPE_ESCALATION_INPUTS
    ),
]


class TestScopeEscalationInDocEditCheck:
    """対象ドキュメント編集時のscope-escalationフレーズ転記検出警告（block降格済み）。

    対象は`agent-toolkit/rules/`配下と`agent-toolkit/skills/**/SKILL.md`（`references/`配下を除く）。
    フレーズ本文は隔離フィクスチャから動的に読み込む。テストメソッド名の`blocks` /
    `detects_phrase`は検出時の警告出力を指し、いずれも`returncode`は0を維持する
    （完成条件を満たさない状態での次工程移行の抑止は`ExitPlanMode`・`plan-impl-executor`起動時の
    ブロックへ集約する）。
    """

    @pytest.mark.parametrize("tool_name", ["Write", "Edit", "MultiEdit"])
    @pytest.mark.parametrize(("scenario", "text", "category"), _DOC_EDIT_SCENARIOS)
    def test_doc_edit_scenarios(
        self,
        tmp_path: pathlib.Path,
        tool_name: str,
        scenario: str,
        text: str,
        category: str,
    ) -> None:
        """4種の文書編集シナリオをWrite、Edit、MultiEditで共通検証する。"""
        tool_input, expect_warning = _prepare_doc_edit_scenario(tmp_path, tool_name, scenario, text)
        result = _run({"tool_name": tool_name, "tool_input": tool_input})
        assert result.returncode == 0
        assert ("scope-escalation" in result.stderr) is expect_warning
        assert (category in result.stderr) is expect_warning
        assert ("matched:" in result.stderr) is expect_warning

    def test_multilevel_skill_target_blocks(self):
        """任意階層の`agent-toolkit/skills/**/SKILL.md`を対象に含む。"""
        text = _SCOPE_ESCALATION_INPUTS[0][0]
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": "agent-toolkit/skills/parent/child/SKILL.md",
                    "content": f"{text}\n",
                },
            }
        )
        assert result.returncode == 0

    def test_old_string_not_inspected_on_target(self, tmp_path: pathlib.Path):
        """対象ファイルでも`old_string`内のフレーズは検出しない（既存違反の修正を妨げない）。

        `new_string`にはクリーンな置換後文面を配置し、フレーズが`old_string`にのみあることで
        通過判定が`old_string`不検査に由来することを確認する。
        """
        text = _SCOPE_ESCALATION_INPUTS[0][0]
        clean_replacement = "通常の置換後文面"
        target = _write_tmp_file(tmp_path, "agent-toolkit/rules/01-agent.md", f"{text}\n")
        # 置換後文面にフレーズが残っていないことを確認（テスト前提の自己検査）
        for input_text, _ in _SCOPE_ESCALATION_INPUTS:
            assert input_text not in clean_replacement
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": str(target),
                    "old_string": text,
                    "new_string": clean_replacement,
                },
            }
        )
        assert result.returncode == 0

    @pytest.mark.parametrize(
        "file_path",
        [
            "agent-toolkit/agents/plan-impl-executor.md",
            "agent-toolkit/scripts/pretooluse.py",
            "agent-toolkit/skills/agent-standards/references/scope-escalation-phrases.md",
            "agent-toolkit/skills/agent-standards/references/_scope_escalation_test_inputs.txt",
            "agent-toolkit/skills/x/y/references/SKILL.md",
            "README.md",
            "src/app.py",
        ],
    )
    def test_non_target_doc_allows_phrase(self, file_path: str):
        """対象外ドキュメントでは同一フレーズも通過する。"""
        text = _SCOPE_ESCALATION_INPUTS[0][0]
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": file_path,
                    "content": f"{text}\n",
                },
            }
        )
        assert result.returncode == 0

    def test_clean_content_on_target_allowed(self):
        """対象ファイルでもフレーズを含まない内容は通過する。"""
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": "agent-toolkit/rules/01-agent.md",
                    "content": "# header\n\nplain content.\n",
                },
            }
        )
        assert result.returncode == 0

    def test_absolute_path_target_blocks(self):
        """絶対パス指定でも末尾マッチで対象判定される。"""
        text = _SCOPE_ESCALATION_INPUTS[0][0]
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": "/home/user/dotfiles/agent-toolkit/rules/01-agent.md",
                    "content": f"{text}\n",
                },
            }
        )
        assert result.returncode == 0

    def test_plan_file_target_blocks(self, tmp_path: pathlib.Path):
        """計画ファイル（`~/.claude/plans/*.md`）もscope-escalation転記検出の対象に含まれる。"""
        home = tmp_path / "home"
        plan = _make_plan_file(home)
        env = _plan_file_state_env(tmp_path, home)
        sid = "scope-esc-plan-file"
        text, category = _SCOPE_ESCALATION_INPUTS[0]
        content = _VALID_H2_PLAN_CONTENT.replace("## 対応方針\n\nx\n", f"## 対応方針\n\n{text}\n", 1)
        _write_session_state(
            tmp_path,
            sid,
            {
                "plan_mode_skill_invoked": True,
                "textlint_violations_read": True,
            },
        )
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        assert "scope-escalation" in result.stderr
        assert category in result.stderr

    def test_mitigation_in_adoption_references_process_feedbacks(self):
        """`mitigation-in-adoption`カテゴリはprocess-feedbacks配下review-checklists.mdの項を参照する。"""
        text = next(t for t, c in _SCOPE_ESCALATION_INPUTS if c == "mitigation-in-adoption")
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": "agent-toolkit/rules/01-agent.md",
                    "content": f"# header\n\n{text}\n",
                },
            }
        )
        assert result.returncode == 0
        assert "review-checklists.md" in result.stderr
        assert "採用時の反映内容の縮小禁止" in result.stderr

    def test_other_category_references_01_agent_md(self):
        """`mitigation-in-adoption`以外のカテゴリは01-agent.mdの節を参照する。"""
        text = next(t for t, c in _SCOPE_ESCALATION_INPUTS if c == "process-omission")
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": "agent-toolkit/rules/01-agent.md",
                    "content": f"# header\n\n{text}\n",
                },
            }
        )
        assert result.returncode == 0
        assert "agent-toolkit/rules/01-agent.md" in result.stderr
        assert "01-01-agent.md" not in result.stderr
        assert "完遂と先送り" in result.stderr


class TestScopeEscalationPlanFileFenceExclusion:
    """fb-7: 計画ファイル対象時に`text`コードフェンス内のfixture例語彙を走査対象から除外する。

    規範文書本体（`agent-toolkit/rules/`配下等）はフェンス除外を適用せず、既存の検出精度を維持する。
    """

    def test_priority_consult_in_text_fence_of_plan_file_not_detected(self, tmp_path: pathlib.Path):
        """計画ファイルの`text`フェンス内フレーズは通過する（Write経路）。"""
        home = tmp_path / "home"
        plan = _make_plan_file(home)
        env = _plan_file_state_env(tmp_path, home)
        sid = "scope-esc-plan-fence-write"
        text, _category = _SCOPE_ESCALATION_INPUTS[0]
        content = _VALID_H2_PLAN_CONTENT.replace(
            "## 対応方針\n\nx\n",
            f"## 対応方針\n\n```text\n{text}\n```\n",
            1,
        )
        _write_session_state(
            tmp_path,
            sid,
            {
                "plan_mode_skill_invoked": True,
                "textlint_violations_read": True,
            },
        )
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 0

    def test_priority_consult_in_text_fence_of_plan_file_edit_not_detected(self, tmp_path: pathlib.Path):
        """計画ファイルの`text`フェンス内へのnew_string追加も通過する（Edit経路）。"""
        home = tmp_path / "home"
        plan = _make_plan_file(home)
        env = _plan_file_state_env(tmp_path, home)
        text, _category = _SCOPE_ESCALATION_INPUTS[0]
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": str(plan),
                    "old_string": "# t",
                    "new_string": f"# t\n\n```text\n{text}\n```\n",
                },
            },
            env_overrides=env,
        )
        assert result.returncode == 0

    def test_fence_inner_only_plan_file_edit_not_detected(self, tmp_path: pathlib.Path):
        """フェンス境界を含まないEditでも、全文上の`text`フェンス内なら通過する。"""
        home = tmp_path / "home"
        plan = _make_plan_file(home)
        env = _plan_file_state_env(tmp_path, home)
        text, _category = _SCOPE_ESCALATION_INPUTS[0]
        before = "フェンス内の既存文言"
        after = f"フェンス内の既存文言。{text}"
        plan.write_text(f"# t\n\n```text\n{before}\n```\n", encoding="utf-8")
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": str(plan),
                    "old_string": before,
                    "new_string": after,
                },
            },
            env_overrides=env,
        )
        assert result.returncode == 0

    def test_fence_inner_only_plan_file_multiedit_not_detected(self, tmp_path: pathlib.Path):
        """フェンス境界を含まないMultiEditでも、全文上の`text`フェンス内なら通過する。"""
        home = tmp_path / "home"
        plan = _make_plan_file(home)
        env = _plan_file_state_env(tmp_path, home)
        text, _category = _SCOPE_ESCALATION_INPUTS[0]
        before = "フェンス内の既存文言"
        after = f"フェンス内の既存文言。{text}"
        plan.write_text(f"# t\n\n```text\n{before}\n```\n", encoding="utf-8")
        result = _run(
            {
                "tool_name": "MultiEdit",
                "tool_input": {
                    "file_path": str(plan),
                    "edits": [{"old_string": before, "new_string": after}],
                },
            },
            env_overrides=env,
        )
        assert result.returncode == 0

    def test_priority_consult_in_norm_doc_still_detected(self):
        """計画ファイル以外（規範文書本体）はフェンス内でも従来どおり検出する（警告、block降格済み）。"""
        text, category = _SCOPE_ESCALATION_INPUTS[0]
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": "agent-toolkit/rules/01-agent.md",
                    "content": f"# header\n\n```text\n{text}\n```\n",
                },
            }
        )
        assert result.returncode == 0
        assert category in result.stderr


class TestMatchScopeEscalationIncreaseBracketExclusion:
    """`_match_scope_escalation_increase`の括弧内除外の共有動作を検証する。

    `_scope_escalation._apply_category_exclusions`を経由してnew・old双方へ除外を適用する。
    fixtureは`_scope_escalation_test.py`の`test_priority_consult_phrase_*`3件と同一文言。
    """

    def test_bracket_quoted_priority_consult_not_blocked(self, tmp_path: pathlib.Path):
        """全角鍵括弧内のpriority-consult語彙は増分検出でもブロックしない。"""
        old = "既存記述のみ。"
        new = "既存記述のみ。計画ファイル本文の「スコープ相談節」を確認する。"
        target = _write_tmp_file(tmp_path, "agent-toolkit/skills/agent-standards/SKILL.md", f"{old}\n")
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": str(target),
                    "old_string": old,
                    "new_string": new,
                },
            }
        )
        assert result.returncode == 0

    def test_outside_bracket_priority_consult_blocked(self, tmp_path: pathlib.Path):
        """全角鍵括弧の外側のpriority-consult語彙は増分検出で警告する（block降格済み）。"""
        old = "既存記述のみ。"
        new = "既存記述のみ。優先順位について相談してから着手する。"
        target = _write_tmp_file(tmp_path, "agent-toolkit/skills/agent-standards/SKILL.md", f"{old}\n")
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": str(target),
                    "old_string": old,
                    "new_string": new,
                },
            }
        )
        assert result.returncode == 0
        assert "priority-consult" in result.stderr

    def test_bracket_and_outside_mixed_blocked_by_outside(self, tmp_path: pathlib.Path):
        """内側と外側の両方にある場合、外側の増分で警告する（block降格済み）。"""
        old = "既存記述のみ。"
        new = "既存記述のみ。計画ファイル本文の「スコープ相談節」を参照しつつ、優先順位について相談してから着手する。"
        target = _write_tmp_file(tmp_path, "agent-toolkit/skills/agent-standards/SKILL.md", f"{old}\n")
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": str(target),
                    "old_string": old,
                    "new_string": new,
                },
            }
        )
        assert result.returncode == 0
        assert "priority-consult" in result.stderr


class TestFabricatedMetricsScopeEscalation:
    """`fabricated-metrics`カテゴリ（実測値取得手段が無い数値主張）の検出（FB7）。

    フレーズ本文は隔離フィクスチャ（`_SCOPE_ESCALATION_INPUTS`）経由の既存カテゴリ横断テストで
    網羅済みのため、本クラスは分岐追加時に固有の境界値・誤検出回避・警告文言を追加検証する。
    """

    @pytest.mark.parametrize(
        "text",
        [
            "5分経過",
            "10分相当",
        ],
    )
    def test_option_label_blocks(self, text: str):
        result = _run(
            {
                "tool_name": "AskUserQuestion",
                "tool_input": {
                    "questions": [
                        {
                            "question": "approach?",
                            "header": "header",
                            "options": [{"label": text, "description": "ok"}],
                        }
                    ],
                },
            }
        )
        assert result.returncode == 2
        assert "fabricated-metrics" in result.stderr

    def test_warning_message_includes_alternative(self):
        """警告文言に代替表現例が併記される。"""
        result = _run(
            {
                "tool_name": "AskUserQuestion",
                "tool_input": {
                    "questions": [
                        {
                            "question": "approach?",
                            "header": "header",
                            "options": [{"label": "約80%消費した", "description": "ok"}],
                        }
                    ],
                },
            }
        )
        assert result.returncode == 2
        assert "定性的な進捗記述" in result.stderr

    @pytest.mark.parametrize(
        "text",
        [
            "満足度は80%程度",
            "残りタスクは1000件",
            "3時間後にミーティングがある",
            "5分後に開始する",
            "会議は3時間の予定",
        ],
    )
    def test_option_label_does_not_block_unrelated(self, text: str):
        """数値・単位を含むが実測値主張の文脈を伴わない文面は誤検出しない。"""
        result = _run(
            {
                "tool_name": "AskUserQuestion",
                "tool_input": {
                    "questions": [
                        {
                            "question": "approach?",
                            "header": "header",
                            "options": [{"label": text, "description": "ok"}],
                        }
                    ],
                },
            }
        )
        assert result.returncode == 0
        assert "fabricated-metrics" not in result.stderr


_PROCESS7_FLAGS = ("plan_review_completed",)


def _process7_env(tmp_path: pathlib.Path) -> dict[str, str]:
    return {"TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path)}


class TestProcess7CompletionCheck:
    """ExitPlanMode / `plan-impl-executor`起動時のplan-file-finalizerの整合性チェック完了未達ブロック。"""

    @pytest.mark.parametrize(
        ("missing_flags", "expect_block"),
        [
            pytest.param(frozenset(), False, id="review-completed"),
            pytest.param(frozenset({"plan_review_completed"}), True, id="review-incomplete"),
        ],
    )
    def test_flag_combination_matrix(
        self,
        tmp_path: pathlib.Path,
        missing_flags: frozenset[str],
        expect_block: bool,
    ) -> None:
        """計画レビュー完了フラグの真偽でゲートが分岐することを検証する。"""
        sid = "process7-flags-" + ("-".join(sorted(missing_flags)) or "all-set")
        state = {"plan_mode_skill_invoked": True}
        state.update({flag: flag not in missing_flags for flag in _PROCESS7_FLAGS})
        _write_session_state(tmp_path, sid, state)
        result = _run(
            {"tool_name": "ExitPlanMode", "tool_input": {}, "session_id": sid, "permission_mode": "plan"},
            env_overrides=_process7_env(tmp_path),
        )
        assert result.returncode == (2 if expect_block else 0)
        if expect_block:
            for missing_flag in missing_flags & frozenset(_PROCESS7_FLAGS):
                assert missing_flag in result.stderr
            assert "plan-file-finalizer.md" in result.stderr

    def test_missing_flag_message_explains_gate_and_bypass(self, tmp_path: pathlib.Path):
        """ブロックメッセージが理由・plan-impl feedback処理時の対処・pre-existing planバイパス条件を説明する。"""
        sid = "process7-message-explains"
        state = {"plan_mode_skill_invoked": True}
        state.update({flag: False for flag in _PROCESS7_FLAGS})
        _write_session_state(tmp_path, sid, state)
        result = _run(
            {"tool_name": "ExitPlanMode", "tool_input": {}, "session_id": sid, "permission_mode": "plan"},
            env_overrides=_process7_env(tmp_path),
        )
        assert result.returncode == 2
        assert "plan-impl-feedback-flow.md" in result.stderr
        assert "混在時の並行制御" in result.stderr
        assert "current_plan_file_path" in result.stderr

    def test_no_plan_mode_context_passes(self, tmp_path: pathlib.Path):
        """`plan_mode_skill_invoked`が偽の場合は検査対象外として通過する。"""
        sid = "process7-no-plan-mode"
        _write_session_state(tmp_path, sid, {})
        result = _run(
            {"tool_name": "ExitPlanMode", "tool_input": {}, "session_id": sid, "permission_mode": "plan"},
            env_overrides=_process7_env(tmp_path),
        )
        assert result.returncode == 0

    def test_plan_impl_executor_agent_also_checked(self, tmp_path: pathlib.Path):
        """`plan-impl-executor`のAgent起動も同様にplan-file-finalizerの整合性チェック完了未達をブロックする。"""
        sid = "process7-plan-impl-executor-agent"
        state = {"plan_mode_skill_invoked": True}
        state.update({flag: False for flag in _PROCESS7_FLAGS})
        _write_session_state(tmp_path, sid, state)
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "agent-toolkit:plan-impl-executor"},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=_process7_env(tmp_path),
        )
        assert result.returncode == 2

    def test_agent_referenced_matching_current_plan_blocks(self, tmp_path: pathlib.Path):
        """起動プロンプトが現行計画パスと一致する場合はplan-file-finalizerの整合性チェック完了未達をブロックする。"""
        plan_path = str(tmp_path / ".claude" / "plans" / "current.md")
        sid = "process7-agent-path-match"
        state = {"plan_mode_skill_invoked": True, "current_plan_file_path": plan_path}
        state.update({flag: False for flag in _PROCESS7_FLAGS})
        _write_session_state(tmp_path, sid, state)
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {
                    "subagent_type": "agent-toolkit:plan-impl-executor",
                    "prompt": f"計画ファイル: {plan_path} を実装してください。",
                },
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=_process7_env(tmp_path),
        )
        assert result.returncode == 2

    def test_agent_referenced_other_existing_plan_passes(self, tmp_path: pathlib.Path):
        """起動プロンプトが現行計画と異なる実在パスを指す場合はブロックしない。"""
        current_path = str(tmp_path / ".claude" / "plans" / "current.md")
        other_path = tmp_path / ".claude" / "plans" / "other-reviewed.md"
        other_path.parent.mkdir(parents=True, exist_ok=True)
        other_path.write_text("計画実装型フィードバック投入元セッションで完遂済みの計画。", encoding="utf-8")
        sid = "process7-agent-path-mismatch"
        state = {"plan_mode_skill_invoked": True, "current_plan_file_path": current_path}
        state.update({flag: False for flag in _PROCESS7_FLAGS})
        _write_session_state(tmp_path, sid, state)
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {
                    "subagent_type": "agent-toolkit:plan-impl-executor",
                    "prompt": f"計画ファイル: {other_path} を実装してください。",
                },
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=_process7_env(tmp_path),
        )
        assert result.returncode == 0

    def test_agent_referenced_nonexistent_other_plan_blocks(self, tmp_path: pathlib.Path):
        """起動プロンプトが現行計画と異なる非実在パスを指す場合は従来どおりブロックする。

        実在確認が無ければ、任意の非実在パスを記述するだけでplan-file-finalizerの整合性チェック未達を回避できてしまうため、
        実在しないパスへの参照はブロック（returncode 2）を維持することを確認する。
        """
        current_path = str(tmp_path / ".claude" / "plans" / "current.md")
        other_path = str(tmp_path / ".claude" / "plans" / "other-nonexistent.md")
        sid = "process7-agent-path-mismatch-nonexistent"
        state = {"plan_mode_skill_invoked": True, "current_plan_file_path": current_path}
        state.update({flag: False for flag in _PROCESS7_FLAGS})
        _write_session_state(tmp_path, sid, state)
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {
                    "subagent_type": "agent-toolkit:plan-impl-executor",
                    "prompt": f"計画ファイル: {other_path} を実装してください。",
                },
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=_process7_env(tmp_path),
        )
        assert result.returncode == 2

    def test_agent_backtick_path_with_spaces_matches(self, tmp_path: pathlib.Path):
        """空白を含むバッククォート囲みパスも丸ごと抽出して一致判定する。"""
        plan_path = str(tmp_path / "User Name" / ".claude" / "plans" / "current.md")
        sid = "process7-agent-path-space-match"
        state = {"plan_mode_skill_invoked": True, "current_plan_file_path": plan_path}
        state.update({flag: False for flag in _PROCESS7_FLAGS})
        _write_session_state(tmp_path, sid, state)
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {
                    "subagent_type": "agent-toolkit:plan-impl-executor",
                    "prompt": f"計画ファイル: `{plan_path}` を実装してください。",
                },
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=_process7_env(tmp_path),
        )
        assert result.returncode == 2

    def test_agent_path_normalization_failure_blocks(self, tmp_path: pathlib.Path):
        """パス正規化に失敗した場合（未解決ユーザー表記等）は従来判定でブロックする。

        現行計画パスには正規化可能な別パスを設定する。正規化失敗のフォールバックが
        無ければ両パスは不一致となり通過（returncode 0）するため、
        ブロック（returncode 2）はフォールバック経路の通過を裏付ける。
        """
        referenced_path = "~nonexistent-user-for-test/.claude/plans/current.md"
        current_path = str(tmp_path / ".claude" / "plans" / "other.md")
        sid = "process7-agent-path-normalize-fail"
        state = {"plan_mode_skill_invoked": True, "current_plan_file_path": current_path}
        state.update({flag: False for flag in _PROCESS7_FLAGS})
        _write_session_state(tmp_path, sid, state)
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {
                    "subagent_type": "agent-toolkit:plan-impl-executor",
                    "prompt": f"計画ファイル: `{referenced_path}` を実装してください。",
                },
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=_process7_env(tmp_path),
        )
        assert result.returncode == 2

    def test_agent_prompt_without_plan_path_blocks(self, tmp_path: pathlib.Path):
        """起動プロンプトから計画ファイルパスを抽出できない場合は安全側でブロックする。"""
        sid = "process7-agent-path-unextractable"
        state = {
            "plan_mode_skill_invoked": True,
            "current_plan_file_path": str(tmp_path / ".claude" / "plans" / "current.md"),
        }
        state.update({flag: False for flag in _PROCESS7_FLAGS})
        _write_session_state(tmp_path, sid, state)
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {
                    "subagent_type": "agent-toolkit:plan-impl-executor",
                    "prompt": "計画に従い実装してください。",
                },
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=_process7_env(tmp_path),
        )
        assert result.returncode == 2

    def test_referenced_other_existing_plan_records_verified_path(self, tmp_path: pathlib.Path):
        """別の実在計画への切替を許可した経路で`plan_impl_executor_verified_plan_path`へ当該パスを記録する。"""
        current_path = str(tmp_path / ".claude" / "plans" / "current.md")
        other_path = tmp_path / ".claude" / "plans" / "other-reviewed.md"
        other_path.parent.mkdir(parents=True, exist_ok=True)
        other_path.write_text("別セッションで完遂済みの計画。", encoding="utf-8")
        sid = "process7-verified-path-record"
        state = {"plan_mode_skill_invoked": True, "current_plan_file_path": current_path}
        state.update({flag: False for flag in _PROCESS7_FLAGS})
        _write_session_state(tmp_path, sid, state)
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {
                    "subagent_type": "agent-toolkit:plan-impl-executor",
                    "prompt": f"計画ファイル: {other_path} を実装してください。",
                },
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=_process7_env(tmp_path),
        )
        assert result.returncode == 0
        recorded = _read_session_state(tmp_path, sid)
        assert recorded.get("plan_impl_executor_verified_plan_path") == str(other_path.resolve())
        assert recorded.get("current_plan_file_path") == current_path

    def test_referenced_same_plan_does_not_record_verified_path(self, tmp_path: pathlib.Path):
        """現行計画と同一パスを参照した通過経路では`plan_impl_executor_verified_plan_path`を記録しない。"""
        plan_path = tmp_path / ".claude" / "plans" / "current.md"
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text("本セッションで起草した計画。", encoding="utf-8")
        sid = "process7-verified-path-same-plan"
        state = {"plan_mode_skill_invoked": True, "current_plan_file_path": str(plan_path)}
        state.update({flag: True for flag in _PROCESS7_FLAGS})
        _write_session_state(tmp_path, sid, state)
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {
                    "subagent_type": "agent-toolkit:plan-impl-executor",
                    "prompt": f"計画ファイル: {plan_path} を実装してください。",
                },
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=_process7_env(tmp_path),
        )
        assert result.returncode == 0
        assert _read_session_state(tmp_path, sid).get("plan_impl_executor_verified_plan_path") is None

    def test_referenced_nonexistent_other_plan_does_not_record_verified_path(self, tmp_path: pathlib.Path):
        """非実在パスを参照したブロック経路では`plan_impl_executor_verified_plan_path`を記録しない。"""
        current_path = str(tmp_path / ".claude" / "plans" / "current.md")
        other_path = str(tmp_path / ".claude" / "plans" / "other-nonexistent.md")
        sid = "process7-verified-path-nonexistent"
        state = {"plan_mode_skill_invoked": True, "current_plan_file_path": current_path}
        state.update({flag: False for flag in _PROCESS7_FLAGS})
        _write_session_state(tmp_path, sid, state)
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {
                    "subagent_type": "agent-toolkit:plan-impl-executor",
                    "prompt": f"計画ファイル: {other_path} を実装してください。",
                },
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=_process7_env(tmp_path),
        )
        assert result.returncode == 2
        assert _read_session_state(tmp_path, sid).get("plan_impl_executor_verified_plan_path") is None

    def test_unset_current_plan_path_with_existing_referenced_plan_passes(self, tmp_path: pathlib.Path) -> None:
        """`current_plan_file_path`が未記録でも、参照先が実在する計画ならブロックしない。"""
        other_path = tmp_path / ".claude" / "plans" / "other-reviewed.md"
        other_path.parent.mkdir(parents=True, exist_ok=True)
        other_path.write_text("別セッションで完遂済みの計画。", encoding="utf-8")
        sid = "process7-unset-current-existing-referenced"
        state = {"plan_mode_skill_invoked": True}
        state.update({flag: False for flag in _PROCESS7_FLAGS})
        _write_session_state(tmp_path, sid, state)
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {
                    "subagent_type": "agent-toolkit:plan-impl-executor",
                    "prompt": f"計画ファイル: {other_path} を実装してください。",
                },
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=_process7_env(tmp_path),
        )
        assert result.returncode == 0

    def test_unset_current_plan_path_with_nonexistent_referenced_plan_blocks(self, tmp_path: pathlib.Path) -> None:
        """`current_plan_file_path`が未記録で、参照先が非実在の場合は従来どおりブロックする。"""
        other_path = str(tmp_path / ".claude" / "plans" / "other-nonexistent.md")
        sid = "process7-unset-current-nonexistent-referenced"
        state = {"plan_mode_skill_invoked": True}
        state.update({flag: False for flag in _PROCESS7_FLAGS})
        _write_session_state(tmp_path, sid, state)
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {
                    "subagent_type": "agent-toolkit:plan-impl-executor",
                    "prompt": f"計画ファイル: {other_path} を実装してください。",
                },
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=_process7_env(tmp_path),
        )
        assert result.returncode == 2


class TestAgentNameParameterGate:
    """AgentとTask起動時の`name`引数指定の一律ブロック。"""

    @pytest.mark.parametrize("tool_name", ["Agent", "Task"])
    @pytest.mark.parametrize("name_value", ["impl-1", "", None])
    def test_name_parameter_blocks(self, tmp_path: pathlib.Path, tool_name: str, name_value: str | None) -> None:
        """`name`キーが存在する起動は値の内容によらずブロックする。"""
        sid = f"agent-name-block-{tool_name.lower()}-{name_value!r}"
        result = _run(
            {
                "tool_name": tool_name,
                "tool_input": {"subagent_type": "claude", "name": name_value, "prompt": "調査してください。"},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=_process7_env(tmp_path),
        )
        assert result.returncode == 2
        assert "`name` parameter is not allowed" in result.stderr

    @pytest.mark.parametrize("tool_name", ["Agent", "Task"])
    def test_launch_without_name_passes(self, tmp_path: pathlib.Path, tool_name: str) -> None:
        """`name`キーを持たない起動は通過する。"""
        sid = f"agent-name-allow-{tool_name.lower()}"
        result = _run(
            {
                "tool_name": tool_name,
                "tool_input": {"subagent_type": "claude", "prompt": "調査してください。"},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=_process7_env(tmp_path),
        )
        assert result.returncode == 0
        assert "`name` parameter is not allowed" not in result.stderr

    def test_name_block_explains_execution_result_delivery_route(self, tmp_path: pathlib.Path) -> None:
        """ブロック理由は起動形態を断定せず、実行結果から受領経路を判定するよう案内する。"""
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "claude", "name": "named", "prompt": "調査してください。"},
                "session_id": "agent-name-block-route",
                "permission_mode": "default",
            },
            env_overrides=_process7_env(tmp_path),
        )
        assert result.returncode == 2
        assert "execution result" in result.stderr
        assert "launch in the foreground" not in result.stderr
        assert "tool return value" not in result.stderr

    def test_name_block_precedes_subagent_type_flag_record(self, tmp_path: pathlib.Path) -> None:
        """ブロックされた起動では`subagent_type`別フラグを記録しない（起動しない呼び出しの副作用を残さない）。"""
        sid = "agent-name-block-no-flag"
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "agent-toolkit:plan-file-finalizer", "name": "codex-1"},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=_process7_env(tmp_path),
        )
        assert result.returncode == 2
        state_path = tmp_path / f"claude-agent-toolkit-{sid}.json"
        assert not state_path.exists() or _read_session_state(tmp_path, sid).get("plan_review_completed") is not True


# `TestSubagentModelOverrideGate`用の完全な起動プロンプト。モデル検査は見出し検査より先に
# 評価されるため、参照パスが実在しなくても見出し欠落ゲートの誤検出と混同しない
# （モデル検査が真の場合は`_check_plan_file_finalizer_prompt_completeness`へ到達する前にreturnする）。
_COMPLETE_PLAN_FILE_FINALIZER_PROMPT_FOR_MODEL_GATE = (
    "## 計画ファイルパス\n`~/.claude/plans/example.md`\n\n## permission_mode\n非`plan`\n\n## 作業ディレクトリ\n`/repo`\n"
)


def _complete_plan_file_finalizer_prompt(plan_path: pathlib.Path) -> str:
    """`TestPlanFileFinalizerPromptCompletenessGate`用の、実在する計画ファイルを参照する完全な起動プロンプト。"""
    return f"## 計画ファイルパス\n`{plan_path}`\n\n## permission_mode\n非`plan`\n\n## 作業ディレクトリ\n`/repo`\n"


class TestSubagentModelOverrideGate:
    """`plan-file-finalizer`・`plan-impl-executor`への`model`引数指定の一律ブロック。"""

    def test_plan_file_finalizer_with_opus_blocked(self):
        """完全な起動プロンプトを使い、見出し欠落ゲートではなくモデル検査自体の発火を検証する。"""
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {
                    "subagent_type": "agent-toolkit:plan-file-finalizer",
                    "model": "opus",
                    "prompt": _COMPLETE_PLAN_FILE_FINALIZER_PROMPT_FOR_MODEL_GATE,
                },
                "session_id": "model-override-plan-file-finalizer",
                "permission_mode": "default",
            }
        )
        assert result.returncode == 2
        assert "explicit `model` argument" in result.stderr
        assert "plan-file-finalizer" in result.stderr

    def test_plan_impl_executor_with_model_blocked_short_form(self):
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "plan-impl-executor", "model": "haiku", "prompt": "x"},
                "session_id": "model-override-plan-impl-executor",
                "permission_mode": "default",
            }
        )
        assert result.returncode == 2

    def test_no_model_argument_passes(self):
        """`plan-impl-executor`を使い、同時導入の`TestPlanFileFinalizerPromptCompletenessGate`と非干渉にする。"""
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "agent-toolkit:plan-impl-executor", "prompt": "x"},
                "session_id": "model-override-none",
                "permission_mode": "default",
            }
        )
        assert result.returncode == 0


class TestPlanFileFinalizerPromptCompletenessGate:
    """`plan-file-finalizer`起動プロンプトの必須見出し3点の実在・非空検査と計画ファイル実在検査。"""

    def test_complete_prompt_passes(self, tmp_path: pathlib.Path):
        """観点(a): 必須見出し3点（作業ディレクトリを含む）のプロンプトが通過する。"""
        plan_path = tmp_path / "example.md"
        plan_path.write_text("# 例\n", encoding="utf-8")
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {
                    "subagent_type": "agent-toolkit:plan-file-finalizer",
                    "prompt": _complete_plan_file_finalizer_prompt(plan_path),
                },
                "session_id": "prompt-completeness-ok",
                "permission_mode": "default",
            }
        )
        assert result.returncode == 0

    def test_omitting_legacy_headings_still_passes(self, tmp_path: pathlib.Path):
        """観点(b): 旧必須見出しだった`## 合意済み事項`・`## 照合結果`が無くてもブロックされない。"""
        plan_path = tmp_path / "example.md"
        plan_path.write_text("# 例\n", encoding="utf-8")
        prompt = _complete_plan_file_finalizer_prompt(plan_path)
        assert "合意済み事項" not in prompt
        assert "照合結果" not in prompt
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "agent-toolkit:plan-file-finalizer", "prompt": prompt},
                "session_id": "prompt-completeness-no-legacy-headings",
                "permission_mode": "default",
            }
        )
        assert result.returncode == 0

    def test_nonexistent_plan_file_path_blocked(self, tmp_path: pathlib.Path):
        """観点(c): `## 計画ファイルパス`が指す計画ファイルが実在しない場合にブロックされる。"""
        missing_path = tmp_path / "does-not-exist.md"
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {
                    "subagent_type": "agent-toolkit:plan-file-finalizer",
                    "prompt": _complete_plan_file_finalizer_prompt(missing_path),
                },
                "session_id": "prompt-completeness-missing-plan-file",
                "permission_mode": "default",
            }
        )
        assert result.returncode == 2
        assert "does not resolve to an existing file" in result.stderr

    def test_ambiguous_plan_file_path_not_blocked_by_existence_check(self, tmp_path: pathlib.Path):
        """観点(d): パスを一意に抽出できない場合、実在検査ではブロックされない。"""
        prompt = (
            f"## 計画ファイルパス\n`{tmp_path / 'a.md'}`と`{tmp_path / 'b.md'}`のいずれか\n\n"
            "## permission_mode\n非`plan`\n\n"
            "## 作業ディレクトリ\n`/repo`\n"
        )
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "agent-toolkit:plan-file-finalizer", "prompt": prompt},
                "session_id": "prompt-completeness-ambiguous-path",
                "permission_mode": "default",
            }
        )
        assert result.returncode == 0

    def test_normalization_failure_blocked(self):
        """観点(c'): `~`展開に失敗するパス（未解決ユーザー表記）は正規化失敗として厳格にブロックされる。"""
        prompt = (
            "## 計画ファイルパス\n`~nonexistentuser12345/plan.md`\n\n"
            "## permission_mode\n非`plan`\n\n"
            "## 作業ディレクトリ\n`/repo`\n"
        )
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "agent-toolkit:plan-file-finalizer", "prompt": prompt},
                "session_id": "prompt-completeness-normalization-failure",
                "permission_mode": "default",
            }
        )
        assert result.returncode == 2
        assert "does not resolve to an existing file" in result.stderr

    def test_backtick_less_existing_path_passes(self, tmp_path: pathlib.Path):
        """観点(a'): バッククォート無しの裸パス表記でも実在パスなら通過する。"""
        plan_path = tmp_path / "example.md"
        plan_path.write_text("# 例\n", encoding="utf-8")
        prompt = f"## 計画ファイルパス\n{plan_path}\n\n## permission_mode\n非`plan`\n\n## 作業ディレクトリ\n`/repo`\n"
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "agent-toolkit:plan-file-finalizer", "prompt": prompt},
                "session_id": "prompt-completeness-bare-path",
                "permission_mode": "default",
            }
        )
        assert result.returncode == 0

    def test_backtick_path_takes_priority_over_coexisting_bare_path(self, tmp_path: pathlib.Path):
        """観点(a''): バッククォート表記と裸パスが併存する場合、バッククォート表記が優先され一意に抽出される。"""
        plan_path = tmp_path / "example.md"
        plan_path.write_text("# 例\n", encoding="utf-8")
        bare_missing_path = tmp_path / "does-not-exist-bare.md"
        prompt = (
            f"## 計画ファイルパス\n`{plan_path}`（参考: {bare_missing_path} は無関係な裸パス表記）\n\n"
            "## permission_mode\n非`plan`\n\n"
            "## 作業ディレクトリ\n`/repo`\n"
        )
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "agent-toolkit:plan-file-finalizer", "prompt": prompt},
                "session_id": "prompt-completeness-backtick-priority",
                "permission_mode": "default",
            }
        )
        assert result.returncode == 0

    @pytest.mark.parametrize("indent", [0, 1, 2, 3])
    def test_heading_with_up_to_three_leading_spaces_still_counted(self, tmp_path: pathlib.Path, indent: int):
        """CommonMarkのATX見出し仕様上、先頭スペース0〜3個までは見出しとして有効である境界値を検証する。

        4個以上との区別（インデントコードブロック扱い）は`test_heading_inside_indented_code_block_not_counted`が
        別途検証する。0〜3個をパラメーター化して個別に検証することで、1個・2個を拒否する実装への
        後退を検出できるようにする（3個のみの検証では検出できない）。
        """
        plan_path = tmp_path / "example.md"
        plan_path.write_text("# 例\n", encoding="utf-8")
        pad = " " * indent
        complete_prompt = _complete_plan_file_finalizer_prompt(plan_path)
        prompt = "\n".join(f"{pad}{line}" if line else "" for line in complete_prompt.splitlines())
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "agent-toolkit:plan-file-finalizer", "prompt": prompt},
                "session_id": f"prompt-completeness-{indent}-space-heading",
                "permission_mode": "default",
            }
        )
        assert result.returncode == 0

    @pytest.mark.parametrize("omit_heading", ["計画ファイルパス", "permission_mode", "作業ディレクトリ"])
    def test_missing_heading_blocked(self, tmp_path: pathlib.Path, omit_heading: str):
        """新必須見出し3点を1件ずつ欠落させ、各欠落単独でブロックされることを検証する。"""
        plan_path = tmp_path / "example.md"
        plan_path.write_text("# 例\n", encoding="utf-8")
        headings = {
            "計画ファイルパス": f"## 計画ファイルパス\n`{plan_path}`",
            "permission_mode": "## permission_mode\n非`plan`",
            "作業ディレクトリ": "## 作業ディレクトリ\n`/repo`",
        }
        del headings[omit_heading]
        prompt = "\n\n".join(headings.values()) + "\n"
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "plan-file-finalizer", "prompt": prompt},
                "session_id": f"prompt-completeness-missing-{omit_heading}",
                "permission_mode": "default",
            }
        )
        assert result.returncode == 2
        assert omit_heading in result.stderr

    @pytest.mark.parametrize("empty_heading", ["計画ファイルパス", "permission_mode", "作業ディレクトリ"])
    def test_empty_section_blocked(self, tmp_path: pathlib.Path, empty_heading: str):
        """新必須見出し3点を1件ずつ空にし、各空欄単独でブロックされることを検証する。"""
        plan_path = tmp_path / "example.md"
        plan_path.write_text("# 例\n", encoding="utf-8")
        headings = {
            "計画ファイルパス": f"## 計画ファイルパス\n`{plan_path}`",
            "permission_mode": "## permission_mode\n非`plan`",
            "作業ディレクトリ": "## 作業ディレクトリ\n`/repo`",
        }
        headings[empty_heading] = f"## {empty_heading}\n"
        prompt = "\n\n".join(headings.values()) + "\n"
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "agent-toolkit:plan-file-finalizer", "prompt": prompt},
                "session_id": f"prompt-completeness-empty-{empty_heading}",
                "permission_mode": "default",
            }
        )
        assert result.returncode == 2
        assert empty_heading in result.stderr

    def test_other_subagent_not_checked(self):
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "agent-toolkit:plan-impl-executor", "prompt": "計画を実装して。"},
                "session_id": "prompt-completeness-other",
                "permission_mode": "default",
            }
        )
        assert result.returncode == 0

    def test_heading_inside_tilde_fence_not_counted(self, tmp_path: pathlib.Path):
        """チルダフェンス（`~~~`）内の見出し例示も、バッククォートフェンスと同様に実見出しと誤認しない。"""
        plan_path = tmp_path / "example.md"
        plan_path.write_text("# 例\n", encoding="utf-8")
        complete_prompt = _complete_plan_file_finalizer_prompt(plan_path)
        prompt = "~~~\n" + complete_prompt + "~~~\n本文のみで実見出しは無い。"
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "agent-toolkit:plan-file-finalizer", "prompt": prompt},
                "session_id": "prompt-completeness-tilde-fence",
                "permission_mode": "default",
            }
        )
        assert result.returncode == 2
        assert "計画ファイルパス" in result.stderr

    def test_heading_inside_html_comment_not_counted(self, tmp_path: pathlib.Path):
        """複数行HTMLコメント内の見出し例示も実見出しと誤認しない。"""
        plan_path = tmp_path / "example.md"
        plan_path.write_text("# 例\n", encoding="utf-8")
        complete_prompt = _complete_plan_file_finalizer_prompt(plan_path)
        prompt = "<!--\n" + complete_prompt + "-->\n本文のみで実見出しは無い。"
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "agent-toolkit:plan-file-finalizer", "prompt": prompt},
                "session_id": "prompt-completeness-html-comment",
                "permission_mode": "default",
            }
        )
        assert result.returncode == 2
        assert "計画ファイルパス" in result.stderr

    def test_heading_inside_indented_code_block_not_counted(self, tmp_path: pathlib.Path):
        """4スペース以上インデントされた見出し例示（CommonMarkのインデントコードブロック）も
        実見出しと誤認しない。"""
        plan_path = tmp_path / "example.md"
        plan_path.write_text("# 例\n", encoding="utf-8")
        complete_prompt = _complete_plan_file_finalizer_prompt(plan_path)
        indented_prompt = "\n".join(f"    {line}" if line else "" for line in complete_prompt.splitlines())
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "agent-toolkit:plan-file-finalizer", "prompt": indented_prompt},
                "session_id": "prompt-completeness-indented-code-block",
                "permission_mode": "default",
            }
        )
        assert result.returncode == 2
        assert "計画ファイルパス" in result.stderr

    def test_missing_prompt_key_blocked(self):
        """`prompt`キー自体が無い場合も安全側でブロックする（Agent/Taskスキーマ上は通常発生しない）。"""
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "agent-toolkit:plan-file-finalizer"},
                "session_id": "prompt-completeness-no-prompt",
                "permission_mode": "default",
            }
        )
        assert result.returncode == 2


def _process_loop_log_env(tmp_path: pathlib.Path) -> dict[str, str]:
    return {
        "DOTFILES_AUTONOMOUS_EXIT_REQUIRED": "1",
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "LOCALAPPDATA": str(tmp_path / "state"),
    }


class TestSubagentStartLogOrdering:
    """`subagent_start`記録は全ブロック検査を通過した場合のみ行われる。

    ブロック時に記録が残ると、対応する`subagent_end`が生成されず
    process-loopの所要時間分析の対応関係が崩れるため、ブロック経路ごとに未記録を確認する。
    """

    def _log_path(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> pathlib.Path:
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "state"))
        return pathlib.Path(platformdirs.user_state_dir("agent-toolkit", appauthor=False)) / "process-feedbacks.log"

    def test_model_override_block_does_not_log_start(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
        log_path = self._log_path(monkeypatch, tmp_path)
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {
                    "subagent_type": "agent-toolkit:plan-file-finalizer",
                    "model": "opus",
                    "prompt": _COMPLETE_PLAN_FILE_FINALIZER_PROMPT_FOR_MODEL_GATE,
                },
                "session_id": "log-order-model-override",
                "permission_mode": "default",
            },
            env_overrides=_process_loop_log_env(tmp_path),
        )
        assert result.returncode == 2
        assert not log_path.exists() or "subagent_start" not in log_path.read_text(encoding="utf-8")

    def test_prompt_completeness_block_does_not_log_start(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
        log_path = self._log_path(monkeypatch, tmp_path)
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "agent-toolkit:plan-file-finalizer", "prompt": "x"},
                "session_id": "log-order-prompt-completeness",
                "permission_mode": "default",
            },
            env_overrides=_process_loop_log_env(tmp_path),
        )
        assert result.returncode == 2
        assert not log_path.exists() or "subagent_start" not in log_path.read_text(encoding="utf-8")

    def test_process7_block_does_not_log_start(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
        log_path = self._log_path(monkeypatch, tmp_path)
        sid = "log-order-process7"
        state = {"plan_mode_skill_invoked": True}
        state.update({flag: False for flag in _PROCESS7_FLAGS})
        _write_session_state(tmp_path, sid, state)
        env = {**_process_loop_log_env(tmp_path), **_process7_env(tmp_path)}
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "agent-toolkit:plan-impl-executor", "prompt": "計画を実装して。"},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 2
        assert not log_path.exists() or "subagent_start" not in log_path.read_text(encoding="utf-8")

    def test_all_checks_pass_logs_start(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
        """モデル指定なし・見出し検査対象外・process7未起動時のexecutorは通過し記録される。"""
        log_path = self._log_path(monkeypatch, tmp_path)
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "agent-toolkit:plan-impl-executor", "prompt": "計画を実装して。"},
                "session_id": "log-order-pass",
                "permission_mode": "default",
            },
            env_overrides=_process_loop_log_env(tmp_path),
        )
        assert result.returncode == 0
        assert log_path.exists()
        assert "subagent_start" in log_path.read_text(encoding="utf-8")


class TestPlanModeFlagReset:
    """`agent-toolkit:plan-mode`スキル起動時のplan-file-finalizerの整合性チェック完了フラグリセット。"""

    def test_flags_reset_on_plan_mode_skill_invoke(self, tmp_path: pathlib.Path):
        """新計画着手時にplan-file-finalizerの整合性チェック完了フラグが偽へリセットされ、
        `current_plan_file_path`が消去される。
        """
        sid = "process7-reset"
        state = {
            "plan_mode_skill_invoked": True,
            "current_plan_file_path": "/tmp/previous-plan.md",
        }
        state.update({flag: True for flag in _PROCESS7_FLAGS})
        _write_session_state(tmp_path, sid, state)
        result = _run(
            {
                "tool_name": "Skill",
                "tool_input": {"skill": "agent-toolkit:plan-mode"},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=_process7_env(tmp_path),
        )
        assert result.returncode == 0
        updated = json.loads((tmp_path / f"claude-agent-toolkit-{sid}.json").read_text(encoding="utf-8"))
        for flag in _PROCESS7_FLAGS:
            assert updated[flag] is False
        assert "current_plan_file_path" not in updated


class TestCheckPlanFileH2SectionOrder:
    """plan file Write時のH2節順違反ブロック検査。

    `_VALID_H2_PLAN_CONTENT`は全8必須H2節を正規順で含む最小正規計画ファイル。
    H2節順違反がある場合にのみブロック（returncode 2）し、
    Write以外・plan file以外・正規コンテンツは通過する。
    """

    _state_env = staticmethod(_plan_file_state_env)
    _make_plan = staticmethod(_make_plan_file)

    @staticmethod
    def _prior_flags(tmp_path: pathlib.Path, session_id: str, _content: str) -> None:
        """H2節順検査の前提条件となるセッション状態フラグを書き込む。"""
        _write_session_state(
            tmp_path,
            session_id,
            {
                "plan_mode_skill_invoked": True,
                "textlint_violations_read": True,
            },
        )

    def test_allows_valid_h2_order(self, tmp_path: pathlib.Path):
        """必須H2節が正規順に揃ったコンテンツは通過する。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "h2order-valid"
        self._prior_flags(tmp_path, sid, _VALID_H2_PLAN_CONTENT)
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": _VALID_H2_PLAN_CONTENT},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 0

    def test_blocks_missing_required_h2(self, tmp_path: pathlib.Path):
        """必須H2節が欠落するコンテンツはブロックする。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "h2order-missing"
        content = "# タイトル\n\n## 背景\n\nx\n"
        self._prior_flags(tmp_path, sid, content)
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        assert "[auto-generated: agent-toolkit/pretooluse][warn]" in result.stderr

    def test_blocks_out_of_order_h2(self, tmp_path: pathlib.Path):
        """必須H2節が正規順と異なる場合はブロックする。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "h2order-order"
        # 背景と対応方針を入れ替えて順序違反にする
        content = (
            "# タイトル\n\n"
            "## 変更履歴\n\nx\n\n"
            "## 対応方針\n\nx\n\n"
            "## 背景\n\nx\n\n"
            "## 調査結果\n\nx\n\n"
            "## 変更内容\n\n### 対象ファイル一覧\n\nx\n\n"
            "## 実行方法\n\nx\n\n"
            "## 進捗ログ\n\nx\n\n"
            "## 計画ファイル（本ファイル）のパス\n\nx\n"
        )
        self._prior_flags(tmp_path, sid, content)
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        assert "[auto-generated: agent-toolkit/pretooluse][warn]" in result.stderr

    def test_blocks_unexpected_h2(self, tmp_path: pathlib.Path):
        """許可外のH2節を含むコンテンツはブロックする。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "h2order-unexpected"
        content = _VALID_H2_PLAN_CONTENT + "\n## 予期せぬセクション\n\nx\n"
        self._prior_flags(tmp_path, sid, content)
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        assert "[auto-generated: agent-toolkit/pretooluse][warn]" in result.stderr

    def test_allows_non_write_tool(self, tmp_path: pathlib.Path):
        """Edit/MultiEditで正規H2順の既存内容を保つ編集はH2節順違反を発生させない。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        plan.write_text(_VALID_H2_PLAN_CONTENT, encoding="utf-8")
        env = self._state_env(tmp_path, home)
        sid = "h2order-edit"
        self._prior_flags(tmp_path, sid, _VALID_H2_PLAN_CONTENT)
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": str(plan),
                    "old_string": "x",
                    "new_string": "y",
                },
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert "H2 section order" not in result.stderr

    def test_allows_multi_edit_tool(self, tmp_path: pathlib.Path):
        """MultiEditで正規H2順の既存内容を保つ編集はH2節順違反を発生させない。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        plan.write_text(_VALID_H2_PLAN_CONTENT, encoding="utf-8")
        env = self._state_env(tmp_path, home)
        sid = "h2order-multiedit"
        self._prior_flags(tmp_path, sid, _VALID_H2_PLAN_CONTENT)
        result = _run(
            {
                "tool_name": "MultiEdit",
                "tool_input": {
                    "file_path": str(plan),
                    "edits": [{"old_string": "x", "new_string": "y"}],
                },
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert "H2 section order" not in result.stderr

    def test_allows_non_plan_file(self, tmp_path: pathlib.Path):
        """plan file以外へのWriteはH2節順検査対象外で通過する。"""
        content = "# タイトルのみ\n"
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(tmp_path / "x.md"), "content": content},
                "session_id": "h2order-nonplan",
                "permission_mode": "default",
            },
        )
        assert result.returncode == 0
        assert result.stdout == ""


def _deferral_state_env(tmp_path: pathlib.Path, home_dir: pathlib.Path) -> dict[str, str]:
    """先送り表現検査テスト用の環境変数。事前lint検査はバイパスする。"""
    return _plan_file_state_env(tmp_path, home_dir)


def _deferral_prior_flags(state_dir: pathlib.Path, sid: str) -> None:
    """先送り表現検査の前提となるセッション状態フラグを書き込む。"""
    _write_session_state(
        state_dir,
        sid,
        {
            "plan_mode_skill_invoked": True,
            "textlint_violations_read": True,
        },
    )


# 先送り表現検査用の正規計画テンプレート。`## 変更内容`配下に違反トークンを置けるよう余白を確保する。
_DEFERRAL_BASE_PLAN = (
    "# タイトル\n\n"
    "## 変更履歴\n\nx\n\n"
    "## 背景\n\nx\n\n"
    "## 対応方針\n\nx\n\n"
    "## 調査結果\n\nx\n\n"
    "## 変更内容\n\n"
    "### 対象ファイル一覧\n\nx\n\n"
    "### 詳細\n\n{body}\n\n"
    "## 実行方法\n\nx\n\n"
    "## 進捗ログ\n\nx\n\n"
    "## 計画ファイル（本ファイル）のパス\n\nx\n"
)


def _h3corr_build_content(extra_h3: str) -> str:
    """FB8対象ファイル一覧・H3対応検査用の計画本文を組み立てる。"""
    return (
        "# タイトル\n\n"
        "## 変更履歴\n\nx\n\n"
        "## 背景\n\nx\n\n"
        "## 対応方針\n\nx\n\n"
        "## 調査結果\n\nx\n\n"
        "## 変更内容\n\n"
        "### 対象ファイル一覧\n\n"
        "- [ ] `foo/bar.py`\n"
        "- [ ] `foo/baz.py`\n\n"
        f"{extra_h3}"
        "## 実行方法\n\nx\n\n"
        "## 進捗ログ\n\nx\n\n"
        "## 計画ファイル（本ファイル）のパス\n\nx\n"
    )


class TestCheckPlanFileNoDeferralExpression:
    """plan file Write/Edit/MultiEdit時の先送り含意動詞連結警告検査（block降格済み）。

    走査対象は`## 変更内容`配下および任意H2下の`### エージェント判断`配下。
    検出条件は次の2条件AND成立時。
    条件(a): 「実装時／実装段階」の直後に「精査／選定／確定／評価／検討」等の未確定動詞が続く。
    条件(b): 文末が「判断／決定／選定／確定」+「する」で結ばれる。
    (a)と(b)の共通動詞（選定・確定・決定）は単独出現でも両条件同時成立として検出する。
    `text`コードブロック内・HTMLコメント内・フロントマターは`iter_markdown_body_lines`が除外する。
    """

    _state_env = staticmethod(_deferral_state_env)
    _make_plan = staticmethod(_make_plan_file)
    _prior_flags = staticmethod(_deferral_prior_flags)

    @pytest.mark.parametrize(
        "phrase",
        [
            "実装時に精査して確定する",
            "実装段階で選定する",
            "実装時に確定する",
            "実装時にあらためて内容を精査したうえで最終的に確定する",
            "実装段階であらためて選定する",
        ],
    )
    def test_write_with_deferral_phrase_blocks(self, tmp_path: pathlib.Path, phrase: str):
        """`## 変更内容`配下の先送り含意動詞連結パターンがWriteで警告される。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = f"deferral-{hash(phrase) & 0xFFFF:x}"
        self._prior_flags(tmp_path, sid)
        content = _DEFERRAL_BASE_PLAN.format(body=f"- {phrase}")
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        assert "deferral expressions were detected" in result.stderr
        assert "[auto-generated: agent-toolkit/pretooluse][warn]" in result.stderr

    def test_write_with_deferral_phrase_in_text_block_is_allowed(self, tmp_path: pathlib.Path):
        """`text`コードブロック内の先送り含意動詞連結パターンはブロックされない。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "deferral-textblock"
        self._prior_flags(tmp_path, sid)
        body = "```text\n実装時に精査して確定する\n```\n"
        content = _DEFERRAL_BASE_PLAN.format(body=body)
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        assert "deferral expressions were detected" not in result.stderr

    def test_write_with_deferral_phrase_in_html_comment_is_allowed(self, tmp_path: pathlib.Path):
        """複数行HTMLコメント内の先送り含意動詞連結パターンはブロックされない。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "deferral-htmlcomment"
        self._prior_flags(tmp_path, sid)
        body = "<!--\n実装時に精査して確定する\n-->\n"
        content = _DEFERRAL_BASE_PLAN.format(body=body)
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        assert "deferral expressions were detected" not in result.stderr

    def test_write_with_deferral_phrase_in_background_is_allowed(self, tmp_path: pathlib.Path):
        """`## 背景`配下の先送り含意動詞連結パターンは走査対象外のためブロックされない。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "deferral-background"
        self._prior_flags(tmp_path, sid)
        # `## 背景`配下（`## 変更内容`・`### エージェント判断`のいずれでもない）へ挿入する。
        content = _DEFERRAL_BASE_PLAN.replace("## 背景\n\nx\n", "## 背景\n\n実装時に精査して確定する\n").format(body="x")
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        assert "deferral expressions were detected" not in result.stderr

    def test_write_with_current_form_action_is_allowed(self, tmp_path: pathlib.Path):
        """現在形の実施義務文（末尾が判断/決定/選定/確定+するではない）はブロックされない。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "deferral-currentform"
        self._prior_flags(tmp_path, sid)
        content = _DEFERRAL_BASE_PLAN.format(body="- 実装時に`agent-toolkit-edit`スキルを呼び出す")
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        assert "deferral expressions were detected" not in result.stderr

    def test_write_with_condition_a_unsatisfied_is_allowed(self, tmp_path: pathlib.Path):
        """条件(a)未確定動詞・(a)(b)共通動詞のいずれも現れない文はブロックされない。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "deferral-cond-a-unsat"
        self._prior_flags(tmp_path, sid)
        content = _DEFERRAL_BASE_PLAN.format(body="- 実装時にレビュー内容を確認して最終的に反映する")
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        assert "deferral expressions were detected" not in result.stderr


class TestPlanFileWarningChecksCoexist:
    """plan file Write検査のrequired-reads・no-deferral警告共存検査（block降格後のfb5後継）。

    required-reads・no-deferralは共にwarnへ降格済みのため、単一の`return 2`集約ではなく
    各違反メッセージが個別にstderrへ出力され、`returncode`は常に0を維持する
    （呼び出し順序＝required-reads→retroactive-scan→no-deferralは維持する）。
    """

    _state_env = staticmethod(_plan_file_state_env)
    _make_plan = staticmethod(_make_plan_file)

    def test_required_reads_and_no_deferral_report_together(self, tmp_path: pathlib.Path):
        """required-readsとno-deferralが同時発生した場合、両違反メッセージがstderrへ列挙される。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "integrated-req-and-deferral"
        # 意図的にrequired-readsフラグを設定せず、両違反が同時発生する条件を用意する。
        _write_session_state(tmp_path, sid, {"plan_mode_skill_invoked": True})
        content = _DEFERRAL_BASE_PLAN.format(body="- 実装時に精査して確定する")
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        # required-reads違反メッセージが含まれる
        assert "editing a plan file without reading required references" in result.stderr
        assert "textlint-violations.md" in result.stderr
        # no-deferral違反メッセージが含まれる
        assert "deferral expressions were detected" in result.stderr
        # 両メッセージともにwarnタグが付与されている
        assert result.stderr.count("[auto-generated: agent-toolkit/pretooluse][warn]") >= 2

    def test_only_required_reads_reports_alone(self, tmp_path: pathlib.Path):
        """required-readsのみ違反する場合、no-deferralメッセージは含まれない。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "integrated-req-only"
        _write_session_state(tmp_path, sid, {"plan_mode_skill_invoked": True})
        content = _DEFERRAL_BASE_PLAN.format(body="- 正常な記述")
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        assert "editing a plan file without reading required references" in result.stderr
        assert "deferral expressions were detected" not in result.stderr

    def test_only_no_deferral_reports_alone(self, tmp_path: pathlib.Path):
        """no-deferralのみ違反する場合、required-readsメッセージは含まれない。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "integrated-deferral-only"
        # required-readsフラグを全て真化し、no-deferralのみを違反させる。
        _deferral_prior_flags(tmp_path, sid)
        content = _DEFERRAL_BASE_PLAN.format(body="- 実装時に精査して確定する")
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        assert "deferral expressions were detected" in result.stderr
        assert "editing a plan file without reading required references" not in result.stderr


def _history_sync_build_content(*, history_line: str) -> str:
    """FB5変更履歴と変更内容の対応照合検査用の計画本文を組み立てる。"""
    return (
        "# タイトル\n\n"
        f"## 変更履歴\n\n- 初版\n{history_line}\n\n"
        "## 背景\n\nx\n\n"
        "## 対応方針\n\nx\n\n"
        "## 調査結果\n\nx\n\n"
        "## 変更内容\n\n"
        "### 対象ファイル一覧\n\n"
        "- [ ] `foo/bar.py`\n\n"
        "### foo/bar.py\n\n```text\nx\n```\n\n"
        "## 実行方法\n\nx\n\n"
        "## 進捗ログ\n\nx\n\n"
        "## 計画ファイル（本ファイル）のパス\n\nx\n"
    )


class TestPlanFileRetroactiveScanRecorded:
    """規範対象ドキュメントへのメタ規範新設編集時の遡及スキャン記録検査（FB4）。"""

    @staticmethod
    def _write_current_plan(home_dir: pathlib.Path, content: str) -> pathlib.Path:
        plans_dir = home_dir / ".claude" / "plans"
        plans_dir.mkdir(parents=True, exist_ok=True)
        plan_path = plans_dir / "current.md"
        plan_path.write_text(content, encoding="utf-8")
        return plan_path

    _NEW_PROHIBITION = "既存の記述のみ\n\n- 新規事項はいかなる理由（例: テスト）があっても実施しない"

    def test_blocks_new_prohibition_without_scan_record(self, tmp_path: pathlib.Path):
        home = tmp_path / "home"
        target = tmp_path / "agent-toolkit" / "rules" / "test-rule.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# rule\n\n既存の記述のみ\n", encoding="utf-8")
        plan_path = self._write_current_plan(home, "## 調査結果\n\nなし\n")
        sid = "retro-block"
        env = _plan_file_state_env(tmp_path, home)
        _write_session_state(tmp_path, sid, {"current_plan_file_path": str(plan_path)})
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": str(target),
                    "old_string": "既存の記述のみ",
                    "new_string": self._NEW_PROHIBITION,
                },
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 2
        assert "new meta-norm pattern" in result.stderr
        assert "`## 調査結果` section" in result.stderr

    def test_allows_when_scan_record_present(self, tmp_path: pathlib.Path):
        home = tmp_path / "home"
        target = tmp_path / "agent-toolkit" / "rules" / "test-rule.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# rule\n\n既存の記述のみ\n", encoding="utf-8")
        plan_content = (
            "## 調査結果\n\n### 遡及スキャン結果\n\n- 対象パターン: 全称禁止形\n- 検出件数: 1件\n- 対応方針: 是正済み\n"
        )
        plan_path = self._write_current_plan(home, plan_content)
        sid = "retro-allow"
        env = _plan_file_state_env(tmp_path, home)
        _write_session_state(tmp_path, sid, {"current_plan_file_path": str(plan_path)})
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": str(target),
                    "old_string": "既存の記述のみ",
                    "new_string": self._NEW_PROHIBITION,
                },
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 0

    def test_no_new_prohibition_allows(self, tmp_path: pathlib.Path):
        """禁止形の増加が無い編集はブロックしない。"""
        target = tmp_path / "agent-toolkit" / "rules" / "test-rule.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# rule\n\n既存の記述のみ\n", encoding="utf-8")
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": str(target),
                    "old_string": "既存の記述のみ",
                    "new_string": "既存の記述を少し変更",
                },
                "session_id": "retro-nochange",
                "permission_mode": "default",
            },
        )
        assert result.returncode == 0

    def test_non_target_path_allows(self, tmp_path: pathlib.Path):
        """コーディングエージェント向け文書判定対象外パスは検査対象外。"""
        target = tmp_path / "misc" / "notes.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("既存の記述のみ\n", encoding="utf-8")
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": str(target),
                    "old_string": "既存の記述のみ",
                    "new_string": self._NEW_PROHIBITION,
                },
                "session_id": "retro-outofscope",
                "permission_mode": "default",
            },
        )
        assert result.returncode == 0


def _path_section_build_content(recorded_path: str) -> str:
    """末尾パス節検査用の計画本文を組み立てる。"""
    return (
        "# タイトル\n\n"
        "## 変更履歴\n\nx\n\n"
        "## 背景\n\nx\n\n"
        "## 対応方針\n\nx\n\n"
        "## 調査結果\n\nx\n\n"
        "## 変更内容\n\n### 対象ファイル一覧\n\nなし\n\n"
        "## 実行方法\n\nx\n\n"
        "## 進捗ログ\n\nx\n\n"
        "## 計画ファイル（本ファイル）のパス\n\n"
        f"`{recorded_path}`\n"
    )


class TestPlanFilePathSectionMatchesFilePath:
    """plan file編集で末尾の`## 計画ファイル（本ファイル）のパス`節配下パス値と`file_path`不一致のブロック検査。"""

    _state_env = staticmethod(_plan_file_state_env)
    _make_plan = staticmethod(_make_plan_file)

    @staticmethod
    def _prior_flags(tmp_path: pathlib.Path, session_id: str, _content: str) -> None:
        _write_session_state(
            tmp_path,
            session_id,
            {
                "plan_mode_skill_invoked": True,
                "textlint_violations_read": True,
            },
        )

    def test_blocks_when_recorded_path_differs(self, tmp_path: pathlib.Path):
        """記録パス値とWrite先のfile_pathが異なる場合はブロックする。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "path-mismatch"
        wrong_path = str(tmp_path / "scratchpad" / "other.md")
        content = _path_section_build_content(wrong_path)
        self._prior_flags(tmp_path, sid, content)
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        assert "trailing path section" in result.stderr

    def test_allows_when_recorded_path_matches(self, tmp_path: pathlib.Path):
        """記録パス値とWrite先のfile_pathが一致する場合は通過する。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "path-match"
        content = _path_section_build_content(str(plan))
        self._prior_flags(tmp_path, sid, content)
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 0

    def test_non_plan_file_is_skipped(self, tmp_path: pathlib.Path):
        """plan fileでないパスへの書き込みは検査対象外。"""
        content = _path_section_build_content("/tmp/x.md")
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(tmp_path / "x.md"), "content": content},
                "session_id": "path-nonplan",
                "permission_mode": "default",
            },
        )
        assert result.returncode == 0

    def test_allows_when_recorded_value_is_placeholder(self, tmp_path: pathlib.Path):
        """パス節配下の値が絶対パス表記でない（`/`・`~`で始まらない）場合はプレースホルダーとみなし通過する。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "path-placeholder"
        content = _path_section_build_content("plan-path-here")
        self._prior_flags(tmp_path, sid, content)
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 0

    def test_allows_when_section_body_absent(self, tmp_path: pathlib.Path):
        """パス節が本文に存在しない場合は本検査の対象外として通過する（他検査でブロックされ得る）。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "path-nosection"
        content = (
            "# タイトル\n\n"
            "## 変更履歴\n\nx\n\n"
            "## 背景\n\nx\n\n"
            "## 対応方針\n\nx\n\n"
            "## 調査結果\n\nx\n\n"
            "## 変更内容\n\n### 対象ファイル一覧\n\nなし\n\n"
            "## 実行方法\n\nx\n\n"
            "## 進捗ログ\n\nx\n\n"
            "## 計画ファイル（本ファイル）のパス\n\n\n"
        )
        self._prior_flags(tmp_path, sid, content)
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        # 本検査は「該当節本文が空」の場合は対象外として通過する
        assert "trailing path section" not in result.stderr


class TestStyleNegationCheck:
    """『Xを根拠にYしない』『Xを理由にYしない』形式の増加検出（FB10、warn）。"""

    @staticmethod
    def _target_path(tmp_path: pathlib.Path) -> pathlib.Path:
        target = tmp_path / "agent-toolkit" / "rules" / "test-rule.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def test_write_with_negation_warns(self, tmp_path: pathlib.Path):
        target = self._target_path(tmp_path)
        content = "# rule\n\n作業量を根拠に延期しない\n"
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(target), "content": content},
                "session_id": "styleneg-write",
                "permission_mode": "default",
            },
        )
        assert result.returncode == 0
        assert "根拠に" in result.stderr

    def test_edit_increase_warns(self, tmp_path: pathlib.Path):
        target = self._target_path(tmp_path)
        target.write_text("# rule\n\n既存の記述\n", encoding="utf-8")
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": str(target),
                    "old_string": "既存の記述",
                    "new_string": "既存の記述\n\n工数を理由に対応しない",
                },
                "session_id": "styleneg-edit",
                "permission_mode": "default",
            },
        )
        assert result.returncode == 0
        assert "理由に" in result.stderr

    def test_edit_no_increase_does_not_warn(self, tmp_path: pathlib.Path):
        """既存文字列の保持のみでは警告しない（誤検出解消）。"""
        target = self._target_path(tmp_path)
        target.write_text("# rule\n\n作業量を根拠に延期しない\n", encoding="utf-8")
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": str(target),
                    "old_string": "作業量を根拠に延期しない",
                    "new_string": "作業量を根拠に延期しない。追記のみ",
                },
                "session_id": "styleneg-edit-noincrease",
                "permission_mode": "default",
            },
        )
        assert result.returncode == 0
        assert "根拠に" not in result.stderr

    def test_non_target_path_does_not_warn(self, tmp_path: pathlib.Path):
        target = tmp_path / "misc" / "notes.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        content = "作業量を根拠に延期しない\n"
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(target), "content": content},
                "session_id": "styleneg-outofscope",
                "permission_mode": "default",
            },
        )
        assert result.returncode == 0
        assert "根拠に" not in result.stderr


class TestDirectAgentToolkitEditsAfterPlanMode:
    """plan-modeスキル起動後、計画ファイル未作成のままagent-toolkit配下の直接編集連続を検知。

    2件目でwarn（stderr出力＋通過）、3件目でblock。
    直前と同一パスの繰り返しはincrementしない。
    対象外パスへの編集はカウンタをリセットし通過する。
    """

    _state_env = staticmethod(_plan_file_state_env)

    def _write_flag_state(self, tmp_path: pathlib.Path, sid: str, extra: dict | None = None) -> None:
        state: dict = {
            "plan_mode_skill_invoked": True,
            "textlint_violations_read": True,
        }
        if extra:
            state.update(extra)
        _write_session_state(tmp_path, sid, state)

    def _target(self, tmp_path: pathlib.Path, subpath: str) -> pathlib.Path:
        # 対象パターン`agent-toolkit/skills/`を含む相対パスを組み立てる。
        path = tmp_path / "agent-toolkit" / "skills" / subpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("stub\n", encoding="utf-8")
        return path

    def test_single_target_edit_does_not_warn(self, tmp_path: pathlib.Path):
        sid = "direct-edit-single"
        self._write_flag_state(tmp_path, sid)
        target = self._target(tmp_path, "foo/SKILL.md")
        env = self._state_env(tmp_path)
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {"file_path": str(target), "old_string": "stub", "new_string": "stub2"},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        assert "without first creating a plan file" not in result.stderr

    def test_second_target_edit_warns_and_continues(self, tmp_path: pathlib.Path):
        sid = "direct-edit-warn"
        self._write_flag_state(tmp_path, sid)
        env = self._state_env(tmp_path)
        for i, name in enumerate(("foo/SKILL.md", "bar/SKILL.md")):
            target = self._target(tmp_path, name)
            result = _run(
                {
                    "tool_name": "Edit",
                    "tool_input": {"file_path": str(target), "old_string": "stub", "new_string": "stub2"},
                    "session_id": sid,
                    "permission_mode": "default",
                },
                env_overrides=env,
            )
            if i == 0:
                assert result.returncode == 0
                assert "[warn]" not in result.stderr
            else:
                # 2件目はwarnして通過する（returncode 0）。
                assert result.returncode == 0
                assert "[warn]" in result.stderr
                assert "without first creating a plan file" in result.stderr

    def test_third_target_edit_blocks(self, tmp_path: pathlib.Path):
        sid = "direct-edit-block"
        self._write_flag_state(tmp_path, sid)
        env = self._state_env(tmp_path)
        for i, name in enumerate(("foo/SKILL.md", "bar/SKILL.md", "baz/SKILL.md")):
            target = self._target(tmp_path, name)
            result = _run(
                {
                    "tool_name": "Edit",
                    "tool_input": {"file_path": str(target), "old_string": "stub", "new_string": "stub2"},
                    "session_id": sid,
                    "permission_mode": "default",
                },
                env_overrides=env,
            )
            if i < 2:
                assert result.returncode == 0
            else:
                # 3件目でblockする。
                assert result.returncode == 2
                assert "[block]" in result.stderr
                assert "without first creating a plan file" in result.stderr

    def test_block_persists_on_same_path_retry(self, tmp_path: pathlib.Path):
        """block後にコーディングエージェントが同一パスを再試行してもblockを継続する。

        block時は`direct_agent_toolkit_edit_count`と`last_agent_toolkit_edit_path`を
        更新しない設計により、再試行時も再度3件目としてblockが返る。
        block時に更新してしまうと、直前パス一致条件でカウンタ加算がスキップされ
        blockが素通りする回避経路が発生するため、その回避を防ぐ。
        """
        sid = "direct-edit-block-retry"
        self._write_flag_state(tmp_path, sid)
        env = self._state_env(tmp_path)
        # 1件目・2件目で異なるパスの編集を実行しwarn状態にする。
        for name in ("foo/SKILL.md", "bar/SKILL.md"):
            target = self._target(tmp_path, name)
            result = _run(
                {
                    "tool_name": "Edit",
                    "tool_input": {"file_path": str(target), "old_string": "stub", "new_string": "stub2"},
                    "session_id": sid,
                    "permission_mode": "default",
                },
                env_overrides=env,
            )
            assert result.returncode == 0
        # 3件目でblock。同一パスで複数回再試行しても継続してblockされることを検証する。
        third = self._target(tmp_path, "baz/SKILL.md")
        for _ in range(3):
            result = _run(
                {
                    "tool_name": "Edit",
                    "tool_input": {"file_path": str(third), "old_string": "stub", "new_string": "stub2"},
                    "session_id": sid,
                    "permission_mode": "default",
                },
                env_overrides=env,
            )
            assert result.returncode == 2
            assert "[block]" in result.stderr
            assert "without first creating a plan file" in result.stderr
        # block後もstateは更新されず、カウンタは2・直前パスは2件目のままである。
        state_path = tmp_path / f"claude-agent-toolkit-{sid}.json"
        state_after = json.loads(state_path.read_text(encoding="utf-8"))
        assert state_after["direct_agent_toolkit_edit_count"] == 2
        assert state_after["last_agent_toolkit_edit_path"].endswith("bar/SKILL.md")

    def test_same_path_repeats_do_not_increment(self, tmp_path: pathlib.Path):
        sid = "direct-edit-same"
        self._write_flag_state(tmp_path, sid)
        env = self._state_env(tmp_path)
        target = self._target(tmp_path, "foo/SKILL.md")
        for _ in range(5):
            result = _run(
                {
                    "tool_name": "Edit",
                    "tool_input": {"file_path": str(target), "old_string": "stub", "new_string": "stub2"},
                    "session_id": sid,
                    "permission_mode": "default",
                },
                env_overrides=env,
            )
            assert result.returncode == 0
            assert "[warn]" not in result.stderr
            assert "[block]" not in result.stderr

    def test_non_target_path_edit_passes(self, tmp_path: pathlib.Path):
        sid = "direct-edit-nontarget"
        self._write_flag_state(tmp_path, sid)
        other = tmp_path / "other.md"
        other.write_text("stub\n", encoding="utf-8")
        env = self._state_env(tmp_path)
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {"file_path": str(other), "old_string": "stub", "new_string": "stub2"},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 0

    def test_skipped_when_plan_mode_not_invoked(self, tmp_path: pathlib.Path):
        """`plan_mode_skill_invoked`が偽なら本checkの対象外。"""
        sid = "direct-edit-nomode"
        _write_session_state(tmp_path, sid, {"plan_mode_skill_invoked": False})
        env = self._state_env(tmp_path)
        # 3件連続でもブロックしない。
        for name in ("foo/SKILL.md", "bar/SKILL.md", "baz/SKILL.md"):
            target = self._target(tmp_path, name)
            result = _run(
                {
                    "tool_name": "Edit",
                    "tool_input": {"file_path": str(target), "old_string": "stub", "new_string": "stub2"},
                    "session_id": sid,
                    "permission_mode": "default",
                },
                env_overrides=env,
            )
            assert result.returncode == 0

    def test_skipped_when_plan_file_written(self, tmp_path: pathlib.Path):
        """計画ファイル作成済みフラグ`plan_file_written=True`なら本checkの対象外。"""
        sid = "direct-edit-planwritten"
        self._write_flag_state(tmp_path, sid, {"plan_file_written": True})
        env = self._state_env(tmp_path)
        for name in ("foo/SKILL.md", "bar/SKILL.md", "baz/SKILL.md"):
            target = self._target(tmp_path, name)
            result = _run(
                {
                    "tool_name": "Edit",
                    "tool_input": {"file_path": str(target), "old_string": "stub", "new_string": "stub2"},
                    "session_id": sid,
                    "permission_mode": "default",
                },
                env_overrides=env,
            )
            assert result.returncode == 0

    def test_plan_file_write_marks_written_and_resets_counter(self, tmp_path: pathlib.Path):
        """warn状態まで進めた後、計画ファイル書込で`plan_file_written=True`とカウンタリセットを検証する。

        `_mark_plan_written`（pretooluse.py内）の副作用として、
        `direct_agent_toolkit_edit_count`と`last_agent_toolkit_edit_path`もリセットされる。
        """
        sid = "direct-edit-mark-plan-written"
        home = tmp_path / "home"
        self._write_flag_state(tmp_path, sid)
        env = self._state_env(tmp_path, home)
        # 2件目でwarn状態にする。
        for name in ("foo/SKILL.md", "bar/SKILL.md"):
            target = self._target(tmp_path, name)
            result = _run(
                {
                    "tool_name": "Edit",
                    "tool_input": {"file_path": str(target), "old_string": "stub", "new_string": "stub2"},
                    "session_id": sid,
                    "permission_mode": "default",
                },
                env_overrides=env,
            )
            assert result.returncode == 0
        # warn状態でstate確認: カウンタが2、直前パス記録あり。
        state_path = tmp_path / f"claude-agent-toolkit-{sid}.json"
        state_pre = json.loads(state_path.read_text(encoding="utf-8"))
        assert state_pre["direct_agent_toolkit_edit_count"] == 2
        assert state_pre["last_agent_toolkit_edit_path"] is not None
        assert not state_pre.get("plan_file_written", False)
        # 計画ファイルへの書き込みを実行する。
        plan = _make_plan_file(home)
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": _VALID_H2_PLAN_CONTENT},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        # `_mark_plan_written`の副作用でフラグが真、カウンタと直前パスがリセットされている。
        state_post = json.loads(state_path.read_text(encoding="utf-8"))
        assert state_post["plan_file_written"] is True
        assert state_post["direct_agent_toolkit_edit_count"] == 0
        assert state_post["last_agent_toolkit_edit_path"] is None


def _h3_codeblock_build_content(extra_h3: str) -> str:
    """`## 変更内容`配下H3のtext/diffコードブロック検査用の計画本文を組み立てる。"""
    return (
        "# タイトル\n\n"
        "## 変更履歴\n\nx\n\n"
        "## 背景\n\nx\n\n"
        "## 対応方針\n\nx\n\n"
        "## 調査結果\n\nx\n\n"
        "## 変更内容\n\n"
        "### 対象ファイル一覧\n\n"
        "- [ ] `foo/bar.py`\n\n"
        f"{extra_h3}"
        "## 実行方法\n\nx\n\n"
        "## 進捗ログ\n\nx\n\n"
        "## 計画ファイル（本ファイル）のパス\n\nx\n"
    )


class TestReadIsolatedReferenceCheck:
    """Read: メインエージェントからの隔離指定リファレンス直接Readをブロックする検査。"""

    _ISOLATED_PATH = "agent-toolkit/skills/agent-standards/references/_scope_escalation_test_inputs.txt"

    def test_read_isolated_reference_blocks(self, tmp_path: pathlib.Path):
        env = {"TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path)}
        result = _run(
            {
                "tool_name": "Read",
                "tool_input": {"file_path": self._ISOLATED_PATH},
                "session_id": "isolated-block",
                "isSidechain": False,
            },
            env_overrides=env,
        )
        assert result.returncode == 2
        assert "isolated reference" in result.stderr

    def test_read_isolated_reference_sidechain_passes(self, tmp_path: pathlib.Path):
        env = {"TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path)}
        result = _run(
            {
                "tool_name": "Read",
                "tool_input": {"file_path": self._ISOLATED_PATH},
                "session_id": "isolated-sidechain",
                "isSidechain": True,
            },
            env_overrides=env,
        )
        assert result.returncode == 0

    def test_read_isolated_reference_agent_toolkit_edit_skill_invoked_passes(self, tmp_path: pathlib.Path):
        """`agent_toolkit_edit_skill_invoked`真ならメイン起動でも隔離Read検査は通過する。

        `agent-toolkit-edit`スキル起動セッションでは同ファイル群の編集が正当な作業となるため、
        隔離Readブロックの例外条件として扱う。
        """
        env = {"TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path)}
        sid = "isolated-agent-toolkit-edit"
        _write_session_state(tmp_path, sid, {"agent_toolkit_edit_skill_invoked": True})
        result = _run(
            {
                "tool_name": "Read",
                "tool_input": {"file_path": self._ISOLATED_PATH},
                "session_id": sid,
                "isSidechain": False,
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        assert "isolated reference" not in result.stderr


def test_isolated_targets_excludes_phrases_md() -> None:
    """`scope-escalation-phrases.md`が隔離対象から除かれ、テストデータのみ隔離を保つ。"""
    assert not pretooluse._is_isolated_reference(  # noqa: SLF001  # pylint: disable=protected-access
        "agent-toolkit/skills/agent-standards/references/scope-escalation-phrases.md"
    )
    assert pretooluse._is_isolated_reference(  # noqa: SLF001  # pylint: disable=protected-access
        "agent-toolkit/skills/agent-standards/references/_scope_escalation_test_inputs.txt"
    )


class TestScopeEscalationMessageIncludesMatchedPhrase:
    """AskUserQuestion経路（block）・doc edit経路（warn、block降格済み）の
    通知メッセージにマッチ文言が含まれることを検証する。
    """

    def test_askuserquestion_block_message_includes_matched_phrase(self):
        text, _category = _SCOPE_ESCALATION_INPUTS[0]
        result = _run(
            {
                "tool_name": "AskUserQuestion",
                "tool_input": {
                    "questions": [
                        {
                            "question": "q",
                            "header": "h",
                            "options": [{"label": text, "description": ""}],
                        }
                    ]
                },
            }
        )
        assert result.returncode == 2
        assert "matched:" in result.stderr

    def test_doc_edit_warn_message_includes_matched_phrase(self):
        text, category = _SCOPE_ESCALATION_INPUTS[0]
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": "agent-toolkit/rules/01-agent.md",
                    "content": f"# header\n\n{text}\n",
                },
            }
        )
        assert result.returncode == 0
        assert category in result.stderr
        assert "matched:" in result.stderr


class TestAgentNormReferenceCheck:
    """Agent/Task: 規範非読込型サブエージェント起動時の規範明示引用漏れ警告検査。"""

    def test_agent_norm_skipping_without_reference_warns(self, tmp_path: pathlib.Path):
        env = {"TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path)}
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "claude", "prompt": "調査してください。"},
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        assert "does not load norms" in result.stderr

    def test_agent_norm_skipping_with_reference_passes(self, tmp_path: pathlib.Path):
        env = {"TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path)}
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {
                    "subagent_type": "claude",
                    "prompt": "agent-toolkit:agent-standardsを参照して実装せよ。",
                },
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        assert "does not load norms" not in result.stderr

    @pytest.mark.parametrize(
        "relative_path",
        [
            "skills/agent-standards/SKILL.md",
            "skills/coding-standards/SKILL.md",
            "skills/writing-standards/SKILL.md",
            "rules/01-agent.md",
            "rules/02-claude-code.md",
        ],
    )
    def test_authorized_absolute_path_with_read_instruction_passes(self, tmp_path: pathlib.Path, relative_path: str) -> None:
        """認可規範の絶対パスとRead指示を含む起動は警告しない。"""
        plugin_root = pathlib.Path(pretooluse.__file__).resolve().parents[1]
        prompt = f"`{plugin_root / relative_path}`を着手時にReadで読む。"
        result = _run(
            {"tool_name": "Agent", "tool_input": {"subagent_type": "claude", "prompt": prompt}},
            env_overrides={"TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path)},
        )
        assert result.returncode == 0
        assert "does not load norms" not in result.stderr

    @pytest.mark.parametrize(
        "relative_path",
        [
            "skills/agent-standards/SKILL.md",
            "skills/coding-standards/SKILL.md",
            "skills/writing-standards/SKILL.md",
            "rules/01-agent.md",
            "rules/02-claude-code.md",
        ],
    )
    def test_authorized_relative_path_with_read_instruction_warns(self, tmp_path: pathlib.Path, relative_path: str) -> None:
        """認可対象と同じ接尾辞でも相対パスなら警告する。"""
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "claude", "prompt": f"`{relative_path}`をReadで読む。"},
            },
            env_overrides={"TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path)},
        )
        assert result.returncode == 0
        assert "does not load norms" in result.stderr

    @pytest.mark.parametrize(
        "relative_path",
        [
            "skills/agent-standards/SKILL.md",
            "skills/coding-standards/SKILL.md",
            "skills/writing-standards/SKILL.md",
            "rules/01-agent.md",
            "rules/02-claude-code.md",
        ],
    )
    def test_authorized_absolute_path_without_read_instruction_warns(self, tmp_path: pathlib.Path, relative_path: str) -> None:
        """認可規範の絶対パスだけを列挙してもRead指示が無ければ警告する。"""
        plugin_root = pathlib.Path(pretooluse.__file__).resolve().parents[1]
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "claude", "prompt": f"参照先: `{plugin_root / relative_path}`"},
            },
            env_overrides={"TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path)},
        )
        assert result.returncode == 0
        assert "does not load norms" in result.stderr

    def test_unapproved_markdown_absolute_path_warns(self, tmp_path: pathlib.Path) -> None:
        """認可対象外のMarkdown絶対パスでは警告する。"""
        other = tmp_path / "other.md"
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "claude", "prompt": f"`{other}`をReadで読む。"},
            },
            env_overrides={"TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path)},
        )
        assert result.returncode == 0
        assert "does not load norms" in result.stderr

    def test_authorized_suffix_outside_trusted_root_warns(self, tmp_path: pathlib.Path) -> None:
        """認可接尾辞を持つ信頼済みルート外の偽パスでは警告する。"""
        fake = tmp_path / "fake" / "skills" / "agent-standards" / "SKILL.md"
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "claude", "prompt": f"`{fake}`をReadで読む。"},
            },
            env_overrides={"TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path)},
        )
        assert result.returncode == 0
        assert "does not load norms" in result.stderr

    def test_parent_directory_component_warns(self, tmp_path: pathlib.Path) -> None:
        """信頼済みルート配下に見せかける`..`入りパスでは警告する。"""
        plugin_root = pathlib.Path(pretooluse.__file__).resolve().parents[1]
        candidate = f"{plugin_root}/other/../skills/agent-standards/SKILL.md"
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "claude", "prompt": f"`{candidate}`をReadで読む。"},
            },
            env_overrides={"TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path)},
        )
        assert result.returncode == 0
        assert "does not load norms" in result.stderr

    @pytest.mark.parametrize(
        ("candidate", "roots", "expected"),
        [
            pytest.param(
                "/plugin/skills/agent-standards/SKILL.md",
                ("/plugin", "/distributed"),
                True,
                id="posix",
            ),
            pytest.param(
                r"C:\plugin\skills\agent-standards\SKILL.md",
                (r"C:\plugin", r"C:\distributed"),
                True,
                id="windows-drive",
            ),
            pytest.param(
                r"\\server\share\plugin\skills\agent-standards\SKILL.md",
                (r"\\server\share\plugin", r"\\server\share\distributed"),
                True,
                id="windows-unc",
            ),
            pytest.param(
                "skills/agent-standards/SKILL.md",
                ("/plugin", "/distributed"),
                False,
                id="relative",
            ),
        ],
    )
    def test_absolute_path_formats(self, candidate: str, roots: tuple[str, str], expected: bool) -> None:
        """POSIX・Windowsドライブ・UNC絶対パスを受理し、相対パスを除外する。"""
        assert (
            pretooluse._is_absolute_norm_reference_path(  # pylint: disable=protected-access  # noqa: SLF001
                candidate, roots
            )
            is expected
        )

    def test_symlink_to_untrusted_file_is_rejected(self, tmp_path: pathlib.Path) -> None:
        """信頼済みルート内から外部の偽規範へ向けたシンボリックリンクを受理しない。"""
        plugin_root = tmp_path / "plugin"
        link = plugin_root / "skills" / "agent-standards" / "SKILL.md"
        link.parent.mkdir(parents=True)
        outside = tmp_path / "outside.md"
        outside.write_text("fake\n", encoding="utf-8")
        link.symlink_to(outside)
        assert not pretooluse._is_absolute_norm_reference_path(  # pylint: disable=protected-access  # noqa: SLF001
            str(link), (str(plugin_root), str(tmp_path / "distributed"))
        )

    def test_task_tool_treated_same_as_agent(self, tmp_path: pathlib.Path):
        env = {"TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path)}
        result = _run(
            {
                "tool_name": "Task",
                "tool_input": {"subagent_type": "Explore", "prompt": "調査してください。"},
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        assert "does not load norms" in result.stderr


class TestProcessFeedbacksBlocksEnterPlanMode:
    """process-feedbacks経由起動下でのEnterPlanMode発行ブロック検査。"""

    @staticmethod
    def _env(tmp_path: pathlib.Path) -> dict[str, str]:
        return {"TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path)}

    def test_blocks_when_flag_true(self, tmp_path: pathlib.Path):
        """`process_feedbacks_skill_invoked=True`時、EnterPlanMode発行がブロックされる。"""
        sid = "epm-flag-true"
        _write_session_state(tmp_path, sid, {"process_feedbacks_skill_invoked": True})
        result = _run(
            {"tool_name": "EnterPlanMode", "tool_input": {}, "session_id": sid},
            env_overrides=self._env(tmp_path),
        )
        assert result.returncode == 2
        assert "process-feedbacks" in result.stderr
        assert "EnterPlanMode" in result.stderr
        assert "agent-toolkit:exit-session" in result.stderr
        assert "agent-toolkit:process-feedbacks`" not in result.stderr

    def test_passes_when_flag_absent(self, tmp_path: pathlib.Path):
        """フラグ未設定時はEnterPlanMode発行を通過させる。"""
        sid = "epm-flag-absent"
        _write_session_state(tmp_path, sid, {})
        result = _run(
            {"tool_name": "EnterPlanMode", "tool_input": {}, "session_id": sid},
            env_overrides=self._env(tmp_path),
        )
        assert result.returncode == 0

    def test_passes_when_flag_false(self, tmp_path: pathlib.Path):
        """フラグが偽の場合はEnterPlanMode発行を通過させる。"""
        sid = "epm-flag-false"
        _write_session_state(tmp_path, sid, {"process_feedbacks_skill_invoked": False})
        result = _run(
            {"tool_name": "EnterPlanMode", "tool_input": {}, "session_id": sid},
            env_overrides=self._env(tmp_path),
        )
        assert result.returncode == 0

    def test_ignores_other_tool_names(self, tmp_path: pathlib.Path):
        """`ExitPlanMode`など他のツール名では本ハンドラは発火しない。"""
        sid = "epm-other-tool"
        _write_session_state(tmp_path, sid, {"process_feedbacks_skill_invoked": True})
        result = _run(
            {"tool_name": "ExitPlanMode", "tool_input": {}, "session_id": sid},
            env_overrides=self._env(tmp_path),
        )
        assert "blocked: issuing EnterPlanMode" not in result.stderr


class TestPlanAndAddFeedbackBlocksEnterPlanMode:
    """plan-and-add-feedback経由起動下でのEnterPlanMode発行ブロック検査。"""

    @staticmethod
    def _env(tmp_path: pathlib.Path) -> dict[str, str]:
        return {"TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path)}

    def test_blocks_when_flag_true(self, tmp_path: pathlib.Path):
        """`plan_and_add_entries_skill_invoked=True`時、EnterPlanMode発行がブロックされる。"""
        sid = "epm-paaf-flag-true"
        _write_session_state(tmp_path, sid, {"plan_and_add_entries_skill_invoked": True})
        result = _run(
            {"tool_name": "EnterPlanMode", "tool_input": {}, "session_id": sid},
            env_overrides=self._env(tmp_path),
        )
        assert result.returncode == 2
        assert "plan-and-add-feedback" in result.stderr
        assert "EnterPlanMode" in result.stderr
        assert "agent-toolkit:process-feedbacks`" in result.stderr
        assert "agent-toolkit:exit-session" not in result.stderr

    def test_passes_when_flag_absent(self, tmp_path: pathlib.Path):
        """フラグ未設定時はEnterPlanMode発行を通過させる。"""
        sid = "epm-paaf-flag-absent"
        _write_session_state(tmp_path, sid, {})
        result = _run(
            {"tool_name": "EnterPlanMode", "tool_input": {}, "session_id": sid},
            env_overrides=self._env(tmp_path),
        )
        assert result.returncode == 0

    def test_passes_when_flag_false(self, tmp_path: pathlib.Path):
        """フラグが偽の場合はEnterPlanMode発行を通過させる。"""
        sid = "epm-paaf-flag-false"
        _write_session_state(tmp_path, sid, {"plan_and_add_entries_skill_invoked": False})
        result = _run(
            {"tool_name": "EnterPlanMode", "tool_input": {}, "session_id": sid},
            env_overrides=self._env(tmp_path),
        )
        assert result.returncode == 0

    def test_ignores_other_tool_names(self, tmp_path: pathlib.Path):
        """`ExitPlanMode`など他のツール名では本ハンドラは発火しない。"""
        sid = "epm-paaf-other-tool"
        _write_session_state(tmp_path, sid, {"plan_and_add_entries_skill_invoked": True})
        result = _run(
            {"tool_name": "ExitPlanMode", "tool_input": {}, "session_id": sid},
            env_overrides=self._env(tmp_path),
        )
        assert "blocked: issuing EnterPlanMode" not in result.stderr

    def test_both_flags_report_both_reset_paths(self, tmp_path: pathlib.Path):
        """両フラグが真の場合は両方の解除手段を案内する。"""
        sid = "epm-both-flags-true"
        _write_session_state(
            tmp_path,
            sid,
            {
                "process_feedbacks_skill_invoked": True,
                "plan_and_add_entries_skill_invoked": True,
            },
        )
        result = _run(
            {"tool_name": "EnterPlanMode", "tool_input": {}, "session_id": sid},
            env_overrides=self._env(tmp_path),
        )
        assert result.returncode == 2
        assert "agent-toolkit:exit-session" in result.stderr
        assert "agent-toolkit:process-feedbacks`" in result.stderr


class TestPlanFileBumpStepWhenAgentToolkitTarget:
    """agent-toolkit配下対象計画のversion bumpステップ欠落警告検査。"""

    _state_env = staticmethod(_plan_file_state_env)
    _make_plan = staticmethod(_make_plan_file)

    @staticmethod
    def _prior_flags(tmp_path: pathlib.Path, session_id: str) -> None:
        _write_session_state(
            tmp_path,
            session_id,
            {
                "plan_mode_skill_invoked": True,
                "textlint_violations_read": True,
            },
        )

    @staticmethod
    def _plan_body(target_paths: list[str], include_bump: bool) -> str:
        target_lines = "\n".join(f"- [ ] `{p}`" for p in target_paths)
        exec_lines = "- `scripts/agent_toolkit_bump.py patch`" if include_bump else "- 実装する"
        return (
            "# タイトル\n\n"
            "## 変更履歴\n\n- 初版\n\n"
            "## 背景\n\nx\n\n"
            "## 対応方針\n\nx\n\n"
            "## 調査結果\n\nx\n\n"
            "## 変更内容\n\n"
            "### 対象ファイル一覧\n\n"
            f"{target_lines}\n\n"
            "## 実行方法\n\n"
            f"{exec_lines}\n\n"
            "## 進捗ログ\n\n"
            "## 計画ファイル（本ファイル）のパス\n\nx\n"
        )

    def test_warns_when_agent_toolkit_path_without_bump_step(self, tmp_path: pathlib.Path):
        home = tmp_path / "home"
        plan = self._make_plan(home)
        sid = "bump-missing"
        self._prior_flags(tmp_path, sid)
        content = self._plan_body(["agent-toolkit/scripts/pretooluse.py"], include_bump=False)
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
            },
            env_overrides=self._state_env(tmp_path, home),
        )
        assert result.returncode == 0
        assert "agent_toolkit_bump.py" in result.stderr
        assert "[auto-generated: agent-toolkit/pretooluse][warn]" in result.stderr

    def test_passes_when_bump_step_present(self, tmp_path: pathlib.Path):
        home = tmp_path / "home"
        plan = self._make_plan(home)
        sid = "bump-present"
        self._prior_flags(tmp_path, sid)
        content = self._plan_body(["agent-toolkit/scripts/pretooluse.py"], include_bump=True)
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
            },
            env_overrides=self._state_env(tmp_path, home),
        )
        assert result.returncode == 0
        assert "the target file list includes paths under `agent-toolkit/`" not in result.stderr

    def test_passes_when_no_agent_toolkit_path(self, tmp_path: pathlib.Path):
        home = tmp_path / "home"
        plan = self._make_plan(home)
        sid = "bump-no-target"
        self._prior_flags(tmp_path, sid)
        content = self._plan_body(["pytools/example.py"], include_bump=False)
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
            },
            env_overrides=self._state_env(tmp_path, home),
        )
        assert result.returncode == 0
        assert "the target file list includes paths under `agent-toolkit/`" not in result.stderr

    def test_passes_when_all_test_py_paths(self, tmp_path: pathlib.Path):
        home = tmp_path / "home"
        plan = self._make_plan(home)
        sid = "bump-test-only"
        self._prior_flags(tmp_path, sid)
        content = self._plan_body(
            ["agent-toolkit/scripts/pretooluse_test.py", "agent-toolkit/scripts/posttooluse_test.py"],
            include_bump=False,
        )
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
            },
            env_overrides=self._state_env(tmp_path, home),
        )
        assert result.returncode == 0
        assert "the target file list includes paths under `agent-toolkit/`" not in result.stderr


class TestPlanFileManifestWhenBumpStep:
    """bump step記載計画のmanifest対象ファイル記載欠落警告検査。"""

    _state_env = staticmethod(_plan_file_state_env)
    _make_plan = staticmethod(_make_plan_file)

    @staticmethod
    def _prior_flags(tmp_path: pathlib.Path, session_id: str) -> None:
        _write_session_state(
            tmp_path,
            session_id,
            {
                "plan_mode_skill_invoked": True,
                "textlint_violations_read": True,
            },
        )

    @staticmethod
    def _plan_body(target_paths: list[str], include_bump: bool) -> str:
        target_lines = "\n".join(f"- [ ] `{p}`" for p in target_paths)
        exec_lines = "- `scripts/agent_toolkit_bump.py patch`" if include_bump else "- 実装する"
        return (
            "# タイトル\n\n"
            "## 変更履歴\n\n- 初版\n\n"
            "## 背景\n\nx\n\n"
            "## 対応方針\n\nx\n\n"
            "## 調査結果\n\nx\n\n"
            "## 変更内容\n\n"
            "### 対象ファイル一覧\n\n"
            f"{target_lines}\n\n"
            "## 実行方法\n\n"
            f"{exec_lines}\n\n"
            "## 進捗ログ\n\n"
            "## 計画ファイル（本ファイル）のパス\n\nx\n"
        )

    def test_passes_when_no_bump_step(self, tmp_path: pathlib.Path):
        home = tmp_path / "home"
        plan = self._make_plan(home)
        sid = "manifest-no-bump"
        self._prior_flags(tmp_path, sid)
        content = self._plan_body(["agent-toolkit/scripts/pretooluse.py"], include_bump=False)
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
            },
            env_overrides=self._state_env(tmp_path, home),
        )
        assert result.returncode == 0
        assert "target file list is missing both manifests" not in result.stderr

    def test_passes_when_both_manifests_present(self, tmp_path: pathlib.Path):
        home = tmp_path / "home"
        plan = self._make_plan(home)
        sid = "manifest-both-present"
        self._prior_flags(tmp_path, sid)
        content = self._plan_body(
            [
                "agent-toolkit/scripts/pretooluse.py",
                "agent-toolkit/.claude-plugin/plugin.json",
                ".claude-plugin/marketplace.json",
            ],
            include_bump=True,
        )
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
            },
            env_overrides=self._state_env(tmp_path, home),
        )
        assert result.returncode == 0
        assert "target file list is missing both manifests" not in result.stderr

    def test_warns_when_plugin_json_missing(self, tmp_path: pathlib.Path):
        home = tmp_path / "home"
        plan = self._make_plan(home)
        sid = "manifest-plugin-missing"
        self._prior_flags(tmp_path, sid)
        content = self._plan_body(
            ["agent-toolkit/scripts/pretooluse.py", ".claude-plugin/marketplace.json"],
            include_bump=True,
        )
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
            },
            env_overrides=self._state_env(tmp_path, home),
        )
        assert result.returncode == 0
        assert "target file list is missing both manifests" in result.stderr
        assert "[auto-generated: agent-toolkit/pretooluse][warn]" in result.stderr

    def test_warns_when_marketplace_json_missing(self, tmp_path: pathlib.Path):
        home = tmp_path / "home"
        plan = self._make_plan(home)
        sid = "manifest-marketplace-missing"
        self._prior_flags(tmp_path, sid)
        content = self._plan_body(
            ["agent-toolkit/scripts/pretooluse.py", "agent-toolkit/.claude-plugin/plugin.json"],
            include_bump=True,
        )
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
            },
            env_overrides=self._state_env(tmp_path, home),
        )
        assert result.returncode == 0
        assert "target file list is missing both manifests" in result.stderr
        assert "[auto-generated: agent-toolkit/pretooluse][warn]" in result.stderr


class TestPlanFileTargetFilePathsRelative:
    """対象ファイル一覧のパス表記違反警告検査。"""

    _state_env = staticmethod(_plan_file_state_env)
    _make_plan = staticmethod(_make_plan_file)

    @staticmethod
    def _prior_flags(tmp_path: pathlib.Path, session_id: str) -> None:
        _write_session_state(
            tmp_path,
            session_id,
            {
                "plan_mode_skill_invoked": True,
                "textlint_violations_read": True,
            },
        )

    @staticmethod
    def _plan_body(target_paths: list[str]) -> str:
        target_lines = "\n".join(f"- [ ] `{p}`" for p in target_paths)
        return (
            "# タイトル\n\n"
            "## 変更履歴\n\n- 初版\n\n"
            "## 背景\n\nx\n\n"
            "## 対応方針\n\nx\n\n"
            "## 調査結果\n\nx\n\n"
            "## 変更内容\n\n"
            "### 対象ファイル一覧\n\n"
            f"{target_lines}\n\n"
            "## 実行方法\n\n- 実装する\n\n"
            "## 進捗ログ\n\n"
            "## 計画ファイル（本ファイル）のパス\n\nx\n"
        )

    def test_warns_on_absolute_path(self, tmp_path: pathlib.Path):
        home = tmp_path / "home"
        plan = self._make_plan(home)
        sid = "path-abs"
        self._prior_flags(tmp_path, sid)
        content = self._plan_body(["/home/user/project/foo.py"])
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
            },
            env_overrides=self._state_env(tmp_path, home),
        )
        assert result.returncode == 0
        assert "absolute paths or parent-directory references" in result.stderr
        assert "[auto-generated: agent-toolkit/pretooluse][warn]" in result.stderr

    def test_warns_on_parent_reference(self, tmp_path: pathlib.Path):
        home = tmp_path / "home"
        plan = self._make_plan(home)
        sid = "path-parent"
        self._prior_flags(tmp_path, sid)
        content = self._plan_body(["../outside/bar.py"])
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
            },
            env_overrides=self._state_env(tmp_path, home),
        )
        assert result.returncode == 0
        assert "../outside/bar.py" in result.stderr

    def test_passes_on_relative_paths(self, tmp_path: pathlib.Path):
        home = tmp_path / "home"
        plan = self._make_plan(home)
        sid = "path-relative"
        self._prior_flags(tmp_path, sid)
        content = self._plan_body(["agent-toolkit/scripts/atk.py"])
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
            },
            env_overrides=self._state_env(tmp_path, home),
        )
        assert result.returncode == 0
        assert "absolute paths or parent-directory references" not in result.stderr


class TestFrontmatterSyncNoteBodyExists:
    """frontmatter同期注記の本体該当語句の実在検証（feedback 2、warn）。"""

    @staticmethod
    def _target(tmp_path: pathlib.Path, name: str = "test-agent.md") -> pathlib.Path:
        target = tmp_path / "agent-toolkit" / "agents" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    @classmethod
    def _prepare_scenario(cls, tmp_path: pathlib.Path, setup: str) -> pathlib.Path:
        target = cls._target(tmp_path)
        if setup.startswith("git-"):
            (tmp_path / ".git").mkdir()
        related_files = {
            "sibling": ("other-agent.md", "# other-agent\n\n## 実在節\n\n本文。\n"),
            "missing-section": ("other-agent2.md", "# other-agent2\n\n本文のみ。\n"),
            "multiline": ("other-multiline-agent.md", "# other-multiline-agent\n\n## 実在複数行節\n\n本文。\n"),
            "git-sibling": ("sibling-agent.md", "# sibling-agent\n\n## 実在兄弟節\n\n本文。\n"),
            "git-consecutive-ok": ("external-agent.md", "# external-agent\n\n## 外部節\n\n本文。\n"),
            "git-consecutive-missing": ("external-agent2.md", "# external-agent2\n\n## 外部節2\n\n本文。\n"),
        }
        related = related_files.get(setup)
        if related is not None:
            name, body = related
            (target.parent / name).write_text(body, encoding="utf-8")
        if setup == "git-neighbor":
            rules_dir = tmp_path / "agent-toolkit" / "rules"
            rules_dir.mkdir(parents=True)
            (rules_dir / "01-agent.md").write_text(
                "# 01-agent\n\n## 品質最優先\n\n本文。\n",
                encoding="utf-8",
            )
        return target

    @pytest.mark.parametrize(
        ("setup", "content"),
        [
            pytest.param(
                "sibling",
                "---\nname: test-agent\n# other-agent.mdの「実在節」節と意図的に重複させている\n---\n\n# test-agent\n",
                id="same-directory-reference",
            ),
            pytest.param("none", "# test-agent\n\nfrontmatterなし本文。\n", id="no-frontmatter"),
            pytest.param(
                "multiline",
                "---\nname: test-agent\n# 同期注記: 「実在複数行節」節の内容は\n"
                "# other-multiline-agent.md\n# と意図的に重複する。\n---\n\n# test-agent\n",
                id="multiline-reference",
            ),
            pytest.param(
                "git-sibling",
                "---\nname: test-agent\n# sibling-agent.mdの「実在兄弟節」節と意図的に重複させている\n---\n\n# test-agent\n",
                id="git-ancestor-sibling",
            ),
            pytest.param(
                "git-neighbor",
                "---\nname: test-agent\n# 01-agent.mdの「品質最優先」節と意図的に重複させている\n---\n\n# test-agent\n",
                id="git-ancestor-neighbor",
            ),
            pytest.param(
                "git-consecutive-ok",
                "---\nname: test-agent\n"
                "# external-agent.mdの「外部節」節と意図的に重複させている\n"
                "# 「## 自己節」節はこのファイル自身の内容と意図的に同期する\n"
                "---\n\n# test-agent\n\n## 自己節\n\n本文。\n",
                id="consecutive-notes-separated",
            ),
        ],
    )
    def test_existing_reference_scenarios_do_not_warn(
        self,
        tmp_path: pathlib.Path,
        setup: str,
        content: str,
    ) -> None:
        """実在参照と対象外形式ではfrontmatter同期注記警告を返さない。"""
        target = self._prepare_scenario(tmp_path, setup)
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(target), "content": content},
                "session_id": "fm-sync-ok",
                "permission_mode": "default",
            },
        )
        assert result.returncode == 0
        assert "frontmatter sync note" not in result.stderr

    @pytest.mark.parametrize(
        ("setup", "content", "expected_message", "expected_identifier"),
        [
            pytest.param(
                "none",
                "---\nname: test-agent\n# nonexistent-agent.mdの「何か」節と意図的に重複させている\n---\n\n# test-agent\n",
                "referenced file path does not exist",
                "nonexistent-agent.md",
                id="missing-file",
            ),
            pytest.param(
                "missing-section",
                "---\nname: test-agent\n# other-agent2.mdの「存在しない節」節と意図的に重複させている\n---\n\n# test-agent\n",
                "section name does not exist",
                "存在しない節",
                id="missing-section",
            ),
            pytest.param(
                "none",
                "---\nname: test-agent\n# 同期注記: nonexistent-prefix.mdの「何か」節\n---\n\n# test-agent\n",
                "referenced file path does not exist",
                "nonexistent-prefix.md",
                id="prefix-form-missing",
            ),
            pytest.param(
                "none",
                "---\nname: test-agent\n# 同期注記: 「何か」節の内容は\n"
                "# nonexistent-multiline.md\n# と意図的に重複する。\n---\n\n# test-agent\n",
                "referenced file path does not exist",
                "nonexistent-multiline.md",
                id="multiline-missing",
            ),
            pytest.param(
                "git-missing",
                "---\nname: test-agent\n# never-exists.mdの「何か」節と意図的に重複させている\n---\n\n# test-agent\n",
                "referenced file path does not exist",
                "never-exists.md",
                id="git-ancestor-missing",
            ),
            pytest.param(
                "git-consecutive-missing",
                "---\nname: test-agent\n"
                "# external-agent2.mdの「外部節2」節と意図的に重複させている\n"
                "# 「## 存在しない自己節」節はこのファイル自身の内容と意図的に同期する\n"
                "---\n\n# test-agent\n",
                "section name does not exist",
                "存在しない自己節",
                id="consecutive-note-mismatch",
            ),
        ],
    )
    def test_missing_reference_scenarios_warn(
        self,
        tmp_path: pathlib.Path,
        setup: str,
        content: str,
        expected_message: str,
        expected_identifier: str,
    ) -> None:
        """不在パスまたは不在節を参照する同期注記では警告を返す。"""
        target = self._prepare_scenario(tmp_path, setup)
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(target), "content": content},
                "session_id": "fm-sync-missing-path",
                "permission_mode": "default",
            },
        )
        assert result.returncode == 0
        assert expected_message in result.stderr
        assert expected_identifier in result.stderr


# --- 日本語文中への他言語文字の混入検査 (block) ---

_HANGUL_SAMPLE = "\uac00"  # ハングル音節1文字（エスケープ表記で構成する）
_CYRILLIC_SAMPLE = "\u0430"  # キリル小文字1文字（同上）


class TestForeignScriptMixin:
    """日本語文中への他言語文字の混入検査。"""

    def test_blocks_hangul_in_japanese(self):
        """日本語を含む文字列へのハングル混入を遮断する。"""
        content = "テスト" + _HANGUL_SAMPLE + "名を確認する"
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "/tmp/a.txt", "content": content}})
        assert result.returncode == 2
        assert "non-Japanese script" in result.stderr

    def test_blocks_cyrillic_in_japanese(self):
        """日本語を含む文字列へのキリル混入を遮断する。"""
        content = "テスト" + _CYRILLIC_SAMPLE + "名を確認する"
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "/tmp/a.txt", "content": content}})
        assert result.returncode == 2
        assert "non-Japanese script" in result.stderr

    def test_passes_japanese_only(self):
        """日本語のみの文字列は通過する。"""
        content = "テスト名を確認する"
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "/tmp/a.txt", "content": content}})
        assert result.returncode == 0

    def test_passes_english_with_cyrillic(self):
        """英語＋キリルは通過する（日本語を含まないため対象外）。"""
        content = "test" + _CYRILLIC_SAMPLE + "name"
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "/tmp/a.txt", "content": content}})
        assert result.returncode == 0


# --- .md規範文書の本文中にある節参照の実在検証 (warn) ---


class TestBodySectionReferenceExists:
    """規範文書の本文中にある節参照の実在検査。"""

    def test_passes_existing_section_reference(self, tmp_path):
        """実在する節参照は通過する。"""
        # 参照先ファイルを作成
        ref_file = tmp_path / "referenced.md"
        ref_file.write_text("# 存在する節\n\n本文です。", encoding="utf-8")

        # 参照元ファイル
        target_file = tmp_path / "test.md"
        content = "本文\n\n`referenced.md`「存在する節」節を参照。"

        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(target_file), "content": content},
            }
        )
        # 警告が出ないこと（returncode == 0）を確認
        assert result.returncode == 0

    def test_warns_missing_section_reference(self, tmp_path):
        """不在の節参照に対して警告する。"""
        # 参照先ファイルを作成
        ref_file = tmp_path / "referenced.md"
        ref_file.write_text("# 別の節\n\n本文です。", encoding="utf-8")

        # 参照元ファイル
        target_file = tmp_path / "agent-toolkit" / "rules" / "test.md"
        target_file.parent.mkdir(parents=True, exist_ok=True)
        content = "本文\n\n`referenced.md`「存在しない節」節を参照。"

        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(target_file), "content": content},
            }
        )
        assert result.returncode == 0
        # 警告が出ること
        assert "section name does not exist" in result.stderr


# --- codex sandbox指定（danger-full-access）を含む行の削除・変更の遮断 (block) ---


class TestDangerFullAccessPreserved:
    """codex sandbox指定を含む行の削除・変更の遮断。"""

    def test_blocks_removal_of_sandbox_assignment(self):
        """sandbox指定記述を削除する編集を遮断する。"""
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": "agent-toolkit/scripts/pretooluse.py",
                    "content": '"sandbox": "read-only"',
                },
            }
        )
        assert result.returncode == 2
        assert "blocked" in result.stderr

    def test_blocks_weakening_of_sandbox_value(self):
        """sandbox値を弱める編集を遮断する。"""
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": "agent-toolkit/agents/plan-file-finalizer.md",
                    "content": "`sandbox`へ`workspace-write`と指定",
                },
            }
        )
        assert result.returncode == 2
        assert "blocked" in result.stderr

    def test_passes_non_sandbox_change(self):
        """sandbox指定記述を保ったまま説明文を変える編集は通過する。"""
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": "agent-toolkit/scripts/pretooluse.py",
                    "content": '"sandbox": "danger-full-access" # comment',
                },
            }
        )
        assert result.returncode == 0

    def test_passes_non_protected_file(self):
        """保護対象外ファイルでは通過する。"""
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": "/tmp/unrelated.py",
                    "content": 'sandbox = "read-only"',
                },
            }
        )
        assert result.returncode == 0


class TestCodexRemoteSnapshotRecording:
    """`mcp__codex__codex`/`mcp__codex__codex-reply`呼び出し時のリモート参照スナップショット記録。

    codexプロセス内部の実行がPreToolUse/PostToolUseフックを通らずに不可逆操作（`git push`等）を
    行う事象への機械チェック（事後検知）のうち、記録側（PreToolUse）を検証する。
    """

    _write_state = staticmethod(_write_session_state)
    _read_state = staticmethod(_read_session_state)

    @staticmethod
    def _init_repo(path: pathlib.Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q"], cwd=path, check=True)
        subprocess.run(["git", "config", "user.email", "a@example.com"], cwd=path, check=True)
        subprocess.run(["git", "config", "user.name", "a"], cwd=path, check=True)

    def test_records_snapshot_with_agent_id_key(self, tmp_path: pathlib.Path):
        """`transcript_path`からagentIdを抽出できる場合、agentIdをキーとして記録する。"""
        repo = tmp_path / "repo"
        self._init_repo(repo)
        env = _plan_file_state_env(tmp_path)
        self._write_state(tmp_path, "snap-agent", {"codex_exec_skill_invoked": True})
        result = _run(
            {
                "tool_name": "mcp__codex__codex",
                "tool_input": {"prompt": "hello", "sandbox": "danger-full-access", "cwd": str(repo)},
                "session_id": "snap-agent",
                "cwd": str(repo),
                "transcript_path": "/x/agent-abc123.jsonl",
                "isSidechain": True,
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        state = self._read_state(tmp_path, "snap-agent")
        entries = state.get("codex_remote_snapshot_by_key")
        assert entries is not None
        recorded = entries.get("abc123")
        assert recorded is not None
        assert recorded["cwd"] == str(repo)
        assert recorded["snapshot"] == {}

    def test_records_snapshot_with_session_id_fallback_key(self, tmp_path: pathlib.Path):
        """agentIdを抽出できない場合（メインセッション自身の直接呼び出し）は`session_id`をキーとする。"""
        repo = tmp_path / "repo"
        self._init_repo(repo)
        env = _plan_file_state_env(tmp_path)
        self._write_state(tmp_path, "snap-session", {"codex_exec_skill_invoked": True})
        result = _run(
            {
                "tool_name": "mcp__codex__codex",
                "tool_input": {"prompt": "hello", "sandbox": "danger-full-access", "cwd": str(repo)},
                "session_id": "snap-session",
                "cwd": str(repo),
                "isSidechain": True,
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        state = self._read_state(tmp_path, "snap-session")
        entries = state.get("codex_remote_snapshot_by_key")
        assert entries is not None
        recorded = entries.get("session:snap-session")
        assert recorded is not None
        assert recorded["cwd"] == str(repo)

    def test_records_snapshot_from_tool_input_cwd_not_payload_cwd(self, tmp_path: pathlib.Path):
        """比較対象は`tool_input["cwd"]`（codexの実行対象）であり、`payload["cwd"]`
        （呼び出し元セッション自身の作業ディレクトリ）ではないことを検証する。両者が異なる場合、
        `tool_input["cwd"]`側が記録されなければcodexの実行対象と異なるリポジトリを比較してしまう。
        """
        codex_repo = tmp_path / "codex-repo"
        session_repo = tmp_path / "session-repo"
        self._init_repo(codex_repo)
        self._init_repo(session_repo)
        env = _plan_file_state_env(tmp_path)
        self._write_state(tmp_path, "snap-cwd-src", {"codex_exec_skill_invoked": True})
        result = _run(
            {
                "tool_name": "mcp__codex__codex",
                "tool_input": {"prompt": "hello", "sandbox": "danger-full-access", "cwd": str(codex_repo)},
                "session_id": "snap-cwd-src",
                "cwd": str(session_repo),
                "isSidechain": True,
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        state = self._read_state(tmp_path, "snap-cwd-src")
        entries = state.get("codex_remote_snapshot_by_key")
        assert entries is not None
        recorded = entries.get("session:snap-cwd-src")
        assert recorded is not None
        assert recorded["cwd"] == str(codex_repo)

    def test_reply_skips_recording_when_no_prior_cwd(self, tmp_path: pathlib.Path):
        """直前の`mcp__codex__codex`呼び出しによるcwd記録が無い場合、`-reply`は記録をスキップする。

        `mcp__codex__codex-reply`の`tool_input`には`cwd`が含まれないため、
        同一キーの直近`mcp__codex__codex`呼び出しで永続化したcwdが無ければ比較対象が無い。
        """
        env = _plan_file_state_env(tmp_path)
        self._write_state(tmp_path, "snap-reply-nocwd", {"codex_exec_skill_invoked": True})
        result = _run(
            {
                "tool_name": "mcp__codex__codex-reply",
                "tool_input": {"threadId": "th_abc123", "prompt": "続行"},
                "session_id": "snap-reply-nocwd",
                "isSidechain": False,
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        state = self._read_state(tmp_path, "snap-reply-nocwd")
        assert state.get("codex_remote_snapshot_by_key") is None

    def test_reply_reuses_cwd_recorded_by_prior_codex_call(self, tmp_path: pathlib.Path):
        """`mcp__codex__codex-reply`は同一キーの直近`mcp__codex__codex`呼び出しのcwdを引き継いで記録する。"""
        repo = tmp_path / "repo"
        self._init_repo(repo)
        env = _plan_file_state_env(tmp_path)
        self._write_state(tmp_path, "snap-reply", {"codex_exec_skill_invoked": True})
        first = _run(
            {
                "tool_name": "mcp__codex__codex",
                "tool_input": {"prompt": "hello", "sandbox": "danger-full-access", "cwd": str(repo)},
                "session_id": "snap-reply",
                "isSidechain": True,
            },
            env_overrides=env,
        )
        assert first.returncode == 0
        self._write_state(
            tmp_path,
            "snap-reply",
            self._read_state(tmp_path, "snap-reply") | {"codex_exec_skill_invoked": True},
        )
        result = _run(
            {
                "tool_name": "mcp__codex__codex-reply",
                "tool_input": {"threadId": "th_abc123", "prompt": "続行"},
                "session_id": "snap-reply",
                "isSidechain": False,
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        state = self._read_state(tmp_path, "snap-reply")
        entries = state.get("codex_remote_snapshot_by_key")
        assert entries is not None
        assert entries.get("session:snap-reply", {}).get("cwd") == str(repo)
