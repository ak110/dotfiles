"""`atk`のトップレベルCLIと`atk wi`の基本サブコマンドのテスト。

同値分割と境界値分析で各サブコマンドの観点を網羅する。
add・本文要約切り詰めなど基本サブコマンドの単体テストを集約する。
サブコマンドごとのテストは各実装の隣の`*_test.py`に置く。
UWI共通ヘルパーは本ファイルと他のテストの双方から使うため本ファイルに残置する。
"""

import argparse
import contextlib
import datetime
import json
import os
import pathlib
import subprocess
import sys
import time
import types
from collections.abc import Callable
from typing import Any

import pytest

from agent_toolkit import atk  # noqa: E402  # pylint: disable=wrong-import-position
from agent_toolkit._agents_server import manager as agents_manager
from agent_toolkit._atk import config as _config  # noqa: E402  # pylint: disable=wrong-import-position
from agent_toolkit._atk import git_sync as _git_sync
from agent_toolkit._atk import managed_temp as _managed_temp  # noqa: E402  # pylint: disable=wrong-import-position
from agent_toolkit._atk import plans as _plans_module
from agent_toolkit._atk import worktree_stash as _worktree_stash  # noqa: E402  # pylint: disable=wrong-import-position
from agent_toolkit._atk.wi import add as _add  # noqa: E402  # pylint: disable=wrong-import-position
from agent_toolkit._atk.wi import entries as _wi_entries
from agent_toolkit._atk.wi import listing as _wi_listing
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._atk.wi import web_input as _wi_web_input
from agent_toolkit._atk.wi.mutations import targets as _mutation_targets
from agent_toolkit._atk.wi.mutations import transitions as _mutation_transitions
from agent_toolkit._common import codex_models, inherited_venv, mcp_environment
from agent_toolkit._common import wait_schedule as _wait_schedule  # noqa: E402  # pylint: disable=wrong-import-position
from agent_toolkit._hooks import user_prompt_submit  # noqa: E402  # pylint: disable=wrong-import-position
from agent_toolkit._testing import git_repository, wi_entry_files
from agent_toolkit._testing import wi_bodies as _wi_bodies  # noqa: E402  # pylint: disable=wrong-import-position
from agent_toolkit._testing.git_fakes import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    _FIXED_HEAD_COMMIT,
)
from agent_toolkit._testing.git_fakes import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    make_git_remote_fake as _make_git_remote_fake,
)
from agent_toolkit._testing.isolation import host_package_cache_environ
from agent_toolkit._testing.managed_temp_support import setattr_in_managed_temp_modules


def test_persistent_warning_once_per_session(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: pathlib.Path,
) -> None:
    """期限切れの対象を削除できなかった掃引の失敗を、同じ会話で1回だけ公開サブコマンドから警告する。"""
    for key in ("AGENT_TOOLKIT_OWNER_SESSION", "AGENT_TOOLKIT_DELEGATED_SESSION", "CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID"):
        monkeypatch.delenv(key, raising=False)
    failed = tmp_path / "failed-area"
    setattr_in_managed_temp_modules(
        monkeypatch,
        "sweep_managed_temp",
        lambda **_kwargs: _managed_temp.SweepResult([], ((failed, "persistent deletion failure"),)),
    )
    monkeypatch.setattr(_wait_schedule, "get_schedule", lambda _request_bucket: "result")

    def expect_warning(expected: bool) -> None:
        with pytest.raises(SystemExit) as raised:
            atk.main(["wait-schedule"], home=tmp_path)
        assert raised.value.code == 0
        output = capsys.readouterr()
        assert json.loads(output.out)["cron"] == "result"
        assert ("managed-tempの期限切れの対象を自動削除できなかった" in output.err) is expected
        if expected:
            assert "persistent deletion failure" in output.err and str(failed) in output.err

    for key in ("CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID"):
        monkeypatch.setenv(key, key)
        expect_warning(True)
        expect_warning(False)
        monkeypatch.setenv(key, key + "-other")
        expect_warning(True)
        expect_warning(False)
        monkeypatch.delenv(key)
    expect_warning(True)
    expect_warning(True)
    monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
    expect_warning(False)
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION")
    monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", "owner-only")
    expect_warning(False)


_GitCall = dict[str, Any]


_ATK_PATH = pathlib.Path(atk.__file__).resolve()
_PROJECT_ROOT = _ATK_PATH.parents[1]


def test_all_repos_manager_factory_strips_context_and_preserves_launcher_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    """all-reposの各managerが除去前の同じlauncherを持ち、全backendの継承元を整える。"""
    for name in mcp_environment._NON_INHERITABLE_NAMES:  # pylint: disable=protected-access
        monkeypatch.setenv(name.upper(), "parent-session")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("VIRTUAL_ENV", "/launcher/venv")
    monkeypatch.setenv("PATH", os.pathsep.join(("/launcher/venv/bin", "/usr/bin")))
    managers: list[dict[str, str]] = []

    def create_manager(*, launcher_environment: dict[str, str] | None = None, **_kwargs: Any) -> object:
        managers.append(dict(os.environ) if launcher_environment is None else launcher_environment)
        return object()

    monkeypatch.setattr(agents_manager, "AgentsServerManager", create_manager)
    factories: list[Callable[[], object]] = []

    def dispatch(_args: argparse.Namespace, *, repository_manager_factory: Callable[[], object], **_kwargs: Any) -> int:
        factories.append(repository_manager_factory)
        return 0

    monkeypatch.setattr(atk._wi_cli, "dispatch", dispatch)  # pylint: disable=protected-access
    with pytest.raises(SystemExit) as exit_info:
        atk.main(["wi", "list"])
    assert exit_info.value.code == 0
    assert os.environ["CLAUDE_CODE_SESSION_ID"] == "parent-session"
    factories[0]()
    factories[0]()
    assert len(managers) == 2
    assert all(snapshot["CLAUDE_CODE_SESSION_ID"] == "parent-session" for snapshot in managers)
    assert not {name.casefold() for name in os.environ} & mcp_environment._NON_INHERITABLE_NAMES  # pylint: disable=protected-access
    assert "VIRTUAL_ENV" not in os.environ
    assert os.environ["PATH"] == "/usr/bin"
    assert os.environ["ANTHROPIC_AUTH_TOKEN"] == "test-token"


def _install_myrepo_git_fake(monkeypatch: pytest.MonkeyPatch, myrepo: pathlib.Path) -> list[_GitCall]:
    """`myrepo`を`github.com/example/myrepo`の作業ツリーとして答えるGitの偽装へ`subprocess.run`を差し替え、記録する呼び出しの列を返す。"""
    git_calls: list[_GitCall] = []

    def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
        git_calls.append({"cmd": list(cmd), "kwargs": dict(kwargs)})
        if cmd == ["git", "-C", str(myrepo), "rev-parse", "--is-inside-work-tree"]:
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="true\n", stderr="")
        if cmd == ["git", "-C", str(myrepo), "remote", "get-url", "origin"]:
            url = "https://github.com/example/myrepo.git\n"
            stdout: Any = url if kwargs.get("text") else url.encode()
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr="" if kwargs.get("text") else b"")
        if cmd == ["git", "-C", str(myrepo), "rev-parse", "--verify", "HEAD^{commit}"]:
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=f"{_FIXED_HEAD_COMMIT}\n", stderr="")
        empty: Any = "" if kwargs.get("text") else b""
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

    monkeypatch.setattr(subprocess, "run", fake_run)
    return git_calls


def _isolated_cli_environ(host_environ: Callable[[], dict[str, str]], tmp_path: pathlib.Path) -> dict[str, str]:
    """実CLIを起動する子プロセスへ、managed-tempのrootと状態ディレクトリを`tmp_path`配下へ向けた環境変数を返す。

    `host_environ`はmiseのshimを解決できるよう実環境のホームと設定ディレクトリを戻す。そのまま渡すと、
    `atk`の共通起動が実環境のmanaged-tempを読み、別のセッションが残した領域について警告を
    標準エラーへ書き、実環境の領域を自動回収する。uvのキャッシュは実環境の位置を保ち、依存の再取得を避ける。
    """
    environ = host_environ()
    environ.update(host_package_cache_environ())
    home = pathlib.Path(environ.get("HOME", str(pathlib.Path.home())))
    # miseはshimの信頼設定とキャッシュを同じXDG変数から解決するため、実環境の位置を明示して保つ。
    environ.setdefault("MISE_STATE_DIR", str(pathlib.Path(environ.get("XDG_STATE_HOME", home / ".local" / "state")) / "mise"))
    environ.setdefault("MISE_CACHE_DIR", str(pathlib.Path(environ.get("XDG_CACHE_HOME", home / ".cache")) / "mise"))
    environ["XDG_CACHE_HOME"] = str(tmp_path / "cli-cache")
    environ["XDG_STATE_HOME"] = str(tmp_path / "cli-state")
    return environ


def test_entry_restores_launch_environment_for_children(monkeypatch: pytest.MonkeyPatch) -> None:
    """`atk.main`は開始時に、起動層が変えた値を呼出元の値へ戻し、`atk commit`や`$EDITOR`などの子へ渡さない。"""
    monkeypatch.setenv("PYTHONPATH", "/plugin-root")
    monkeypatch.setenv(inherited_venv.LAUNCH_RESTORE_KEY, json.dumps({"PYTHONPATH": [None, "/plugin-root"]}))
    atk.main(["info"])
    assert "PYTHONPATH" not in os.environ
    assert inherited_venv.LAUNCH_RESTORE_KEY not in os.environ


def test_info_reports_current_execution_context(capsys: pytest.CaptureFixture[str]) -> None:
    """公開CLIのinfoが実行文脈とplugin版数の所在を返す。"""
    atk.main(["info"])

    output = capsys.readouterr().out
    assert f"作業ディレクトリ: {pathlib.Path.cwd()}" in output
    assert "atk実装:" not in output
    assert f"plugin root: {_PROJECT_ROOT}" in output
    assert "plugin version (plugin.json):" in output


_FIXED_DT = datetime.datetime(2024, 1, 15, 10, 30, 0)
_FIXED_TIMESTAMP = _FIXED_DT.strftime("%Y%m%d-%H%M%S")
_FIXED_ISO = _FIXED_DT.isoformat()


def _body_file_args(tmp_path: pathlib.Path, *messages: str) -> list[str]:
    """本文群をファイルへ保存し、繰り返し可能な`--body-file`引数列を返す。"""
    args: list[str] = []
    for index, message in enumerate(messages):
        path = tmp_path / f"body-{index}.md"
        path.write_text(message, encoding="utf-8")
        args.extend(("--body-file", str(path)))
    return args


@pytest.fixture(autouse=True)
def _isolate_agent_and_managed_temp_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """listとmanaged-tempが引数を省略した場合の動作をホスト環境から隔離する。"""
    for name in ("AI_AGENT", "CODEX_CI", "CLAUDECODE", "CURSOR_AGENT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-app-data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


@pytest.mark.parametrize(
    "argv",
    [
        ["wi", "list", "--skip-pull"],
        ["wi", "show", "--all", "--skip-pull"],
        ["wi", "grep", ".", "--skip-pull"],
        ["config", "show"],
    ],
)
def test_cli_exits_quietly_when_stdout_pipe_is_closed_early(
    tmp_path: pathlib.Path,
    argv: list[str],
    host_environ: Callable[[], dict[str, str]],
) -> None:
    """公開された出力処理は読取側の早期クローズをTracebackなしのexit 1として処理する。"""
    notes = _setup_notes(tmp_path)
    _write_awi_file(notes, "awi.md", body="searchable")
    with contextlib.ExitStack() as descriptors, contextlib.ExitStack() as reader_owner:
        read_fd, write_fd = os.pipe()
        descriptors.callback(os.close, write_fd)
        reader_owner.callback(os.close, read_fd)
        reader_owner.close()
        env = _isolated_cli_environ(host_environ, tmp_path)
        env["AGENT_TOOLKIT_PRIVATE_NOTES"] = str(notes)
        env.update(_child_config_env())
        # `codex`が起動されると印のファイルを書く偽の実行ファイルをPATHの先頭へ置き、起動の有無を観測する。
        fake_bin = tmp_path / "fake-bin"
        fake_bin.mkdir()
        marker = tmp_path / "codex-started"
        fake_codex = fake_bin / "codex"
        fake_codex.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 1\n", encoding="utf-8")
        fake_codex.chmod(0o755)
        env["PATH"] = os.pathsep.join((str(fake_bin), env.get("PATH", "")))
        # Gitの作業ツリー外で起動し、`--target-repo`を省略した場合も対象を限定しない状態にする。
        with subprocess.Popen(  # noqa: S603
            ["uv", "run", "--project", str(_PROJECT_ROOT), "--locked", "--no-default-groups", str(_ATK_PATH), *argv],
            stdout=write_fd,
            stderr=subprocess.PIPE,
            env=env,
            cwd=tmp_path,
            text=True,
        ) as process:
            stderr = process.communicate()[1]

            assert process.returncode == 1
        assert "Traceback" not in stderr
        assert "Exception ignored on flushing sys.stdout" not in stderr
        assert not marker.exists()


def _child_config_env() -> dict[str, str]:
    """子の`atk`へ渡す全設定キーの環境変数を、設定の初期値から組み立てる。

    `config show`がCodexの系列名を解決するために`codex` CLIを起動しないよう、
    工程別候補のCodex系列名を完全IDで上書きする。
    """
    env: dict[str, str] = {}
    for key in _config._MUTABLE_KEY_DEFAULTS:  # pylint: disable=protected-access  # noqa: SLF001
        value = _config.mutable_setting_default(key)
        if key.endswith("_model"):
            candidates = _config.parse_stage_model_candidates(value)
            value = ",".join(
                f"{engine}:{'gpt-6-sol' if engine == 'codex' and model in codex_models.FAMILIES else model}"
                f"{'@' if engine == 'opencode' else '/'}{effort}"
                for engine, model, effort in candidates
            )
        env[f"AGENT_TOOLKIT_CONFIG_{key.upper()}"] = value
    return env


def test_child_config_env_derives_values_from_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """書式の異なる設定キーを加えても、組み立てた全キーの値が設定の検証に合格する。

    テストが値の書式を自前で持つと、段位モデルとengine順以外の書式のキーを加えるたびに検証で拒否される。
    加えるキーは候補列の検証（engine接頭辞付き）の対象になり、キー名から書式を推測する値（`opus/medium`）は拒否される。
    """
    monkeypatch.setitem(
        _config._MUTABLE_KEY_DEFAULTS,  # pylint: disable=protected-access  # noqa: SLF001
        "future_stage_model",
        "claude:opus/medium",
    )
    env = _child_config_env()
    assert set(env) == {
        f"AGENT_TOOLKIT_CONFIG_{key.upper()}"
        for key in _config._MUTABLE_KEY_DEFAULTS  # pylint: disable=protected-access  # noqa: SLF001
    }
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    for key in _config._MUTABLE_KEY_DEFAULTS:  # pylint: disable=protected-access  # noqa: SLF001
        _config.resolve_mutable_setting(key)
    candidates: list[tuple[str, str, str]] = []
    for name, value in env.items():
        if name.endswith("_MODEL"):
            candidates.extend(_config.parse_stage_model_candidates(value))
    assert not codex_models.needs_catalog(candidates)


def test_cli_local_path_filter_counts_legacy_and_current_uwis(
    tmp_path: pathlib.Path,
    host_environ: Callable[[], dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """実CLIはローカルパス指定時に旧パス形とURL形のUWIをともに数える。"""
    notes = _setup_notes(tmp_path)
    local_repo = tmp_path / "repo"
    git_repository.init_repository(local_repo, origin="git@github.com:example/repo.git")
    _write_uwi_file(notes, "legacy.md", target_repo=str(local_repo), question="旧形式")
    _write_uwi_file(notes, "current.md", target_repo="github.com/example/repo", question="現行形式")
    _write_uwi_file(notes, "other.md", target_repo="github.com/example/other", question="対象外")
    monkeypatch.setenv("UV_FROZEN", "1")
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / "parent-venv"))
    env = _isolated_cli_environ(host_environ, tmp_path)
    env["AGENT_TOOLKIT_PRIVATE_NOTES"] = str(notes)
    # 子projectのlock指定と環境を使い、親のuv設定による警告をCLI出力へ混ぜない。
    env.pop("UV_FROZEN", None)
    env.pop("VIRTUAL_ENV", None)

    result = subprocess.run(  # noqa: S603
        [
            "uv",
            "run",
            "--project",
            str(_PROJECT_ROOT),
            "--locked",
            "--no-default-groups",
            str(_ATK_PATH),
            "wi",
            "list",
            "--count",
            f"--target-repo={local_repo}",
            "--skip-pull",
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout == "2\n"
    assert not result.stderr


class TestWorktreeStashDispatch:
    """`worktree-stash`の公開dispatchを検証する。"""

    def test_worktree_stash_dispatch_receives_private_notes(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """worktree-stash分岐は解決したprivate-notesをdispatchへ渡す。"""
        received: dict[str, object] = {}

        def fake_dispatch(
            args: object,
            *,
            command_dest: str,
            private_notes: pathlib.Path,
        ) -> int:
            received.update(args=args, command_dest=command_dest, private_notes=private_notes)
            return 0

        monkeypatch.setattr(_worktree_stash, "dispatch", fake_dispatch)
        private_notes = tmp_path / "private-notes"
        monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(private_notes))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["worktree-stash", "save", "--label=test"], home=tmp_path)

        assert exc_info.value.code == 0
        assert received["command_dest"] == "worktree_stash_subcommand"
        assert received["private_notes"] == private_notes


class TestAgentsWaitParser:
    """`agents wait`の公開parserを検証する。"""

    def test_accepts_no_arguments(self) -> None:
        """待機対象と待機上限の入力を持たない。"""
        parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
        args = parser.parse_args(["agents", "wait"])
        assert args.command == "agents"
        assert args.agents_subcommand == "wait"
        assert not hasattr(args, "timeout")
        assert not hasattr(args, "session_id")

    @pytest.mark.parametrize(
        "argv",
        [
            ["agents", "wait", "session-1"],
            ["agents", "wait", "session-1", "--timeout=0"],
            ["agents", "wait", "--timeout=12"],
        ],
    )
    def test_rejects_wait_target_arguments(self, argv: list[str]) -> None:
        """撤去した待機対象の指定を拒否する。"""
        parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(argv)
        assert exc_info.value.code == 2

    def test_publishes_only_one_wait_subcommand(self) -> None:
        """待機を`agents wait`だけで公開する。"""
        parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
        command_action = next(
            action
            for action in parser._actions  # pylint: disable=protected-access  # noqa: SLF001
            if action.dest == "command"
        )
        assert command_action.choices is not None
        command_choices = dict(command_action.choices)
        assert "agents-wait" not in command_choices
        agents_parser = command_choices["agents"]
        agents_action = next(
            action
            for action in agents_parser._actions  # pylint: disable=protected-access  # noqa: SLF001
            if action.dest == "agents_subcommand"
        )
        assert agents_action.choices is not None
        assert "wait" in agents_action.choices


class TestAgentsExitSessionParser:
    """`agents-exit-session`は外部から識別子を受け取らない。"""

    def test_accepts_no_arguments(self) -> None:
        parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
        args = parser.parse_args(["agents-exit-session"])
        assert args.command == "agents-exit-session"

    def test_rejects_target_argument(self) -> None:
        parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["agents-exit-session", "123"])
        assert exc_info.value.code == 2


class TestWaitScheduleParser:
    """`wait-schedule`の公開parserとdispatchを検証する。"""

    def test_accepts_no_arguments(self) -> None:
        parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
        assert parser.parse_args(["wait-schedule"]).command == "wait-schedule"

    @pytest.mark.parametrize(
        "argv",
        [
            ["wait-schedule", "--request-bucket=main"],
            ["wait-schedule", "--format", "json"],
            ["wait-schedule", "extra"],
        ],
    )
    def test_rejects_removed_or_extra_arguments(self, argv: list[str]) -> None:
        """廃止した`--request-bucket`・`--format`と余分な引数を拒否する。"""
        parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(argv)
        assert exc_info.value.code == 2

    def test_outputs_main_schedule_and_prompt_as_json(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """メインのcron式と、1行目がUserPromptSubmitの機械注入の標識であるpromptを1行のJSONで出力する。"""
        buckets: list[str] = []

        def fake_get_schedule(request_bucket: str) -> str:
            buckets.append(request_bucket)
            return "*/3 * * * *"

        monkeypatch.setattr(_wait_schedule, "get_schedule", fake_get_schedule)
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wait-schedule"], home=tmp_path)
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert out.count("\n") == 1
        payload = json.loads(out)
        assert buckets == ["main"]
        assert payload["cron"] == "*/3 * * * *"
        assert payload["prompt"].split("\n", 1)[0] == user_prompt_submit.PERIODIC_RECHECK_MARKER

    def test_sweeps_managed_temp_before_dispatch(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """任意のサブコマンドに共通する実行処理でmanaged-tempを整理する。"""
        calls: list[datetime.datetime] = []

        def fake_sweep(*, now: datetime.datetime) -> _managed_temp.SweepResult:
            calls.append(now)
            return _managed_temp.SweepResult([], ())

        setattr_in_managed_temp_modules(monkeypatch, "sweep_managed_temp", fake_sweep)
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wait-schedule"], home=tmp_path, now=_FIXED_DT)
        assert exc_info.value.code == 0
        assert calls == [_FIXED_DT]

    @pytest.mark.parametrize(
        ("delegated_session", "expects_warning"),
        [(None, True), ("1", False), ("other", True)],
    )
    def test_sweep_failure_warning_depends_on_delegated_session(
        self,
        delegated_session: str | None,
        expects_warning: bool,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """削除できなかった対象の警告は、回収を判断できない委譲先（値が1）だけで抑止する。"""
        monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
        monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
        monkeypatch.setattr(_wait_schedule, "get_schedule", lambda _request_bucket: "*/3 * * * *")
        setattr_in_managed_temp_modules(
            monkeypatch,
            "sweep_managed_temp",
            lambda *, now: _managed_temp.SweepResult([], ((tmp_path / "orphan", "削除失敗"),)),
        )
        if delegated_session is None:
            monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
        else:
            monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", delegated_session)
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wait-schedule"], home=tmp_path, now=_FIXED_DT)
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out)["cron"] == "*/3 * * * *"
        assert ("自動削除できなかった" in captured.err) is expects_warning

    def test_does_not_expose_environment_values(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """環境変数値を公開出力へ混在させない。"""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "environment-secret")
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wait-schedule"], home=tmp_path)
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out)["cron"] == "*/3 * * * *"
        assert not captured.err
        assert "environment-secret" not in captured.out + captured.err

    def test_does_not_expose_auth_output(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """認証応答を公開出力へ混在させない。"""

        def fake_run(cmd: list[str], *args: object, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
            del args
            del kwargs
            return subprocess.CompletedProcess(
                cmd,
                returncode=0,
                stdout=json.dumps(
                    {
                        "loggedIn": True,
                        "authMethod": "claude.ai",
                        "subscriptionType": "max",
                        "token": "auth-secret",
                    }
                ),
                stderr="auth-error-secret",
            )

        monkeypatch.setattr(_wait_schedule.subprocess, "run", fake_run)
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wait-schedule"], home=tmp_path)
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out)["cron"] == "*/30 * * * *"
        assert not captured.err
        assert "auth-secret" not in captured.out + captured.err
        assert "auth-error-secret" not in captured.out + captured.err


# 端末幅の固定化は`_testing.pytest_plugin`の`_fixed_terminal_size`autouseフィクスチャへ集約する
# （`shutil`モジュール差し替えのため個別テストファイルへの重複定義は不要）。


def _make_subprocess_fake(
    calls: list[_GitCall],
    *,
    has_upstream: bool = True,
) -> Callable[..., subprocess.CompletedProcess[Any]]:
    """subprocess.runのfakeを返す。呼び出し引数をcallsへ記録する。

    `text=True`が指定された場合は`stdout`/`stderr`を空文字列で返し、それ以外は空バイト列で返す。
    """

    def fake(cmd: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
        del args
        calls.append({"cmd": list(cmd), "kwargs": dict(kwargs)})
        empty: Any = "" if kwargs.get("text") else b""
        no_upstream = not has_upstream and cmd[-4:] == ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"]
        return subprocess.CompletedProcess(cmd, returncode=128 if no_upstream else 0, stdout=empty, stderr=empty)

    return fake


def _setup_notes(tmp_path: pathlib.Path, *, legacy_layout: bool = False) -> pathlib.Path:
    """private-notesディレクトリを準備する。"""
    notes = tmp_path / "private-notes"
    notes.mkdir()
    if legacy_layout:
        (notes / "inbox").mkdir()
        (notes / "hold").mkdir()
    return notes


def _write_awi_file(
    notes: pathlib.Path,
    filename: str,
    target_repo: str = "github.com/example/foo",
    body: str = "テスト本文",
    source: str | None = None,
    legacy_layout: bool = False,
) -> pathlib.Path:
    """状態付き項目を書き込み、移行検証の明示指定時は旧配置を使う。"""
    path = wi_entry_files.entry_path(notes, filename, legacy_layout=legacy_layout)
    source_line = f"source: {source}\n" if source is not None else ""
    state_line = "" if legacy_layout else "state: inbox\n"
    path.write_text(
        f"---\n{state_line}target_repo: {target_repo}\ntype: awi\n{source_line}---\n\n{body}\n",
        encoding="utf-8",
    )
    return path


def _setup_notes_with_pending_commit(tmp_path: pathlib.Path, *, pending: bool = True) -> pathlib.Path:
    """upstreamより1件先行したprivate-notesのテスト用cloneを作成する。`pending=False`ではupstreamと一致させる。"""
    origin = tmp_path / "origin.git"
    git_repository.init_bare_repository(origin)
    notes = _setup_notes(tmp_path)
    git_repository.init_repository(
        notes,
        initial_branch="main",
        origin=str(origin),
        files={".gitignore": "plans/.agent-toolkit-plan-create.lock\n"},
        commit_message="base",
    )
    git_repository.run_git(notes, "push", "-u", "origin", "main")
    if not pending:
        return notes
    (notes / "pending.txt").write_text("pending\n", encoding="utf-8")
    git_repository.commit_all(notes, "pending")
    return notes


_SYNC_MUTATION_ENTRIES = ("wi start-processing", "wi add --dry-run", "plans commit")
"""終了時に未pushを判定する同期対象操作のコマンド。`wi add --dry-run`は自身でcommitしない操作として含める。"""

_DRY_RUN_SUCCESS = "成功: 投入前の検証が成立した（--dry-runのため保存していない）"


def _prepare_sync_mutation(entry: str, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """同期対象操作のコマンドごとに外部への作用を差し替え、`atk.main`へ渡す引数を返す。"""
    if entry == "wi start-processing":
        monkeypatch.setattr(_mutation_transitions, "cmd_start_processing", lambda *_args: None)
        return ["wi", "start-processing", "awi.md"]
    if entry == "wi add --dry-run":
        monkeypatch.setattr(_add, "resolve_add_target", lambda _value: ("github.com/example/repo", None))
        body = tmp_path / "body.md"
        body.write_text("本文\n", encoding="utf-8")
        return ["wi", "add", "--dry-run", "--target-repo", "github.com/example/repo", "--body-file", str(body)]
    monkeypatch.setattr(_plans_module, "dispatch", lambda *_args: 0)
    return ["plans", "commit", "01-0900_example.md"]


@pytest.mark.parametrize("entry", _SYNC_MUTATION_ENTRIES)
def test_main_reports_pending_commit_only_for_sync_mutations(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    entry: str,
) -> None:
    """同期対象操作だけが、処理の終了後に残る未pushを終了コード3で通知する。"""
    notes = _setup_notes_with_pending_commit(tmp_path)
    monkeypatch.setattr(_wi_sync, "ensure_environment", lambda _home: notes)
    monkeypatch.setattr(_wi_listing, "cmd_list", lambda *_args: None)
    argv = _prepare_sync_mutation(entry, tmp_path, monkeypatch)

    with pytest.raises(SystemExit) as exc_info:
        atk.main(argv, home=tmp_path)
    assert exc_info.value.code == 3
    output = capsys.readouterr()
    if entry == "wi add --dry-run":
        assert _DRY_RUN_SUCCESS in output.out
    assert "警告: private-notesに未pushのcommitが1件残る" in output.err
    # pushの手順は警告行に続く次の操作の行へ置く。
    next_action = next(line for line in output.err.splitlines() if line.startswith("次の操作: "))
    assert f"`git -C {notes.resolve()} status`" in next_action
    assert "atk wi commit" in next_action

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", "list", "--skip-pull"], home=tmp_path)
    assert exc_info.value.code == 0
    assert "未pushのcommit" not in capsys.readouterr().err


_CONCURRENT_PUSH_SCRIPT = """
import pathlib
import subprocess
import sys
import time

from agent_toolkit._atk import git_sync

notes = pathlib.Path(sys.argv[1])
ready = pathlib.Path(sys.argv[2])
with git_sync.repo_lock(notes):
    (notes / "concurrent.txt").write_text("concurrent\\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(notes), "add", "concurrent.txt"], check=True)
    subprocess.run(["git", "-C", str(notes), "commit", "-q", "-m", "concurrent"], check=True)
    ready.write_text("committed", encoding="utf-8")
    time.sleep(2)
    subprocess.run(["git", "-C", str(notes), "push", "-q"], check=True)
"""


@pytest.mark.parametrize("entry", _SYNC_MUTATION_ENTRIES)
def test_main_does_not_report_commit_pushed_by_concurrent_process(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    entry: str,
) -> None:
    """別プロセスがロック内でcommitしてからpushする間に終えた同期対象操作は、未pushを通知しない。

    commit後・push前の一時状態を読むと、同期が完了する途中の状態を同期未達として終了コード3で報告する。
    """
    notes = _setup_notes_with_pending_commit(tmp_path, pending=False)
    monkeypatch.setattr(_wi_sync, "ensure_environment", lambda _home: notes)
    argv = _prepare_sync_mutation(entry, tmp_path, monkeypatch)
    script = tmp_path / "concurrent_push.py"
    script.write_text(_CONCURRENT_PUSH_SCRIPT, encoding="utf-8")
    ready = tmp_path / "ready"
    environ = os.environ.copy()
    environ["PYTHONPATH"] = os.pathsep.join(filter(None, [str(_PROJECT_ROOT), environ.get("PYTHONPATH")]))
    with subprocess.Popen([sys.executable, str(script), str(notes), str(ready)], env=environ) as writer:
        deadline = time.monotonic() + 30
        while not ready.exists():
            assert writer.poll() is None, "別プロセスがcommit前に終了した"
            assert time.monotonic() < deadline, "別プロセスのcommitを観測できない"
            time.sleep(0.05)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(argv, home=tmp_path)
        assert writer.wait(timeout=30) == 0

    assert exc_info.value.code == 0
    output = capsys.readouterr()
    if entry == "wi add --dry-run":
        assert _DRY_RUN_SUCCESS in output.out
    assert "未pushのcommit" not in output.err


class TestMutationTargetRepoParserOption:
    """mutation系サブコマンドの`--target-repo`受理をargparseレベルで検証する。"""

    @pytest.mark.parametrize(
        ("top_command", "subcommand", "argv_tail"),
        [
            ("wi", "adopt", ["20260714-000001-001.md"]),
            ("wi", "reject", ["20260714-000001-001.md"]),
            ("wi", "rm", ["20260714-000001-001.md"]),
            ("wi", "edit", ["20260714-000001-001.md"]),
            ("wi", "start-processing", ["20260714-000001-001.md"]),
            ("wi", "return-to-inbox", ["20260714-000001-001.md"]),
        ],
    )
    def test_accepts_target_repo(self, top_command: str, subcommand: str, argv_tail: list[str]) -> None:
        """6種のmutation系サブコマンドすべてが`--target-repo`を受理する。"""
        parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
        args = parser.parse_args([top_command, subcommand, "--target-repo", "github.com/foo/bar", *argv_tail])
        expected = "github.com/foo/bar" if subcommand == "edit" else ["github.com/foo/bar"]
        assert args.target_repo == expected

    def test_edit_rejects_message(self) -> None:
        """`edit FILENAME MESSAGE`の旧受理形式を拒否する。"""
        parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["wi", "edit", "20260714-000001-001.md", "更新本文"])
        assert exc_info.value.code == 2

    def test_edit_accepts_append_option(self) -> None:
        """`edit --append FILENAME --body-file PATH`を追記モードとして解析する。"""
        parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
        args = parser.parse_args(["wi", "edit", "--append", "20260714-000001-001.md", "--body-file", "body.md"])
        assert args.append is True
        assert args.filename == "20260714-000001-001.md"
        assert args.body_file == "body.md"

    def test_edit_accepts_body_file(self) -> None:
        """`edit --body-file`を単一の本文ファイルとして解析する。"""
        parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
        args = parser.parse_args(["wi", "edit", "entry.md", "--body-file", "body.md"])
        assert args.body_file == "body.md"

    @pytest.mark.parametrize("value", ["2", "3.5", "three"])
    def test_return_to_inbox_rejects_invalid_cooldown_days(self, value: str) -> None:
        """再処理抑制日数は3以上の整数だけを受理する。"""
        parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
        with pytest.raises(SystemExit):
            parser.parse_args(["wi", "return-to-inbox", "entry.md", f"--cooldown-days={value}"])

    def test_return_to_inbox_accepts_minimum_cooldown_days(self) -> None:
        """再処理抑制日数の下限3を受理する。"""
        parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
        args = parser.parse_args(["wi", "return-to-inbox", "entry.md", "--cooldown-days=3"])
        assert args.cooldown_days == 3

    def test_return_to_inbox_uses_main_injected_time(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """mainへ注入した基準時刻から再処理抑制期限を保存する。"""
        notes = _setup_notes(tmp_path)
        entry = wi_entry_files.write_awi(notes, "entry.md", state="processing", target_repo="github.com/example/foo")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        now = datetime.datetime(2026, 8, 12, 3, 4, 5, tzinfo=datetime.UTC)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["wi", "return-to-inbox", "entry.md", "--cooldown-days=3"],
                home=tmp_path,
                now=now,
            )

        assert exc_info.value.code == 0
        content = entry.read_text(encoding="utf-8")
        assert _wi_entries.entry_state_of(entry, content) == "inbox"
        assert "cooldown_until: '2026-08-15T03:04:05+00:00'" in content

    def test_edit_without_message_remains_interactive(self) -> None:
        """`edit FILENAME`では本文ファイルを未指定として扱う。"""
        parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
        args = parser.parse_args(["wi", "edit", "20260714-000001-001.md"])
        assert args.filename == "20260714-000001-001.md"
        assert args.body_file is None

    def test_commit_has_no_target_repo_option(self) -> None:
        """`commit`は引数を取らないシグネチャのため`--target-repo`を受理しない。"""
        parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["wi", "commit", "--target-repo", "github.com/foo/bar"])
        assert exc_info.value.code == 2


def test_repeatable_wi_filters_preserve_all_values() -> None:
    """一覧系フィルターは同じオプションの全指定値を構文解析結果へ保持する。"""
    parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
    args = parser.parse_args(
        [
            "wi",
            "list",
            "--target-repo=github.com/example/foo",
            "--target-repo=github.com/example/bar",
            "--type=awi",
            "--type=uwi",
            "--status=inbox",
            "--status=hold",
            "--answered=yes",
            "--answered=no",
            "--source=human",
            "--source=!agent",
        ]
    )

    assert args.target_repo == ["github.com/example/foo", "github.com/example/bar"]
    assert args.type == ["awi", "uwi"]
    assert args.status == ["inbox", "hold"]
    assert args.answered == ["yes", "no"]
    assert args.source == ["human", "!agent"]


@pytest.mark.parametrize("subcommand", ("adopt", "reject", "rm"))
def test_note_and_note_file_are_mutually_exclusive(subcommand: str) -> None:
    """3つの状態変更コマンドは文字列メモとファイルメモの併用を構文解析時に拒否する。"""
    parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["wi", subcommand, "entry.md", "--note=本文", "--note-file=/tmp/note.txt"])

    assert exc_info.value.code == 2


def test_note_file_preserves_utf8_text(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ファイルメモは改行とシェル制御文字を変更せず既存の採用処理へ渡す。"""
    notes = _setup_notes(tmp_path)
    entry = _write_awi_file(notes, "entry.md")
    note_file = tmp_path / "note.txt"
    note = '1行目\n"引用"と`backtick`と$記号\n'
    note_file.write_text(note, encoding="utf-8")
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", "adopt", "entry.md", "--note-file", str(note_file)], home=tmp_path)

    assert exc_info.value.code == 0
    assert f"- メモ: {note}" in entry.read_text(encoding="utf-8")
    assert _wi_entries.entry_state_of(entry) == "adopted"


@pytest.mark.parametrize("invalid_bytes", (None, b"\xff"))
def test_note_file_read_error_precedes_environment_changes(
    tmp_path: pathlib.Path,
    invalid_bytes: bytes | None,
) -> None:
    """存在しないまたはUTF-8でないメモはprivate-notesの準備前に拒否する。"""
    note_file = tmp_path / "note.txt"
    if invalid_bytes is not None:
        note_file.write_bytes(invalid_bytes)

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", "reject", "entry.md", "--note-file", str(note_file)], home=tmp_path)

    assert exc_info.value.code == 2
    assert not (tmp_path / "private-notes").exists()


@pytest.mark.parametrize("subcommand", ("adopt", "reject", "rm"))
def test_note_file_rejects_relative_path_before_environment_changes(
    tmp_path: pathlib.Path,
    subcommand: str,
) -> None:
    """相対メモパスはprivate-notesの準備前に3コマンドで拒否する。"""
    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", subcommand, "entry.md", "--note-file=note.txt"], home=tmp_path)

    assert exc_info.value.code == 2
    assert not (tmp_path / "private-notes").exists()


@pytest.mark.parametrize(
    "argv",
    [
        ["wi", "convert-to-plan", "awi.md", "--plan-file", "/tmp/plan.md"],
        ["plans", "progress", "2026/09/09-example-1a2b.md"],
        ["plans", "migrate"],
    ],
)
def test_removed_plan_mutation_commands_are_rejected(argv: list[str]) -> None:
    """廃止された計画変換・更新コマンドを受理しない。"""
    parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(argv)
    assert exc_info.value.code == 2


_REMOVED_RUN_SCRIPTS = (
    "verification-record",
    "exec-review-evidence-check",
    "plan-verify",
    "review-contract",
    "review-impact",
    "reader-fit-prepare",
    "recurrence-items",
    "lane-receipt-check",
    "wi-draft-prepare",
)


@pytest.mark.parametrize(
    "argv",
    [
        ["managed-temp", "list"],
        ["managed-temp", "create", "--prefix", "example"],
        ["managed-temp", "cleanup", "--path", "/absolute/example"],
        ["read-file", "--", "/absolute/example"],
        ["run-command", "--", "true"],
        ["watch", "--file", "/absolute/example"],
        ["plans", "rewrite-references"],
        ["worktree-stash", "protect", "refs/worktree/example"],
        ["wait-schedule", "--request-bucket", "main"],
        *(["run-script", name, "--"] for name in _REMOVED_RUN_SCRIPTS),
    ],
)
def test_removed_public_paths_are_rejected(argv: list[str]) -> None:
    """標準手段へ置き換えて削除した公開経路を、引数の誤りとして受理しない。"""
    parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(argv)
    assert exc_info.value.code == 2


def test_retained_run_scripts_are_exactly_the_kept_registrations() -> None:
    """残す19登録だけを公開し、撤去した9登録を含めない。"""
    from agent_toolkit._atk import run_script  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

    assert sorted(run_script.SCRIPT_PATHS) == sorted(
        (
            "wait-ci",
            "cost-metrics",
            "delegation-prompt",
            "history-compare",
            "agent-doc-changes",
            "plan-create",
            "plan-check",
            "plan-progress",
            "plan-commits",
            "plan-rewrite",
            "pick-wi-check",
            "termination-evidence",
            "multi-turn-trials",
            "record-stall-detection",
            "wait-compaction-context",
            "session-review-evidence",
            "session-review-prepare",
            "writing-check-dash",
            "wi-quote-check",
        )
    )
    assert not set(_REMOVED_RUN_SCRIPTS) & set(run_script.SCRIPT_PATHS)


def test_removed_mq_alias_is_rejected() -> None:
    """廃止済みの`mq`は公開CLIの未知コマンドとして終了する。"""
    with pytest.raises(SystemExit) as raised:
        atk.main(["mq", "--help"])
    assert raised.value.code == 2


def test_set_dependencies_parser_accepts_repeated_dependencies() -> None:
    """`set-dependencies`が既存AWIと複数の依存先を受理する。"""
    parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
    args = parser.parse_args(["wi", "set-dependencies", "awi.md", "--depends-on", "first", "--depends-on", "second.md"])
    assert args.filename == "awi.md"
    assert args.depends_on == ["first", "second.md"]


class TestUwiAddSourceOptionParser:
    """UWI投入時の`--source`受理をargparseレベルで検証する。"""

    def test_accepts_source(self) -> None:
        """`mq add --type=uwi`が`--source`を受理しargs.sourceへ格納される。"""
        parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
        args = parser.parse_args(["wi", "add", "--type=uwi", "--source", "session-hold"])
        assert args.source == "session-hold"


class TestServeParser:
    """serveトップレベルサブコマンドの引数境界を検証する。"""

    def test_defaults_and_explicit_values(self) -> None:
        """host/portの省略値と明示値を保持する。"""
        parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
        defaults = parser.parse_args(["serve"])
        explicit = parser.parse_args(["serve", "--host", "0.0.0.0", "--port", "65535"])
        assert (defaults.host, defaults.port) == (None, None)
        assert (explicit.host, explicit.port) == ("0.0.0.0", 65535)

    @pytest.mark.parametrize("port", ["0", "65536"])
    def test_rejects_out_of_range_port(self, port: str) -> None:
        """port範囲外をargparseエラーにする。"""
        parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
        with pytest.raises(SystemExit) as error:
            parser.parse_args(["serve", "--port", port])
        assert error.value.code == 2

    def test_dispatches_resolved_arguments(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """mainがhost・port・homeをserve起動層へ渡して正常終了する。"""
        calls: list[dict[str, object]] = []

        def run(*, host: str | None, port: int | None, home: pathlib.Path) -> None:
            calls.append({"host": host, "port": port, "home": home})

        serve = types.ModuleType("agent_toolkit._atk.serve.cli")
        serve.__dict__["run"] = run
        monkeypatch.setitem(sys.modules, "agent_toolkit._atk.serve.cli", serve)
        with pytest.raises(SystemExit) as error:
            atk.main(["serve", "--host", "127.0.0.2", "--port", "28766"], home=tmp_path)
        assert error.value.code == 0
        assert calls == [{"host": "127.0.0.2", "port": 28766, "home": tmp_path}]

    def test_non_serve_import_does_not_load_serve_dependencies(self) -> None:
        """fresh processでatkを読み込んでもserve実装を解決しない。"""
        script_dir = pathlib.Path(atk.__file__).resolve().parent
        code = (
            "import sys; "
            f"sys.path.insert(0, {str(script_dir)!r}); "
            "import atk; "
            "raise SystemExit(1 if '_atk.serve.cli' in sys.modules else 0)"
        )

        result = subprocess.run([sys.executable, "-c", code], check=False)

        assert result.returncode == 0


class TestAddTargetRepoOptionParser:
    """`mq add`の`--target-repo`受理をargparseレベルで検証する。"""

    @pytest.mark.parametrize("type_option", [[], ["--type=uwi"]])
    def test_add_accepts_target_repo(self, type_option: list[str]) -> None:
        """`mq add`が種別にかかわらず`--target-repo`を受理する。"""
        parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
        args = parser.parse_args(["wi", "add", *type_option, "--target-repo", "github.com/foo/bar"])
        assert args.target_repo == "github.com/foo/bar"


def test_process_loop_abort_commands_report_and_transition_state(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """中断要求の設定・解除・参照コマンドが状態と標準出力を一致させる。"""
    state_file = tmp_path / "state" / "agent-toolkit" / "process-wi-abort"

    for command, expected in (
        ("status", "process-loopへの中断要求: なし\n保持中の追加指示: 0件\n"),
        ("abort-cancel", "成功: process-loopへの中断要求は設定されていないため、解除の変更は無い\n"),
        ("abort", "成功: process-loopへ中断を要求した\n"),
        ("status", "process-loopへの中断要求: あり\n保持中の追加指示: 0件\n"),
        ("abort-cancel", "成功: process-loopへの中断要求を解除した\n"),
        ("status", "process-loopへの中断要求: なし\n保持中の追加指示: 0件\n"),
    ):
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "process-loop", command], home=tmp_path)
        assert exc_info.value.code == 0
        assert capsys.readouterr().out == expected

    assert not state_file.exists()
    assert not (tmp_path / "private-notes").exists()


def test_process_loop_instruction_commands_complete_storage_cycle(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """公開CLIで次回指示を保持し、状態を確認して破棄する。"""
    for argv, expected in (
        (["instruct", "次の起動で確認する"], "成功: 次のセッションへ渡す追加指示を保持した。"),
        (["status"], "保持中の追加指示: 1件"),
        (["instruct-cancel"], "成功: 保持中の追加指示を1件破棄した"),
        (["status"], "保持中の追加指示: 0件"),
    ):
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "process-loop", *argv], home=tmp_path)
        assert exc_info.value.code == 0
        assert expected in capsys.readouterr().out


def test_add_output_reloads_saved_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """add完了表示が保存済みfrontmatterと比較する対象を列挙する。"""
    _setup_notes(tmp_path)
    myrepo = tmp_path / "myrepo"
    myrepo.mkdir()
    body_path = tmp_path / "body.md"
    body_path.write_text("本文", encoding="utf-8")
    monkeypatch.setattr(subprocess, "run", _make_git_remote_fake(myrepo))

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", "add", str(myrepo), "--body-file", str(body_path)], home=tmp_path, now=_FIXED_DT)

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "target_repo: github.com/example/myrepo" in output
    assert f"target_commit: {_FIXED_HEAD_COMMIT}" in output
    assert "plan_file" not in output
    assert "depends_on: なし" in output


def test_review_table_subcommands_are_public() -> None:
    """レビュー指摘管理表の5操作がトップレベルCLIへ登録されている。"""
    parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
    for subcommand in ("init", "add", "respond", "show", "validate"):
        argv = ["review-table", subcommand, "review.tsv"]
        if subcommand == "add":
            argv.extend(
                [
                    "--round=1",
                    "--track=exec-review",
                    "--level=詳細",
                    "--location-file=location.txt",
                    "--issue-file=issue.txt",
                ]
            )
        elif subcommand == "respond":
            argv.extend(
                [
                    "--track=exec-review",
                    "--location-file=location.txt",
                    "--issue-file=issue.txt",
                    "--response-file=response.txt",
                ]
            )
        args = parser.parse_args(argv)
        assert args.command == "review-table"
        assert args.review_table_subcommand == subcommand


def test_public_review_table_validate_rejects_unanswered_rows(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """公開CLIは構造検証の明示指定を許容し、指定しない場合は未応答行を拒否する。"""
    path = tmp_path / "review.tsv"
    path.write_text(
        "\t".join(
            json.dumps(value, ensure_ascii=False)
            for value in ("1", "implementation-review", "位置", "指摘", "詳細", "", "", "")
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(SystemExit) as structural_exc_info:
        atk.main(["review-table", "validate", "--allow-unanswered", str(path)])
    assert structural_exc_info.value.code == 0
    assert "成功: 構造を検証した" in capsys.readouterr().out

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["review-table", "validate", str(path)])

    assert exc_info.value.code == 1
    assert "未応答" in capsys.readouterr().err


def test_public_review_table_show_accepts_compat_track(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """公開CLIのshowは保存済み旧trackを受理してraw TSVを表示する。"""
    path = tmp_path / "review.tsv"
    raw = (
        "\t".join(
            json.dumps(value, ensure_ascii=False)
            for value in ("1", "implementation-review", "位置", "指摘", "詳細", "", "", "")
        )
        + "\n"
    )
    path.write_text(raw, encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["review-table", "show", str(path), "--track=implementation-review"])

    assert exc_info.value.code == 0
    assert capsys.readouterr().out == f"1\t{raw}"


def test_public_review_table_validate_rejects_whitespace_around_stored_track(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """公開CLIは表示時に選択不能となる空白付きtrackを構造検証で拒否する。"""
    path = tmp_path / "review.tsv"
    path.write_text(
        "\t".join(
            json.dumps(value, ensure_ascii=False) for value in ("1", " independent ", "位置", "指摘", "詳細", "yes", "修正", "")
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["review-table", "validate", "--allow-unanswered", str(path)])

    assert exc_info.value.code == 1
    assert "trackが正規値ではない" in capsys.readouterr().err


def test_public_review_table_add_requires_track_and_shows_choices(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """公開CLIは追加時のtrack省略をusageと正規値集合付きで拒否する。"""
    parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["review-table", "add", "review.tsv", "--round=1", "--level=詳細", "位置", "指摘"])
    assert exc_info.value.code == 2
    error = capsys.readouterr().err
    assert "--track" in error
    assert "exec-review" in error
    assert "plan-review" not in error
    assert "implementation-review" not in error
    assert "plan-conformance" not in error
    assert "independent" not in error


def _assert_review_table_recovery_error(error: str) -> None:
    """失敗行に続く次の操作の行が、列位置とtrack値の修復案内を含むことを確かめる。"""
    error_lines = error.splitlines()
    assert error_lines[0].startswith("失敗: ")
    next_action = error_lines[1]
    assert next_action.startswith("次の操作: ")
    assert "期待列数は7" in next_action
    assert "trackの位置はroundの直後" in next_action
    assert "levelの位置はissueの直後" in next_action
    assert "plan-review, exec-review, plan-conformance, independent" in next_action
    assert "implementation-reviewはexec-reviewとして読み取る" in next_action


def test_public_review_table_invalid_column_count_error_explains_recovery(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """公開CLIが旧列数を検出したとき、列位置とtrack値を修復案内へ含める。"""
    path = tmp_path / "review.tsv"
    path.write_text(
        "\t".join(
            json.dumps(value, ensure_ascii=False)
            for value in ("1", "plan-conformance", "旧重大度", "位置", "指摘", "詳細", "", "", "")
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as exc_info:
        atk.main(["review-table", "validate", "--allow-unanswered", str(path)])
    assert exc_info.value.code == 1
    _assert_review_table_recovery_error(capsys.readouterr().err)


@pytest.mark.parametrize("subcommand", ["respond", "add"])
def test_public_review_table_mutations_reject_old_column_count_with_recovery(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    subcommand: str,
) -> None:
    """公開CLIの応答と追加も旧列数を拒否し、移行に必要な情報を示す。"""
    path = tmp_path / "review.tsv"
    path.write_text(
        "\t".join(
            json.dumps(value, ensure_ascii=False)
            for value in ("1", "plan-conformance", "旧重大度", "位置", "指摘", "詳細", "", "", "")
        )
        + "\n",
        encoding="utf-8",
    )
    location_file = tmp_path / "location.txt"
    issue_file = tmp_path / "issue.md"
    response_file = tmp_path / "response.md"
    location_file.write_text("位置", encoding="utf-8")
    issue_file.write_text("指摘", encoding="utf-8")
    response_file.write_text("修正", encoding="utf-8")
    argv = ["review-table", subcommand, str(path)]
    if subcommand == "respond":
        argv.extend(
            [
                "--track=exec-review",
                f"--location-file={location_file}",
                f"--issue-file={issue_file}",
                f"--response-file={response_file}",
            ]
        )
    else:
        argv.extend(
            [
                "--round=2",
                "--track=exec-review",
                "--level=詳細",
                f"--location-file={location_file}",
                f"--issue-file={issue_file}",
            ]
        )

    with pytest.raises(SystemExit) as exc_info:
        atk.main(argv)

    assert exc_info.value.code == 1
    _assert_review_table_recovery_error(capsys.readouterr().err)


class TestSpaceSeparatedOptionWithoutWarning:
    """受理に成功した空白区切りの値付きオプションへ形式の警告を出力しない。

    空白区切りの値もargparseが正しく受理するため、警告は呼び出しの結果と後続の操作を変えない。
    推奨形式の案内はヘルプだけが担う。
    """

    @pytest.mark.parametrize("subcommand", ("adopt", "reject"))
    def test_successful_space_separated_call_emits_no_format_warning(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        subcommand: str,
    ) -> None:
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", subcommand, "fb-001.md", "--note", "memo"], home=tmp_path)

        assert exc_info.value.code == 0
        assert "VALUE形式" not in capsys.readouterr().err


@pytest.mark.parametrize("agent_environment", [False, True], ids=["human", "agent"])
def test_wi_commands_report_only_their_results_with_pending_questions(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *,
    agent_environment: bool,
) -> None:
    """読み取りと投入・編集・状態遷移は、未回答項目があっても実行した結果だけを表示する。"""
    notes = _setup_notes(tmp_path)
    _write_uwi_file(notes, "pending-question.md", question="回答待ち")
    _write_awi_file(notes, "entry.md", body=_wi_bodies.AGENT_AWI_BODY, source="test")
    body = tmp_path / "body.md"
    body.write_text(_wi_bodies.AGENT_AWI_BODY, encoding="utf-8")
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    if agent_environment:
        monkeypatch.setenv("CLAUDECODE", "1")
    commands = [
        ["list", "--count", "--skip-pull"],
        ["show", "entry.md", "--skip-pull"],
        ["grep", ".", "--skip-pull"],
        ["add", "--source=test", "--target-repo=github.com/example/repo", "--body-file", str(body)],
        ["edit", "entry.md", "--body-file", str(body)],
        ["hold", "entry.md"],
        ["unhold", "entry.md"],
        ["adopt", "entry.md"],
    ]
    for command in commands:
        with pytest.raises(SystemExit) as result:
            atk.main(["wi", *command], home=tmp_path, now=_FIXED_DT)
        captured = capsys.readouterr()
        assert result.value.code == 0, (command, captured.err)
        assert "# 未回答" + "UWI通知" not in captured.out + captured.err
        assert "pending-question.md" not in captured.err
        assert "[inbox/unanswered]" not in captured.err
    entry = notes / "2026/10/entry.md"
    assert entry.is_file()
    assert _wi_entries.entry_state_of(entry) == "adopted"


class TestInboxAlwaysEnabled:
    """inbox常時有効化: フラグファイル不在でもprivate-notesさえ揃えば通常どおり動作すること。"""

    @pytest.mark.parametrize("subcommand", ["enable", "disable", "status"])
    def test_removed_control_subcommands_exit_with_usage_error(self, subcommand: str) -> None:
        """削除済みのinbox制御サブコマンドはexit 2で拒否される。"""
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", subcommand])

        assert exc_info.value.code == 2

    def test_add_succeeds_without_flag_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """フラグファイルを作成しなくても、private-notesが存在すれば`mq add`が成功する。"""
        notes = tmp_path / "private-notes"
        notes.mkdir()
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        body_path = tmp_path / "body.md"
        body_path.write_text("dummy message", encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_git_remote_fake(myrepo))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "add", str(myrepo), "--body-file", str(body_path)], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0
        assert "成功: 1件をinboxへ投入した" in capsys.readouterr().out


class TestPrivateNotesMissing:
    """`AGENT_TOOLKIT_PRIVATE_NOTES`で明示指定したパスが不在の場合にexit 1とディレクトリ不在案内を返すこと。

    conftestが適用する隔離（`agent_toolkit._testing.isolation`）が全テストへ`AGENT_TOOLKIT_PRIVATE_NOTES=tmp_path/private-notes`を
    設定するため、このディレクトリを作成しない限り「明示指定パスが不在」の分岐（自動生成の対象外）を検証できる。
    """

    def test_exits_with_directory_missing_guide(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """管理repo rootが存在しない場合はexit 1でディレクトリ不在案内を出力する。"""
        body_path = tmp_path / "body.md"
        body_path.write_text("dummy message", encoding="utf-8")
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "add", "--body-file", str(body_path)], home=tmp_path)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "WI保存ディレクトリが見つからない" in captured.err
        assert "\n次の操作: " in captured.err
        assert "AGENT_TOOLKIT_PRIVATE_NOTES" in captured.err.split("\n次の操作: ", 1)[1]


class TestAddSingleMessage:
    """addサブコマンド: 単一メッセージで1ファイル生成とgit操作順序を検証する。"""

    def test_single_message_generates_one_file(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """単一メッセージで1ファイルが生成され、frontmatterとgit操作順序が正しいこと。"""
        notes = _setup_notes(tmp_path)

        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        git_calls = _install_myrepo_git_fake(monkeypatch, myrepo)

        repo_path = str(myrepo)
        message = "テストメッセージ"

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "add", repo_path, *_body_file_args(tmp_path, message)], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0

        inbox_dir = notes / "2024/01"
        files = sorted(inbox_dir.iterdir())
        assert len(files) == 1

        content = files[0].read_text(encoding="utf-8")
        assert "created:" not in content.split("---\n\n", 1)[0]
        assert "target_repo: github.com/example/myrepo" in content

        body = content.split("---\n\n", 1)[1]
        assert body == message + "\n"

        git_cmds = [c["cmd"] for c in git_calls]
        remote_url_cmd = ["git", "-C", str(myrepo), "remote", "get-url", "origin"]
        assert remote_url_cmd in git_cmds
        saved_commands = [cmd for cmd in git_cmds if cmd[1] in {"fetch", "merge", "add", "commit", "push"}]
        assert saved_commands[:4] == [
            ["git", "fetch"],
            ["git", "merge", "--ff-only", "@{u}"],
            ["git", "add", "--all", "--", f"2024/01/{files[0].name}"],
            ["git", "commit", "-m", "chore: add 1 awi item", "--", f"2024/01/{files[0].name}"],
        ]
        assert ["git", "push"] in saved_commands[4:]
        for call in git_calls:
            if call["cmd"][:2] != ["git", "-C"]:
                assert call["kwargs"].get("cwd") == notes

        captured = capsys.readouterr()
        assert "成功: 1件をinboxへ投入した\n" in captured.out
        assert f"  ~/private-notes/2024/01/{files[0].name}\n" in captured.out
        assert "inbox: 計" not in captured.out
        assert "編集する場合:" not in captured.out
        assert f"  atk wi edit {files[0].name}\n" not in captured.out


class TestMqLifecycleScenario:
    """add→list→start-processing→adoptの一連運用フローを検証する。"""

    def test_add_then_list_then_start_processing_then_adopt(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """登録したエントリが一覧表示を経て処理中・採用済みへ遷移する。"""
        notes = _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        git_calls = _install_myrepo_git_fake(monkeypatch, myrepo)
        filename = f"{_FIXED_TIMESTAMP}-001.md"
        message = "ライフサイクル確認"

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "add", str(myrepo), *_body_file_args(tmp_path, message)], home=tmp_path, now=_FIXED_DT)
        assert exc_info.value.code == 0
        capsys.readouterr()

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list"], home=tmp_path)
        assert exc_info.value.code == 0
        expected = f"{filename}: github.com/example/myrepo [inbox/normal/ready] {message}"
        assert expected in capsys.readouterr().out

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "start-processing", filename], home=tmp_path)
        assert exc_info.value.code == 0
        entry = notes / "2024/01" / filename
        assert entry.is_file()
        assert _wi_entries.entry_state_of(entry) == "processing"

        git_calls.clear()
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "adopt", filename], home=tmp_path)
        assert exc_info.value.code == 0
        assert entry.is_file()
        assert _wi_entries.entry_state_of(entry) == "adopted"
        git_cmds = [call["cmd"] for call in git_calls]
        assert any(cmd[:2] == ["git", "add"] for cmd in git_cmds)
        assert any(cmd[:2] == ["git", "commit"] for cmd in git_cmds)
        assert ["git", "push"] in git_cmds


class TestAddCompletionNoQueueOverview:
    """投入結果だけを示し、無関係なキュー件数と固定案内を出力しない。"""

    @pytest.mark.parametrize("existing_state", ["inbox", "processing"])
    @pytest.mark.parametrize("agent_environment", [False, True])
    def test_omits_queue_overview_in_both_environments(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        existing_state: str,
        agent_environment: bool,
    ) -> None:
        """人の端末とエージェントの双方で、別項目の件数を投入結果に付けない。"""
        notes = _setup_notes(tmp_path)
        existing = wi_entry_files.write_awi(notes, "existing-001.md", state=existing_state)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        monkeypatch.setattr(subprocess, "run", _make_git_remote_fake(myrepo))
        if agent_environment:
            monkeypatch.setenv("AI_AGENT", "1")

        with pytest.raises(SystemExit, match="0"):
            atk.main(
                ["wi", "add", str(myrepo), "--source=agent", *_body_file_args(tmp_path, _wi_bodies.AGENT_AWI_BODY)],
                home=tmp_path,
                now=_FIXED_DT,
            )

        captured = capsys.readouterr()
        assert captured.out.splitlines()[0] == "成功: 1件をinboxへ投入した"
        assert "inbox: 計" not in captured.out
        assert "  うち" not in captured.out
        assert "編集する場合:" not in captured.out
        assert existing.name not in captured.out
        assert not captured.err
        assert existing.is_file()


class TestAddMultipleMessages:
    """addサブコマンド: 2件以上のメッセージで連番と件数コミットメッセージを検証する。"""

    def test_multiple_messages_generate_files_with_sequence(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """2件のメッセージで連番001・002の付与とコミットメッセージを検証する。"""
        notes = _setup_notes(tmp_path)

        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        git_calls = _install_myrepo_git_fake(monkeypatch, myrepo)

        repo_path = str(myrepo)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["wi", "add", repo_path, *_body_file_args(tmp_path, "メッセージ1", "メッセージ2")],
                home=tmp_path,
                now=_FIXED_DT,
            )

        assert exc_info.value.code == 0

        inbox_dir = notes / "2024/01"
        files = sorted(inbox_dir.iterdir())
        assert len(files) == 2
        assert files[0].name == f"{_FIXED_TIMESTAMP}-001.md"
        assert files[1].name == f"{_FIXED_TIMESTAMP}-002.md"

        commit_cmd = [c["cmd"] for c in git_calls if "commit" in c["cmd"]][0]
        assert "chore: add 2 awi items" in commit_cmd

        captured = capsys.readouterr()
        assert "成功: 2件をinboxへ投入した\n" in captured.out
        assert f"  ~/private-notes/2024/01/{files[0].name}\n" in captured.out
        assert f"  ~/private-notes/2024/01/{files[1].name}\n" in captured.out
        assert "inbox: 計" not in captured.out
        assert "編集する場合:" not in captured.out
        assert f"  atk wi edit {files[0].name}\n" not in captured.out
        assert f"  atk wi edit {files[1].name}\n" not in captured.out


class TestAddRepoPathExpansion:
    """addサブコマンド: ~プレフィックスのrepo_pathがリモートURLへ正規化されること。"""

    def test_tilde_repo_path_is_expanded(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """~展開後にgit remote get-urlでリモートURLが取得され、target_repoへ書き込まれる。"""
        notes = _setup_notes(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))

        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        monkeypatch.setattr(subprocess, "run", _make_git_remote_fake(myrepo))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "add", "~/myrepo", *_body_file_args(tmp_path, "テストメッセージ")], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0

        inbox = notes / "2024/01"
        files = list(inbox.iterdir())
        assert len(files) == 1
        content = files[0].read_text(encoding="utf-8")
        assert "target_repo: github.com/example/myrepo" in content


class TestParseLeadingFrontmatter:
    """_parse_leading_frontmatterの単体テスト（frontmatter判定と本文分離を検証する）。"""

    def test_leading_frontmatter_overrides_target_repo(self) -> None:
        """先頭frontmatterの`target_repo`がパース結果に含まれる。"""
        message = "---\ntarget_repo: github.com/x/y\n---\n\n本文"
        fm, body = _add._parse_leading_frontmatter(message)  # pylint: disable=protected-access  # noqa: SLF001
        assert fm == {"target_repo": "github.com/x/y"}
        assert body == "\n本文"

    def test_leading_frontmatter_source_priority(self) -> None:
        """先頭frontmatterに`source`が含まれる場合はパース結果に含まれる。"""
        message = "---\ntarget_repo: github.com/x/y\nsource: session-review\n---\n\n本文"
        fm, body = _add._parse_leading_frontmatter(message)  # pylint: disable=protected-access  # noqa: SLF001
        assert fm == {"target_repo": "github.com/x/y", "source": "session-review"}
        assert body == "\n本文"

    def test_without_frontmatter_returns_original(self) -> None:
        """先頭がfrontmatterでない場合は空dictと元メッセージを返す。"""
        message = "普通の本文\n2行目"
        fm, body = _add._parse_leading_frontmatter(message)  # pylint: disable=protected-access  # noqa: SLF001
        assert not fm
        assert body == message

    def test_body_horizontal_rule_not_treated_as_frontmatter(self) -> None:
        """本文中の水平線があるメッセージはfrontmatterと解釈せず本文が保持される。"""
        message = "---\n\n本文開始\n\n---\n\n本文継続"
        fm, body = _add._parse_leading_frontmatter(message)  # pylint: disable=protected-access  # noqa: SLF001
        assert not fm
        assert body == message

    def test_frontmatter_without_closing_returns_original(self) -> None:
        """先頭が3ハイフンで始まっても閉じ区切りがない場合は元メッセージを返す。"""
        message = "---\ntarget_repo: github.com/x/y\n本文継続なし"
        fm, body = _add._parse_leading_frontmatter(message)  # pylint: disable=protected-access  # noqa: SLF001
        assert not fm
        assert body == message


class TestAddFrontmatterOverride:
    """addサブコマンド: CLI指定とメッセージfrontmatterの優先順位を検証する。"""

    def test_message_frontmatter_overrides_cli_target_repo(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """メッセージfrontmatterの`target_repo`がCLI指定より優先される。"""
        notes = _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        monkeypatch.setattr(subprocess, "run", _make_git_remote_fake(myrepo))

        message = f"---\ntarget_repo: github.com/other/repo\nsource: session-review\n---\n\n{_wi_bodies.AGENT_AWI_BODY}"
        argv = ["wi", "add", str(myrepo), "--source", "cli-source", *_body_file_args(tmp_path, message)]

        with pytest.raises(SystemExit) as exc_info:
            atk.main(argv, home=tmp_path, now=_FIXED_DT)
        assert exc_info.value.code == 0

        files = list((notes / "2024/01").iterdir())
        assert len(files) == 1
        content = files[0].read_text(encoding="utf-8")
        assert "target_repo: github.com/other/repo" in content
        assert "source: session-review" in content
        body = content.split("---\n\n", 1)[1]
        assert body == _wi_bodies.AGENT_AWI_BODY

    def test_multiple_messages_mixed_frontmatter(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """複数メッセージ混在時（一部のみfrontmatter付き）に各メッセージが独立判定される。"""
        notes = _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        monkeypatch.setattr(subprocess, "run", _make_git_remote_fake(myrepo))

        msg_with_fm = "---\ntarget_repo: github.com/override/repo\n---\n\nfm付き本文"
        msg_plain = "frontmatter無し本文"

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["wi", "add", str(myrepo), *_body_file_args(tmp_path, msg_with_fm, msg_plain)], home=tmp_path, now=_FIXED_DT
            )
        assert exc_info.value.code == 0

        inbox = notes / "2024/01"
        files = sorted(inbox.iterdir())
        assert len(files) == 2
        content_first = files[0].read_text(encoding="utf-8")
        content_second = files[1].read_text(encoding="utf-8")
        assert "target_repo: github.com/override/repo" in content_first
        assert content_first.split("---\n\n", 1)[1] == "fm付き本文"
        assert "target_repo: github.com/example/myrepo" in content_second
        assert content_second.split("---\n\n", 1)[1] == msg_plain + "\n"

    def test_frontmatter_target_repo_only_falls_back_to_cli_source(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """frontmatterに`target_repo`のみで`source`未指定の場合、CLIオプション値を採用する。"""
        notes = _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        monkeypatch.setattr(subprocess, "run", _make_git_remote_fake(myrepo))

        message = f"---\ntarget_repo: github.com/other/repo\n---\n\n{_wi_bodies.AGENT_AWI_BODY}"

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                [
                    "wi",
                    "add",
                    str(myrepo),
                    "--source",
                    "cli-source",
                    *_body_file_args(tmp_path, message),
                ],
                home=tmp_path,
                now=_FIXED_DT,
            )
        assert exc_info.value.code == 0

        inbox = notes / "2024/01"
        files = list(inbox.iterdir())
        assert len(files) == 1
        content = files[0].read_text(encoding="utf-8")
        assert "target_repo: github.com/other/repo" in content
        assert "source: cli-source" in content


def _write_uwi_file(
    notes: pathlib.Path,
    filename: str,
    target_repo: str = "github.com/example/foo",
    question: str = "テスト質問",
    answer: str = "",
    source: str | None = None,
    legacy_layout: bool = False,
) -> pathlib.Path:
    """状態付きUWIを書き込み、移行検証の明示指定時は旧配置を使う。"""
    path = wi_entry_files.entry_path(notes, filename, legacy_layout=legacy_layout)
    source_line = f"source: {source}\n" if source is not None else ""
    state_line = "" if legacy_layout else "state: inbox\n"
    path.write_text(
        f"---\n{state_line}created: {_FIXED_ISO}\ntarget_repo: {target_repo}\ntype: uwi\n"
        f"question_type: free-form\n{source_line}---\n\n"
        f"## 質問\n\n{question}\n\n## 回答\n\n{answer}",
        encoding="utf-8",
    )
    return path


def test_wi_add_rejects_retired_origin_locator() -> None:
    """廃止した`--origin-locator`はargparseで拒否する。"""
    parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["wi", "add", "--origin-locator", "rollout-123:1"])

    assert exc_info.value.code == 2


class TestAddBatchOption:
    """`mq add --batch`のCLI分岐と併用制約を検証する。"""

    _ENTRY = "### keep.md [inbox]\n---\ntarget_repo: github.com/example/foo\ntype: awi\n---\n\n取り込む本文\n\n"

    @staticmethod
    def _patch_batch_repo_operations(monkeypatch: pytest.MonkeyPatch) -> None:
        """一括取り込み側のロック・remote同期・commitを無効化する。"""

        monkeypatch.setattr(_wi_sync, "repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
        monkeypatch.setattr(_wi_sync, "pull", lambda _path: None)
        monkeypatch.setattr(_wi_sync, "commit_and_push", lambda *_args, **_kwargs: None)

    def test_normal_add_normalizes_omitted_type_to_awi(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`--type`省略の通常addは従来どおりawiとして保存する。"""
        notes = _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        body_path = tmp_path / "body.md"
        body_path.write_text("本文", encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_git_remote_fake(myrepo))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "add", str(myrepo), "--body-file", str(body_path)], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0
        contents = [path.read_text(encoding="utf-8") for path in (notes / "2024/01").iterdir()]
        assert len(contents) == 1
        assert all("type: awi" in content for content in contents)

    @pytest.mark.parametrize(
        "options",
        [
            ["--type=awi"],
            ["--type=uwi"],
            ["--target-repo=github.com/example/foo"],
            ["--source=session-review"],
            ["--scope=name"],
            ["--question-type=yes-no"],
            ["--choices=A,B"],
            ["--choices=A", "--choices=B,C"],
            ["--depends-on=other.md"],
        ],
    )
    def test_rejects_options_conflicting_with_batch(
        self,
        options: list[str],
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`--batch`と併用できないオプション指定をusage表示付きでexit 2にする。"""
        _setup_notes(tmp_path)
        body_path = tmp_path / "batch.md"
        body_path.write_text(self._ENTRY, encoding="utf-8")

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "add", "--batch", *options, "--body-file", str(body_path)], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 2
        assert "--batchと併用できません" in capsys.readouterr().err

    def test_rejects_positional_message_with_body_file(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """MESSAGE位置引数を受理しない。"""
        _setup_notes(tmp_path)
        self._patch_batch_repo_operations(monkeypatch)
        body_path = tmp_path / "batch.md"
        body_path.write_text(self._ENTRY, encoding="utf-8")

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "add", "--batch", "--body-file", str(body_path), self._ENTRY], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 2
        assert "余った位置引数:" in capsys.readouterr().err

    def test_rejects_positional_message_without_writing(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """MESSAGE位置引数のshow形式テキストを拒否して書き込まない。"""
        notes = _setup_notes(tmp_path)
        self._patch_batch_repo_operations(monkeypatch)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "add", "--batch", self._ENTRY], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 2
        assert not list(_wi_entries.iter_entry_paths(notes))

    def test_imports_from_body_file(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`--body-file`経由でも同じ内容を取り込む。"""
        notes = _setup_notes(tmp_path)
        self._patch_batch_repo_operations(monkeypatch)
        body_path = tmp_path / "batch.md"
        body_path.write_text(self._ENTRY, encoding="utf-8")

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "add", "--batch", "--body-file", str(body_path)], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0
        assert (notes / "2024/01/keep.md").is_file()

    @staticmethod
    def _patch_editor(monkeypatch: pytest.MonkeyPatch, saved_text: str) -> None:
        """$EDITOR相当の実行を、指定テキストを一時ファイルへ保存するだけの動作へ差し替える。"""
        monkeypatch.setenv("EDITOR", "fake-editor")

        def fake_run(cmd: list[str], *_args: Any, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
            if cmd[0] == "fake-editor":
                pathlib.Path(cmd[1]).write_text(saved_text, encoding="utf-8")
                return subprocess.CompletedProcess(cmd, returncode=0)
            empty: Any = "" if kwargs.get("text") else b""
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

        monkeypatch.setattr(subprocess, "run", fake_run)

    def test_imports_via_editor_preserving_raw_text(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`--body-file`省略時はエディターで入力した生テキストを原文のまま取り込む。"""
        notes = _setup_notes(tmp_path)
        self._patch_batch_repo_operations(monkeypatch)
        entry = "### keep.md [inbox]\n---\ntarget_repo: github.com/example/foo\ntype: awi\n---\n\n取り込む本文  \n"
        self._patch_editor(monkeypatch, f"\n{entry}")

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "add", "--batch"], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0
        assert (notes / "2024/01/keep.md").read_text(encoding="utf-8") == (
            "---\nstate: inbox\ntarget_repo: github.com/example/foo\ntype: awi\n---\n\n取り込む本文  \n"
        )
        assert "成功: 1件をinboxへ取り込んだ" in capsys.readouterr().out

    def test_rejects_non_show_format_input(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """show形式として解析できない入力はexit 1で全件拒否する。"""
        notes = _setup_notes(tmp_path)
        self._patch_batch_repo_operations(monkeypatch)
        body_path = tmp_path / "batch.md"
        body_path.write_text("ただの本文\n", encoding="utf-8")

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "add", "--batch", "--body-file", str(body_path)], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 1
        assert "投入を拒否した" in capsys.readouterr().err
        assert not list(_wi_entries.iter_entry_paths(notes))


class TestMainFailureNextAction:
    """`atk.py:main`が捕捉した例外を失敗行と次の操作の行の2行で出力することを検証する。"""

    @staticmethod
    def _run_wi_commit(
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        error: BaseException,
    ) -> list[str]:
        """`atk wi commit`の処理が`error`を送出したときの標準エラーの行を返す。"""
        monkeypatch.setattr(_wi_sync, "ensure_environment", lambda _home: tmp_path)

        def fail(*_args: object) -> None:
            raise error

        monkeypatch.setattr(_mutation_targets, "cmd_commit", fail)
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "commit"], home=tmp_path, now=_FIXED_DT)
        assert exc_info.value.code == 1
        return capsys.readouterr().err.splitlines()

    @staticmethod
    def _next_action_after_failure(lines: list[str]) -> str:
        """失敗行の直後の行が次の操作の行であることを確かめて返す。"""
        index = next(i for i, line in enumerate(lines) if line.startswith("失敗: "))
        assert lines[index + 1].startswith("次の操作: ")
        return lines[index + 1]

    def test_actionable_error_prints_its_next_action(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """`WebInputError`は発生源が渡した次の操作を出力する。"""
        error = _wi_web_input.WebInputError("対象が無い", next_action="`atk wi list`で対象を確認する")
        lines = self._run_wi_commit(tmp_path, monkeypatch, capsys, error)
        assert lines[0] == "失敗: 操作を拒否した: 対象が無い"
        assert self._next_action_after_failure(lines) == "次の操作: `atk wi list`で対象を確認する"

    def test_plain_value_error_names_help_and_bug_report(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """共通型でない`ValueError`は受理形式の確認と不具合の報告を案内する。"""
        lines = self._run_wi_commit(tmp_path, monkeypatch, capsys, ValueError("想定外の値"))
        next_action = self._next_action_after_failure(lines)
        assert "`atk wi commit --help`" in next_action
        assert "ユーザーへ報告" in next_action

    def test_rebase_in_progress_error_is_reported_without_traceback(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """wiの処理で発生した`RebaseInProgressError`は失敗行と競合解消の次の操作で出力する。"""
        error = _git_sync.RebaseInProgressError("rebase中")
        lines = self._run_wi_commit(tmp_path, monkeypatch, capsys, error)
        assert "git -c submodule.recurse=false rebase --continue" in self._next_action_after_failure(lines)

    def test_wi_edit_rejects_rebase_in_progress_before_change(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """rebaseの中間状態が残るWI保存リポジトリでは、`atk wi edit`が本文を変える前に拒否し、解消操作を示す。"""
        notes = tmp_path / "notes"
        git_repository.init_repository(notes)
        original = "---\nstate: inbox\ntype: awi\ntarget_repo: github.com/example/repo\n---\n\n本文\n"
        entry = wi_entry_files.entry_path(notes, "entry.md")
        entry.write_text(original, encoding="utf-8")
        git_repository.run_git(notes, "add", "-A")
        git_repository.run_git(notes, "commit", "-m", "setup")
        rebase_dir = git_repository.run_git(notes, "rev-parse", "--git-path", "rebase-merge").stdout.strip()
        (notes / rebase_dir).mkdir(parents=True)
        monkeypatch.setattr(_wi_sync, "ensure_environment", lambda _home: notes)
        body = tmp_path / "body.md"
        body.write_text(original.replace("本文", "変更後の本文"), encoding="utf-8")

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "edit", "entry.md", "--body-file", str(body)], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 1
        lines = capsys.readouterr().err.splitlines()
        assert "rebase中" in next(line for line in lines if line.startswith("失敗: "))
        assert "git -c submodule.recurse=false rebase --continue" in self._next_action_after_failure(lines)
        assert entry.read_text(encoding="utf-8") == original

    def test_unreported_git_failure_names_status_check(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """同期基盤が報告していないGit操作の失敗は、`git -C <private-notes> status`での確認を案内する。"""
        error = subprocess.CalledProcessError(1, ["git", "commit"])
        lines = self._run_wi_commit(tmp_path, monkeypatch, capsys, error)
        assert f"`git -C {tmp_path.resolve()} status`" in self._next_action_after_failure(lines)

    def test_reported_git_failure_is_not_repeated(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """同期基盤が原因と次の操作を出力済みのGit操作の失敗へ、汎用の失敗行を重ねない。"""
        error = subprocess.CalledProcessError(1, ["git", "push"])
        _git_sync.mark_reported(error)
        lines = self._run_wi_commit(tmp_path, monkeypatch, capsys, error)
        assert not any(line.startswith("失敗: ") for line in lines)
