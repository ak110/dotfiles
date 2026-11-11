"""atkの分割試験で共有する準備と補助処理。"""

from __future__ import annotations

import datetime
import pathlib
import subprocess
import types
from collections.abc import Callable
from typing import Any

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import config as _config
from agent_toolkit._atk import plans as _plans_module
from agent_toolkit._atk.wi import add as _add
from agent_toolkit._atk.wi import alerts as _wi_alerts
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._atk.wi.mutations import transitions as _mutation_transitions
from agent_toolkit._common import codex_models
from agent_toolkit._testing import git_repository, wi_entry_files
from agent_toolkit._testing import wi_bodies as _wi_bodies
from agent_toolkit._testing.git_fakes import _FIXED_HEAD_COMMIT
from agent_toolkit._testing.isolation import host_package_cache_environ

_GitCall = dict[str, Any]

_ATK_PATH = pathlib.Path(atk.__file__).resolve()

_PROJECT_ROOT = _ATK_PATH.parents[1]


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


_SYNC_MUTATION_ENTRIES = ("wi start-processing", "plans commit")

"""終了時に未pushを判定する操作のコマンド。`wi start-processing`は代役が実際にcommitし、送信を試みる。"""

_NON_SAVING_ENTRIES = ("add --dry-run", "edit --dry-run", "edit without diff", "answer without target", "check-alerts")

"""commitも未pushの送信も試みずに成功で終わる`atk wi`の操作。"""

_EDIT_TARGET = "20260714-000001-001.md"

# `_write_awi_file`が省略時に書く対象リポジトリ。実行位置から解決した値との不一致で編集が拒否されないよう明示する。
_EDIT_TARGET_REPO = "github.com/example/foo"

_DRY_RUN_SUCCESS = "成功: 投入前の検証が成立した（--dry-runのため保存していない）"


def _prepare_sync_mutation(entry: str, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """同期対象操作のコマンドごとに外部への作用を差し替え、`atk.main`へ渡す引数を返す。"""
    if entry == "wi start-processing":

        def commit_in_lock(_args: Any, notes: pathlib.Path, *_rest: Any) -> None:
            # 実物と同じく、ロック内でcommitしてから未pushの送信を試みる。
            with _wi_sync.repo_lock(notes):
                (notes / "started.txt").write_text("started\n", encoding="utf-8")
                _wi_sync.commit_and_push(notes, "chore: start", ["started.txt"])

        monkeypatch.setattr(_mutation_transitions, "cmd_start_processing", commit_in_lock)
        return ["wi", "start-processing", "awi.md"]
    monkeypatch.setattr(_plans_module, "dispatch", lambda *_args: 0)
    return ["plans", "commit", "01-0900_example.md"]


def _prepare_non_saving_operation(
    entry: str, notes: pathlib.Path, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> list[str]:
    """保存しない操作ごとに入力を用意し、`atk.main`へ渡す引数を返す。"""
    body = tmp_path / "body.md"
    if entry == "add --dry-run":
        monkeypatch.setattr(_add, "resolve_add_target", lambda _value: ("github.com/example/repo", None))
        body.write_text("本文\n", encoding="utf-8")
        return ["wi", "add", "--dry-run", "--target-repo", "github.com/example/repo", "--body-file", str(body)]
    if entry == "edit --dry-run":
        body.write_text(f"{_wi_bodies.AGENT_AWI_BODY}\n追記\n", encoding="utf-8")
        return ["wi", "edit", _EDIT_TARGET, "--dry-run", "--target-repo", _EDIT_TARGET_REPO, "--body-file", str(body)]
    if entry == "edit without diff":
        body.write_text(f"{_wi_bodies.AGENT_AWI_BODY}\n", encoding="utf-8")
        return ["wi", "edit", _EDIT_TARGET, "--target-repo", _EDIT_TARGET_REPO, "--body-file", str(body)]
    if entry == "answer without target":
        monkeypatch.setenv("EDITOR", "true")
        return ["wi", "answer"]
    del notes
    # CIの照会は外部サービスを呼ぶため、失敗の無い監視結果（投入0件）へ差し替える。
    monkeypatch.setattr(
        _wi_alerts, "check_and_submit_alerts", lambda *_args, **_kwargs: types.SimpleNamespace(submitted=0, failures=())
    )
    return ["wi", "check-alerts"]


_CONCURRENT_PUSH_SCRIPT = """
import pathlib
import subprocess
import sys
import time

from agent_toolkit._atk import git_sync

notes = pathlib.Path(sys.argv[1])
ready = pathlib.Path(sys.argv[2])
release = pathlib.Path(sys.argv[3])
with git_sync.repo_lock(notes):
    (notes / "concurrent.txt").write_text("concurrent\\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(notes), "add", "concurrent.txt"], check=True)
    subprocess.run(["git", "-C", str(notes), "commit", "-q", "-m", "concurrent"], check=True)
    ready.write_text("committed", encoding="utf-8")
    deadline = time.monotonic() + 30
    while not release.exists():
        assert time.monotonic() < deadline
        time.sleep(0.01)
    subprocess.run(["git", "-C", str(notes), "push", "-q"], check=True)
"""

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
