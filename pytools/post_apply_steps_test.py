# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""post_applyのstepsの試験。"""

# pylint: disable=protected-access

import ast
import dataclasses
import inspect
import re
import typing
from pathlib import Path

import pytest

from pytools import post_apply
from pytools._internal import apply_dotfiles_plugins, post_apply_outcome
from pytools._internal.post_apply_test_helpers import _isolate_sync_report as _isolate_sync_report
from pytools._internal.post_apply_test_helpers import _isolate_update_log as _isolate_update_log
from pytools._internal.post_apply_test_helpers import _make_plugin_step, _make_step


class TestDefaultSteps:
    """`DEFAULT_STEPS`の登録内容と先行工程・対象OSの宣言を検証する。"""

    # 共有資源と実行ファイルの前提から定めた先行工程。宣言の欠落は同時実行による競合を招く。
    _EXPECTED_PREDECESSORS: dict[str, set[str]] = {
        "mise セットアップ": {"npm/pnpm サプライチェーン対策", "bin PATH 登録 (Windows)"},
        "pnpm サプライチェーン対策": {"mise セットアップ"},
        "Codex CLI の導入と更新": {"mise セットアップ", "Codex 診断ログの通常ストレージ復元 (Linux)"},
        "Codex 診断ログの通常ストレージ復元 (Linux)": {"Codex リンクの同期"},
        "Codex の Claude MCP 登録削除": {"Codex CLI の導入と更新"},
        "Claude Code CLI の導入と更新": {"mise セットアップ"},
        "OpenCode CLI の導入": {"mise セットアップ", "Codex CLI の導入と更新", "Claude Code CLI の導入と更新"},
        "OpenCode toolkit の同期": {"OpenCode CLI の導入"},
        "Codex plugin snapshot の生成": {"Claude Code plugin のインストール"},
        "Antigravity plugin の生成と登録": {"Antigravity CLI の導入", "Codex plugin snapshot の生成", "Gemini 設定"},
        "Codex plugin のインストール": {
            "Codex の Claude MCP 登録削除",
            "Codex plugin snapshot の生成",
            "Codex リンクの同期",
            "旧配布物の削除",
        },
        "agent-toolkit ルールの同期": {"旧配布物の削除"},
        "Claude Code plugin のインストール": {"Claude Code CLI の導入と更新"},
        "Claude Code plugin cache の旧版削除": {"Claude Code plugin のインストール"},
        "agents_serverのuv環境ウォームアップ": {"Claude Code plugin のインストール", "Codex plugin のインストール"},
        "pyfltr MCPのuv環境ウォームアップ": {"Claude Code plugin のインストール", "Codex plugin のインストール"},
        "旧Codex User scope MCP登録の移行": {"Claude Code CLI の導入と更新", "Codex plugin のインストール"},
        "Claude 設定": {"Claude Code plugin のインストール", "旧Codex User scope MCP登録の移行"},
        "claude-statusline バイナリの取得": {"Codex CLI の導入と更新"},
        "libarchive (Windows)": {"mise セットアップ"},
        "atk serve 自動起動セットアップ (Linux)": {"Claude Code plugin のインストール", "agents_serverのuv環境ウォームアップ"},
        "一時ファイルの経過削除タイマー セットアップ (Linux)": {"atk serve 自動起動セットアップ (Linux)"},
    }
    _WINDOWS_STEPS = {
        "bin PATH 登録 (Windows)",
        "MSYS 環境変数 (Windows)",
        "user.env 環境変数 (Windows)",
        "libarchive (Windows)",
        "Windowsレジストリ設定",
        "SendTo ショートカット (Windows)",
        "メディアリモコン自動起動 (Windows/media_remote)",
        "ユーザー PATH 整理 (Windows)",
    }
    _LINUX_STEPS = {
        "Codex 診断ログの通常ストレージ復元 (Linux)",
        "tmux プラグインの導入 (Linux)",
        "atk serve 自動起動セットアップ (Linux)",
        "一時ファイルの経過削除タイマー セットアップ (Linux)",
    }

    def test_step_names_are_unique_and_declarations_resolve(self) -> None:
        """ステップ名は一意であり、全ての先行工程の宣言が既存ステップを指し循環しない。"""
        steps = post_apply.DEFAULT_STEPS  # noqa: SLF001
        names = [step.name for step in steps]
        assert len(names) == len(set(names))
        post_apply._resolve_predecessors(steps)  # noqa: SLF001

    def test_predecessor_declarations(self) -> None:
        """共有資源を扱うステップの組へ先行工程を宣言する。"""
        declared = {step.name: set(step.after) for step in post_apply.DEFAULT_STEPS if step.after}  # noqa: SLF001
        assert declared == self._EXPECTED_PREDECESSORS

    def test_user_path_cleanup_follows_all_other_steps(self) -> None:
        """ユーザーPATHの整理は他の全ステップの後に実行する。"""
        steps = post_apply.DEFAULT_STEPS  # noqa: SLF001
        assert steps[-1].name == "ユーザー PATH 整理 (Windows)"
        assert [step.name for step in steps if step.after_all_preceding] == [
            "dotfilesプラグインの反映",
            "ユーザー PATH 整理 (Windows)",
        ]

    @pytest.mark.parametrize("platform", ["linux", "win32"])
    def test_dotfiles_plugins_follow_main_steps_and_precede_path_cleanup(
        self,
        monkeypatch: pytest.MonkeyPatch,
        platform: str,
    ) -> None:
        """通常の工程表で本体後処理を終えてからプラグインを反映し、最後にPATHを整理する。"""
        registered = post_apply.DEFAULT_STEPS
        plugin_steps = [step for step in registered if step.run is apply_dotfiles_plugins.run]
        assert len(plugin_steps) == 1
        plugin = plugin_steps[0]
        assert plugin.name == "dotfilesプラグインの反映"
        assert not plugin.platforms
        plugin_index = registered.index(plugin)
        expected_before = {step.name for step in registered[:plugin_index] if not step.platforms or platform in step.platforms}
        calls: list[str] = []

        def apply_plugins() -> post_apply_outcome.PostApplyOutcome:
            assert set(calls) == expected_before
            calls.append(plugin.name)
            return post_apply_outcome.PostApplyOutcome(changed=True)

        monkeypatch.setattr(post_apply, "sys", type("_Platform", (), {"platform": platform}))
        monkeypatch.setattr(post_apply, "_home_is_substituted", lambda: False)
        monkeypatch.setattr(
            post_apply,
            "DEFAULT_STEPS",
            [
                dataclasses.replace(
                    step,
                    run=apply_plugins if step is plugin else _make_step(step.name, calls),
                )
                for step in registered
            ],
        )

        results, _ = post_apply.run()

        assert all(result.ok for result in results)
        assert calls.count(plugin.name) == 1
        if platform == "win32":
            assert calls[-2:] == [plugin.name, "ユーザー PATH 整理 (Windows)"]
        else:
            assert calls[-1] == plugin.name

    def test_platform_declarations(self) -> None:
        """実行中のOSでは何もしないステップへ対象OSを宣言する。"""
        steps = post_apply.DEFAULT_STEPS  # noqa: SLF001
        assert {step.name for step in steps if step.platforms == ("win32",)} == self._WINDOWS_STEPS
        assert {step.name for step in steps if step.platforms == ("linux",)} == self._LINUX_STEPS
        assert not [step.name for step in steps if step.platforms not in ((), ("win32",), ("linux",))]

    def test_pyfltr_mcp_warmup_registered_once(self) -> None:
        """pyfltr MCPのウォームアップを1回だけ登録する。"""
        names = [step.name for step in post_apply.DEFAULT_STEPS]  # noqa: SLF001
        assert names.count("pyfltr MCPのuv環境ウォームアップ") == 1

    def test_statusline_binary_step_registered(self):
        """claude-statuslineバイナリ取得ステップを1回登録する。"""
        names = [step.name for step in post_apply.DEFAULT_STEPS]  # pylint: disable=protected-access  # noqa: SLF001
        assert names.count("claude-statusline バイナリの取得") == 1

    def test_removed_steps_are_not_registered(self) -> None:
        """廃止済みの計画移行と旧計画ビューアーの自動起動を登録しない。"""
        names = [step.name for step in post_apply.DEFAULT_STEPS]  # noqa: SLF001
        assert not [name for name in names if "claude-plans-viewer" in name or "計画" in name]


class TestPluginRecommendations:
    """``install_claude_plugins.run()`` の推奨コマンド戻り値による案内出力。"""

    def test_prints_single_recommendation_without_continuation(
        self,
        capsys: pytest.CaptureFixture[str],
    ):
        """推奨コマンドが 1 件のみなら && も継続記号も付けず単一行で出力する。"""
        fake_recommendations = ["claude plugin install a --scope=user"]
        steps: list[tuple[str, post_apply.Callable[[], post_apply_outcome.PostApplyOutcome]]] = [
            ("plugins", _make_plugin_step(fake_recommendations)),
        ]
        with pytest.raises(SystemExit):
            post_apply.main(runner=lambda: post_apply.run(steps=steps))
        stdout_lines = capsys.readouterr().out.splitlines()
        assert any("推奨プラグイン設定" in line for line in stdout_lines)
        assert "claude plugin install a --scope=user" in stdout_lines
        # コマンド行は cmd.exe での貼り付け失敗を避けるため行頭インデントを付けない。
        assert not any(line.startswith(" ") for line in stdout_lines if "claude plugin" in line)
        assert not any("&&" in line for line in stdout_lines)

    def test_prints_multiple_recommendations_bash(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ):
        """bash 系では && \\ で連結し、最終行のみ継続記号なしで出力する。"""
        monkeypatch.setattr(post_apply.sys, "platform", "linux")
        fake_recommendations = [
            "claude plugin install a --scope=user",
            "claude plugin install b --scope=user",
            "claude plugin disable c --scope=user",
        ]
        steps: list[tuple[str, post_apply.Callable[[], post_apply_outcome.PostApplyOutcome]]] = [
            ("plugins", _make_plugin_step(fake_recommendations)),
        ]
        with pytest.raises(SystemExit):
            post_apply.main(runner=lambda: post_apply.run(steps=steps))
        stdout_lines = capsys.readouterr().out.splitlines()
        assert any("推奨プラグイン設定" in line for line in stdout_lines)
        assert "claude plugin install a --scope=user && \\" in stdout_lines
        assert "claude plugin install b --scope=user && \\" in stdout_lines
        assert "claude plugin disable c --scope=user" in stdout_lines
        # コマンド行は cmd.exe での貼り付け失敗を避けるため行頭インデントを付けない。
        assert not any(line.startswith(" ") for line in stdout_lines if "claude plugin" in line)

    def test_prints_multiple_recommendations_windows(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        capsys: pytest.CaptureFixture[str],
    ):
        """Windows では && ^ で連結し、最終行のみ継続記号なしで出力する。"""
        monkeypatch.setattr(post_apply.sys, "platform", "win32")
        fake_recommendations = [
            "claude plugin install a --scope=user",
            "claude plugin disable b --scope=user",
        ]
        steps: list[tuple[str, post_apply.Callable[[], post_apply_outcome.PostApplyOutcome]]] = [
            ("plugins", _make_plugin_step(fake_recommendations)),
        ]
        with caplog.at_level("INFO", logger=post_apply.logger.name), pytest.raises(SystemExit):
            post_apply.main(runner=lambda: post_apply.run(steps=steps))
        stdout_lines = capsys.readouterr().out.splitlines()
        assert "claude plugin install a --scope=user && ^" in stdout_lines
        assert "claude plugin disable b --scope=user" in stdout_lines
        # cmd.exe では `^` 継続後の行頭空白が解析エラーを起こすため、コマンド行は無インデントとする。
        assert not any(line.startswith(" ") for line in stdout_lines if "claude plugin" in line)

    def test_no_output_when_no_recommendations(
        self,
        caplog: pytest.LogCaptureFixture,
        capsys: pytest.CaptureFixture[str],
    ):
        """推奨コマンドが空なら案内を出力しない。"""
        steps: list[tuple[str, post_apply.Callable[[], post_apply_outcome.PostApplyOutcome]]] = [
            ("ok", _make_step("ok", [], changed=False)),
        ]
        with caplog.at_level("INFO", logger=post_apply.logger.name), pytest.raises(SystemExit):
            post_apply.main(runner=lambda: post_apply.run(steps=steps))
        messages = [record.getMessage() for record in caplog.records]
        assert not any("推奨プラグイン設定" in m for m in messages)
        stdout_lines = capsys.readouterr().out.splitlines()
        assert not any("claude plugin" in line for line in stdout_lines)


def test_all_steps_return_post_apply_outcome() -> None:
    """全工程の`run`の戻り値の型が`PostApplyOutcome`だけである。"""
    returns = {step.name: typing.get_type_hints(step.run).get("return") for step in post_apply.DEFAULT_STEPS}
    assert {name: hint for name, hint in returns.items() if hint is not post_apply_outcome.PostApplyOutcome} == {}


def test_predecessor_names_are_not_duplicated_literals() -> None:
    """工程名の文字列リテラルは`post_apply.py`に1回だけ書き、先行工程の指定は定数で参照する。"""
    source = Path(post_apply.__file__).read_text(encoding="utf-8")
    literals = [
        node.value for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    names = {step.name for step in post_apply.DEFAULT_STEPS}
    duplicated = sorted(name for name in names if literals.count(name) > 1)
    assert not duplicated


def test_platform_steps_have_no_module_os_guard() -> None:
    """`platforms`を宣言した工程のモジュールは、対象OSを自ら判定しない。"""
    guards = re.compile(r"\bsys\.platform\b|\bos\.name\b|\bplatform\.system\(")
    offenders = []
    for step in post_apply.DEFAULT_STEPS:
        if not step.platforms:
            continue
        module = inspect.getmodule(step.run)
        assert module is not None
        if module is post_apply:
            continue
        source = Path(inspect.getfile(module)).read_text(encoding="utf-8")
        if guards.search(source):
            offenders.append(module.__name__)
    assert not offenders


def test_internal_module_names_have_no_legacy_prefix_or_os_suffix() -> None:
    """`pytools/_internal/`のモジュール名に`warm_`と対象OSの接尾辞が無い。"""
    internal = Path(post_apply.__file__).parent / "_internal"
    pattern = re.compile(r"^(warm_.*|.*_(linux|windows)(_test)?)\.py$")
    assert not sorted(path.name for path in internal.glob("*.py") if pattern.match(path.name))
