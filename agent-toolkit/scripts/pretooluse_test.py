"""agent-toolkit/scripts/pretooluse.py のテスト。

subprocessで起動しexit code・stderr・stdoutを検証する。
"""

import ast
import json
import os
import pathlib
import re
import subprocess
import tempfile
import textwrap

import _fork_runner
import claude_hook
import pretooluse
import pytest
from _test_helpers import SESSION_STATE_FILENAME_TEMPLATE
from pyfltr.colloquial import check as _colloquial_check

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "claude_hook.py"
_PLUGIN_MANIFEST = pathlib.Path(__file__).resolve().parents[1] / ".claude-plugin" / "plugin.json"
_MARKETPLACE_MANIFEST = pathlib.Path(__file__).resolve().parents[2] / ".claude-plugin" / "marketplace.json"
_PLAN_MODE_REFERENCES = pathlib.Path(__file__).resolve().parents[1] / "skills" / "plan-mode" / "references"

# `.env`系の遮断メッセージだけへ添える案内の照合断片（`TestSecretsCheck`が使う）。
_SECRETS_COPY_GUIDANCE = "copy the original with `cp` via Bash"
_SECRETS_VALUE_EDIT_GUIDANCE = "append or edit lines via Bash"

# 実装レビューのタスク文書名（`TestExecuteReviewAlternateRouteAllowed`が使う）。
_EXECUTE_REVIEW_TASK_NAMES: tuple[str, ...] = (
    "implementation-plan-review-task.md",
    "implementation-independent-review-task.md",
)


def _run(payload: object, env_overrides: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    return _fork_runner.run_script(_SCRIPT, argv=("pretooluse",), input=text, env=env)


def _additional_context(result: subprocess.CompletedProcess[str]) -> str:
    """stdoutのJSONから`hookSpecificOutput.additionalContext`を取り出す。"""
    stdout = result.stdout.strip()
    if not stdout:
        return ""
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return ""
    context = payload.get("hookSpecificOutput", {}).get("additionalContext")
    return context if isinstance(context, str) else ""


def _agent_messages(result: subprocess.CompletedProcess[str]) -> str:
    """コーディングエージェントへ届く本文を連結して返す。

    Claude Codeの公式仕様では、exit 0で終了したフックのstderrはデバッグログだけに送られ
    コーディングエージェントへ渡らない。そのためstderrはexit 2（ブロック）の場合だけ含める。
    """
    stderr = result.stderr if result.returncode == 2 else ""
    return f"{stderr}\n{_additional_context(result)}"


def _stderr_warn_offenders(source: str) -> list[int]:
    """warn通知をstderrへ出力する箇所の行番号を返す。

    検出するのは`print(..., file=sys.stderr)`の引数へwarn通知が現れる2形とする。
    1つは`tag="warn"`の呼び出しを引数へ直接書く形、
    もう1つはwarn通知を変数へ束縛してから引数へ渡す形である。
    後者には束縛元がwarn通知を返す関数の呼び出しである場合も含め、
    定義順に依存しないよう関数名と変数名の収集を不動点まで繰り返す。
    warn通知を関数の引数として別のヘルパーへ渡し、渡された先のヘルパーがstderrへ出力する
    間接的な受け渡しは検出しない。仮引数へ渡る値の由来を追跡しないためである。
    """
    tree = ast.parse(source)

    def has_warn_tag(node: ast.AST) -> bool:
        return any(
            isinstance(inner, ast.keyword) and inner.arg == "tag" and getattr(inner.value, "value", None) == "warn"
            for inner in ast.walk(node)
        )

    # warn通知を保持する変数名と、warn通知を返す関数名。
    warn_names: set[str] = set()
    warn_functions: set[str] = set()

    def is_warn_value(node: ast.expr) -> bool:
        if has_warn_tag(node):
            return True
        if isinstance(node, ast.Name) and node.id in warn_names:
            return True
        return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in warn_functions

    while True:
        before = (len(warn_names), len(warn_functions))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and is_warn_value(node.value):
                warn_names.update(target.id for target in node.targets if isinstance(target, ast.Name))
            elif (
                isinstance(node, ast.AnnAssign)
                and node.value is not None
                and is_warn_value(node.value)
                and isinstance(node.target, ast.Name)
            ):
                warn_names.add(node.target.id)
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and any(
                isinstance(inner, ast.Return) and inner.value is not None and is_warn_value(inner.value)
                for inner in ast.walk(node)
            ):
                warn_functions.add(node.name)
        if (len(warn_names), len(warn_functions)) == before:
            break

    offenders: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id != "print":
            continue
        if not any(keyword.arg == "file" and ast.unparse(keyword.value) == "sys.stderr" for keyword in node.keywords):
            continue
        if has_warn_tag(node) or any(is_warn_value(arg) for arg in node.args):
            offenders.append(node.lineno)
    return sorted(set(offenders))


@pytest.mark.parametrize("module_name", sorted(claude_hook._SUBCOMMANDS))  # noqa: SLF001  # pylint: disable=protected-access
def test_warn_notices_are_not_written_to_stderr(module_name: str) -> None:
    """exit 0で届かないstderrへwarn通知を出力する実装の再混入を検出する。"""
    source = (pathlib.Path(pretooluse.__file__).parent / f"{module_name}.py").read_text(encoding="utf-8")
    assert _stderr_warn_offenders(source) == [], module_name


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


def _write_session_state(state_dir: pathlib.Path, session_id: str, state: dict) -> None:
    path = state_dir / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=session_id)
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def _read_session_state(state_dir: pathlib.Path, session_id: str) -> dict:
    path = state_dir / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=session_id)
    return json.loads(path.read_text(encoding="utf-8"))


def _stage_model_env(tmp_path: pathlib.Path, value: str) -> dict[str, str]:
    """工程別モデル設定を隔離したXDG設定ディレクトリへ保存する。"""
    config_home = tmp_path / "config"
    config_dir = config_home / "agent-toolkit"
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text(
        json.dumps({"execute_review_model": value}),
        encoding="utf-8",
    )
    return {"XDG_CONFIG_HOME": str(config_home)}


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
        assert "secret" in result.stderr
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

    _HOME = str(pathlib.Path.home())

    def test_home_path_in_content_warns(self):
        content = f"config_path = '{self._HOME}/myproj/config.yaml'\n"
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "src/app.py", "content": content}})
        assert result.returncode == 0
        assert "home directory" in _additional_context(result)

    def test_home_path_in_non_git_temp_document_is_skipped(self, tmp_path: pathlib.Path):
        """Git管理外の一時作業文書では正確なホーム絶対パスを許容する。"""
        target = tmp_path / "draft.md"
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(target), "content": f"対象: {self._HOME}/worktree"},
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
                "tool_input": {"file_path": str(target), "content": f"対象: {self._HOME}/worktree"},
            }
        )
        assert result.returncode == 0
        assert "home directory" in _additional_context(result)

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
                    "tool_input": {"file_path": str(target), "content": f"対象: {self._HOME}/worktree"},
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
                    "tool_input": {"file_path": str(target), "content": f"対象: {self._HOME}/worktree"},
                },
                ensure_ascii=False,
            )
        )

        assert return_code == 0
        assert "home directory" in capsys.readouterr().out

    def test_home_path_in_temp_worktree_git_file_warns(self, tmp_path: pathlib.Path):
        """一時ルート配下のworktree用`.git`ファイルもGit管理候補として警告する。"""
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        (worktree / ".git").write_text("gitdir: /tmp/example.git/worktrees/worktree\n", encoding="utf-8")
        target = worktree / "docs" / "note.md"
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(target), "content": f"対象: {self._HOME}/worktree"},
            }
        )
        assert result.returncode == 0
        assert "home directory" in _additional_context(result)

    def test_home_path_in_temp_prefix_sibling_warns(self):
        """一時ルートと文字列prefixだけが同じ兄弟パスは除外しない。"""
        sibling = pathlib.Path(f"{tempfile.gettempdir()}-sibling") / "draft.md"
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(sibling), "content": f"対象: {self._HOME}/worktree"},
            }
        )
        assert result.returncode == 0
        assert "home directory" in _additional_context(result)

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

    def test_home_path_in_plan_file_skipped(self, tmp_path: pathlib.Path):
        """Git管理外の計画ファイルでは正確なホーム絶対パスを許容する。"""
        home = tmp_path / "home"
        plan = _make_plan_file(home, "home-path.md")
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


@pytest.fixture(name="deny_substring")
def _deny_substring_fixture() -> str:
    """辞書ファイルから口語表現の検出サンプルを生成する。

    テスト本体へ口語表現を直接書かないため、allowlistの最初のオーバーラップサンプルから
    denylist部分文字列を抽出する。本番ロジック`_colloquial_check.load_patterns`と同じ解釈で
    タブ区切りの置換候補列を除外する。
    """
    deny_patterns = [pattern for pattern, _ in _colloquial_check.load_patterns(_colloquial_check.DENY_PATH)]
    for raw in _colloquial_check.ALLOW_PATH.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        sample = re.sub(r"\[([^\]]+)\]", lambda m: m.group(1)[0], stripped)
        for pattern in deny_patterns:
            match = pattern.search(sample)
            if match:
                return match.group(0)
    pytest.skip("no overlap between denylist and allowlist; cannot generate test sample")
    return ""  # unreachable


class TestColloquialCheck:
    """口語的な日本語表現の混入警告（warn のみ、exit code は 0）。"""

    def test_warns_on_deny(self, deny_substring: str):
        content = f"概要は{deny_substring}該当する。\n"
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "src/note.md", "content": content}})
        assert result.returncode == 0
        assert "colloquial" in _additional_context(result)
        assert "Matches: 1 (line 1, column 4)." in _additional_context(result)
        assert "Rewrite the whole sentence containing the detected expression" in _additional_context(result)
        assert "[auto-generated: agent-toolkit/pretooluse][warn]" in _additional_context(result)
        # 検出語そのものは出力に含めない（コンテキスト汚染防止）
        assert deny_substring not in _agent_messages(result)

    def test_lists_every_match_position_within_limit(self, deny_substring: str):
        content = f"概要は{deny_substring}該当する。\n" * 5
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "src/note.md", "content": content}})
        assert result.returncode == 0
        assert (
            "Matches: 5 (line 1, column 4; line 2, column 4; line 3, column 4; line 4, column 4; line 5, column 4)."
            in _additional_context(result)
        )

    def test_omits_match_positions_beyond_limit(self, deny_substring: str):
        content = f"概要は{deny_substring}該当する。\n" * 6
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "src/note.md", "content": content}})
        assert result.returncode == 0
        assert (
            "Matches: 6 (line 1, column 4; line 2, column 4; line 3, column 4; line 4, column 4; line 5, column 4)."
            in _additional_context(result)
        )
        assert "line 6" not in _additional_context(result)

    def test_does_not_block(self, deny_substring: str):
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "x.md", "content": deny_substring}})
        assert result.returncode == 0  # warnのみ

    def test_clean_text_no_warn(self):
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "src/app.py", "content": "x = 1\n"}})
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


def _plan_file_state_env(
    tmp_path: pathlib.Path,
    home_dir: pathlib.Path | None = None,
) -> dict[str, str]:
    env = {"TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path)}
    if home_dir is not None:
        env["HOME"] = str(home_dir)
        env["USERPROFILE"] = str(home_dir)
    return env


def _delegation_state_env(tmp_path: pathlib.Path, session_id: str) -> dict[str, str]:
    """delegation起動済みの後段ゲート検証用に状態ファイルを準備する。"""
    _write_session_state(tmp_path, session_id, {"delegation_skill_invoked": True})
    return _plan_file_state_env(tmp_path)


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


# 現行の機械検査を通過する最小限の正規計画ファイル内容。
_VALID_H2_PLAN_CONTENT = (
    "## 概要\n\nx\n\n"
    "### 計画メタ情報\n\n"
    f"- ベースコミット: `{'a' * 40}`\n\n"
    "## 実装資料\n\n### 変更説明\n\nREADMEを更新する。\n\n"
    "## 完了条件\n\nx\n\n"
    "## 進捗ログ\n\nx\n"
)


class TestPlanModeSkillFirstCheck:
    """plan fileの起草編集でplan-modeスキル未起動を警告する検査（block降格済み）。

    plan-modeスキル未起動でもplan file以外の操作（Read・Bash・他Skill・通常ファイル編集等）は
    一切ブロックも警告もしない。`~/.claude/plans/`直下の`*.md`に対する
    Writeと進捗ログ節外を変更するEdit/MultiEditが警告対象となる。`permission_mode`の値には依存しない。
    既存計画の一意かつ最後の`## 進捗ログ`節だけを変更するEdit/MultiEditは警告しない。
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
        messages = _agent_messages(result)
        assert "plan-mode" in messages
        assert "Phase 1" in messages
        assert "reviewing a delegated plan" in messages
        assert "only correcting values uniquely determined by the artifact and evidence" in messages
        assert "continue without restarting plan-mode" in messages
        assert "recording each correction and its evidence in `## 変更履歴`" in messages
        assert "[auto-generated: agent-toolkit/pretooluse][warn]" in messages
        assert "editing a plan file without invoking" in _additional_context(result)
        assert "editing a plan file without invoking" not in result.stderr

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
        assert "editing a plan file without invoking" in _agent_messages(result)

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
                # 委譲を伴う工程を定めないスキルを選び、delegation未起動の事前案内と混在させない。
                {"skill": "agent-toolkit:coding-standards"},
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

    plan-modeは委譲を伴う工程を定めるため、delegation未起動では事前案内（warn）が載る。
    遮断はせず`returncode`は0を保つ。
    """

    _state_env = staticmethod(_plan_file_state_env)

    @pytest.mark.parametrize("skill_name", ["agent-toolkit:plan-mode", "plan-mode"])
    def test_allowed_outside_plan_mode(self, tmp_path: pathlib.Path, skill_name: str):
        env = self._state_env(tmp_path)
        _write_session_state(tmp_path, "outside-plan", {"delegation_skill_invoked": True})
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
        _write_session_state(tmp_path, "inside-plan", {"delegation_skill_invoked": True})
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


class TestPlanFileDoesNotRequireTextlintRead:
    """計画編集前の文章lint資料読了条件が撤去済みであることを検証する。

    `permission_mode`の値に依らず、`~/.claude/plans/`直下の`*.md`に対する
    Write/Edit/MultiEditのみが警告対象となる。plan file以外の操作は
    一切ブロック・警告しない。完成条件を満たさない状態での次工程移行の抑止は
    `ExitPlanMode`・`plan-impl-executor`起動時のブロックへ集約する。
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

    def test_no_warn_without_transcript_path(self):
        """transcript_path未指定なら検査スキップ。"""
        result = _run({"tool_name": "Bash", "tool_input": {"command": "ls"}})
        assert result.returncode == 0
        assert result.stdout == ""


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
        assert "permissionDecision" not in output["hookSpecificOutput"]
        context = output["hookSpecificOutput"]["additionalContext"]
        commit_warning = "committing without running tests"
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

    def test_first_english_warns(self, tmp_path: pathlib.Path):
        """1回目の英語検出はexit 0 + additionalContextで警告する。"""
        env = self._state_env(tmp_path)
        result = self._invoke(tmp_path, env, "esc-first", "A" * 100, msg_id="m1")
        assert result.returncode == 0
        ctx = _additional_context(result)
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
        ctx = _additional_context(r3)
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
        ctx = _additional_context(result)
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
    """固定sleep後に処理が続く前景待機を初回warn・再検出blockで扱う。"""

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
            # 閾値以上の固定待機は、後続コマンドが状態確認コマンド一覧に無くても検出する。
            ("sleep 570; atk watch --worktree /tmp/lane-example/wt", "sleep-poll-first-17"),
            ("sleep 420; cd /tmp/lane-example/wt && ./scripts/check_state.sh", "sleep-poll-first-18"),
            ("sleep 30; echo done", "sleep-poll-first-19"),
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
        assert "may cause repeated polling" in _additional_context(result)

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
            # 閾値未満の待機は、後続が状態確認コマンドでない限り通過させる。
            ("sleep 5; echo done", "sleep-poll-allow-25"),
            ("sleep 29; echo done", "sleep-poll-allow-26"),
            # 条件成立で抜けるループ内の長い待機は、ループ予約語の位置を問わず通過させる。
            ("until test -f /tmp/marker; do echo waiting; sleep 60; done", "sleep-poll-allow-27"),
            ("while true; do echo waiting; sleep 60; done", "sleep-poll-allow-28"),
            ("for i in 1 2 3; do echo $i; sleep 60; done", "sleep-poll-allow-29"),
            ("attempt=0; until test -f /tmp/marker; do echo waiting; sleep 60; done", "sleep-poll-allow-30"),
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
        assert "may cause repeated polling" not in _additional_context(result)
        follow_up = _run(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "sleep 1; git status --short"},
                "session_id": session_id,
            },
            env,
        )
        assert follow_up.returncode == 0
        assert "may cause repeated polling" in _additional_context(follow_up)

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
            output = json.loads(result.stdout)
            assert "permissionDecision" not in output["hookSpecificOutput"]
            assert self._has_additional_context(result, "[auto-generated: agent-toolkit/pretooluse][warn]")
            assert self._has_additional_context(result, "committing without running tests")
            assert self._has_additional_context(result, "Auto-generated hook notice")
        else:
            assert result.stdout == ""

    def test_commit_warning_uses_effective_cd_cwd(self, state_dir: dict[str, str], tmp_path: pathlib.Path) -> None:
        """`cd`後のdocs-only commitは移動先のステージ内容で判定する。"""
        target_base = tmp_path / "target"
        target_base.mkdir()
        payload_base = tmp_path / "payload"
        payload_base.mkdir()
        target = self._make_repo_with_staged(target_base, {"docs/a.md": "# docs\n"})
        payload = self._make_repo_with_staged(payload_base, {})
        result = self._invoke(
            f"cd {target} && git commit -m 'docs'",
            "effective-commit-cwd",
            state_dir,
            cwd=str(payload),
        )
        assert result.returncode == 0
        assert result.stdout == ""

    def test_unresolved_commit_warns_without_payload_fallback(self, state_dir: dict[str, str], tmp_path: pathlib.Path) -> None:
        """解決不能なcommitはpayload cwdのdocs-only状態へフォールバックせず警告する。"""
        payload = self._make_repo_with_staged(tmp_path, {"docs/a.md": "# docs\n"})
        result = self._invoke(
            'cd "$TARGET" && git commit -m "docs"',
            "unresolved-commit-cwd",
            state_dir,
            cwd=str(payload),
        )
        assert result.returncode == 0
        assert self._has_additional_context(result, "committing without running tests")


class TestBashGitLogDecorate:
    """git log --decorate自動付与。"""

    def test_adds_decorate(self):
        result = _run({"tool_name": "Bash", "tool_input": {"command": "git log --oneline -5"}})
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["hookSpecificOutput"]["permissionDecision"] == "allow"
        updated = data["hookSpecificOutput"]["updatedInput"]["command"]
        assert "--decorate" in updated
        assert "systemMessage" not in data

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
        assert "permissionDecision" not in data["hookSpecificOutput"]

    def test_codex_exec_nudge_uses_conditional_plan_review_wording(self) -> None:
        """用途を断定せず、計画レビューの場合だけ点検を促す。"""
        result = _run({"tool_name": "Bash", "tool_input": {"command": "codex exec --help"}})
        additional_context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        assert "running codex exec." in additional_context
        assert "If this run submits a plan file for review" in additional_context
        assert "submitting plan file to codex review." not in additional_context

    def test_no_nudge_on_resume(self):
        result = _run({"tool_name": "Bash", "tool_input": {"command": "codex exec resume --dangerously-bypass abc prompt"}})
        assert result.returncode == 0
        assert result.stdout == ""

    def test_no_nudge_on_unrelated_command(self):
        result = _run({"tool_name": "Bash", "tool_input": {"command": "echo codex"}})
        assert result.returncode == 0
        assert result.stdout == ""

    def test_no_nudge_when_name_is_in_argument_position(self):
        """`codex exec`を引数として含むだけの読み取り操作は警告しない。"""
        result = _run({"tool_name": "Bash", "tool_input": {"command": "echo 'codex exec is documented here'"}})
        assert result.returncode == 0
        assert "running codex exec" not in _agent_messages(result)

    def test_nudge_follows_execution_position(self):
        """実行位置の`codex exec`は警告し、同じ位置の`codex exec resume`は警告しない。"""
        executed = _run({"tool_name": "Bash", "tool_input": {"command": 'codex exec "review the plan"'}})
        resumed = _run({"tool_name": "Bash", "tool_input": {"command": "codex exec resume 01ABCDEF"}})
        assert "running codex exec" in _agent_messages(executed)
        assert "running codex exec" not in _agent_messages(resumed)


class TestBashAmendRebaseBlock:
    """git amend / rebaseのlog未確認ブロック。

    `git_log_checked`はcwd別辞書`{cwd: True}`で管理する。
    既存状態に残る旧形式の単一bool値も後方互換として受け入れる。
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
        """既存状態の旧形式bool値`True`は解決済みcwdで後方互換として受け入れる。"""
        self._write_state(tmp_path, "with-log", {"git_log_checked": True})
        cmd = "git commit " + "--amend --no-edit"
        result = self._invoke(cmd, "with-log", state_dir, cwd="/repo/a")
        assert result.returncode == 0

    def test_rebase_allowed_with_legacy_bool_flag(self, state_dir: dict[str, str], tmp_path: pathlib.Path):
        self._write_state(tmp_path, "with-log-rb", {"git_log_checked": True})
        result = self._invoke("GIT_SEQUENCE_EDITOR=: git rebase -i HEAD~2", "with-log-rb", state_dir, cwd="/repo/a")
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

    def test_unresolved_cwd_is_blocked_without_payload_fallback(self, state_dir: dict[str, str], tmp_path: pathlib.Path):
        """shell展開を含むcwdは、payloadのcwdに記録された確認結果へフォールバックしない。"""
        sid = "unresolved-amend-cwd"
        self._write_state(tmp_path, sid, {"git_log_checked": {"/repo/a": True}})
        result = self._invoke('cd "$TARGET" && git commit --amend --no-edit', sid, state_dir, cwd="/repo/a")
        assert result.returncode == 2
        assert "unresolved" in result.stderr


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
    - 解決不能なcwdではpayloadのcwdへフォールバックしない
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
        assert "permissionDecision" not in data["hookSpecificOutput"]
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

    def test_unresolved_cwd_skips_without_payload_fallback(
        self,
        state_dir: dict[str, str],
        tmp_path: pathlib.Path,
    ) -> None:
        """shell展開を含むcwdでは、payload cwd側の未編集ファイルを誤って警告しない。"""
        repo = tmp_path / "repo-unresolved"
        repo.mkdir()
        _init_git_repo(repo)
        (repo / "unedited.txt").write_text("x", encoding="utf-8")
        self._write_state(tmp_path, "add-unresolved", {"session_edited_files": []})
        result = self._invoke('cd "$TARGET" && git add -A', "add-unresolved", state_dir, cwd=str(repo))
        self._assert_no_warn(result)


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
        path = state_dir_path / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=session_id)
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

    def test_unresolved_push_blocks_when_any_worktree_is_pending(
        self, state_dir: dict[str, str], tmp_path: pathlib.Path
    ) -> None:
        """解決不能なpushは、いずれかのworktreeにamend後確認待ちがあれば遮断する。"""
        sid = "push-unresolved"
        self._write_state(tmp_path, sid, {"amend_pending_status_check": {"/repo/a": True}})
        result = self._invoke('cd "$TARGET" && git push origin master', sid, state_dir, cwd=str(tmp_path))
        assert result.returncode == 2
        assert "amend" in result.stderr

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
    def _make_child_directory(tmp_path: pathlib.Path) -> pathlib.Path:
        """子ディレクトリを作成して返す。"""
        child = tmp_path / "child"
        child.mkdir()
        return child

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

    def test_child_of_python_project_allowed(self, tmp_path: pathlib.Path) -> None:
        """祖先の`[project]`を解決する子ディレクトリでは正規実行を許容する。"""
        self._make_python_project(tmp_path)
        cwd = self._make_child_directory(tmp_path)
        result = self._invoke("uv run python -c 'print(1)'", str(cwd))
        assert result.returncode == 0

    def test_nearest_non_python_project_blocks_ancestor_project(self, tmp_path: pathlib.Path) -> None:
        """直近が`[tool.uv]`のみなら祖先に`[project]`があっても遮断する。"""
        self._make_python_project(tmp_path)
        cwd = self._make_child_directory(tmp_path)
        self._make_non_python_project(cwd)
        result = self._invoke("uv run python -c 'print(1)'", str(cwd))
        assert result.returncode == 2

    @pytest.mark.parametrize(
        "command",
        ["uv run python /tmp/foo.py", "uv run python -c 'print(1)'"],
        ids=["path", "inline_code"],
    )
    def test_non_python_project_forms_are_blocked(self, tmp_path: pathlib.Path, command: str) -> None:
        """パス引数形とインラインコード形の双方を公開インターフェースでブロックする。"""
        cwd = self._make_non_python_project(tmp_path)
        result = self._invoke(command, cwd)
        assert result.returncode == 2
        assert "[auto-generated: agent-toolkit/pretooluse]" in result.stderr
        assert "uv run python" in result.stderr

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

    def test_cd_to_python_project_allowed(self, tmp_path: pathlib.Path) -> None:
        """静的に解決できる`cd`先がPythonプロジェクトなら許容する。"""
        payload_cwd = tmp_path / "payload"
        payload_cwd.mkdir()
        target = tmp_path / "python-target"
        target.mkdir()
        self._make_python_project(target)
        result = self._invoke(f"cd {target} && uv run python /tmp/foo.py", str(payload_cwd))
        assert result.returncode == 0

    def test_quoted_cd_to_python_project_blocks_for_safety(self, tmp_path: pathlib.Path) -> None:
        """引用符で保護した`cd`先でもglob文字を含むcwdは安全側で遮断する。"""
        payload_cwd = tmp_path / "payload"
        payload_cwd.mkdir()
        target = tmp_path / "python-target[1]"
        target.mkdir()
        self._make_python_project(target)
        result = self._invoke(f"cd '{target}' && uv run python /tmp/foo.py", str(payload_cwd))
        assert result.returncode == 2

    def test_cd_to_non_python_project_blocks(self, tmp_path: pathlib.Path) -> None:
        """静的に解決できる`cd`先がPythonプロジェクトでなければ遮断する。"""
        payload_cwd = tmp_path / "payload"
        payload_cwd.mkdir()
        target = tmp_path / "non-python-target"
        target.mkdir()
        self._make_non_python_project(target)
        result = self._invoke(f"cd {target} && uv run python /tmp/foo.py", str(payload_cwd))
        assert result.returncode == 2

    def test_unresolved_cd_blocks(self, tmp_path: pathlib.Path) -> None:
        """shell展開を含む`cd`は、payload cwdがPythonプロジェクトでも遮断する。"""
        payload_cwd = self._make_python_project(tmp_path)
        result = self._invoke('cd "$TARGET" && uv run python /tmp/foo.py', payload_cwd)
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


class TestDelegationRouteGate:
    """メインセッションのdelegation起動ゲート。"""

    @pytest.fixture(name="state_dir")
    def _state_dir(self, tmp_path: pathlib.Path) -> dict[str, str]:
        return _plan_file_state_env(tmp_path)

    _write_state = staticmethod(_write_session_state)

    def test_blocked_when_skill_not_invoked(self, state_dir: dict[str, str]) -> None:
        """メインセッションでdelegation未起動の初回呼び出しをブロックする。"""
        result = _run(
            {"tool_name": "mcp__codex__codex", "tool_input": {"prompt": "hello"}, "session_id": "no-review"},
            env_overrides=state_dir,
        )
        assert result.returncode == 2
        assert "agent-toolkit:delegation" in result.stderr

    def test_allowed_when_skill_invoked(self, state_dir: dict[str, str], tmp_path: pathlib.Path) -> None:
        """delegation起動後の初回呼び出しを許可する。"""
        self._write_state(tmp_path, "with-review", {"delegation_skill_invoked": True})
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
        assert "agent-toolkit:delegation" in result.stderr


class TestCodexMcpExecution:
    """codex MCP sandbox明示指定の強制・approval-policy自動修正（CLI統合テスト）。"""

    @pytest.fixture(name="state_dir")
    def _state_dir(self, tmp_path: pathlib.Path) -> dict[str, str]:
        return _plan_file_state_env(tmp_path)

    _write_state = staticmethod(_write_session_state)

    def test_sandbox_unspecified_blocked(self, state_dir: dict[str, str], tmp_path: pathlib.Path):
        """sandbox未指定の場合は`danger-full-access`へ自動補正する。"""
        self._write_state(tmp_path, "fix1", {"delegation_skill_invoked": True})
        result = _run(
            {
                "tool_name": "mcp__codex__codex",
                "tool_input": {"prompt": "hello", "cwd": "/tmp/workdir"},
                "session_id": "fix1",
            },
            env_overrides=state_dir,
        )
        assert result.returncode == 0
        out = json.loads(result.stdout)
        assert out["hookSpecificOutput"]["updatedInput"]["sandbox"] == "danger-full-access"
        assert "systemMessage" not in out

    @pytest.mark.parametrize("sandbox", ["network-only", "read-only", "workspace-write"])
    def test_sandbox_other_values_blocked(self, sandbox: str, state_dir: dict[str, str], tmp_path: pathlib.Path):
        """`danger-full-access`以外のsandbox指定は自動補正する。"""
        self._write_state(tmp_path, "fix2", {"delegation_skill_invoked": True})
        result = _run(
            {
                "tool_name": "mcp__codex__codex",
                "tool_input": {"prompt": "hello", "sandbox": sandbox, "cwd": "/tmp/workdir"},
                "session_id": "fix2",
            },
            env_overrides=state_dir,
        )
        assert result.returncode == 0
        out = json.loads(result.stdout)
        assert out["hookSpecificOutput"]["updatedInput"]["sandbox"] == "danger-full-access"
        assert "systemMessage" not in out

    def test_sandbox_blocked_in_sidechain(self, state_dir: dict[str, str], tmp_path: pathlib.Path):
        """サブエージェント内部からの呼び出しでもsandboxを自動補正する。"""
        self._write_state(tmp_path, "fix_side", {"delegation_skill_invoked": True})
        result = _run(
            {
                "tool_name": "mcp__codex__codex",
                "tool_input": {"prompt": "hello", "sandbox": "read-only", "cwd": "/tmp/workdir"},
                "session_id": "fix_side",
                "isSidechain": True,
            },
            env_overrides=state_dir,
        )
        assert result.returncode == 0
        out = json.loads(result.stdout)
        assert out["hookSpecificOutput"]["updatedInput"]["sandbox"] == "danger-full-access"
        assert "systemMessage" not in out

    def test_sandbox_correct_no_message(self, state_dir: dict[str, str], tmp_path: pathlib.Path):
        """sandbox・approval-policyが共に既定値の場合、updatedInputは返すがsystemMessageを含めない。"""
        self._write_state(tmp_path, "fix3", {"delegation_skill_invoked": True})
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
        self._write_state(tmp_path, "fix_ap", {"delegation_skill_invoked": True})
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
        assert "systemMessage" not in out


class TestCheckCodexMcpSandbox:
    """`_check_codex_mcp_sandbox`単体テスト（`danger-full-access`への自動補正）。"""

    @pytest.mark.parametrize("sandbox", ["read-only", "workspace-write"])
    def test_corrects_other_sandbox_modes(self, sandbox: str, capsys: pytest.CaptureFixture[str]) -> None:
        updated = pretooluse._check_codex_mcp_sandbox({"prompt": "test", "sandbox": sandbox})  # noqa: SLF001  # pylint: disable=protected-access
        assert updated["sandbox"] == "danger-full-access"
        assert capsys.readouterr().err == ""

    def test_corrects_unspecified_sandbox(self, capsys: pytest.CaptureFixture[str]) -> None:
        updated = pretooluse._check_codex_mcp_sandbox({"prompt": "test"})  # noqa: SLF001  # pylint: disable=protected-access
        assert updated["sandbox"] == "danger-full-access"
        assert capsys.readouterr().err == ""

    def test_preserves_danger_full_access(self, capsys: pytest.CaptureFixture[str]) -> None:
        updated = pretooluse._check_codex_mcp_sandbox({"prompt": "test", "sandbox": "danger-full-access"})  # noqa: SLF001  # pylint: disable=protected-access
        assert updated["sandbox"] == "danger-full-access"
        assert capsys.readouterr().err == ""


class TestCheckCodexMcpCwd:
    """`mcp__codex__codex`呼び出しの`cwd`絶対パス強制（CLI統合テスト、公開インターフェース経由）。"""

    @pytest.fixture(name="state_dir")
    def _state_dir(self, tmp_path: pathlib.Path) -> dict[str, str]:
        return _plan_file_state_env(tmp_path)

    _write_state = staticmethod(_write_session_state)

    def test_blocks_missing_cwd(self, state_dir: dict[str, str], tmp_path: pathlib.Path) -> None:
        """`cwd`未指定の場合はブロックする。"""
        self._write_state(tmp_path, "cwd-missing", {"delegation_skill_invoked": True})
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
        self._write_state(tmp_path, "cwd-empty", {"delegation_skill_invoked": True})
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
        self._write_state(tmp_path, "cwd-whitespace", {"delegation_skill_invoked": True})
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
        self._write_state(tmp_path, "cwd-relative", {"delegation_skill_invoked": True})
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
        self._write_state(tmp_path, "cwd-absolute", {"delegation_skill_invoked": True})
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
        tool_input = {"prompt": "test"}
        result = pretooluse._check_codex_mcp_execution(tool_input)  # noqa: SLF001  # pylint: disable=protected-access
        updated = result["hookSpecificOutput"]["updatedInput"]
        assert updated["sandbox"] == "danger-full-access"
        assert updated["approval-policy"] == "never"

    def test_overrides_user_specified_value(self) -> None:
        tool_input = {"prompt": "test", "sandbox": "danger-full-access", "approval-policy": "on-request"}
        result = pretooluse._check_codex_mcp_execution(tool_input)  # noqa: SLF001  # pylint: disable=protected-access
        updated = result["hookSpecificOutput"]["updatedInput"]
        assert updated["approval-policy"] == "never"
        assert updated["sandbox"] == "danger-full-access"
        assert "systemMessage" not in result

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
        """delegation起動後は`mcp__codex__codex-reply`が強制承認される。"""
        self._write_state(tmp_path, "reply1", {"delegation_skill_invoked": True})
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

    `flush_pending_notices()`を廃止し`emit_json()`単独で承認とadditionalContextを
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
        self._write_state(tmp_path, "codex-lang", {"delegation_skill_invoked": True})
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
        self._write_state(tmp_path, "reply-lang", {"delegation_skill_invoked": True})
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
        self._write_state(tmp_path, "probe1", {"delegation_skill_invoked": True})
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
                "delegation_skill_invoked": True,
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
        self._write_state(tmp_path, "probe3", {"delegation_skill_invoked": True})
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
        self._write_state(tmp_path, "probe4", {"delegation_skill_invoked": True})
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
        self._write_state(tmp_path, "probe-rotate", {"delegation_skill_invoked": True})
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
        self._write_state(tmp_path, "probe-reply", {"delegation_skill_invoked": True})
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
        output = json.loads(result.stdout)
        assert "permissionDecision" not in output["hookSpecificOutput"]
        assert self._has_version_bump_warning(result)

    def test_commit_uses_effective_cd_cwd(self, tmp_path: pathlib.Path):
        """`cd`後のcommitはpayload cwdではなく移動先の差分でversion警告を判定する。"""
        target_base = tmp_path / "target"
        target_base.mkdir()
        payload_base = tmp_path / "payload"
        payload_base.mkdir()
        target = self._make_repo(target_base, {"agent-toolkit/skills/x/SKILL.md": "# x\n"})
        payload_repo = self._make_repo(payload_base)
        result = self._invoke(f"cd {target} && git commit -m 'skill'", str(payload_repo))
        assert result.returncode == 0
        assert self._has_version_bump_warning(result)

    def test_commit_with_unresolved_cwd_suppresses_version_warning(self, tmp_path: pathlib.Path):
        """shell展開を含むcommitではpayload cwdへフォールバックしてversion警告を抑止する。"""
        payload_repo = self._make_repo(tmp_path, {"agent-toolkit/skills/x/SKILL.md": "# x\n"})
        result = self._invoke('cd "$TARGET" && git commit -m "skill"', str(payload_repo))
        assert result.returncode == 0
        assert not self._has_version_bump_warning(result)

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

    def test_commit_string_in_argument_position_no_warn(self, tmp_path: pathlib.Path):
        """`git commit`を検索語として含むだけの読み取り操作は警告しない。"""
        repo = self._make_repo(tmp_path, {"agent-toolkit/skills/x/SKILL.md": "# x\n"})
        result = self._invoke("grep -rn 'git commit' docs", str(repo))
        assert result.returncode == 0
        assert not self._has_version_bump_warning(result)


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


class TestBashBlockBeforeAccumulatedWarnings:
    """Bashハンドラーは警告条件との同居時も遮断検査を優先する。"""

    @pytest.mark.parametrize(
        ("blocking_command", "expected_message"),
        [
            ('pkill -f "worker"', "pattern-based process termination"),
            ("uv run python script.py", "`uv run python` invocation"),
        ],
        ids=["process-kill", "uv-run-python"],
    )
    def test_bulk_stage_warning_cannot_bypass_block(
        self,
        blocking_command: str,
        expected_message: str,
        tmp_path: pathlib.Path,
    ) -> None:
        """一括stage警告より後続の遮断条件を常に優先する。"""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        _git_commit_initial(repo, {"tracked.txt": "初期値\n"})
        (repo / "untracked.txt").write_text("未追跡\n", encoding="utf-8")
        session_id = f"block-before-warning-{blocking_command.split()[0]}"
        _write_session_state(tmp_path, session_id, {"session_edited_files": []})

        result = _run(
            {
                "tool_name": "Bash",
                "tool_input": {"command": f"git add -A; {blocking_command}"},
                "session_id": session_id,
                "cwd": str(repo),
            },
            env_overrides=_plan_file_state_env(tmp_path),
        )

        assert result.returncode == 2
        assert "blocked" in result.stderr
        assert expected_message in result.stderr

    def test_multiple_warnings_are_accumulated_in_one_output(self, tmp_path: pathlib.Path) -> None:
        """複数の警告条件を単一PreToolUse応答へ欠落なく蓄積する。"""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        _git_commit_initial(repo, {"tracked.txt": "初期値\n"})
        (repo / "untracked.txt").write_text("未追跡\n", encoding="utf-8")
        session_id = "accumulate-warnings"
        _write_session_state(tmp_path, session_id, {"session_edited_files": []})

        result = _run(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "git add -A; codex exec --help"},
                "session_id": session_id,
                "cwd": str(repo),
            },
            env_overrides=_plan_file_state_env(tmp_path),
        )

        assert result.returncode == 0
        output = json.loads(result.stdout)
        context = output["hookSpecificOutput"]["additionalContext"]
        assert "bulk staging" in context
        assert "running codex exec" in context


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
        output = json.loads(result.stdout)
        assert "warn" in output["hookSpecificOutput"]["additionalContext"]

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

    @pytest.mark.parametrize(
        "command",
        [
            "grep -n 'pyfltr' pyproject.toml | head -40",
            "rg pytest docs | head -20",
            "uv run --with pytest ruff check | head -5",
            "uv run -w pytest ruff check | head -5",
            "uv run -qw pytest ruff check | head -5",
            "uv run --help pytest | head -5",
        ],
        ids=["grep", "rg", "with-long", "with-short", "combined-short", "terminal-option"],
    )
    def test_verification_name_outside_execution_position_silent(self, command: str) -> None:
        """検証ツール名を検索語・オプションの値として含むだけのコマンドは警告しない。"""
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        assert result.returncode == 0
        assert "truncating it" not in _agent_messages(result)

    @pytest.mark.parametrize(
        "command",
        [
            "uv run --no-project pytest -q | tail -5",
            "uv --directory /tmp run pytest -q | tail -5",
            "uv run pytest -q | tail -5",
            "python -m pytest | head -5",
            "timeout 600 uvx pyfltr run | tail -20",
        ],
        ids=["run-option", "global-option-with-value", "no-option", "python-module", "timeout-uvx"],
    )
    def test_verification_in_execution_position_warns(self, command: str) -> None:
        """前置語とオプションを介して実行位置へ現れる検証コマンドは警告を維持する。"""
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        assert result.returncode == 0
        assert "truncating it" in _agent_messages(result)

    @pytest.mark.parametrize(
        "command",
        [
            "pytest -q && head -5 report.txt",
            "pytest -q; head -5 report.txt",
            "pytest -q || head -5 report.txt",
            "pytest -q & head -5 report.txt",
        ],
        ids=["and", "semicolon", "or", "background"],
    )
    def test_truncation_outside_verification_pipeline_silent(self, command: str) -> None:
        """検証コマンドの出力を受け取らない後続コマンドの`head`・`tail`は警告しない。"""
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        assert result.returncode == 0
        assert "truncating it" not in _agent_messages(result)

    @pytest.mark.parametrize(
        "command",
        [
            "sudo sh -c 'pytest -q | head -5'",
            "pytest -q 2>&1 | head -5",
            "pytest -q; pytest -q | head -5",
            "sh -c 'pytest -q' | head -5",
            "sh -c 'ls; pytest -q | head -5'",
            "sh -c 'ls; pytest -q' | head -5",
            'sh -c "pytest -q | head -5; echo done" | tee /tmp/full.log',
            "pytest -q | head -5 | tee /tmp/test.log",
        ],
        ids=[
            "prefixed-shell-c",
            "stderr-redirect",
            "second-of-multiple",
            "shell-c-into-outer-pipe",
            "shell-c-inner-pipeline",
            "multi-statement-shell-into-outer-pipe",
            "tee-after-inner-truncation",
            "tee-after-truncation",
        ],
    )
    def test_truncation_inside_verification_pipeline_warns(self, command: str) -> None:
        """`sh -c`展開・標準エラー統合・2件目の検証コマンドを含む形も同一パイプラインとして警告する。"""
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        assert result.returncode == 0
        assert "truncating it" in _agent_messages(result)

    @pytest.mark.parametrize(
        "command",
        [
            'pytest -q | sh -c "tee /tmp/x1.log; head -5"',
            'pytest -q | sh -c "head -5; tee /tmp/x2.log"',
            "sh -c 'ls; pytest -q' | tee /tmp/full.log | head -5",
        ],
        ids=["tee-then-head", "head-then-tee", "downstream-tee"],
    )
    def test_multi_statement_shell_upstream_not_connected_silent(self, command: str) -> None:
        """内側が複数の文へ分かれる`sh -c`は、渡した標準入力を消費する文を確定できないため警告しない。

        下流は各文へ連結するため、下流に`tee`がある場合も全量保存として扱い警告しない。
        """
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        assert result.returncode == 0
        assert "truncating it" not in _agent_messages(result)


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
            env_overrides=_delegation_state_env(tmp_path, sid),
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
            env_overrides=_delegation_state_env(tmp_path, sid),
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
            env_overrides=_delegation_state_env(tmp_path, "agent-name-block-route"),
        )
        assert result.returncode == 2
        assert "execution result" in result.stderr
        assert "launch in the foreground" not in result.stderr
        assert "tool return value" not in result.stderr

    def test_name_block_precedes_subagent_type_flag_record(self, tmp_path: pathlib.Path) -> None:
        """ブロックされた起動では`subagent_type`別フラグを記録しない（起動しない呼び出しの副作用を残さない）。"""
        sid = "agent-name-block-no-flag"
        plan = _make_plan_file(tmp_path / "home", "name-block.md")
        _write_session_state(tmp_path, sid, {"delegation_skill_invoked": True})
        env = {**_plan_file_state_env(tmp_path), **_process_loop_log_env(tmp_path)}
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {
                    "subagent_type": "agent-toolkit:plan-impl-executor",
                    "name": "codex-1",
                    "prompt": f"計画ファイル `{plan}` を実装する。",
                },
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 2
        log_path = tmp_path / "state" / "agent-toolkit" / "process-feedbacks.log"
        assert not log_path.exists() or "subagent_start" not in log_path.read_text(encoding="utf-8")


class TestSubagentModelOverrideGate:
    """定義済みモデルを使う委譲調整役への`model`引数指定の一律ブロック。"""

    def test_plan_impl_executor_with_model_blocked_short_form(self, tmp_path: pathlib.Path):
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "plan-impl-executor", "model": "haiku", "prompt": "x"},
                "session_id": "model-override-plan-impl-executor",
                "permission_mode": "default",
            },
            env_overrides=_delegation_state_env(tmp_path, "model-override-plan-impl-executor"),
        )
        assert result.returncode == 2

    def test_no_model_argument_passes(self, tmp_path: pathlib.Path):
        """`plan-impl-executor`でモデル指定を省略した起動は通過する。"""
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "agent-toolkit:plan-impl-executor", "prompt": "x"},
                "session_id": "model-override-none",
                "permission_mode": "default",
            },
            env_overrides=_delegation_state_env(tmp_path, "model-override-none"),
        )
        assert result.returncode == 0

    @pytest.mark.parametrize("subagent_type", ["feedbacks-planner", "agent-toolkit:feedbacks-planner"])
    def test_feedbacks_planner_with_model_is_blocked(self, tmp_path: pathlib.Path, subagent_type: str) -> None:
        """`feedbacks-planner`の固定モデルを呼び出し側から変更できない。"""
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {"subagent_type": subagent_type, "model": "haiku", "prompt": "x"},
                "session_id": "model-override-feedbacks-planner",
                "permission_mode": "default",
            },
            env_overrides=_delegation_state_env(tmp_path, "model-override-feedbacks-planner"),
        )
        assert result.returncode == 2


class TestExecuteReviewAlternateRouteAllowed:
    """`execute_review_model`が指すengineによらず実装レビューのAgent起動が通過する。"""

    @pytest.mark.parametrize("task_name", _EXECUTE_REVIEW_TASK_NAMES)
    def test_codex_setting_allows_sidechain_agent(self, tmp_path: pathlib.Path, task_name: str) -> None:
        """可用性起因の代替としてClaude経路へ切り替えた実装レビュー起動を遮断しない。"""
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "general-purpose", "prompt": f"{task_name}を読んでレビューする。"},
                "session_id": "execute-review-codex",
                "isSidechain": True,
            },
            env_overrides=_stage_model_env(tmp_path, "codex:gpt-5.6-sol/high"),
        )
        assert result.returncode == 0
        assert "blocked:" not in result.stderr
        assert "execute_review_model" not in result.stderr

    def test_claude_setting_allows_sidechain_agent(self, tmp_path: pathlib.Path) -> None:
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {
                    "subagent_type": "general-purpose",
                    "prompt": "implementation-plan-review-task.mdを読んでレビューする。",
                },
                "session_id": "execute-review-claude",
                "isSidechain": True,
            },
            env_overrides=_stage_model_env(tmp_path, "claude:sonnet/medium"),
        )
        assert result.returncode == 0

    def test_codex_setting_allows_main_session_agent(self, tmp_path: pathlib.Path) -> None:
        session_id = "execute-review-main"
        env = _stage_model_env(tmp_path, "codex:gpt-5.6-sol/medium")
        env.update(_delegation_state_env(tmp_path, session_id))
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {
                    "subagent_type": "general-purpose",
                    "prompt": "implementation-independent-review-task.mdを読んでレビューする。",
                },
                "session_id": session_id,
                "isSidechain": False,
            },
            env_overrides=env,
        )
        assert result.returncode == 0

    def test_guarded_task_references_exist(self) -> None:
        """回帰検査が与えるタスク文書名の実在を確認し、改名による空振りを検出する。"""
        for task_name in _EXECUTE_REVIEW_TASK_NAMES:
            assert (_PLAN_MODE_REFERENCES / task_name).is_file()


def _process_loop_log_env(tmp_path: pathlib.Path) -> dict[str, str]:
    return {
        "AGENT_TOOLKIT_PROCESS_LOOP_SESSION": "1",
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "LOCALAPPDATA": str(tmp_path / "state"),
    }


class TestSubagentStartLogOrdering:
    """`subagent_start`記録は全ブロック検査を通過した場合のみ行われる。

    ブロック時に記録が残ると、対応する`subagent_end`が生成されず
    process-loopの所要時間分析の対応関係が崩れるため、ブロック経路ごとに未記録を確認する。
    """

    def _log_path(self, tmp_path: pathlib.Path) -> pathlib.Path:
        return tmp_path / "state" / "agent-toolkit" / "process-feedbacks.log"

    def test_model_override_block_does_not_log_start(self, tmp_path: pathlib.Path):
        log_path = self._log_path(tmp_path)
        _write_session_state(tmp_path, "log-order-model-override", {"delegation_skill_invoked": True})
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {
                    "subagent_type": "agent-toolkit:plan-impl-executor",
                    "model": "opus",
                    "prompt": "計画を実装する。",
                },
                "session_id": "log-order-model-override",
                "permission_mode": "default",
            },
            env_overrides={**_plan_file_state_env(tmp_path), **_process_loop_log_env(tmp_path)},
        )
        assert result.returncode == 2
        assert not log_path.exists() or "subagent_start" not in log_path.read_text(encoding="utf-8")

    def test_all_checks_pass_logs_start(self, tmp_path: pathlib.Path):
        """モデル指定なし・見出し検査対象外・`process7`未起動時の`plan-impl-executor`は通過し記録される。"""
        log_path = self._log_path(tmp_path)
        _write_session_state(tmp_path, "log-order-pass", {"delegation_skill_invoked": True})
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "agent-toolkit:plan-impl-executor", "prompt": "計画を実装して。"},
                "session_id": "log-order-pass",
                "permission_mode": "default",
            },
            env_overrides={**_plan_file_state_env(tmp_path), **_process_loop_log_env(tmp_path)},
        )
        assert result.returncode == 0
        assert log_path.exists()
        assert "subagent_start" in log_path.read_text(encoding="utf-8")


def _path_section_build_content(recorded_path: str) -> str:
    """撤去済みの末尾パス節検査へ与える計画本文を組み立てる。"""
    return (
        "## 概要\n\nx\n\n"
        "## 実装資料\n\n### 変更説明\n\nREADMEを更新する。\n\n"
        "## 完了条件\n\nx\n\n"
        "## 進捗ログ\n\nx\n\n"
        "## 計画ファイル（本ファイル）のパス\n\n"
        f"`{recorded_path}`\n"
    )


class TestPlanFileDoesNotRequireSelfPath:
    """計画自身のパス照合が撤去済みであることを検証する。"""

    _state_env = staticmethod(_plan_file_state_env)
    _make_plan = staticmethod(_make_plan_file)

    @staticmethod
    def _prior_flags(tmp_path: pathlib.Path, session_id: str, _content: str) -> None:
        _write_session_state(
            tmp_path,
            session_id,
            {
                "plan_mode_skill_invoked": True,
            },
        )

    def test_recorded_path_difference_does_not_warn(self, tmp_path: pathlib.Path):
        """記録パス値とWrite先が異なっても警告しない。"""
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
        assert "trailing path section" not in result.stderr

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
            "## 概要\n\nx\n\n"
            "## 実装資料\n\n### 変更説明\n\nREADMEを更新する。\n\n"
            "## 完了条件\n\nx\n\n"
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
        assert "根拠に" in _additional_context(result)

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
        assert "理由に" in _additional_context(result)

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
        assert "根拠に" not in _agent_messages(result)

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
        assert "根拠に" not in _agent_messages(result)


class TestDirectAgentToolkitEditsAfterPlanMode:
    """plan-modeスキル起動後、計画ファイル未作成のままagent-toolkit配下の直接編集連続を検知。

    2件目でwarn（`additionalContext`出力＋通過）、3件目でblock。
    直前と同一パスの繰り返しはincrementしない。
    対象外パスへの編集はカウンタをリセットし通過する。
    """

    _state_env = staticmethod(_plan_file_state_env)

    def _write_flag_state(self, tmp_path: pathlib.Path, sid: str, extra: dict | None = None) -> None:
        state: dict = {
            "plan_mode_skill_invoked": True,
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
        assert "without first creating a plan file" not in _agent_messages(result)

    @pytest.mark.parametrize(
        ("file_path", "expected_count"),
        [
            (".claude/rules/foo.md", 0),
            (".claude/skills/x/SKILL.md", 0),
            (".claude/skills/x/references/y.md", 0),
            (".claude/rules/agent-toolkit/01-agent.md", 1),
            ("agent-toolkit/rules/01-agent.md", 1),
        ],
    )
    def test_direct_agent_toolkit_edit_hook_excludes_project_local_docs(
        self,
        tmp_path: pathlib.Path,
        file_path: str,
        expected_count: int,
    ) -> None:
        """公開フック経路でプロジェクト直下の規範文書を抑止対象から外す。"""
        sid = "direct-edit-project-local"
        self._write_flag_state(tmp_path, sid)
        target = tmp_path / file_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("stub\n", encoding="utf-8")
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {"file_path": str(target), "old_string": "stub", "new_string": "stub2"},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=self._state_env(tmp_path),
        )
        assert result.returncode == 0
        state = _read_session_state(tmp_path, sid)
        assert state.get("direct_agent_toolkit_edit_count", 0) == expected_count

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
                assert "[warn]" not in _agent_messages(result)
            else:
                # 2件目はwarnして通過する（returncode 0）。
                assert result.returncode == 0
                assert "[warn]" in _agent_messages(result)
                assert "without first creating a plan file" in _agent_messages(result)
                assert "without first creating a plan file" not in result.stderr

    def test_second_target_edit_warn_survives_block(self, tmp_path: pathlib.Path):
        """2件目の警告と同じ呼び出しで遮断が成立しても、警告をコーディングエージェントへ届ける。

        遮断時点でカウンタと直前パスは更新済みのため、同一パスを安全に再試行しても
        警告は再生成されない。遮断で終える直前に出力しなければ警告が失われる。
        """
        sid = "direct-edit-warn-with-block"
        self._write_flag_state(tmp_path, sid)
        env = self._state_env(tmp_path)
        first = self._target(tmp_path, "foo/SKILL.md")
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {"file_path": str(first), "old_string": "stub", "new_string": "stub2"},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        # 2件目は警告対象であり、同じ入力が文字化け検査で遮断される。
        second = self._target(tmp_path, "bar/SKILL.md")
        blocked = _run(
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": str(second),
                    "old_string": "stub",
                    "new_string": "stub2" + chr(0xFFFD),
                },
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert blocked.returncode == 2
        assert "U+FFFD" in blocked.stderr
        assert "The next such edit will be blocked." in _agent_messages(blocked)
        # 同一パスの安全な再試行では警告が再生成されない。
        retried = _run(
            {
                "tool_name": "Edit",
                "tool_input": {"file_path": str(second), "old_string": "stub", "new_string": "stub2"},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert retried.returncode == 0
        assert "The next such edit will be blocked." not in _agent_messages(retried)

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
        state_path = tmp_path / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=sid)
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
            assert "[warn]" not in _agent_messages(result)
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
        state_path = tmp_path / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=sid)
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


class TestFrontmatterSyncNoteBodyExists:
    """frontmatter同期注記の本体該当語句の実在検証（フィードバック2、`warn`）。"""

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
                "# 01-agent\n\n## 判断指針\n\n本文。\n",
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
                "---\nname: test-agent\n# 01-agent.mdの「判断指針」節と意図的に重複させている\n---\n\n# test-agent\n",
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
        assert "frontmatter sync note" not in _agent_messages(result)

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
        assert expected_message in _additional_context(result)
        assert expected_identifier in _additional_context(result)


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
        target_file = tmp_path / "agent-toolkit" / "rules" / "test.md"
        target_file.parent.mkdir(parents=True)
        ref_file = target_file.parent / "referenced.md"
        ref_file.write_text("# 存在する節\n\n本文です。", encoding="utf-8")
        content = "本文\n\n`referenced.md`「存在する節」節を参照。"

        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(target_file), "content": content},
            }
        )
        assert result.returncode == 0
        assert result.stderr == ""

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
        assert "section name does not exist" in _additional_context(result)


# --- codex sandbox指定（danger-full-access）を含む行の削除・変更の遮断 (block) ---

# 保護対象パスの相対表記と、sandbox指定記述を持つ検体本文。Claude入力とCodex `apply_patch`の双方で使う。
_PROTECTED_RELATIVE_PATH = "agent-toolkit/scripts/pretooluse.py"
_PROTECTED_BODY = "説明文\n`sandbox: danger-full-access`を指定する\n末尾\n"


class TestDangerFullAccessPreserved:
    """codex sandbox指定を含む行の削除・変更の遮断。"""

    def test_blocks_removal_of_sandbox_assignment(self):
        """sandbox指定記述を削除する編集を遮断する。"""
        file_path = pathlib.Path(pretooluse.__file__).resolve()
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": str(file_path),
                    "content": "sandbox指定記述を含まない本文",
                },
            }
        )
        assert result.returncode == 2
        assert "blocked" in result.stderr
        assert "codex sandbox assignment" in result.stderr

    def test_blocks_edit_removing_sandbox_assignment(self):
        """Edit経路でsandbox指定記述を削除する操作を遮断する。"""
        file_path = pathlib.Path(__file__).resolve().parents[1] / "skills/delegation/references/runtime-routing.md"
        old_string = "作業ディレクトリの絶対パスと`sandbox: danger-full-access`を例外なく渡す"
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": str(file_path),
                    "old_string": old_string,
                    "new_string": "作業ディレクトリの絶対パスを例外なく渡す",
                },
            }
        )
        assert result.returncode == 2
        assert "blocked" in result.stderr
        assert "codex sandbox assignment" in result.stderr

    def test_blocks_weakening_of_sandbox_value(self):
        """sandbox値を弱める編集を遮断する。"""
        file_path = pathlib.Path(__file__).resolve().parents[1] / "skills/delegation/references/runtime-routing.md"
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": str(file_path),
                    "content": "`sandbox: workspace-write`を指定する",
                },
            }
        )
        assert result.returncode == 2
        assert "blocked" in result.stderr
        assert "codex sandbox assignment" in result.stderr

    def test_passes_non_sandbox_change(self):
        """sandbox指定記述を保ったまま説明文を変える編集は通過する。"""
        file_path = pathlib.Path(pretooluse.__file__).resolve()
        content = file_path.read_text(encoding="utf-8").replace("統合フック", "統合済みフック", 1)
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": str(file_path),
                    "content": content,
                },
            }
        )
        assert result.returncode == 0

    def test_protected_files_contain_only_required_sandbox_assignments(self):
        """保護対象の各実体から1件以上の固定sandbox値を抽出する。"""
        repository_root = pathlib.Path(__file__).resolve().parents[2]
        for relative_path in pretooluse._DANGER_FULL_ACCESS_PROTECTED_PATHS:  # pylint: disable=protected-access
            content = (repository_root / relative_path).read_text(encoding="utf-8")
            values = pretooluse._extract_sandbox_assignments(content)  # pylint: disable=protected-access
            assert values, relative_path
            assert set(values) == {"danger-full-access"}, relative_path

    _WRITE_CONTENT = "`sandbox: danger-full-access`を指定する\n"

    def test_write_over_undecodable_protected_file_blocks(self, tmp_path: pathlib.Path) -> None:
        """既存の保護対象を復号できないWriteは判定不能として遮断する。"""
        target = tmp_path / _PROTECTED_RELATIVE_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"\xff\xfe`sandbox: danger-full-access`\n")
        result = _run({"tool_name": "Write", "tool_input": {"file_path": str(target), "content": self._WRITE_CONTENT}})
        assert result.returncode == 2
        assert "could not be reconstructed" in result.stderr

    def test_write_over_unreadable_protected_path_blocks(self, tmp_path: pathlib.Path) -> None:
        """対象不在を確定できない読み取り失敗（対象がディレクトリ）でも遮断する。"""
        target = tmp_path / _PROTECTED_RELATIVE_PATH
        target.mkdir(parents=True, exist_ok=True)
        result = _run({"tool_name": "Write", "tool_input": {"file_path": str(target), "content": self._WRITE_CONTENT}})
        assert result.returncode == 2
        assert "could not be reconstructed" in result.stderr

    def test_write_creating_new_protected_file_passes(self, tmp_path: pathlib.Path) -> None:
        """未作成の保護対象パスへの新規Writeは従来どおり通過する。"""
        target = tmp_path / _PROTECTED_RELATIVE_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        result = _run({"tool_name": "Write", "tool_input": {"file_path": str(target), "content": self._WRITE_CONTENT}})
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
        self._write_state(tmp_path, "snap-agent", {"delegation_skill_invoked": True})
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
        self._write_state(tmp_path, "snap-session", {"delegation_skill_invoked": True})
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
        self._write_state(tmp_path, "snap-cwd-src", {"delegation_skill_invoked": True})
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
        self._write_state(tmp_path, "snap-reply-nocwd", {"delegation_skill_invoked": True})
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
        self._write_state(tmp_path, "snap-reply", {"delegation_skill_invoked": True})
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
            self._read_state(tmp_path, "snap-reply") | {"delegation_skill_invoked": True},
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


class TestDelegationGateForAgentTask:
    """新規Agent／Task委譲はdelegation起動後だけ通す。"""

    @pytest.mark.parametrize("tool_name", ["Agent", "Task"])
    def test_main_launch_without_delegation_is_blocked(self, tmp_path: pathlib.Path, tool_name: str) -> None:
        sid = f"missing-{tool_name.lower()}"
        plan = _make_plan_file(tmp_path / "home", f"{tool_name.lower()}-missing.md")
        env = {**_plan_file_state_env(tmp_path), **_process_loop_log_env(tmp_path)}
        result = _run(
            {
                "session_id": sid,
                "tool_name": tool_name,
                "tool_input": {
                    "subagent_type": "agent-toolkit:plan-impl-executor",
                    "prompt": f"計画ファイル `{plan}` を実装する。",
                },
            },
            env_overrides=env,
        )
        assert result.returncode == 2
        assert "agent-toolkit:delegation" in result.stderr
        state_path = tmp_path / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=sid)
        assert not state_path.exists()
        log_path = tmp_path / "state" / "agent-toolkit" / "process-feedbacks.log"
        assert not log_path.exists() or "subagent_start" not in log_path.read_text(encoding="utf-8")

    @pytest.mark.parametrize("tool_name", ["Agent", "Task"])
    def test_main_launch_with_delegation_or_sidechain_passes(self, tmp_path: pathlib.Path, tool_name: str) -> None:
        env = {"TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path)}
        _write_session_state(tmp_path, "ready", {"delegation_skill_invoked": True})
        allowed = _run({"session_id": "ready", "tool_name": tool_name, "tool_input": {}}, env_overrides=env)
        sidechain = _run(
            {"session_id": "sidechain", "tool_name": tool_name, "tool_input": {}, "isSidechain": True},
            env_overrides=env,
        )
        assert allowed.returncode == 0
        assert sidechain.returncode == 0

    def test_claude_code_guide_without_delegation_passes(self, tmp_path: pathlib.Path) -> None:
        """公式資料照会専用エージェントは委譲スキル未起動でも許容する。"""
        result = _run(
            {
                "session_id": "guide-without-delegation",
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "claude-code-guide", "prompt": "公式資料を確認する。"},
            },
            env_overrides=_plan_file_state_env(tmp_path),
        )
        assert result.returncode == 0


class TestDelegationSkillReminderOnSkillInvocation:
    """委譲を伴う工程のスキル起動時にdelegation未起動を事前案内する。

    事前案内はAgent・Task起動プロンプトを構成する前の最後の観測点で1回返し、
    既存のAgent・Task遮断判定とセッション状態は変えない。
    """

    @pytest.mark.parametrize(
        "skill_name",
        [
            "agent-toolkit:plan-mode",
            "plan-mode",
            "agent-toolkit:process-feedbacks",
            "process-feedbacks",
            "agent-toolkit:session-review",
            "session-review",
            "agent-toolkit:bugfix",
            "bugfix",
        ],
    )
    def test_notice_for_delegation_workflow_skill(self, tmp_path: pathlib.Path, skill_name: str) -> None:
        """対象スキルの起動かつdelegation未起動では案内が1回返る。"""
        result = _run(
            {
                "tool_name": "Skill",
                "tool_input": {"skill": skill_name},
                "session_id": "reminder-target",
            },
            env_overrides=_plan_file_state_env(tmp_path),
        )
        context = _additional_context(result)
        assert result.returncode == 0
        assert context.count("agent-toolkit:delegation") == 1
        assert skill_name in context

    def test_no_notice_when_delegation_already_invoked(self, tmp_path: pathlib.Path) -> None:
        """delegation起動済みでは案内を返さない。"""
        _write_session_state(tmp_path, "reminder-done", {"delegation_skill_invoked": True})
        result = _run(
            {
                "tool_name": "Skill",
                "tool_input": {"skill": "agent-toolkit:process-feedbacks"},
                "session_id": "reminder-done",
            },
            env_overrides=_plan_file_state_env(tmp_path),
        )
        assert result.returncode == 0
        assert result.stdout == ""

    def test_no_notice_for_other_skill(self, tmp_path: pathlib.Path) -> None:
        """委譲を伴う工程を定めないスキルでは案内を返さない。"""
        result = _run(
            {
                "tool_name": "Skill",
                "tool_input": {"skill": "agent-toolkit:coding-standards"},
                "session_id": "reminder-other",
            },
            env_overrides=_plan_file_state_env(tmp_path),
        )
        assert result.returncode == 0
        assert result.stdout == ""

    def test_no_notice_in_sidechain(self, tmp_path: pathlib.Path) -> None:
        """サイドチェーンでは案内を返さない。"""
        result = _run(
            {
                "tool_name": "Skill",
                "tool_input": {"skill": "agent-toolkit:plan-mode"},
                "session_id": "reminder-sidechain",
                "isSidechain": True,
            },
            env_overrides=_plan_file_state_env(tmp_path),
        )
        assert result.returncode == 0
        assert result.stdout == ""

    def test_notice_does_not_unblock_agent_launch(self, tmp_path: pathlib.Path) -> None:
        """案内を返した後もAgent起動の遮断判定は変わらない。"""
        env = _plan_file_state_env(tmp_path)
        skill_result = _run(
            {
                "tool_name": "Skill",
                "tool_input": {"skill": "agent-toolkit:plan-mode"},
                "session_id": "reminder-then-agent",
            },
            env_overrides=env,
        )
        agent_result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "general-purpose", "prompt": "調査する。"},
                "session_id": "reminder-then-agent",
            },
            env_overrides=env,
        )
        assert "agent-toolkit:delegation" in _additional_context(skill_result)
        assert agent_result.returncode == 2
        assert "agent-toolkit:delegation" in agent_result.stderr


# --- Codex `apply_patch` / Bash の共通検査 ---


def _patch(*sections: str) -> str:
    """Codexの`apply_patch`入力本文を組み立てる。"""
    body = "".join(sections)
    return f"*** Begin Patch\n{body}*** End Patch\n"


def _codex_payload(patch_text: str, cwd: pathlib.Path, session_id: str = "codex-edit") -> dict:
    """Codexの編集payloadを組み立てる。"""
    return {
        "tool_name": "apply_patch",
        "tool_input": {"command": patch_text},
        "cwd": str(cwd),
        "session_id": session_id,
        "turn_id": "turn-1",
    }


def _write_protected_file(repo: pathlib.Path) -> pathlib.Path:
    """sandbox指定記述を持つ保護対象ファイルを作業ツリーへ用意する。"""
    target = repo / _PROTECTED_RELATIVE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_PROTECTED_BODY, encoding="utf-8")
    return target


class TestCodexApplyPatchEditChecks:
    """Codexの`apply_patch`入力に対する共通編集検査。"""

    def test_add_file_warns_colloquial_without_revealing_word(self, tmp_path: pathlib.Path, deny_substring: str) -> None:
        """追加全文の口語表現を警告し、検出語そのものは出力しない。"""
        patch_text = _patch(f"*** Add File: docs/note.md\n+概要は{deny_substring}該当する。\n")
        result = _run(_codex_payload(patch_text, tmp_path))

        assert result.returncode == 0
        assert "colloquial" in _additional_context(result)
        assert deny_substring not in _agent_messages(result)

    def test_removed_lines_only_do_not_warn(self, tmp_path: pathlib.Path, deny_substring: str) -> None:
        """削除行だけに該当表現があるpatchは警告しない。"""
        target = tmp_path / "docs" / "note.md"
        target.parent.mkdir(parents=True)
        target.write_text(f"前文\n概要は{deny_substring}該当する。\n後文\n", encoding="utf-8")
        patch_text = _patch(
            f"*** Update File: docs/note.md\n@@\n 前文\n-概要は{deny_substring}該当する。\n+概要は条件に該当する。\n 後文\n"
        )
        result = _run(_codex_payload(patch_text, tmp_path))

        assert result.returncode == 0
        assert "colloquial" not in _agent_messages(result)

    def test_multiple_warnings_are_merged_into_single_json(self, tmp_path: pathlib.Path) -> None:
        """複数対象の警告を1つのadditionalContextへ結合する。"""
        home = str(pathlib.Path.home())
        patch_text = _patch(
            f"*** Add File: src/one.py\n+first = '{home}/a'\n",
            f"*** Add File: src/two.py\n+second = '{home}/b'\n",
        )
        result = _run(_codex_payload(patch_text, tmp_path))

        assert result.returncode == 0
        assert len(result.stdout.strip().splitlines()) == 1
        assert _additional_context(result).count("home directory absolute path") == 2

    def test_mojibake_in_patch_blocks(self, tmp_path: pathlib.Path) -> None:
        """patch本文の文字化けを遮断する。"""
        patch_text = _patch("*** Add File: docs/a.md\n+hello � world\n")
        result = _run(_codex_payload(patch_text, tmp_path))

        assert result.returncode == 2
        assert "U+FFFD" in result.stderr

    def test_unparsable_patch_passes_through(self, tmp_path: pathlib.Path) -> None:
        """patch構造を認識できない入力は遮断も警告もせず通過させる。"""
        result = _run(_codex_payload("not a patch at all\n", tmp_path))

        assert result.returncode == 0
        assert result.stdout == ""
        assert result.stderr == ""

    def test_lockfile_path_in_patch_blocks(self, tmp_path: pathlib.Path) -> None:
        """patchの対象パス判定は既存のパターン検査を共有する。"""
        patch_text = _patch("*** Update File: uv.lock\n@@\n-old\n+new\n")
        result = _run(_codex_payload(patch_text, tmp_path))

        assert result.returncode == 2
        assert "uv.lock" in result.stderr

    def test_delete_of_protected_file_blocks(self, tmp_path: pathlib.Path) -> None:
        """保護対象ファイル全体の削除を遮断する（相対パスをcwd起点で解決する）。"""
        _write_protected_file(tmp_path)
        patch_text = _patch(f"*** Delete File: {_PROTECTED_RELATIVE_PATH}\n")
        result = _run(_codex_payload(patch_text, tmp_path))

        assert result.returncode == 2
        assert "codex sandbox assignment" in result.stderr

    def test_delete_of_unprotected_file_passes(self, tmp_path: pathlib.Path) -> None:
        """非保護対象の削除はこの検査で誤遮断しない。"""
        target = tmp_path / "docs" / "old.md"
        target.parent.mkdir(parents=True)
        target.write_text("本文\n", encoding="utf-8")
        patch_text = _patch("*** Delete File: docs/old.md\n")
        result = _run(_codex_payload(patch_text, tmp_path))

        assert result.returncode == 0

    def test_unmaterializable_protected_update_blocks(self, tmp_path: pathlib.Path) -> None:
        """保護対象の変更前後像を具体化できない場合も遮断する。"""
        _write_protected_file(tmp_path)
        patch_text = _patch(f"*** Update File: {_PROTECTED_RELATIVE_PATH}\n@@\n 実在しない文脈行\n-古い行\n+新しい行\n")
        result = _run(_codex_payload(patch_text, tmp_path))

        assert result.returncode == 2
        assert "could not be reconstructed" in result.stderr

    def test_protected_update_preserving_assignment_passes(self, tmp_path: pathlib.Path) -> None:
        """sandbox指定記述を保つ更新は通過する。"""
        _write_protected_file(tmp_path)
        patch_text = _patch(f"*** Update File: {_PROTECTED_RELATIVE_PATH}\n@@\n-説明文\n+説明文を更新する\n")
        result = _run(_codex_payload(patch_text, tmp_path))

        assert result.returncode == 0

    def test_move_of_protected_file_blocks(self, tmp_path: pathlib.Path) -> None:
        """保護対象の移動は移動元の消滅として遮断する。"""
        _write_protected_file(tmp_path)
        patch_text = _patch(
            f"*** Update File: {_PROTECTED_RELATIVE_PATH}\n"
            "*** Move to: agent-toolkit/scripts/moved.py\n@@\n-末尾\n+末尾を更新\n"
        )
        result = _run(_codex_payload(patch_text, tmp_path))

        assert result.returncode == 2
        assert "codex sandbox assignment" in result.stderr

    def test_frontmatter_and_body_reference_checks_are_claude_only(self, tmp_path: pathlib.Path) -> None:
        """外部ファイル解決を伴う2検査はCodex入力で起動しない。"""
        content = "---\nname: test-agent\n# nonexistent-agent.mdの「何か」節と意図的に重複させている\n---\n\n# test-agent\n"
        relative = "agent-toolkit/agents/test-agent.md"
        patch_text = _patch(f"*** Add File: {relative}\n" + "".join(f"+{line}\n" for line in content.splitlines()))
        codex = _run(_codex_payload(patch_text, tmp_path))
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        claude = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(target), "content": content},
                "session_id": "fm-sync-claude",
            }
        )

        assert codex.returncode == 0
        assert "frontmatter sync note" not in _agent_messages(codex)
        assert claude.returncode == 0
        assert "referenced file path does not exist" in _additional_context(claude)


class TestStyleNegationAcrossHosts:
    """否定規定表現の判定単位がホストごとの契約どおりであること。"""

    @staticmethod
    def _rule_path(tmp_path: pathlib.Path) -> pathlib.Path:
        target = tmp_path / "agent-toolkit" / "rules" / "test-rule.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def test_multiedit_warns_even_when_file_total_is_unchanged(self, tmp_path: pathlib.Path) -> None:
        """追加と削除がファイル全体で相殺するMultiEditでも追加した編集単位を警告する。"""
        target = self._rule_path(tmp_path)
        target.write_text("# rule\n\n作業量を根拠に延期しない\n\n別の記述\n", encoding="utf-8")
        result = _run(
            {
                "tool_name": "MultiEdit",
                "tool_input": {
                    "file_path": str(target),
                    "edits": [
                        {"old_string": "作業量を根拠に延期しない", "new_string": "作業量に応じて計画を見直す"},
                        {"old_string": "別の記述", "new_string": "工数を理由に対応しない"},
                    ],
                },
                "session_id": "styleneg-multiedit",
            },
        )

        assert result.returncode == 0
        assert "理由に" in _additional_context(result)

    def test_codex_add_file_uses_whole_text(self, tmp_path: pathlib.Path) -> None:
        """Codexの追加は追加全文の件数で判定する。"""
        patch_text = _patch("*** Add File: agent-toolkit/rules/test-rule.md\n+# rule\n+\n+作業量を根拠に延期しない\n")
        result = _run(_codex_payload(patch_text, tmp_path))

        assert result.returncode == 0
        assert "根拠に" in _additional_context(result)

    def test_codex_update_preserving_existing_phrase_does_not_warn(self, tmp_path: pathlib.Path) -> None:
        """Codexの更新は断片ごとの増加で判定し、既存表現の保持では警告しない。"""
        target = self._rule_path(tmp_path)
        target.write_text("# rule\n\n作業量を根拠に延期しない\n", encoding="utf-8")
        patch_text = _patch(
            "*** Update File: agent-toolkit/rules/test-rule.md\n@@\n"
            "-作業量を根拠に延期しない\n"
            "+作業量を根拠に延期しない。追記のみ\n"
        )
        result = _run(_codex_payload(patch_text, tmp_path))

        assert result.returncode == 0
        assert "根拠に" not in _agent_messages(result)


class TestCodexBashCheckSelection:
    """同一のBash入力に対するホスト別の検査集合。"""

    @staticmethod
    def _payload(command: str, cwd: pathlib.Path, session_id: str, *, codex: bool) -> dict:
        payload: dict = {
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "session_id": session_id,
            "cwd": str(cwd),
        }
        if codex:
            payload["turn_id"] = "turn-1"
        return payload

    def test_amend_without_git_log_is_claude_only(self, tmp_path: pathlib.Path) -> None:
        """`git log`成功状態に依存するamend検査はCodexで起動しない。"""
        env = _plan_file_state_env(tmp_path)
        _write_session_state(tmp_path, "amend-host", {})
        claude = _run(self._payload("git commit --amend", tmp_path, "amend-host", codex=False), env_overrides=env)
        codex = _run(self._payload("git commit --amend", tmp_path, "amend-host", codex=True), env_overrides=env)

        assert claude.returncode == 2
        assert codex.returncode == 0

    def test_commit_verification_warning_is_claude_only(self, tmp_path: pathlib.Path) -> None:
        """検証実行状態に依存するcommit警告はCodexで起動しない。"""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        _git_commit_initial(repo, {"app.py": "x = 1\n"})
        (repo / "app.py").write_text("x = 2\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
        env = _plan_file_state_env(tmp_path)
        _write_session_state(tmp_path, "commit-host", {"git_log_checked": {str(repo): True}})
        claude = _run(self._payload("git commit -m x", repo, "commit-host", codex=False), env_overrides=env)
        codex = _run(self._payload("git commit -m x", repo, "commit-host", codex=True), env_overrides=env)

        assert "committing without running tests" in _additional_context(claude)
        assert "committing without running tests" not in _agent_messages(codex)

    def test_bulk_stage_warning_is_shared(self, tmp_path: pathlib.Path) -> None:
        """成功した編集が記録する状態による一括stage警告は両ホストで動作する。"""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        _git_commit_initial(repo, {"tracked.txt": "初期値\n"})
        (repo / "tracked.txt").write_text("更新\n", encoding="utf-8")
        _write_session_state(tmp_path, "bulk-codex", {"session_edited_files": []})
        result = _run(
            self._payload("git add -A", repo, "bulk-codex", codex=True),
            env_overrides=_plan_file_state_env(tmp_path),
        )

        assert result.returncode == 0
        assert "bulk staging includes files" in _additional_context(result)

    def test_input_only_checks_are_shared(self, tmp_path: pathlib.Path) -> None:
        """現在入力だけで判定する遮断と入力補正は両ホストで動作する。"""
        blocked = _run(self._payload("uv run python script.py", tmp_path, "codex-uv", codex=True))
        decorated = _run(self._payload("git log --oneline", tmp_path, "codex-log", codex=True))

        assert blocked.returncode == 2
        assert decorated.returncode == 0
        assert "--decorate" in json.loads(decorated.stdout)["hookSpecificOutput"]["updatedInput"]["command"]

    def test_transcript_language_check_is_claude_only(self, tmp_path: pathlib.Path) -> None:
        """transcript由来の言語検査はCodexで起動しない。"""
        entry = {
            "type": "assistant",
            "message": {
                "id": "m1",
                "role": "assistant",
                "content": [{"type": "text", "text": "This is a plain English status report written for the reviewer."}],
                "stop_reason": "end_turn",
            },
        }
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text(json.dumps(entry, ensure_ascii=False) + "\n", encoding="utf-8")
        env = _plan_file_state_env(tmp_path)
        claude_payload = {
            **self._payload("ls", tmp_path, "lang-claude", codex=False),
            "transcript_path": str(transcript),
        }
        codex_payload = {
            **self._payload("ls", tmp_path, "lang-codex", codex=True),
            "transcript_path": str(transcript),
        }

        claude_result = _run(claude_payload, env_overrides=env)
        codex_result = _run(codex_payload, env_overrides=env)

        assert "英語主体" in _additional_context(claude_result)
        assert codex_result.stdout == ""
