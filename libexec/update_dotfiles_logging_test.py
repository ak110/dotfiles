# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""update_dotfilesのloggingの試験。"""

# pylint: disable=protected-access

import io
import logging
import pathlib
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
    real_handler = update_dotfiles_runtime.logging.handlers.RotatingFileHandler

    def handler(*args: Any, **kwargs: Any):
        instance = real_handler(*args, **kwargs)
        # 被検証側の終了後を観測し、旧版の失敗対照でもfixtureが残存資源を回収する。
        request.addfinalizer(instance.close)
        request.addfinalizer(lambda: update_dotfiles_runtime.logger.removeHandler(instance))
        created.append(instance)
        return instance

    def interrupt(*_args: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(update_dotfiles_runtime.logging.handlers, "RotatingFileHandler", handler)
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


@pytest.mark.parametrize("exit_code", [0, 7])
def test_logs_cli_preserves_rotated_multiline_records(logs_path: pathlib.Path, exit_code: int) -> None:
    """成功・失敗の実行を世代境界から読み、diffとpost-applyの続きも保つ。"""
    first = "2026-10-01 12:00:00,000 run=100-2 INFO update-dotfiles開始: root=test\n"
    diff = "2026-10-01 12:00:01,000 run=100-2 INFO chezmoi diffの出力:\n-old\n+日本語の差分\n"
    post_apply = "2026-10-01 12:00:02,000 run=100-2 WARNING post-applyの記録\nTraceback: 保存した続き\n"
    finish = f"2026-10-01 12:00:03,000 run=100-2 INFO update-dotfiles終了: exit={exit_code}\n"
    logs_path.with_name(f"{logs_path.name}.3").write_text(
        "2026-10-01 11:00:00,000 run=9-1 INFO 以前の更新\n" + first, encoding="utf-8"
    )
    logs_path.with_name(f"{logs_path.name}.1").write_text(diff, encoding="utf-8")
    logs_path.write_text(post_apply + finish, encoding="utf-8")

    result = _run_logs_cli()

    assert result.returncode == 0, result.stderr
    assert result.stdout == first + diff + post_apply + finish
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
