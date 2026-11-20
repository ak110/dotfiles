# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""post_applyのloggingの試験。"""

# pylint: disable=protected-access

import errno
import io
import json
import logging
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from agent_toolkit._atk.managed_temp import creation, paths, resources, sweep
from agent_toolkit._common import plugin_environment, plugin_environment_cleanup

from pytools import post_apply
from pytools._internal import common, install_codex_plugins, log_format, plugin_warmup, post_apply_outcome, setup_herdr_cli
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


@pytest.mark.parametrize("unwritable", ["open", "append", None])
def test_failure_detail_reaches_stderr_if_not_saved(tmp_path, capsys, unwritable):
    path = tmp_path / "update-dotfiles.log"
    if unwritable == "open":
        path.mkdir()
    with post_apply._configure_logging():
        if unwritable == "append":
            path.unlink()
            path.mkdir()
        logging.getLogger("warmup").warning("子の標準エラーと対処", extra=log_format.LOG_ONLY)
    screen = capsys.readouterr().err
    assert ("子の標準エラーと対処" in screen) is (unwritable is not None)
    if unwritable is None:
        assert "子の標準エラーと対処" in path.read_text(encoding="utf-8")


def test_warmup_pressure_keeps_weekend_contract(monkeypatch):
    session = creation.resolve_session_temp("weekend")
    contract = session / "work-contract.md"
    contract.write_text("再開条件", encoding="utf-8")
    stamp = time.time() - 3 * 86400
    standalone = paths.temp_root() / "handoff.md"
    standalone.write_text("引き継ぎ", encoding="utf-8")
    for path in (contract, session, standalone):
        os.utime(path, (stamp, stamp))
    monkeypatch.delenv(sweep.MIN_FREE_RATIO_ENV, raising=False)
    monkeypatch.setattr(sweep, "_disk_usage", lambda _root: SimpleNamespace(total=237 * 1024**3, free=19 * 1024**3))

    plugin_warmup.cleanup_prepared(plugin_warmup.PreparedTargets([]))

    assert standalone.read_text(encoding="utf-8") == "引き継ぎ"
    assert creation.find_session_temp("weekend") == session
    assert contract.read_text(encoding="utf-8") == "再開条件"


def test_warmup_summarizes_cleanup_and_keeps_details_in_log(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("UPDATE_DOTFILES_RUN_ID", "update")
    monkeypatch.setenv(plugin_environment.RUNTIME_DATA_ENV, str(tmp_path))
    monkeypatch.setattr(plugin_environment_cleanup, "_process_references", lambda: ([], False))
    current, old, broken = (tmp_path / "environments" / name for name in ("current-key", "old-key", "broken-key"))
    for target in (current, old, broken):
        target.mkdir(parents=True)
    (broken / ".agent-toolkit-sources.json").write_text("{", encoding="utf-8")
    monkeypatch.setattr(plugin_environment, "project_environment", lambda *args, **kwargs: current)
    monkeypatch.setattr(plugin_environment, "record_source", lambda *args: None)
    monkeypatch.setattr(plugin_warmup.session_review, "sweep", lambda **kwargs: None)

    area = paths.temp_root() / "cleanup-test"
    for name in ("deleted-key", "busy-key", "denied-key"):
        cache = area / name
        cache.mkdir(parents=True)
        (cache / "CACHEDIR.TAG").write_text("Signature: 8a477f597d28d172789f06886806bc55\n", encoding="ascii")
    (area / "protected").symlink_to(current, target_is_directory=True)
    monkeypatch.setattr(resources, "process_references", lambda: [])
    remove_tree = resources.shutil.rmtree

    def remove(cache: Path) -> None:
        if cache.name == "denied-key":
            raise PermissionError(errno.EACCES, "access denied", str(cache))
        if cache.name == "busy-key":
            raise OSError(errno.EBUSY, "resource busy", str(cache))
        remove_tree(cache)

    monkeypatch.setattr(resources, "shutil", SimpleNamespace(rmtree=remove))

    with post_apply._configure_logging():
        plugin_warmup.cleanup_prepared(plugin_warmup.PreparedTargets([["python", "--project", "root"]]))
    captured = capsys.readouterr()
    screen = captured.out + captured.err
    assert "保持1件、削除1件、保留1件" in screen
    assert "update-dotfiles logs" in screen
    saved = post_apply._UPDATE_LOG_PATH.read_text(encoding="utf-8")
    assert "一時資源1件の回収を保留" in screen
    assert "アクセス権" in captured.err
    assert f"一時資源の回収を保留: {area / 'denied-key'}" in saved
    assert f"一時資源の回収を保留: {area / 'busy-key'}" not in saved
    for detail in ("再生成物2件", "保護対象を回収から除外", "一時資源を1件回収", "resource busy", "access denied"):
        assert detail not in screen and detail in saved
    for name in (current.name, old.name, broken.name, "busy-key", "deleted-key", "denied-key"):
        assert name not in screen and name in saved


@pytest.mark.skipif(not hasattr(os, "O_PATH") or os.geteuid() == 0, reason="Linuxの非rootアカウントでアクセス拒否を再現する")
@pytest.mark.parametrize(
    ("blocked", "mode", "reason"),
    [
        ("project", 0o100, "走査不能"),
        ("project/cache/CACHEDIR.TAG", 0, "再生成物の確認失敗"),
        ("project/.venv", 0, "再生成物の確認失敗"),
        ("project/cache", 0o500, "回収失敗"),
    ],
    ids=["walk", "tag-read", "marker-lookup", "remove"],
)
def test_update_reports_unresolved_permission_error_and_retries(blocked, mode, reason, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("UPDATE_DOTFILES_RUN_ID", "update")
    monkeypatch.setenv(plugin_environment.RUNTIME_DATA_ENV, str(tmp_path))
    monkeypatch.setattr(resources, "process_references", lambda: [])
    area = paths.temp_root() / "permission-test"
    project = area / "project"
    cache = project / (".venv" if blocked == "project/.venv" else "cache")
    cache.mkdir(parents=True)
    if cache.name == ".venv":
        (cache / "pyvenv.cfg").touch()
        (project / "uv.lock").touch()
    else:
        (cache / "CACHEDIR.TAG").write_text("Signature: 8a477f597d28d172789f06886806bc55\n", encoding="ascii")
    restricted = area / blocked
    original = restricted.stat().st_mode
    restricted.chmod(mode)
    try:
        with post_apply._configure_logging():
            plugin_warmup.cleanup_prepared(plugin_warmup.PreparedTargets([]))
        screen = capsys.readouterr()
        assert not screen.out and len(screen.err.splitlines()) == 1
        assert "一時資源1件" in screen.err and "update-dotfiles logs" in screen.err
        saved = post_apply._UPDATE_LOG_PATH.read_text(encoding="utf-8")
        assert any(
            reason in line and str(restricted) in line and os.strerror(errno.EACCES) in line for line in saved.splitlines()
        )
    finally:
        restricted.chmod(original)
    assert cache.is_dir()
    with post_apply._configure_logging():
        plugin_warmup.cleanup_prepared(plugin_warmup.PreparedTargets([]))
    assert capsys.readouterr() == ("", "")
    assert not cache.exists()


def test_update_hides_success_but_preserves_manual_recovery(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("UPDATE_DOTFILES_RUN_ID", "update")
    malformed = tmp_path / "settings.json"
    malformed.write_text("{", encoding="utf-8")

    def step():
        logging.getLogger("update-test").info("成功した操作の明細")
        assert common.load_json_dict(malformed, {}, tag="fixture") is None
        return post_apply_outcome.PostApplyOutcome()

    with pytest.raises(SystemExit) as result:
        post_apply.main(runner=lambda: post_apply.run([("設定", step)]))
    assert result.value.code == 0
    screen = capsys.readouterr()
    assert str(malformed) in screen.out and "再実行" in screen.out
    log = post_apply._UPDATE_LOG_PATH.read_text(encoding="utf-8")
    for detail in ("成功した操作の明細", "[1/1] 設定", "完了: 更新", "post-apply終了"):
        assert detail not in screen.out + screen.err and detail in log


def test_actionable_notices_show_each_command_once(capsys):
    with post_apply._configure_logging():
        post_apply._print_post_apply_notices(
            [
                install_codex_plugins._CODEX_HOOK_TRUST_NOTICE,
                setup_herdr_cli._deferred_notice(inside_session=True),
            ]
        )
    screen = capsys.readouterr().err
    assert screen.count("/hooks") == 1
    assert "    herdr update" in screen.splitlines()


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
    generations = sorted([path, *path.parent.glob(f"{path.name}.[0-9]")])
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
