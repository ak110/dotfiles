"""`atk wi process-loop`のテストが共有する、子プロセスの起動の差し替えとprivate-notesの準備。"""

import json
import pathlib
import shutil
import subprocess
from collections.abc import Sequence
from typing import Any, NoReturn

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import config as _config
from agent_toolkit._atk.wi import process_loop as _loop
from agent_toolkit._atk.wi import process_loop_control as _control
from agent_toolkit._atk.wi import process_loop_scheduler as _scheduler
from agent_toolkit._atk.wi import process_loop_watch as _pl_watch
from agent_toolkit._atk.wi import process_loop_worktree as _pl_worktree
from agent_toolkit._common import codex_models
from agent_toolkit._common import wait_schedule as _wait_schedule
from agent_toolkit._testing.managed_temp_support import setattr_in_managed_temp_modules

DOTFILES_REPO_ID = _pl_worktree.DOTFILES_REPO_ID  # pylint: disable=protected-access  # noqa: SLF001


class _InlineScheduler(_scheduler.RepositoryScheduler):
    """従来のCLI試験ではworker境界だけを代用し、実LLMを起動しない。"""

    def __init__(self) -> None:
        super().__init__()
        self._ready: dict[str, dict[str, Any]] = {}

    def start(self, args: Any, snapshots: Any, launches: Any) -> None:
        """所有・準備・結果検収は実装を通し、外部workerだけを省く。"""
        if request_id := getattr(args, "resume_request_id", None):
            # この代用は起動済みCLIの実行を表す。Popen失敗の予約保持は実launch側で検査する。
            with _control.resume_launch(request_id):
                pass
        results = _loop.run_coordinator(args, snapshots, launch_inputs=launches)
        self._ready.update((repo, result) for repo, result in results.items() if not result.get("reservation_excluded"))
        self.started.update(repo for repo, result in results.items() if not result.get("reservation_excluded"))

    def poll(self) -> dict[str, dict[str, Any]]:
        """完了通知を次のscheduler反復へ渡す。"""
        ready, self._ready = self._ready, {}
        self.results.update(ready)
        return ready


def use_inline_scheduler(monkeypatch: pytest.MonkeyPatch) -> None:
    """実workerの別プロセス起動を、同じ対象検収を使う代用へ替える。"""
    monkeypatch.setattr(_scheduler, "RepositoryScheduler", _InlineScheduler)


def set_process_loop_repositories(monkeypatch: pytest.MonkeyPatch, paths: Sequence[pathlib.Path]) -> None:
    """正常系で使うGit作業ツリーを現行のrepository属性mapで登録する。"""
    monkeypatch.setenv(
        "AGENT_TOOLKIT_CONFIG_REPOSITORIES",
        json.dumps({str(path): {"edit": "allow", "process_loop": True} for path in paths}),
    )


def set_orchestrate_model(tmp_path: pathlib.Path, value: str) -> None:
    """工程別候補列を公開CLIで保存し、軽量プローブにはCodexの完全IDを与える。"""
    for key, setting in [("orchestrate_model", value), ("chores_fast_model", "codex:gpt-6-luna/medium,claude:haiku/medium")]:
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "set", key, setting], home=tmp_path)
        assert exc_info.value.code == 0


def set_orchestrate_environment(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    """候補列と、モデル一覧を必要としない軽量プローブ設定を環境変数で与える。"""
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_ORCHESTRATE_MODEL", value)
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CHORES_FAST_MODEL", "codex:gpt-6-luna/medium,claude:haiku/medium")


def hook_debug_log(command: list[str]) -> pathlib.Path:
    """Claude起動コマンドからhook診断ログを取得し、共通契約を検証する。"""
    assert command[0] == "claude"
    assert "--debug=hooks" in command
    debug_log = pathlib.Path(command[command.index("--debug-file") + 1])
    assert debug_log.is_absolute()
    assert debug_log.is_file()
    return debug_log


def fake_run_with_remote_url(
    myrepo: pathlib.Path,
    claude_calls: list[dict[str, Any]],
    claude_returncode: int,
) -> Any:
    """対話セッション呼び出しを記録し、リモートURL取得にはダミー値を返すfake_runを構築する。"""

    def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
        if any('kind="availability-probe"' in argument for argument in cmd):
            probe_empty: Any = "" if kwargs.get("text") else b""
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=probe_empty, stderr=probe_empty)
        if cmd[:1] in (["claude"], ["codex"]):
            claude_calls.append(
                {
                    "cmd": list(cmd),
                    "env": kwargs.get("env"),
                    "cwd": kwargs.get("cwd"),
                    "kwargs": dict(kwargs),
                }
            )
            empty: Any = "" if kwargs.get("text") else b""
            return subprocess.CompletedProcess(cmd, returncode=claude_returncode, stdout=empty, stderr=empty)
        if cmd == ["git", "-C", str(myrepo), "remote", "get-url", "origin"]:
            stdout: Any = (
                "https://github.com/example/myrepo.git\n" if kwargs.get("text") else b"https://github.com/example/myrepo.git\n"
            )
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr="" if kwargs.get("text") else b"")
        empty = "" if kwargs.get("text") else b""
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

    return fake_run


def raise_system_exit_0(*_a: object, **_kw: object) -> NoReturn:  # os.execvの代替として無条件にSystemExit(0)を送出する。
    raise SystemExit(0)


def isolate_process_loop_commands(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """外部コマンド・Claude設定・初期値のTTLをユーザー環境から分離する。

    `atk wi process-loop`を公開CLI経由で動かすテストが、各テストの前に呼ぶ。
    """
    use_inline_scheduler(monkeypatch)
    monkeypatch.setattr(_config.platformdirs, "user_config_dir", lambda _name, **_kwargs: str(tmp_path / "config"))
    setattr_in_managed_temp_modules(monkeypatch, "_state_root_path", lambda: tmp_path / "managed-temp-state")
    monkeypatch.setattr(shutil, "which", lambda command: f"/resolved/{command}")
    monkeypatch.setattr(_pl_watch, "pull_private_notes", lambda _path: True)
    monkeypatch.setattr(_pl_watch, "_submission_active", lambda _targets: False)
    monkeypatch.setattr(
        codex_models,
        "list_models",
        lambda: [{"model": "gpt-6-sol", "supportedReasoningEfforts": [{"reasoningEffort": "medium"}]}],
    )
    monkeypatch.setattr(_wait_schedule, "get_prompt_cache_ttl", lambda _bucket: "1h")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
