# ruff: noqa: E402,F401,F403,F405,I001
# pylint: disable=unused-import,unused-wildcard-import,wildcard-import,wrong-import-position,undefined-variable
"""agent-toolkit/agent_toolkit/_hooks/pretooluse/dispatch.py のテスト。

subprocessで起動しexit code・stderr・stdoutを検証する。
"""

import ast
import json
import os
import pathlib
import re
import shlex
import subprocess
import tempfile
import textwrap
import time
from collections.abc import Callable

import pytest
from pyfltr.colloquial import check as _colloquial_check

from agent_toolkit import hook
from agent_toolkit._atk import managed_temp as _managed_temp
from agent_toolkit._hooks import required_reads
from agent_toolkit._hooks.pretooluse import dispatch as pretooluse
from agent_toolkit._hooks.pretooluse.test_support_test import *  # noqa: F403
from agent_toolkit._testing import fork_runner as _fork_runner
from agent_toolkit._testing.helpers import SESSION_STATE_FILENAME_TEMPLATE


@pytest.mark.parametrize("module_name", sorted(hook._SUBCOMMANDS))  # noqa: SLF001  # pylint: disable=protected-access
def test_warn_notices_are_not_written_to_stderr(module_name: str) -> None:
    """exit 0で届かないstderrへwarn通知を出力する実装の再混入を検出する。"""
    package_dir = pathlib.Path(pretooluse.__file__).parent
    if module_name == "pretooluse":
        source = "\n".join(
            path.read_text(encoding="utf-8") for path in sorted(package_dir.glob("*.py")) if not path.name.endswith("_test.py")
        )
    else:
        source = (package_dir.parent / f"{module_name}.py").read_text(encoding="utf-8")
    offenders = _stderr_warn_offenders(source)
    if module_name == "pretooluse":
        assert offenders == []
    else:
        assert offenders == [], module_name


def test_stderr_warn_offenders_detects_indirect_binding() -> None:
    """warn通知を返す関数の結果を束縛してstderrへ渡す形を、定義順に依存せず検出する。"""
    source = textwrap.dedent(
        """\
        import sys


        def _llm_notice(text, tag):
            return f"{tag}: {text}"


        def _compose_notice():
            return _warn_remote_change()


        def _warn_remote_change():
            return _llm_notice("body", tag="warn")


        def main():
            notice = _compose_notice()
            print(notice, file=sys.stderr)
            print("plain", file=sys.stderr)
        """
    )
    expected_lineno = source.splitlines().index("    print(notice, file=sys.stderr)") + 1
    assert _stderr_warn_offenders(source) == [expected_lineno]


def test_bash_atk_subcommand_without_help_is_not_blocked(tmp_path: pathlib.Path) -> None:
    """ヘルプ未観測の`atk`サブコマンドを遮断しない。"""
    result = _run(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "atk wi add example"},
            "session_id": "dispatch-unverified-atk-help",
        },
        env_overrides=_plan_file_state_env(tmp_path),
    )

    assert result.returncode == 0
    assert "ヘルプ出力を観測していない" not in result.stderr


class TestMojibakeCheck:
    """文字化け（U+FFFD）検出。"""

    def test_write_with_mojibake(self):
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "/tmp/a.txt", "content": "hello \ufffd world"}})
        assert result.returncode == 2
        assert "U+FFFD" in result.stderr
        # コーディングエージェント宛てメッセージ規約: プレフィックスとサフィックスが付与されていること。
        assert "[auto-generated: agent-toolkit/pretooluse]" in result.stderr
        assert "[block]" in result.stderr
        assert "Fix: U+FFFDを意図した文字へ置き換えて再実行する" in result.stderr
        assert "自動生成のhook通知" in result.stderr

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


class TestEditBoundaryResolution:
    """複数断片の境界解決による遮断。"""

    def test_multiedit_missing_last_boundary_is_blocked(self, tmp_path: pathlib.Path) -> None:
        target = tmp_path / "target.txt"
        target.write_text("first\nsecond\n", encoding="utf-8")
        result = _run(
            {
                "tool_name": "MultiEdit",
                "tool_input": {
                    "file_path": str(target),
                    "edits": [
                        {"old_string": "first", "new_string": "first-updated"},
                        {"old_string": "missing", "new_string": "added"},
                    ],
                },
            }
        )
        assert result.returncode == 2
        assert f"{target}: edits[1].new_string" in result.stderr
        assert target.read_text(encoding="utf-8") == "first\nsecond\n"

    def test_multiedit_all_boundaries_resolvable_is_allowed(self, tmp_path: pathlib.Path) -> None:
        target = tmp_path / "target.txt"
        target.write_text("first\nsecond\n", encoding="utf-8")
        result = _run(
            {
                "tool_name": "MultiEdit",
                "tool_input": {
                    "file_path": str(target),
                    "edits": [
                        {"old_string": "first", "new_string": "first-updated"},
                        {"old_string": "second", "new_string": "second-updated"},
                    ],
                },
            }
        )
        assert result.returncode == 0

    def test_boundary_check_skips_unreadable_and_single_fragment(self, tmp_path: pathlib.Path) -> None:
        missing = tmp_path / "missing.txt"
        unreadable = _run(
            {
                "tool_name": "MultiEdit",
                "tool_input": {
                    "file_path": str(missing),
                    "edits": [
                        {"old_string": "first", "new_string": "first-updated"},
                        {"old_string": "second", "new_string": "second-updated"},
                    ],
                },
            }
        )
        single = _run(
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": str(missing),
                    "old_string": "missing",
                    "new_string": "added",
                },
            }
        )
        assert unreadable.returncode == 0
        assert single.returncode == 0

    def test_multiedit_ambiguous_boundary_is_blocked(self, tmp_path: pathlib.Path) -> None:
        target = tmp_path / "target.txt"
        target.write_text("repeat\nrepeat\nunique\n", encoding="utf-8")
        result = _run(
            {
                "tool_name": "MultiEdit",
                "tool_input": {
                    "file_path": str(target),
                    "edits": [
                        {"old_string": "repeat", "new_string": "updated"},
                        {"old_string": "unique", "new_string": "changed"},
                    ],
                },
            }
        )
        assert result.returncode == 2
        assert f"{target}: edits[0].new_string" in result.stderr

    def test_apply_patch_ambiguous_hunk_is_blocked(self, tmp_path: pathlib.Path) -> None:
        target = tmp_path / "target.txt"
        target.write_text("repeat\nrepeat\nunique\n", encoding="utf-8")
        command = f"*** Begin Patch\n*** Update File: {target}\n@@\n-repeat\n+updated\n@@\n-unique\n+changed\n*** End Patch"
        result = _run({"tool_name": "apply_patch", "tool_input": {"command": command}, "turn_id": "turn-1"})
        assert result.returncode == 2
        assert f"{target}: hunk[0]" in result.stderr

    def test_apply_patch_missing_hunk_is_blocked(self, tmp_path: pathlib.Path) -> None:
        target = tmp_path / "target.txt"
        target.write_text("first\nsecond\n", encoding="utf-8")
        command = f"*** Begin Patch\n*** Update File: {target}\n@@\n-first\n+updated\n@@\n-missing\n+added\n*** End Patch"
        result = _run({"tool_name": "apply_patch", "tool_input": {"command": command}, "turn_id": "turn-1"})
        assert result.returncode == 2
        assert f"{target}: hunk[1]" in result.stderr

    def test_apply_patch_unique_hunks_are_allowed(self, tmp_path: pathlib.Path) -> None:
        target = tmp_path / "target.txt"
        target.write_text("first\nsecond\n", encoding="utf-8")
        command = f"*** Begin Patch\n*** Update File: {target}\n@@\n-first\n+updated\n@@\n-second\n+changed\n*** End Patch"
        result = _run({"tool_name": "apply_patch", "tool_input": {"command": command}, "turn_id": "turn-1"})
        assert result.returncode == 0


class TestPs1EolCheck:
    """PowerShell ファイルへの LF-only 書き込み検出。"""

    def test_ps1_with_lf_only_blocks(self):
        content = "Set-StrictMode\nWrite-Host 'x'\n"
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "C:/x/a.ps1", "content": content}})
        assert result.returncode == 2
        assert "LFだけの内容" in result.stderr
        assert "UTF-8 BOMが失われて日本語が文字化け" in result.stderr
        assert "*.ps1 text eol=crlf" in result.stderr
        assert "Fix: 既存ファイルにはEditツールを使う" in result.stderr

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
        assert "直接編集" in result.stderr
        assert "Fix: " in result.stderr

    def test_edit_cargo_lock_blocked(self):
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {"file_path": "Cargo.lock", "old_string": "a", "new_string": "b"},
            }
        )
        assert result.returncode == 2
        assert "cargo add" in result.stderr
        assert "Fix: " in result.stderr

    def test_normal_file_allowed(self):
        """lockfile 名を部分的に含むだけのパスは通過する (例: uv.lock.bak)。"""
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "uv.lock.bak", "content": "x"}})
        assert result.returncode == 0


class TestSecretsCheck:
    """シークレット/鍵ファイルの直接編集ブロック。"""

    @pytest.mark.parametrize(
        ("file_path", "expects_guidance"),
        [
            (".env", True),
            (".env.local", True),
            ("app/.env.production", True),
            (".encrypt_key", False),
            (".secret_key", False),
            ("github_action", False),
            ("keys/github_action.pub", False),
            ("certs/server.pem", False),
            ("private.key", False),
        ],
    )
    def test_blocked(self, file_path: str, expects_guidance: bool):
        """遮断と、`.env`系だけへ代替経路を案内する契約を検証する。"""
        result = _run({"tool_name": "Write", "tool_input": {"file_path": file_path, "content": "x"}})
        assert result.returncode == 2
        assert "シークレット" in result.stderr
        assert "Fix: " in result.stderr
        assert (_SECRETS_COPY_GUIDANCE in result.stderr) is expects_guidance
        assert (_SECRETS_VALUE_EDIT_GUIDANCE in result.stderr) is expects_guidance

    @pytest.mark.parametrize(
        "command",
        [
            "cp .env ../wt/.env",
            "echo FLAG=x >> .env",
            "sed -i /FLAG/d .env",
            "sed -i s/A=1/A=2/ .env",
        ],
    )
    def test_bash_env_operations_allowed(self, command: str):
        """Bash経由の`.env`複製・追記・行削除・値の置換は遮断しない。"""
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        assert result.returncode == 0

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
        assert "pyproject.toml" in _additional_context(result)
        assert "uv add" in _additional_context(result)
        # 編集警告はstderrではなくadditionalContextへ集約する。
        assert result.stderr == ""

    def test_package_json_warns(self):
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": "app/package.json", "content": "{}"},
            }
        )
        assert result.returncode == 0
        assert "package.json" in _additional_context(result)
        assert "pnpm add" in _additional_context(result)

    def test_normal_file_no_warn(self):
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "foo.txt", "content": "x"}})
        assert result.returncode == 0
        assert _agent_messages(result).strip() == ""


class TestHomePathCheck:
    """ホームディレクトリ絶対パス混入の警告 (warn のみ)。"""

    def test_home_path_in_content_warns(self):
        content = f"config_path = '{_home_path()}/myproj/config.yaml'\n"
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "src/app.py", "content": content}})
        assert result.returncode == 0
        assert "ホームディレクトリ" in _additional_context(result)

    def test_home_path_in_claude_job_file_is_skipped(self):
        """Claude Codeが生成するセッション作業領域では正確なホーム絶対パスを許容する。"""
        target = pathlib.Path.home() / ".claude" / "jobs" / "session" / "tmp" / "material.md"
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(target), "content": f"対象: {_home_path()}/worktree"},
            }
        )
        assert result.returncode == 0
        assert "home directory" not in _agent_messages(result)

    def test_home_path_in_non_git_temp_document_is_skipped(self, tmp_path: pathlib.Path):
        """Git管理外の一時作業文書では正確なホーム絶対パスを許容する。"""
        target = tmp_path / "draft.md"
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(target), "content": f"対象: {_home_path()}/worktree"},
            }
        )
        assert result.returncode == 0
        assert "home directory" not in _agent_messages(result)

    def test_home_path_in_git_worktree_under_temp_warns(self, tmp_path: pathlib.Path):
        """一時ルート配下でもGit worktreeの成果物には警告する。"""
        worktree = tmp_path / "repo"
        subprocess.run(["git", "init", str(worktree)], check=True, capture_output=True)
        target = worktree / "docs" / "note.md"
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(target), "content": f"対象: {_home_path()}/worktree"},
            }
        )
        assert result.returncode == 0
        assert "ホームディレクトリ" in _additional_context(result)

    def test_home_path_git_boundary_does_not_parse_localized_git_diagnostics(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ):
        """Gitのロケール依存診断文を呼び出しも解析もせず管理外を確定する。"""

        def _localized_git(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            del args, kwargs
            return subprocess.CompletedProcess(
                ["git", "rev-parse"],
                128,
                "",
                "致命的エラー: Gitリポジトリではありません",
            )

        monkeypatch.setattr(pretooluse.subprocess, "run", _localized_git)
        target = tmp_path / "repo" / "docs" / "note.md"
        return_code = pretooluse.main(
            json.dumps(
                {
                    "tool_name": "Write",
                    "tool_input": {"file_path": str(target), "content": f"対象: {_home_path()}/worktree"},
                },
                ensure_ascii=False,
            )
        )

        assert return_code == 0
        assert "home directory" not in capsys.readouterr().out

    def test_home_path_warns_when_git_marker_detection_fails(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ):
        """Git管理マーカーを確認できない場合は既存警告を維持する。"""
        original_lstat = pathlib.Path.lstat

        def _unreadable_git_marker(path: pathlib.Path, *args: object, **kwargs: object) -> os.stat_result:
            if path.name == ".git":
                raise PermissionError(path)
            return original_lstat(path, *args, **kwargs)

        monkeypatch.setattr(pathlib.Path, "lstat", _unreadable_git_marker)
        target = tmp_path / "repo" / "docs" / "note.md"
        return_code = pretooluse.main(
            json.dumps(
                {
                    "tool_name": "Write",
                    "tool_input": {"file_path": str(target), "content": f"対象: {_home_path()}/worktree"},
                },
                ensure_ascii=False,
            )
        )

        assert return_code == 0
        assert "ホームディレクトリ" in capsys.readouterr().out

    def test_home_path_in_temp_worktree_git_file_warns(self, tmp_path: pathlib.Path):
        """一時ルート配下のworktree用`.git`ファイルもGit管理候補として警告する。"""
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        (worktree / ".git").write_text("gitdir: /tmp/example.git/worktrees/worktree\n", encoding="utf-8")
        target = worktree / "docs" / "note.md"
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(target), "content": f"対象: {_home_path()}/worktree"},
            }
        )
        assert result.returncode == 0
        assert "ホームディレクトリ" in _additional_context(result)

    def test_home_path_in_non_git_prefix_sibling_is_skipped(self):
        """一時ルートと文字列prefixだけが同じGit管理外の兄弟パスは除外する。"""
        sibling = pathlib.Path(f"{tempfile.gettempdir()}-sibling") / "draft.md"
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(sibling), "content": f"対象: {_home_path()}/worktree"},
            }
        )
        assert result.returncode == 0
        assert "home directory" not in _agent_messages(result)

    @pytest.mark.parametrize("xdg_value", ["unset", ""])
    def test_home_path_in_default_cache_document_is_skipped(self, xdg_value: str, monkeypatch: pytest.MonkeyPatch):
        """XDGキャッシュ設定が未設定又は空値の場合は`$HOME/.cache`をGit管理外として扱う。"""
        if xdg_value == "unset":
            monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        else:
            monkeypatch.setenv("XDG_CACHE_HOME", xdg_value)
        target = pathlib.Path.home() / ".cache" / "draft.md"
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(target), "content": f"対象: {_home_path()}/worktree"},
            },
            env_overrides={"XDG_CACHE_HOME": xdg_value} if xdg_value != "unset" else None,
        )
        assert result.returncode == 0
        assert "home directory" not in _agent_messages(result)

    def test_home_path_in_absolute_xdg_cache_document_is_skipped(self, tmp_path: pathlib.Path):
        """絶対`XDG_CACHE_HOME`配下のGit管理外文書はホームパス警告の対象外とする。"""
        cache_home = tmp_path / "cache"
        target = cache_home / "draft.md"
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(target), "content": f"対象: {_home_path()}/worktree"},
            },
            env_overrides={"XDG_CACHE_HOME": str(cache_home)},
        )
        assert result.returncode == 0
        assert "home directory" not in _agent_messages(result)

    def test_home_path_in_relative_xdg_cache_document_warns(self):
        """相対`XDG_CACHE_HOME`は除外ルートとして扱わず警告する。"""
        target = pathlib.Path.cwd() / "relative-cache" / "draft.md"
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(target), "content": f"対象: {_home_path()}/worktree"},
            },
            env_overrides={"XDG_CACHE_HOME": "relative-cache"},
        )
        assert result.returncode == 0
        assert "ホームディレクトリ" in _additional_context(result)

    def test_home_path_in_git_managed_xdg_cache_warns(self, tmp_path: pathlib.Path):
        """絶対`XDG_CACHE_HOME`配下でもGit管理マーカーがあれば警告する。"""
        cache_repo = tmp_path / "cache-repo"
        subprocess.run(["git", "init", str(cache_repo)], check=True, capture_output=True)
        target = cache_repo / "docs" / "note.md"
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(target), "content": f"対象: {_home_path()}/worktree"},
            },
            env_overrides={"XDG_CACHE_HOME": str(cache_repo.parent)},
        )
        assert result.returncode == 0
        assert "ホームディレクトリ" in _additional_context(result)

    def test_home_path_in_local_md_skipped(self):
        content = f"See {_home_path()}/proj for details."
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "CLAUDE.local.md", "content": content}})
        assert result.returncode == 0
        assert result.stderr == ""

    def test_home_path_in_settings_local_json_skipped(self):
        content = f'{{"path": "{_home_path()}/x"}}'
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
                "tool_input": {"file_path": "README.md", "old_string": "a", "new_string": f"{_home_path()}/x"},
            }
        )
        assert result.returncode == 0

    @pytest.mark.parametrize("name", ["home-path.md", "home-path.bugs.md"])
    def test_home_path_in_plan_file_skipped(self, tmp_path: pathlib.Path, name: str):
        """計画ファイル（メイン）と計画ファイル（バグ）では正確なホーム絶対パスを許容する。"""
        home = tmp_path / "home"
        plan = _make_plan_file(home, name)
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": str(plan),
                    "old_string": "# t",
                    "new_string": f"対象: {home}/worktree",
                },
            },
            env_overrides=_plan_file_state_env(tmp_path, home),
        )
        assert result.returncode == 0
        assert "home directory" not in _agent_messages(result)


class TestRecursiveHomeSearchCheck:
    """高容量の利用者領域を無限定に再帰検索する実行位置の警告。"""

    @pytest.mark.parametrize(
        "command",
        [
            "rg keyword ~/.local",
            "grep -r keyword ~/.npm",
            "grep -R keyword ~/.codex",
            "rg keyword ~/.local ~/.codex",
            "rg /tmp ~/.local",
            "rg --color always -n keyword ~/.claude",
        ],
    )
    def test_warns_for_unlimited_recursive_home_search(self, command: str) -> None:
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        assert result.returncode == 0
        assert "大容量のユーザーディレクトリ" in _additional_context(result)

    def test_warns_for_expanded_absolute_home_search(self) -> None:
        """チルダを展開済みの絶対パスで指定した再帰検索も警告する。

        検索先はフックが解決するホームから実行時に組み立てる。収集時に組み立てると、
        実行環境のホームを差し替えるfixtureの適用前の値が固定される。
        """
        home = pathlib.Path(_home_path())
        targets = f"{shlex.quote(str(home / '.codex'))} {shlex.quote(str(home / '.claude'))}"
        result = _run({"tool_name": "Bash", "tool_input": {"command": f"rg -n keyword {targets} 2>/dev/null"}})
        assert result.returncode == 0
        assert "大容量のユーザーディレクトリ" in _additional_context(result)

    @pytest.mark.parametrize(
        "command",
        [
            "rg --files ~/.local",
            "rg keyword ~/.local/share/foo",
            "rg keyword ~/.local /tmp/repository",
            "rg --ignore-file ~/.local keyword /tmp/repository",
            "rg --ignore-file=~/.local keyword /tmp/repository",
            "rg -g ~/.local keyword /tmp/repository",
            "grep -r --include ~/.local keyword /tmp/repository",
            "grep -r --exclude-dir=~/.local keyword /tmp/repository",
            "rg --ignore-file /tmp/ignore keyword ~/.local",
            "rg --type-add custom:*.txt keyword ~/.local",
            "rg -g '*.py' keyword ~/.local",
            "rg --max-count 2 keyword ~/.local",
            "rg --max-filesize 1M keyword ~/.claude",
            "rg -n keyword ~/.claude /tmp/repository",
            "grep -r --include '*.py' keyword ~/.local",
            "grep -r --exclude-dir cache keyword ~/.local",
            "grep --recursive keyword ~/.local",
            "echo rg keyword ~/.local",
            "grep keyword ~/.codex",
        ],
    )
    def test_allows_bounded_or_non_execution_position_search(self, command: str) -> None:
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        assert result.returncode == 0
        assert "high-capacity user directory" not in _additional_context(result)


class TestUnboundedHomeTraversalCheck:
    """対象限定の無い`find`・`ls -R`による高容量領域の走査の警告。"""

    @pytest.mark.parametrize(
        "command",
        [
            "find ~ -name '*.md'",
            "find $HOME",
            "find ~/.local ~/.codex -type f",
            "ls -R ~/.local",
            "ls -aR ~/.claude",
            "ls -R ~/.local ~/.claude",
        ],
    )
    def test_warns_for_unbounded_home_traversal(self, command: str) -> None:
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        assert result.returncode == 0
        assert "除外設定を持たない走査コマンド" in _additional_context(result)

    @pytest.mark.parametrize(
        "command",
        [
            "find ~ -maxdepth 2",
            "find ~/.local -prune -o -print",
            "find /tmp/repository",
            "find ~ -xdev",
            "find ~ -mount",
            "find ~/.local /tmp/repository",
            "find",
            "find ~ -unknown",
            "ls -R /tmp/repository",
            "ls -R ~/.local /tmp/repository",
            "ls -R",
            "ls --unknown -R ~/.local",
            "ls ~/.local",
            "echo find ~",
        ],
    )
    def test_bounded_or_non_target_traversal_is_silent(self, command: str) -> None:
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        assert result.returncode == 0
        assert "除外設定を持たない走査コマンド" not in _agent_messages(result)

    def test_heredoc_is_silent(self) -> None:
        result = _run({"tool_name": "Bash", "tool_input": {"command": "cat <<'EOF'\nfind ~\nEOF"}})
        assert result.returncode == 0
        assert "除外設定を持たない走査コマンド" not in _agent_messages(result)


class TestNonEditToolWarnings:
    """WebFetchとSendMessageの入力警告。"""

    @pytest.mark.parametrize("phrase", ["全文", "原文", "そのまま", "逐語", "引用", "verbatim", "word-for-word"])
    def test_webfetch_verbatim_request_warns(self, phrase: str) -> None:
        result = _run({"tool_name": "WebFetch", "tool_input": {"url": "https://example.invalid", "prompt": f"{phrase}で返す"}})
        assert result.returncode == 0
        assert "要約モデル" in _additional_context(result)
        assert "生データ" in _additional_context(result)

    def test_webfetch_summary_request_does_not_warn(self) -> None:
        result = _run({"tool_name": "WebFetch", "tool_input": {"url": "https://example.invalid", "prompt": "要点を整理する"}})
        assert result.returncode == 0
        assert result.stdout == ""

    def test_sendmessage_agent_type_recipient_warns(self) -> None:
        result = _run({"tool_name": "SendMessage", "tool_input": {"to": "plugin-dev:skill-reviewer", "message": "通知"}})
        assert result.returncode == 0
        assert "SendMessageの到達可能な宛先ではない" in _additional_context(result)

    def test_sendmessage_runtime_recipient_does_not_warn(self) -> None:
        result = _run({"tool_name": "SendMessage", "tool_input": {"to": "main", "message": "通知"}})
        assert result.returncode == 0
        assert result.stdout == ""

    @pytest.mark.parametrize(
        "recipient",
        ["uds:/tmp/cc-socks/1939480.sock", "unix:/run/agent/relay.sock"],
    )
    def test_sendmessage_runtime_socket_recipient_does_not_warn(self, recipient: str) -> None:
        result = _run({"tool_name": "SendMessage", "tool_input": {"to": recipient, "message": "通知"}})
        assert result.returncode == 0
        assert result.stdout == ""


class TestColloquialCheck:
    """口語的な日本語表現の混入警告（warn のみ、exit code は 0）。"""

    def test_warns_on_deny(self, deny_substring: str):
        content = f"概要は{deny_substring}該当する。\n"
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "src/note.md", "content": content}})
        assert result.returncode == 0
        assert "口語的な日本語表現" in _additional_context(result)
        assert "一致: 1件（行1、列4）" in _additional_context(result)
        assert "検出箇所を含む文全体" in _additional_context(result)
        assert "[auto-generated: agent-toolkit/pretooluse][warn]" in _additional_context(result)
        assert f"検出語: {deny_substring}" in _agent_messages(result)

    def test_lists_every_match_position_within_limit(self, deny_substring: str):
        content = f"概要は{deny_substring}該当する。\n" * 5
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "src/note.md", "content": content}})
        assert result.returncode == 0
        assert "一致: 5件（行1、列4; 行2、列4; 行3、列4; 行4、列4; 行5、列4）" in _additional_context(result)

    def test_omits_match_positions_beyond_limit(self, deny_substring: str):
        content = f"概要は{deny_substring}該当する。\n" * 6
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "src/note.md", "content": content}})
        assert result.returncode == 0
        assert "一致: 6件（行1、列4; 行2、列4; 行3、列4; 行4、列4; 行5、列4）" in _additional_context(result)
        assert "行6" not in _additional_context(result)

    def test_does_not_block(self, deny_substring: str):
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "x.md", "content": deny_substring}})
        assert result.returncode == 0  # warnのみ

    def test_clean_text_no_warn(self):
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "src/app.py", "content": "x = 1\n"}})
        assert result.returncode == 0
        assert "colloquial" not in _agent_messages(result)

    def test_managed_temp_skips_colloquial_warning(self, tmp_path: pathlib.Path, deny_substring: str) -> None:
        managed = tmp_path / "managed"
        managed.mkdir()
        (managed / ".agent-toolkit-managed-temp.json").write_text("{}\n", encoding="utf-8")
        target = managed / "nested" / "report.md"
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(target), "content": f"概要は{deny_substring}該当する。\n"},
            }
        )
        assert result.returncode == 0
        assert "colloquial" not in _agent_messages(result)

    def test_old_string_not_inspected(self, deny_substring: str):
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {"file_path": "x.md", "old_string": deny_substring, "new_string": "ok"},
            }
        )
        assert result.returncode == 0
        assert "colloquial" not in _agent_messages(result)

    def test_edit_inside_fenced_code_is_silent(self, tmp_path: pathlib.Path, deny_substring: str) -> None:
        target = tmp_path / "note.md"
        target.write_text("本文。\n```text\n既存行\n```\n", encoding="utf-8")
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": str(target),
                    "old_string": "既存行\n",
                    "new_string": f"既存行\n{deny_substring}を含む行\n",
                },
            }
        )
        assert result.returncode == 0
        assert "colloquial" not in _agent_messages(result)

    def test_edit_outside_fenced_code_warns(self, tmp_path: pathlib.Path, deny_substring: str) -> None:
        target = tmp_path / "note.md"
        target.write_text("本文。\n```text\n既存行\n```\n", encoding="utf-8")
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": str(target),
                    "old_string": "本文。\n",
                    "new_string": f"本文。\n{deny_substring}を含む行\n",
                },
            }
        )
        assert result.returncode == 0
        assert "口語的な日本語表現" in _additional_context(result)

    def test_multiedit_inside_fenced_code_is_silent(self, tmp_path: pathlib.Path, deny_substring: str) -> None:
        target = tmp_path / "note.md"
        target.write_text("本文。\n```text\n既存行\n```\n", encoding="utf-8")
        result = _run(
            {
                "tool_name": "MultiEdit",
                "tool_input": {
                    "file_path": str(target),
                    "edits": [
                        {
                            "old_string": "既存行\n",
                            "new_string": f"既存行\n{deny_substring}を含む行\n",
                        }
                    ],
                },
            }
        )
        assert result.returncode == 0
        assert "colloquial" not in _agent_messages(result)

    def test_apply_patch_inside_fenced_code_is_silent(self, tmp_path: pathlib.Path, deny_substring: str) -> None:
        target = tmp_path / "note.md"
        target.write_text("本文。\n```text\n既存行\n```\n", encoding="utf-8")
        command = f"*** Begin Patch\n*** Update File: {target}\n@@\n 既存行\n+{deny_substring}を含む行\n*** End Patch"
        result = _run({"tool_name": "apply_patch", "tool_input": {"command": command}, "turn_id": "turn-1"})
        assert result.returncode == 0
        assert "colloquial" not in _agent_messages(result)

    def test_unchanged_colloquial_line_is_silent(self, tmp_path: pathlib.Path, deny_substring: str) -> None:
        target = tmp_path / "note.md"
        target.write_text(f"{deny_substring}を含む既存行\n変更前\n", encoding="utf-8")
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {"file_path": str(target), "old_string": "変更前", "new_string": "変更後"},
            }
        )
        assert result.returncode == 0
        assert "colloquial" not in _agent_messages(result)

    def test_missing_file_falls_back_to_fragment_check(self, tmp_path: pathlib.Path, deny_substring: str) -> None:
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": str(tmp_path / "missing.md"),
                    "old_string": "変更前",
                    "new_string": f"{deny_substring}を含む変更後",
                },
            }
        )
        assert result.returncode == 0
        assert "口語的な日本語表現" in _additional_context(result)

    @pytest.mark.parametrize("tool_name", ["Write", "Edit", "MultiEdit"])
    def test_plan_file_skips_colloquial_warning_for_claude_edit_tools(
        self, tmp_path: pathlib.Path, deny_substring: str, tool_name: str
    ) -> None:
        """計画ファイルではClaudeの3編集操作すべてで口語警告を出力しない。"""
        plan = _make_plan_file(tmp_path / "home", "colloquial.md")
        content = f"概要は{deny_substring}該当する。\n"
        if tool_name == "Write":
            tool_input = {"file_path": str(plan), "content": content}
        elif tool_name == "Edit":
            tool_input = {"file_path": str(plan), "old_string": "# t\n", "new_string": content}
        else:
            tool_input = {
                "file_path": str(plan),
                "edits": [{"old_string": "# t\n", "new_string": content}],
            }
        result = _run(
            {"tool_name": tool_name, "tool_input": tool_input},
            env_overrides=_plan_file_state_env(tmp_path, tmp_path / "home"),
        )
        assert result.returncode == 0
        assert "colloquial" not in _agent_messages(result)

    @pytest.mark.parametrize("name", ["colloquial.detail.md", "colloquial.bugs.md"])
    def test_detail_file_skips_colloquial_warning(self, tmp_path: pathlib.Path, deny_substring: str, name: str) -> None:
        """計画ファイル（詳細）と計画ファイル（バグ）は口語警告を出力しない。"""
        detail = _make_plan_file(tmp_path / "home", name)
        content = f"概要は{deny_substring}該当する。\n"
        result = _run(
            {"tool_name": "Write", "tool_input": {"file_path": str(detail), "content": content}},
            env_overrides=_plan_file_state_env(tmp_path, tmp_path / "home"),
        )
        assert result.returncode == 0
        assert "colloquial" not in _agent_messages(result)


class TestUserFacingTextChecks:
    """ユーザーが直接読む質問・計画本文へ共通本文検査を適用する。"""

    @pytest.mark.parametrize("field", ["question", "header", "label", "description", "plan"])
    @pytest.mark.parametrize("check", ["mojibake", "foreign", "colloquial"])
    def test_checks_each_user_facing_field(self, field: str, check: str, deny_substring: str) -> None:
        values = {
            "mojibake": "日本語の�本文",
            "foreign": "日本語に가が混入した本文",
            "colloquial": f"概要は{deny_substring}該当する。",
        }
        result = _run(_user_facing_payload(field, values[check]))

        if check == "colloquial":
            assert result.returncode == 0
            assert "口語的な日本語表現" in _additional_context(result)
            assert "Target:" not in _additional_context(result)
            assert f"検出語: {deny_substring}" in _agent_messages(result)
        else:
            assert result.returncode == 2
            expected = "U+FFFD" if check == "mojibake" else "日本語以外の文字"
            assert expected in result.stderr

    @pytest.mark.parametrize(
        "payload",
        [
            {"tool_name": "AskUserQuestion", "tool_input": {"questions": "invalid"}},
            {"tool_name": "AskUserQuestion", "tool_input": {"questions": []}},
            {
                "tool_name": "AskUserQuestion",
                "tool_input": {"questions": [{"question": "質問文", "header": "見出し", "options": "invalid"}]},
            },
            _user_facing_payload("question", "確認する対象を選択してください。"),
            {"tool_name": "ExitPlanMode", "tool_input": {"plan": "計画に従って実装する。"}},
        ],
    )
    def test_malformed_empty_or_clean_input_is_silent(self, payload: dict) -> None:
        result = _run(payload)
        assert result.returncode == 0
        assert result.stdout == ""
        assert result.stderr == ""

    def test_managed_temp_cwd_does_not_skip_user_facing_text(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, deny_substring: str
    ) -> None:
        managed = tmp_path / "managed"
        managed.mkdir()
        (managed / ".agent-toolkit-managed-temp.json").write_text("{}\n", encoding="utf-8")
        monkeypatch.chdir(managed)

        result = _run(_user_facing_payload("question", f"概要は{deny_substring}該当する。"))

        assert result.returncode == 0
        assert "口語的な日本語表現" in _additional_context(result)

    def test_empty_file_path_uses_ask_user_question_as_target(self, deny_substring: str) -> None:
        """質問入力の口語警告は空のパスではなくツール名を対象として示す。"""
        result = _run(_user_facing_payload("question", f"概要は{deny_substring}該当する。"))

        assert result.returncode == 0
        assert "対象: AskUserQuestion" in _additional_context(result)


class TestAskUserQuestionRequiredRead:
    """判断基準文書の全文読解を観測するまで質問を遮断する。"""

    def test_unread_is_blocked_and_exit_plan_mode_passes(self, tmp_path: pathlib.Path) -> None:
        env = _plan_file_state_env(tmp_path)
        question = _user_facing_payload("question", "確認する対象を選択してください。")
        question["session_id"] = "required-unread"

        blocked = _run(question, env_overrides=env)
        exit_plan = _run(
            {"session_id": "required-unread", "tool_name": "ExitPlanMode", "tool_input": {"plan": "実装する。"}},
            env_overrides=env,
        )

        assert blocked.returncode == 2
        assert required_reads.document_path() in blocked.stderr
        assert exit_plan.returncode == 0

    def test_full_read_allows_question(self, tmp_path: pathlib.Path) -> None:
        env = _plan_file_state_env(tmp_path)
        sid = "required-full"
        _run_posttooluse(
            {
                "session_id": sid,
                "tool_name": "Read",
                "tool_input": {"file_path": required_reads.document_path()},
            },
            env,
        )
        question = _user_facing_payload("question", "確認する対象を選択してください。")
        question["session_id"] = sid

        assert _run(question, env_overrides=env).returncode == 0

    @pytest.mark.parametrize(
        "tool_input",
        [
            {"file_path": required_reads.document_path(), "offset": 1},
            {"file_path": required_reads.document_path(), "limit": 10},
            {"file_path": "/tmp/copy/skills/review-standards/references/judgment-details.md"},
        ],
    )
    def test_partial_or_other_copy_does_not_allow_question(self, tmp_path: pathlib.Path, tool_input: dict) -> None:
        env = _plan_file_state_env(tmp_path)
        sid = f"required-partial-{len(str(tool_input))}"
        _run_posttooluse({"session_id": sid, "tool_name": "Read", "tool_input": tool_input}, env)
        question = _user_facing_payload("question", "確認する対象を選択してください。")
        question["session_id"] = sid

        assert _run(question, env_overrides=env).returncode == 2

    def test_bash_path_mention_does_not_allow_question(self, tmp_path: pathlib.Path) -> None:
        env = _plan_file_state_env(tmp_path)
        sid = "required-bash"
        _run_posttooluse(
            {"session_id": sid, "tool_name": "Bash", "tool_input": {"command": f"echo {required_reads.document_path()}"}},
            env,
        )
        question = _user_facing_payload("question", "確認する対象を選択してください。")
        question["session_id"] = sid

        assert _run(question, env_overrides=env).returncode == 2


class TestPlanModeSkillFirstCheck:
    """plan fileの起草編集でplan-modeスキル未起動を警告する検査（block降格済み）。

    plan-modeスキル未起動でもplan file以外の操作（Read・Bash・他Skill・通常ファイル編集等）は
    一切ブロックも警告もしない。新旧計画root配下の`*.md`に対する
    Writeと進捗ログ節外を変更するEdit/MultiEditが警告対象となる。`permission_mode`の値には依存しない。
    既存計画の一意かつ最後の`## 進捗ログ`節だけを変更するEdit/MultiEditは警告しない。
    完成条件を満たさない状態での次工程移行の抑止は`ExitPlanMode`のブロックへ集約する。
    """

    _state_env = staticmethod(_plan_file_state_env)
    _make_plan = staticmethod(_make_plan_file)

    @pytest.mark.parametrize("name", ["plan.md", "plan.bugs.md"])
    def test_warns_plan_file_write_without_skill(self, tmp_path: pathlib.Path, name: str):
        home = tmp_path / "home"
        plan = self._make_plan(home, name)
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
        messages = _agent_messages(result)
        assert "plan-mode" in messages
        assert "Phase 1" in messages
        assert "委譲した計画をレビュー" in messages
        assert "成果物と根拠から一意に定まる値だけを訂正" in messages
        assert "`plan-mode`をやり直さずに続行" in messages
        assert "訂正内容と根拠を`## 変更履歴`へ記録" in messages
        assert "[auto-generated: agent-toolkit/pretooluse][warn]" in messages
        assert "計画ファイルを編集している" in _additional_context(result)
        assert "計画ファイルを編集している" not in result.stderr

    def test_warns_private_notes_plan_file_write_without_skill(self, tmp_path: pathlib.Path) -> None:
        """新しいprivate-notes計画rootのWriteもplan-mode未起動として警告する。"""
        private_notes = tmp_path / "private-notes"
        plan = _make_private_notes_plan_file(private_notes, "30-計画保存先移行-a1b2.md")
        env = self._state_env(tmp_path)
        env["AGENT_TOOLKIT_PRIVATE_NOTES"] = str(private_notes)
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": "# t\n"},
                "session_id": "private-notes-plan-write",
                "permission_mode": "plan",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        assert "計画ファイルを編集している" in _additional_context(result)

    @pytest.mark.parametrize("name", ["edit.md", "edit.bugs.md"])
    def test_warns_plan_file_edit_without_skill(self, tmp_path: pathlib.Path, name: str):
        home = tmp_path / "home"
        plan = self._make_plan(home, name)
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

    @pytest.mark.parametrize("tool_name", ["Edit", "MultiEdit"])
    def test_allows_progress_log_only_edit_without_skill(self, tmp_path: pathlib.Path, tool_name: str) -> None:
        """進捗ログ節だけを変更するEditとMultiEditは受領側の正規操作として許容する。"""
        home = tmp_path / "home"
        plan = self._make_plan(home, "progress-only.md")
        plan.write_text(
            "# 計画\n\n## 概要\n\n本文\n\n## 完了条件\n\n維持\n\n## 進捗ログ\n\n旧工程\n旧結果\n",
            encoding="utf-8",
        )
        if tool_name == "Edit":
            tool_input = {
                "file_path": str(plan),
                "old_string": "旧工程\n",
                "new_string": "旧工程\n新工程\n",
            }
        else:
            tool_input = {
                "file_path": str(plan),
                "edits": [
                    {"old_string": "旧工程", "new_string": "新工程"},
                    {"old_string": "旧結果", "new_string": "新結果"},
                ],
            }
        result = _run(
            {
                "tool_name": tool_name,
                "tool_input": tool_input,
                "session_id": f"progress-only-{tool_name.lower()}",
                "permission_mode": "default",
            },
            env_overrides=self._state_env(tmp_path, home),
        )
        assert result.returncode == 0
        assert "editing a plan file without invoking" not in _agent_messages(result)

    @pytest.mark.parametrize("heading", ["## 進捗ログ", "## 進捗ログ（実行時）"])
    def test_progress_log_only_edit_accepts_legacy_and_canonical_headings(self, tmp_path: pathlib.Path, heading: str) -> None:
        """進捗ログだけの編集許可判定は新旧の固定見出しを同じ節として扱う。"""
        home = tmp_path / "home"
        plan = self._make_plan(home, "progress-alias.md")
        plan.write_text(f"# 計画\n\n## 概要\n\n本文\n\n{heading}\n\n旧工程\n", encoding="utf-8")
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {"file_path": str(plan), "old_string": "旧工程", "new_string": "新工程"},
                "session_id": "progress-alias-edit",
                "permission_mode": "default",
            },
            env_overrides=self._state_env(tmp_path, home),
        )
        assert result.returncode == 0
        assert "editing a plan file without invoking" not in _agent_messages(result)

    @pytest.mark.parametrize("tool_name", ["Edit", "MultiEdit"])
    def test_warns_edit_outside_progress_log_without_skill(self, tmp_path: pathlib.Path, tool_name: str) -> None:
        """進捗ログより前を変更するEditと節内外混在MultiEditは警告する。"""
        home = tmp_path / "home"
        plan = self._make_plan(home, "outside-progress.md")
        plan.write_text(
            "# 計画\n\n## 概要\n\n本文\n\n## 完了条件\n\n旧条件\n\n## 進捗ログ\n\n旧工程\n",
            encoding="utf-8",
        )
        edits = [{"old_string": "旧条件", "new_string": "新条件"}]
        if tool_name == "MultiEdit":
            edits.append({"old_string": "旧工程", "new_string": "新工程"})
        tool_input = {"file_path": str(plan), **edits[0]} if tool_name == "Edit" else {"file_path": str(plan), "edits": edits}
        result = _run(
            {
                "tool_name": tool_name,
                "tool_input": tool_input,
                "session_id": f"outside-progress-{tool_name.lower()}",
                "permission_mode": "default",
            },
            env_overrides=self._state_env(tmp_path, home),
        )
        assert result.returncode == 0
        assert "計画ファイルを編集している" in _agent_messages(result)

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
                # 通常の品質スキルを選び、計画単位の状態検査と分離する。
                {"skill": "agent-toolkit:writing-standards"},
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
        _write_session_state(tmp_path, sid, {})
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
        assert "plan-mode" in _agent_messages(result)


class TestPlanModeSkillCallSites:
    """plan-modeスキル呼び出しの素通り保証。

    plan-mode起動時の計画単位リセット以外の委譲状態を参照せず、`returncode`は0を保つ。
    """

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
                "tool_input": {"skill": "agent-toolkit:writing-standards"},
                "session_id": "other-skill",
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        assert result.stdout == ""


class TestPlanFileDoesNotRequireTextlintRead:
    """計画編集前の文章lint資料読了条件が撤去済みであることを検証する。

    `permission_mode`の値に依らず、新旧計画root配下の`*.md`に対する
    Write/Edit/MultiEditのみが警告対象となる。plan file以外の操作は
    一切ブロック・警告しない。完成条件を満たさない状態での次工程移行の抑止は
    `ExitPlanMode`のブロックへ集約する。
    """

    _state_env = staticmethod(_plan_file_state_env)
    _make_plan = staticmethod(_make_plan_file)

    def test_write_without_read_does_not_warn(self, tmp_path: pathlib.Path):
        """資料未読のWriteを警告しない。"""
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
        assert "textlint-violations.md" not in result.stderr

    def test_edit_without_read_does_not_warn(self, tmp_path: pathlib.Path):
        """資料未読のEditを警告しない。"""
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
        assert "textlint-violations.md" not in result.stderr

    def test_legacy_read_flag_does_not_change_result(self, tmp_path: pathlib.Path):
        """旧読了フラグが残るセッションでも計画編集を妨げない。"""
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
        output = json.loads(result.stdout)
        assert "permissionDecision" not in output["hookSpecificOutput"]
        ctx = _additional_context(result)
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
        assert "英語主体" not in _additional_context(result)

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

    @pytest.mark.parametrize(("delegated", "warns"), [(False, True), (True, False)])
    def test_delegated_session_language_check_boundary(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        delegated: bool,
        warns: bool,
    ) -> None:
        """agents_serverの委譲先だけを言語検査から除外する。"""
        transcript = self._write_transcript(tmp_path, "This is a plain English status report for the current task.")
        if delegated:
            monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
        else:
            monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)

        result = _run(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "ls"},
                "transcript_path": str(transcript),
            }
        )

        assert result.returncode == 0
        assert ("英語主体" in _additional_context(result)) is warns

    def test_no_warn_without_transcript_path(self):
        """transcript_path未指定なら検査スキップ。"""
        result = _run({"tool_name": "Bash", "tool_input": {"command": "ls"}})
        assert result.returncode == 0
        assert result.stdout == ""

    def test_warns_for_text_turn_before_tool_only_turn(self, tmp_path: pathlib.Path) -> None:
        transcript = tmp_path / "transcript.jsonl"
        entries = [
            {
                "type": "assistant",
                "message": {"id": "text", "role": "assistant", "content": [{"type": "text", "text": "English response only."}]},
            },
            {
                "type": "assistant",
                "message": {"id": "tool", "role": "assistant", "content": [{"type": "tool_use", "name": "Bash"}]},
            },
        ]
        transcript.write_text("\n".join(json.dumps(entry) for entry in entries) + "\n", encoding="utf-8")

        result = _run({"tool_name": "Bash", "tool_input": {"command": "ls"}, "transcript_path": str(transcript)})

        assert result.returncode == 0
        assert "英語主体" in _additional_context(result)


class TestBlockCheckExecutionOrder:
    """複数のblock系checkが同時に違反する場合の先行check契約を検証する。"""

    def test_direct_edit_block_preempts_retroactive_scan_block(self, tmp_path: pathlib.Path) -> None:
        plan = _write_tmp_file(tmp_path, "home/.claude/plans/current.md", "## 実装資料\n\nなし\n")
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
        assert "`Write`・`Edit`・`MultiEdit`" in result.stderr
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
        assert "permissionDecision" not in output["hookSpecificOutput"]
        context = output["hookSpecificOutput"]["additionalContext"]
        commit_warning = "テストを実行せずにcommit"
        language_warning = "英語主体"
        assert commit_warning in context
        assert language_warning in context
        assert context.index(commit_warning) < context.index(language_warning)
        assert "\n\n" in context[context.index(commit_warning) : context.index(language_warning)]

    def test_language_warning_preserves_allow_and_updated_input(self, tmp_path: pathlib.Path) -> None:
        """入力書き換えへ警告を合成しても明示許可と変更後入力を維持する。"""
        transcript = tmp_path / "transcript-git-log.jsonl"
        transcript.write_text(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "id": "m-language-git-log",
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
        result = _run(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "git log --oneline -1"},
                "transcript_path": str(transcript),
            }
        )
        output = json.loads(result.stdout)["hookSpecificOutput"]
        assert output["permissionDecision"] == "allow"
        assert "--decorate" in output["updatedInput"]["command"]
        assert "英語主体" in output["additionalContext"]

    def test_append_additional_context_creates_warning_only_output(self) -> None:
        """出力本体が無い場合も警告追加だけでは明示許可しない。"""
        result: dict = {}
        pretooluse._append_additional_context(result, "warning")  # noqa: SLF001  # pylint: disable=protected-access
        assert result == {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": "warning",
            }
        }
