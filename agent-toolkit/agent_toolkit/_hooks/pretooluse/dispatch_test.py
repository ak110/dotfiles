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
from agent_toolkit._atk import help_text as _ATK_HELP_SOURCE
from agent_toolkit._hooks import rules_context
from agent_toolkit._hooks.pretooluse import agent_checks
from agent_toolkit._hooks.pretooluse import content_checks
from agent_toolkit._hooks.pretooluse import dispatch as pretooluse
from agent_toolkit._hooks.pretooluse.test_support_test import *  # noqa: F403
from agent_toolkit._testing import fork_runner as _fork_runner
from agent_toolkit._testing.helpers import SESSION_STATE_FILENAME_TEMPLATE, auto_message_opening_attributes

_HOOKS_JSON_PATH = pathlib.Path(__file__).resolve().parents[3] / "hooks" / "hooks.json"
_HOOKS_CODEX_JSON_PATH = pathlib.Path(__file__).resolve().parents[3] / "hooks" / "hooks.codex.json"


def _matcher_covers(matcher: str, tool_name: str) -> bool:
    """matcherがtool_nameへ一致するかを判定する。

    `re.fullmatch`は`*`だけのパターンへ`re.error: nothing to repeat`を送出するため、
    matcherが`*`である場合は正規表現として評価せず全一致として扱う。
    """
    if matcher == "*":
        return True
    return re.fullmatch(matcher, tool_name) is not None


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


def test_pretooluse_matcher_covers_agents_server_tool_names() -> None:
    """PreToolUse matcherが実装側のagents_serverツール名集合全体を被覆する。

    実装側の`agent_checks.AGENTS_SERVER_HOOK_TOOL_NAMES`を入力として反復し、
    hooks.json（Claude Code、matcherは`*`）とhooks.codex.json（Codex）の双方が
    全要素を被覆することを検査する。実装側の集合へ要素を追加してもmatcherへ
    追加し忘れると、Codex側の当該要素だけが検査から漏れて本検査が失敗する。
    """
    claude_matcher = json.loads(_HOOKS_JSON_PATH.read_text(encoding="utf-8"))["hooks"]["PreToolUse"][0]["matcher"]
    codex_matcher = json.loads(_HOOKS_CODEX_JSON_PATH.read_text(encoding="utf-8"))["hooks"]["PreToolUse"][0]["matcher"]
    assert claude_matcher == "*"
    for tool_name in agent_checks.AGENTS_SERVER_HOOK_TOOL_NAMES:
        assert _matcher_covers(claude_matcher, tool_name)
        if tool_name.startswith("mcp__agents_server__"):
            assert _matcher_covers(codex_matcher, tool_name), tool_name


def test_second_removable_warning_continues_with_count_and_fix(tmp_path: pathlib.Path) -> None:
    """同一原因の2件目も、累積件数と解消手段を添えて通過させる。"""
    payload = {
        "tool_name": "Write",
        "tool_input": {"file_path": str(tmp_path / "uv.lock"), "content": "version = 1\n"},
        "session_id": "dispatch-repeated-removable-warning",
    }
    environment = _plan_file_state_env(tmp_path)

    first = _run(payload, env_overrides=environment)
    second = _run(payload, env_overrides=environment)

    assert first.returncode == 0
    assert second.returncode == 0
    context = _additional_context(second)
    assert "この通知は同一セッションで2件目である" in context
    assert "uv add" in context
    assert second.stderr == ""


def test_irremovable_warning_does_not_advance_removable_repeat_count(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """除去不能警告の後も、除去可能警告の2回目だけに反復注記を付ける。"""
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))

    def emit_controlled_warning(
        _tool_name: str,
        tool_input: dict,
        _cwd: str,
        _emit_json: Callable[[dict], None],
        _flush_warning: Callable[[], None],
    ) -> int:
        notice = pretooluse._llm_notice(  # noqa: SLF001  # pylint: disable=protected-access
            "controlled warning\n対処: retry",
            tag=pretooluse._WARN_TAG,  # noqa: SLF001  # pylint: disable=protected-access
            removable_cause=bool(tool_input["removable"]),
        )
        _emit_json({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": notice}})
        return 0

    monkeypatch.setattr(pretooluse, "_handle_edit_tool", emit_controlled_warning)

    def run(removable: bool) -> int:
        return pretooluse.main(
            json.dumps(
                {
                    "tool_name": "SyntheticEdit",
                    "tool_input": {"removable": removable},
                    "session_id": "dispatch-mixed-removability",
                }
            )
        )

    irremovable = run(False)
    first_removable = run(True)
    second_removable = run(True)

    captured = capsys.readouterr()
    assert irremovable == 0
    assert first_removable == 0
    assert second_removable == 0
    assert "この通知は同一セッションで2件目である" in captured.out
    assert captured.err == ""


class TestMojibakeCheck:
    """文字化け（U+FFFD）検出。

    編集対象もユーザーが直接読む本文も再編集で是正できるため、いずれも警告で返す。
    """

    def test_user_facing_text_with_mojibake_is_warned(self):
        result = _run({"tool_name": "ExitPlanMode", "tool_input": {"plan": "hello " + chr(0xFFFD) + " world"}})
        assert result.returncode == 0
        context = _additional_context(result)
        assert "U+FFFD" in context
        assert "U+FFFDを意図した文字へ置き換えて再実行する" in context

    def test_write_with_mojibake(self):
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "/tmp/a.txt", "content": "hello \ufffd world"}})
        assert result.returncode == 0
        context = _additional_context(result)
        assert "U+FFFD" in context
        # コーディングエージェント宛てメッセージ規約: XMLの開始境界と終了境界が付与されていること。
        assert auto_message_opening_attributes(context) == {"source": "agent-toolkit/pretooluse", "kind": "warn"}
        assert "対処: U+FFFDを意図した文字へ置き換えて再実行する" in context
        assert context.endswith("</agent-toolkit-auto-inserted>")

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
        assert "LFだけの内容" in context
        assert "UTF-8 BOMが失われて日本語が文字化け" in context
        assert "*.ps1 text eol=crlf" in context
        assert "「書込ツールの改行・BOM保全」に従う" in context
        assert "既存ファイルにはEditツールを使い" in context

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
        assert "LFだけの内容" in _additional_context(result)

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
        assert "対処: " in context

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
        assert "対処: " in context

    def test_normal_file_allowed(self):
        """lockfile 名を部分的に含むだけのパスは通過する (例: uv.lock.bak)。"""
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "uv.lock.bak", "content": "x"}})
        assert result.returncode == 0


class TestManifestCheck:
    """manifest 手編集の警告 (warn のみ、exit code は 0)。

    lockfileとの同期が失われるのは依存の節を変える編集に限るため、
    当該節へ触れない編集では通知しない。
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
        # 編集警告はstderrではなくadditionalContextへ集約する。
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
    """ユーザーが直接読む質問・計画本文へ文字化け検査を適用し、警告で返す。"""

    @pytest.mark.parametrize("tool_name", ["AskUserQuestion", "ExitPlanMode"])
    def test_user_facing_tools_are_never_blocked(self, tool_name: str) -> None:
        """ユーザーへ提示する本文を入力とする判定は、当該ツールの実行を遮断しない。

        遮断は当該ターンの入力と作業を失わせ、同じ確認の再発行を要する。
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


class TestAtkContractBeforeQuestion:
    """`atk`サブコマンドを主題とする確認を遮断しない。

    受理形式の事前確認は`agent-toolkit/rules/02-agent-operations.md`「ツール・コマンド運用」が定め、
    hookの遮断では強制しない。
    """

    _SUBCOMMAND = "atk wi process-loop abort"

    def _payload(self, field: str, session_id: str) -> dict:
        payload = _user_facing_payload(field, f"`{self._SUBCOMMAND}`の扱いを選んでください。")
        payload["session_id"] = session_id
        return payload

    @pytest.mark.parametrize("field", ["question", "header", "label", "description"])
    def test_unobserved_subcommand_is_not_blocked(self, tmp_path: pathlib.Path, field: str) -> None:
        """未観測のサブコマンド名を含む確認も遮断しない。"""
        result = _run(self._payload(field, f"contract-{field}"), env_overrides=_plan_file_state_env(tmp_path))

        assert result.returncode == 0
        assert result.stderr == ""

    def test_question_without_subcommand_is_not_blocked(self, tmp_path: pathlib.Path) -> None:
        """サブコマンド名を含まない確認は遮断しない。"""
        payload = _user_facing_payload("question", "常駐処理の扱いを選んでください。")
        payload["session_id"] = "contract-absent"

        result = _run(payload, env_overrides=_plan_file_state_env(tmp_path))

        assert result.returncode == 0
        assert result.stderr == ""

    def test_exit_plan_mode_is_not_blocked(self, tmp_path: pathlib.Path) -> None:
        """計画本文も遮断しない。"""
        payload = {
            "tool_name": "ExitPlanMode",
            "tool_input": {"plan": f"`{self._SUBCOMMAND}`で常駐処理を止める。"},
            "session_id": "contract-plan",
        }

        result = _run(payload, env_overrides=_plan_file_state_env(tmp_path))

        assert result.returncode == 0
        assert result.stderr == ""


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
        assert auto_message_opening_attributes(ctx) == {"source": "agent-toolkit/pretooluse", "kind": "warn"}
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
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(tmp_path / "uv.lock"), "content": "version = 1\n"},
                "transcript_path": str(transcript),
                "session_id": "edit-language-warning-composition",
            },
            env_overrides=_plan_file_state_env(tmp_path),
        )
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert "permissionDecision" not in output["hookSpecificOutput"]
        context = output["hookSpecificOutput"]["additionalContext"]
        language_warning = "英語主体"
        lockfile_warning = "uv add"
        assert language_warning in context
        assert lockfile_warning in context


class TestRemovedChecksAreSilent:
    """規範の想起、CLI形式の事前検出又は文体の検出を目的とする撤去済み検査が、通知も補正も返さないことを検証する。

    撤去した検査の呼び出しが残ると、公開入口の出力へ警告・遮断・`updatedInput`のいずれかが現れる。
    """

    @pytest.mark.parametrize(
        "command",
        [
            "python -c 'x = 1; print(x)'",
            "sh -c 'echo ok'",
            "cat /nonexistent-directory/missing.txt",
            "git commit -m 'message without attribution'",
            "git commit --amend --no-edit",
            "git log -3",
            "git add -A",
            "sleep 500; ls",
            "pytest | tail -5",
            "grep -r keyword .",
            "rg keyword ~/.local",
            "cat .env",
            "echo a(b)",
            "atk wi list --unknown-option",
            "codex exec 'task'",
            "uv run python script.py",
        ],
    )
    def test_bash_input_passes_without_output(self, tmp_path: pathlib.Path, command: str) -> None:
        result = _run(
            {"tool_name": "Bash", "tool_input": {"command": command}, "session_id": "removed-bash", "cwd": str(tmp_path)},
            env_overrides=_plan_file_state_env(tmp_path),
        )

        assert result.returncode == 0
        assert result.stdout == ""
        assert result.stderr == ""

    def test_edit_and_non_edit_tools_pass_without_output(self, tmp_path: pathlib.Path, deny_substring: str) -> None:
        home = tmp_path / "home"
        (home / ".claude" / "plans").mkdir(parents=True)
        payloads = [
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(tmp_path / "doc.md"), "content": f"概要は{deny_substring}該当する。\n"},
            },
            {"tool_name": "Write", "tool_input": {"file_path": str(tmp_path / "doc.py"), "content": f"PATH = '{home}/a'\n"}},
            {"tool_name": "Write", "tool_input": {"file_path": str(tmp_path / "id_rsa"), "content": "key\n"}},
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(home / ".claude" / "plans" / "plan.md"), "content": "# 計画\n"},
            },
            {"tool_name": "Read", "tool_input": {"file_path": str(tmp_path / ".env")}},
            {"tool_name": "WebFetch", "tool_input": {"url": "https://example.com", "prompt": "全文を返す"}},
            {
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "general-purpose", "prompt": "調べる", "description": "調査"},
            },
            {"tool_name": "SendMessage", "tool_input": {"to": "general-purpose", "message": "続ける"}},
            {
                "tool_name": "AskUserQuestion",
                "tool_input": {
                    "questions": [{"question": f"概要は{deny_substring}該当する。", "header": "確認", "options": []}]
                },
            },
        ]
        for index, payload in enumerate(payloads):
            result = _run(
                {**payload, "session_id": f"removed-tool-{index}", "cwd": str(tmp_path)},
                env_overrides=_plan_file_state_env(tmp_path, home_dir=home),
            )

            assert result.returncode == 0, payload
            assert result.stdout == "", payload
            assert result.stderr == "", payload


class TestLanguageReinjection:
    """Claude Codeのメインセッションで、直前の注入から10回目のツール呼び出しへ日本語の応答指示を添える。

    間隔10回は、2026-09-22以降のClaude Codeメイン記録で、セッション開始又は会話圧縮から最初の英語検知通知までの
    ツール呼び出し回数が中央値20回、下位30%が11回だった集計に基づく。
    委譲先とCodexは応答をユーザーが直接読まないため添えない。
    """

    _MAIN_ENV = {"AGENT_TOOLKIT_DELEGATED_SESSION": "0", "AGENT_TOOLKIT_OWNER_SESSION": ""}

    def _contexts(self, tmp_path: pathlib.Path, extra_payload: dict, extra_env: dict[str, str]) -> list[str]:
        env = {**_plan_file_state_env(tmp_path), **self._MAIN_ENV, **extra_env}
        payload = {
            "session_id": "language-reinjection",
            "tool_name": "Read",
            "tool_input": {"file_path": str(tmp_path / "missing.txt")},
            **extra_payload,
        }
        contexts = []
        for _ in range(agent_checks.LANGUAGE_REINJECTION_INTERVAL + 1):
            result = _run(payload, env_overrides=env)
            assert result.returncode == 0, result.stderr
            contexts.append(_additional_context(result))
        return contexts

    def test_main_session_receives_notice_on_interval(self, tmp_path: pathlib.Path) -> None:
        contexts = self._contexts(tmp_path, {}, {})
        notice = rules_context.RESPONSE_LANGUAGE_REINJECTION_NOTICE
        interval = agent_checks.LANGUAGE_REINJECTION_INTERVAL
        assert [notice in context for context in contexts] == [False] * (interval - 1) + [True, False]
        assert all(rules_context.RESPONSE_LANGUAGE_NOTICE not in context for context in contexts)

    def test_repeated_reinjection_has_no_repeat_count(self, tmp_path: pathlib.Path) -> None:
        """定期の再注入は違反の通知ではないため、2周目以降も同じ本文を件数なしで届ける。"""
        interval = agent_checks.LANGUAGE_REINJECTION_INTERVAL
        contexts = self._contexts(tmp_path, {}, {}) + self._contexts(tmp_path, {}, {})
        injected = [context for context in contexts if rules_context.RESPONSE_LANGUAGE_REINJECTION_NOTICE in context]
        assert len(injected) == len(contexts) // interval
        assert len(injected) >= 2
        assert all("件目" not in context for context in injected)

    @pytest.mark.parametrize(
        ("extra_payload", "extra_env"),
        [
            ({}, {"AGENT_TOOLKIT_DELEGATED_SESSION": "1"}),
            ({"agent_id": "subagent-1"}, {}),
            ({"turn_id": "codex-turn"}, {}),
        ],
        ids=["agents-server-delegate", "claude-subagent", "codex"],
    )
    def test_delegates_and_codex_do_not_receive_notice(
        self, tmp_path: pathlib.Path, extra_payload: dict, extra_env: dict[str, str]
    ) -> None:
        contexts = self._contexts(tmp_path, extra_payload, extra_env)
        assert all(rules_context.RESPONSE_LANGUAGE_REINJECTION_NOTICE not in context for context in contexts)
