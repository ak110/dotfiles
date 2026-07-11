"""agent-toolkit/scripts/pretooluse.py のテスト。

subprocessで起動しexit code・stderr・stdoutを検証する。
"""

# pylint: disable=too-many-lines  # ハンドラ網羅のためテストケースが多く、分割するとフィクスチャ重複が増えるため許容する

import json
import os
import pathlib
import re
import subprocess
import sys

import pytest
from _scope_escalation_test_helpers import load_scope_escalation_inputs as _load_scope_escalation_inputs
from pyfltr.colloquial import check as _colloquial_check

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "pretooluse.py"
_PLUGIN_MANIFEST = pathlib.Path(__file__).resolve().parents[1] / ".claude-plugin" / "plugin.json"
_MARKETPLACE_MANIFEST = pathlib.Path(__file__).resolve().parents[2] / ".claude-plugin" / "marketplace.json"


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
    """plan file編集全般で plan-mode スキル未起動をブロックする検査。

    plan-modeスキル未起動でもplan file以外の操作（Read・Bash・他Skill・通常ファイル編集等）は
    一切ブロックも警告もしない。`~/.claude/plans/`直下の`*.md`に対する
    Write/Edit/MultiEditのみがブロック対象となる。`permission_mode`の値には依存しない。
    """

    _state_env = staticmethod(_plan_file_state_env)
    _make_plan = staticmethod(_make_plan_file)

    def test_blocks_plan_file_write_without_skill(self, tmp_path: pathlib.Path):
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
        assert result.returncode == 2
        assert "plan-mode" in result.stderr
        assert "Phase 1" in result.stderr
        assert "[auto-generated: agent-toolkit/pretooluse][block]" in result.stderr

    def test_blocks_plan_file_edit_without_skill(self, tmp_path: pathlib.Path):
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
        assert result.returncode == 2

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
                "plan_file_guidelines_read": True,
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

    def test_allows_non_plan_file_edit_without_skill(self, tmp_path: pathlib.Path):
        """plan file 以外の編集はスキル未起動でも通過する。"""
        home = tmp_path / "home"
        home.mkdir()
        env = self._state_env(tmp_path, home)
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(tmp_path / "x.md"), "content": "# t\n"},
                "session_id": "plan-other-file",
                "permission_mode": "plan",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        assert result.stdout == ""

    def test_allows_read_in_plan_mode(self, tmp_path: pathlib.Path):
        """Read は plan-mode スキル未起動でも一切ブロック・警告しない。"""
        env = self._state_env(tmp_path)
        result = _run(
            {
                "tool_name": "Read",
                "tool_input": {"file_path": "/etc/hostname"},
                "session_id": "plan-read",
                "permission_mode": "plan",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        assert result.stdout == ""

    def test_allows_bash_in_plan_mode(self, tmp_path: pathlib.Path):
        env = self._state_env(tmp_path)
        result = _run(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "ls"},
                "session_id": "plan-bash",
                "permission_mode": "plan",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        assert result.stdout == ""

    def test_allows_other_skill_in_plan_mode(self, tmp_path: pathlib.Path):
        """`apply-feedback`等のplan mode下での他Skill呼び出しはブロックしない。"""
        env = self._state_env(tmp_path)
        result = _run(
            {
                "tool_name": "Skill",
                "tool_input": {"skill": "agent-toolkit:apply-feedback"},
                "session_id": "plan-other-skill",
                "permission_mode": "plan",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        assert result.stdout == ""

    def test_skipped_outside_plan_mode(self, tmp_path: pathlib.Path):
        """plan mode 外でも plan-mode スキル未起動時は plan file 編集をブロックする。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "non-plan-mode"
        # textlint_violations_read・plan_file_guidelines_readを設定して独立checkとの干渉を回避
        _write_session_state(
            tmp_path,
            sid,
            {
                "textlint_violations_read": True,
                "plan_file_guidelines_read": True,
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
        assert result.returncode == 2
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
    """plan file 編集前に必須リファレンス未読の場合のブロック検査。

    `permission_mode`の値に依らず、`~/.claude/plans/`直下の`*.md`に対する
    Write/Edit/MultiEditのみがブロック対象となる。plan file以外の操作は
    一切ブロック・警告しない。
    """

    _state_env = staticmethod(_plan_file_state_env)
    _make_plan = staticmethod(_make_plan_file)

    def test_blocks_when_both_unread(self, tmp_path: pathlib.Path):
        """両方未読の場合、ブロックメッセージに両参照パスが含まれる。"""
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
        assert result.returncode == 2
        assert "textlint-violations.md" in result.stderr
        assert "plan-file-guidelines.md" in result.stderr
        assert "[auto-generated: agent-toolkit/pretooluse][block]" in result.stderr

    def test_blocks_when_only_textlint_violations_unread(self, tmp_path: pathlib.Path):
        """片方のみ未読の場合、当該参照パスのみメッセージに列挙される。"""
        home = tmp_path / "home"
        plan = self._make_plan(home, "edit.md")
        env = self._state_env(tmp_path, home)
        sid = "req-textlint-unread"
        _write_session_state(
            tmp_path,
            sid,
            {"plan_mode_skill_invoked": True, "plan_file_guidelines_read": True},
        )
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {"file_path": str(plan), "old_string": "a", "new_string": "b"},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 2
        assert "textlint-violations.md" in result.stderr
        assert "plan-file-guidelines.md" not in result.stderr
        assert "[auto-generated: agent-toolkit/pretooluse][block]" in result.stderr

    def test_blocks_when_only_plan_file_guidelines_unread(self, tmp_path: pathlib.Path):
        """片方のみ未読の場合、当該参照パスのみメッセージに列挙される。"""
        home = tmp_path / "home"
        plan = self._make_plan(home, "multi.md")
        env = self._state_env(tmp_path, home)
        sid = "req-guidelines-unread"
        _write_session_state(
            tmp_path,
            sid,
            {"plan_mode_skill_invoked": True, "textlint_violations_read": True},
        )
        result = _run(
            {
                "tool_name": "MultiEdit",
                "tool_input": {
                    "file_path": str(plan),
                    "edits": [{"old_string": "a", "new_string": "b"}],
                },
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 2
        assert "plan-file-guidelines.md" in result.stderr
        assert "textlint-violations.md" not in result.stderr
        assert "[auto-generated: agent-toolkit/pretooluse][block]" in result.stderr

    def test_allows_plan_file_when_both_read(self, tmp_path: pathlib.Path):
        """両方読了の場合は通過する。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "req-both-read"
        _write_session_state(
            tmp_path,
            sid,
            {
                "plan_mode_skill_invoked": True,
                "textlint_violations_read": True,
                "plan_file_guidelines_read": True,
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


class TestPlanFileSizeLimitTargetWcLRecorded:
    """plan file Write時の文書サイズ上限対象ファイルwc -l実測値記録漏れ検出。

    `## 変更内容`に文書サイズ上限対象パスが列挙され、実ファイルが200行以上にもかかわらず
    `## 調査結果`または`### エージェント判断`にwc -l実測値（±2許容）が未記載の場合にブロックする。
    対象外パス・200行未満・Write以外のツール・plan file以外は一切ブロックしない。
    """

    _state_env = staticmethod(_plan_file_state_env)
    _make_plan = staticmethod(_make_plan_file)

    @staticmethod
    def _run_with_cwd(
        payload: object,
        cwd: pathlib.Path,
        env_overrides: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        text = json.dumps(payload, ensure_ascii=False)
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
            cwd=str(cwd),
        )

    @staticmethod
    def _all_prior_flags(tmp_path: pathlib.Path, session_id: str, content: str | None = None) -> None:
        # `content`パラメーターは旧prelint検査用の状態設定に使っていたが撤廃済み。
        # 互換のためシグネチャは維持する（呼び出し側の書き換え範囲を最小化する）。
        del content
        state: dict = {
            "plan_mode_skill_invoked": True,
            "textlint_violations_read": True,
            "plan_file_guidelines_read": True,
        }
        _write_session_state(tmp_path, session_id, state)

    @staticmethod
    def _make_target_file(base: pathlib.Path, rel: str, lines: int = 210) -> pathlib.Path:
        target = base / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(f"line {i}" for i in range(lines)) + "\n", encoding="utf-8")
        return target

    def test_blocks_when_wc_l_not_recorded(self, tmp_path: pathlib.Path):
        """変更内容に対象パスがあり実ファイルが200行以上だが調査結果に基名未記載の場合はブロックする。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "psl-no-record"
        self._all_prior_flags(tmp_path, sid)

        target_rel = "agent-toolkit/rules/test-rule.md"
        self._make_target_file(tmp_path, target_rel, lines=210)

        content = f"## 変更内容\n\n- `{target_rel}` を変更する\n\n## 調査結果\n\nなし\n"
        result = self._run_with_cwd(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
                "permission_mode": "default",
            },
            cwd=tmp_path,
            env_overrides=env,
        )
        assert result.returncode == 0
        assert "test-rule.md" in result.stderr
        assert "[auto-generated: agent-toolkit/pretooluse][warn]" in result.stderr

    def test_blocks_when_number_deviation_exceeds_2(self, tmp_path: pathlib.Path):
        """調査結果に基名はあるが記載行数が実測値から±3以上ずれている場合はブロックする。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "psl-wrong-count"
        self._all_prior_flags(tmp_path, sid)

        target_rel = "agent-toolkit/rules/test-rule.md"
        self._make_target_file(tmp_path, target_rel, lines=210)

        content = f"## 変更内容\n\n- `{target_rel}` を変更する\n\n## 調査結果\n\ntest-rule.md は 100 行。\n"
        result = self._run_with_cwd(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
                "permission_mode": "default",
            },
            cwd=tmp_path,
            env_overrides=env,
        )
        assert result.returncode == 0
        assert "test-rule.md" in result.stderr
        assert "[auto-generated: agent-toolkit/pretooluse][warn]" in result.stderr

    def test_passes_when_file_under_200_lines(self, tmp_path: pathlib.Path):
        """実ファイルが200行未満の場合は通過する。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "psl-small-file"

        target_rel = "agent-toolkit/rules/test-rule.md"
        self._make_target_file(tmp_path, target_rel, lines=50)

        content = (
            "## 変更履歴\n\nx\n\n"
            "## 背景\n\nx\n\n"
            "## 対応方針\n\nx\n\n"
            "## 調査結果\n\nなし\n\n"
            f"## 変更内容\n\n- `{target_rel}` を変更する\n\n"
            "## 実行方法\n\nx\n\n"
            "## 進捗ログ\n\nx\n\n"
            "## 計画ファイル（本ファイル）のパス\n\nx\n"
        )
        self._all_prior_flags(tmp_path, sid, content=content)
        result = self._run_with_cwd(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
                "permission_mode": "default",
            },
            cwd=tmp_path,
            env_overrides=env,
        )
        assert result.returncode == 0

    def test_passes_when_path_not_in_scope(self, tmp_path: pathlib.Path):
        """文書サイズ上限対象外パスは200行以上でも通過する。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "psl-out-of-scope"

        target_rel = "some/other/file.md"
        self._make_target_file(tmp_path, target_rel, lines=300)

        content = (
            "## 変更履歴\n\nx\n\n"
            "## 背景\n\nx\n\n"
            "## 対応方針\n\nx\n\n"
            "## 調査結果\n\nなし\n\n"
            f"## 変更内容\n\n- `{target_rel}` を変更する\n\n"
            "## 実行方法\n\nx\n\n"
            "## 進捗ログ\n\nx\n\n"
            "## 計画ファイル（本ファイル）のパス\n\nx\n"
        )
        self._all_prior_flags(tmp_path, sid, content=content)
        result = self._run_with_cwd(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
                "permission_mode": "default",
            },
            cwd=tmp_path,
            env_overrides=env,
        )
        assert result.returncode == 0

    def test_passes_when_wc_l_recorded_in_survey_results(self, tmp_path: pathlib.Path):
        """調査結果に実測値±2の数値が記載されている場合は通過する。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "psl-correct-chosa"

        target_rel = "agent-toolkit/rules/test-rule.md"
        self._make_target_file(tmp_path, target_rel, lines=210)

        content = (
            "## 変更履歴\n\nx\n\n"
            "## 背景\n\nx\n\n"
            "## 対応方針\n\nx\n\n"
            "## 調査結果\n\ntest-rule.md は 210 行。\n\n"
            f"## 変更内容\n\n- `{target_rel}` を変更する\n\n"
            "## 実行方法\n\nx\n\n"
            "## 進捗ログ\n\nx\n\n"
            "## 計画ファイル（本ファイル）のパス\n\nx\n"
        )
        self._all_prior_flags(tmp_path, sid, content=content)
        result = self._run_with_cwd(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
                "permission_mode": "default",
            },
            cwd=tmp_path,
            env_overrides=env,
        )
        assert result.returncode == 0

    def test_passes_when_wc_l_recorded_in_agent_judgment(self, tmp_path: pathlib.Path):
        """エージェント判断に実測値±2の数値が記載されている場合は通過する。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "psl-agent-judgment"

        target_rel = "agent-toolkit/rules/test-rule.md"
        self._make_target_file(tmp_path, target_rel, lines=210)

        content = (
            "## 変更履歴\n\nx\n\n"
            "## 背景\n\nx\n\n"
            "## 対応方針\n\nx\n\n"
            "## 調査結果\n\n調査内容。\n\n"
            "### エージェント判断\n\ntest-rule.md: 209行。\n\n"
            f"## 変更内容\n\n- `{target_rel}` を変更する\n\n"
            "## 実行方法\n\nx\n\n"
            "## 進捗ログ\n\nx\n\n"
            "## 計画ファイル（本ファイル）のパス\n\nx\n"
        )
        self._all_prior_flags(tmp_path, sid, content=content)
        result = self._run_with_cwd(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
                "permission_mode": "default",
            },
            cwd=tmp_path,
            env_overrides=env,
        )
        assert result.returncode == 0

    def test_passes_when_wc_l_recorded_in_agent_judgment_under_changes_section(self, tmp_path: pathlib.Path):
        """`### エージェント判断`が`## 調査結果`配下でなく`## 変更内容`配下にある場合でも通過する。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "psl-agent-judgment-only"

        target_rel = "agent-toolkit/rules/test-rule.md"
        self._make_target_file(tmp_path, target_rel, lines=210)

        # `### エージェント判断`を`## 変更内容`配下に置き、`## 調査結果`配下でなくても認識されることを検証する
        content = (
            "## 変更履歴\n\nx\n\n"
            "## 背景\n\nx\n\n"
            "## 対応方針\n\nx\n\n"
            "## 調査結果\n\nなし\n\n"
            f"## 変更内容\n\n- `{target_rel}` を変更する\n\n"
            "### エージェント判断\n\ntest-rule.md: 209行。\n\n"
            "## 実行方法\n\nx\n\n"
            "## 進捗ログ\n\nx\n\n"
            "## 計画ファイル（本ファイル）のパス\n\nx\n"
        )
        self._all_prior_flags(tmp_path, sid, content=content)
        result = self._run_with_cwd(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
                "permission_mode": "default",
            },
            cwd=tmp_path,
            env_overrides=env,
        )
        assert result.returncode == 0

    def test_passes_for_non_write_tool(self, tmp_path: pathlib.Path):
        """Write以外のツール（Editなど）は本検査の対象外。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        # H2節順検査がEdit/MultiEditにも適用されるため、plan初期内容をvalid H2にする
        plan.write_text(_VALID_H2_PLAN_CONTENT, encoding="utf-8")
        env = self._state_env(tmp_path, home)
        sid = "psl-non-write"
        self._all_prior_flags(tmp_path, sid)

        result = self._run_with_cwd(
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": str(plan),
                    "old_string": "# タイトル",
                    "new_string": "# タイトル",
                },
                "session_id": sid,
                "permission_mode": "default",
            },
            cwd=tmp_path,
            env_overrides=env,
        )
        assert result.returncode == 0

    def test_passes_for_non_plan_file(self, tmp_path: pathlib.Path):
        """plan file以外のWriteは通過する。"""
        home = tmp_path / "home"
        home.mkdir(parents=True, exist_ok=True)
        env = self._state_env(tmp_path, home)
        # file_pathが計画ファイル外のため、本checkは先行する全checkで即時returnする（事前フラグ不要）

        target_rel = "agent-toolkit/rules/test-rule.md"
        self._make_target_file(tmp_path, target_rel, lines=210)

        content = f"## 変更内容\n\n- `{target_rel}` を変更する\n\n## 調査結果\n\nなし\n"
        result = self._run_with_cwd(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(tmp_path / "not-a-plan.md"), "content": content},
                "session_id": "psl-not-plan",
                "permission_mode": "default",
            },
            cwd=tmp_path,
            env_overrides=env,
        )
        assert result.returncode == 0

    def test_passes_when_number_deviation_is_2(self, tmp_path: pathlib.Path):
        """記載値が実測値から±2の場合は通過する（上限境界値）。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "psl-dev-2-pass"

        target_rel = "agent-toolkit/rules/test-rule.md"
        self._make_target_file(tmp_path, target_rel, lines=210)

        # 208（実測値210から-2）で通過することを検証
        content = (
            "## 変更履歴\n\nx\n\n"
            "## 背景\n\nx\n\n"
            "## 対応方針\n\nx\n\n"
            f"## 調査結果\n\ntest-rule.md は 208 行。\n\n"
            f"## 変更内容\n\n- `{target_rel}` を変更する\n\n"
            "## 実行方法\n\nx\n\n"
            "## 進捗ログ\n\nx\n\n"
            "## 計画ファイル（本ファイル）のパス\n\nx\n"
        )
        self._all_prior_flags(tmp_path, sid, content=content)
        result = self._run_with_cwd(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
                "permission_mode": "default",
            },
            cwd=tmp_path,
            env_overrides=env,
        )
        assert result.returncode == 0

    def test_blocks_when_number_deviation_is_3(self, tmp_path: pathlib.Path):
        """記載値が実測値から±3の場合はブロックする（上限+1境界値）。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "psl-dev-3-block"
        self._all_prior_flags(tmp_path, sid)

        target_rel = "agent-toolkit/rules/test-rule.md"
        self._make_target_file(tmp_path, target_rel, lines=210)

        # 207（実測値210から-3）でブロックすることを検証
        content = f"## 変更内容\n\n- `{target_rel}` を変更する\n\n## 調査結果\n\ntest-rule.md は 207 行。\n"
        result = self._run_with_cwd(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
                "permission_mode": "default",
            },
            cwd=tmp_path,
            env_overrides=env,
        )
        assert result.returncode == 0
        assert "[auto-generated: agent-toolkit/pretooluse][warn]" in result.stderr

    def test_passes_when_number_deviation_is_plus_2(self, tmp_path: pathlib.Path):
        """記載値が実測値から+2の場合は通過する（上限境界値・正方向）。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "psl-dev-plus-2-pass"
        self._all_prior_flags(tmp_path, sid)

        target_rel = "agent-toolkit/rules/test-rule.md"
        self._make_target_file(tmp_path, target_rel, lines=210)

        # 212（実測値210から+2）で通過することを検証
        content = (
            "## 変更履歴\n\nx\n\n"
            "## 背景\n\nx\n\n"
            "## 対応方針\n\nx\n\n"
            f"## 調査結果\n\ntest-rule.md は 212 行。\n\n"
            f"## 変更内容\n\n- `{target_rel}` を変更する\n\n"
            "## 実行方法\n\nx\n\n"
            "## 進捗ログ\n\nx\n\n"
            "## 計画ファイル（本ファイル）のパス\n\nx\n"
        )
        result = self._run_with_cwd(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
                "permission_mode": "default",
            },
            cwd=tmp_path,
            env_overrides=env,
        )
        assert result.returncode == 0

    def test_blocks_when_number_deviation_is_plus_3(self, tmp_path: pathlib.Path):
        """記載値が実測値から+3の場合はブロックする（上限+1境界値・正方向）。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "psl-dev-plus-3-block"
        self._all_prior_flags(tmp_path, sid)

        target_rel = "agent-toolkit/rules/test-rule.md"
        self._make_target_file(tmp_path, target_rel, lines=210)

        # 213（実測値210から+3）でブロックすることを検証
        content = f"## 変更内容\n\n- `{target_rel}` を変更する\n\n## 調査結果\n\ntest-rule.md は 213 行。\n"
        result = self._run_with_cwd(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
                "permission_mode": "default",
            },
            cwd=tmp_path,
            env_overrides=env,
        )
        assert result.returncode == 0
        assert "[auto-generated: agent-toolkit/pretooluse][warn]" in result.stderr

    def test_blocks_when_agents_md_wc_l_not_recorded(self, tmp_path: pathlib.Path):
        """`_SIZE_LIMIT_TARGET_BASENAMES`照合によりAGENTS.mdが対象となる場合にブロックする。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "psl-agents-md-block"
        self._all_prior_flags(tmp_path, sid)

        target_rel = "some/dir/AGENTS.md"
        self._make_target_file(tmp_path, target_rel, lines=210)

        content = f"## 変更内容\n\n- `{target_rel}` を変更する\n\n## 調査結果\n\nなし\n"
        result = self._run_with_cwd(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
                "permission_mode": "default",
            },
            cwd=tmp_path,
            env_overrides=env,
        )
        assert result.returncode == 0
        assert "AGENTS.md" in result.stderr
        assert "[auto-generated: agent-toolkit/pretooluse][warn]" in result.stderr

    def test_blocks_when_claude_md_wc_l_not_recorded(self, tmp_path: pathlib.Path):
        """`_SIZE_LIMIT_TARGET_BASENAMES`照合によりCLAUDE.mdが対象となる場合にブロックする。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "psl-claude-md-block"
        self._all_prior_flags(tmp_path, sid)

        target_rel = "some/dir/CLAUDE.md"
        self._make_target_file(tmp_path, target_rel, lines=210)

        content = f"## 変更内容\n\n- `{target_rel}` を変更する\n\n## 調査結果\n\nなし\n"
        result = self._run_with_cwd(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
                "permission_mode": "default",
            },
            cwd=tmp_path,
            env_overrides=env,
        )
        assert result.returncode == 0
        assert "CLAUDE.md" in result.stderr
        assert "[auto-generated: agent-toolkit/pretooluse][warn]" in result.stderr

    def test_passes_when_path_backquote_missing(self, tmp_path: pathlib.Path):
        """`## 変更内容`にバッククォートパスが存在しない場合は通過する。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "psl-no-changes-section"
        self._all_prior_flags(tmp_path, sid)

        target_rel = "agent-toolkit/rules/test-rule.md"
        self._make_target_file(tmp_path, target_rel, lines=210)

        content = (
            "## 変更履歴\n\nx\n\n"
            "## 背景\n\nx\n\n"
            "## 対応方針\n\nx\n\n"
            "## 調査結果\n\nなし\n\n"
            "## 変更内容\n\nファイル参照なし。\n\n"
            "## 実行方法\n\nx\n\n"
            "## 進捗ログ\n\nx\n\n"
            "## 計画ファイル（本ファイル）のパス\n\nx\n"
        )
        result = self._run_with_cwd(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
                "permission_mode": "default",
            },
            cwd=tmp_path,
            env_overrides=env,
        )
        assert result.returncode == 0

    def test_passes_when_target_file_does_not_exist(self, tmp_path: pathlib.Path):
        """`## 変更内容`に対象パスが列挙されても実ファイルが存在しない場合は通過する。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "psl-no-file"
        self._all_prior_flags(tmp_path, sid)

        # 実ファイルを作成しない
        target_rel = "agent-toolkit/rules/nonexistent.md"

        content = (
            "## 変更履歴\n\nx\n\n"
            "## 背景\n\nx\n\n"
            "## 対応方針\n\nx\n\n"
            "## 調査結果\n\nなし\n\n"
            f"## 変更内容\n\n- `{target_rel}` を変更する\n\n"
            "## 実行方法\n\nx\n\n"
            "## 進捗ログ\n\nx\n\n"
            "## 計画ファイル（本ファイル）のパス\n\nx\n"
        )
        result = self._run_with_cwd(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
                "permission_mode": "default",
            },
            cwd=tmp_path,
            env_overrides=env,
        )
        assert result.returncode == 0

    def test_passes_when_lines_just_below_200(self, tmp_path: pathlib.Path):
        """実ファイルが199行（閾値未満）の場合は通過する。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "psl-199-lines"
        self._all_prior_flags(tmp_path, sid)

        target_rel = "agent-toolkit/rules/test-rule.md"
        self._make_target_file(tmp_path, target_rel, lines=199)

        content = (
            "## 変更履歴\n\nx\n\n"
            "## 背景\n\nx\n\n"
            "## 対応方針\n\nx\n\n"
            "## 調査結果\n\nなし\n\n"
            f"## 変更内容\n\n- `{target_rel}` を変更する\n\n"
            "## 実行方法\n\nx\n\n"
            "## 進捗ログ\n\nx\n\n"
            "## 計画ファイル（本ファイル）のパス\n\nx\n"
        )
        result = self._run_with_cwd(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
                "permission_mode": "default",
            },
            cwd=tmp_path,
            env_overrides=env,
        )
        assert result.returncode == 0

    def test_blocks_when_lines_exactly_200(self, tmp_path: pathlib.Path):
        """実ファイルが200行（閾値ちょうど）の場合は照合対象となりブロックする。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "psl-200-lines"
        self._all_prior_flags(tmp_path, sid)

        target_rel = "agent-toolkit/rules/test-rule.md"
        self._make_target_file(tmp_path, target_rel, lines=200)

        content = f"## 変更内容\n\n- `{target_rel}` を変更する\n\n## 調査結果\n\nなし\n"
        result = self._run_with_cwd(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
                "permission_mode": "default",
            },
            cwd=tmp_path,
            env_overrides=env,
        )
        assert result.returncode == 0
        assert "[auto-generated: agent-toolkit/pretooluse][warn]" in result.stderr

    def test_blocks_when_skill_md_wc_l_not_recorded(self, tmp_path: pathlib.Path):
        """`agent-toolkit/skills/foo/SKILL.md`相当のパスが対象として認識される場合にブロックする。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "psl-skill-md-block"
        self._all_prior_flags(tmp_path, sid)

        target_rel = "agent-toolkit/skills/foo/SKILL.md"
        self._make_target_file(tmp_path, target_rel, lines=210)

        content = f"## 変更内容\n\n- `{target_rel}` を変更する\n\n## 調査結果\n\nなし\n"
        result = self._run_with_cwd(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
                "permission_mode": "default",
            },
            cwd=tmp_path,
            env_overrides=env,
        )
        assert result.returncode == 0
        assert "SKILL.md" in result.stderr
        assert "[auto-generated: agent-toolkit/pretooluse][warn]" in result.stderr

    def test_blocks_when_references_md_wc_l_not_recorded(self, tmp_path: pathlib.Path):
        """`agent-toolkit/skills/foo/references/bar.md`相当のパスが対象として認識される場合にブロックする。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "psl-references-md-block"
        self._all_prior_flags(tmp_path, sid)

        target_rel = "agent-toolkit/skills/foo/references/bar.md"
        self._make_target_file(tmp_path, target_rel, lines=210)

        content = f"## 変更内容\n\n- `{target_rel}` を変更する\n\n## 調査結果\n\nなし\n"
        result = self._run_with_cwd(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
                "permission_mode": "default",
            },
            cwd=tmp_path,
            env_overrides=env,
        )
        assert result.returncode == 0
        assert "bar.md" in result.stderr
        assert "[auto-generated: agent-toolkit/pretooluse][warn]" in result.stderr

    def test_blocks_when_agents_definition_md_wc_l_not_recorded(self, tmp_path: pathlib.Path):
        """`agent-toolkit/agents/foo.md`相当のパスが対象として認識される場合にブロックする。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "psl-agents-def-block"
        self._all_prior_flags(tmp_path, sid)

        target_rel = "agent-toolkit/agents/foo.md"
        self._make_target_file(tmp_path, target_rel, lines=210)

        content = f"## 変更内容\n\n- `{target_rel}` を変更する\n\n## 調査結果\n\nなし\n"
        result = self._run_with_cwd(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
                "permission_mode": "default",
            },
            cwd=tmp_path,
            env_overrides=env,
        )
        assert result.returncode == 0
        assert "foo.md" in result.stderr
        assert "[auto-generated: agent-toolkit/pretooluse][warn]" in result.stderr

    def test_blocks_when_chezmoi_dot_claude_rules_wc_l_not_recorded(self, tmp_path: pathlib.Path):
        """`.chezmoi-source/dot_claude/rules/foo.md`相当のパスが対象として認識される場合にブロックする。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "psl-chezmoi-rules-block"
        self._all_prior_flags(tmp_path, sid)

        target_rel = ".chezmoi-source/dot_claude/rules/foo.md"
        self._make_target_file(tmp_path, target_rel, lines=210)

        content = f"## 変更内容\n\n- `{target_rel}` を変更する\n\n## 調査結果\n\nなし\n"
        result = self._run_with_cwd(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
                "permission_mode": "default",
            },
            cwd=tmp_path,
            env_overrides=env,
        )
        assert result.returncode == 0
        assert "foo.md" in result.stderr
        assert "[auto-generated: agent-toolkit/pretooluse][warn]" in result.stderr

    def test_passes_when_all_files_have_wc_l_recorded(self, tmp_path: pathlib.Path):
        """`## 変更内容`に複数ファイルが列挙されており全ファイルの行数が`## 調査結果`に記載済みの場合は通過する。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "psl-multi-all-recorded"
        self._all_prior_flags(tmp_path, sid)

        target_rel1 = "agent-toolkit/rules/test-rule1.md"
        target_rel2 = "agent-toolkit/rules/test-rule2.md"
        self._make_target_file(tmp_path, target_rel1, lines=210)
        self._make_target_file(tmp_path, target_rel2, lines=210)

        content = (
            "## 変更履歴\n\nx\n\n"
            "## 背景\n\nx\n\n"
            "## 対応方針\n\nx\n\n"
            "## 調査結果\n\n"
            "test-rule1.md は 210 行。\ntest-rule2.md は 210 行。\n\n"
            "## 変更内容\n\n"
            f"- `{target_rel1}` を変更する\n"
            f"- `{target_rel2}` を変更する\n\n"
            "## 実行方法\n\nx\n\n"
            "## 進捗ログ\n\nx\n\n"
            "## 計画ファイル（本ファイル）のパス\n\nx\n"
        )
        result = self._run_with_cwd(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
                "permission_mode": "default",
            },
            cwd=tmp_path,
            env_overrides=env,
        )
        assert result.returncode == 0

    def test_blocks_when_first_file_recorded_but_second_missing(self, tmp_path: pathlib.Path):
        """`## 変更内容`に複数ファイルが列挙され先頭ファイルは記載済みでも後続ファイルが未記載の場合はブロックする。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "psl-multi-second-missing"
        self._all_prior_flags(tmp_path, sid)

        target_rel1 = "agent-toolkit/rules/test-rule1.md"
        target_rel2 = "agent-toolkit/rules/test-rule2.md"
        self._make_target_file(tmp_path, target_rel1, lines=210)
        self._make_target_file(tmp_path, target_rel2, lines=210)

        content = (
            "## 変更内容\n\n"
            f"- `{target_rel1}` を変更する\n"
            f"- `{target_rel2}` を変更する\n\n"
            "## 調査結果\n\n"
            "test-rule1.md は 210 行。\n"
        )
        result = self._run_with_cwd(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
                "permission_mode": "default",
            },
            cwd=tmp_path,
            env_overrides=env,
        )
        assert result.returncode == 0
        assert "test-rule2.md" in result.stderr
        assert "[auto-generated: agent-toolkit/pretooluse][warn]" in result.stderr


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
    """plugin.jsonとmarketplace.jsonのSSOT整合性。

    version / description / nameを2箇所で重複管理しているため、
    片方だけ更新して配布されない事故を防ぐためのハードチェック。
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


class TestAgentDocTargetPatternsSsot:
    """agent-doc-validator の対象ファイル群列挙 SSOT 整合性検査。

    pretooluse.py の _AGENT_DOC_TARGET_FILE_PATTERNS を SSOT とし、
    agents/agent-doc-validator.md および
    skills/plan-mode/references/integrity-checks.md の本文にすべての
    対象パス文字列が現れることを保証する。
    """

    _REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
    _AGENT_DOC_VALIDATOR_MD = _REPO_ROOT / "agent-toolkit" / "agents" / "agent-doc-validator.md"
    _INTEGRITY_CHECKS_MD = _REPO_ROOT / "agent-toolkit" / "skills" / "plan-mode" / "references" / "integrity-checks.md"

    @classmethod
    def _expected_path_snippets(cls) -> list[str]:
        """_AGENT_DOC_TARGET_FILE_PATTERNS からエスケープを解いてパス文字列を取得する。"""
        # 実装SSOTを直接参照する
        from pretooluse import _AGENT_DOC_TARGET_FILE_PATTERNS  # noqa: C0415  # pylint: disable=import-outside-toplevel

        snippets: list[str] = []
        for pat in _AGENT_DOC_TARGET_FILE_PATTERNS:
            src = pat.pattern
            # 正規表現エスケープ(\.)をリテラル(.)へ復元する
            snippets.append(src.replace("\\.", "."))
        return snippets

    def _extract_h2_section(self, content: str, heading: str) -> str:
        """指定H2見出し配下の本文を抽出する（次のH2または末尾まで）。"""
        marker = f"## {heading}"
        idx = content.find(marker)
        assert idx != -1, f"見出し '{heading}' が見つからない"
        after = content[idx + len(marker) :]
        # 次のH2見出しを検索
        next_h2 = after.find("\n## ")
        return after if next_h2 == -1 else after[:next_h2]

    def test_frontmatter_description_contains_all_patterns(self):
        """agent-doc-validator.md frontmatter description が全パターンを含むこと。"""
        content = self._AGENT_DOC_VALIDATOR_MD.read_text(encoding="utf-8")
        # frontmatterは先頭の --- で囲まれた領域
        assert content.startswith("---\n")
        end = content.find("\n---\n", 4)
        assert end != -1
        frontmatter = content[4:end]
        for snippet in self._expected_path_snippets():
            assert snippet in frontmatter, f"frontmatter description に {snippet!r} が欠落"

    def test_body_scope_section_contains_all_patterns(self):
        """agent-doc-validator.md 本文「適用範囲」節が全パターンを含むこと。"""
        content = self._AGENT_DOC_VALIDATOR_MD.read_text(encoding="utf-8")
        section = self._extract_h2_section(content, "適用範囲")
        for snippet in self._expected_path_snippets():
            assert snippet in section, f"『適用範囲』節に {snippet!r} が欠落"

    def test_integrity_checks_condition_section_contains_all_patterns(self):
        """integrity-checks.md の条件付き起動記述部分が全パターンを含むこと。"""
        content = self._INTEGRITY_CHECKS_MD.read_text(encoding="utf-8")
        # `agent-doc-validator` を含む節を検索し、その前後の当該記述行を確認
        assert "agent-doc-validator" in content
        for snippet in self._expected_path_snippets():
            assert snippet in content, f"integrity-checks.md に {snippet!r} が欠落"

    def test_integrity_checks_bypass_section_contains_all_patterns(self):
        """integrity-checks.md の工程7バイパスファイル群記述が全パターンを含むこと。"""
        content = self._INTEGRITY_CHECKS_MD.read_text(encoding="utf-8")
        # 上のテストで全content確認済みだが、ファイル群の記述部分の存在自体を検証
        assert "工程7" in content or "バイパス" in content or "該当ファイル群" in content
        for snippet in self._expected_path_snippets():
            assert snippet in content, f"integrity-checks.md に {snippet!r} が欠落"


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
        # tracked .mdを作業ツリー上でのみ変更（indexには反映しない）
        (repo / "doc.md").write_text("# v2")
        # 一括ステージ警告が別途発火しないようsession_edited_filesに含める。
        _write_session_state(tmp_path, "commit-all", {"session_edited_files": ["doc.md"]})
        result = self._invoke("git commit -am 'update'", "commit-all", state_dir, cwd=str(repo))
        assert result.returncode == 0
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
        assert "一括ステージ" in ctx
        return ctx

    def _assert_no_warn(self, result: subprocess.CompletedProcess[str]) -> None:
        assert result.returncode == 0
        data = self._extract_json(result.stdout)
        if data is None:
            return
        ctx = data.get("hookSpecificOutput", {}).get("additionalContext", "")
        assert "一括ステージ" not in ctx

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


class TestCodexReviewNotRead:
    """codex-review.md未読時のブロック。"""

    @pytest.fixture(name="state_dir")
    def _state_dir(self, tmp_path: pathlib.Path) -> dict[str, str]:
        return _plan_file_state_env(tmp_path)

    _write_state = staticmethod(_write_session_state)

    def test_blocked_when_not_read(self, state_dir: dict[str, str]):
        """codex-review.md未読時にcodex MCP呼び出しがブロックされる。"""
        result = _run(
            {"tool_name": "mcp__codex__codex", "tool_input": {"prompt": "hello"}, "session_id": "no-review"},
            env_overrides=state_dir,
        )
        assert result.returncode == 2
        assert "codex-review.md" in result.stderr

    def test_allowed_when_read(self, state_dir: dict[str, str], tmp_path: pathlib.Path):
        """codex-review.md読み込み済みの場合は強制承認して通過する。"""
        self._write_state(tmp_path, "with-review", {"codex_review_read": True})
        result = _run(
            {
                "tool_name": "mcp__codex__codex",
                "tool_input": {"prompt": "hello", "sandbox": "danger-full-access"},
                "session_id": "with-review",
            },
            env_overrides=state_dir,
        )
        assert result.returncode == 0
        out = json.loads(result.stdout)
        assert out["hookSpecificOutput"]["permissionDecision"] == "allow"
        assert "updatedInput" not in out["hookSpecificOutput"]

    def test_allowed_when_codex_impl_invoked_without_review_read(self, state_dir: dict[str, str], tmp_path: pathlib.Path):
        """`codex_impl_invoked`が真の場合、codex-review.md未読でもブロックせず通過する。"""
        self._write_state(tmp_path, "impl-invoked", {"codex_impl_invoked": True})
        result = _run(
            {
                "tool_name": "mcp__codex__codex",
                "tool_input": {"prompt": "hello", "sandbox": "danger-full-access"},
                "session_id": "impl-invoked",
            },
            env_overrides=state_dir,
        )
        assert result.returncode == 0
        out = json.loads(result.stdout)
        assert out["hookSpecificOutput"]["permissionDecision"] == "allow"

    def test_blocked_when_codex_impl_not_invoked_and_review_unread(self, state_dir: dict[str, str], tmp_path: pathlib.Path):
        """`codex_impl_invoked`が偽に設定済み＋codex-review.md未読の場合は従来どおりブロックされる。"""
        self._write_state(tmp_path, "impl-not-invoked", {"codex_impl_invoked": False})
        result = _run(
            {
                "tool_name": "mcp__codex__codex",
                "tool_input": {"prompt": "hello"},
                "session_id": "impl-not-invoked",
            },
            env_overrides=state_dir,
        )
        assert result.returncode == 2
        assert "codex-review.md" in result.stderr


class TestCodexMcpSandbox:
    """codex MCP sandbox自動修正。"""

    @pytest.fixture(name="state_dir")
    def _state_dir(self, tmp_path: pathlib.Path) -> dict[str, str]:
        return _plan_file_state_env(tmp_path)

    _write_state = staticmethod(_write_session_state)

    def test_sandbox_auto_fix(self, state_dir: dict[str, str], tmp_path: pathlib.Path):
        """sandboxが未指定の場合、danger-full-accessに自動修正される。"""
        self._write_state(tmp_path, "fix1", {"codex_review_read": True})
        result = _run(
            {"tool_name": "mcp__codex__codex", "tool_input": {"prompt": "hello"}, "session_id": "fix1"},
            env_overrides=state_dir,
        )
        assert result.returncode == 0
        out = json.loads(result.stdout)
        updated = out["hookSpecificOutput"]["updatedInput"]
        assert updated["sandbox"] == "danger-full-access"
        assert updated["prompt"] == "hello"
        assert "自動修正" in out["systemMessage"]

    def test_sandbox_wrong_value_auto_fix(self, state_dir: dict[str, str], tmp_path: pathlib.Path):
        """sandboxが不正な値の場合も自動修正される。"""
        self._write_state(tmp_path, "fix2", {"codex_review_read": True})
        result = _run(
            {
                "tool_name": "mcp__codex__codex",
                "tool_input": {"prompt": "hello", "sandbox": "network-only"},
                "session_id": "fix2",
            },
            env_overrides=state_dir,
        )
        assert result.returncode == 0
        out = json.loads(result.stdout)
        updated = out["hookSpecificOutput"]["updatedInput"]
        assert updated["sandbox"] == "danger-full-access"
        assert "自動修正" in out["systemMessage"]

    def test_sandbox_correct_no_fix(self, state_dir: dict[str, str], tmp_path: pathlib.Path):
        """sandboxが既にdanger-full-accessの場合は修正せず強制承認のみ返す。"""
        self._write_state(tmp_path, "fix3", {"codex_review_read": True})
        result = _run(
            {
                "tool_name": "mcp__codex__codex",
                "tool_input": {"prompt": "hello", "sandbox": "danger-full-access"},
                "session_id": "fix3",
            },
            env_overrides=state_dir,
        )
        assert result.returncode == 0
        out = json.loads(result.stdout)
        assert out["hookSpecificOutput"]["permissionDecision"] == "allow"
        assert "updatedInput" not in out["hookSpecificOutput"]


class TestCodexMcpReply:
    """mcp__codex__codex-reply強制承認。"""

    @pytest.fixture(name="state_dir")
    def _state_dir(self, tmp_path: pathlib.Path) -> dict[str, str]:
        return _plan_file_state_env(tmp_path)

    def test_reply_auto_approved(self, state_dir: dict[str, str]):
        """codex-reply呼び出しは無条件で強制承認される。"""
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
        self._write_state(tmp_path, "codex-lang", {"codex_review_read": True})
        transcript = self._write_transcript(tmp_path, "A" * 100)
        result = _run(
            {
                "tool_name": "mcp__codex__codex",
                "tool_input": {"prompt": "hello", "sandbox": "danger-full-access"},
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


_SCOPE_ESCALATION_INPUTS = _load_scope_escalation_inputs()


class TestAskUserQuestionScopeEscalationCheck:
    """AskUserQuestion向け縮退誘発フレーズ検出ブロック。

    フレーズ本文の代わりにパターンマッチ最小単位（正規表現の最短一致）を
    隔離フィクスチャから動的に読み込む（`agent-toolkit:agent-standards`「コンテキスト汚染の回避」節）。
    """

    @pytest.mark.parametrize(("text", "category"), _SCOPE_ESCALATION_INPUTS)
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
        assert "縮退誘発フレーズ" in result.stderr
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
        assert "縮退誘発フレーズ" not in result.stderr

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
                                {"label": "本セッションで完遂困難", "description": "do all at once"},
                                {"label": "ok", "description": "ok"},
                            ],
                        }
                    ],
                },
            }
        )
        assert result.returncode == 2
        assert "縮退誘発フレーズ" in result.stderr

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
        assert "縮退誘発フレーズ" in result.stderr

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


class TestScopeEscalationInDocEditCheck:
    """対象ドキュメント編集時のscope-escalationフレーズ転記検出ブロック。

    対象は`agent-toolkit/rules/`配下と`agent-toolkit/skills/**/SKILL.md`（`references/`配下を除く）。
    フレーズ本文は隔離フィクスチャから動的に読み込む。
    """

    @pytest.mark.parametrize(("text", "category"), _SCOPE_ESCALATION_INPUTS)
    def test_write_blocks_on_target_doc(self, text: str, category: str):
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": "agent-toolkit/rules/01-agent.md",
                    "content": f"# header\n\n{text}\n",
                },
            }
        )
        assert result.returncode == 2
        assert "scope-escalation" in result.stderr
        assert category in result.stderr
        # フレーズ本文は通知へ転記しない（コンテキスト汚染防止）
        assert text not in result.stderr

    @pytest.mark.parametrize(("text", "category"), _SCOPE_ESCALATION_INPUTS)
    def test_edit_blocks_on_target_doc(self, text: str, category: str):
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": "agent-toolkit/skills/agent-standards/SKILL.md",
                    "old_string": "old",
                    "new_string": f"old {text}",
                },
            }
        )
        assert result.returncode == 2
        assert category in result.stderr
        assert text not in result.stderr

    @pytest.mark.parametrize(("text", "category"), _SCOPE_ESCALATION_INPUTS)
    def test_multiedit_blocks_on_target_doc(self, text: str, category: str):
        result = _run(
            {
                "tool_name": "MultiEdit",
                "tool_input": {
                    "file_path": "agent-toolkit/skills/x/SKILL.md",
                    "edits": [
                        {"old_string": "a", "new_string": "b"},
                        {"old_string": "c", "new_string": f"c {text}"},
                    ],
                },
            }
        )
        assert result.returncode == 2
        assert category in result.stderr
        assert text not in result.stderr

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
        assert result.returncode == 2

    def test_old_string_not_inspected_on_target(self):
        """対象ファイルでも`old_string`内のフレーズは検出しない（既存違反の修正を妨げない）。

        `new_string`にはクリーンな置換後文面を配置し、フレーズが`old_string`にのみあることで
        通過判定が`old_string`不検査に由来することを確認する。
        """
        text = _SCOPE_ESCALATION_INPUTS[0][0]
        clean_replacement = "通常の置換後文面"
        # 置換後文面にフレーズが残っていないことを確認（テスト前提の自己検査）
        for input_text, _ in _SCOPE_ESCALATION_INPUTS:
            assert input_text not in clean_replacement
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": "agent-toolkit/rules/01-agent.md",
                    "old_string": text,
                    "new_string": clean_replacement,
                },
            }
        )
        assert result.returncode == 0

    @pytest.mark.parametrize(
        "file_path",
        [
            "agent-toolkit/agents/plan-implementer.md",
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
        assert result.returncode == 2

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
                "plan_file_guidelines_read": True,
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
        assert result.returncode == 2
        assert "scope-escalation" in result.stderr
        assert category in result.stderr

    def test_mitigation_in_adoption_references_apply_feedback(self):
        """`mitigation-in-adoption`カテゴリはapply-feedback SKILL.mdの節を参照する。"""
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
        assert result.returncode == 2
        assert "apply-feedback/SKILL.md" in result.stderr
        assert "採用時の反映内容の縮小禁止" in result.stderr

    def test_other_category_references_01_agent_md(self):
        """`mitigation-in-adoption`以外のカテゴリは01-agent.mdの節を参照する。"""
        text = next(t for t, c in _SCOPE_ESCALATION_INPUTS if c == "workload")
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": "agent-toolkit/rules/01-agent.md",
                    "content": f"# header\n\n{text}\n",
                },
            }
        )
        assert result.returncode == 2
        assert "agent-toolkit/rules/01-agent.md" in result.stderr
        assert "01-01-agent.md" not in result.stderr
        assert "セッション分割・別計画化は禁止する" in result.stderr

    @pytest.mark.parametrize(("text", "category"), _SCOPE_ESCALATION_INPUTS)
    def test_edit_preserves_existing_phrase_in_unchanged_region(self, text: str, category: str):
        """Edit時、`old_string`と`new_string`双方に同一フレーズが既存文字列として保持される場合は通過する。

        フレーズ出現回数の増加比較方式ではold=new同数のため検出されない。
        旧仕様（new_string全文検査）では検出ブロックされる回帰ケース。
        """
        del category  # 未使用
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": "agent-toolkit/rules/01-agent.md",
                    "old_string": f"既存記述。{text}。末尾A",
                    "new_string": f"既存記述。{text}。末尾B",
                },
            }
        )
        assert result.returncode == 0

    @pytest.mark.parametrize(("text", "category"), _SCOPE_ESCALATION_INPUTS)
    def test_multiedit_preserves_existing_phrase_in_unchanged_region(self, text: str, category: str):
        """MultiEditでも既存文字列の保持部分のフレーズは検査対象外として通過する。"""
        del category  # 未使用
        result = _run(
            {
                "tool_name": "MultiEdit",
                "tool_input": {
                    "file_path": "agent-toolkit/skills/x/SKILL.md",
                    "edits": [
                        {
                            "old_string": f"前文。{text}。末尾A",
                            "new_string": f"前文。{text}。末尾B",
                        }
                    ],
                },
            }
        )
        assert result.returncode == 0

    @pytest.mark.parametrize(("text", "category"), _SCOPE_ESCALATION_INPUTS)
    def test_edit_detects_phrase_addition_in_new_string(self, text: str, category: str):
        """Edit時に純粋追加された新規フレーズはブロックする。

        既存に0件、追加で1件出現する純粋追加パターンの陽性テスト。
        """
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": "agent-toolkit/rules/01-agent.md",
                    "old_string": "既存記述のみ",
                    "new_string": f"既存記述のみ。{text}",
                },
            }
        )
        assert result.returncode == 2
        assert category in result.stderr

    @pytest.mark.parametrize(("text", "category"), _SCOPE_ESCALATION_INPUTS)
    def test_multiedit_detects_phrase_addition_in_new_string(self, text: str, category: str):
        """MultiEdit時に純粋追加された新規フレーズはブロックする。

        edits内のold_stringにフレーズなし・new_stringにフレーズありの陽性テスト。
        """
        result = _run(
            {
                "tool_name": "MultiEdit",
                "tool_input": {
                    "file_path": "agent-toolkit/skills/x/SKILL.md",
                    "edits": [
                        {
                            "old_string": "既存記述のみ",
                            "new_string": f"既存記述のみ。{text}",
                        }
                    ],
                },
            }
        )
        assert result.returncode == 2
        assert category in result.stderr


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


class TestWorkaroundMemoGate:
    """plan fileのWrite時、ワークアラウンド語検出に伴う事前検討メモの未整備ブロック。"""

    _state_env = staticmethod(_plan_file_state_env)
    _make_plan = staticmethod(_make_plan_file)
    # メモパスは計画ファイル自身のstemから導出するため、`_make_plan`が生成する
    # 既定のplan file名（`test.md`）のstemに合わせる。
    # フィードバック起因かどうかを問わず全ての計画ファイルへ一律適用できるため、
    # 複数inbox問題（複数の採否確定ファイルが1計画ファイルに列挙されるケース）を検証するテストは不要になった。
    _PLAN_STEM = "test"

    @classmethod
    def _prior_flags(cls, tmp_path: pathlib.Path, sid: str) -> None:
        _write_session_state(
            tmp_path,
            sid,
            {
                "plan_mode_skill_invoked": True,
                "textlint_violations_read": True,
                "plan_file_guidelines_read": True,
            },
        )

    @classmethod
    def _changes_body_with_workaround(cls) -> str:
        return "### 対象ファイル一覧\n\n- フォールバックとして代替経路を追加する\n"

    @staticmethod
    def _content(changes_body: str) -> str:
        return (
            "# タイトル\n\n"
            "## 変更履歴\n\nx\n\n"
            "## 背景\n\nx\n\n"
            "## 対応方針\n\nx\n\n"
            "## 調査結果\n\nx\n\n"
            f"## 変更内容\n\n{changes_body}\n\n"
            "## 実行方法\n\nx\n\n"
            "## 進捗ログ\n\nx\n\n"
            "## 計画ファイル（本ファイル）のパス\n\nx\n"
        )

    def test_no_workaround_terms_passes(self, tmp_path: pathlib.Path):
        """ワークアラウンド語を含まない場合は通過する。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "workaround-clean"
        self._prior_flags(tmp_path, sid)
        content = self._content("### 対象ファイル一覧\n\nx\n")
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

    def test_missing_memo_blocks(self, tmp_path: pathlib.Path):
        """ワークアラウンド語検出時にメモファイルが不在の場合はブロックする。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "workaround-missing"
        self._prior_flags(tmp_path, sid)
        content = self._content(self._changes_body_with_workaround())
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

    @classmethod
    def _memo_path(cls, home: pathlib.Path) -> pathlib.Path:
        return home / ".claude" / "plans" / f"{cls._PLAN_STEM}-workaround-check.md"

    def test_incomplete_memo_blocks(self, tmp_path: pathlib.Path):
        """メモファイルは存在するが必須項目の記入漏れがある場合はブロックする。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "workaround-incomplete"
        self._prior_flags(tmp_path, sid)
        self._memo_path(home).write_text("根本原因の候補: 未整理\n", encoding="utf-8")
        content = self._content(self._changes_body_with_workaround())
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

    def test_complete_memo_passes(self, tmp_path: pathlib.Path):
        """メモファイルに必須3項目が記入済みの場合は通過する。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "workaround-complete"
        self._prior_flags(tmp_path, sid)
        self._memo_path(home).write_text(
            "根本原因の候補: A\n根本対応が成立するか: 否\n成立しない場合の理由: 外部制約\n",
            encoding="utf-8",
        )
        content = self._content(self._changes_body_with_workaround())
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

    def test_empty_body_items_block(self, tmp_path: pathlib.Path):
        """必須項目名は全て存在するが本文が空欄の場合はブロックする。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "workaround-empty"
        self._prior_flags(tmp_path, sid)
        self._memo_path(home).write_text(
            "根本原因の候補:\n根本対応が成立するか:\n成立しない場合の理由:\n",
            encoding="utf-8",
        )
        content = self._content(self._changes_body_with_workaround())
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

    def test_body_on_next_line_passes(self, tmp_path: pathlib.Path):
        """項目名の次行に本文がある場合は通過する。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "workaround-next-line"
        self._prior_flags(tmp_path, sid)
        self._memo_path(home).write_text(
            "根本原因の候補\n候補A\n根本対応が成立するか\n否\n成立しない場合の理由\n外部制約\n",
            encoding="utf-8",
        )
        content = self._content(self._changes_body_with_workaround())
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


_PROCESS7_FLAGS = (
    "plan_reviewer_invoked",
    "naive_executor_invoked",
    "plan_impl_reviewer_invoked",
    "codex_review_invoked",
)


def _process7_env(tmp_path: pathlib.Path) -> dict[str, str]:
    return {"TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path)}


class TestProcess7CompletionCheck:
    """ExitPlanMode / `agent-toolkit:plan-impl`起動時の工程7完了未達ブロック。"""

    def test_all_flags_set_passes(self, tmp_path: pathlib.Path):
        """4フラグ全て真の場合はExitPlanModeを通過する。"""
        sid = "process7-all-set"
        state = {"plan_mode_skill_invoked": True}
        state.update({flag: True for flag in _PROCESS7_FLAGS})
        _write_session_state(tmp_path, sid, state)
        result = _run(
            {"tool_name": "ExitPlanMode", "tool_input": {}, "session_id": sid, "permission_mode": "plan"},
            env_overrides=_process7_env(tmp_path),
        )
        assert result.returncode == 0

    @pytest.mark.parametrize("missing_flag", _PROCESS7_FLAGS)
    def test_missing_flag_blocks(self, tmp_path: pathlib.Path, missing_flag: str):
        """4フラグのいずれか1つでも偽の場合はExitPlanModeをブロックする。"""
        sid = f"process7-missing-{missing_flag}"
        state = {"plan_mode_skill_invoked": True}
        state.update({flag: (flag != missing_flag) for flag in _PROCESS7_FLAGS})
        _write_session_state(tmp_path, sid, state)
        result = _run(
            {"tool_name": "ExitPlanMode", "tool_input": {}, "session_id": sid, "permission_mode": "plan"},
            env_overrides=_process7_env(tmp_path),
        )
        assert result.returncode == 2
        assert missing_flag in result.stderr
        assert "integrity-checks.md" in result.stderr

    def test_no_plan_mode_context_passes(self, tmp_path: pathlib.Path):
        """`plan_mode_skill_invoked`が偽の場合は検査対象外として通過する。"""
        sid = "process7-no-plan-mode"
        _write_session_state(tmp_path, sid, {})
        result = _run(
            {"tool_name": "ExitPlanMode", "tool_input": {}, "session_id": sid, "permission_mode": "plan"},
            env_overrides=_process7_env(tmp_path),
        )
        assert result.returncode == 0

    def test_plan_impl_skill_also_checked(self, tmp_path: pathlib.Path):
        """`agent-toolkit:plan-impl`のSkill起動も同様に工程7完了未達をブロックする。"""
        sid = "process7-plan-impl-skill"
        state = {"plan_mode_skill_invoked": True}
        state.update({flag: False for flag in _PROCESS7_FLAGS})
        _write_session_state(tmp_path, sid, state)
        result = _run(
            {
                "tool_name": "Skill",
                "tool_input": {"skill": "agent-toolkit:plan-impl"},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=_process7_env(tmp_path),
        )
        assert result.returncode == 2

    def test_agent_doc_validator_required_when_target_file_listed(self, tmp_path: pathlib.Path):
        """対象ファイル一覧にコーディングエージェント向け文書が含まれる場合、agent_doc_validator_invokedも必須化する。"""
        plan_path = tmp_path / "plan.md"
        plan_path.write_text(
            "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `agent-toolkit/agents/agent-doc-validator.md`\n",
            encoding="utf-8",
        )
        sid = "process7-agent-doc-validator-required"
        state = {"plan_mode_skill_invoked": True, "current_plan_file_path": str(plan_path)}
        state.update({flag: True for flag in _PROCESS7_FLAGS})
        _write_session_state(tmp_path, sid, state)
        result = _run(
            {"tool_name": "ExitPlanMode", "tool_input": {}, "session_id": sid, "permission_mode": "plan"},
            env_overrides=_process7_env(tmp_path),
        )
        assert result.returncode == 2
        assert "agent_doc_validator_invoked" in result.stderr

    def test_agent_doc_validator_not_required_when_target_file_absent(self, tmp_path: pathlib.Path):
        """対象ファイル一覧にコーディングエージェント向け文書が含まれない場合、agent_doc_validator_invokedを必須化しない。"""
        plan_path = tmp_path / "plan.md"
        plan_path.write_text(
            "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `pytools/example.py`\n",
            encoding="utf-8",
        )
        sid = "process7-agent-doc-validator-not-required"
        state = {"plan_mode_skill_invoked": True, "current_plan_file_path": str(plan_path)}
        state.update({flag: True for flag in _PROCESS7_FLAGS})
        _write_session_state(tmp_path, sid, state)
        result = _run(
            {"tool_name": "ExitPlanMode", "tool_input": {}, "session_id": sid, "permission_mode": "plan"},
            env_overrides=_process7_env(tmp_path),
        )
        assert result.returncode == 0

    def test_agent_doc_validator_required_via_fallback_when_target_list_section_missing(self, tmp_path: pathlib.Path):
        """`### 対象ファイル一覧`節が無い場合は計画ファイル全文を走査対象とし、フォールバック判定で必須化する。"""
        plan_path = tmp_path / "plan.md"
        plan_path.write_text(
            "## 変更内容\n\n`agent-toolkit/rules/01-agent.md`の該当節へ改訂内容を追記する。\n",
            encoding="utf-8",
        )
        sid = "process7-agent-doc-validator-fallback"
        state = {"plan_mode_skill_invoked": True, "current_plan_file_path": str(plan_path)}
        state.update({flag: True for flag in _PROCESS7_FLAGS})
        _write_session_state(tmp_path, sid, state)
        result = _run(
            {"tool_name": "ExitPlanMode", "tool_input": {}, "session_id": sid, "permission_mode": "plan"},
            env_overrides=_process7_env(tmp_path),
        )
        assert result.returncode == 2
        assert "agent_doc_validator_invoked" in result.stderr

    def test_agent_doc_validator_not_required_when_plan_file_path_missing(self, tmp_path: pathlib.Path):
        """`current_plan_file_path`が存在しないファイルを指す場合は要否判定不能として必須化しない（安全側フォールバック）。"""
        sid = "process7-agent-doc-validator-missing-plan-file"
        state = {
            "plan_mode_skill_invoked": True,
            "current_plan_file_path": str(tmp_path / "does-not-exist.md"),
        }
        state.update({flag: True for flag in _PROCESS7_FLAGS})
        _write_session_state(tmp_path, sid, state)
        result = _run(
            {"tool_name": "ExitPlanMode", "tool_input": {}, "session_id": sid, "permission_mode": "plan"},
            env_overrides=_process7_env(tmp_path),
        )
        assert result.returncode == 0


class TestPlanModeFlagReset:
    """`agent-toolkit:plan-mode`スキル起動時の工程7完了フラグリセット。"""

    def test_flags_reset_on_plan_mode_skill_invoke(self, tmp_path: pathlib.Path):
        """新計画着手時に工程7完了フラグ・`agent_doc_validator_invoked`・`codex_impl_invoked`が偽へリセットされ、
        `current_plan_file_path`が消去される。
        """
        sid = "process7-reset"
        state = {
            "plan_mode_skill_invoked": True,
            "agent_doc_validator_invoked": True,
            "codex_impl_invoked": True,
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
        assert updated["agent_doc_validator_invoked"] is False
        assert updated["codex_impl_invoked"] is False
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
                "plan_file_guidelines_read": True,
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
        """Edit/MultiEditはH2節順検査の判定に入るがcontentフィールドがないため通過する。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": str(plan),
                    "old_string": "x",
                    "new_string": "y",
                },
                "session_id": "h2order-edit",
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        # 別checkでブロックされ得るが、本テストの関心はH2節順違反メッセージが出ないこと
        assert "H2 section order" not in result.stderr

    def test_allows_multi_edit_tool(self, tmp_path: pathlib.Path):
        """MultiEditはH2節順検査の判定に入るがcontentフィールドがないため通過する。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        result = _run(
            {
                "tool_name": "MultiEdit",
                "tool_input": {
                    "file_path": str(plan),
                    "edits": [{"old_string": "x", "new_string": "y"}],
                },
                "session_id": "h2order-multiedit",
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        # 別checkでブロックされ得るが、本テストの関心はH2節順違反メッセージが出ないこと
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


def _absnum_state_env(tmp_path: pathlib.Path, home_dir: pathlib.Path) -> dict[str, str]:
    """絶対行番号検査テスト用の環境変数。事前lint検査はバイパスする。"""
    return _plan_file_state_env(tmp_path, home_dir)


def _absnum_prior_flags(state_dir: pathlib.Path, sid: str) -> None:
    """絶対行番号検査の前提となるセッション状態フラグを書き込む。"""
    _write_session_state(
        state_dir,
        sid,
        {
            "plan_mode_skill_invoked": True,
            "textlint_violations_read": True,
            "plan_file_guidelines_read": True,
        },
    )


# 絶対行番号検査用の正規計画テンプレート。`## 変更内容`配下に違反トークンを置けるよう余白を確保する。
_ABSNUM_BASE_PLAN = (
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


class TestCheckPlanFileAbsoluteLineNumbers:
    """plan file Write/Edit/MultiEdit時の絶対行番号トークン直書きブロック検査。

    posttooluseから移管された既存挙動の完全互換を維持する:
    - 検出パターン: L\\d+ / N行目 / N-N行 / NからN行
    - 許容: `## 調査結果`配下かつ`<!-- line-ref-ok -->`マーカー付与行のみ
    - 除外領域: コードフェンス内 / 複数行HTMLコメント内 / フロントマター内
    - 単一行HTMLコメント内は検出対象（既存仕様継承）
    - 上限5件・「; and N more」省略表記
    """

    _state_env = staticmethod(_absnum_state_env)
    _make_plan = staticmethod(_make_plan_file)
    _prior_flags = staticmethod(_absnum_prior_flags)

    _LINE_TOKEN = "現行" + "L" + "66"
    _LINE_RANGE_HYPHEN = "148" + "-" + "151行"
    _LINE_RANGE_KARA = "148から151行"
    _LINE_NTH = "100行目"
    _ALPHA_PREFIX_TOKEN = "Graph" + "QL2"
    _COUNT_EXPR = "3件・5項目"

    @pytest.mark.parametrize(
        ("session_id", "token", "expected_match"),
        [
            ("absnum-token", _LINE_TOKEN, "L" + "66"),
            ("absnum-range", _LINE_RANGE_HYPHEN, "148" + "-" + "151行"),
            ("absnum-kara", _LINE_RANGE_KARA, "148から151行"),
            ("absnum-nth", _LINE_NTH, "100行目"),
        ],
    )
    def test_write_with_absolute_line_number_blocks(
        self, tmp_path: pathlib.Path, session_id: str, token: str, expected_match: str
    ):
        """`## 変更内容`配下の各種行番号トークンがWriteでブロックされる。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        self._prior_flags(tmp_path, session_id)
        content = _ABSNUM_BASE_PLAN.format(body=f"- {token}")
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": session_id,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        assert "absolute line-number references" in result.stderr
        assert repr(expected_match) in result.stderr

    def test_write_in_research_section_with_marker_is_allowed(self, tmp_path: pathlib.Path):
        """`## 調査結果`配下でマーカー付き行の行番号トークンはブロックされない。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "absnum-research-marker"
        self._prior_flags(tmp_path, sid)
        content = _ABSNUM_BASE_PLAN.replace(
            "## 調査結果\n\nx\n", f"## 調査結果\n\n- {self._LINE_TOKEN} <!-- line-ref-ok -->\n"
        ).format(body="x")
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

    def test_write_in_research_section_without_marker_blocks(self, tmp_path: pathlib.Path):
        """`## 調査結果`配下でもマーカー無しの行番号トークンはブロックされる。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "absnum-research-nomarker"
        self._prior_flags(tmp_path, sid)
        content = _ABSNUM_BASE_PLAN.replace("## 調査結果\n\nx\n", f"## 調査結果\n\n- {self._LINE_TOKEN}\n").format(body="x")
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
        assert "absolute line-number references" in result.stderr

    def test_write_outside_research_section_with_marker_blocks(self, tmp_path: pathlib.Path):
        """`## 調査結果`外の節ではマーカー付与でも行番号トークンはブロックされる。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "absnum-outside-marker"
        self._prior_flags(tmp_path, sid)
        content = _ABSNUM_BASE_PLAN.format(body=f"- {self._LINE_TOKEN} <!-- line-ref-ok -->")
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
        assert "absolute line-number references" in result.stderr

    def test_write_inside_code_fence_is_allowed(self, tmp_path: pathlib.Path):
        """コードフェンス内の行番号トークンはブロックされない。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "absnum-fence"
        self._prior_flags(tmp_path, sid)
        body = f"```text\n{self._LINE_TOKEN}\n```"
        content = _ABSNUM_BASE_PLAN.format(body=body)
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

    def test_write_inside_multiline_html_comment_is_allowed(self, tmp_path: pathlib.Path):
        """複数行HTMLコメント内の行番号トークンはブロックされない。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "absnum-multicomment"
        self._prior_flags(tmp_path, sid)
        body = f"<!--\n{self._LINE_TOKEN}\n-->"
        content = _ABSNUM_BASE_PLAN.format(body=body)
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

    def test_write_inside_single_line_html_comment_is_blocked(self, tmp_path: pathlib.Path):
        """単一行HTMLコメント内の行番号トークンはブロックされる（既存挙動継承）。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "absnum-singlecomment"
        self._prior_flags(tmp_path, sid)
        body = f"<!-- {self._LINE_TOKEN} -->"
        content = _ABSNUM_BASE_PLAN.format(body=body)
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
        assert "absolute line-number references" in result.stderr

    def test_alpha_prefix_is_not_false_positive(self, tmp_path: pathlib.Path):
        """英字接頭のトークン（`GraphQL2`等）はブロックされない（負の後読み）。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "absnum-alpha"
        self._prior_flags(tmp_path, sid)
        content = _ABSNUM_BASE_PLAN.format(body=f"- {self._ALPHA_PREFIX_TOKEN}")
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

    def test_count_expression_is_not_false_positive(self, tmp_path: pathlib.Path):
        """件数表現はパターン対象外でブロックされない。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "absnum-count"
        self._prior_flags(tmp_path, sid)
        content = _ABSNUM_BASE_PLAN.format(body=f"- {self._COUNT_EXPR}")
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

    def test_frontmatter_token_does_not_affect_body_check(self, tmp_path: pathlib.Path):
        """フロントマター内の行番号トークンは本文の検出に影響しない。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "absnum-frontmatter"
        self._prior_flags(tmp_path, sid)
        prefix = f"---\nfm: {self._LINE_TOKEN}\n---\n\n"
        content = prefix + _ABSNUM_BASE_PLAN.format(body="x")
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

    def test_truncates_at_five_matches(self, tmp_path: pathlib.Path):
        """6件以上の行番号トークンを含む場合、最大5件まで列挙され`and N more`表記が付く。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "absnum-truncate"
        self._prior_flags(tmp_path, sid)
        tokens = "\n".join(f"- {self._LINE_TOKEN}" for _ in range(6))
        content = _ABSNUM_BASE_PLAN.format(body=tokens)
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
        assert result.stderr.count("line ") == 5
        assert "and 1 more." in result.stderr

    def test_edit_introduces_violation_blocks(self, tmp_path: pathlib.Path):
        """Edit適用後のcontentに違反が混入する場合はブロックされる。"""
        home = tmp_path / "home"
        plans_dir = home / ".claude" / "plans"
        plans_dir.mkdir(parents=True, exist_ok=True)
        plan = plans_dir / "test.md"
        plan.write_text(_ABSNUM_BASE_PLAN.format(body="placeholder-content"), encoding="utf-8")
        env = self._state_env(tmp_path, home)
        sid = "absnum-edit"
        self._prior_flags(tmp_path, sid)
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": str(plan),
                    "old_string": "placeholder-content",
                    "new_string": f"changed-{self._LINE_TOKEN}",
                },
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        assert "absolute line-number references" in result.stderr

    def test_multiedit_introduces_violation_blocks(self, tmp_path: pathlib.Path):
        """MultiEdit適用後のcontentに違反が混入する場合はブロックされる。"""
        home = tmp_path / "home"
        plans_dir = home / ".claude" / "plans"
        plans_dir.mkdir(parents=True, exist_ok=True)
        plan = plans_dir / "test.md"
        plan.write_text(_ABSNUM_BASE_PLAN.format(body="placeholder-content"), encoding="utf-8")
        env = self._state_env(tmp_path, home)
        sid = "absnum-multiedit"
        self._prior_flags(tmp_path, sid)
        result = _run(
            {
                "tool_name": "MultiEdit",
                "tool_input": {
                    "file_path": str(plan),
                    "edits": [
                        {"old_string": "placeholder-content", "new_string": f"changed-{self._LINE_TOKEN}"},
                    ],
                },
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        assert "absolute line-number references" in result.stderr

    def test_non_plan_file_is_skipped(self, tmp_path: pathlib.Path):
        """plan fileでないパスへの書き込みは検査対象外。"""
        env = self._state_env(tmp_path, tmp_path / "home")
        content = _ABSNUM_BASE_PLAN.format(body=f"- {self._LINE_TOKEN}")
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(tmp_path / "x.md"), "content": content},
                "session_id": "absnum-nonplan",
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 0


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
    """plan file Write/Edit/MultiEdit時の先送り含意動詞連結ブロック検査。

    走査対象は`## 変更内容`配下および任意H2下の`### エージェント判断`配下。
    検出条件は次の2条件AND成立時。
    条件(a): 「実装時／実装段階」の直後に「精査／選定／確定／評価／検討」等の未確定動詞が続く。
    条件(b): 文末が「判断／決定／選定／確定」+「する」で結ばれる。
    (a)と(b)の共通動詞（選定・確定）は単独出現でも両条件同時成立として検出する。
    `text`コードブロック内・HTMLコメント内・フロントマターは`iter_markdown_body_lines`が除外する。
    """

    _state_env = staticmethod(_absnum_state_env)
    _make_plan = staticmethod(_make_plan_file)
    _prior_flags = staticmethod(_absnum_prior_flags)

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
        """`## 変更内容`配下の先送り含意動詞連結パターンがWriteでブロックされる。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = f"deferral-{hash(phrase) & 0xFFFF:x}"
        self._prior_flags(tmp_path, sid)
        content = _ABSNUM_BASE_PLAN.format(body=f"- {phrase}")
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 2
        assert "先送り表現を検出" in result.stderr
        assert "[auto-generated: agent-toolkit/pretooluse][block]" in result.stderr

    def test_write_with_deferral_phrase_in_text_block_is_allowed(self, tmp_path: pathlib.Path):
        """`text`コードブロック内の先送り含意動詞連結パターンはブロックされない。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "deferral-textblock"
        self._prior_flags(tmp_path, sid)
        body = "```text\n実装時に精査して確定する\n```\n"
        content = _ABSNUM_BASE_PLAN.format(body=body)
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
        assert "先送り表現を検出" not in result.stderr

    def test_write_with_deferral_phrase_in_html_comment_is_allowed(self, tmp_path: pathlib.Path):
        """複数行HTMLコメント内の先送り含意動詞連結パターンはブロックされない。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "deferral-htmlcomment"
        self._prior_flags(tmp_path, sid)
        body = "<!--\n実装時に精査して確定する\n-->\n"
        content = _ABSNUM_BASE_PLAN.format(body=body)
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
        assert "先送り表現を検出" not in result.stderr

    def test_write_with_deferral_phrase_in_background_is_allowed(self, tmp_path: pathlib.Path):
        """`## 背景`配下の先送り含意動詞連結パターンは走査対象外のためブロックされない。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "deferral-background"
        self._prior_flags(tmp_path, sid)
        # `## 背景`配下（`## 変更内容`・`### エージェント判断`のいずれでもない）へ挿入する。
        content = _ABSNUM_BASE_PLAN.replace("## 背景\n\nx\n", "## 背景\n\n実装時に精査して確定する\n").format(body="x")
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
        assert "先送り表現を検出" not in result.stderr

    def test_write_with_current_form_action_is_allowed(self, tmp_path: pathlib.Path):
        """現在形の実施義務文（末尾が判断/決定/選定/確定+するではない）はブロックされない。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "deferral-currentform"
        self._prior_flags(tmp_path, sid)
        content = _ABSNUM_BASE_PLAN.format(body="- 実装時に`agent-toolkit-edit`スキルを呼び出す")
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
        assert "先送り表現を検出" not in result.stderr

    def test_write_with_condition_a_unsatisfied_is_allowed(self, tmp_path: pathlib.Path):
        """条件(a)未確定動詞が現れない文（末尾が決定するでも(a)不成立）はブロックされない。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "deferral-cond-a-unsat"
        self._prior_flags(tmp_path, sid)
        content = _ABSNUM_BASE_PLAN.format(body="- 実装時にレビュー内容を確認して最終的に決定する")
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
        assert "先送り表現を検出" not in result.stderr


class TestPlanFileTargetFilesH3Correspondence:
    """plan file Write/Edit/MultiEdit時の対象ファイル一覧とH3見出し1対1対応検査（FB8）。"""

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
                "plan_file_guidelines_read": True,
            },
        )

    def test_allows_full_correspondence(self, tmp_path: pathlib.Path):
        """対象ファイル一覧の各パスに対応するH3見出しが揃っている場合は通過する。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "h3corr-valid"
        # H3配下にはtext/diffコードブロックを含める（`_check_plan_file_change_h3_has_code_block`検査通過のため）。
        content = _h3corr_build_content("### foo/bar.py\n\n```text\nx\n```\n\n### foo/baz.py\n\n```text\nx\n```\n\n")
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

    def test_blocks_missing_h3(self, tmp_path: pathlib.Path):
        """対象ファイル一覧に対応するH3見出しが不足する場合はブロックする。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "h3corr-missing"
        content = _h3corr_build_content("### foo/bar.py\n\nx\n\n")
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
        assert "H3見出し未対応" in result.stderr

    def test_blocks_extra_h3(self, tmp_path: pathlib.Path):
        """対象ファイル一覧に無い余分なH3見出しがある場合はブロックする。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "h3corr-extra"
        content = _h3corr_build_content("### foo/bar.py\n\nx\n\n### foo/baz.py\n\nx\n\n### foo/extra.py\n\nx\n\n")
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
        assert "対象ファイル一覧に無いH3見出し" in result.stderr

    def test_allows_no_target_files(self, tmp_path: pathlib.Path):
        """対象ファイル一覧が空の場合は検査対象外で通過する。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "h3corr-empty"
        content = (
            "# タイトル\n\n"
            "## 変更履歴\n\nx\n\n"
            "## 背景\n\nx\n\n"
            "## 対応方針\n\nx\n\n"
            "## 調査結果\n\nx\n\n"
            "## 変更内容\n\n### 対象ファイル一覧\n\nなし\n\n"
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

    def test_non_plan_file_is_skipped(self, tmp_path: pathlib.Path):
        """plan fileでないパスへの書き込みは検査対象外。"""
        content = _h3corr_build_content("### foo/bar.py\n\nx\n\n")
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(tmp_path / "x.md"), "content": content},
                "session_id": "h3corr-nonplan",
                "permission_mode": "default",
            },
        )
        assert result.returncode == 0

    def test_allows_replacement_pattern_h3(self, tmp_path: pathlib.Path):
        """「置換パターン: 」で始まるH3は対象ファイル一覧との1対1対応判定から除外される。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "h3corr-replacement"
        content = _h3corr_build_content("### 置換パターン: old-name → atk fb（対象: foo/bar.py foo/baz.py）\n\nx\n\n")
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


class TestPlanFileHistoryContentSync:
    """plan file Write/Edit/MultiEdit時の変更履歴と変更内容の対応照合検査（FB5）。"""

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
                "plan_file_guidelines_read": True,
            },
        )

    def test_history_content_sync_matches(self, tmp_path: pathlib.Path):
        """変更履歴の対象ファイル・節名アンカーが変更内容側と一致する場合はブロックしない。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "histsync-match"
        content = _history_sync_build_content(history_line="- 指摘反映（1回目）。`foo/bar.py`を修正。")
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
        assert result.returncode == 0, result.stderr

    def test_history_content_sync_detects_missing_correspondence(self, tmp_path: pathlib.Path):
        """変更履歴の対象ファイル・節名アンカーが変更内容側に無い場合はブロックする。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "histsync-missing"
        content = _history_sync_build_content(history_line="- 指摘反映（1回目）。`foo/missing.py`を修正。")
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
        assert "foo/missing.py" in result.stderr

    def test_non_plan_file_is_skipped(self, tmp_path: pathlib.Path):
        """plan fileでないパスへの書き込みは検査対象外。"""
        content = _history_sync_build_content(history_line="- 指摘反映（1回目）。`foo/missing.py`を修正。")
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(tmp_path / "x.md"), "content": content},
                "session_id": "histsync-nonplan",
                "permission_mode": "default",
            },
        )
        assert result.returncode == 0

    def test_history_content_sync_ignores_non_correspondence_path_mention(self, tmp_path: pathlib.Path):
        """対応関係を意図しない参考言及のパス記載は誤検出しない。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "histsync-mention"
        content = _history_sync_build_content(history_line="- 初版。例として`foo/example.py`を参照した。")
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
        assert result.returncode == 0, result.stderr

    def test_history_content_sync_ignores_rejected_entry(self, tmp_path: pathlib.Path):
        """却下項目に含まれるパスは`## 変更内容`側に対応記述が無くてもブロックしない。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "histsync-rejected"
        content = _history_sync_build_content(
            history_line="- 却下: 指摘の対象は`foo/rejected.py`。既存機構でカバー済みのため却下。",
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
        assert result.returncode == 0, result.stderr

    @pytest.mark.parametrize("tool_name", ["Edit", "MultiEdit"])
    def test_history_content_sync_checks_edit_and_multiedit(self, tmp_path: pathlib.Path, tool_name: str):
        """Edit・MultiEditの適用後contentも検査対象とする。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = f"histsync-{tool_name.lower()}"
        content = _history_sync_build_content(history_line="- 指摘反映（1回目）。`foo/missing.py`を修正。")
        self._prior_flags(tmp_path, sid, content)
        if tool_name == "Edit":
            tool_input = {"file_path": str(plan), "old_string": "# t\n", "new_string": content}
        else:
            tool_input = {"file_path": str(plan), "edits": [{"old_string": "# t\n", "new_string": content}]}
        result = _run(
            {
                "tool_name": tool_name,
                "tool_input": tool_input,
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        assert "foo/missing.py" in result.stderr


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
        assert "遡及スキャン" in result.stderr

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
        """文書サイズ上限対象外パスは検査対象外。"""
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
                "plan_file_guidelines_read": True,
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
        assert "パス節配下のパス値" in result.stderr

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
        assert "パス節配下のパス値" not in result.stderr


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
            "plan_file_guidelines_read": True,
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
        assert "計画ファイル未作成" not in result.stderr

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
                assert "計画ファイル未作成" in result.stderr

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
                assert "計画ファイル未作成" in result.stderr

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
            assert "計画ファイル未作成" in result.stderr
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


class TestPlanFileChangeH3HasCodeBlock:
    """plan file Write/Edit/MultiEdit時の`## 変更内容`配下H3のtext/diffコードブロック存在検査。"""

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
                "plan_file_guidelines_read": True,
            },
        )

    def test_non_target_tool_is_skipped(self):
        """対象外ツール名（Bash）は検査対象外。"""
        result = _run(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "echo hello"},
                "session_id": "h3cb-bash",
                "permission_mode": "default",
            },
        )
        assert result.returncode == 0

    def test_non_plan_file_is_skipped(self, tmp_path: pathlib.Path):
        """plan fileでないパスは検査対象外。"""
        content = _h3_codeblock_build_content("### foo/bar.py\n\nテキストのみ\n\n")
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(tmp_path / "x.md"), "content": content},
                "session_id": "h3cb-nonplan",
                "permission_mode": "default",
            },
        )
        assert result.returncode == 0

    def test_allows_when_text_code_block_present(self, tmp_path: pathlib.Path):
        """text/diffコードブロックが揃っている場合は通過する。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "h3cb-ok"
        content = _h3_codeblock_build_content("### foo/bar.py\n\n```text\n変更後の最終文面\n```\n\n")
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

    def test_blocks_when_code_block_missing(self, tmp_path: pathlib.Path):
        """コードブロック欠落H3を検出してブロックする。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "h3cb-missing"
        content = _h3_codeblock_build_content("### foo/bar.py\n\n概念記述のみ\n\n")
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
        assert "text/diffコードブロックが存在しない" in result.stderr

    def test_exception_prefixes_are_skipped(self, tmp_path: pathlib.Path):
        """例外プレフィックス（`置換パターン:`・`fix-`）および`対象ファイル一覧`は検査対象外として通過する。

        対象ファイル一覧を空にしてh3-correspondence検査をバイパスしたうえで、
        `text`/`diff`コードブロックを持たない例外プレフィックスH3のみを配置し、
        本検査が例外プレフィックスを検査対象から除外することを確認する。
        """
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "h3cb-exception"
        content = (
            "# タイトル\n\n"
            "## 変更履歴\n\nx\n\n"
            "## 背景\n\nx\n\n"
            "## 対応方針\n\nx\n\n"
            "## 調査結果\n\nx\n\n"
            "## 変更内容\n\n"
            "### 対象ファイル一覧\n\nなし\n\n"
            "### 置換パターン: foo → bar（対象: foo/bar.py）\n\nx\n\n"
            "### fix-a\n\nx\n\n"
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


class TestReadIsolatedReferenceCheck:
    """Read: メインエージェントからの隔離指定リファレンス直接Readをブロックする検査。"""

    _ISOLATED_PATH = "agent-toolkit/skills/agent-standards/references/scope-escalation-phrases.md"

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
