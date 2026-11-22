"""取得キャッシュの整理の周期、結果の報告、独立プロセスの起動を確かめる。"""

# pylint: disable=protected-access  # 対象分類・削除境界の内部関数を隔離環境で検証する。

import json
import logging
import os
import shutil
import subprocess
import sys
import time
import types
import typing
from pathlib import Path

import filelock
import psutil
import pytest
from agent_toolkit._common import process_tree, windows_job

from pytools import post_apply
from pytools._internal import (
    common,
    post_apply_outcome,
    prune_installation,
    setup_mise,
    update_log,
    warmup_pyfltr_mcp,
)
from pytools._internal.prune_installation_test_helpers import UV_CACHE_MAINTENANCE, maintain, prepare, state_path

_REAL_RESOLVE_EXECUTABLE = common.resolve_executable
_REAL_RUN_SUBPROCESS = common.run_subprocess
_REAL_START_UV_CACHE_MAINTENANCE = prune_installation.start_cache_maintenance
_UV_CACHE_MAINTENANCE = ["uv-cache-maintenance"]
_HOLDER_SCRIPT = "import sys; print('ready', flush=True); sys.stdin.read()"


def test_full_cache_cleanup_tracks_success_per_root_and_30_day_boundary(tmp_path, monkeypatch):
    """初回・30日境界だけ全消去し、同日と29日目はpruneする。"""
    _, calls = prepare(tmp_path, monkeypatch, "full")
    now = 10000000.0
    monkeypatch.setattr(prune_installation.time, "time", lambda: now)
    completed = (prune_installation.MaintenanceResult.COMPLETED, "")
    assert prune_installation.clean_uv_cache() == completed
    assert calls.count(["/fixture/uv", "cache", "clean"]) == 1
    assert prune_installation.clean_uv_cache() == completed
    now += 29 * 86400
    assert prune_installation.clean_uv_cache() == completed
    assert calls.count(["/fixture/uv", "cache", "clean"]) == 1
    assert calls.count(["/fixture/uv", "cache", "prune"]) == 2
    now += 86400
    assert prune_installation.clean_uv_cache() == completed
    assert calls.count(["/fixture/uv", "cache", "clean"]) == 2
    assert not any("--force" in command for command in calls if "uv" in command[0])


@pytest.mark.parametrize("locked", [False, True])
def test_uv_cleanup_failure_preserves_success_time(tmp_path, monkeypatch, locked):
    """競合は保留、それ以外は失敗として区別し、どちらも成功日時を記録せず次回に全消去を再試行する。"""
    _, calls = prepare(tmp_path, monkeypatch, "full")
    normal = common.run_subprocess
    failing = True

    def execute(command, **kwargs):
        if command[1:] == ["cache", "clean"] and failing:
            calls.append(command)
            return subprocess.CompletedProcess(command, 2, "", "when waiting for lock on cache" if locked else "failed")
        return normal(command, **kwargs)

    monkeypatch.setattr(common, "run_subprocess", execute)
    results = prune_installation.MaintenanceResult
    result, _detail = prune_installation.clean_uv_cache()
    assert result is (results.DEFERRED if locked else results.FAILED)
    failing = False
    assert prune_installation.clean_uv_cache()[0] is results.COMPLETED
    assert calls.count(["/fixture/uv", "cache", "clean"]) == 2


@pytest.mark.windows
def test_update_does_not_wait_for_uv_cache_in_use(tmp_path, monkeypatch, host_environ, request):
    """他のuvが使用中でも整理を待たずに戻り、使用の終了後にuvの全消去と成功記録を完了する。"""
    native_path = os.pathsep.join(part for part in host_environ()["PATH"].split(os.pathsep) if Path(part).name != "shims")
    uv = shutil.which("uv", path=native_path)
    if uv is None:
        pytest.skip("uvが無い環境では実際のキャッシュロックを再現できない")
    which = shutil.which
    prepare(tmp_path, monkeypatch, "full")
    # 独立プロセスは回収の前に自然に終了し得る。Windowsは終了後のPIDの世代を後から確かめられないため、
    # 起動直後から回収まで同じ世代のhandleを保持し、終了の前後どちらでも同じ所有として回収する。
    pins = process_tree.ProcessIdentityPins()
    request.addfinalizer(pins.close)
    maintenance: list[psutil.Process] = []

    def start() -> int:
        pid = _REAL_START_UV_CACHE_MAINTENANCE()
        maintenance.extend(process_tree.collect_descendants(pid, pins=pins, include_root=True))
        return pid

    monkeypatch.setattr(prune_installation, "start_cache_maintenance", start)
    # 隔離PATHはmise shimを別名で置くため、整理側が解決するuvをshimを経由しない本体にそろえる。
    monkeypatch.setenv("PATH", os.pathsep.join((str(Path(uv).parent), os.environ["PATH"])))
    monkeypatch.setattr(common, "resolve_executable", _REAL_RESOLVE_EXECUTABLE)
    monkeypatch.setattr(common, "run_subprocess", _REAL_RUN_SUBPROCESS)
    monkeypatch.setattr(setup_mise, "find_mise_binary", lambda: None)
    monkeypatch.setattr(shutil, "which", lambda name, path=None: None if name in {"npm", "pnpm"} else which(name, path=path))
    cache = tmp_path / "uv-cache"
    marker = cache / "archive-v0" / "previous-entry"
    marker.mkdir(parents=True)
    monkeypatch.setenv("UV_CACHE_DIR", str(cache))
    monkeypatch.setenv("UV_PYTHON_DOWNLOADS", "never")
    # 実行中の`uv run`はキャッシュの共有ロックを終了まで保持する。
    # 時間上限なし: 標準入力の終了で止まる保持役であり、終了時のcloseで止まる。
    with subprocess.Popen(
        [uv, "run", "--no-project", "--python", sys.executable, "python", "-c", _HOLDER_SCRIPT],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    ) as holder:
        assert holder.stdout is not None and holder.stdout.readline().strip() == "ready"
        started = time.monotonic()
        result = prune_installation.run()
        elapsed = time.monotonic() - started
    assert elapsed < 10, f"使用中のuvキャッシュの整理を{elapsed:.1f}秒待った"
    assert result.failure is None
    record = state_path("uv-cache-maintenance.json")
    # 試験全体の時間上限（`--timeout=60`）より先に期限を迎え、未完了を期限切れでなく診断付きの失敗で示す。
    deadline = time.monotonic() + 30
    while not record.exists() and time.monotonic() < deadline:
        time.sleep(0.2)
    assert record.exists(), "独立プロセスが30秒以内に結果を記録しなかった"
    assert json.loads(record.read_text(encoding="utf-8"))["result"] == "completed"
    assert os.path.normcase(str(cache.resolve())) in json.loads(state_path("uv-cache-clean.json").read_text(encoding="utf-8"))
    assert not marker.exists()
    # 独立プロセスは記録の後もuv以外の整理とMCPの事前構築を続ける。どちらもこの試験の対象外で、
    # 所要は実行環境の負荷と導入済みのツールで変わる（Windows CIで記録の後20秒を超えた）。
    # 自然な終了を待たずに回収し、独立プロセスを試験の外へ残さない。
    assert maintenance, "起動直後の独立プロセスの世代を保持できなかった"
    stopped = process_tree.stop_tree(None, known=maintenance, pins=pins)
    assert not stopped.residual and not stopped.unresolved, stopped.descriptions()


def _uv_clean_failure(monkeypatch: pytest.MonkeyPatch, calls: list[list[str]]) -> dict[str, str | None]:
    """uvの全消去・pruneを、返す辞書の`stderr`の診断で失敗させる。`None`の間は成功させる。"""
    normal = common.run_subprocess
    failure: dict[str, str | None] = {"stderr": None}

    def execute(command, **kwargs):
        stderr = failure["stderr"]
        if Path(command[0]).stem == "uv" and command[1:] in (["cache", "clean"], ["cache", "prune"]) and stderr is not None:
            calls.append(command)
            return subprocess.CompletedProcess(command, 2, "", stderr)
        return normal(command, **kwargs)

    monkeypatch.setattr(common, "run_subprocess", execute)
    return failure


def test_failed_maintenance_fails_following_updates_until_resolved(tmp_path, monkeypatch):
    """独立プロセスの失敗は、解消するまで次の更新の失敗として診断・次の操作・永続ログのパスとともに返す。

    保留は未解消の失敗を上書きしない。修正前は失敗が永続ログにだけ残り、更新の画面と終了コードから消えていた。
    """
    _, calls = prepare(tmp_path, monkeypatch, "full")
    failure = _uv_clean_failure(monkeypatch, calls)
    failure["stderr"] = "error: fixture disk failure"
    maintain(monkeypatch, tmp_path)
    failed = prune_installation.run()
    assert failed.failure is not None and "fixture disk failure" in failed.failure
    assert failed.next_action is not None and str(update_log.LOG_PATH) in failed.next_action
    failure["stderr"] = "error: Timeout (30s) when waiting for lock on cache"
    maintain(monkeypatch, tmp_path)
    still_failed = prune_installation.run()
    assert still_failed.failure is not None and "fixture disk failure" in still_failed.failure
    # 保留は整理を試していないため失敗を残し、最後の試行が保留だったことと、その診断を添える。
    assert "保留" in still_failed.failure and "when waiting for lock" not in still_failed.failure
    failure["stderr"] = None
    maintain(monkeypatch, tmp_path)
    assert prune_installation.run().failure is None


def test_deferred_maintenance_preserves_corrupt_record(tmp_path, monkeypatch, capsys):
    _, calls = prepare(tmp_path, monkeypatch, "full")
    _uv_clean_failure(monkeypatch, calls)["stderr"] = "when waiting for lock on cache"
    state = state_path("uv-cache-maintenance.json")
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text("{", encoding="utf-8")

    maintain(monkeypatch, tmp_path)
    with pytest.raises(SystemExit) as exited:
        post_apply.main(["--prune-only"], runner=lambda: ([], []))

    assert exited.value.code == 1
    assert "整理の独立プロセスの結果を読めません" in capsys.readouterr().err


def test_npm_failure_is_reported_next_update_even_when_uv_defers(tmp_path, monkeypatch, caplog):
    _, calls = prepare(tmp_path, monkeypatch, "full")
    _uv_clean_failure(monkeypatch, calls)["stderr"] = "when waiting for lock on cache"
    run = common.run_subprocess

    def fail_verify(command, **kwargs):
        if command[1:] == ["cache", "verify"]:
            return subprocess.CompletedProcess(command, 1, "", "fixture verify failed")
        return run(command, **kwargs)

    monkeypatch.setattr(common, "run_subprocess", fail_verify)
    assert prune_installation.run().failure is None
    maintain(monkeypatch, tmp_path)
    failed = prune_installation.run()
    assert failed.failure and "fixture verify failed" in failed.failure
    assert ["/fixture/pnpm", "store", "prune"] in calls
    monkeypatch.setattr(common, "run_subprocess", run)
    maintain(monkeypatch, tmp_path)
    with caplog.at_level(logging.INFO):
        assert prune_installation.run().failure is None
    assert "mise・npm・pnpmのキャッシュ整理は完了" in caplog.text


def test_corrupt_clean_record_is_reported_with_its_path(tmp_path, monkeypatch):
    """全消去の成功記録が壊れると整理は毎回失敗するため、次の更新は記録のパスを示して失敗を返す。"""
    prepare(tmp_path, monkeypatch, "full")
    state = state_path("uv-cache-clean.json")
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text("{", encoding="utf-8")
    maintain(monkeypatch, tmp_path)
    result = prune_installation.run()
    assert result.failure is not None and str(state) in result.failure


@pytest.mark.skipif(sys.platform != "linux", reason="Linuxのprocfsでロックの保持者を確認する")
def test_uv_lock_holder_in_persistent_log_only(tmp_path, monkeypatch, capsys, host_environ):
    monkeypatch.setenv("HOME", str(tmp_path))
    path = os.pathsep.join(part for part in host_environ()["PATH"].split(os.pathsep) if Path(part).name != "shims")
    uv = shutil.which("uv", path=path)
    assert uv is not None
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(prune_installation, "_UV_LOCK_TIMEOUT_SECONDS", "1")
    log = tmp_path / "update-dotfiles.log"
    monkeypatch.setattr(post_apply, "_UPDATE_LOG_PATH", log)
    for flags in ([], ["--no-cache"]):
        command = [uv, "run", *flags, "--no-project", "--python", sys.executable, "python", "-c", _HOLDER_SCRIPT]
        with subprocess.Popen(command, cwd=tmp_path, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True) as holder:
            try:
                assert holder.stdout is not None and holder.stdout.readline().strip() == "ready"
                with post_apply._configure_logging():
                    if flags:
                        prune_installation._clean_uv_cache(Path(uv))
                    else:
                        with pytest.raises(prune_installation._InUseError):
                            prune_installation._clean_uv_cache(Path(uv))
                        assert f"保持者: PID {holder.pid} (" in log.read_text(encoding="utf-8")
            finally:
                assert holder.stdin is not None
                holder.stdin.close()
    screen = capsys.readouterr()
    assert not screen.out and not screen.err


def test_deferred_maintenance_is_announced_with_log_path(tmp_path, monkeypatch, capsys):
    """使用中で保留した独立プロセスの結果は、次の更新が失敗にせず、永続ログのパスとともに案内する。"""
    _, calls = prepare(tmp_path, monkeypatch, "full")
    detail = "Cache is currently in-use\nerror: Timeout (30s) when waiting for lock on cache"
    _uv_clean_failure(monkeypatch, calls)["stderr"] = detail
    maintain(monkeypatch, tmp_path)
    capsys.readouterr()
    with post_apply._configure_logging():
        result = prune_installation.run()
    assert result.failure is None
    screen = capsys.readouterr()
    assert not screen.err
    assert screen.out.splitlines() == [
        f"  prune: uvキャッシュが使用中のため整理を保留しました。整理は次回の更新時に再試行します。詳細: {update_log.LOG_PATH}"
    ]
    assert detail in (tmp_path / "update-dotfiles.log").read_text(encoding="utf-8")


def test_unexpected_maintenance_exception_is_logged_and_reported(tmp_path, monkeypatch):
    """独立プロセスの想定外の例外は、出力を持たないプロセスから永続ログと次の更新の失敗へ届く。"""
    prepare(tmp_path, monkeypatch, "full")

    def explode(_uv: Path) -> None:
        raise KeyError("fixture-unexpected")

    monkeypatch.setattr(prune_installation, "_clean_uv_cache", explode)
    log = maintain(monkeypatch, tmp_path)
    assert "Traceback" in log and "KeyError: 'fixture-unexpected'" in log
    result = prune_installation.run()
    assert result.failure is not None and "fixture-unexpected" in result.failure


def test_slow_verify_does_not_block_next_update_or_start_duplicate_cleanup(tmp_path, monkeypatch):
    _, calls = prepare(tmp_path, monkeypatch, "full")
    run = common.run_subprocess

    def check_while_verifying(command, **kwargs):
        if command[1:] == ["cache", "verify"]:
            update_lock = filelock.FileLock(str(state_path("locks/update-dotfiles.lock")), timeout=0, preserve_lock_file=True)
            assert prune_installation.run(lock=update_lock).failure is None
            prune_installation.maintain_caches()
        return run(command, **kwargs)

    monkeypatch.setattr(common, "run_subprocess", check_while_verifying)
    maintain(monkeypatch, tmp_path)
    assert calls.count(["/fixture/npm", "cache", "verify"]) == 1
    assert calls.count(["/fixture/mise", "cache", "prune"]) == 1
    assert json.loads(state_path("tools-cache-maintenance.json").read_text(encoding="utf-8"))["result"] == "completed"


def test_maintenance_defers_while_update_is_running(tmp_path, monkeypatch):
    """更新が更新ロックを保持する間、独立プロセスはロックを待たずに整理を保留し、uvの排他操作を重ねない。"""
    _, calls = prepare(tmp_path, monkeypatch, "full")
    lock = state_path("locks/update-dotfiles.lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with filelock.FileLock(str(lock), timeout=0, preserve_lock_file=True):
        maintain(monkeypatch, tmp_path)
    assert time.monotonic() - started < 5
    assert not [command for command in calls if command[1:3] in (["cache", "clean"], ["cache", "prune"])]
    record = json.loads(state_path("uv-cache-maintenance.json").read_text(encoding="utf-8"))
    assert record["result"] == "deferred"


def test_prune_after_update_starts_maintenance_after_releasing_update_lock(tmp_path, monkeypatch):
    """更新後の整理は、更新ロックを解放してから独立プロセスを開始し、独立プロセスがロックを取得できる。"""
    _, calls = prepare(tmp_path, monkeypatch, "full")
    monkeypatch.setattr(post_apply, "_UPDATE_LOG_PATH", tmp_path / "update-dotfiles.log")
    acquirable: list[bool] = []

    def start() -> int:
        try:
            with filelock.FileLock(str(state_path("locks/update-dotfiles.lock")), timeout=0, preserve_lock_file=True):
                acquirable.append(True)
        except filelock.Timeout:
            acquirable.append(False)
        return 4242

    monkeypatch.setattr(prune_installation, "start_cache_maintenance", start)
    post_apply.main(["--prune-only-if-minimal"], runner=lambda: pytest.fail("通常の更新工程へ到達した"))
    assert acquirable == [True]
    assert ["/fixture/mise", "cache", "prune"] not in calls


@pytest.mark.parametrize("cause", ["installation", "start"])
def test_mcp_environment_is_prepared_without_maintenance_process(tmp_path, monkeypatch, cause):
    """独立プロセスを開始しない・できない場合も、キャッシュ内のMCP環境をこのプロセスで事前構築する。"""
    _, calls = prepare(tmp_path, monkeypatch, "full")
    warmups: list[str] = []

    def warmup() -> post_apply_outcome.PostApplyOutcome:
        warmups.append("warmup")
        return post_apply_outcome.PostApplyOutcome()

    monkeypatch.setattr(warmup_pyfltr_mcp, "run", warmup)
    if cause == "installation":
        monkeypatch.setattr(common, "find_dotfiles_root", lambda: None)
    else:

        def refuse() -> int:
            raise PermissionError("fixture")

        monkeypatch.setattr(prune_installation, "start_cache_maintenance", refuse)
    result = prune_installation.run()
    assert result.failure is not None
    assert warmups == ["warmup"]
    if cause == "installation":
        # 導入実体の整理に失敗した場合は、従来どおり取得キャッシュを整理しない。
        assert not calls
    else:
        assert "独立プロセスを起動できません" in result.failure
        assert not [command for command in calls if command[1:2] in (["cache"], ["store"])]


def test_missing_uv_still_prepares_mcp_environment_in_maintenance_process(tmp_path, monkeypatch):
    """uvを解決できない更新でも独立プロセスを開始し、独立プロセスは対象なしを記録してMCP環境を事前構築する。"""
    _, calls = prepare(tmp_path, monkeypatch, "full")
    monkeypatch.setattr(common, "resolve_executable", lambda name: None)
    assert prune_installation.run().failure is None
    assert UV_CACHE_MAINTENANCE in calls
    warmups: list[str] = []

    def warmup() -> post_apply_outcome.PostApplyOutcome:
        warmups.append("warmup")
        return post_apply_outcome.PostApplyOutcome()

    monkeypatch.setattr(warmup_pyfltr_mcp, "run", warmup)
    maintain(monkeypatch, tmp_path)
    assert warmups == ["warmup"]
    assert json.loads(state_path("uv-cache-maintenance.json").read_text(encoding="utf-8"))["result"] == "none"


def test_breakaway_refusal_is_reported(tmp_path, monkeypatch, caplog):
    """起動元のWindows Jobに離脱を拒まれ、離脱なしで独立プロセスを開始した場合は警告で記録する。"""
    prepare(tmp_path, monkeypatch, "full")
    started: list[int] = []

    def popen(_command, **options):
        started.append(options["creationflags"])
        if options["creationflags"] & windows_job.CREATE_BREAKAWAY_FROM_JOB:
            raise PermissionError(5, "アクセスが拒否されました")
        return types.SimpleNamespace(pid=4242, wait=lambda: 0)

    monkeypatch.setattr(prune_installation, "start_cache_maintenance", _REAL_START_UV_CACHE_MAINTENANCE)
    monkeypatch.setattr(prune_installation, "os", types.SimpleNamespace(name="nt"))
    monkeypatch.setattr(prune_installation, "subprocess", types.SimpleNamespace(Popen=popen, DEVNULL=subprocess.DEVNULL))
    with caplog.at_level(logging.WARNING):
        assert prune_installation.start_cache_maintenance() == 4242
    assert len(started) == 2 and not started[1] & windows_job.CREATE_BREAKAWAY_FROM_JOB
    assert any("離脱を許可しない" in record.getMessage() for record in caplog.records)


@pytest.mark.windows
@pytest.mark.skipif(sys.platform != "win32", reason="Windows Jobの終了が所属プロセスを止める条件を実APIで確かめる")
@pytest.mark.timeout(240)
def test_maintenance_outlives_killing_job_of_its_launcher(tmp_path, monkeypatch, host_environ, request):
    """離脱を許すKILL_ON_JOB_CLOSEのJobの中から開始したuvの整理は、そのJobの終了後も続いて完了を記録する。

    更新jobとprocess-loopは、起動した子孫をこの種類のJobで所有し、終了時にまとめて止める。
    """
    native_path = os.pathsep.join(part for part in host_environ()["PATH"].split(os.pathsep) if Path(part).name != "shims")
    uv = shutil.which("uv", path=native_path)
    assert uv is not None
    prepare(tmp_path, monkeypatch, "minimal")
    cache = tmp_path / "uv-cache"
    marker = cache / "archive-v0" / "previous-entry"
    marker.mkdir(parents=True)
    monkeypatch.setenv("UV_CACHE_DIR", str(cache))
    monkeypatch.setenv("UV_PYTHON_DOWNLOADS", "never")
    monkeypatch.setenv("PATH", os.pathsep.join((str(Path(uv).parent), os.environ["PATH"])))
    record = state_path("uv-cache-maintenance.json")
    starter = "from pytools._internal import prune_installation as p; print(p.start_cache_maintenance(), flush=True)"
    pins = process_tree.ProcessIdentityPins()
    request.addfinalizer(pins.close)
    # 実行中の`uv run`はキャッシュの共有ロックを終了まで保持し、独立プロセスの全消去をJobの終了後まで待たせる。
    # 時間上限なし: 標準入力の終了で止まる保持役であり、終了時のcloseで止まる。
    with subprocess.Popen(
        [uv, "run", "--no-project", "--python", sys.executable, "python", "-c", _HOLDER_SCRIPT],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    ) as holder:
        assert holder.stdout is not None and holder.stdout.readline().strip() == "ready"
        members: list[subprocess.Popen[str]] = []
        try:
            with windows_job.WindowsJob() as job:

                def member(command: list[str]) -> subprocess.Popen[str]:
                    # 時間上限なし: Jobの終了で止まる所属プロセスであり、終了はfinallyで回収する。
                    # pylint: disable-next=consider-using-with
                    process = subprocess.Popen(
                        command, stdout=subprocess.PIPE, text=True, creationflags=windows_job.CREATE_SUSPENDED
                    )
                    members.append(process)
                    # _handleはWindows版CPythonの属性であり、Linux上の型検査からは解決できない。
                    job.assign_suspended(
                        int(typing.cast(typing.Any, process)._handle),  # pylint: disable=protected-access,no-member
                        process.pid,
                    )
                    return process

                launcher = member([sys.executable, "-c", starter])
                unbroken = member([sys.executable, "-c", "import time; time.sleep(600)"])
                output, _ = launcher.communicate(timeout=60)
                assert launcher.returncode == 0, output
                maintenance = psutil.Process(int(output.strip()))
                # 保持役のロックで整理を待っている間に独立プロセスの世代を保持し、記録の後に同じ所有として回収する。
                owned = process_tree.collect_descendants(maintenance.pid, pins=pins, include_root=True)
                assert owned, "独立プロセスの世代を保持できなかった"

                def reap_maintenance() -> None:
                    # 独立プロセスは記録の後もuv以外の整理を続け、一時領域のロックを保持する。
                    # この試験の対象外で、所要は負荷で変わる（Windows CIで試験の終了から55秒後もロックを保持し、
                    # pytestの一時領域の回収が失敗した）。試験の成否によらず`pins.close`より先に回収し、残存を報告する。
                    stopped = process_tree.stop_tree(None, known=owned, pins=pins)
                    assert not stopped.residual and not stopped.unresolved, stopped.descriptions()

                request.addfinalizer(reap_maintenance)
            # Jobの終了が所属プロセスへ届いたことを、離脱していない所属プロセスの終了で確かめる。
            unbroken.wait(timeout=30)
            assert maintenance.is_running(), "Jobの終了とともに独立プロセスが止まった"
            assert not record.exists(), "独立プロセスがJobの終了前に整理を終え、Jobの終了後の生存を確かめられない"
        finally:
            for process in members:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=10)
    deadline = time.monotonic() + 60
    while not record.exists() and time.monotonic() < deadline:
        time.sleep(0.2)
    assert record.exists(), "独立プロセスが60秒以内に結果を記録しなかった"
    assert json.loads(record.read_text(encoding="utf-8"))["result"] == "completed"
    assert not marker.exists()


@pytest.mark.parametrize(
    ("active", "create_tmp", "old_remains", "status"),
    [
        (False, True, False, prune_installation.MaintenanceResult.COMPLETED),
        (True, True, True, prune_installation.MaintenanceResult.DEFERRED),
        (False, False, False, prune_installation.MaintenanceResult.NONE),
    ],
)
def test_clean_caches_removes_old_rustup_tmp_only_when_idle(tmp_path, monkeypatch, active, create_tmp, old_remains, status):
    """古いもの、新しいもの、rustup稼働中、tmp不在の4条件を確かめる。"""
    home = tmp_path / "rustup"
    monkeypatch.setenv("RUSTUP_HOME", str(home))
    monkeypatch.setattr(prune_installation.setup_mise, "find_mise_binary", lambda: None)
    monkeypatch.setattr(prune_installation.shutil, "which", lambda _name: None)
    running = {"name": "rustup.exe" if active else "cargo.exe"}
    monkeypatch.setattr(prune_installation.psutil, "process_iter", lambda _attrs: [types.SimpleNamespace(info=running)])
    old, fresh = home / "tmp/old_dir", home / "tmp/fresh_dir"
    if create_tmp:
        old.mkdir(parents=True)
        fresh.mkdir()
        stamp = time.time() - 2 * 86_400
        os.utime(old, (stamp, stamp))

    assert prune_installation.clean_caches()[0] is status
    assert old.exists() is old_remains
    assert fresh.exists() is create_tmp


def test_clean_caches_keeps_unsafe_rustup_root(tmp_path, monkeypatch):
    """既存の共有ルートの判定を通らない設定からは削除しない。"""
    monkeypatch.setenv("RUSTUP_HOME", str(tmp_path))
    monkeypatch.setattr(prune_installation.common, "find_dotfiles_root", lambda: tmp_path)
    monkeypatch.setattr(prune_installation.setup_mise, "find_mise_binary", lambda: None)
    monkeypatch.setattr(prune_installation.shutil, "which", lambda _name: None)
    old = tmp_path / "tmp/unrelated"
    old.mkdir(parents=True)
    stamp = time.time() - 2 * 86_400
    os.utime(old, (stamp, stamp))

    assert prune_installation.clean_caches() == (prune_installation.MaintenanceResult.NONE, "")
    assert old.is_dir()
