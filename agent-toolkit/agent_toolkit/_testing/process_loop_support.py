"""`atk wi process-loop`のテストが共有する、子プロセスの起動の差し替えとprivate-notesの準備。"""

import pathlib
import shutil
import subprocess
from typing import Any, NoReturn

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import config as _config
from agent_toolkit._atk.wi import process_loop_watch as _pl_watch
from agent_toolkit._atk.wi import process_loop_worktree as _pl_worktree
from agent_toolkit._common import codex_models
from agent_toolkit._common import wait_schedule as _wait_schedule
from agent_toolkit._testing.managed_temp_support import setattr_in_managed_temp_modules

DOTFILES_REPO_ID = _pl_worktree.DOTFILES_REPO_ID  # pylint: disable=protected-access  # noqa: SLF001


def set_orchestrate_model(tmp_path: pathlib.Path, value: str) -> None:
    """テスト用の`orchestrate_model`を公開設定CLI経由で保存する。"""
    with pytest.raises(SystemExit) as exc_info:
        atk.main(["config", "set", "orchestrate_model", value], home=tmp_path)
    assert exc_info.value.code == 0


def hook_debug_log(command: list[str]) -> pathlib.Path:
    """Claude起動コマンドからhook診断ログを取得し、共通契約を検証する。"""
    assert command[:3] == ["claude", "--debug=hooks", "--debug-file"]
    debug_log = pathlib.Path(command[3])
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
        if cmd[:2] == ["codex", "exec"] or (cmd[:1] == ["claude"] and "-p" in cmd):
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
    monkeypatch.setattr(_config.platformdirs, "user_config_dir", lambda _name, **_kwargs: str(tmp_path / "config"))
    setattr_in_managed_temp_modules(monkeypatch, "_state_root_path", lambda: tmp_path / "managed-temp-state")
    monkeypatch.setattr(shutil, "which", lambda command: f"/resolved/{command}")
    monkeypatch.setattr(_pl_watch, "pull_private_notes", lambda _path: True)
    monkeypatch.setattr(
        codex_models,
        "list_models",
        lambda: [{"model": "gpt-6-sol", "supportedReasoningEfforts": [{"reasoningEffort": "medium"}]}],
    )
    monkeypatch.setattr(_wait_schedule, "get_prompt_cache_ttl", lambda _bucket: "1h")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
