# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""post_applyのrunの試験。"""

# pylint: disable=protected-access

import logging
import logging.handlers
import re
import subprocess
import threading
import types
from pathlib import Path

import pytest

from pytools import post_apply
from pytools._internal import claude_common, common, post_apply_outcome
from pytools._internal.post_apply_test_helpers import _isolate_sync_report as _isolate_sync_report
from pytools._internal.post_apply_test_helpers import _isolate_update_log as _isolate_update_log
from pytools._internal.post_apply_test_helpers import _make_broken_step, _make_outcome_step, _make_step


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

    def test_foreground_completion_reports_step_duration(
        self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """前景ステップの完了行へステップ本体の所要時間を表示する。"""

        clock = [0.0]
        monkeypatch.setattr(post_apply, "time", types.SimpleNamespace(monotonic=lambda: clock[0]))

        def foreground() -> post_apply_outcome.PostApplyOutcome:
            clock[0] += 0.3
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

    def test_reclaim_failure_blocks_steps_not_yet_started(self) -> None:
        """先行工程が子プロセスを回収できなければ、後続工程を開始せず失敗として数える。"""
        calls: list[str] = []

        def unreclaimed() -> post_apply_outcome.PostApplyOutcome:
            calls.append("before")
            raise common.ProcessReclaimError(["1234: codex app-server"], [])

        results, _ = post_apply.run(
            [
                post_apply._StepSpec("先行", unreclaimed),  # noqa: SLF001
                post_apply._StepSpec("後続", _make_step("after", calls, changed=True), after=("先行",)),  # noqa: SLF001
            ]
        )

        assert calls == ["before"]
        assert [(result.name, result.ok) for result in results] == [("先行", False), ("後続", False)]
        assert results[0].reclaim_failed
        assert "回収できない" in (results[1].reason or "")

    def test_after_all_preceding_waits_for_every_earlier_step(self) -> None:
        """列挙順で前にある全ステップの完了後に開始する。"""
        finished: list[str] = []
        observed: list[list[str]] = []
        fast_done = threading.Event()

        def slow() -> post_apply_outcome.PostApplyOutcome:
            assert fast_done.wait(timeout=5)
            finished.append("slow")
            return post_apply_outcome.PostApplyOutcome()

        def fast() -> post_apply_outcome.PostApplyOutcome:
            finished.append("fast")
            fast_done.set()
            return post_apply_outcome.PostApplyOutcome()

        def last() -> post_apply_outcome.PostApplyOutcome:
            observed.append(list(finished))
            return post_apply_outcome.PostApplyOutcome()

        post_apply.run(
            [
                post_apply._StepSpec("slow", slow),  # noqa: SLF001
                post_apply._StepSpec("fast", fast),  # noqa: SLF001
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


@pytest.mark.parametrize("failed", [step.name for step in post_apply.DEFAULT_STEPS])
@pytest.mark.parametrize("timed_out", [False, True])
def test_all_declared_step_boundaries_continue(capsys: pytest.CaptureFixture[str], failed: str, timed_out: bool) -> None:
    """実工程境界の未知例外は後続を止めず、公開mainの一覧と終了1に届く。"""
    visited: list[str] = []

    def invoke(name: str) -> post_apply_outcome.PostApplyOutcome:
        visited.append(name)
        if name == failed:
            if timed_out:
                raise subprocess.TimeoutExpired(name, 1)
            raise RuntimeError("injected unknown failure")
        return post_apply_outcome.PostApplyOutcome()

    steps = [(step.name, lambda name=step.name: invoke(name)) for step in post_apply.DEFAULT_STEPS]
    with pytest.raises(SystemExit) as result:
        post_apply.main(runner=lambda: post_apply.run(steps))
    assert result.value.code == 1
    assert sorted(visited) == sorted(step.name for step in post_apply.DEFAULT_STEPS)
    output = capsys.readouterr()
    assert failed in output.err + output.out
    assert "次の操作:" in output.err
