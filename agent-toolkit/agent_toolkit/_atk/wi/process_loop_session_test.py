"""`atk wi process-loop`のプロンプトとオーケストレーターの選択、セッションの起動と終了の扱いのテスト。"""

import argparse
import io
import json
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import time
from collections.abc import Callable
from typing import Any

import pytest

from agent_toolkit._atk import codex_connection as _codex_connection
from agent_toolkit._atk import lane
from agent_toolkit._atk.wi import process_loop as _process_loop
from agent_toolkit._atk.wi import process_loop_env as _pl_env
from agent_toolkit._atk.wi import process_loop_session as _pl_session
from agent_toolkit._atk.wi import process_loop_update as _pl_update
from agent_toolkit._atk.wi import process_loop_worktree as _pl_worktree
from agent_toolkit._common import automated_prompt as _automated_prompt
from agent_toolkit._common import inherited_venv as _inherited_venv
from agent_toolkit._common import session_registry
from agent_toolkit._testing.process_loop_support import (
    isolate_process_loop_commands,
)

_ORIGINAL_RUN = subprocess.run
_ORIGINAL_CODEX_SESSION = _pl_session._run_cli_session  # pylint: disable=protected-access  # noqa: SLF001


def _run_claude_session(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    launch: Callable[..., subprocess.CompletedProcess[bytes]],
) -> None:
    """Claudeのオーケストレーターを1回起動し、本起動の`subprocess.run`を`launch`で受ける。"""
    monkeypatch.setattr(_pl_session, "_run_cli_session", lambda argv, env, cwd, **_kwargs: launch(argv, env=env, cwd=cwd))
    monkeypatch.setattr(lane, "cleanup_session", lambda _session, **_kwargs: {"retained": False})
    args = argparse.Namespace(resume=None, no_update=True, worktree="custom")
    assert _pl_session.run_process_session(
        args,
        tmp_path,
        "作業する",
        dict(os.environ),
        orchestrator="claude",
        model="sonnet",
        effort="high",
        resume_pending=False,
    )


def _connection_launch(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    order: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """本起動境界へ渡されたargv・環境と、起動時に読めるsettingsを取得する。"""
    api = tmp_path / "api.env"
    api.write_text("ANTHROPIC_API_KEY=selected-test-key\nANTHROPIC_BASE_URL=https://api.example.test\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_PAYG_ENV_FILE", str(api))
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_ORCHESTRATOR_CONNECTIONS", order)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "parent-test-key")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "parent-test-token")
    observed: dict[str, Any] = {}
    settings: dict[str, Any] = {}

    def launch(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        observed.update(argv=argv, env=kwargs["env"])
        info = session_registry.resolve(kwargs["env"][_pl_env.PROCESS_LOOP_SESSION_ID_ENV]).resume_info
        assert info is not None and info.cwd == str(tmp_path)
        raw = argv[argv.index("--settings") + 1]
        settings.update(json.loads(pathlib.Path(raw).read_text(encoding="utf-8") if not raw.startswith("{") else raw))
        return subprocess.CompletedProcess(argv, 0)

    _run_claude_session(tmp_path, monkeypatch, launch)
    return observed, settings


@pytest.mark.parametrize("resume", [None, "inherit", "legacy"])
def test_unconfigured_process_session_preserves_parent_auth(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, resume: str | None
) -> None:
    """未設定時とその会話の再開では、親のAPIキーと設定による通常の認証解決を保つ。"""
    monkeypatch.delenv("AGENT_TOOLKIT_CONFIG_CLAUDE_CREDITS_ENV_FILE", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "parent-test-key")
    monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "1")
    if resume:
        session_registry.publish(
            resume,
            terminal=True,
            engine="claude",
            cwd=str(tmp_path),
            claude_connection="inherit" if resume == "inherit" else None,
        )
    observed: dict[str, Any] = {}

    def launch(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        observed.update(env=kwargs["env"], settings=json.loads(argv[argv.index("--settings") + 1]))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(_pl_session, "_run_cli_session", lambda argv, env, cwd, **_kwargs: launch(argv, env=env, cwd=cwd))
    monkeypatch.setattr(lane, "cleanup_session", lambda _session, **_kwargs: {"retained": False})
    _pl_session.run_process_session(
        argparse.Namespace(resume=resume, no_update=True),
        tmp_path,
        "作業",
        dict(os.environ),
        orchestrator="claude",
        model="sonnet",
        effort="high",
        resume_pending=resume is not None,
    )
    assert observed["env"]["ANTHROPIC_API_KEY"] == "parent-test-key"
    assert observed["env"]["CLAUDE_CODE_USE_BEDROCK"] == "1"
    assert "apiKeyHelper" not in observed["settings"]
    assert "ANTHROPIC_API_KEY" not in observed["settings"]["env"]


@pytest.mark.parametrize("content", [None, "", "# 未記入\n"])
def test_invalid_env_file_warning_reaches_process_loop_stderr(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], content: str | None
) -> None:
    """無効なAPI環境ファイルでは、process-loopの起動がサブスクリプションで続き、標準エラーへパスと修正操作を示す。"""
    path = tmp_path / "invalid.env"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_PAYG_ENV_FILE", str(path))
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_ORCHESTRATOR_CONNECTIONS", "subscription,payg")
    observed: dict[str, Any] = {}

    def launch(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        observed.update(env=kwargs["env"])
        return subprocess.CompletedProcess(argv, 0)

    _run_claude_session(tmp_path, monkeypatch, launch)
    stderr = capsys.readouterr().err
    assert str(path) in stderr
    assert "atk config set claude_payg_env_file" in stderr
    assert observed["env"]["CLAUDE_CODE_OAUTH_TOKEN"]


def test_payg_orchestrator_uses_api_only(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """従量課金だけの使用順はAPIを使い、対話確認を避けるhelperへ鍵の所在だけを渡す。"""
    observed, settings = _connection_launch(tmp_path, monkeypatch, "payg")
    assert "ANTHROPIC_API_KEY" not in observed["env"]
    assert not observed["env"].get("CLAUDE_CODE_OAUTH_TOKEN")
    assert observed["env"]["ANTHROPIC_BASE_URL"] == "https://api.example.test"
    assert str(tmp_path / "api.env") in settings["apiKeyHelper"]
    assert "selected-test-key" not in str(observed["argv"])


def test_subscription_orchestrator_uses_subscription_only(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """API環境ファイルを適用せずサブスクリプションの認証を渡す。"""
    observed, settings = _connection_launch(tmp_path, monkeypatch, "subscription")
    assert observed["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == "test-subscription"
    assert not settings["apiKeyHelper"]
    assert "api.example.test" not in str(observed) + str(settings)


def test_subscription_orchestrator_drops_inherited_api_auth(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """親のAPIキー・トークンを本起動の環境とsettingsへ流入させない。"""
    observed, settings = _connection_launch(tmp_path, monkeypatch, "subscription")
    assert "ANTHROPIC_API_KEY" not in observed["env"]
    assert "ANTHROPIC_AUTH_TOKEN" not in observed["env"]
    assert "ANTHROPIC_API_KEY" not in settings["env"]
    assert "ANTHROPIC_AUTH_TOKEN" not in settings["env"]


def test_orchestrator_resume_probe_keeps_selected_connection(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """使用順の変更後も、保存した種別が使用順に含まれる会話は同じAPI接続で可用性を確認する。"""
    path = tmp_path / "selected.env"
    path.write_text("ANTHROPIC_API_KEY=selected-key\n", encoding="utf-8")
    session_registry.publish(
        "resume-api",
        terminal=True,
        engine="claude",
        model="sonnet",
        effort="high",
        cwd=str(tmp_path),
        claude_api_env_file=str(path),
        claude_connection="api",
        claude_role="orchestrator",
    )
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_ORCHESTRATOR_CONNECTIONS", "subscription,payg")
    observed: list[dict[str, Any]] = []

    def probe(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        observed.append(kwargs["env"])
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", probe)
    candidate = ("claude", "sonnet", "high")
    selected = _pl_session.select_available_orchestrator(
        [candidate],
        {**os.environ, _pl_env.PROCESS_LOOP_SESSION_ENV: "1"},
        tmp_path,
        resume_session_id="resume-api",
    )
    assert selected == (*candidate, None)
    assert observed[0]["ANTHROPIC_API_KEY"] == "selected-key"
    assert not observed[0].get("CLAUDE_CODE_OAUTH_TOKEN")


def _configure_codex_orchestrator(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, order: str) -> dict[str, str]:
    """Codexの統括担当の接続設定と、`config.toml`だけに定義したproviderを用意する。"""
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir(exist_ok=True)
    (codex_home / "config.toml").write_text(
        '[model_providers.my-credits]\nname = "my-credits"\nbase_url = "https://credits.example.test/v1"\n'
        'env_key = "MY_CREDITS_KEY"\nrequires_openai_auth = false\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CHORES_FAST_MODEL", "codex:gpt-6-luna/medium")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CODEX_CREDITS_PROVIDER", "my-credits")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CODEX_ORCHESTRATOR_CONNECTIONS", order)
    return {**os.environ, "CODEX_HOME": str(codex_home)}


def _codex_probes(monkeypatch: pytest.MonkeyPatch, *, chatgpt: bool) -> list[list[str]]:
    """有効なアカウントがChatGPTログインかどうかを与え、可用性判定のargvを記録する。"""
    probes: list[list[str]] = []

    def run(argv: list[str], *_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        probes.append(argv)
        return subprocess.CompletedProcess(argv, 0, "OK\n", "")

    def login(_env: Any) -> bool:
        return chatgpt

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(_codex_connection, "chatgpt_login", login)
    return probes


def _provider_option(argv: list[str]) -> str | None:
    """argvの`-c model_provider=<ID>`の値を返す。"""
    values = [argv[index + 1] for index, item in enumerate(argv[:-1]) if item == "-c"]
    return next((json.loads(value.partition("=")[2]) for value in values if value.startswith("model_provider=")), None)


def test_codex_orchestrator_probe_start_and_resume_use_selected_provider(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """可用性判定・本起動・再開の起動引数へ同じproviderを渡し、判定へ`config.toml`の定義も渡す。"""
    env = _configure_codex_orchestrator(monkeypatch, tmp_path, "subscription,credits")
    probes = _codex_probes(monkeypatch, chatgpt=False)
    candidate = ("codex", "gpt-5.6-sol", "medium")
    selected = _pl_session.select_available_orchestrator([candidate], env, tmp_path)
    # APIキーのアカウントではsubscriptionを除いて診断を出し、使用順の次のクレジットを選ぶ。
    assert selected == (*candidate, "my-credits")
    assert "codex_payg_provider openai" in capsys.readouterr().err
    assert len(probes) == 1 and "--ignore-user-config" in probes[0]
    assert _provider_option(probes[0]) == "my-credits"
    assert any(item.startswith("model_providers.my-credits={") and "credits.example.test" in item for item in probes[0])
    for resume_pending, resume in ((False, None), (True, "orchestrator-thread")):
        argv, _ = _pl_session._build_session_argv(  # pylint: disable=protected-access  # noqa: SLF001
            argparse.Namespace(resume=resume),
            "/goal 作業",
            dict(env),
            orchestrator="codex",
            model="gpt-5.6-sol",
            effort="medium",
            resume_pending=resume_pending,
            cwd=tmp_path,
            codex_provider="my-credits",
        )
        assert (argv[1] == "resume") is resume_pending
        assert _provider_option(argv) == "my-credits"


def test_codex_orchestrator_session_record_keeps_provider(tmp_path: pathlib.Path) -> None:
    """統括担当の起動中に公開するセッション記録へ、選んだproviderを保存する。"""
    env = {**os.environ, _pl_env.PROCESS_LOOP_SESSION_ID_ENV: "orchestrator-thread", "CODEX_HOME": str(tmp_path)}
    result = _ORIGINAL_CODEX_SESSION(
        [sys.executable, "-c", "import time; time.sleep(1.5)"],
        env,
        tmp_path,
        orchestrator="codex",
        model="gpt-5.6-sol",
        effort="medium",
        codex_provider="my-credits",
    )
    assert result.returncode == 0
    info = session_registry.resolve("orchestrator-thread").resume_info
    assert info is not None and info.codex_model_provider == "my-credits"
    assert info.codex_subscription_provider is None


@pytest.mark.parametrize(("order", "expected"), [("subscription,credits", "my-credits"), ("subscription", "openai")])
def test_codex_orchestrator_resume_keeps_saved_provider_until_removed_from_order(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, order: str, expected: str
) -> None:
    """保存した接続で再開し、その種別を使用順から外した場合だけ使用順の先頭の利用可能な候補へ移る。"""
    env = _configure_codex_orchestrator(monkeypatch, tmp_path, order)
    probes = _codex_probes(monkeypatch, chatgpt=True)
    session_registry.publish(
        "orchestrator-thread",
        terminal=True,
        engine="codex",
        model="gpt-5.6-sol",
        effort="medium",
        cwd=str(tmp_path),
        model_type="orchestrate",
        codex_model_provider="my-credits",
    )
    selected = _pl_session.select_available_orchestrator(
        [("codex", "other-model", "high")], env, tmp_path, resume_session_id="orchestrator-thread"
    )
    assert selected == ("codex", "gpt-5.6-sol", "medium", expected)
    assert [_provider_option(argv) for argv in probes] == [expected]


@pytest.fixture(autouse=True)
def _isolate_process_loop_commands(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """外部コマンド・Claude設定・初期値のTTLをユーザー環境から分離する。"""
    isolate_process_loop_commands(monkeypatch, tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-subscription")

    def run_codex(argv: list[str], env: dict[str, str], cwd: pathlib.Path, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        # 従来の起動引数の試験は外部CLI全体をrunで代用する。実子試験は監視を含む本体へ到達する。
        if subprocess.run is _ORIGINAL_RUN:
            return _ORIGINAL_CODEX_SESSION(argv, env, cwd, **kwargs)
        return subprocess.run(argv, check=False, env=env, cwd=cwd, creationflags=_pl_env.session_creation_flags("codex"))

    monkeypatch.setattr(_pl_session, "_run_cli_session", run_codex)


_PROCESS_LOOP_SESSION_ENV = "AGENT_TOOLKIT_PROCESS_LOOP_SESSION"


_PROCESS_LOOP_SESSION_ID_ENV = "AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID"


_DELEGATED_SESSION_ENV = "AGENT_TOOLKIT_DELEGATED_SESSION"


class TestProcessLoopPromptAndEnv:
    """process-loopサブコマンド: claudeへ渡す最初のプロンプトと環境変数、正常終了時の反復継続を検証する。"""

    def test_empty_path_entries_are_preserved(self) -> None:
        """`PATH`の空要素を除去対象に含めないこと。

        POSIXの`PATH`では空要素がカレントディレクトリを表すため、除去すると解決順序が宣言外に変わる。
        """
        venv_root = "/tmp/venv"
        env = {
            "VIRTUAL_ENV": venv_root,
            "PATH": os.pathsep.join((f"{venv_root}/bin", "", "/usr/bin", "")),
        }
        _inherited_venv.strip_inherited_venv(env)
        assert env["PATH"] == os.pathsep.join(("", "/usr/bin", ""))

    def test_prompt_is_short_goal_with_workflow_boundary(self) -> None:
        """新規セッションの目的文がスキルの完遂だけを伝え、機械生成を示す`atk-auto`要素を持つこと。"""
        prompt = _pl_session.build_process_loop_prompt()  # pylint: disable=protected-access  # noqa: SLF001
        assert prompt.startswith("/goal ")
        assert "agent-toolkit:process-wi-all-reposを完遂する" in prompt
        assert _automated_prompt.contains(prompt)
        assert "agent-toolkit:exit-session" not in prompt

        forbidden_details = (
            "atk wi list",
            "atk wi show",
            "frontmatter",
            "worker",
            "レビュー",
            "commit",
            "push",
            "CI",
            "atk wi adopt",
            "session-review",
        )
        assert all(detail not in prompt for detail in forbidden_details)

    def test_prompt_references_process_wi(self) -> None:
        """プロンプトが後続工程の集約先としてprocess-wiスキルを参照すること。"""
        prompt = _pl_session.build_process_loop_prompt()  # pylint: disable=protected-access  # noqa: SLF001
        assert "agent-toolkit:process-wi" in prompt

    def test_manifest_goal_separates_own_result_from_post_exit_acceptance(self, tmp_path: pathlib.Path) -> None:
        """開始時入力と本人の成果保存、CLI終了後の常駐検収を区別する。"""
        manifest = tmp_path / "round.json"
        prompt = _pl_session.build_process_loop_prompt(manifest)

        assert prompt.startswith('/goal <atk-auto source="process-loop" kind="goal">')
        assert f"開始時入力のmanifest {manifest}" in prompt
        assert "取得済み候補・所有・準備先" in prompt
        assert "スキル所定の処理と終了準備を終え" in prompt
        assert "result_fileへ実態に即した対象別結果を保存した時点" in prompt
        assert "統括自身の到達点" in prompt
        assert "続行不能時も理由と残工程を保存して制御を常駐へ返す" in prompt
        assert "常駐による結果の受理・所有解放・回収は統括CLI終了後に行う" in prompt
        assert prompt.endswith("</atk-auto>")
        assert "completed" not in prompt
        assert "atk wi list" not in prompt

    @pytest.mark.parametrize("orchestrator", ["claude", "codex", "agy"])
    @pytest.mark.parametrize("resume", [None, "saved-session"])
    def test_manifest_goal_keeps_host_specific_delivery(
        self, tmp_path: pathlib.Path, orchestrator: str, resume: str | None
    ) -> None:
        """manifest付き目標は新規・再開ともClaudeだけに/goalを付けて配送する。"""
        prompt = _pl_session.build_process_loop_prompt(tmp_path / "round.json")
        argv, _ = _pl_session._build_session_argv(  # pylint: disable=protected-access  # noqa: SLF001
            argparse.Namespace(resume=resume),
            prompt,
            dict(os.environ),
            orchestrator=orchestrator,
            model="test-model",
            effort="medium",
            resume_pending=resume is not None,
            cwd=tmp_path,
        )
        assert argv[-1] == (prompt if orchestrator == "claude" else prompt.removeprefix("/goal "))

    def test_prompt_does_not_inject_publish_destination(self) -> None:
        """対象リポジトリ固有の公開先をprocess-loopが目的文へ注入しない。"""
        prompt = _pl_session.build_process_loop_prompt()  # pylint: disable=protected-access  # noqa: SLF001

        assert "refs/remotes/origin/master" not in prompt
        assert "公開先" not in prompt
        assert "git push" not in prompt
        assert "commit" not in prompt
        assert "レビュー" not in prompt

    def test_unstartable_candidate_falls_back_to_next_engine(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """候補の実行ファイルを起動できない場合も残る候補を判定する。"""
        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CHORES_FAST_MODEL", "codex:gpt-6-luna/medium,claude:haiku/medium")
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(cmd)
            if cmd[:2] == ["codex", "exec"]:
                raise FileNotFoundError("codex")
            return subprocess.CompletedProcess(cmd, 0, "OK\n", "")

        monkeypatch.setattr(subprocess, "run", fake_run)

        selected = _pl_session.select_available_orchestrator(  # pylint: disable=protected-access  # noqa: SLF001
            [("codex", "gpt-5.6-sol", "medium"), ("claude", "sonnet", "high")],
            {},
            tmp_path,
        )

        assert selected == ("claude", "sonnet", "high", None)
        assert [call[0] for call in calls] == ["codex", "claude"]

    def test_all_unstartable_candidates_use_abnormal_exit(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """全候補の実行機能を起動できない場合は既存の異常終了へ戻る。"""
        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CHORES_FAST_MODEL", "codex:gpt-6-luna/medium,claude:haiku/medium")
        calls: list[list[str]] = []

        def fail_start(cmd: list[str], *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(cmd)
            raise OSError("起動不能")

        monkeypatch.setattr(subprocess, "run", fail_start)

        with pytest.raises(SystemExit) as exc_info:
            _pl_session.select_available_orchestrator(  # pylint: disable=protected-access  # noqa: SLF001
                [("codex", "gpt-5.6-sol", "medium"), ("claude", "sonnet", "high")],
                {},
                tmp_path,
            )

        assert exc_info.value.code == 1
        assert [call[0] for call in calls] == ["codex", "claude"]


class TestCodexWindowsSessionConfiguration:
    """Windows Codexだけに適用するプロセスグループとhook互換PATHを検証する。"""

    def test_windows_codex_uses_new_process_group(self) -> None:
        """Windows Codexだけが親と別のコンソール制御グループで起動すること。"""
        assert _pl_env.session_creation_flags("codex", platform="nt") == 0x00000200  # pylint: disable=protected-access  # noqa: SLF001
        assert _pl_env.session_creation_flags("claude", platform="nt") == 0  # pylint: disable=protected-access  # noqa: SLF001
        assert _pl_env.session_creation_flags("codex", platform="posix") == 0  # pylint: disable=protected-access  # noqa: SLF001

    def test_windows_codex_alone_prepends_bash_shim(self) -> None:
        """共通環境を変更せず、Windows Codex用コピーだけへshimを追加すること。"""
        inherited = {"PATH": os.pathsep.join(("first", "second")), "KEEP": "value"}

        codex_env = _pl_env.session_env(inherited, "codex", platform="nt")  # pylint: disable=protected-access  # noqa: SLF001
        claude_env = _pl_env.session_env(inherited, "claude", platform="nt")  # pylint: disable=protected-access  # noqa: SLF001
        posix_env = _pl_env.session_env(inherited, "codex", platform="posix")  # pylint: disable=protected-access  # noqa: SLF001

        shim_dir = pathlib.Path(_process_loop.__file__).resolve().parents[2] / "windows-shims"
        assert pathlib.Path(codex_env["PATH"].split(os.pathsep, maxsplit=1)[0]) == shim_dir
        assert claude_env["CLAUDE_CODE_RETRY_WATCHDOG"] == "1"
        assert "CLAUDE_CODE_RETRY_WATCHDOG" not in codex_env
        assert "CLAUDE_CODE_RETRY_WATCHDOG" not in posix_env
        assert claude_env["KEEP"] == inherited["KEEP"]
        assert posix_env == inherited
        assert inherited["PATH"] == os.pathsep.join(("first", "second"))

    @pytest.mark.skipif(os.name != "nt", reason="Windows用cmd shimの実機検証")
    def test_bash_shim_converts_windows_script_path_and_preserves_stdin_and_exit_code(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """Git BashへWindowsパスを渡し、hookの入出力契約を保持すること。"""
        script = tmp_path / "hook.sh"
        script.write_text(
            "read -r value || exit 31\n"
            '[ "$value" = "ok" ] || exit 32\n'
            '[ "$#" -eq 12 ] || exit 33\n'
            '[ "${10}" = "ten value" ] || exit 34\n'
            "exit 23\n",
            encoding="utf-8",
        )
        shim = pathlib.Path(_process_loop.__file__).resolve().parent / "windows-shims" / "bash.cmd"
        env = os.environ.copy()
        env["PATH"] = os.pathsep.join((str(shim.parent), env.get("PATH", "")))

        result = subprocess.run(
            [
                "cmd.exe",
                "/d",
                "/c",
                "bash",
                str(script),
                "one",
                "two",
                "three",
                "four",
                "five",
                "six",
                "seven",
                "eight",
                "nine",
                "ten value",
                "eleven",
                "twelve",
            ],
            input=b"ok\n",
            text=False,
            check=False,
            env=env,
        )

        assert result.returncode == 23


class TestProcessLoopReturncode:
    """process-loopサブコマンドのオーケストレーター・OS別returncode判定を検証する。"""

    @pytest.mark.parametrize(
        ("orchestrator", "platform", "returncode", "expected"),
        [
            ("claude", "posix", 0, True),
            ("claude", "posix", -15, True),
            ("claude", "posix", 15, True),
            ("claude", "posix", 143, True),
            ("codex", "posix", 0, True),
            ("codex", "posix", -15, True),
            ("codex", "nt", 0, True),
            ("codex", "posix", 15, False),
            ("codex", "posix", 143, False),
            ("codex", "nt", 15, False),
            ("codex", "nt", 143, False),
            ("claude", "posix", 42, False),
            ("codex", "posix", 42, False),
            ("codex", "nt", 42, False),
        ],
    )
    def test_normal_exit_codes_follow_orchestrator_and_platform(
        self,
        orchestrator: str,
        platform: str,
        returncode: int,
        expected: bool,
    ) -> None:
        """オーケストレーターとOSごとの正常終了コードだけを受け入れること。"""
        assert (
            _pl_session._is_normal_session_exit(  # pylint: disable=protected-access  # noqa: SLF001
                orchestrator,
                returncode,
                platform=platform,
            )
            is expected
        )


class TestConsoleTitleReset:
    """常駐区間のコンソールタイトル制御を検証する（再起動先は`TestProcessLoopUpdateAndRestart`の既存argv検証が担う）。"""

    def test_helper_functions_reset_title_after_each_run(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """`_sync_worktree_with_upstream`・`_check_and_restart_on_update`配下の各subprocess.run直後にタイトルを再設定すること。"""
        local_path = tmp_path / "repo"
        (local_path / ".claude" / "worktrees" / "process-loop").mkdir(parents=True)
        calls: list[str] = []
        monkeypatch.setattr(_pl_session._console_title, "set_console_title", calls.append)  # pylint: disable=protected-access  # noqa: SLF001

        def fake_run(cmd: list[str], *_a: object, **_kwargs: object) -> subprocess.CompletedProcess[Any]:
            if cmd == ["git", "check-ignore", "-q", ".claude/worktrees/"]:
                return subprocess.CompletedProcess(cmd, 0, "", "")
            if cmd == ["git", "check-ref-format", "--branch", "worktree-process-loop"]:
                return subprocess.CompletedProcess(cmd, 0, "", "")
            if cmd == ["git", "rev-parse", "--git-common-dir"]:
                return subprocess.CompletedProcess(cmd, 0, str(local_path / ".git"), "")
            if cmd == ["git", "rev-parse", "--show-toplevel"]:
                return subprocess.CompletedProcess(cmd, 0, str(local_path / ".claude" / "worktrees" / "process-loop"), "")
            if cmd == ["git", "symbolic-ref", "HEAD"]:
                return subprocess.CompletedProcess(cmd, 0, "worktree-process-loop", "")
            if cmd == ["git", "symbolic-ref", "refs/remotes/origin/HEAD"]:
                return subprocess.CompletedProcess(cmd, 0, "origin/master\n", "")
            if cmd == ["git", "remote"]:
                return subprocess.CompletedProcess(cmd, 0, "origin\n", "")
            stdout = "1\n" if "rev-list" in cmd else ""
            return subprocess.CompletedProcess(cmd, 0, stdout, "")

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(_pl_update, "code_hash", lambda _plugin_root: "same-hash")  # 再起動へ進ませない
        _pl_worktree._sync_worktree_with_upstream(local_path, "process-loop")  # pylint: disable=protected-access  # noqa: SLF001
        _pl_update.check_and_restart_on_update(tmp_path, "same-hash", ["argv0"])  # pylint: disable=protected-access  # noqa: SLF001
        assert calls
        assert all(call == "atk wi process-loop" for call in calls)

    @pytest.mark.parametrize(
        ("platform", "tty_result", "reset_path", "expected"),
        [
            ("posix", True, "/usr/bin/reset", [["/usr/bin/reset"]]),
            ("nt", True, "/usr/bin/reset", []),
            ("posix", False, "/usr/bin/reset", []),
            ("posix", True, None, []),
            ("posix", AttributeError, "/usr/bin/reset", []),
            ("posix", ValueError, "/usr/bin/reset", []),
        ],
    )
    def test_reset_console_runs_only_on_posix_tty_with_reset_available(
        self,
        monkeypatch: pytest.MonkeyPatch,
        platform: str,
        tty_result: bool | type[Exception],
        reset_path: str | None,
        expected: list[list[str]],
    ) -> None:
        """POSIXのTTYでresetを解決できる場合だけコンソールを初期化する。"""
        calls: list[list[str]] = []

        class TestStream(io.StringIO):
            def isatty(self) -> bool:
                if isinstance(tty_result, type) and issubclass(tty_result, Exception):
                    raise tty_result
                assert isinstance(tty_result, bool)
                return tty_result

        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            assert kwargs == {"check": False}
            calls.append(command)
            return subprocess.CompletedProcess(command, 0)

        monkeypatch.setattr(shutil, "which", lambda command: reset_path if command == "reset" else None)
        monkeypatch.setattr(subprocess, "run", fake_run)

        _pl_env.reset_console(  # pylint: disable=protected-access  # noqa: SLF001
            platform=platform,
            stream=TestStream(),
        )

        assert calls == expected

    def test_console_is_reset_between_child_session_and_title_reset(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """子セッション終了後、タイトル再設定より前にコンソールを初期化する。"""
        calls: list[str] = []

        def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append("session")
            return subprocess.CompletedProcess(command, 0)

        monkeypatch.setattr(_pl_session, "_build_session_argv", lambda *_a, **_kw: (["claude"], None))
        monkeypatch.setattr(_pl_session._process_loop_log, "append", lambda *_a, **_kw: None)  # pylint: disable=protected-access  # noqa: SLF001
        monkeypatch.setattr(_pl_env, "session_env", lambda env, _orchestrator: env)
        monkeypatch.setattr(_pl_env, "session_creation_flags", lambda _orchestrator: 0)
        monkeypatch.setattr(_pl_env, "reset_console", lambda: calls.append("reset"))
        monkeypatch.setattr(_pl_session._console_title, "set_console_title", lambda _title: calls.append("title"))  # pylint: disable=protected-access  # noqa: SLF001
        monkeypatch.setattr(_pl_session, "_run_cli_session", lambda argv, _env, _cwd, **_kwargs: fake_run(argv))

        result = _pl_session.run_process_session(  # pylint: disable=protected-access  # noqa: SLF001
            argparse.Namespace(no_update=True),
            tmp_path,
            "prompt",
            {},
            orchestrator="claude",
            model="model",
            effort="effort",
            resume_pending=False,
        )

        assert result is True
        assert calls == ["session", "reset", "title"]


@pytest.mark.parametrize("new_turn", [False, True])
@pytest.mark.parametrize("resume", [False, True])
def test_codex_usage_limit_child_continues_next_iteration(
    tmp_path: pathlib.Path,
    new_turn: bool,
    resume: bool,
) -> None:
    """実子の上限停止だけを親が終了し、新turn開始後の子は自身の正常終了まで保持する。"""
    binary = tmp_path / "codex"
    marker = tmp_path / "completed"
    binary.write_text(
        f"#!{sys.executable}\n"
        "import datetime, json, os, pathlib, time\n"
        "directory = pathlib.Path(os.environ['CODEX_HOME']) / 'sessions/2026/10/16'\n"
        "directory.mkdir(parents=True, exist_ok=True)\n"
        "stamp = datetime.datetime.now(datetime.UTC).isoformat()\n"
        "events = [\n"
        " {'type': 'session_meta', 'payload': {'id': 'child', 'cwd': os.getcwd(),\n"
        " 'timestamp': '2026-01-01T00:00:00Z' if os.environ['RESUME'] == '1' else stamp,\n"
        " 'originator': 'codex-tui', 'source': 'cli'}},\n"
        " {'type': 'event_msg', 'timestamp': stamp, 'payload': {'type': 'task_started'}},\n"
        " {'type': 'event_msg', 'payload': {'type': 'task_complete', 'error': {'codex_error_info': 'usage_limit_exceeded'}}},\n"
        "]\n"
        "if os.environ['NEW_TURN'] == '1': events.append({'type': 'event_msg', 'payload': {'type': 'task_started'}})\n"
        "record = directory / 'rollout-2026-10-16-00000000-0000-0000-0000-000000000001.jsonl'\n"
        "record.write_text('\\n'.join(json.dumps(e) for e in events))\n"
        "time.sleep(2.5 if os.environ['NEW_TURN'] == '1' else 20)\n"
        "pathlib.Path(os.environ['COMPLETED']).write_text('completed')\n",
        encoding="utf-8",
    )
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    env = {
        **os.environ,
        "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
        "CODEX_HOME": str(tmp_path / "codex-home"),
        "NEW_TURN": "1" if new_turn else "0",
        "RESUME": "1" if resume else "0",
        "COMPLETED": str(marker),
    }
    finished: list[bool] = []
    started = time.monotonic()
    assert (
        _pl_session.run_process_session(
            argparse.Namespace(no_update=True, resume="00000000-0000-0000-0000-000000000001" if resume else None),
            tmp_path,
            "goal",
            env,
            orchestrator="codex",
            model="test",
            effort="high",
            resume_pending=resume,
            on_session_finished=finished.append,
        )
        is True
    )
    assert finished == [True]
    assert marker.exists() is new_turn
    assert time.monotonic() - started < 15
