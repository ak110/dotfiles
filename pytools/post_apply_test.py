"""pytools.post_apply のテスト。

各ステップが呼ばれること、先行工程の順序と並列実行、途中ステップが例外を送出しても他が継続すること、
画面と永続ログへの出力の振り分け、失敗時の exit code を検証する。
撤去前の配布内容はwheelへ含めないため、リポジトリ直下のtestdata/post_applyへ保持する。
"""

import ast
import dataclasses
import inspect
import io
import json
import logging
import logging.handlers
import os
import re
import subprocess
import threading
import time
import typing
from pathlib import Path

import pytest
import pytilpack.jsonc

from pytools import post_apply
from pytools._internal import apply_dotfiles_plugins, claude_common, common, deferred_updates, post_apply_outcome

# `post_apply`は削除対象の表をimport時のホームで組み立てる。テストの実行中はホームが一時ディレクトリへ隔離されるため、
# 表を引く鍵もimport時のホームから組み立てる。
_IMPORT_HOME = Path.home()

# 配布先cleanup契約の定数を直接検証する。
# pylint: disable=protected-access


@pytest.fixture(autouse=True)
def _isolate_update_log(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """post-applyの永続ログを実際のOSアカウントのstate directoryから隔離する。"""
    monkeypatch.setattr(post_apply, "_UPDATE_LOG_PATH", tmp_path / "update-dotfiles.log")
    monkeypatch.setattr(post_apply, "is_human_terminal", lambda: False)
    monkeypatch.delenv(post_apply._UPDATE_RUN_ID_ENV, raising=False)  # noqa: SLF001


def _gated_steps(count: int, monkeypatch: pytest.MonkeyPatch) -> list[post_apply._StepSpec]:  # noqa: SLF001
    """独立した`count`個の工程を作成し、`wait`が呼ばれるたびに1工程ずつ完了させる。"""
    gates = [threading.Event() for _ in range(count)]
    real_wait = post_apply.wait

    def release_next(*args, **kwargs):
        for gate in gates:
            if not gate.is_set():
                gate.set()
                break
        return real_wait(*args, **kwargs)

    monkeypatch.setattr(post_apply, "wait", release_next)

    def make(gate: threading.Event):
        def step() -> post_apply_outcome.PostApplyOutcome:
            assert gate.wait(30)
            return post_apply_outcome.PostApplyOutcome(changed=True)

        return step

    return [post_apply._StepSpec(f"工程{index}", make(gate)) for index, gate in enumerate(gates)]  # noqa: SLF001


@pytest.mark.parametrize("human", [True, False], ids=["terminal", "not-terminal"])
def test_progress_is_one_rewritten_line(monkeypatch, capsys, human):
    """端末では進捗が1行の書き換えになり、結果行に残らない。端末でなければ出力しない。"""
    monkeypatch.setattr(post_apply, "is_human_terminal", lambda: human)
    monkeypatch.setattr(post_apply.shutil, "get_terminal_size", lambda *_args, **_kwargs: os.terminal_size((40, 24)))
    steps = _gated_steps(4, monkeypatch)
    results, _ = post_apply.run(steps)
    assert [r.name for r in results] == ["工程0", "工程1", "工程2", "工程3"]
    captured = capsys.readouterr()
    assert "post-apply実行中" not in captured.out
    if not human:
        assert not captured.err
        return
    # 新しい行を追加せず、書き換えと消去だけで終わる。
    assert "\n" not in captured.err
    assert captured.err.count("post-apply実行中") >= 2
    assert captured.err.endswith("\r")
    for segment in captured.err.split("\r"):
        assert post_apply._display_width(segment) <= 39  # noqa: SLF001


def test_progress_is_cleared_before_outcome_lines(monkeypatch):
    """工程結果の出力前に進捗の行を消去し、標準出力と標準エラーが同じ端末で混ざらない。"""
    monkeypatch.setattr(post_apply, "is_human_terminal", lambda: True)
    monkeypatch.setattr(post_apply.shutil, "get_terminal_size", lambda *_args, **_kwargs: os.terminal_size((200, 24)))
    events: list[str] = []
    progress = post_apply._ProgressLine  # noqa: SLF001
    real_show, real_clear, real_emit = progress.show, progress.clear, post_apply._emit_outcome  # noqa: SLF001

    def show(self, text):
        events.append("show")
        real_show(self, text)

    def clear(self):
        events.append("clear")
        real_clear(self)

    def emit(label, outcome):
        events.append("emit")
        real_emit(label, outcome)

    monkeypatch.setattr(progress, "show", show)
    monkeypatch.setattr(progress, "clear", clear)
    monkeypatch.setattr(post_apply, "_emit_outcome", emit)
    post_apply.run(_gated_steps(3, monkeypatch))
    assert "emit" in events
    for index, event in enumerate(events):
        if event == "emit":
            assert events[index - 1] == "clear"
    assert events[-1] == "clear"


def test_exception_prints_one_line_and_keeps_traceback_in_log(tmp_path, capsys):
    """例外で失敗した工程は画面に1行だけ出力し、Tracebackは永続ログだけへ残す。"""

    def boom() -> post_apply_outcome.PostApplyOutcome:
        raise RuntimeError("受入の例外")

    steps = [post_apply._StepSpec("例外工程", boom)]  # noqa: SLF001
    with pytest.raises(SystemExit) as ended:
        post_apply.main(runner=lambda: post_apply.run(steps))
    assert ended.value.code == 1
    captured = capsys.readouterr()
    screen = captured.out + captured.err
    assert "例外工程: 失敗: RuntimeError: 受入の例外" in screen
    assert screen.count("受入の例外") == 1
    assert "Traceback" not in screen
    log = (tmp_path / "update-dotfiles.log").read_text(encoding="utf-8")
    assert "Traceback (most recent call last)" in log
    assert 'raise RuntimeError("受入の例外")' in log


@pytest.mark.parametrize("via_update_dotfiles", [False, True], ids=["standalone", "via-update-dotfiles"])
def test_recovery_guidance_is_printed_once_only_for_standalone_run(monkeypatch, capsys, via_update_dotfiles):
    """案内は単独実行でだけ1回出し、`update-dotfiles`経由では`update-dotfiles`の終了時に任せる。"""
    if via_update_dotfiles:
        monkeypatch.setenv(post_apply._UPDATE_RUN_ID_ENV, "run-1")  # noqa: SLF001

    def boom() -> post_apply_outcome.PostApplyOutcome:
        raise RuntimeError("受入の例外")

    def fail() -> post_apply_outcome.PostApplyOutcome:
        return post_apply_outcome.PostApplyOutcome(failure="戻り値の失敗", next_action="工程固有の操作")

    steps = [post_apply._StepSpec("例外工程", boom), post_apply._StepSpec("失敗工程", fail)]  # noqa: SLF001
    with pytest.raises(SystemExit):
        post_apply.main(runner=lambda: post_apply.run(steps))
    captured = capsys.readouterr()
    screen = captured.out + captured.err
    assert screen.count("工程固有の操作") == 1
    expected = 0 if via_update_dotfiles else 1
    assert screen.count("永続ログ:") == expected
    assert screen.count("update-dotfilesを再実行してください") == expected


@pytest.mark.parametrize("agent", [False, True], ids=["human", "agent"])
def test_run_reports_overall_progress_while_steps_run(monkeypatch, tmp_path, capsys, agent):
    """保留中の工程を含む全体状態が届き、列挙順の結果と永続ログを保つ。"""
    monkeypatch.setattr(post_apply, "is_human_terminal", lambda: not agent)
    real_wait = post_apply.wait
    release = threading.Event()
    observed = []

    def first():
        assert release.wait(30)
        logging.getLogger("progress-test").info("first-detail")
        return post_apply_outcome.PostApplyOutcome(changed=True)

    def second():
        logging.getLogger("progress-test").info("second-detail")
        return post_apply_outcome.PostApplyOutcome(failure="工程の失敗", next_action="再実行する")

    def wait_for_steps(*args, **kwargs):
        observed.append(capsys.readouterr())
        release.set()
        return real_wait(*args, **kwargs)

    monkeypatch.setattr(post_apply, "wait", wait_for_steps)
    steps = [post_apply._StepSpec("先行工程", first), post_apply._StepSpec("後続工程", second, after=("先行工程",))]
    try:
        with pytest.raises(SystemExit) as ended:
            post_apply.main(runner=lambda: post_apply.run(steps))
        assert ended.value.code == 1
    finally:
        release.set()
    assert observed
    if agent:
        assert not observed[0].err
    else:
        assert "post-apply実行中" in observed[0].err and "先行工程" in observed[0].err
    output = "".join(item.out for item in observed) + capsys.readouterr().out
    assert output.index("[1/2] 先行工程") < output.index("first-detail") < output.index("[2/2] 後続工程")
    assert output.index("[2/2] 後続工程") < output.index("second-detail")
    assert output.count("[1/2] 先行工程") == 1
    log = (tmp_path / "update-dotfiles.log").read_text(encoding="utf-8")
    assert "first-detail" in log and "second-detail" in log and "post-apply終了: exit=1" in log


@pytest.fixture(autouse=True, name="sync_report_path")
def _isolate_sync_report(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """同期結果の記録先を一時領域へ向け、実行環境の状態ディレクトリを書き換えないようにする。"""
    report_path = tmp_path / "state" / "sync-report.json"
    monkeypatch.setattr(post_apply.sync_report, "REPORT_PATH", report_path)
    return report_path


@pytest.mark.parametrize(("argv", "exit_code"), [(["--help"], 0), (["--unknown"], 2)])
def test_main_rejects_nondefault_arguments_before_side_effects(
    argv: list[str],
    exit_code: int,
    capsys: pytest.CaptureFixture[str],
    sync_report_path: Path,
) -> None:
    """確認引数と未知引数は更新処理や永続記録へ到達しない。"""
    called = False

    def runner() -> tuple[list[post_apply._StepResult], list[str]]:  # noqa: SLF001
        nonlocal called
        called = True
        return [], []

    with pytest.raises(SystemExit) as exc_info:
        post_apply.main(argv, runner=runner)

    assert exc_info.value.code == exit_code
    assert not called
    assert not post_apply._UPDATE_LOG_PATH.exists()  # noqa: SLF001
    assert not sync_report_path.exists()
    captured = capsys.readouterr()
    if exit_code == 0:
        assert "usage:" in captured.out
        assert captured.err == ""
    else:
        assert "usage:" in captured.err
        assert "unrecognized arguments" in captured.err


def test_main_help_without_runner_skips_default_steps(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], sync_report_path: Path
) -> None:
    """公開CLIと同じ引数形で、工程を指定しない場合の`_DEFAULT_STEPS`を起動しない。"""

    def unexpected_run() -> tuple[list[post_apply._StepResult], list[str]]:  # noqa: SLF001
        pytest.fail("--helpで_DEFAULT_STEPSの実行へ到達した")

    monkeypatch.setattr(post_apply, "run", unexpected_run)
    with pytest.raises(SystemExit) as exc_info:
        post_apply.main(["--help"])

    assert exc_info.value.code == 0
    assert "usage:" in capsys.readouterr().out
    assert not post_apply._UPDATE_LOG_PATH.exists()  # noqa: SLF001
    assert not sync_report_path.exists()


def test_linked_worktree_is_rejected_before_any_step_or_record(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    sync_report_path: Path,
) -> None:
    """複製作業ツリーで工程を指定せずに実行しても、全段と永続記録へ到達しない。"""
    root = tmp_path / "linked"
    canonical_root = tmp_path / "main"
    monkeypatch.setattr(common, "find_dotfiles_root", lambda: root)

    def fake_git(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert cmd == ["git", "-C", str(root), "rev-parse", "--path-format=absolute", "--git-common-dir"]
        assert not kwargs
        return subprocess.CompletedProcess(cmd, 0, f"{canonical_root / '.git'}\n", "")

    def unexpected_run() -> tuple[list[post_apply._StepResult], list[str]]:  # noqa: SLF001
        pytest.fail("linked worktreeで_DEFAULT_STEPSの実行へ到達した")

    monkeypatch.setattr(common, "run_subprocess", fake_git)
    monkeypatch.setattr(post_apply, "run", unexpected_run)

    with pytest.raises(SystemExit) as exc_info:
        post_apply.main([])

    assert exc_info.value.code == 2
    assert not post_apply._UPDATE_LOG_PATH.exists()  # noqa: SLF001
    assert not sync_report_path.exists()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert str(root) in captured.err
    assert str(canonical_root) in captured.err


@pytest.mark.parametrize("allow_non_canonical", [False, True])
def test_canonical_root_or_explicit_override_runs_steps(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    allow_non_canonical: bool,
) -> None:
    """正規ルートと明示解除では既存の全段実行経路へ進む。"""
    canonical_root = tmp_path / "main"
    root = tmp_path / "linked" if allow_non_canonical else canonical_root
    monkeypatch.setattr(common, "find_dotfiles_root", lambda: root)

    def fake_git(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert cmd == ["git", "-C", str(root), "rev-parse", "--path-format=absolute", "--git-common-dir"]
        assert not kwargs
        return subprocess.CompletedProcess(cmd, 0, f"{canonical_root / '.git'}\n", "")

    calls: list[str] = []
    monkeypatch.setattr(common, "run_subprocess", fake_git)
    monkeypatch.setattr(
        post_apply,
        "_DEFAULT_STEPS",
        [("first", _make_step("first", calls)), ("second", _make_step("second", calls))],
    )
    argv = ["--allow-non-canonical-root"] if allow_non_canonical else []

    with pytest.raises(SystemExit) as exc_info:
        post_apply.main(argv)

    assert exc_info.value.code == 0
    assert sorted(calls) == ["first", "second"]


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


def test_deferred_steps_are_recorded_and_cleared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """延期を返した工程を延期記録へ残し、延期の無い実行で記録を削除する。

    自動更新タイマーはこの記録を確認して上流変更の有無によらず再試行するため、延期が残る間は記録が要る。
    """
    monkeypatch.setattr(common, "find_dotfiles_root", lambda: tmp_path / "dotfiles")
    record = deferred_updates.record_path(tmp_path / "dotfiles")
    deferring = [
        ("延期する工程", lambda: post_apply_outcome.PostApplyOutcome(deferred=True)),
        ("通常の工程", lambda: post_apply_outcome.PostApplyOutcome(changed=True)),
    ]

    with pytest.raises(SystemExit) as exc_info:
        post_apply.main(runner=lambda: post_apply.run(steps=deferring))

    assert exc_info.value.code == 0
    assert json.loads(record.read_text(encoding="utf-8"))["steps"] == ["延期する工程"]

    with pytest.raises(SystemExit) as exc_info:
        post_apply.main(runner=lambda: post_apply.run(steps=deferring[1:]))

    assert exc_info.value.code == 0
    assert not record.exists()


def test_deferred_record_write_failure_fails_post_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sync_report_path: Path
) -> None:
    """延期記録を保存できない場合は、自動の再試行が成立しないため失敗として終了コード1で伝える。"""
    monkeypatch.setattr(common, "find_dotfiles_root", lambda: tmp_path / "dotfiles")

    def fail(_steps: list[str]) -> None:
        raise OSError("書込権限が無い")

    monkeypatch.setattr(deferred_updates, "write", fail)

    with pytest.raises(SystemExit) as exc_info:
        post_apply.main(
            runner=lambda: post_apply.run(steps=[("延期する工程", lambda: post_apply_outcome.PostApplyOutcome(deferred=True))])
        )

    assert exc_info.value.code == 1
    failed_steps = json.loads(sync_report_path.read_text(encoding="utf-8"))["post_apply"]["failed_steps"]
    assert [step["name"] for step in failed_steps] == ["延期記録"]
    assert "書込権限が無い" in failed_steps[0]["reason"]


def test_sync_report_preserves_report_of_the_same_run(monkeypatch: pytest.MonkeyPatch, sync_report_path: Path) -> None:
    """同じ実行の記録がある場合は、その記録へpost-apply段の結果を足す。"""
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


def test_persistent_log_uses_size_limited_rotation() -> None:
    """永続ログの総容量を固定世代数で制限する。"""
    root_logger = logging.getLogger()
    with post_apply._configure_logging() as persistent_log_ready:  # noqa: SLF001
        current_handlers = root_logger.handlers.copy()
        file_handlers = [handler for handler in current_handlers if isinstance(handler, logging.handlers.RotatingFileHandler)]
        assert persistent_log_ready
        assert len(file_handlers) == 1
        assert file_handlers[0].maxBytes == post_apply._UPDATE_LOG_MAX_BYTES  # noqa: SLF001
        assert file_handlers[0].backupCount == post_apply._UPDATE_LOG_BACKUP_COUNT  # noqa: SLF001


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


def _registered_paths(base: Path) -> list[Path]:
    """撤去表の基点`~/<base>`へ登録された相対パスを返す。"""
    return [entry.path for entry in post_apply._REMOVED_PATHS[_IMPORT_HOME / base]]  # noqa: SLF001


def test_removed_paths_bases_are_distribution_targets() -> None:
    """撤去表の基点は配布先だけで、作業ツリー（`~/dotfiles`）を指さない。"""
    startup = Path("AppData") / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    allowed = {
        _IMPORT_HOME / base
        for base in (
            Path(".claude"),
            Path(".codex"),
            Path(".config"),
            Path(".ipython"),
            Path("bin"),
            Path(".local") / "bin",
            startup,
        )
    }
    # ホーム直下へ配布した設定（`~/.screenrc`など）は内容一致の表だけがホームを基点にする。
    assert set(post_apply._REMOVED_PATHS) <= allowed  # noqa: SLF001
    assert set(post_apply._REMOVED_PATHS_IF_CONTENT) <= allowed | {_IMPORT_HOME}  # noqa: SLF001
    bases = set(post_apply._REMOVED_PATHS) | set(post_apply._REMOVED_PATHS_IF_CONTENT)  # noqa: SLF001
    assert _IMPORT_HOME / "dotfiles" not in bases


def test_removed_session_review_skill_paths_cover_claude_and_codex() -> None:
    """旧個人スキルをClaude CodeとCodexの両配布先からcleanupする。"""
    relative = Path("skills/session-review-dotfiles")
    assert relative in _registered_paths(Path(".claude"))
    assert relative in _registered_paths(Path(".codex"))


def test_removed_sync_cross_project_paths_cover_claude_and_codex() -> None:
    """改名前の個人プロジェクト運用スキルを両配布先からcleanupする。"""
    relative = Path("skills/sync-cross-project")
    assert relative in _registered_paths(Path(".claude"))
    assert relative in _registered_paths(Path(".codex"))


def test_legacy_reference_directory_is_cleanup_target() -> None:
    """スキル配下以外の旧`references/`を配布先から削除する。"""
    assert Path("references") in _registered_paths(Path(".claude"))


def test_removes_legacy_plans_viewer_config_and_shim() -> None:
    """旧計画ビューアーの設定・CLI・Windowsスタートアップ用shimを配布先から除去する。"""
    assert Path("pytools/claude-plans-viewer.toml") in _registered_paths(Path(".config"))
    local_bin = _registered_paths(Path(".local") / "bin")
    assert Path("claude-plans-viewer") in local_bin
    assert Path("claude-plans-viewer.exe") in local_bin
    startup = _IMPORT_HOME / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    assert Path("claude-plans-viewer.cmd") in [entry.path for entry in post_apply._REMOVED_PATHS_IF_CONTENT[startup]]  # noqa: SLF001
    # 旧systemd unitは停止と無効化を経てから削除するため、この一括削除の対象へ含めない。
    for entries in post_apply._REMOVED_PATHS.values():  # noqa: SLF001
        assert not [entry for entry in entries if "claude-plans-viewer.service" in entry.path.name]


def test_removes_legacy_atk_launcher_but_keeps_current_wrappers() -> None:
    """作業ツリー版を覆い隠す旧atkランチャーを登録し、現行のサービス用・hook用ラッパーは登録しない。"""
    local_bin = _registered_paths(Path(".local") / "bin")
    assert Path("atk") in local_bin
    assert Path("atk.cmd") in local_bin
    for kept in ("atk-serve", "atk-hook", "atk-hook.cmd"):
        assert Path(kept) not in local_bin


def test_removes_flag_files_of_retired_steps_but_keeps_current_config() -> None:
    """廃止した工程のフラグファイルを登録し、現行のagent-toolkit設定は登録しない。"""
    config = _registered_paths(Path(".config"))
    assert Path("agent-toolkit/feedback-inbox.enabled") in config
    assert Path("agent-toolkit/review-balance-mode.claude-heavy") in config
    for kept in ("agent-toolkit/config.json", "agent-toolkit/serve.toml"):
        assert Path(kept) not in config


@pytest.mark.parametrize(
    ("base", "removed", "kept"),
    [
        (
            Path(".local") / "bin",
            ("atk", "atk.cmd"),
            ("atk-serve", "atk-hook", "atk-hook.cmd", "uv"),
        ),
        (
            Path(".config"),
            ("agent-toolkit/feedback-inbox.enabled", "agent-toolkit/review-balance-mode.claude-heavy"),
            ("agent-toolkit/config.json", "agent-toolkit/serve.toml"),
        ),
    ],
)
def test_cleanup_applies_registered_legacy_paths_without_touching_current_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    base: Path,
    removed: tuple[str, ...],
    kept: tuple[str, ...],
) -> None:
    """実際の登録内容を一時ホームへ適用し、旧生成物だけが削除されることを確かめる。"""
    registered = post_apply._REMOVED_PATHS[_IMPORT_HOME / base]  # noqa: SLF001
    home_dir = tmp_path / "home"
    target_dir = home_dir / base
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("USERPROFILE", str(home_dir))
    monkeypatch.setattr(post_apply, "_REMOVED_PATHS", {target_dir: registered})
    monkeypatch.setattr(post_apply, "_REMOVED_PATHS_IF_CONTENT", {})
    for name in (*removed, *kept):
        (target_dir / name).parent.mkdir(parents=True, exist_ok=True)
        (target_dir / name).write_text("x\n", encoding="utf-8")

    changed = post_apply._cleanup_removed_paths().changed  # noqa: SLF001

    assert changed is True
    for name in removed:
        assert not (target_dir / name).exists()
    for name in kept:
        assert (target_dir / name).is_file()


def test_removed_ipython_profile_is_limited_to_profile_default() -> None:
    """旧IPythonプロファイルのcleanup対象に利用中のprofile_ipyを含めない。"""
    paths = _registered_paths(Path(".ipython"))
    assert Path("profile_default/startup/README") in paths
    assert not any(path.is_relative_to("profile_ipy") for path in paths)


def _redirect_removed_paths_to(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Path:
    """旧配布物削除先を一時ホームへ限定し、撤去表のIPythonの項目だけを登録する。"""
    home_dir = tmp_path / "home"
    ipython_dir = home_dir / ".ipython"
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("USERPROFILE", str(home_dir))
    monkeypatch.setattr(
        post_apply,
        "_REMOVED_PATHS",
        {ipython_dir: post_apply._REMOVED_PATHS[_IMPORT_HOME / ".ipython"]},  # noqa: SLF001
    )
    monkeypatch.setattr(post_apply, "_REMOVED_PATHS_IF_CONTENT", {})
    return ipython_dir


def test_cleanup_removes_empty_ipython_dirs_from_table(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """配布済みREADMEの削除後、撤去表の空ディレクトリの項目で空の親を深い順で除去する。"""
    ipython_dir = _redirect_removed_paths_to(monkeypatch, tmp_path)
    default_readme = ipython_dir / "profile_default/startup/README"
    default_readme.parent.mkdir(parents=True)
    default_readme.write_text("old\n", encoding="utf-8")

    changed = post_apply._cleanup_removed_paths().changed  # noqa: SLF001

    assert changed is True
    assert not (ipython_dir / "profile_default").exists()


def test_removed_ipython_profile_cleanup_preserves_user_file_in_startup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """startupにユーザーファイルが残る場合はprofile_defaultまで保持する。"""
    ipython_dir = _redirect_removed_paths_to(monkeypatch, tmp_path)
    default_readme = ipython_dir / "profile_default/startup/README"
    default_readme.parent.mkdir(parents=True)
    default_readme.write_text("old\n", encoding="utf-8")
    user_script = default_readme.parent / "00-user.py"
    user_script.write_text("print('user')\n", encoding="utf-8")

    changed = post_apply._cleanup_removed_paths().changed  # noqa: SLF001

    assert changed is True
    assert not default_readme.exists()
    assert user_script.read_text(encoding="utf-8") == "print('user')\n"
    assert (ipython_dir / "profile_default/startup").is_dir()
    assert (ipython_dir / "profile_default").is_dir()


def test_removed_ipython_profile_cleanup_preserves_user_file_in_profile_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """profile_default直下にユーザーファイルが残る場合はルートだけを保持する。"""
    ipython_dir = _redirect_removed_paths_to(monkeypatch, tmp_path)
    default_readme = ipython_dir / "profile_default/startup/README"
    default_readme.parent.mkdir(parents=True)
    default_readme.write_text("old\n", encoding="utf-8")
    user_config = ipython_dir / "profile_default/ipython_config.py"
    user_config.write_text("c = get_config()\n", encoding="utf-8")
    active_config = ipython_dir / "profile_ipy/ipython_config.py"
    active_config.parent.mkdir(parents=True)
    active_config.write_text("active\n", encoding="utf-8")

    changed = post_apply._cleanup_removed_paths().changed  # noqa: SLF001

    assert changed is True
    assert not default_readme.exists()
    assert not (ipython_dir / "profile_default/startup").exists()
    assert user_config.read_text(encoding="utf-8") == "c = get_config()\n"
    assert (ipython_dir / "profile_default").is_dir()
    assert active_config.read_text(encoding="utf-8") == "active\n"


def test_removed_ipython_profile_cleanup_skips_missing_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """配布済みファイルも親ディレクトリも存在しない場合は変更なしとする。"""
    _redirect_removed_paths_to(monkeypatch, tmp_path)

    changed = post_apply._cleanup_removed_paths().changed  # noqa: SLF001

    assert changed is False


def test_removed_ipython_profile_cleanup_preserves_symlink_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """profile_defaultが外部リンクの場合はリンク先の空ディレクトリを削除しない。"""
    ipython_dir = _redirect_removed_paths_to(monkeypatch, tmp_path)
    outside_profile = tmp_path / "outside-profile"
    outside_startup = outside_profile / "startup"
    outside_startup.mkdir(parents=True)
    ipython_dir.mkdir(parents=True)
    profile_link = ipython_dir / "profile_default"
    profile_link.symlink_to(outside_profile, target_is_directory=True)

    changed = post_apply._cleanup_removed_paths().changed  # noqa: SLF001

    assert changed is False
    assert profile_link.is_symlink()
    assert outside_startup.is_dir()


def test_cleanup_removes_unedited_ipython_kernel_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    pytestconfig: pytest.Config,
) -> None:
    """撤去前の配布物と同じ`ipython_kernel_config.py`は削除し、編集されたものは残す。"""
    distributed = (pytestconfig.rootpath / "testdata/post_apply/ipython_kernel_config.py.txt").read_bytes()
    home_dir = tmp_path / "home"
    ipython_dir = home_dir / ".ipython"
    monkeypatch.setattr(post_apply, "_REMOVED_PATHS", {})
    monkeypatch.setattr(
        post_apply,
        "_REMOVED_PATHS_IF_CONTENT",
        {ipython_dir: post_apply._REMOVED_PATHS_IF_CONTENT[_IMPORT_HOME / ".ipython"]},  # noqa: SLF001
    )
    target = ipython_dir / "profile_ipy" / "ipython_kernel_config.py"
    target.parent.mkdir(parents=True)

    target.write_bytes(distributed + b"c.IPKernelApp.matplotlib = 'inline'\n")
    assert post_apply._cleanup_removed_paths().changed is False  # noqa: SLF001
    assert target.exists()

    target.write_bytes(distributed)
    assert post_apply._cleanup_removed_paths().changed is True  # noqa: SLF001
    assert not target.exists()


@pytest.mark.parametrize(
    ("base", "relative", "fixture_name"),
    [
        (Path(), ".screenrc", "screenrc.txt"),
        (Path(".config"), "xonsh/rc.xsh", "xonsh_rc.xsh.txt"),
        (Path(".config"), "yapf/style", "yapf_style.txt"),
        (Path(".config"), "pypoetry/config.toml", "pypoetry_config.toml.txt"),
        (Path(".config"), "rest-client/environment.json", "rest_client_environment.json.txt"),
    ],
)
def test_cleanup_removes_unedited_retired_configs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    pytestconfig: pytest.Config,
    base: Path,
    relative: str,
    fixture_name: str,
) -> None:
    """撤去した配布設定は撤去前の配布物と同じ内容なら削除し、1バイトでも違えば残す。"""
    distributed = (pytestconfig.rootpath / "testdata/post_apply" / fixture_name).read_bytes()
    target_dir = tmp_path / "home" / base
    monkeypatch.setattr(post_apply, "_REMOVED_PATHS", {})
    monkeypatch.setattr(
        post_apply,
        "_REMOVED_PATHS_IF_CONTENT",
        {target_dir: post_apply._REMOVED_PATHS_IF_CONTENT[_IMPORT_HOME / base]},  # noqa: SLF001
    )
    target = target_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)

    target.write_bytes(distributed + b"#")
    assert post_apply._cleanup_removed_paths().changed is False  # noqa: SLF001
    assert target.exists()

    target.write_bytes(distributed)
    assert post_apply._cleanup_removed_paths().changed is True  # noqa: SLF001
    assert not target.exists()


def _make_step(name: str, calls: list[str], changed: bool = False):
    """呼び出し記録を残すステップ関数を返すヘルパー。"""

    def fn() -> post_apply_outcome.PostApplyOutcome:
        calls.append(name)
        return post_apply_outcome.PostApplyOutcome(changed=changed)

    return fn


def _make_broken_step(name: str, calls: list[str], message: str = "boom"):
    """例外を送出するステップ関数を返すヘルパー。"""

    def fn() -> post_apply_outcome.PostApplyOutcome:
        calls.append(name)
        raise RuntimeError(message)

    return fn


def _make_plugin_step(recommendations: list[str]):
    """推奨コマンドリストを返すステップ関数を返すヘルパー。"""

    def fn() -> post_apply_outcome.PostApplyOutcome:
        return post_apply_outcome.PostApplyOutcome(changed=True, recommendations=tuple(recommendations))

    return fn


def _make_outcome_step(*, changed: bool, notices: tuple[post_apply_outcome.PostApplyNotice, ...]):
    """構造化したpost-apply結果を返すステップ関数を返す。"""

    def fn() -> post_apply_outcome.PostApplyOutcome:
        return post_apply_outcome.PostApplyOutcome(changed=changed, notices=notices)

    return fn


class TestRun:
    """post_apply.run() の振る舞い。"""

    def test_all_steps_succeed(self):
        """全ステップ成功時、ok=True のリストが返る。"""
        calls: list[str] = []
        steps: list[tuple[str, post_apply.Callable[[], post_apply_outcome.PostApplyOutcome]]] = [
            ("Claude 設定", _make_step("claude", calls, changed=True)),
            ("VSCode 設定", _make_step("vscode", calls)),
            ("SSH config", _make_step("ssh", calls)),
            ("旧配布物の削除", _make_step("cleanup", calls)),
            ("npm/pnpm サプライチェーン対策", _make_step("npmrc", calls, changed=True)),
            ("mise セットアップ", _make_step("mise", calls)),
            ("Claude Code plugin のインストール", _make_step("plugins", calls)),
            ("旧Codex User scope MCP登録の移行", _make_step("codex-migration", calls)),
            ("libarchive (Windows)", _make_step("libarchive", calls)),
            ("atk serve 再起動 (Linux)", _make_step("atk-serve-restart-linux", calls)),
        ]

        results, recommendations = post_apply.run(steps=steps)

        assert sorted(calls) == sorted(
            [
                "claude",
                "vscode",
                "ssh",
                "cleanup",
                "npmrc",
                "mise",
                "plugins",
                "codex-migration",
                "libarchive",
                "atk-serve-restart-linux",
            ]
        )
        assert all(r.ok for r in results)
        assert [r.changed for r in results] == [
            True,
            False,
            False,
            False,
            True,
            False,
            False,
            False,
            False,
            False,
        ]
        assert not recommendations

    def test_failing_step_does_not_stop_others(self):
        """途中ステップが例外を送出しても後続は実行される。"""
        calls: list[str] = []
        steps: list[tuple[str, post_apply.Callable[[], post_apply_outcome.PostApplyOutcome]]] = [
            ("Claude 設定", _make_step("claude", calls)),
            ("VSCode 設定", _make_step("vscode", calls)),
            ("SSH config", _make_broken_step("broken", calls)),
            ("旧配布物の削除", _make_step("cleanup", calls)),
            ("npm/pnpm サプライチェーン対策", _make_step("npmrc", calls)),
            ("mise セットアップ", _make_step("mise", calls)),
            ("Claude Code plugin のインストール", _make_step("plugins", calls)),
            ("旧Codex User scope MCP登録の移行", _make_step("codex-migration", calls)),
            ("libarchive (Windows)", _make_step("libarchive", calls)),
            ("atk serve 再起動 (Linux)", _make_step("atk-serve-restart-linux", calls)),
        ]

        results, _ = post_apply.run(steps=steps)

        assert sorted(calls) == sorted(
            [
                "claude",
                "vscode",
                "broken",
                "cleanup",
                "npmrc",
                "mise",
                "plugins",
                "codex-migration",
                "libarchive",
                "atk-serve-restart-linux",
            ]
        )
        ok_flags = [r.ok for r in results]
        assert ok_flags == [True, True, False, True, True, True, True, True, True, True]

    def test_foreground_completion_reports_step_duration(self, caplog: pytest.LogCaptureFixture) -> None:
        """前景ステップの完了行へステップ本体の所要時間を表示する。"""

        def foreground() -> post_apply_outcome.PostApplyOutcome:
            time.sleep(0.3)
            return post_apply_outcome.PostApplyOutcome()

        caplog.set_level(logging.INFO)
        post_apply.run([post_apply._StepSpec("前景", foreground)])  # noqa: SLF001

        completion = next(record.getMessage() for record in caplog.records if record.getMessage().startswith("[1/1] 前景 ("))
        match = re.fullmatch(r"\[1/1] 前景 \(([0-9]+\.[0-9])秒\)", completion)
        assert match is not None
        assert float(match.group(1)) >= 0.2

    def test_independent_steps_overlap_and_predecessor_blocks_successor(self) -> None:
        """先行工程を持たないステップは同時に実行し、後続ステップは先行工程の完了後に開始する。"""
        first_started = threading.Event()
        second_started = threading.Event()
        events: list[str] = []
        lock = threading.Lock()

        def record(event: str) -> None:
            with lock:
                events.append(event)

        def first() -> post_apply_outcome.PostApplyOutcome:
            record("first-start")
            first_started.set()
            # 2番目のステップが同時に動いていなければ、待機が上限に達して失敗する。
            assert second_started.wait(timeout=5)
            time.sleep(0.1)
            record("first-end")
            return post_apply_outcome.PostApplyOutcome()

        def second() -> post_apply_outcome.PostApplyOutcome:
            second_started.set()
            assert first_started.wait(timeout=5)
            return post_apply_outcome.PostApplyOutcome()

        def successor() -> post_apply_outcome.PostApplyOutcome:
            record("successor-start")
            return post_apply_outcome.PostApplyOutcome()

        results, _ = post_apply.run(
            [
                post_apply._StepSpec("first", first),  # noqa: SLF001
                post_apply._StepSpec("second", second),  # noqa: SLF001
                post_apply._StepSpec("successor", successor, after=("first",)),  # noqa: SLF001
            ]
        )

        assert all(result.ok for result in results)
        assert events.index("first-end") < events.index("successor-start")

    def test_failed_predecessor_does_not_stop_successor(self) -> None:
        """先行工程が失敗しても後続ステップを実行し、失敗をそのステップの結果にとどめる。"""
        calls: list[str] = []
        results, _ = post_apply.run(
            [
                post_apply._StepSpec("先行", _make_broken_step("before", calls)),  # noqa: SLF001
                post_apply._StepSpec("後続", _make_step("after", calls, changed=True), after=("先行",)),  # noqa: SLF001
            ]
        )

        assert calls == ["before", "after"]
        assert [(result.name, result.ok, result.changed) for result in results] == [
            ("先行", False, False),
            ("後続", True, True),
        ]

    def test_after_all_preceding_waits_for_every_earlier_step(self) -> None:
        """列挙順で前にある全ステップの完了後に開始する。"""
        finished: list[str] = []
        observed: list[list[str]] = []

        def slow() -> post_apply_outcome.PostApplyOutcome:
            time.sleep(0.2)
            finished.append("slow")
            return post_apply_outcome.PostApplyOutcome()

        def last() -> post_apply_outcome.PostApplyOutcome:
            observed.append(list(finished))
            return post_apply_outcome.PostApplyOutcome()

        post_apply.run(
            [
                post_apply._StepSpec("slow", slow),  # noqa: SLF001
                post_apply._StepSpec("fast", _make_step("fast", finished)),  # noqa: SLF001
                post_apply._StepSpec("last", last, after_all_preceding=True),  # noqa: SLF001
            ]
        )

        assert sorted(observed[0]) == ["fast", "slow"]

    def test_output_and_results_follow_enumeration_order(self, caplog: pytest.LogCaptureFixture) -> None:
        """後に完了した先頭ステップの出力を、先に完了した後続ステップより前へまとめて出力する。"""
        second_done = threading.Event()

        def first() -> post_apply_outcome.PostApplyOutcome:
            assert second_done.wait(timeout=5)
            logging.getLogger("order-test").info("一のログ")
            return post_apply_outcome.PostApplyOutcome()

        def second() -> post_apply_outcome.PostApplyOutcome:
            logging.getLogger("order-test").info("二のログ")
            second_done.set()
            return post_apply_outcome.PostApplyOutcome(changed=True, recommendations=tuple(["cmd-2"]))

        caplog.set_level(logging.INFO)
        results, recommendations = post_apply.run(
            [
                post_apply._StepSpec("一", first),  # noqa: SLF001
                post_apply._StepSpec("二", second),  # noqa: SLF001
            ]
        )

        assert [result.name for result in results] == ["一", "二"]
        assert recommendations == ["cmd-2"]
        messages = [record.getMessage() for record in caplog.records]
        first_heading = next(message for message in messages if message.startswith("[1/2] 一 ("))
        second_heading = next(message for message in messages if message.startswith("[2/2] 二 ("))
        order = [messages.index(item) for item in (first_heading, "一のログ", second_heading, "二のログ")]
        assert order == sorted(order)

    @pytest.mark.parametrize(
        ("steps", "message"),
        [
            (
                [post_apply._StepSpec("a", post_apply_outcome.PostApplyOutcome, after=("missing",))],
                "先行工程が見つかりません",
            ),  # noqa: SLF001
            (
                [
                    post_apply._StepSpec("a", post_apply_outcome.PostApplyOutcome, after=("b",)),  # noqa: SLF001
                    post_apply._StepSpec("b", post_apply_outcome.PostApplyOutcome, after=("a",)),  # noqa: SLF001
                ],
                "先行工程が循環しています",
            ),
        ],
    )
    def test_invalid_predecessor_declaration_is_rejected(self, steps: list[post_apply._StepSpec], message: str) -> None:  # noqa: SLF001
        """未知の先行工程と循環は実行前に拒否する。"""
        with pytest.raises(ValueError, match=message):
            post_apply.run(steps)

    def test_screen_hides_noise_lines_and_persistent_log_keeps_them(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """HTTP Request行、claude CLIの実行記録、対象外OSのステップ、開始行を画面から外し、永続ログへ残す。"""
        monkeypatch.setattr(post_apply.sys, "platform", "linux")
        monkeypatch.setattr(common, "resolve_executable", lambda name, **_kwargs: Path(name))
        monkeypatch.setattr(
            common,
            "run_subprocess",
            lambda cmd, **_kwargs: subprocess.CompletedProcess(cmd, 0, "", ""),
        )
        windows_calls: list[str] = []

        def http_step() -> post_apply_outcome.PostApplyOutcome:
            logging.getLogger("httpx").info('HTTP Request: GET https://example.invalid "HTTP/1.1 200 OK"')
            logging.getLogger("result-test").info("    http: 変更なし")
            return post_apply_outcome.PostApplyOutcome()

        def claude_step() -> post_apply_outcome.PostApplyOutcome:
            claude_common.run_claude(["plugin", "list"])
            return post_apply_outcome.PostApplyOutcome()

        steps = [
            post_apply._StepSpec("HTTPを使う工程", http_step),  # noqa: SLF001
            post_apply._StepSpec("claudeを使う工程", claude_step),  # noqa: SLF001
            post_apply._StepSpec(  # noqa: SLF001
                "Windows専用工程", _make_step("windows", windows_calls), platforms=("win32",)
            ),
        ]

        with pytest.raises(SystemExit) as exc_info:
            post_apply.main(runner=lambda: post_apply.run(steps))

        assert exc_info.value.code == 0
        assert not windows_calls
        out = capsys.readouterr().out
        assert "HTTP Request:" not in out
        assert "claude: exec:" not in out
        assert "claude: exit" not in out
        assert "Windows専用工程" not in out
        lines = out.splitlines()
        assert "  [1/3] HTTPを使う工程" not in lines
        assert "  [2/3] claudeを使う工程" not in lines
        heading = next(index for index, line in enumerate(lines) if line.startswith("  [1/3] HTTPを使う工程 ("))
        assert lines[heading + 1] == "      http: 変更なし"
        assert any(line.startswith("  [2/3] claudeを使う工程 (") for line in lines)
        assert "完了: 更新 0 件 / スキップ 3 件 / 失敗 0 件" in out
        log_text = post_apply._UPDATE_LOG_PATH.read_text(encoding="utf-8")  # noqa: SLF001
        assert "HTTP Request: GET" in log_text
        assert "claude: exec: plugin list" in log_text
        assert "claude: exit 0: plugin list" in log_text
        assert "[1/3] HTTPを使う工程\n" in log_text
        assert "[3/3] Windows専用工程: 実行中のOSは対象外のため実行しない" in log_text

    def test_main_exits_1_on_failure(self):
        """失敗があれば main() は SystemExit(1) で終了する。"""
        calls: list[str] = []
        steps: list[tuple[str, post_apply.Callable[[], post_apply_outcome.PostApplyOutcome]]] = [
            ("ok", _make_step("ok", calls)),
            ("broken", _make_broken_step("broken", calls)),
        ]
        with pytest.raises(SystemExit) as exc_info:
            post_apply.main(runner=lambda: post_apply.run(steps=steps))
        assert exc_info.value.code == 1

    def test_cli_install_failure_marks_step_failed_continues_and_exits_1(self):
        """CLI導入失敗を失敗結果へ変換し、後続実行後に終了コード1とする。"""
        calls: list[str] = []
        steps: list[tuple[str, post_apply.Callable[[], post_apply_outcome.PostApplyOutcome]]] = [
            ("Codex CLI の導入と更新", _make_broken_step("codex", calls)),
            ("後続ステップ", _make_step("later", calls)),
        ]

        results, _ = post_apply.run(steps=steps)

        assert sorted(calls) == ["codex", "later"]
        assert [(result.ok, result.changed) for result in results] == [(False, False), (True, False)]
        with pytest.raises(SystemExit) as exc_info:
            post_apply.main(runner=lambda: (results, []))
        assert exc_info.value.code == 1

    def test_statusline_development_failure_marks_step_failed_continues_and_exits_1(self):
        """statusline開発版の導入失敗を失敗結果へ変換し、後続実行後に終了コード1とする。"""
        calls: list[str] = []
        steps: list[tuple[str, post_apply.Callable[[], post_apply_outcome.PostApplyOutcome]]] = [
            ("claude-statusline バイナリの取得", _make_broken_step("statusline", calls)),
            ("後続ステップ", _make_step("later", calls)),
        ]

        results, _ = post_apply.run(steps=steps)

        assert sorted(calls) == ["later", "statusline"]
        assert [(result.ok, result.changed) for result in results] == [(False, False), (True, False)]
        with pytest.raises(SystemExit) as exc_info:
            post_apply.main(runner=lambda: (results, []))
        assert exc_info.value.code == 1

    def test_main_exits_0_on_success(self):
        """全て成功なら main() は SystemExit(0) で正常終了する。"""
        calls: list[str] = []
        steps: list[tuple[str, post_apply.Callable[[], post_apply_outcome.PostApplyOutcome]]] = [
            ("ok", _make_step("ok", calls)),
        ]
        with pytest.raises(SystemExit) as exc_info:
            post_apply.main(runner=lambda: post_apply.run(steps=steps))
        assert exc_info.value.code == 0

    def test_main_splits_info_and_errors_between_streams(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """正常な状態表示はstdout、失敗一覧はstderrへ出力する。"""
        calls: list[str] = []
        steps: list[tuple[str, post_apply.Callable[[], post_apply_outcome.PostApplyOutcome]]] = [
            ("ok", _make_step("ok", calls)),
            ("broken", _make_broken_step("broken", calls)),
        ]

        with pytest.raises(SystemExit) as exc_info:
            post_apply.main(runner=lambda: post_apply.run(steps=steps))

        assert exc_info.value.code == 1
        assert sorted(calls) == ["broken", "ok"]
        captured = capsys.readouterr()
        assert "完了: 更新 0 件 / スキップ 1 件 / 失敗 1 件" in captured.out
        assert "失敗したステップ" not in captured.out
        assert "失敗したステップ: broken" in captured.err
        assert "完了:" not in captured.err

    def test_structured_outcome_preserves_changed_and_notices(self) -> None:
        """構造化結果の変更有無と案内をステップ結果へ保持する。"""
        notice = post_apply_outcome.PostApplyNotice("Codex pluginを更新しました。")
        steps: list[tuple[str, post_apply.Callable[[], post_apply_outcome.PostApplyOutcome]]] = [
            ("Codex plugin", _make_outcome_step(changed=True, notices=(notice,))),
        ]

        results, recommendations = post_apply.run(steps=steps)

        assert not recommendations
        assert results[0].changed is True
        assert results[0].notices == (notice,)

    def test_main_prints_deduplicated_notice_without_command(self, capsys: pytest.CaptureFixture[str]) -> None:
        """commandを持たない重複案内をstderrへ1回表示する。"""
        notice = post_apply_outcome.PostApplyNotice("Codex pluginを更新しました。")
        steps: list[tuple[str, post_apply.Callable[[], post_apply_outcome.PostApplyOutcome]]] = [
            ("first", _make_outcome_step(changed=True, notices=(notice,))),
            ("second", _make_outcome_step(changed=True, notices=(notice,))),
        ]

        with pytest.raises(SystemExit) as exc_info:
            post_apply.main(runner=lambda: post_apply.run(steps=steps))

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "codex app-server daemon restart" not in captured.out
        assert captured.err.count("Codex pluginを更新しました。") == 1
        assert captured.err.splitlines()[-1].endswith("Codex pluginを更新しました。")

    def test_main_keeps_notice_on_later_failure(self, capsys: pytest.CaptureFixture[str]) -> None:
        """案内の発生後に後続が失敗しても非0終了と案内を両立する。"""
        calls: list[str] = []
        notice = post_apply_outcome.PostApplyNotice("Codex pluginを更新しました。")
        steps: list[tuple[str, post_apply.Callable[[], post_apply_outcome.PostApplyOutcome]]] = [
            ("Codex plugin", _make_outcome_step(changed=True, notices=(notice,))),
            ("broken", _make_broken_step("broken", calls)),
        ]

        with pytest.raises(SystemExit) as exc_info:
            post_apply.main(runner=lambda: post_apply.run(steps=steps))

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "失敗したステップ: broken" in captured.err
        assert captured.err.splitlines()[-1].endswith("Codex pluginを更新しました。")

    def test_main_keeps_notice_from_failed_codex_step(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Codex plugin処理が案内後に失敗しても案内と後続実行を保持する。"""
        calls: list[str] = []
        notice = post_apply_outcome.PostApplyNotice(
            "外部pluginを更新しました。",
            "codex app-server daemon restart",
        )
        assert notice.command is not None
        failure = f"local pluginの確定に失敗\n{notice.message}\n{notice.command}"
        steps: list[tuple[str, post_apply.Callable[[], post_apply_outcome.PostApplyOutcome]]] = [
            ("Codex plugin", _make_broken_step("codex", calls, failure)),
            ("later", _make_step("later", calls)),
        ]

        with pytest.raises(SystemExit) as exc_info:
            post_apply.main(runner=lambda: post_apply.run(steps=steps))

        assert exc_info.value.code == 1
        assert sorted(calls) == ["codex", "later"]
        captured = capsys.readouterr()
        assert captured.err.count(notice.message) == 1
        assert captured.err.count(notice.command) == 1


class TestSubstitutedHome:
    """HOMEを差し替えた実行で、HOMEの外にある実機の共有資源を操作するステップを実行しない契約。

    `systemctl --user`や`/dev/shm`はHOMEで解決されないため、手動観測やテストでHOMEだけを差し替えても
    実機の`atk-serve.service`の再起動や共有メモリー上のファイルの削除が起こる。
    """

    _HOST_RESOURCE_STEPS = (
        "Codex 診断ログの通常ストレージ復元 (Linux)",
        "atk serve 自動起動セットアップ (Linux)",
        "dotfiles自動更新タイマー セットアップ (Linux)",
    )

    def _default_steps_with_recorders(self, calls: list[str]) -> list[post_apply._StepSpec]:  # noqa: SLF001
        """`_DEFAULT_STEPS`にある3ステップの宣言を保ったまま、`run`だけを呼び出しの記録へ差し替える。"""
        steps = [step for step in post_apply._DEFAULT_STEPS if step.name in self._HOST_RESOURCE_STEPS]  # noqa: SLF001
        assert [step.name for step in steps] == list(self._HOST_RESOURCE_STEPS)
        return [dataclasses.replace(step, after=(), run=_make_step(step.name, calls)) for step in steps]

    def test_substituted_home_skips_host_resource_steps(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setattr(post_apply.sys, "platform", "linux")
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        systemctl_calls: list[object] = []
        monkeypatch.setattr(subprocess, "run", lambda *args, **_kwargs: systemctl_calls.append(args))
        calls: list[str] = []

        with caplog.at_level(logging.INFO):
            results, _ = post_apply.run(self._default_steps_with_recorders(calls))

        assert not calls
        assert not systemctl_calls
        assert [(result.ok, result.changed) for result in results] == [(True, False)] * 3
        for index, name in enumerate(self._HOST_RESOURCE_STEPS, start=1):
            record = next(r for r in caplog.records if r.getMessage().startswith(f"[{index}/3] {name}: "))
            assert "HOMEが実行ユーザーのホームと異なるため" in record.getMessage()
            # 手動観測の実行者が画面で確認できるよう、画面から外す印を付けない。
            assert not post_apply.log_format.is_log_only(record)

    def test_actual_home_runs_host_resource_steps(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import pwd  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,import-error  # POSIX専用でありWindowsには存在しない。

        monkeypatch.setattr(post_apply.sys, "platform", "linux")
        monkeypatch.setenv("HOME", pwd.getpwuid(os.getuid()).pw_dir)
        calls: list[str] = []

        post_apply.run(self._default_steps_with_recorders(calls))

        assert sorted(calls) == sorted(self._HOST_RESOURCE_STEPS)


class TestPytoolsInstallNotices:
    """テンプレートから渡されたpytools再導入状態の最終案内。"""

    def test_unset_state_does_not_print_notice(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """状態が未設定なら案内を表示しない。"""
        monkeypatch.delenv("DOTFILES_PYTOOLS_INSTALL_STATE", raising=False)
        monkeypatch.delenv("DOTFILES_PYTOOLS_INSTALL_DETAIL", raising=False)

        with pytest.raises(SystemExit) as exc_info:
            post_apply.main(runner=lambda: ([], []))

        assert exc_info.value.code == 0
        assert "pytoolsの再インストール" not in capsys.readouterr().err

    def test_empty_state_does_not_print_notice(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """状態が空文字列なら案内を表示しない。"""
        monkeypatch.setenv("DOTFILES_PYTOOLS_INSTALL_STATE", "")
        monkeypatch.setenv("DOTFILES_PYTOOLS_INSTALL_DETAIL", "ignored")

        with pytest.raises(SystemExit) as exc_info:
            post_apply.main(runner=lambda: ([], []))

        assert exc_info.value.code == 0
        assert "pytoolsの再インストール" not in capsys.readouterr().err

    @pytest.mark.parametrize(
        ("state", "expected"),
        [
            ("deferred", "pytoolsの再インストールを延期しました。"),
            ("failed", "pytoolsの再インストールに失敗しました。"),
        ],
    )
    def test_known_state_prints_notice(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
        state: str,
        expected: str,
    ) -> None:
        """延期と失敗を最終案内へ表示する。"""
        monkeypatch.setenv("DOTFILES_PYTOOLS_INSTALL_STATE", state)
        monkeypatch.delenv("DOTFILES_PYTOOLS_INSTALL_DETAIL", raising=False)

        with pytest.raises(SystemExit) as exc_info:
            post_apply.main(runner=lambda: ([], []))

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        (tmp_path / "notice.stdout.txt").write_text(captured.out, encoding="utf-8")
        (tmp_path / "notice.stderr.txt").write_text(captured.err, encoding="utf-8")
        assert expected in captured.err
        assert "update-dotfiles" in captured.err.splitlines()

    def test_notice_includes_detail(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """補足がある場合は状態案内の本文へ含める。"""
        monkeypatch.setenv("DOTFILES_PYTOOLS_INSTALL_STATE", "failed")
        monkeypatch.setenv("DOTFILES_PYTOOLS_INSTALL_DETAIL", "uv tool install error")

        with pytest.raises(SystemExit):
            post_apply.main(runner=lambda: ([], []))

        assert "詳細: uv tool install error" in capsys.readouterr().err

    def test_unknown_state_raises_value_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """送信契約にない状態を黙って無視しない。"""
        monkeypatch.setenv("DOTFILES_PYTOOLS_INSTALL_STATE", "unknown")

        with pytest.raises(ValueError, match="未知のpytools再導入状態"):
            post_apply.main(runner=lambda: ([], []))


class TestDefaultSteps:
    """`_DEFAULT_STEPS`の登録内容と先行工程・対象OSの宣言を検証する。"""

    # 共有資源と実行ファイルの前提から定めた先行工程。宣言の欠落は同時実行による競合を招く。
    _EXPECTED_PREDECESSORS: dict[str, set[str]] = {
        "mise セットアップ": {"npm/pnpm サプライチェーン対策", "bin PATH 登録 (Windows)"},
        "Codex CLI の導入と更新": {"mise セットアップ", "Codex 診断ログの通常ストレージ復元 (Linux)"},
        "Codex 診断ログの通常ストレージ復元 (Linux)": {"Codex リンクの同期"},
        "Codex の Claude MCP 登録削除": {"Codex CLI の導入と更新"},
        "Claude Code CLI の導入と更新": {"mise セットアップ"},
        "Codex plugin snapshot の生成": {"Claude Code plugin のインストール"},
        "Codex plugin のインストール": {
            "Codex の Claude MCP 登録削除",
            "Codex plugin snapshot の生成",
            "Codex リンクの同期",
            "旧配布物の削除",
        },
        "agent-toolkit ルールの同期": {"旧配布物の削除"},
        "Claude Code plugin のインストール": {"Claude Code CLI の導入と更新"},
        "Claude Code plugin cache の旧版削除": {"Claude Code plugin のインストール"},
        "agents_serverのuv環境ウォームアップ": {"Claude Code plugin のインストール", "Codex plugin のインストール"},
        "pyfltr MCPのuv環境ウォームアップ": {"Claude Code plugin のインストール", "Codex plugin のインストール"},
        "旧Codex User scope MCP登録の移行": {"Claude Code CLI の導入と更新", "Codex plugin のインストール"},
        "Claude 設定": {"Claude Code plugin のインストール", "旧Codex User scope MCP登録の移行"},
        "claude-statusline バイナリの取得": {"Codex CLI の導入と更新"},
        "libarchive (Windows)": {"mise セットアップ"},
        "atk serve 自動起動セットアップ (Linux)": {"Claude Code plugin のインストール"},
        "dotfiles自動更新タイマー セットアップ (Linux)": {"atk serve 自動起動セットアップ (Linux)"},
    }
    _WINDOWS_STEPS = {
        "bin PATH 登録 (Windows)",
        "MSYS 環境変数 (Windows)",
        "user.env 環境変数 (Windows)",
        "libarchive (Windows)",
        "Windowsレジストリ設定",
        "SendTo ショートカット (Windows)",
        "メディアリモコン自動起動 (Windows/media_remote)",
        "ユーザー PATH 整理 (Windows)",
    }
    _LINUX_STEPS = {
        "Codex 診断ログの通常ストレージ復元 (Linux)",
        "tmux プラグインの導入 (Linux)",
        "atk serve 自動起動セットアップ (Linux)",
        "dotfiles自動更新タイマー セットアップ (Linux)",
    }

    def test_step_names_are_unique_and_declarations_resolve(self) -> None:
        """ステップ名は一意であり、全ての先行工程の宣言が既存ステップを指し循環しない。"""
        steps = post_apply._DEFAULT_STEPS  # noqa: SLF001
        names = [step.name for step in steps]
        assert len(names) == len(set(names))
        post_apply._resolve_predecessors(steps)  # noqa: SLF001

    def test_predecessor_declarations(self) -> None:
        """共有資源を扱うステップの組へ先行工程を宣言する。"""
        declared = {step.name: set(step.after) for step in post_apply._DEFAULT_STEPS if step.after}  # noqa: SLF001
        assert declared == self._EXPECTED_PREDECESSORS

    def test_user_path_cleanup_follows_all_other_steps(self) -> None:
        """ユーザーPATHの整理は他の全ステップの後に実行する。"""
        steps = post_apply._DEFAULT_STEPS  # noqa: SLF001
        assert steps[-1].name == "ユーザー PATH 整理 (Windows)"
        assert [step.name for step in steps if step.after_all_preceding] == [
            "dotfilesプラグインの反映",
            "ユーザー PATH 整理 (Windows)",
        ]

    @pytest.mark.parametrize("platform", ["linux", "win32"])
    def test_dotfiles_plugins_follow_main_steps_and_precede_path_cleanup(
        self,
        monkeypatch: pytest.MonkeyPatch,
        platform: str,
    ) -> None:
        """通常の工程表で本体後処理を終えてからプラグインを反映し、最後にPATHを整理する。"""
        registered = post_apply._DEFAULT_STEPS
        plugin_steps = [step for step in registered if step.run is apply_dotfiles_plugins.run]
        assert len(plugin_steps) == 1
        plugin = plugin_steps[0]
        assert plugin.name == "dotfilesプラグインの反映"
        assert not plugin.platforms
        plugin_index = registered.index(plugin)
        expected_before = {step.name for step in registered[:plugin_index] if not step.platforms or platform in step.platforms}
        calls: list[str] = []

        def apply_plugins() -> post_apply_outcome.PostApplyOutcome:
            assert set(calls) == expected_before
            calls.append(plugin.name)
            return post_apply_outcome.PostApplyOutcome(changed=True)

        monkeypatch.setattr(post_apply, "sys", type("_Platform", (), {"platform": platform}))
        monkeypatch.setattr(post_apply, "_home_is_substituted", lambda: False)
        monkeypatch.setattr(
            post_apply,
            "_DEFAULT_STEPS",
            [
                dataclasses.replace(
                    step,
                    run=apply_plugins if step is plugin else _make_step(step.name, calls),
                )
                for step in registered
            ],
        )

        results, _ = post_apply.run()

        assert all(result.ok for result in results)
        assert calls.count(plugin.name) == 1
        if platform == "win32":
            assert calls[-2:] == [plugin.name, "ユーザー PATH 整理 (Windows)"]
        else:
            assert calls[-1] == plugin.name

    def test_platform_declarations(self) -> None:
        """実行中のOSでは何もしないステップへ対象OSを宣言する。"""
        steps = post_apply._DEFAULT_STEPS  # noqa: SLF001
        assert {step.name for step in steps if step.platforms == ("win32",)} == self._WINDOWS_STEPS
        assert {step.name for step in steps if step.platforms == ("linux",)} == self._LINUX_STEPS
        assert not [step.name for step in steps if step.platforms not in ((), ("win32",), ("linux",))]

    def test_pyfltr_mcp_warmup_registered_once(self) -> None:
        """pyfltr MCPのウォームアップを1回だけ登録する。"""
        names = [step.name for step in post_apply._DEFAULT_STEPS]  # noqa: SLF001
        assert names.count("pyfltr MCPのuv環境ウォームアップ") == 1

    def test_statusline_binary_step_registered(self):
        """claude-statuslineバイナリ取得ステップを1回登録する。"""
        names = [step.name for step in post_apply._DEFAULT_STEPS]  # pylint: disable=protected-access  # noqa: SLF001
        assert names.count("claude-statusline バイナリの取得") == 1

    def test_removed_steps_are_not_registered(self) -> None:
        """廃止済みの計画移行と旧計画ビューアーの自動起動を登録しない。"""
        names = [step.name for step in post_apply._DEFAULT_STEPS]  # noqa: SLF001
        assert not [name for name in names if "claude-plans-viewer" in name or "計画" in name]


class TestPluginRecommendations:
    """``install_claude_plugins.run()`` の推奨コマンド戻り値による案内出力。"""

    def test_prints_single_recommendation_without_continuation(
        self,
        capsys: pytest.CaptureFixture[str],
    ):
        """推奨コマンドが 1 件のみなら && も継続記号も付けず単一行で出力する。"""
        fake_recommendations = ["claude plugin install a --scope=user"]
        steps: list[tuple[str, post_apply.Callable[[], post_apply_outcome.PostApplyOutcome]]] = [
            ("plugins", _make_plugin_step(fake_recommendations)),
        ]
        with pytest.raises(SystemExit):
            post_apply.main(runner=lambda: post_apply.run(steps=steps))
        stdout_lines = capsys.readouterr().out.splitlines()
        assert any("推奨プラグイン設定" in line for line in stdout_lines)
        assert "claude plugin install a --scope=user" in stdout_lines
        # コマンド行は cmd.exe での貼り付け失敗を避けるため行頭インデントを付けない。
        assert not any(line.startswith(" ") for line in stdout_lines if "claude plugin" in line)
        assert not any("&&" in line for line in stdout_lines)

    def test_prints_multiple_recommendations_bash(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ):
        """bash 系では && \\ で連結し、最終行のみ継続記号なしで出力する。"""
        monkeypatch.setattr(post_apply.sys, "platform", "linux")
        fake_recommendations = [
            "claude plugin install a --scope=user",
            "claude plugin install b --scope=user",
            "claude plugin disable c --scope=user",
        ]
        steps: list[tuple[str, post_apply.Callable[[], post_apply_outcome.PostApplyOutcome]]] = [
            ("plugins", _make_plugin_step(fake_recommendations)),
        ]
        with pytest.raises(SystemExit):
            post_apply.main(runner=lambda: post_apply.run(steps=steps))
        stdout_lines = capsys.readouterr().out.splitlines()
        assert any("推奨プラグイン設定" in line for line in stdout_lines)
        assert "claude plugin install a --scope=user && \\" in stdout_lines
        assert "claude plugin install b --scope=user && \\" in stdout_lines
        assert "claude plugin disable c --scope=user" in stdout_lines
        # コマンド行は cmd.exe での貼り付け失敗を避けるため行頭インデントを付けない。
        assert not any(line.startswith(" ") for line in stdout_lines if "claude plugin" in line)

    def test_prints_multiple_recommendations_windows(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        capsys: pytest.CaptureFixture[str],
    ):
        """Windows では && ^ で連結し、最終行のみ継続記号なしで出力する。"""
        monkeypatch.setattr(post_apply.sys, "platform", "win32")
        fake_recommendations = [
            "claude plugin install a --scope=user",
            "claude plugin disable b --scope=user",
        ]
        steps: list[tuple[str, post_apply.Callable[[], post_apply_outcome.PostApplyOutcome]]] = [
            ("plugins", _make_plugin_step(fake_recommendations)),
        ]
        with caplog.at_level("INFO", logger=post_apply.logger.name), pytest.raises(SystemExit):
            post_apply.main(runner=lambda: post_apply.run(steps=steps))
        stdout_lines = capsys.readouterr().out.splitlines()
        assert "claude plugin install a --scope=user && ^" in stdout_lines
        assert "claude plugin disable b --scope=user" in stdout_lines
        # cmd.exe では `^` 継続後の行頭空白が解析エラーを起こすため、コマンド行は無インデントとする。
        assert not any(line.startswith(" ") for line in stdout_lines if "claude plugin" in line)

    def test_no_output_when_no_recommendations(
        self,
        caplog: pytest.LogCaptureFixture,
        capsys: pytest.CaptureFixture[str],
    ):
        """推奨コマンドが空なら案内を出力しない。"""
        steps: list[tuple[str, post_apply.Callable[[], post_apply_outcome.PostApplyOutcome]]] = [
            ("ok", _make_step("ok", [], changed=False)),
        ]
        with caplog.at_level("INFO", logger=post_apply.logger.name), pytest.raises(SystemExit):
            post_apply.main(runner=lambda: post_apply.run(steps=steps))
        messages = [record.getMessage() for record in caplog.records]
        assert not any("推奨プラグイン設定" in m for m in messages)
        stdout_lines = capsys.readouterr().out.splitlines()
        assert not any("claude plugin" in line for line in stdout_lines)


def test_all_steps_return_post_apply_outcome() -> None:
    """全工程の`run`の戻り値の型が`PostApplyOutcome`だけである。"""
    returns = {step.name: typing.get_type_hints(step.run).get("return") for step in post_apply._DEFAULT_STEPS}
    assert {name: hint for name, hint in returns.items() if hint is not post_apply_outcome.PostApplyOutcome} == {}


def test_predecessor_names_are_not_duplicated_literals() -> None:
    """工程名の文字列リテラルは`post_apply.py`に1回だけ書き、先行工程の指定は定数で参照する。"""
    source = Path(post_apply.__file__).read_text(encoding="utf-8")
    literals = [
        node.value for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    names = {step.name for step in post_apply._DEFAULT_STEPS}
    duplicated = sorted(name for name in names if literals.count(name) > 1)
    assert not duplicated


def test_platform_steps_have_no_module_os_guard() -> None:
    """`platforms`を宣言した工程のモジュールは、対象OSを自ら判定しない。"""
    guards = re.compile(r"\bsys\.platform\b|\bos\.name\b|\bplatform\.system\(")
    offenders = []
    for step in post_apply._DEFAULT_STEPS:
        if not step.platforms:
            continue
        module = inspect.getmodule(step.run)
        assert module is not None
        if module is post_apply:
            continue
        source = Path(inspect.getfile(module)).read_text(encoding="utf-8")
        if guards.search(source):
            offenders.append(module.__name__)
    assert not offenders


def test_internal_module_names_have_no_legacy_prefix_or_os_suffix() -> None:
    """`pytools/_internal/`のモジュール名に`warm_`と対象OSの接尾辞が無い。"""
    internal = Path(post_apply.__file__).parent / "_internal"
    pattern = re.compile(r"^(warm_.*|.*_(linux|windows)(_test)?)\.py$")
    assert not sorted(path.name for path in internal.glob("*.py") if pattern.match(path.name))


def test_step_returning_non_outcome_is_counted_as_failure() -> None:
    """契約に反する戻り値の工程は失敗と数え、後続の工程と集計を止めない。"""

    def legacy_step() -> bool:
        return False

    # 型注釈を持たない呼び出し元を模すため、型検査上は契約どおりの工程として渡す。
    legacy = typing.cast("typing.Callable[[], post_apply_outcome.PostApplyOutcome]", legacy_step)
    results, _ = post_apply.run([("legacy", legacy), ("next", post_apply_outcome.PostApplyOutcome)])

    assert [(result.name, result.ok) for result in results] == [("legacy", False), ("next", True)]
    assert results[0].reason is not None
    assert "PostApplyOutcome" in results[0].reason


def _isolate_default_steps(
    monkeypatch: pytest.MonkeyPatch,
    allowed: dict[str, typing.Callable[[], post_apply_outcome.PostApplyOutcome]],
) -> None:
    """通常工程表の順序とOS宣言を保ち、許可した工程以外の外部操作を置き換える。"""
    monkeypatch.setattr(
        post_apply,
        "_DEFAULT_STEPS",
        [
            dataclasses.replace(step, run=allowed.get(step.name, post_apply_outcome.PostApplyOutcome))
            for step in post_apply._DEFAULT_STEPS
        ],
    )


@pytest.mark.parametrize("missing_root", [True, False])
def test_normal_main_root_failure_reports_recovery_before_side_effects(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    sync_report_path: Path,
    missing_root: bool,
) -> None:
    """ルート解決に失敗した通常入口は確認対象と再実行手段を示し、工程とログを開始しない。"""
    root = tmp_path / "dotfiles"
    monkeypatch.setattr(common, "find_dotfiles_root", lambda: None if missing_root else root)

    def fail_git(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 128, "", "not a git repository")

    monkeypatch.setattr(common, "run_subprocess", fail_git)
    calls: list[str] = []
    _isolate_default_steps(monkeypatch, {"Gemini 設定": _make_step("gemini", calls)})
    with pytest.raises(SystemExit) as exc_info:
        post_apply.main([])
    assert exc_info.value.code == 2
    assert not calls
    assert not post_apply._UPDATE_LOG_PATH.exists()
    assert not sync_report_path.exists()
    captured = capsys.readouterr()
    (tmp_path / "root.stdout.txt").write_text(captured.out, encoding="utf-8")
    (tmp_path / "root.stderr.txt").write_text(captured.err, encoding="utf-8")
    assert not captured.out
    assert "git" in captured.err
    assert "status" in captured.err
    assert "dotfiles-post-apply" in captured.err
    if not missing_root:
        assert str(root) in captured.err
        assert "not a git repository" in captured.err


@pytest.mark.parametrize("failure_mode", ["outcome", "exception", "invalid"])
@pytest.mark.parametrize("log_available", [True, False])
def test_normal_main_reports_failure_and_runs_successor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    pytestconfig: pytest.Config,
    capsys: pytest.CaptureFixture[str],
    failure_mode: str,
    log_available: bool,
    sync_report_path: Path,
) -> None:
    """通常入口で失敗理由・次操作を表示し、実出力先へ記録して後続を完了する。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(common, "find_dotfiles_root", lambda: pytestconfig.rootpath)
    log_path = post_apply._UPDATE_LOG_PATH
    if not log_available:
        log_path.mkdir()
    calls: list[str] = []

    def fail() -> post_apply_outcome.PostApplyOutcome:
        calls.append("failure")
        if failure_mode == "exception":
            raise RuntimeError("受入検証の例外")
        if failure_mode == "invalid":
            return typing.cast("post_apply_outcome.PostApplyOutcome", False)
        return post_apply_outcome.PostApplyOutcome(
            failure="受入検証の書込失敗", next_action="対象設定の権限を直してupdate-dotfilesを再実行する"
        )

    def successor() -> post_apply_outcome.PostApplyOutcome:
        assert calls == ["failure"]
        calls.append("successor")
        return post_apply_outcome.PostApplyOutcome(changed=True)

    _isolate_default_steps(monkeypatch, {"Claude Code plugin のインストール": fail, "Claude 設定": successor})
    with pytest.raises(SystemExit) as exc_info:
        post_apply.main(["--allow-non-canonical-root"])
    assert exc_info.value.code == 1
    assert calls == ["failure", "successor"]
    captured = capsys.readouterr()
    (tmp_path / "main.stdout.txt").write_text(captured.out, encoding="utf-8")
    (tmp_path / "main.stderr.txt").write_text(captured.err, encoding="utf-8")
    reason = {
        "outcome": "受入検証の書込失敗",
        "exception": "RuntimeError: 受入検証の例外",
        "invalid": "戻り値が PostApplyOutcome ではない",
    }[failure_mode]
    assert reason in captured.err
    assert "次の操作:" in captured.err
    assert "update-dotfiles" in captured.err
    assert "失敗 1 件" in captured.out
    if failure_mode == "outcome":
        assert "対象設定の権限を直して" in captured.err
    if log_available:
        assert str(log_path) in captured.err
        log_text = log_path.read_text(encoding="utf-8")
        assert reason in log_text
        assert "次の操作:" in log_text
        assert "post-apply終了: exit=1" in log_text
    else:
        assert "永続ログを開始できませんでした" in captured.err
        assert "この標準エラー" in captured.err
        assert f"永続ログ {log_path}" not in captured.err
        assert "永続ログ:" not in captured.err
    report = json.loads(sync_report_path.read_text(encoding="utf-8"))["post_apply"]
    assert report["failed"] == 1
    assert report["updated"] == 1


@pytest.mark.parametrize("platform", ["linux", "win32"])
@pytest.mark.parametrize("existing", [False, True])
def test_normal_main_applies_host_settings_and_preserves_user_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    pytestconfig: pytest.Config,
    capsys: pytest.CaptureFixture[str],
    platform: str,
    existing: bool,
) -> None:
    """通常入口のClaude・Gemini工程は未作成と既存設定を管理値へ収束させ、再適用でも値を保つ。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    # Gitの照会は実OSのまま行い、Claude更新処理だけへ対象OS値を与える。
    monkeypatch.setattr(common, "find_dotfiles_root", lambda: pytestconfig.rootpath)
    monkeypatch.setattr(post_apply.update_claude_settings, "sys", type("_Platform", (), {"platform": platform}))
    claude_path = home / ".claude/settings.json"
    gemini_path = home / ".gemini/settings.json"
    monkeypatch.setattr(post_apply.update_claude_settings, "_SETTINGS_PATH", claude_path)
    monkeypatch.setattr(post_apply.update_claude_settings, "_CONFIG_PATH", home / ".claude.json")
    original_claude = {
        "outputStyle": "Explanatory",
        "includeGitInstructions": True,
        "model": "user-model",
        "mcpServers": {"custom": {"command": "custom-tool"}},
    }
    original_gemini = {
        "general": {"topicUpdateNarration": True, "userOption": "keep"},
        "experimental": {"topicUpdateNarration": True},
        "model": {"name": "user-model"},
        "security": {"auth": {"selectedType": "user-auth"}},
        "mcpServers": {"custom": {"command": "custom-tool"}},
    }
    if existing:
        for path, data in ((claude_path, original_claude), (gemini_path, original_gemini)):
            path.parent.mkdir()
            path.write_text("// ユーザー設定\n" + json.dumps(data), encoding="utf-8")
    allowed = {step.name: step.run for step in post_apply._DEFAULT_STEPS if step.name in {"Claude 設定", "Gemini 設定"}}
    assert set(allowed) == {"Claude 設定", "Gemini 設定"}
    _isolate_default_steps(monkeypatch, allowed)
    previous: dict[str, object] | None = None
    for application in range(2):
        with pytest.raises(SystemExit) as exc_info:
            post_apply.main(["--allow-non-canonical-root"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        (tmp_path / f"host-{application}.stdout.txt").write_text(captured.out, encoding="utf-8")
        (tmp_path / f"host-{application}.stderr.txt").write_text(captured.err, encoding="utf-8")
        claude = pytilpack.jsonc.loads(claude_path.read_text(encoding="utf-8"))
        gemini = pytilpack.jsonc.loads(gemini_path.read_text(encoding="utf-8"))
        assert claude["outputStyle"] == "default"
        assert claude["includeGitInstructions"] is False
        assert gemini["general"]["topicUpdateNarration"] is False
        if existing:
            for key in ("model", "mcpServers"):
                assert claude[key] == original_claude[key]
            for key in ("experimental", "model", "security", "mcpServers"):
                assert gemini[key] == original_gemini[key]
            assert gemini["general"]["userOption"] == "keep"
        current = {"claude": claude, "gemini": gemini}
        if previous is not None:
            assert current == previous
        previous = current
