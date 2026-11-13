# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""post_applyのloggingの試験。"""

# pylint: disable=protected-access

import io
import json
import logging
from pathlib import Path

import pytest

from pytools import post_apply
from pytools._internal import post_apply_outcome
from pytools._internal.post_apply_test_helpers import _isolate_sync_report as _isolate_sync_report
from pytools._internal.post_apply_test_helpers import _isolate_update_log as _isolate_update_log


def test_persistent_log_redacts_plugin_output_only(capsys: pytest.CaptureFixture[str]) -> None:
    """プラグインの出力と例外を保存時だけ伏せ、画面には元の診断を保つ。"""
    output = "api_key=fake-plugin-key\n-----BEGIN " + "PRIVATE KEY-----\nfake-private-body\n-----END PRIVATE KEY-----"

    def runner():
        plugin_logger = logging.getLogger("pytools._internal.apply_dotfiles_plugins")
        plugin_logger.warning("stage=plugin exit=7 stderr:\n%s", output)
        try:
            raise RuntimeError("Authorization: Basic fake-plugin-basic")
        except RuntimeError:
            plugin_logger.exception("plugin失敗")
        return post_apply.run(steps=[])

    with pytest.raises(SystemExit) as result:
        post_apply.main(runner=runner)
    assert result.value.code == 0
    saved = post_apply._UPDATE_LOG_PATH.read_text(encoding="utf-8")  # noqa: SLF001
    screen = capsys.readouterr().err
    for value in ("fake-plugin-key", "fake-private-body", "fake-plugin-basic"):
        assert value not in saved
        assert value in screen
    assert output in screen
    assert "stage=plugin exit=7 stderr:\n" in saved
    assert "RuntimeError" in saved
    assert "[REDACTED]" in saved


def test_sync_report_records_failed_step_reason_and_detail(sync_report_path: Path) -> None:
    """失敗したステップの名前、例外の内容、tracebackの末尾を同期結果の記録へ残す。"""
    steps = [
        ("success", lambda: post_apply_outcome.PostApplyOutcome(changed=True)),
        ("failure", lambda: (_ for _ in ()).throw(RuntimeError("boom"))),
    ]

    with pytest.raises(SystemExit):
        post_apply.main(runner=lambda: post_apply.run(steps=steps))

    post_apply_report = json.loads(sync_report_path.read_text(encoding="utf-8"))["post_apply"]
    assert post_apply_report["updated"] == 1
    assert post_apply_report["skipped"] == 0
    assert post_apply_report["failed"] == 1
    failed_step = post_apply_report["failed_steps"][0]
    assert failed_step["name"] == "failure"
    assert failed_step["reason"] == "RuntimeError: boom"
    assert "RuntimeError: boom" in failed_step["detail"]


def test_sync_report_preserves_report_of_the_same_run(monkeypatch: pytest.MonkeyPatch, sync_report_path: Path) -> None:
    """同じ実行の記録がある場合は、その記録へpost-apply段の結果を追加する。"""
    monkeypatch.setenv("UPDATE_DOTFILES_RUN_ID", "run-1")
    post_apply.sync_report.write_start("run-1", "2026-09-22T00:00:00+00:00")

    with pytest.raises(SystemExit):
        post_apply.main(
            runner=lambda: post_apply.run(steps=[("success", lambda: post_apply_outcome.PostApplyOutcome(changed=True))])
        )

    report = json.loads(sync_report_path.read_text(encoding="utf-8"))
    assert report["run_id"] == "run-1"
    assert report["status"] == "running"
    assert report["post_apply"]["failed"] == 0


def test_configure_logging_preserves_cp932_record_with_unencodable_character(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CP932で表現できない文字を代替表現へ変換し、ログレコードを保持する。"""
    stdout_buffer = io.BytesIO()
    stderr_buffer = io.BytesIO()
    stdout = io.TextIOWrapper(stdout_buffer, encoding="cp932")
    stderr = io.TextIOWrapper(stderr_buffer, encoding="cp932")
    monkeypatch.setattr(post_apply.sys, "stdout", stdout)
    monkeypatch.setattr(post_apply.sys, "stderr", stderr)
    with post_apply._configure_logging() as persistent_log_ready:  # noqa: SLF001
        logging.getLogger("cp932-test").info("符号化不能文字: ✓")
        stdout.flush()
        stderr.flush()
        output = stdout_buffer.getvalue().decode("cp932") + stderr_buffer.getvalue().decode("cp932")

    assert stdout.encoding == "cp932"
    assert persistent_log_ready
    assert "符号化不能文字" in output
    assert r"\u2713" in output
    assert "--- Logging error ---" not in output


def test_main_records_steps_and_failure_in_persistent_log() -> None:
    """post-applyの各ステップと最終失敗を同じ永続ログへ記録する。"""
    steps = [
        ("success", post_apply_outcome.PostApplyOutcome),
        ("failure", lambda: (_ for _ in ()).throw(RuntimeError("boom"))),
    ]

    with pytest.raises(SystemExit) as exc_info:
        post_apply.main(runner=lambda: post_apply.run(steps=steps))

    assert exc_info.value.code == 1
    log_text = post_apply._UPDATE_LOG_PATH.read_text(encoding="utf-8")  # noqa: SLF001
    assert "post-apply開始" in log_text
    assert "[1/2] success" in log_text
    assert "[2/2] failure" in log_text
    assert "post-apply終了: exit=1" in log_text


def test_persistent_log_uses_size_limited_rotation(monkeypatch: pytest.MonkeyPatch) -> None:
    """永続ログの総容量を固定世代数で制限する。"""
    monkeypatch.setattr(post_apply, "_UPDATE_LOG_MAX_BYTES", 512)
    monkeypatch.setattr(post_apply, "_UPDATE_LOG_BACKUP_COUNT", 2)
    with post_apply._configure_logging() as persistent_log_ready:  # noqa: SLF001
        assert persistent_log_ready
        for number in range(30):
            logging.getLogger("rotation-test").warning("診断%02d: %s", number, "x" * 120)

    path = post_apply._UPDATE_LOG_PATH  # noqa: SLF001
    generations = sorted(path.parent.glob(f"{path.name}*"))
    assert {item.name for item in generations} == {path.name, f"{path.name}.1", f"{path.name}.2"}
    assert all(0 < item.stat().st_size <= 512 for item in generations)
    assert "診断29:" in path.read_text(encoding="utf-8")
    retained = "".join(item.read_text(encoding="utf-8") for item in generations)
    assert "診断00:" not in retained
    assert "診断28:" in retained


def test_logging_scope_restores_logger_and_closes_all_handlers_after_close_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """後始末の1件が失敗しても、loggerを復元して残るハンドラも全て閉じる。"""
    root_logger = logging.getLogger()
    previous_handlers = root_logger.handlers.copy()
    previous_level = root_logger.level
    closed: list[str] = []
    labels: dict[logging.Handler, str] = {}
    original_close = logging.Handler.close

    def close(handler: logging.Handler) -> None:
        original_close(handler)
        if handler in labels:
            label = labels[handler]
            closed.append(label)
            if label == "stderr":
                raise OSError("stderr close failed")

    monkeypatch.setattr(logging.Handler, "close", close)
    with pytest.raises(OSError, match="stderr close failed"), post_apply._configure_logging():  # noqa: SLF001
        handlers = root_logger.handlers.copy()
        labels.update(zip(handlers, ("stdout", "stderr", "file"), strict=True))
    assert set(closed) == {"stdout", "stderr", "file"}
    assert root_logger.handlers == previous_handlers
    assert root_logger.level == previous_level
