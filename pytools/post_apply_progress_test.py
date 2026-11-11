# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""post_applyのprogressの試験。"""

# pylint: disable=protected-access

import asyncio
import logging
import logging.handlers
import os
import subprocess
import threading
from pathlib import Path

import pytest

from pytools import post_apply
from pytools._internal import post_apply_outcome
from pytools._internal.post_apply_test_helpers import _gated_steps
from pytools._internal.post_apply_test_helpers import _isolate_sync_report as _isolate_sync_report
from pytools._internal.post_apply_test_helpers import _isolate_update_log as _isolate_update_log


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


@pytest.mark.parametrize("fail_second", [False, True])
def test_parallel_warmup_keeps_root_order_and_worker_log_capture(monkeypatch, tmp_path, capsys, fail_second):
    """非同期のroot別診断を親workerで捕捉し、工程とrootの列挙順に1回だけ表示する。"""
    warmup = post_apply.warmup_agents_server
    commands = [["python", str(tmp_path / name)] for name in ("first", "second")]
    monkeypatch.setattr(warmup.plugin_warmup, "prepare_targets", lambda *_args, **_kwargs: commands)

    async def dependency(command):
        return warmup.plugin_warmup.CommandOutcome(command, subprocess.CompletedProcess(command, 0, "", ""), 0)

    async def check_targets():
        second_done = asyncio.Event()

        async def startup(command):
            name = Path(command[-1]).name
            if name == "first":
                await asyncio.wait_for(second_done.wait(), 5)
            else:
                second_done.set()
            return warmup._StartupOutcome(not (name == "second" and fail_second), f"{name}-root-detail")

        monkeypatch.setattr(warmup, "_check_startup", startup)
        return await original_check_targets(commands)

    original_check_targets = warmup._check_targets
    monkeypatch.setattr(warmup.plugin_warmup, "check_command", dependency)

    async def checks(_commands):
        return await check_targets()

    monkeypatch.setattr(warmup, "_check_targets", checks)

    def independent():
        logging.getLogger("independent-test").info("independent-detail")
        return post_apply_outcome.PostApplyOutcome()

    with pytest.raises(SystemExit) as ended:
        post_apply.main(runner=lambda: post_apply.run([("warmup", warmup.run), ("independent", independent)]))
    assert ended.value.code == int(fail_second)
    captured = capsys.readouterr()
    screen = captured.out + captured.err
    log = (tmp_path / "update-dotfiles.log").read_text(encoding="utf-8")
    assert log.index("first-root-detail") < log.index("second-root-detail") < log.index("independent-detail")
    assert screen.count("first-root-detail") == screen.count("second-root-detail") == 1


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


def test_run_records_unfinished_steps_periodically_without_terminal(monkeypatch, tmp_path, capsys):
    """端末でない出力でも、終わらない工程の名前を一定間隔で記録し、止まった工程を判別できる。"""
    monkeypatch.setattr(post_apply, "is_human_terminal", lambda: False)
    monkeypatch.setattr(post_apply, "_RUNNING_NOTICE_SECONDS", 0.05)
    release = threading.Event()

    def stuck():
        assert release.wait(30)
        return post_apply_outcome.PostApplyOutcome(changed=False)

    def quick():
        return post_apply_outcome.PostApplyOutcome(changed=False)

    real_wait = post_apply.wait
    waits = []

    def wait_until_noticed(*args, **kwargs):
        waits.append(kwargs.get("timeout"))
        # 未完了の工程の記録を観測した後（観測できない場合も待機の4回目以降）に、止まった工程を終わらせる。
        if capsys.readouterr().out.count("未完了の工程: 止まる工程") or len(waits) > 3:
            release.set()
        return real_wait(*args, **kwargs)

    monkeypatch.setattr(post_apply, "wait", wait_until_noticed)
    steps = [post_apply._StepSpec("止まる工程", stuck), post_apply._StepSpec("すぐ終わる工程", quick)]
    try:
        with pytest.raises(SystemExit) as ended:
            post_apply.main(runner=lambda: post_apply.run(steps))
    finally:
        release.set()
    assert ended.value.code == 0
    log = (tmp_path / "update-dotfiles.log").read_text(encoding="utf-8")
    assert "未完了の工程: 止まる工程（" in log
    assert all(timeout is not None for timeout in waits)
