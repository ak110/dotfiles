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

import _colloquial_check
import pytest

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
        """old_string 内の文字化けは既存修復を妨げないため通す。"""
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


class TestColloquialCheck:
    """口語的な日本語表現の混入警告（warn のみ、exit code は 0）。

    辞書ファイルから動的にサンプルを生成するため、テスト本体には口語表現を直接書かない。
    """

    _DENY_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "_colloquial_words.txt"
    _ALLOW_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "_colloquial_words_allow.txt"

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


class TestPlanModeSkillFirstCheck:
    """plan mode 下で plan-mode スキル未起動のまま plan file 編集をブロックする検査。

    plan-modeスキル未起動でもplan file以外の操作（Read・Bash・他Skill・通常ファイル編集等）は
    一切ブロックも警告もしない。`~/.claude/plans/`直下の`*.md`に対する
    Write/Edit/MultiEditのみがブロック対象となる。
    """

    @staticmethod
    def _state_env(tmp_path: pathlib.Path, home_dir: pathlib.Path | None = None) -> dict[str, str]:
        env = {"TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path)}
        if home_dir is not None:
            env["HOME"] = str(home_dir)
        return env

    @staticmethod
    def _make_plan(home_dir: pathlib.Path, name: str = "test.md") -> pathlib.Path:
        plans = home_dir / ".claude" / "plans"
        plans.mkdir(parents=True, exist_ok=True)
        plan = plans / name
        plan.write_text("# t\n", encoding="utf-8")
        return plan

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
        # textlint_violations_readも併せて設定する（独立checkとの干渉回避）
        _write_session_state(
            tmp_path,
            sid,
            {"plan_mode_skill_invoked": True, "textlint_violations_read": True},
        )
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": "# t\n"},
                "session_id": sid,
                "permission_mode": "plan",
            },
            env_overrides=env,
        )
        assert result.returncode == 0

    def test_allows_non_plan_file_edit_without_skill(self, tmp_path: pathlib.Path):
        """plan file 以外の編集はスキル未起動でも通す。"""
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
        """plan mode 外では plan-mode スキル未起動による plan file 編集ブロックは発火しない。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "non-plan-mode"
        # textlint_violations_readを設定して独立checkとの干渉を回避
        _write_session_state(tmp_path, sid, {"textlint_violations_read": True})
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
        assert result.stdout == ""


class TestPlanModeSkillCallSites:
    """plan-modeスキル呼び出しの素通り保証。"""

    @staticmethod
    def _state_env(tmp_path: pathlib.Path) -> dict[str, str]:
        return {"TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path)}

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


class TestTextlintViolationsReadFirstCheck:
    """plan file 編集前に textlint-violations.md 未読の場合のブロック検査。

    `permission_mode`の値に依らず、`~/.claude/plans/`直下の`*.md`に対する
    Write/Edit/MultiEditのみがブロック対象となる。plan file以外の操作は
    一切ブロック・警告しない。
    """

    @staticmethod
    def _state_env(tmp_path: pathlib.Path, home_dir: pathlib.Path | None = None) -> dict[str, str]:
        env = {"TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path)}
        if home_dir is not None:
            env["HOME"] = str(home_dir)
        return env

    @staticmethod
    def _make_plan(home_dir: pathlib.Path, name: str = "test.md") -> pathlib.Path:
        plans = home_dir / ".claude" / "plans"
        plans.mkdir(parents=True, exist_ok=True)
        plan = plans / name
        plan.write_text("# t\n", encoding="utf-8")
        return plan

    def test_blocks_plan_file_write_without_read(self, tmp_path: pathlib.Path):
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "tv-write-block"
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
        assert "[auto-generated: agent-toolkit/pretooluse][block]" in result.stderr

    def test_blocks_plan_file_edit_without_read(self, tmp_path: pathlib.Path):
        home = tmp_path / "home"
        plan = self._make_plan(home, "edit.md")
        env = self._state_env(tmp_path, home)
        sid = "tv-edit-block"
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
        assert result.returncode == 2

    def test_blocks_plan_file_multiedit_without_read(self, tmp_path: pathlib.Path):
        home = tmp_path / "home"
        plan = self._make_plan(home, "multi.md")
        env = self._state_env(tmp_path, home)
        sid = "tv-multi-block"
        _write_session_state(tmp_path, sid, {"plan_mode_skill_invoked": True})
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

    def test_allows_plan_file_when_flag_set(self, tmp_path: pathlib.Path):
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "tv-flag-set"
        _write_session_state(
            tmp_path,
            sid,
            {"plan_mode_skill_invoked": True, "textlint_violations_read": True},
        )
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

    def test_allows_non_plan_file_edit_without_read(self, tmp_path: pathlib.Path):
        """plan file以外の編集はフラグ未設定でも通す。"""
        home = tmp_path / "home"
        home.mkdir()
        env = self._state_env(tmp_path, home)
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(tmp_path / "x.md"), "content": "# t\n"},
                "session_id": "tv-other-file",
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


class TestLanguageEscalation:
    """言語検査のエスカレーション（連続英語ターン → ブロック）。

    セッション状態を介してexit code 2でツール呼び出しをブロックする。
    """

    @staticmethod
    def _state_env(tmp_path: pathlib.Path) -> dict[str, str]:
        return {"TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path)}

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


class TestBashGitCommitWarning:
    """git commit未検証警告。

    セッション状態のtest_executedを参照し、テスト未実行時に警告する。
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
        return {"TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path)}

    def _write_state(self, tmp_path: pathlib.Path, session_id: str, state: dict) -> None:
        path = tmp_path / f"claude-agent-toolkit-{session_id}.json"
        path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    def test_blocked_when_not_read(self, state_dir: dict[str, str]):
        """codex-review.md未読時にcodex MCP呼び出しがブロックされる。"""
        result = _run(
            {"tool_name": "mcp__codex__codex", "tool_input": {"prompt": "hello"}, "session_id": "no-review"},
            env_overrides=state_dir,
        )
        assert result.returncode == 2
        assert "codex-review.md" in result.stderr

    def test_allowed_when_read(self, state_dir: dict[str, str], tmp_path: pathlib.Path):
        """codex-review.md読み込み済みの場合は通過する。"""
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
        assert result.stdout.strip() == ""


class TestCodexMcpSandbox:
    """codex MCP sandbox自動修正。"""

    @pytest.fixture(name="state_dir")
    def _state_dir(self, tmp_path: pathlib.Path) -> dict[str, str]:
        return {"TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path)}

    def _write_state(self, tmp_path: pathlib.Path, session_id: str, state: dict) -> None:
        path = tmp_path / f"claude-agent-toolkit-{session_id}.json"
        path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

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
        """sandboxが既にdanger-full-accessの場合は修正しない。"""
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
        assert result.stdout.strip() == ""


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


class TestAskUserQuestionScopeEscalationCheck:
    """AskUserQuestion向け縮退誘発フレーズ検出ブロック。

    フレーズ本文の代わりにパターンマッチ最小単位（正規表現の最短一致）を
    テスト入力に用いる（`agent-toolkit:agent-standards`「コンテキスト汚染の回避」節）。
    """

    @pytest.mark.parametrize(
        ("text", "category"),
        [
            ("作業量で多い", "workload"),
            ("セッションで完遂", "single-session"),
            ("進め方を確認", "approach-confirm"),
            ("分割して進める", "split-execution"),
            ("残コンテキスト", "context-shortage"),
            ("対応を後回し", "defer-onset"),
            ("優先順位を相談", "priority-consult"),
            ("対象件数が多い", "scope-volume"),
        ],
    )
    def test_question_text_blocks(self, text: str, category: str):
        result = _run(
            {
                "tool_name": "AskUserQuestion",
                "tool_input": {
                    "questions": [
                        {
                            "question": text,
                            "header": "header",
                            "options": [{"label": "ok", "description": "ok"}],
                        }
                    ],
                },
            }
        )
        assert result.returncode == 2
        assert "縮退誘発フレーズ" in result.stderr
        assert category in result.stderr

    def test_header_blocks(self):
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
        assert result.returncode == 2
        assert "縮退誘発フレーズ" in result.stderr

    def test_option_label_blocks(self):
        result = _run(
            {
                "tool_name": "AskUserQuestion",
                "tool_input": {
                    "questions": [
                        {
                            "question": "approach?",
                            "options": [
                                {"label": "セッションで完遂", "description": "do all at once"},
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
