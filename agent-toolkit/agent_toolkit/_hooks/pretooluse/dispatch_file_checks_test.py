# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
# pylint: disable=protected-access
"""dispatch_file_checksの契約テスト。"""

import json

import pytest

from agent_toolkit._testing.dispatch_support import _existing_work_contract as _existing_work_contract
from agent_toolkit._testing.helpers import auto_message_opening_attributes
from agent_toolkit._testing.pretooluse_support import _additional_context, _agent_messages, _user_facing_payload
from agent_toolkit._testing.pretooluse_support import run_after_read_delivery as _run


class TestMojibakeCheck:
    """文字化け（U+FFFD）検出。

    編集対象もユーザーが直接読む本文も再編集で是正できるため、いずれも警告で返す。
    """

    def test_user_facing_text_with_mojibake_is_warned(self):
        result = _run({"tool_name": "ExitPlanMode", "tool_input": {"plan": "内容を確認" + chr(0xFFFD) + "してください。"}})
        assert result.returncode == 0
        context = _additional_context(result)
        assert "U+FFFD" in context
        assert "U+FFFDを意図した文字へ置き換えて再実行" in context

    def test_write_with_mojibake(self):
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "/tmp/a.txt", "content": "hello \ufffd world"}})
        assert result.returncode == 0
        context = _additional_context(result)
        assert "U+FFFD" in context
        # エージェント宛てメッセージの規約: XMLの開始境界と終了境界が付与されていること。
        assert auto_message_opening_attributes(context) == {"source": "pretooluse", "kind": "warn"}
        assert "\n次の操作: U+FFFDを意図した文字へ置き換えて再実行" in context
        assert context.endswith("</atk-auto>")

    def test_edit_with_mojibake(self):
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {"file_path": "/tmp/a.txt", "old_string": "foo", "new_string": "bar\ufffd"},
            }
        )
        assert result.returncode == 0
        assert "U+FFFD" in _additional_context(result)

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
        assert result.returncode == 0
        assert "U+FFFD" in _additional_context(result)

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
    """PowerShell ファイルへの LF-only 書き込み検出（警告）。"""

    def test_ps1_with_lf_only_warns(self):
        content = "Set-StrictMode\nWrite-Host 'x'\n"
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "C:/x/a.ps1", "content": content}})
        assert result.returncode == 0
        context = _additional_context(result)
        assert "`LF`だけの内容" in context
        assert "`UTF-8`の`BOM`が失われて日本語が文字化け" in context
        assert "*.ps1 text eol=crlf" in context
        assert (
            "\n次の操作: `agent-basics:writing-standards`の`references/encoding.md`「書込ツールの改行・BOM保全」に従い"
            in context
        )
        assert "既存ファイルには`Edit`ツールを使" in context

    def test_ps1_tmpl_edit_with_lf_only_allowed(self):
        """Edit は内部的に CRLF を維持するため、LF-only でもブロックしない。"""
        content = "Set-StrictMode\n{{ .chezmoi.homeDir }}\n"
        result = _run({"tool_name": "Edit", "tool_input": {"file_path": "./a.ps1.tmpl", "new_string": content}})
        assert result.returncode == 0

    def test_ps1_tmpl_write_with_lf_only_warns(self):
        """Write は LF のまま書き込むため警告する。"""
        content = "Set-StrictMode\n{{ .chezmoi.homeDir }}\n"
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "./a.ps1.tmpl", "content": content}})
        assert result.returncode == 0
        assert "`LF`だけの内容" in _additional_context(result)

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
    """lockfile / 生成物ディレクトリの直接編集警告。

    手編集した内容はパッケージ管理ツールの再生成で復元できるため遮断しない。
    """

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
    def test_write_warned(self, file_path: str):
        result = _run({"tool_name": "Write", "tool_input": {"file_path": file_path, "content": "x"}})
        assert result.returncode == 0
        context = _additional_context(result)
        assert "直接編集" in context
        assert "\n次の操作: " in context

    def test_edit_cargo_lock_warned(self):
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {"file_path": "Cargo.lock", "old_string": "a", "new_string": "b"},
            }
        )
        assert result.returncode == 0
        context = _additional_context(result)
        assert "cargo add" in context
        assert "\n次の操作: " in context

    def test_normal_file_allowed(self):
        """lockfile 名を部分的に含むだけのパスは通過する (例: uv.lock.bak)。"""
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "uv.lock.bak", "content": "x"}})
        assert result.returncode == 0


class TestManifestCheck:
    """manifest 手編集の警告 (warn のみ、exit code は 0)。

    lockfileとの同期が失われるのは依存の節を変える編集に限るため、
    その節へ触れない編集では通知しない。
    """

    def test_pyproject_toml_dependency_edit_warns(self):
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": "pyproject.toml",
                    "old_string": "a",
                    "new_string": 'dependencies = ["httpx"]',
                },
            }
        )
        assert result.returncode == 0
        assert "pyproject.toml" in _additional_context(result)
        assert "uv add" in _additional_context(result)
        # 編集警告はadditionalContextへ集約し、stderrは空のままとする。
        assert result.stderr == ""

    def test_pyproject_toml_tool_section_edit_is_silent(self):
        """`[tool.*]`と版数だけを変える編集では通知しない。"""
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": "pyproject.toml",
                    "old_string": 'version = "1.0.0"',
                    "new_string": 'version = "1.0.1"',
                },
            }
        )
        assert result.returncode == 0
        assert "pyproject.toml" not in _agent_messages(result)

    def test_package_json_warns(self):
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": "app/package.json", "content": '{"dependencies": {"x": "1"}}'},
            }
        )
        assert result.returncode == 0
        assert "package.json" in _additional_context(result)
        assert "pnpm add" in _additional_context(result)

    def test_normal_file_no_warn(self):
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "foo.txt", "content": "x"}})
        assert result.returncode == 0
        assert _agent_messages(result).strip() == ""


class TestUserFacingTextChecks:
    """ユーザーが直接読む質問・計画本文に文字化けがあるか確かめ、警告で返す。"""

    @pytest.mark.parametrize(
        ("field", "text", "denied"),
        [
            ("label", "24KiB (Recommended)", False),
            ("label", "pnpm (Recommended)", False),
            ("label", "v2 (Recommended)", False),
            ("label", "Use defaults (Recommended)", True),
            ("label", "(Recommended) pnpm", True),
            ("question", "pnpm (Recommended)", True),
            ("header", "pnpm (Recommended)", True),
            ("description", "pnpm (Recommended)", True),
            ("plan", "pnpm (Recommended)", True),
        ],
    )
    def test_recommended_suffix_is_excluded_only_from_label(self, field, text, denied):
        """規範の固定推奨印をlabel末尾だけから除き、英語本文の拒否を維持する。"""
        result = _run(_user_facing_payload(field, text))
        assert result.returncode == 0
        assert not result.stderr
        specific = json.loads(result.stdout).get("hookSpecificOutput", {}) if result.stdout else {}
        assert (specific.get("permissionDecision") == "deny") is denied

    def test_recommended_label_keeps_mojibake_warning(self):
        """末尾の推奨印を除いてもlabelの文字化けを通知する。"""
        result = _run(_user_facing_payload("label", "日本語の�本文 (Recommended)"))
        assert result.returncode == 0
        assert "U+FFFD" in _additional_context(result)

    @pytest.mark.parametrize("tool_name", ["AskUserQuestion", "ExitPlanMode"])
    def test_user_facing_tools_are_never_blocked(self, tool_name: str) -> None:
        """ユーザーへ提示する本文を入力とする判定は、そのツールの実行を遮断しない。

        遮断はそのターンの入力と作業を失わせ、同じ確認の再発行を要する。
        ユーザーが本文を読んで誤りを指摘できるため、検出は警告で返す。
        """
        body = "日本語の�本文に가が混入し、atk wi addの契約へ触れる。"
        tool_input = (
            {"plan": body}
            if tool_name == "ExitPlanMode"
            else {
                "questions": [
                    {
                        "question": body,
                        "header": body,
                        "options": [{"label": body, "description": body}],
                    }
                ]
            }
        )

        result = _run({"tool_name": tool_name, "tool_input": tool_input})

        assert result.returncode == 0
        assert "[block]" not in result.stderr

    @pytest.mark.parametrize("field", ["question", "header", "label", "description", "plan"])
    def test_checks_each_user_facing_field(self, field: str) -> None:
        result = _run(_user_facing_payload(field, "日本語の�本文"))

        assert result.returncode == 0
        assert "U+FFFD" in _additional_context(result)

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
