"""pytools/claude_hook/pretooluse.py のテスト。

dotfiles 個人環境専用の PreToolUse フックのテスト。
mojibake / PS1 EOL は plugin 側 (agent-toolkit) が担う。
独立スクリプトなのでfork-server経由（フォールバック時はsubprocess）で起動し
exit code / stderr / stdout (JSON) を検証する。
"""

import json
import pathlib
import subprocess

import pytest
from agent_toolkit._testing import fork_runner as _fork_runner

_HOME = pathlib.Path.home()

_SCRIPT = pathlib.Path(__file__).resolve().parent / "__init__.py"
_DOTFILES_ROOT = pathlib.Path(__file__).resolve().parents[2]
_AT_DIR = _DOTFILES_ROOT / "agent-toolkit"
_TOOLKIT_PREFIX = "agent-" + "toolkit"
_AT_RULES_DIR = _AT_DIR / "rules"

# 文字列リテラルで直接書くと本ファイル自身が警告を発する原因になるため、
# テスト対象の「言及される名前」はプログラム的に組み立てる。
_LOCAL_MD = "CLAUDE" + ".local.md"

# `uv tool run` / `uvx` に続くコマンド名の並びも、本ファイル自身がブロックされないよう分割して組み立てる。
_PYTOOLS_COMMAND = "claude-session-" + "export"
_UV_TOOL_RUN = "uv tool " + "run"
_UVX = "uv" + "x"


def _run(payload: object, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return _fork_runner.run_script(_SCRIPT, argv=("pretooluse",), input=text, env=env)


def _get_additional_context(result: subprocess.CompletedProcess[str]) -> str:
    """stdout の JSON から hookSpecificOutput.additionalContext を取得する。"""
    if not result.stdout.strip():
        return ""
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return ""
    return data.get("hookSpecificOutput", {}).get("additionalContext", "")


class TestPs1DirectivesBlock:
    """PowerShell スクリプトの必須ディレクティブ欠落警告。

    書き込んだファイルは再編集で復元できるため遮断せず、検出条件は格下げ前と同じとする。
    """

    _OK_HEADER = "Set-StrictMode -Version Latest\r\n$ErrorActionPreference = 'Stop'\r\n"

    @pytest.mark.parametrize("file_path", ["a.ps1", "scripts/foo.ps1.tmpl", "C:/x/setup.ps1"])
    def test_missing_both_warns(self, file_path: str):
        result = _run({"tool_name": "Write", "tool_input": {"file_path": file_path, "content": "Write-Host 'x'\r\n"}})
        assert result.returncode == 0
        message = _get_additional_context(result)
        assert "Set-StrictMode" in message
        assert "ErrorActionPreference" in message

    def test_missing_only_strict_mode_warns(self):
        content = "$ErrorActionPreference = 'Stop'\r\nWrite-Host 'x'\r\n"
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "a.ps1", "content": content}})
        assert result.returncode == 0
        assert "Set-StrictMode" in _get_additional_context(result)

    def test_missing_only_error_action_warns(self):
        content = "Set-StrictMode -Version Latest\r\nWrite-Host 'x'\r\n"
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "a.ps1", "content": content}})
        assert result.returncode == 0
        assert "ErrorActionPreference" in _get_additional_context(result)

    def test_both_present_at_top_allowed(self):
        result = _run(
            {"tool_name": "Write", "tool_input": {"file_path": "a.ps1", "content": self._OK_HEADER + "Write-Host 'x'\r\n"}}
        )
        assert result.returncode == 0

    def test_both_present_after_comment_block_allowed(self):
        """先頭コメントブロックの後に書かれていても 50 行以内なら許可。"""
        comments = "\r\n".join(f"# comment {i}" for i in range(20)) + "\r\n"
        content = comments + self._OK_HEADER + "Write-Host 'x'\r\n"
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "a.ps1", "content": content}})
        assert result.returncode == 0

    def test_bom_prefixed_template_allowed(self):
        """chezmoi テンプレートで使われる先頭 BOM は除去してから判定する。"""
        content = "\ufeff" + self._OK_HEADER + "{{ .chezmoi.homeDir }}\r\n"
        result = _run({"tool_name": "Edit", "tool_input": {"file_path": "a.ps1.tmpl", "old_string": "x", "new_string": "y"}})
        # Edit/MultiEdit は対象外なので無条件に通る
        assert result.returncode == 0
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "a.ps1.tmpl", "content": content}})
        assert result.returncode == 0

    def test_directives_after_50_lines_warns(self):
        """先頭 50 行を超えた位置にしかディレクティブが無ければ警告する。"""
        padding = "\r\n".join(f"# pad {i}" for i in range(60)) + "\r\n"
        content = padding + self._OK_HEADER
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "a.ps1", "content": content}})
        assert result.returncode == 0
        assert "Set-StrictMode" in _get_additional_context(result)

    def test_edit_skipped(self):
        """Edit はファイル先頭を含まないことが多いため対象外として通す。"""
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {"file_path": "a.ps1", "old_string": "Old", "new_string": "New"},
            }
        )
        assert result.returncode == 0

    def test_multiedit_skipped(self):
        result = _run(
            {
                "tool_name": "MultiEdit",
                "tool_input": {
                    "file_path": "a.ps1",
                    "edits": [{"old_string": "a", "new_string": "b"}],
                },
            }
        )
        assert result.returncode == 0

    def test_non_ps1_skipped(self):
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "a.sh", "content": "echo hi\n"}})
        assert result.returncode == 0

    def test_directive_in_comment_warns(self):
        """コメント行内に文字列だけ含まれる PS1 は検出されること (I-2)。"""
        content = "# TODO: add Set-StrictMode -Version Latest and $ErrorActionPreference = 'Stop'\r\nWrite-Host 'x'\r\n"
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "a.ps1", "content": content}})
        assert result.returncode == 0
        message = _get_additional_context(result)
        assert "Set-StrictMode" in message
        assert "ErrorActionPreference" in message

    def test_indented_directive_warns(self):
        """行頭にインデントされたディレクティブは検出されること (I-2)。

        関数/条件ブロック内に書かれている可能性があり、スクリプト全体には適用されないため。
        """
        content = "    " + self._OK_HEADER + "Write-Host 'x'\r\n"
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "a.ps1", "content": content}})
        assert result.returncode == 0
        assert "Set-StrictMode" in _get_additional_context(result)

    def test_directive_with_extra_spaces_allowed(self):
        """`Set-StrictMode  -Version  Latest` のように空白が複数でも許可されること (`\\s+` パターン確認)。"""
        content = "Set-StrictMode  -Version  Latest\r\n$ErrorActionPreference  =  'Stop'\r\nWrite-Host 'x'\r\n"
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "a.ps1", "content": content}})
        assert result.returncode == 0


class TestAgentToolkitDotfilesNamesCheck:
    """agent-toolkit 配布物への dotfiles 固有名混入検出 (block + warn)。

    対象は `agent-toolkit/` 配下。
    block 対象は配布先のエンドユーザーにとって意味不明な参照となるため exit 2 で停止する。
    warn 対象 (pyfltr / pytilpack) は OSS として正規参照される場合があるため通知のみ。
    """

    @pytest.mark.parametrize(
        "name",
        [
            "ak110-projects-operations",  # 個人スキル名 (.chezmoi-source/dot_claude/skills/)
            "sync-platform-pair",  # dotfiles スキル名 (.claude/skills/)
            "claude-session-export",  # pytools コマンド名 (project.scripts)
            "psgrep",  # pytools コマンド名
            "agent_toolkit_bump",  # scripts 名
            "glatasks",  # 固定プロジェクト名
            "gv",
            "lc",
            "smpr",
        ],
    )
    def test_warn_when_target_is_in_agent_toolkit_with_specific_name(self, name: str):
        target = str(_AT_DIR / "skills" / "example" / "SKILL.md")
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": target, "content": f"See {name} for details."},
            }
        )
        assert result.returncode == 0
        message = _get_additional_context(result)
        assert name in message
        assert "generalized wording" in message
        assert '<agent-toolkit-auto-inserted source="dotfiles/claude_hook_pretooluse"' in message

    def test_warn_in_agent_toolkit_rules(self):
        target = str(_AT_RULES_DIR / "01-agent.md")
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": target,
                    "old_string": "x",
                    "new_string": "Refer to glatasks for details.",
                },
            }
        )
        assert result.returncode == 0
        assert "glatasks" in _get_additional_context(result)

    def test_warn_in_multiedit(self):
        target = str(_AT_DIR / "skills" / "example" / "SKILL.md")
        result = _run(
            {
                "tool_name": "MultiEdit",
                "tool_input": {
                    "file_path": target,
                    "edits": [
                        {"old_string": "a", "new_string": "harmless"},
                        {"old_string": "c", "new_string": "See smpr usage."},
                    ],
                },
            }
        )
        assert result.returncode == 0
        assert "smpr" in _get_additional_context(result)

    @pytest.mark.parametrize("name", ["pyfltr", "pytilpack"])
    def test_warn_when_target_is_in_agent_toolkit(self, name: str):
        target = str(_AT_DIR / "skills" / "example" / "SKILL.md")
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": target, "content": f"See {name} for details."},
            }
        )
        assert result.returncode == 0
        msg = _get_additional_context(result)
        assert name in msg
        assert "warn" in msg.lower()

    def test_specific_and_oss_names_are_reported_together(self):
        """固有名とOSS名の双方が成立する場合は、いずれも同じ警告本文へ載せる。"""
        target = str(_AT_DIR / "skills" / "example" / "SKILL.md")
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": target,
                    "content": "Use pyfltr alongside glatasks tooling.",
                },
            }
        )
        assert result.returncode == 0
        message = _get_additional_context(result)
        assert "glatasks" in message
        assert "pyfltr" in message

    def test_outside_distribution_silently_allowed(self):
        """配布範囲外のファイル (例: scripts/) では混入しても通す。"""
        target = str(_DOTFILES_ROOT / "scripts" / "fictional.py")
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": target, "content": "Refer to glatasks and gv."},
            }
        )
        assert result.returncode == 0
        assert result.stdout == ""

    def test_relative_path_silently_allowed(self):
        """相対パスは判定不能なため通す (誤検出を避ける)。"""
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": f"{_TOOLKIT_PREFIX}/skills/example/SKILL.md",
                    "content": "Refer to glatasks.",
                },
            }
        )
        assert result.returncode == 0
        assert result.stdout == ""

    def test_word_boundary_avoids_substring_match(self):
        """単語境界マッチで部分一致は検出しない (短い名前 `gv` / `lc` の誤検出回避)。"""
        target = str(_AT_DIR / "skills" / "example" / "SKILL.md")
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": target,
                    "content": "Use pygvX-extension and pylcY-tool.",
                },
            }
        )
        assert result.returncode == 0
        assert result.stdout == ""

    def test_clean_content_silently_allowed(self):
        target = str(_AT_DIR / "skills" / "example" / "SKILL.md")
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": target,
                    "content": "Plain documentation without project-specific references.",
                },
            }
        )
        assert result.returncode == 0
        assert result.stdout == ""

    def test_old_string_not_inspected(self):
        """new_string のみが対象。old_string に違反語があっても通す。"""
        target = str(_AT_DIR / "skills" / "example" / "SKILL.md")
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": target,
                    "old_string": "Refer to glatasks for details.",
                    "new_string": "Refer to the upstream project.",
                },
            }
        )
        assert result.returncode == 0
        assert result.stdout == ""


class TestGeneralBehavior:
    """共通の振る舞い。"""

    @pytest.mark.parametrize(
        "payload",
        [
            # Write/Edit/MultiEdit 以外は全て通す
            {"tool_name": "Bash", "tool_input": {"command": "echo test"}},
            # tool_input が欠落していても通す
            {"tool_name": "Write"},
            # 通常の日本語は通す
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

    def test_mojibake_no_longer_blocks(self):
        """mojibake チェックは plugin 側に移管されたため dotfiles 側では通す。"""
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "a.txt", "content": "hello \ufffd world"}})
        assert result.returncode == 0

    def test_ps1_lf_no_longer_blocks(self):
        """PS1 EOL チェックは plugin 側に移管されたため dotfiles 側では通す。

        新しい必須ディレクティブ チェックには引っかからないよう両ディレクティブを LF 改行で含めて検証する。
        """
        content = "Set-StrictMode -Version Latest\n$ErrorActionPreference = 'Stop'\nWrite-Host 'x'\n"
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "a.ps1", "content": content}})
        assert result.returncode == 0
