"""起動時に登録する共通fixture。自動の差し替えは元のディレクトリ境界だけへ適用する。

package collectorが収集順で再生成されても、fixtureの登録を失わないようにする。
上流pytestの修正採用後の撤去条件はdocs/development/design-packages-test-isolation.mdに記す。
"""

import asyncio
import contextlib
import datetime
import json
import math
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Generator, Iterator
from multiprocessing import resource_tracker
from typing import Any

import playwright.async_api
import psutil
import pytest
import pytest_asyncio
import watchdog.observers
import watchdog.observers.polling
from _pytest.runner import runtestprotocol
from _pytest.terminal import TerminalReporter
from pyfltr.colloquial import check as _colloquial_check
from pyfltr.command import execution_slot

import agent_toolkit
from agent_toolkit._agents_server import api_credits, claude, claude_stream, launch_requests
from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._atk import config_settings, managed_temp
from agent_toolkit._atk import orchestrator as _orchestrator
from agent_toolkit._atk.managed_temp import sweep
from agent_toolkit._common import codex_models, plugin_environment, process_tree
from agent_toolkit._common import wait_schedule as _wait_schedule
from agent_toolkit._git import command as _git_command
from agent_toolkit._hooks import notice
from agent_toolkit._hooks import transcript_scan as _transcript_scan
from agent_toolkit._plan import creation_times as plan_creation_times
from agent_toolkit._testing import (
    agents_server_fixtures,
    cpu_accounting,
    hook_support,
    isolation,
    resource_diagnostics,
    session_evidence_support,
    xdist_stop,
)
from agent_toolkit._testing import git_repository as _git_repository
from agent_toolkit._testing.logging_support import isolated_agents_server_logging
from agent_toolkit._testing.managed_temp_support import setattr_in_managed_temp_modules
from agent_toolkit._testing.wi_mutations_support import _AGENT_ENVIRONMENT_VARIABLES

local_time_jst = session_evidence_support.local_time_jst

_BROWSER_REPORTS = pytest.StashKey[dict[str, pytest.TestReport]]()
_BROWSER_EVIDENCE = pytest.StashKey[pathlib.Path]()
_CPU_TIMES = pytest.StashKey[dict[str, float]]()
_CPU_LIMIT = pytest.StashKey[tuple[float, str]]()
_CPU_EXCLUSIONS = pytest.StashKey[dict[str, dict[str, float]]]()
_CPU_GROSS_LIMIT = pytest.StashKey[float]()
_CPU_BUDGET = pytest.StashKey[cpu_accounting.CpuBudget]()
_CPU_PROCESS_STARTED = pytest.StashKey[float]()
_CPU_RETRY_CI = pytest.StashKey[bool]()
_CPU_RECORD_ONLY = pytest.StashKey[bool]()
_CPU_ONLY_FAILURE = pytest.StashKey[bool]()


@pytest.hookimpl(specname="pytest_runtest_protocol", tryfirst=True)
def pytest_runtest_cpu_retry(item: pytest.Item, nextitem: pytest.Item | None) -> bool | None:
    """CIのCPU単独超過だけを再確認し、未確定の初回失敗を集計へ送らない。"""
    if not item.config.stash.get(_CPU_RETRY_CI, False):
        return None
    item.ihook.pytest_runtest_logstart(nodeid=item.nodeid, location=item.location)
    reports = runtestprotocol(item, log=False, nextitem=nextitem)
    initial = reports[-1]
    if item.stash.get(_CPU_ONLY_FAILURE, False) and all(report.passed for report in reports[:-1]):
        history = list(initial.user_properties)
        diagnostics = list(initial.sections)
        keys: list[pytest.StashKey[Any]] = [
            _CPU_TIMES,
            _CPU_EXCLUSIONS,
            _CPU_BUDGET,
            _CPU_PROCESS_STARTED,
            _BROWSER_REPORTS,
        ]
        for key in keys:
            if key in item.stash:
                del item.stash[key]
        reports = runtestprotocol(item, log=False, nextitem=nextitem)
        final = reports[-1]
        final.user_properties.extend((f"cpu_first_attempt_{name}", value) for name, value in history if name.startswith("cpu_"))
        final.sections.extend(("CPU初回超過", text) for name, text in diagnostics if name == "CPU上限")
    for report in reports:
        item.ihook.pytest_runtest_logreport(report=report)
    item.ihook.pytest_runtest_logfinish(nodeid=item.nodeid, location=item.location)
    return True


def pytest_sessionstart(session: pytest.Session) -> None:
    """共有初期化費用の観測をroot session単位で開始する。"""
    cpu_accounting.start(session)


def pytest_sessionfinish(session: pytest.Session) -> None:
    """自身が所有する観測を終了し、元のimportとcollectorへ戻す。"""
    cpu_accounting.stop(session)


def _measure_cpu(item: pytest.Item, phase: str) -> Generator[None, object, object]:
    budget = item.stash.setdefault(_CPU_BUDGET, cpu_accounting.CpuBudget())
    with cpu_accounting.activate(budget):
        excluded = cpu_accounting.main_snapshot()
        if _CPU_PROCESS_STARTED not in item.stash:
            item.stash[_CPU_PROCESS_STARTED] = time.process_time()
        started = time.thread_time()
        try:
            return (yield)
        finally:
            finished = time.thread_time()
            after = cpu_accounting.main_snapshot()
            item.stash.setdefault(_CPU_TIMES, {})[phase] = finished - started
            item.stash.setdefault(_CPU_EXCLUSIONS, {})[phase] = {key: after[key] - excluded[key] for key in ("gc", "import")}


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> Generator[None, object, object]:
    """fixtureの準備を含む、試験が所有するCPUを計測する。"""
    return (yield from _measure_cpu(item, "setup"))


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_call(item: pytest.Item) -> Generator[None, object, object]:
    """待機時間や子プロセスCPUを含めず本体を計測する。"""
    return (yield from _measure_cpu(item, "call"))


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_teardown(item: pytest.Item) -> Generator[None, object, object]:
    """回収が完了してからCPU合算の判定へ進む。"""
    return (yield from _measure_cpu(item, "teardown"))


def _tracker_state() -> tuple[int | None, float | None, bool]:
    """検査のためには起動せず、worker自身が保持するtrackerの世代だけを読む。"""
    pid = getattr(resource_tracker._resource_tracker, "_pid", None)  # pylint: disable=protected-access
    if pid is None:
        return None, None, False
    try:
        process = psutil.Process(pid)
        return (
            pid,
            process.create_time(),
            process.is_running() and process.status() not in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD),
        )
    except psutil.NoSuchProcess:
        return pid, None, False


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_protocol(item: pytest.Item, nextitem: pytest.Item | None) -> Generator[None, object, object]:
    """差し替えの復元後に検査し、trackerの誤停止を起こした試験へ帰属させる。"""
    del nextitem
    before = _tracker_state()
    result = yield
    after = _tracker_state()
    if (before[2] and (before[:2] != after[:2] or not after[2])) or (
        after[0] is not None and not after[2] and before[:2] != after[:2]
    ):
        report = pytest.TestReport(
            nodeid=item.nodeid,
            location=item.location,
            keywords={},
            outcome="failed",
            longrepr=f"resource_tracker生存検査: {item.nodeid}: before={before}, after={after}",
            when="teardown",
            sections=(),
            duration=0,
        )
        item.ihook.pytest_runtest_logreport(report=report)
    return result


@pytest.fixture(scope="session", name="browser_evidence_root")
def _browser_evidence_root() -> pathlib.Path:
    """試験単位のHOME隔離より前に、cleanup対象外の証拠保存先を固定する。"""
    root = (
        pathlib.Path(os.environ["RUNNER_TEMP"])
        if os.environ.get("CI") and os.environ.get("RUNNER_TEMP")
        else managed_temp.temp_root_path()
    )
    root.mkdir(parents=True, exist_ok=True)
    return root


@pytest_asyncio.fixture(scope="session", loop_scope="session", name="shared_serve_browser")
async def _shared_serve_browser() -> Any:
    """Chromiumの起動を共有し、各試験はcontextだけを所有する。"""
    async with playwright.async_api.async_playwright() as instance:
        browser = await instance.chromium.launch()
        try:
            yield browser
        finally:
            await browser.close()


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[Any]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    """fixture終了時にも本体の失敗理由を参照できるようにする。"""
    del call
    report = yield
    if report.when == "teardown":
        item.stash[_CPU_ONLY_FAILURE] = False
        recorded = item.stash.get(_CPU_TIMES, {})
        times = {phase: recorded.get(phase, 0.0) for phase in ("setup", "call", "teardown")}
        limit, reason = item.stash.get(_CPU_LIMIT, (0.5, "通常上限"))
        owner = item.stash.get(_CPU_BUDGET, None)
        # phase間も所有workerは動き得るため、完了reportで試験の累計を1回だけ加える。
        workers = cpu_accounting.snapshot(owner) if owner is not None else {"worker": 0.0, "gc": 0.0, "import": 0.0}
        total = sum(times.values()) + workers["worker"]
        process_start = item.stash.get(_CPU_PROCESS_STARTED, None)
        process_total = time.process_time() - process_start if process_start is not None else 0.0
        excluded = item.stash.get(_CPU_EXCLUSIONS, {})
        deductions = {kind: sum(row.get(kind, 0.0) for row in excluded.values()) + workers[kind] for kind in ("gc", "import")}
        budget = max(0.0, total - sum(deductions.values()))
        gross_limit = item.stash.get(_CPU_GROSS_LIMIT, max(2.0, limit))
        report.user_properties.extend(
            [(f"cpu_{phase}_seconds", times.get(phase, 0.0)) for phase in ("setup", "call", "teardown")]
            + [
                ("cpu_total_seconds", total),
                ("cpu_worker_seconds", workers["worker"]),
                ("cpu_process_seconds", process_total),
                ("cpu_unattributed_seconds", max(0.0, process_total - total)),
                ("cpu_excluded_gc_seconds", deductions["gc"]),
                ("cpu_excluded_import_seconds", deductions["import"]),
                ("cpu_budget_seconds", budget),
                ("cpu_limit_seconds", limit),
                ("cpu_gross_limit_seconds", gross_limit),
                ("cpu_limit_reason", reason),
            ]
        )
        if budget >= limit or total >= gross_limit:
            diagnostic = (
                f"CPU上限: {item.nodeid}: {times}, worker={workers['worker']:.6f}s, total={total:.6f}s, "
                f"process={process_total:.6f}s, excluded={deductions}, "
                f"budget={budget:.6f}s, limit={limit}s, gross_limit={gross_limit}s, reason={reason}。"
                "不要な処理を削減し、必要な実境界には理由付きcpu_limitを指定してください"
            )
            report.sections.append(("CPU上限", diagnostic))
            if item.stash.get(_CPU_RECORD_ONLY, False):
                item.warn(pytest.PytestWarning(diagnostic))
            elif report.passed:
                item.stash[_CPU_ONLY_FAILURE] = True
                report.outcome = "failed"
                report.longrepr = diagnostic
    assert isinstance(report.when, str)
    item.stash.setdefault(_BROWSER_REPORTS, {})[report.when] = report
    if report.when == "teardown" and (evidence := item.stash.get(_BROWSER_EVIDENCE, None)) is not None:
        failures = [result for result in item.stash[_BROWSER_REPORTS].values() if result.failed]
        if failures:
            try:
                (evidence / "failure.txt").write_text(
                    item.nodeid + "\n" + "\n".join(str(result.longrepr) for result in failures), encoding="utf-8"
                )
            except OSError as error:
                report.sections.append(("ブラウザー証拠保存", str(error)))
            if isinstance(terminal := item.config.pluginmanager.getplugin("terminalreporter"), TerminalReporter):
                terminal.write_line(f"ブラウザー失敗証拠: {evidence}")
        else:
            shutil.rmtree(evidence, ignore_errors=True)
    return report


_PACKAGE_ROOT = pathlib.Path(__file__).resolve().parents[1]
_ORIGINAL_WAIT_FOR_END_TURN = _transcript_scan.wait_for_end_turn

_FIXED_TERMINAL_WIDTH = 200  # list系出力の表示幅算出を決定論化するための固定端末幅（列数）


class _StubPluginReloader:
    """backend単体試験から外部CLI照会とファイル監視を分離する。"""

    updated = False

    def __init__(self, options: dict[str, Any], _loop: asyncio.AbstractEventLoop) -> None:
        options.setdefault("plugins", [{"type": "local", "path": str(claude.plugin_roots.SERVER_PLUGIN_ROOT)}])
        # 外部CLIを起動しないため、所有タスクへ引き渡す未回収の子孫は無い。
        self.unreclaimed: list[Any] = []

    async def prepare(self) -> None:
        """外部CLIと監視threadを起動しない。"""

    async def wait_for_change(self) -> None:
        """更新の試験は実クラスのIO境界だけを差し替える。"""
        await asyncio.Event().wait()

    async def reload_if_idle(self, _client: Any, _session: Any) -> None:
        """通常試験では再読込を要求しない。"""

    def request_reload(self) -> None:
        """通常試験では変更を通知しない。"""

    async def close(self, *, retain_input: bool = False) -> None:
        """外部資源を保持しない。"""
        del retain_input


@pytest.fixture(autouse=True)
def _isolate_claude_plugin_watch(monkeypatch: pytest.MonkeyPatch) -> None:
    """実監視の試験はclaude_pluginsの実クラスを明示的に使う。"""
    monkeypatch.setattr(claude_stream, "ClaudePluginReloader", _StubPluginReloader)


class _TestPollingObserver(watchdog.observers.polling.PollingObserver):
    """試験で`watchdog.observers.Observer`の代わりに使うObserver。実ファイルの変更を短い間隔の走査で検知し、OSの通知資源を確保しない。"""

    def __init__(self, timeout: float = 0.05, **kwargs: Any) -> None:
        super().__init__(timeout=timeout, **kwargs)


@pytest.fixture(autouse=True)
def _launch_follows_patched_subprocess_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """`subprocess.run`を差し替えた試験では、時間上限付きの起動も差し替え先へ渡す。

    gitの共通起動関数などは時間切れで子孫まで止めるため`subprocess.Popen`から起動し、`subprocess.run`を経由しない。
    `subprocess.run`の差し替えで外部コマンドを偽装する既存の試験が実物を起動しないよう、各モジュールの起動の
    1か所（`_launch`）で橋渡しする。差し替えていない試験は実物の起動関数を使う。
    """
    original_run = subprocess.run

    def follow(launch: Callable[..., Any]) -> Callable[..., Any]:
        def launch_or_patched_run(command: list[str], **kwargs: Any) -> Any:
            if subprocess.run is original_run:
                return launch(command, **kwargs)
            # checkは呼び出し元が渡す。
            return subprocess.run(command, **kwargs)  # noqa: PLW1510  # pylint: disable=subprocess-run-check

        return launch_or_patched_run

    for module in (_git_command, _orchestrator):
        # 起動の1か所を差し替えるための参照であり、各モジュールのdocstringがこの利用を明記している。
        monkeypatch.setattr(module, "_launch", follow(module._launch))  # pylint: disable=protected-access


@pytest.fixture(autouse=True)
def _observer_without_inotify(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """試験が使う`watchdog.observers.Observer`を、inotifyなどOSの通知資源を使わないポーリング監視へ差し替える。

    watchdogが標準で選ぶObserver（Linuxでは`InotifyObserver`）は監視先ごとにinotifyのインスタンスを確保し、
    その上限はOSアカウント全体で並行レーンの検証や常駐の`atk serve`と共有される。上限に達すると、
    変更と無関係な試験の合否が実行環境で変わる。OSの通知そのものや監視資源の解放を確かめる試験だけが
    `@pytest.mark.real_observer`を付けて実Observerを使い、その宣言が資源依存の一覧になる。
    ポーリング監視は更新時刻とサイズの差で変更を検知する。更新時刻が1秒単位のファイルシステム（ext3など）では、
    同じ秒の中で同じサイズのまま書き換えると検知しないため、変更を確かめる試験はサイズの異なる内容を書く。
    """
    if request.node.get_closest_marker("real_observer") is None:
        monkeypatch.setattr(watchdog.observers, "Observer", _TestPollingObserver)


def _target_plugin_root(config: pytest.Config) -> pathlib.Path | None:
    """公開pytest設定が対象にするagent-toolkit rootだけを返す。"""
    root = config.rootpath.resolve()
    if (root / "agent_toolkit" / "__init__.py").is_file():
        return root
    candidate = root / "agent-toolkit"
    return candidate if (candidate / "agent_toolkit" / "__init__.py").is_file() else None


def _validate_import_root(config: pytest.Config) -> None:
    """対象worktreeのpytestが別のagent-toolkitを読んだ状態を早期に拒否する。"""
    target = _target_plugin_root(config)
    if target is None:
        return
    loaded = pathlib.Path(agent_toolkit.__file__).resolve().parent.parent
    if loaded != target:
        raise pytest.UsageError(
            "agent_toolkitの読込元が対象worktreeと異なる: "
            f"loaded={loaded}, target={target}。PYTHONPATH と UV_PYTHON を解除し、"
            "対象worktreeをカレントディレクトリにして "
            "`uv run --frozen pytest <対象試験>` を再実行する。"
        )


_WAIT_SCHEDULE_ENVIRONMENT_NAMES = (
    "FORCE_PROMPT_CACHING_5M",
    "CLAUDE_CODE_PROMPT_CACHE_TTL",
    "CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL",
    "ENABLE_PROMPT_CACHING_1H",
    "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_MANTLE",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
    "CLAUDE_CODE_USE_ANTHROPIC_AWS",
)


def _notify_slot_wait(owner: execution_slot.SlotOwner | None) -> None:
    """実行枠の待機を始めたことを、保持者の情報とともに標準エラーへ1回出力する。

    出力が無いまま待つと、呼出元は待機と停止を区別できない。示す情報はpyfltrの`execution-slot`の警告とそろえる。
    """
    if owner is None:
        holder = "保持者の記録を読めない"
    else:
        holder = f"label={owner.label}、pid={owner.pid}、cwd={owner.cwd}、acquired_at={owner.acquired_at}"
    print(
        f"pytest: 同じOSアカウントで別の検証が実行枠を保持しているため（{holder}）、その解放を待ってから試験を実行する。"
        "待機時間は試験の処理時間に含まれない",
        file=sys.stderr,
        flush=True,
    )


def pytest_ignore_collect(collection_path: pathlib.Path, config: pytest.Config) -> bool | None:
    """`-m windows`の実行では、`windows` markerを書いていない試験モジュールを読み込まない。

    Windows CIの「Windows境界テスト」はリポジトリ全体から`windows` markerの試験を選ぶ。
    選ばれない試験モジュールにはPOSIX専用の値をimport時に使うものがあり、Windowsでは読み込むだけで収集が失敗するため、
    markerによる選択の前に、本文にmarkerの記述があるモジュールだけを読み込む。
    """
    if config.getoption("markexpr") != "windows" or not collection_path.name.endswith("_test.py"):
        return None
    return "pytest.mark.windows" not in collection_path.read_text(encoding="utf-8")


def reclaim_stale_pytest_fixtures() -> list[pathlib.Path]:
    """作成プロセスが終了した`pytest-fixtures-*`を回収し、回収したパスを返す。

    異常終了したpytestの領域は正常終了時の後始末が働かず、7日の期限まで残る。実行中のpytestの領域を
    消すと試験が壊れるため、作成プロセスの終了を確認できた領域に限る。識別の記録が無い領域は、
    作成途中か旧版の領域かを区別できないので期限の掃引へ任せる。
    """
    try:
        root = managed_temp.temp_root_path()
        candidates = sorted(root.glob("pytest-fixtures-*"))
    except OSError, managed_temp.ManagedTempError:
        return []
    reclaimed: list[pathlib.Path] = []
    for area in candidates:
        try:
            if (
                area.is_symlink()
                or not area.is_dir()
                or sweep.owner_alive((area / sweep.OWNER_FILE).read_text(encoding="ascii"))
            ):
                continue
            managed_temp.cleanup_managed_temp(area)
            reclaimed.append(area)
        except OSError, managed_temp.ManagedTempError:
            continue
    return reclaimed


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    """共有検証の所有と一時領域をpytestの寿命へ結び、配布CLIの依存環境の保存先を固定する。

    HOMEやキャッシュを一時ディレクトリへ向けたテストから起動すると、未指定ではテストごとに環境を構築し直す。
    保存先そのものを検証するテストは、明示した環境の辞書か`delenv`で上書きする。
    """
    _validate_import_root(config)
    config.stash[_CPU_RETRY_CI] = bool(os.environ.get("CI"))
    config.addinivalue_line("markers", "cpu_limit(seconds, reason): 理由付き所有CPU上限")
    if not config.pluginmanager.is_registered(xdist_stop):
        config.pluginmanager.register(xdist_stop, "dotfiles-xdist-stop")
    # 不正な分割指定は、xdistのworkerが収集を始める前に1回だけ報告する。
    if (shard := config.getoption("test_shard")) is not None:
        _parse_test_shard(shard)
    os.environ.setdefault(plugin_environment.RUNTIME_DATA_ENV, str(plugin_environment.runtime_data(os.environ)))
    # 試験が起動する子プロセスの`atk`が、実ディスクの空き不足で他の試験の領域を回収しないよう、環境変数で継承させて止める。
    os.environ.setdefault(sweep.MIN_FREE_RATIO_ENV, "0")
    stack = contextlib.ExitStack()
    config.add_cleanup(stack.close)
    worker = hasattr(config, "workerinput")
    # 最外側のpytestは開始から子孫の回収まで実行枠を保持し、試験の途中では待機者へ譲らない。
    # workerと入れ子の実行は、共有APIが祖先の所有を検証して継承する。
    # 収集だけの実行（`--collect-only`）は試験を実行せず、枠が守る外部ツールも起動しないため、枠を取らない。
    if not config.option.collectonly:
        stack.enter_context(
            execution_slot.hold(label="pytest", on_wait=_notify_slot_wait, should_abort=resource_diagnostics.slot_wait_probe())
        )
    previous_children = set(process_tree.collect_descendants(os.getpid()))
    resource_environment = {
        name: os.environ.get(name)
        for name in ("HOME", "USERPROFILE", "XDG_STATE_HOME", "XDG_CACHE_HOME", "LOCALAPPDATA", "APPDATA")
    }
    directory = None
    if not worker and config.option.basetemp is None:
        reclaim_stale_pytest_fixtures()
        directory = managed_temp.create_managed_temp("pytest-fixtures")
        (directory / sweep.OWNER_FILE).write_text(sweep.owner_token(), encoding="ascii")
        # pytestは開始時にbasetempを削除して作成し直すため、作成した領域の子を初期化対象にする。
        config.option.basetemp = str(directory / "fixtures")

    def release_resources() -> None:
        # multiprocessingのresource_trackerは同じインタープリターが終了まで使う補助プロセスであり、
        # pytestの終了で書込側のpipeが閉じると自ら終了する。先に停止すると、終了時の資源解放が
        # trackerの死亡を検出して再起動し、未解放の資源を報告する警告（`process died unexpectedly, relaunching`）を出力する。
        # 起動PIDは公開APIを持たないため、標準ライブラリの内部属性から読む。
        tracker = getattr(resource_tracker._resource_tracker, "_pid", None)  # pylint: disable=protected-access
        children = [
            child
            for child in process_tree.collect_descendants(os.getpid())
            if child not in previous_children and child.pid != tracker
        ]
        diagnostic_pids = [os.getpid(), *[child.pid for child in children]]
        before = [process_tree.identity(pid) for pid in diagnostic_pids] if sys.platform == "win32" else []
        if sys.platform == "win32":
            print(
                f"pytest資源回収開始: at={datetime.datetime.now(datetime.UTC).isoformat()} "
                f"pytest_pid={os.getpid()} owned={before}",
                file=sys.stderr,
                flush=True,
            )
        try:
            residual = process_tree.reclaim_descendants(children)
            if residual:
                raise pytest.UsageError(
                    "pytestの子孫を回収できない: " + "; ".join(residual) + "; 対象の終了を確認してから一時領域を回収する"
                )
            if directory is not None:
                # テストの隔離がHOME・キャッシュの場所を変えても、作成時と同じ一時rootで包含を判定する。
                with pytest.MonkeyPatch.context() as patch:
                    for name, value in resource_environment.items():
                        if value is None:
                            patch.delenv(name, raising=False)
                        else:
                            patch.setenv(name, value)
                    managed_temp.cleanup_managed_temp(directory)
        except Exception as error:
            # 診断の出力先が閉じている場合も、元の回収失敗をそのまま再送出する。
            with contextlib.suppress(Exception):
                resource_diagnostics.report_cleanup_failure(
                    error,
                    directory=directory,
                    before=before,
                    after=[process_tree.identity(pid) for pid in diagnostic_pids],
                )
            raise
        if sys.platform == "win32":
            print(
                f"pytest資源回収完了: at={datetime.datetime.now(datetime.UTC).isoformat()} pytest_pid={os.getpid()}",
                file=sys.stderr,
                flush=True,
            )

    stack.callback(release_resources)


def pytest_addoption(parser: pytest.Parser) -> None:
    """CPU上限の設定と、CIで1つの試験集合を複数runnerへ分ける`--test-shard`、CPU上限を記録だけにする指定を登録する。"""
    parser.addini("cpu_limit", "試験が所有するCPUの上限（秒）", default="0.5")
    parser.addoption(
        "--test-shard",
        metavar="INDEX/COUNT",
        default=None,
        help="収集した試験を記録所要でCOUNT個へ均衡配分し、INDEX番目（1始まり）だけを実行する",
    )
    parser.addoption(
        "--cpu-limit-record-only",
        action="store_true",
        help="CPU上限の超過を警告とJUnitへ記録するだけにし、試験の失敗と再試行にしない",
    )


def _parse_test_shard(value: str) -> tuple[int, int]:
    """`INDEX/COUNT`を検証し、0始まりの番号と分割数を返す。"""
    index_text, separator, count_text = value.partition("/")
    if separator and index_text.isdecimal() and count_text.isdecimal():
        index, count = int(index_text), int(count_text)
        if 1 <= index <= count:
            return index - 1, count
    raise pytest.UsageError(
        f"--test-shardの値が不正です: {value!r}。1以上COUNT以下のINDEXを`--test-shard=INDEX/COUNT`の形で指定してください"
    )


def _assign_test_shards(nodeids: list[str], durations: dict[str, float], count: int) -> dict[str, int]:
    """全workerで同じ入力から所要降順の貪欲割当を再現する。"""
    if not isinstance(durations, dict) or any(
        not isinstance(nodeid, str)
        or not nodeid
        or isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
        for nodeid, value in durations.items()
    ):
        raise pytest.UsageError("test_durations.jsonの所要が不正です。正の有限値で再生成してください")
    default = sum(durations.values()) / len(durations) if durations else 1.0
    totals = [0.0] * count
    assignments: dict[str, int] = {}
    for nodeid in sorted(nodeids, key=lambda nodeid: (-durations.get(nodeid, default), nodeid)):
        shard = min(range(count), key=lambda index: (totals[index], index))
        assignments[nodeid] = shard
        totals[shard] += durations.get(nodeid, default)
    return assignments


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """`--test-shard`の指定時は、担当外の試験を選択解除する。

    同じ所要記録とnode ID集合から担当を決め、収集順に依存しない。
    全分割を合わせると各試験がちょうど1回実行される。
    試験ファイル単位でなく試験単位で分けるのは、多数のparametrizeを持つ重いファイルを1つのrunnerへ偏らせないためである。
    """
    try:
        configured_limit = config.getini("cpu_limit")
        if isinstance(configured_limit, bool):
            raise ValueError("真偽値はCPU秒数でない")
        default_limit = float(configured_limit)
    except (TypeError, ValueError, OverflowError) as error:
        raise pytest.UsageError("cpu_limit設定には正の有限秒数を指定してください") from error
    if not math.isfinite(default_limit) or default_limit <= 0:
        raise pytest.UsageError("cpu_limit設定には正の有限秒数を指定してください")
    record_only = bool(config.getoption("cpu_limit_record_only"))
    for item in items:
        item.stash[_CPU_RECORD_ONLY] = record_only
        marker = item.get_closest_marker("cpu_limit")
        seconds: Any = default_limit
        reason = "通常上限"
        if marker is not None:
            if len(marker.args) != 1 or set(marker.kwargs) != {"reason"}:
                raise pytest.UsageError(f"{item.nodeid}: cpu_limit(seconds, reason=...) が必要")
            seconds, reason = marker.args[0], marker.kwargs["reason"]
        if (
            isinstance(seconds, bool)
            or not isinstance(seconds, (int, float))
            or abs(seconds) > sys.float_info.max
            or not math.isfinite(seconds)
            or seconds <= 0
            or not isinstance(reason, str)
            or not reason.strip()
        ):
            raise pytest.UsageError(f"{item.nodeid}: cpu_limitには正有限値と非空のreasonを指定してください")
        item.stash[_CPU_LIMIT] = (float(seconds), reason)
        # netから控除する共有初期化費用には、通常0.5/gross2と同じ余裕を重い試験にも残す。
        item.stash[_CPU_GROSS_LIMIT] = float(seconds) + 1.5 if marker is not None else max(2.0, default_limit)
    value = config.getoption("test_shard")
    if value is None:
        return
    index, count = _parse_test_shard(value)
    path = pathlib.Path(__file__).with_name("test_durations.json")
    durations = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    assignments = _assign_test_shards([item.nodeid for item in items], durations, count)
    selected: list[pytest.Item] = []
    deselected: list[pytest.Item] = []
    for item in items:
        shard = assignments[item.nodeid]
        (selected if shard == index else deselected).append(item)
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = selected


@pytest.hookimpl(wrapper=True)
def pytest_collection(session: pytest.Session) -> Generator[None, object, object]:
    """収集区間だけホームと設定ディレクトリをbasetemp配下へ向け、収集時にimportされる定数を実ホームから外す。

    試験関数ごとの隔離は収集の後に始まるため、収集時にimportされた製品モジュールが`Path.home()`などから
    確定した定数は、この区間が無いと実ホームを指す。セッション全体で差し替えない理由は`isolation`の説明を参照する。
    """
    basetemp = session.config.option.basetemp
    if basetemp is None:
        raise pytest.UsageError("収集時のホームを隔離するbasetempが未設定です。--basetempを指定して再実行してください")
    with isolation.import_time_home(pathlib.Path(basetemp) / "import-time-home"):
        return (yield)


@pytest.fixture(autouse=True)
def _reclaim_agents_server_logging(request: pytest.FixtureRequest) -> Iterator[None]:
    """診断ログ設定を呼んだテストのhandlerを、そのテストの終了時に回収してloggerの設定を戻す。

    `configure_logging`の標準エラー向けhandlerは構築時の`sys.stderr`（試験中はpytestの取り込み用stream）を保持する。
    回収しないと、streamが閉じた後も後続のテストの警告がそのhandlerへ届き、書込に失敗する。
    """
    if not request.path.is_relative_to(_PACKAGE_ROOT):
        yield
        return
    with isolated_agents_server_logging():
        yield


@pytest.fixture(autouse=True)
def _clear_wait_schedule_environment(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """TTL判定用の環境変数を各テストの実行環境から除去する。"""
    if not request.path.is_relative_to(_PACKAGE_ROOT):
        return
    for name in _WAIT_SCHEDULE_ENVIRONMENT_NAMES:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _single_execution_slot_in_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """試験が起動する検証を、実行枠1つの構成で動かす。

    pyfltr 3.30.0以降は、環境変数の指定が無ければ枠が2つあり、保持者1件の間にもう1件が待たずに実行枠を取る。
    実行枠の待ちと区間の直列を確かめる試験は、一時の保存先で保持者1件の後ろに後続が並ぶ構成を前提にする。
    外側のpytest自身の枠の取得は、試験より前の`pytest_configure`で済んでいるため、この指定の影響を受けない。
    """
    monkeypatch.setenv("PYFLTR_EXECUTION_SLOTS", "1")


@pytest.fixture(autouse=True)
def _reset_warning_context(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """通知の反復計数に使う`notice`モジュールの大域状態を各テストの開始時に初期状態へ戻す。

    本番の各hookは別プロセスで起動するため、この状態は起動ごとに初期値から始まる。
    同じプロセスで複数のテストを実行すると、先行テストが`set_warning_session_id`で設定した
    セッションIDが残り、後続テストの通知が反復として計数されて要約形へ置き換わる。
    結果が実行順序とworkerへの割り振りで変わるため、テストごとに初期値の辞書へ差し替える。
    """
    if not request.path.is_relative_to(_PACKAGE_ROOT):
        return
    monkeypatch.setattr(notice, "_warning_context", {"session_id": "", "blocks": []})


@pytest.fixture(autouse=True)
def _reset_api_credit_observations(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """APIクレジットの残高不足の観測を保持する`api_credits`の大域状態を各テストの開始時に空へ戻す。

    本番ではagents_serverのプロセスごとに空から始まる。同じプロセスで複数のテストを実行すると、
    先行テストが記録した観測が後続テストの委譲先の使用順を変えるため、テストごとに空の辞書へ差し替える。
    """
    if not request.path.is_relative_to(_PACKAGE_ROOT):
        return
    monkeypatch.setattr(api_credits, "_observations", {})


@pytest.fixture(autouse=True)
def _fixed_codex_model_catalog(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Codexの利用可能モデル一覧を固定値へ差し替え、実行環境の`codex` CLIの有無によらず同じ結果にする。

    工程別モデル設定で値を省略した場合の候補にはCodexの系列名が含まれ、系列の解決は`codex app-server`を起動して一覧を取得する。
    `codex`を導入していない継続的インテグレーションでは解決が失敗し、設定の解決を経由する
    process-loopなどのテストが対象外の理由で失敗する。一覧の内容や取得失敗を検証するテストは、
    モジュール側のfixtureやテスト内で改めて差し替える。子プロセスへは本差し替えが及ばないため、
    `atk`を別プロセスで起動するテストは系列名を含まない値を`AGENT_TOOLKIT_CONFIG_<キー>`で与える。
    """
    if not request.path.is_relative_to(_PACKAGE_ROOT):
        return
    catalog = [
        {
            "model": f"gpt-6-{family}",
            "supportedReasoningEfforts": [{"reasoningEffort": effort} for effort in ("low", "medium", "high", "xhigh")],
        }
        for family in sorted(codex_models.FAMILIES)
    ]
    monkeypatch.setattr(codex_models, "list_models", lambda: catalog)


@pytest.fixture(autouse=True)
def _skip_end_turn_wait(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop判定が終了を許可する`transcript_scan.wait_for_end_turn`を、待たずに戻る関数へ差し替える。

    本番の待機はtranscriptの最新のassistantエントリが`stop_reason`の`end_turn`になるまで最大0.3秒ポーリングする。
    テストが組み立てるtranscriptの多くは末尾を`end_turn`で終えないため、待機を検証しないテストでも上限まで待つ。
    待機そのものを検証するテストは`real_end_turn_wait`を要求して実物へ戻す。
    子プロセスで起動するStopフックへは本差し替えが及ばないため、そのテストでは末尾を`end_turn`で終えるtranscriptを渡す。
    """
    if not request.path.is_relative_to(_PACKAGE_ROOT):
        return
    monkeypatch.setattr(_transcript_scan, "wait_for_end_turn", lambda *_args, **_kwargs: None)


@pytest.fixture(autouse=True)
def _fixed_terminal_size(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """`shutil.get_terminal_size`を固定幅へ差し替え、実行環境の端末幅によらず同じ結果にする。

    `_atk/wi/listing.py`は`shutil.get_terminal_size()`から表示幅を算出し、
    `atk wi list`の出力を切り詰める。`shutil`モジュール自体を差し替えることで、
    このディレクトリ配下の全テストファイルへ一括で適用する
    （テストファイルごとに同じフィクスチャを定義しないため）。
    """
    if not request.path.is_relative_to(_PACKAGE_ROOT):
        return
    fixed = os.terminal_size((_FIXED_TERMINAL_WIDTH, 24))
    monkeypatch.setattr("shutil.get_terminal_size", lambda *_a, **_kw: fixed)


_TEST_WHOLE_CONTRACT_SESSION = "test-whole-contract-session"


@pytest.fixture(autouse=True)
def _resolvable_whole_contract(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """公開`start`の通常起動が、テスト用の会話の全体作業契約へ解決できる状態から始める。

    通常起動は全体作業契約を解決できなければ起動前に拒否する。隔離下では実行環境の会話の保存領域へ届かないため、
    起動時に保持する会話をテスト用の値へ固定し、その会話の保存領域と契約を最初の参照時に一時領域へ作成する。
    実行環境の`CLAUDE_CODE_SESSION_ID`などが結果を変えないようにする効果もある。
    解決順や拒否を検証するテストは、`launch_requests._HANDOFF_SESSION`か保存領域の解決を改めて差し替える。
    """
    if not request.path.is_relative_to(_PACKAGE_ROOT):
        return
    original = launch_requests._conversation_temp  # pylint: disable=protected-access
    created: list[pathlib.Path] = []

    def conversation_temp(session_id: str) -> pathlib.Path | None:
        if session_id != _TEST_WHOLE_CONTRACT_SESSION:
            return original(session_id)
        if not created:
            directory = tmp_path_factory.mktemp("whole-contract")
            (directory / "work-contract.md").write_text("目的: テスト用の全体要求\n", encoding="utf-8")
            created.append(directory)
        return created[0]

    monkeypatch.setattr(launch_requests, "_HANDOFF_SESSION", (_TEST_WHOLE_CONTRACT_SESSION,))
    monkeypatch.setattr(launch_requests, "_conversation_temp", conversation_temp)


@pytest.fixture(name="real_end_turn_wait")
def _real_end_turn_wait(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """自動fixtureの差し替えを外し、対象内で実物の待機を使わせる。"""
    if request.path.is_relative_to(_PACKAGE_ROOT):
        monkeypatch.setattr(_transcript_scan, "wait_for_end_turn", _ORIGINAL_WAIT_FOR_END_TURN)


@pytest.fixture(autouse=True)
def _roomy_disk_for_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    """掃引の空き判定を実ディスクから隔離する。実機の空きが下限を割っていても、他のテストの一時領域を回収させない。"""
    monkeypatch.setattr(sweep, "_disk_usage", lambda _path: shutil._ntuple_diskusage(1000, 0, 1000))  # pylint: disable=protected-access


@pytest.fixture(autouse=True)
def isolated_state_root(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """一時rootと状態rootを各テストの専用領域へ分離する。"""
    if not request.path.is_relative_to(_PACKAGE_ROOT / "_atk" / "managed_temp"):
        return
    cache_root = tmp_path / "managed-temp"
    cache_root.mkdir(mode=0o700)
    setattr_in_managed_temp_modules(monkeypatch, "_state_root_path", lambda: tmp_path / "external-state")
    setattr_in_managed_temp_modules(monkeypatch, "_temp_root_path", lambda: cache_root)


@pytest.fixture(autouse=True)
def _isolate_environment(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """編集テストをホスト側のエージェント環境と一時rootから隔離する。"""
    if not request.path.is_relative_to(_PACKAGE_ROOT / "_atk" / "wi" / "mutations"):
        return
    for name in _AGENT_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    temp_root = tmp_path / "temp"
    temp_root.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(temp_root))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


async def _skipped_plugin_preflight(_cwd: str) -> None:
    """事前確認を省く。イベントループへ制御を返さず、従来の起動と再開の順序を保つ。"""


async def _forbidden_backend_process(*args: Any, **_kwargs: Any) -> Any:
    """実backendの子プロセス起動を拒否し、差し替えていない呼び出しを実行環境によらず失敗させる。"""
    raise _backend_process_forbidden(args)


def _backend_process_forbidden(args: tuple[Any, ...]) -> AssertionError:
    """実backendの起動時に共通して返す例外を生成する。"""
    return AssertionError(f"テストが実backendの子プロセスを起動しようとしました: {args}")


def _forbidden_claude_client(*args: Any, **_kwargs: Any) -> Any:
    """同期factoryからClaude SDK clientを返さず、実backend起動を拒否する。"""
    raise _backend_process_forbidden(args)


@pytest.fixture
def _skip_plugin_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    """起動と再開の事前確認を通常は省き、実コマンドの所要時間とスレッド切替を各テストの時間制約へ持ち込まない。

    事前確認そのものを確かめるテストは`_use_real_plugin_preflight`で実装へ戻す。
    """
    monkeypatch.setattr(launch_requests, "check_plugin_commands", _skipped_plugin_preflight)


@pytest.fixture
def _forbid_backend_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """全engineの実backendが実CLIを起動する処理を通常は拒否する。

    backendを差し替えたengineが候補のengineと一致しないと、実backendが`codex app-server`などを起動する。
    CLIを導入した開発機ではそれが成功して欠陥が隠れ、導入していないCIだけが失敗するため、
    起動そのものを失敗させて両環境の結果をそろえる。起動引数を確かめるテストはテスト内で改めて差し替える。
    """
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _forbidden_backend_process)
    monkeypatch.setattr(claude.ClaudeServerManager, "_default_client_factory", _forbidden_claude_client)


@pytest.fixture
def _short_start_availability_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """起動直後の終了確認を短縮し、実行中のまま返るsessionでテストを待たせない。"""
    monkeypatch.setattr(server_manager, "START_AVAILABILITY_TIMEOUT", 0.05)


@pytest.fixture
def _immediate_wait_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """待機上限の導出を省き、未終了sessionの待機でテストを待たせない。

    `wait`は待機上限を入力として受け取らないため、上限の導出だけをテスト用の値へ差し替える。
    """
    monkeypatch.setattr(_wait_schedule, "get_wait_timeout", lambda request_bucket: 0.0)


@pytest.fixture
def _owner_session_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """所有セッションの解決に使う環境変数を未設定の状態から始める。"""
    for name in isolation.DEVELOPMENT_SESSION_ENVIRONMENT_NAMES:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def agents_server_isolation(
    monkeypatch: pytest.MonkeyPatch,
    _skip_plugin_preflight: None,
    _forbid_backend_process: None,
    _restore_session_listeners: None,
    _short_start_availability_timeout: None,
    _immediate_wait_timeout: None,
) -> None:
    """agents_serverのmanagerとMCPツールのテストを、事前確認・子プロセス・共有リスナー・待機時間から切り離す。"""
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-subscription")
    # 起動時の利用枠の再取得は、実行するマシンの設定とキャッシュの鮮度によって実のcodex app-serverを起動する。
    # 試験の結果をそのマシンの利用枠の状態に左右させないよう、自動配分を無効にする。
    monkeypatch.setenv(config_settings.config_env_name("usage_auto_allocation"), "false")


@pytest.fixture(name="index_path")
def _index_path(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """作成日時インデックスを一時ディレクトリへ隔離する。"""
    path = tmp_path / "cache" / "index.json"
    monkeypatch.setattr(plan_creation_times, "_CREATION_TIME_INDEX_PATH", path)
    monkeypatch.setattr(plan_creation_times, "observed_creation_epoch", lambda st: float(st.st_mtime))
    return path


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


@pytest.fixture(name="make_clean_repo")
def _make_clean_repo() -> Callable[[pathlib.Path], pathlib.Path]:
    """変更なしのgitリポジトリを作成するfactory fixture。"""

    def _make(tmp_path: pathlib.Path, name: str = "clean") -> pathlib.Path:
        return _git_repository.init_repository(tmp_path / name, files={"file.txt": "clean"}, commit_message="init")

    return _make


# fixture定義を起動時の名前空間へ登録するため、実装名を参照する。
# pylint: disable=protected-access
session_environment = agents_server_fixtures.session_environment
diagnostic_log = agents_server_fixtures._diagnostic_log
codex_stable_plugin_roots = agents_server_fixtures._isolate_stable_plugin_roots
provider_environment = agents_server_fixtures._configure_provider_environment
_wait_environment = agents_server_fixtures._wait_environment
_restore_session_listeners = agents_server_fixtures._restore_session_listeners
leaf_git_commands = _git_repository.leaf_git_commands
_without_skill_notices = hook_support._without_skill_notices
