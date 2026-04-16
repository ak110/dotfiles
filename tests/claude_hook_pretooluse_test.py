"""scripts/claude_hook_pretooluse.py のテスト。

dotfiles 個人環境専用の PreToolUse フックのテスト。
mojibake / PS1 EOL は plugin 側 (plugins/agent-toolkit) に移管済み。
独立スクリプトなので subprocess で起動し exit code / stderr / stdout (JSON) を検証する。
"""

import json
import os
import pathlib
import subprocess
import sys

import pytest

_HOME = pathlib.Path.home()

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "claude_hook_pretooluse.py"

# 文字列リテラルで直接書くと本ファイル自身が警告を出す原因になるため、
# テスト対象の「言及される名前」はプログラム的に組み立てる。
_LOCAL_MD = "CLAUDE" + ".local.md"


def _run(payload: object, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(
        [sys.executable, str(_SCRIPT)],
        input=text,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


class TestHomeClaudeEditBlock:
    """`~/.claude/` 配下の直接編集ブロック。"""

    @pytest.mark.parametrize(
        "rel",
        [
            "settings.json",
            "CLAUDE.md",
            "rules/agent-basics/agent.md",
            "agents/foo.md",
            "skills/bar/SKILL.md",
            "plugins/agent-toolkit/hooks/hooks.json",
        ],
    )
    def test_blocked(self, rel: str):
        target = str(_HOME / ".claude" / rel)
        result = _run({"tool_name": "Write", "tool_input": {"file_path": target, "content": "x"}})
        assert result.returncode == 2
        assert ".chezmoi-source/dot_claude/" in result.stderr
        # LLM 宛てメッセージ規約: プレフィックスとサフィックスが付与されていること。
        assert "[auto-generated: pretooluse]" in result.stderr
        assert "Auto-generated hook notice" in result.stderr

    def test_edit_blocked(self):
        target = str(_HOME / ".claude" / "settings.json")
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {"file_path": target, "old_string": "a", "new_string": "b"},
            }
        )
        assert result.returncode == 2

    @pytest.mark.parametrize(
        "rel",
        [
            "plans/foo.md",  # plan mode が書き込む
            "projects/session.jsonl",  # Claude Code セッション
            "todos/todo.json",
            "shell-snapshots/foo.sh",
            "ide/cache.json",
            "statsig/cache",
            "settings.local.json",  # ローカル設定 (`.local.` を含む)
            "CLAUDE.local.md",  # ローカル メモ
            "rules/agent-basics/agent.local.md",  # サブディレクトリ配下でも `.local.` 系は許可
        ],
    )
    def test_allowed(self, rel: str):
        target = str(_HOME / ".claude" / rel)
        result = _run({"tool_name": "Write", "tool_input": {"file_path": target, "content": "x"}})
        assert result.returncode == 0

    def test_outside_home_claude_allowed(self):
        """`~/.claude/` 配下でなければ通す (例: `~/.claudette/foo` は別物)。"""
        target = str(_HOME / ".claudette" / "foo")
        result = _run({"tool_name": "Write", "tool_input": {"file_path": target, "content": "x"}})
        assert result.returncode == 0

    def test_chezmoi_source_allowed(self):
        """配布元の `.chezmoi-source/dot_claude/` 配下は通す。"""
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": "/tmp/proj/.chezmoi-source/dot_claude/CLAUDE.md",
                    "content": "x",
                },
            }
        )
        assert result.returncode == 0

    def test_relative_path_allowed(self):
        """相対パスは判定不能なため通す (誤検出を避ける)。"""
        result = _run({"tool_name": "Write", "tool_input": {"file_path": ".claude/settings.json", "content": "x"}})
        assert result.returncode == 0

    @pytest.mark.parametrize(
        "rel",
        [
            "./.claude/settings.json",  # 冗長な `.` セグメント
            "foo/../.claude/settings.json",  # `..` で戻る
            ".claude/./rules/agent-basics/agent.md",  # 途中の `.`
        ],
    )
    def test_blocked_with_non_canonical_segments(self, rel: str):
        """非正規化パス (`./` や `../`) でも resolve 後にブロックされること (I-1)。"""
        target = str(_HOME / rel)
        result = _run({"tool_name": "Write", "tool_input": {"file_path": target, "content": "x"}})
        assert result.returncode == 2
        assert ".chezmoi-source/dot_claude/" in result.stderr

    def test_symlinked_home_claude_blocked(self, tmp_path: pathlib.Path):
        """`~/.claude` がシンボリックリンクの場合でも resolve 後にブロックされること (I-1)。"""
        real_claude = tmp_path / "real_claude"
        real_claude.mkdir()
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        (fake_home / ".claude").symlink_to(real_claude)
        # Claude Code が resolve 後の実体パスを渡してくるケースを想定。
        target = str(real_claude / "settings.json")
        env = {**os.environ, "HOME": str(fake_home)}
        result = _run(
            {"tool_name": "Write", "tool_input": {"file_path": target, "content": "x"}},
            env=env,
        )
        assert result.returncode == 2
        assert ".chezmoi-source/dot_claude/" in result.stderr


class TestPs1DirectivesBlock:
    """PowerShell スクリプトの必須ディレクティブ欠落ブロック。"""

    _OK_HEADER = "Set-StrictMode -Version Latest\r\n$ErrorActionPreference = 'Stop'\r\n"

    @pytest.mark.parametrize("file_path", ["a.ps1", "scripts/foo.ps1.tmpl", "C:/x/setup.ps1"])
    def test_missing_both_blocks(self, file_path: str):
        result = _run({"tool_name": "Write", "tool_input": {"file_path": file_path, "content": "Write-Host 'x'\r\n"}})
        assert result.returncode == 2
        assert "Set-StrictMode" in result.stderr
        assert "ErrorActionPreference" in result.stderr

    def test_missing_only_strict_mode_blocks(self):
        content = "$ErrorActionPreference = 'Stop'\r\nWrite-Host 'x'\r\n"
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "a.ps1", "content": content}})
        assert result.returncode == 2
        assert "Set-StrictMode" in result.stderr

    def test_missing_only_error_action_blocks(self):
        content = "Set-StrictMode -Version Latest\r\nWrite-Host 'x'\r\n"
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "a.ps1", "content": content}})
        assert result.returncode == 2
        assert "ErrorActionPreference" in result.stderr

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

    def test_directives_after_50_lines_blocks(self):
        """先頭 50 行を超えた位置にしかディレクティブが無ければブロック。"""
        padding = "\r\n".join(f"# pad {i}" for i in range(60)) + "\r\n"
        content = padding + self._OK_HEADER
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "a.ps1", "content": content}})
        assert result.returncode == 2

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

    def test_directive_in_comment_blocks(self):
        """コメント行内に文字列だけ含まれる PS1 はブロックされること (I-2)。"""
        content = "# TODO: add Set-StrictMode -Version Latest and $ErrorActionPreference = 'Stop'\r\nWrite-Host 'x'\r\n"
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "a.ps1", "content": content}})
        assert result.returncode == 2
        assert "Set-StrictMode" in result.stderr
        assert "ErrorActionPreference" in result.stderr

    def test_indented_directive_blocks(self):
        """行頭にインデントされたディレクティブはブロックされること (I-2)。

        関数/条件ブロック内に書かれている可能性があり、スクリプト全体には効かないため。
        """
        content = "    " + self._OK_HEADER + "Write-Host 'x'\r\n"
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "a.ps1", "content": content}})
        assert result.returncode == 2

    def test_directive_with_extra_spaces_allowed(self):
        """`Set-StrictMode  -Version  Latest` のように空白が複数でも許可されること (`\\s+` パターン確認)。"""
        content = "Set-StrictMode  -Version  Latest\r\n$ErrorActionPreference  =  'Stop'\r\nWrite-Host 'x'\r\n"
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "a.ps1", "content": content}})
        assert result.returncode == 0


class TestPersonalFileMentionWarning:
    """個人用 / ローカル専用ファイル言及検出 (allow + additionalContext 警告)。

    対象は `CLAUDE.local.md` と、ファイル名に `___` (3連アンダースコア) を含むトークン。
    バックティック囲みの言及と、対象ファイル自身の編集は除外される。
    """

    # ``___`` を含むトークンもプログラム的に組み立てる (本テストファイル自身が警告を
    # 誘発しないようにするため)。
    _TRIPLE = "_" * 3
    # 正規表現 `\w+___\w+` がファイル名全体 (拡張子まで) を一致として抽出するわけではない点に注意。
    # `.` は word 文字でないため、マッチされるのは拡張子を除いた stem 部分 (`foo___bar`)。
    _TRIPLE_STEM = f"foo{_TRIPLE}bar"
    _TRIPLE_TOKEN = f"{_TRIPLE_STEM}.md"

    @staticmethod
    def _get_additional_context(result: subprocess.CompletedProcess[str]) -> str:
        """stdout の JSON から hookSpecificOutput.additionalContext を取得する。"""
        if not result.stdout.strip():
            return ""
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return ""
        return data.get("hookSpecificOutput", {}).get("additionalContext", "")

    # --- CLAUDE.local.md 言及 ---

    def test_content_reference_warns_but_passes(self):
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": "docs/guide.md", "content": f"See {_LOCAL_MD} for details."},
            }
        )
        assert result.returncode == 0
        msg = self._get_additional_context(result)
        assert _LOCAL_MD in msg
        assert "warn" in msg.lower()
        # LLM 宛てメッセージ規約: プレフィックスとサフィックスが付与されていること。
        assert "[auto-generated: pretooluse][warn]" in msg
        assert "Auto-generated hook notice" in msg

    def test_edit_reference_warns_but_passes(self):
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": "README.md",
                    "old_string": "foo",
                    "new_string": f"Refer to {_LOCAL_MD}",
                },
            }
        )
        assert result.returncode == 0
        assert _LOCAL_MD in self._get_additional_context(result)

    def test_multiedit_reference_warns_but_passes(self):
        result = _run(
            {
                "tool_name": "MultiEdit",
                "tool_input": {
                    "file_path": "README.md",
                    "edits": [
                        {"old_string": "a", "new_string": "b"},
                        {"old_string": "c", "new_string": f"See {_LOCAL_MD}"},
                    ],
                },
            }
        )
        assert result.returncode == 0
        assert _LOCAL_MD in self._get_additional_context(result)

    def test_backtick_wrapped_reference_also_warns(self):
        """バックティック囲みでも警告は出す (文脈依存のため最終判断は LLM に委ねる)。"""
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": "docs/guide.md",
                    "content": f"Recommended: create `{_LOCAL_MD}` in your project.",
                },
            }
        )
        assert result.returncode == 0
        assert _LOCAL_MD in self._get_additional_context(result)

    def test_editing_target_file_itself_is_allowed_silently(self):
        """対象ファイル自体の編集は正当な操作として警告も出さない。"""
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": f"/home/user/proj/{_LOCAL_MD}", "content": f"# {_LOCAL_MD}\nmemo"},
            }
        )
        assert result.returncode == 0
        assert result.stdout == ""

    def test_editing_target_file_with_windows_path_allowed_silently(self):
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": f"C:\\proj\\{_LOCAL_MD}",
                    "content": f"memo {_LOCAL_MD}",
                },
            }
        )
        assert result.returncode == 0
        assert result.stdout == ""

    def test_old_string_reference_is_allowed_silently(self):
        """言及を削除する Edit は old_string に書いてあっても警告しない。"""
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": "README.md",
                    "old_string": f"See {_LOCAL_MD}",
                    "new_string": "See docs",
                },
            }
        )
        assert result.returncode == 0
        assert result.stdout == ""

    # --- ファイル名に `___` を含むトークンの言及 ---

    def test_triple_underscore_mention_warns_but_passes(self):
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": "docs/guide.md",
                    "content": f"See {self._TRIPLE_TOKEN} for details.",
                },
            }
        )
        assert result.returncode == 0
        msg = self._get_additional_context(result)
        assert self._TRIPLE_STEM in msg
        assert "___" in msg
        assert "warn" in msg.lower()

    def test_triple_underscore_in_backticks_also_warns(self):
        """バックティック囲みでも警告は出す (文脈依存のため最終判断は LLM に委ねる)。"""
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": "docs/guide.md",
                    "content": f"Recommended: create `{self._TRIPLE_TOKEN}` locally.",
                },
            }
        )
        assert result.returncode == 0
        assert self._TRIPLE_STEM in self._get_additional_context(result)

    def test_triple_underscore_self_edit_is_allowed_silently(self):
        """ファイル名自体に `___` を含むファイルの作成・編集は除外。"""
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": f"/home/user/notes/{self._TRIPLE_TOKEN}",
                    "content": f"memo referencing {self._TRIPLE_TOKEN}",
                },
            }
        )
        assert result.returncode == 0
        assert result.stdout == ""

    def test_bare_triple_underscore_not_matched(self):
        """区切り記号などに使われる裸の `___` (前後に word 文字なし) は検出しない。"""
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": "docs/guide.md",
                    "content": "separator: ___ end",
                },
            }
        )
        assert result.returncode == 0
        assert result.stdout == ""

    def test_seven_underscore_mention_warns(self):
        """7 文字の連続アンダースコアは警告対象。"""
        sep7 = "_" * 7
        stem = f"foo{sep7}bar"
        token = f"{stem}.md"
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": "docs/guide.md",
                    "content": f"See {token} for details.",
                },
            }
        )
        assert result.returncode == 0
        msg = self._get_additional_context(result)
        assert stem in msg
        assert "warn" in msg.lower()

    def test_eight_underscore_mention_ignored(self):
        """8 文字以上の連続アンダースコアは装飾用途とみなし警告しない。"""
        sep8 = "_" * 8
        token = f"foo{sep8}bar"
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": "docs/guide.md",
                    "content": f"See {token} for details.",
                },
            }
        )
        assert result.returncode == 0
        assert result.stdout == ""

    def test_long_underscore_self_edit_not_excluded(self):
        """8 文字以上のアンダースコアを含むファイル名は個人ファイルとみなさず除外しない。"""
        sep8 = "_" * 8
        long_name = f"foo{sep8}bar.md"
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": f"/home/user/notes/{long_name}",
                    "content": f"memo referencing {self._TRIPLE_TOKEN}",
                },
            }
        )
        assert result.returncode == 0
        msg = self._get_additional_context(result)
        assert self._TRIPLE_STEM in msg

    def test_both_patterns_reported_together(self):
        """`CLAUDE.local.md` と `___` の両方が言及されたら両方報告する。"""
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": "docs/guide.md",
                    "content": f"Refer to {_LOCAL_MD} and {self._TRIPLE_TOKEN}.",
                },
            }
        )
        assert result.returncode == 0
        msg = self._get_additional_context(result)
        assert _LOCAL_MD in msg
        assert self._TRIPLE_STEM in msg


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
