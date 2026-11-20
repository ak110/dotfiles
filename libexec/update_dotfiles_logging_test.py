# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""update_dotfilesのloggingの試験。"""

# pylint: disable=protected-access

import collections.abc
import contextlib
import errno
import io
import logging
import pathlib
import subprocess
import sys
from typing import Any

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import update_dotfiles  # noqa: E402  # pylint: disable=wrong-import-position
import update_dotfiles_runtime  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _isolate_sync_report as _isolate_sync_report  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _logs_environment as _logs_environment  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _no_codex_processes as _no_codex_processes  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _own_git_process_scan as _own_git_process_scan  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _real_stash_runtime as _real_stash_runtime  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _run_logs_cli  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _separate_git_recovery as _separate_git_recovery  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _skip_agent_toolkit_prepare as _skip_agent_toolkit_prepare  # noqa: E402  # pylint: disable=wrong-import-position

from pytools import post_apply  # noqa: E402  # pylint: disable=wrong-import-position
from pytools._internal import plugin_warmup  # noqa: E402  # pylint: disable=wrong-import-position


def test_persistent_log_redacts_only_saved_diagnostic() -> None:
    """書式引数と例外を保存時に伏せ、診断元と別handlerの本文は保つ。"""
    output = "api_key=fake-update-key\nhttps://fake-user:fake-pass@example.invalid/path?token=fake-query\n"
    screen = io.StringIO()
    handler = logging.StreamHandler(screen)
    update_dotfiles_runtime.logger.addHandler(handler)
    try:
        with update_dotfiles_runtime.configure_persistent_log("redaction-test"):
            update_dotfiles_runtime.logger.error("stage=child exit=7 stdout:\n%s", output)
            try:
                raise RuntimeError("Authorization: Bearer fake-update-bearer")
            except RuntimeError:
                update_dotfiles_runtime.logger.exception("child失敗")
    finally:
        update_dotfiles_runtime.logger.removeHandler(handler)
        handler.close()
    saved = update_dotfiles_runtime.LOG_PATH.read_text(encoding="utf-8")
    for value in ("fake-update-key", "fake-user", "fake-pass", "fake-query", "fake-update-bearer"):
        assert value not in saved
        assert value in screen.getvalue()
    assert output in screen.getvalue()
    assert "stage=child exit=7 stdout:\n" in saved
    assert "example.invalid/path?token=" in saved
    assert "RuntimeError" in saved
    assert "[REDACTED]" in saved


def test_main_closes_persistent_log_when_start_record_is_interrupted(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """開始記録が中断されても、獲得済みのログファイルを閉じて登録を外す。"""
    original_handlers = update_dotfiles_runtime.logger.handlers.copy()
    created: list[logging.FileHandler] = []
    real_handler = update_dotfiles_runtime.update_log.ProcessSafeRotatingFileHandler

    def handler(*args: Any, **kwargs: Any):
        instance = real_handler(*args, **kwargs)
        # 被検証側の終了後を観測し、旧版の失敗対照でもfixtureが残存資源を回収する。
        request.addfinalizer(instance.close)
        request.addfinalizer(lambda: update_dotfiles_runtime.logger.removeHandler(instance))
        created.append(instance)
        return instance

    def interrupt(*_args: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(update_dotfiles_runtime.update_log, "ProcessSafeRotatingFileHandler", handler)
    monkeypatch.setattr(update_dotfiles.sync_report, "write_start", interrupt)
    with pytest.raises(KeyboardInterrupt):
        update_dotfiles.main([])
    assert len(created) == 1
    assert created[0].stream is None
    assert update_dotfiles_runtime.logger.handlers == original_handlers


def test_logs_cli_shows_latest_update_only(logs_path: pathlib.Path) -> None:
    """単独post-applyと以前の更新を表示へ混ぜない。"""
    latest = "2026-10-01 12:00:00,000 run=100-2 INFO update-dotfiles開始: root=test\n"
    logs_path.write_text(
        "2026-10-01 11:00:00,000 run=9-1 INFO 以前の更新\n"
        + latest
        + "2026-10-01 12:00:01,000 run=post-apply-999 INFO 単独起動\n単独の続き\n",
        encoding="utf-8",
    )

    result = _run_logs_cli()

    assert result.returncode == 0, result.stderr
    assert result.stdout == latest
    assert not result.stderr


@pytest.mark.parametrize("parent_exit", [None, 0, 7], ids=["standalone", "update-success", "update-failure"])
@pytest.mark.parametrize("storage", ["saved", "open-failure", "append-failure"])
def test_dependency_warning_points_to_its_diagnostic_once(
    logs_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    parent_exit: int | None,
    storage: str,
) -> None:
    """依存構築の失敗は警告に留め、親の終了値や保存可否によらず同じ対処を繰り返さない。"""
    monkeypatch.setattr(post_apply, "_UPDATE_LOG_PATH", logs_path)
    monkeypatch.setattr(update_dotfiles_runtime, "LOG_PATH", logs_path)
    if storage == "open-failure":
        logs_path.mkdir()
    else:
        logs_path.write_text("2026-11-18 12:00:00,000 run=10-1 INFO 以前の更新\n", encoding="utf-8")

    def runner():
        if storage == "append-failure":
            logs_path.unlink()
            logs_path.mkdir()
        command = ["warmup-fixture"]
        result = subprocess.CompletedProcess(command, 7, "原因の明細", "失敗の要旨")
        plugin_warmup.CommandOutcome(command, result, 0).report(target="fixture", tag="warmup")
        return [], []

    if parent_exit is not None:
        monkeypatch.setenv("UPDATE_DOTFILES_RUN_ID", "11-1")
        monkeypatch.setattr(update_dotfiles_runtime, "current_run_id", "11-1")
    with update_dotfiles_runtime.configure_persistent_log("11-1") if parent_exit is not None else contextlib.nullcontext():
        with pytest.raises(SystemExit) as child:
            post_apply.main(runner=runner)
        assert child.value.code == 0
        if parent_exit is not None:
            assert update_dotfiles_runtime.finish(parent_exit) == parent_exit
    screen = capsys.readouterr().err
    assert screen.count("手動で`warmup-fixture`") == 1
    assert screen.count("次の操作:") == int(parent_exit == 7)
    assert screen.count(f"永続ログ: {logs_path}") == int(storage == "saved")
    assert screen.count("update-dotfiles logs") == int(storage == "saved" and parent_exit is not None)
    assert ("原因の明細" in screen) == (storage != "saved")
    if storage == "saved":
        assert "原因の明細" in logs_path.read_text(encoding="utf-8")
        assert ("原因の明細" in _run_logs_cli().stdout) == (parent_exit is not None)


@pytest.mark.parametrize("fault", ["child-report-rename", "child-log-rename", "parent-final-append", "saved-in-backup"])
def test_parent_locates_saved_child_warning_across_retries(
    logs_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], fault: str
) -> None:
    """同期結果や他のレコードの成否ではなく、警告の明細を書いた時の結果で所在を示す。"""
    monkeypatch.setenv("UPDATE_DOTFILES_RUN_ID", "11-1")
    monkeypatch.setattr(post_apply, "_UPDATE_LOG_PATH", logs_path)
    monkeypatch.setattr(update_dotfiles_runtime, "LOG_PATH", logs_path)
    monkeypatch.setattr(update_dotfiles_runtime, "current_run_id", "11-1")

    def runner():
        with monkeypatch.context() as child_fault:
            if fault == "child-log-rename":
                child_fault.setattr(post_apply.update_log.ProcessSafeRotatingFileHandler, "rotate", fail_rename)
            command = ["warmup-fixture"]
            result = subprocess.CompletedProcess(command, 7, "不足パッケージ名", "依存構築失敗")
            plugin_warmup.CommandOutcome(command, result, 0).report(target="fixture", tag="warmup")
        return [], []

    def fail_rename(*_args):
        raise PermissionError(errno.EACCES, "rename")

    def no_space():
        raise OSError(errno.ENOSPC, "parent append")

    with update_dotfiles_runtime.configure_persistent_log("11-1") as parent:
        assert parent is not None
        with monkeypatch.context() as child_fault:
            if fault == "child-report-rename":
                child_fault.setattr(post_apply.sync_report.os, "replace", fail_rename)
            if fault == "child-log-rename":
                child_fault.setattr(post_apply, "_UPDATE_LOG_MAX_BYTES", 1)
            for _ in range(2):
                with pytest.raises(SystemExit) as child:
                    post_apply.main(runner=runner)
                assert child.value.code == 0
        child_screen = capsys.readouterr().err
        if fault == "saved-in-backup":
            logs_path.rename(logs_path.with_suffix(".log.1"))
        if fault == "parent-final-append":
            monkeypatch.setattr(parent, "_open", no_space)
        assert update_dotfiles_runtime.finish(0) == 0
    parent_screen = capsys.readouterr().err
    assert "永続ログ:" not in child_screen and "この標準エラー" not in parent_screen
    assert child_screen.count("手動で") == 2
    saved = fault != "child-log-rename"
    assert parent_screen.count("update-dotfiles logs") == int(saved)
    assert ("不足パッケージ名" in _run_logs_cli().stdout) == saved
    assert ("不足パッケージ名" in child_screen) != saved
    assert ("この標準エラー" in child_screen) != saved


@pytest.mark.windows
def test_update_parent_and_post_apply_child_can_rotate_the_same_log(
    logs_path: pathlib.Path,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    host_environ: collections.abc.Callable[[], dict[str, str]],
) -> None:
    """親がログを所有したまま子が世代移動し、両者の診断と容量上限を維持する。"""
    monkeypatch.setattr(update_dotfiles_runtime, "LOG_PATH", logs_path)
    monkeypatch.setattr(update_dotfiles_runtime, "_LOG_MAX_BYTES", 512)
    writer = tmp_path / "post_apply_writer.py"
    writer.write_text(
        "import logging, pathlib, sys\n"
        "from pytools import post_apply\n"
        "post_apply._UPDATE_LOG_PATH = pathlib.Path(sys.argv[1])\n"
        "post_apply._UPDATE_LOG_MAX_BYTES = 512\n"
        "post_apply.sync_report.REPORT_PATH = pathlib.Path(sys.argv[2])\n"
        "def run():\n"
        "    for number in range(3):\n"
        "        logging.getLogger('child').warning('child-%s %s', number, 'x' * 180)\n"
        "    return post_apply.run(steps=[])\n"
        "post_apply.main(runner=run)\n",
        encoding="utf-8",
    )
    with update_dotfiles_runtime.configure_persistent_log("parent-child-test"):
        update_dotfiles_runtime.logger.info("parent-before")
        result = subprocess.run(
            [sys.executable, str(writer), str(logs_path), str(tmp_path / "child-report.json")],
            env=host_environ(),
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        update_dotfiles_runtime.logger.info("parent-after")
    assert result.returncode == 0, result.stderr
    assert "Logging error" not in result.stderr
    generations = [logs_path, *logs_path.parent.glob(f"{logs_path.name}.[0-9]")]
    saved = "".join(path.read_text(encoding="utf-8") for path in generations)
    assert "parent-before" in saved
    assert "child-2" in saved
    assert "parent-after" in saved
    assert 1 < len(generations) <= 4
    assert all(path.stat().st_size <= 512 for path in generations)


@pytest.mark.parametrize("exit_code", [0, 7])
def test_logs_cli_preserves_rotated_multiline_records(logs_path: pathlib.Path, exit_code: int) -> None:
    """成功・失敗の実行を世代境界から読み、diffとpost-applyの続きも保つ。"""
    first = "2026-10-01 12:00:00,000 run=100-2 INFO update-dotfiles開始: root=test\n"
    diff = "2026-10-01 12:00:01,000 run=100-2 INFO chezmoi diffの出力:\n-old\n+日本語の差分\n"
    post_apply_record = "2026-10-01 12:00:02,000 run=100-2 WARNING post-applyの記録\nTraceback: 保存した続き\n"
    finish = f"2026-10-01 12:00:03,000 run=100-2 INFO update-dotfiles終了: exit={exit_code}\n"
    logs_path.with_name(f"{logs_path.name}.3").write_text(
        "2026-10-01 11:00:00,000 run=9-1 INFO 以前の更新\n" + first, encoding="utf-8"
    )
    logs_path.with_name(f"{logs_path.name}.1").write_text(diff, encoding="utf-8")
    logs_path.write_text(post_apply_record + finish, encoding="utf-8")

    result = _run_logs_cli()

    assert result.returncode == 0, result.stderr
    assert result.stdout == first + diff + post_apply_record + finish
    assert not result.stderr


def test_logs_cli_reads_update_when_start_record_has_rotated_away(logs_path: pathlib.Path) -> None:
    """開始記録が残らない更新も、保存された記録を表示する。"""
    saved = "2026-10-01 12:00:03,000 run=100-2 INFO update-dotfiles終了: exit=7\n"
    logs_path.write_text(saved, encoding="utf-8")

    result = _run_logs_cli()

    assert result.returncode == 0, result.stderr
    assert result.stdout == saved


@pytest.mark.parametrize("contents", [None, "", "2026-10-01 12:00:00,000 run=post-apply-999 INFO 単独起動\n"])
def test_logs_cli_reports_no_saved_update(logs_path: pathlib.Path, contents: str | None) -> None:
    """保存された更新実行が無いことをユーザーへ案内する。"""
    if contents is not None:
        logs_path.write_text(contents, encoding="utf-8")

    result = _run_logs_cli()

    assert result.returncode == 0, result.stderr
    assert result.stdout == "保存済みの更新ログはありません。\n"
    assert not result.stderr


def test_logs_cli_does_not_write_state(logs_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """表示によるログ追記、同期結果更新、ロック生成と更新開始を検出する。"""
    logs_path.write_text("2026-10-01 12:00:00,000 run=100-2 INFO 保存済みログ\n", encoding="utf-8")
    report = logs_path.with_name("sync-report.json")
    report.write_text('{"status": "failed"}', encoding="utf-8")
    before = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in logs_path.parent.iterdir()}
    monkeypatch.setenv("UPDATE_DOTFILES_GIT_TIMEOUT_SEC", "invalid")

    result = _run_logs_cli()

    assert result.returncode == 0, result.stderr
    assert "保存済みログ" in result.stdout
    after = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in logs_path.parent.iterdir()}
    assert after == before


def test_logs_cli_reports_read_failure(logs_path: pathlib.Path) -> None:
    """読めない保存先を空ログ扱いせず、対象と失敗を示す。"""
    logs_path.mkdir()

    result = _run_logs_cli()

    assert result.returncode == 1
    assert not result.stdout
    assert str(logs_path) in result.stderr
    assert "読み取れませんでした" in result.stderr
    assert "Traceback" not in result.stderr


def test_help_describes_logs(logs_path: pathlib.Path) -> None:
    """ヘルプの表示は正常終了し、更新を開始しない。"""
    result = _run_logs_cli(["--help"])

    assert result.returncode == 0, result.stderr
    assert not list(logs_path.parent.iterdir())
