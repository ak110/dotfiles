"""`atk wi process-loop`のアラートの確認のテスト。"""

import pathlib
import subprocess
import time
from typing import Any

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import review_audit as _review_audit_module
from agent_toolkit._atk.wi import alerts as _wi_alerts
from agent_toolkit._atk.wi import process_loop_watch as _pl_watch
from agent_toolkit._atk.wi import readiness as _wi_readiness
from agent_toolkit._testing.process_loop_support import fake_run_with_remote_url, isolate_process_loop_commands
from agent_toolkit.atk_test import _setup_notes


@pytest.fixture(autouse=True)
def _isolate_process_loop_commands(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """外部コマンド・Claude設定・初期値のTTLをユーザー環境から分離する。"""
    isolate_process_loop_commands(monkeypatch, tmp_path)


class TestAlertMonitoring:
    """process-loop常駐ループへのアラート自動検出統合。"""

    def test_alert_check_invoked_when_pending_zero(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """件数0の反復でアラート確認が呼ばれ、投入0件なら待機へ進む。"""
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        monkeypatch.setattr(subprocess, "run", fake_run_with_remote_url(myrepo, [], 0))
        monkeypatch.setattr(_wi_readiness, "count_pending_entries", lambda *_a, **_k: 0)
        calls: list[str] = []

        def fake_check(*_args: object, **_kwargs: object) -> _wi_alerts.AlertCheckResult:
            calls.append("checked")
            return _wi_alerts.AlertCheckResult(0, ())

        monkeypatch.setattr(  # pylint: disable=protected-access
            _wi_alerts,  # pylint: disable=protected-access
            "check_and_submit_alerts",
            fake_check,
        )

        def fake_wait(*_args: object, **_kwargs: object) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(_pl_watch, "wait_for_changes", fake_wait)
        with pytest.raises(SystemExit):
            atk.main(["wi", "process-loop", f"--target-repo={myrepo}", "--no-update"], home=tmp_path)
        assert calls == ["checked"]

    def test_no_alerts_flag_skips_check(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """--no-alerts指定時はアラート確認を呼ばない。"""
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        monkeypatch.setattr(subprocess, "run", fake_run_with_remote_url(myrepo, [], 0))
        monkeypatch.setattr(_wi_readiness, "count_pending_entries", lambda *_a, **_k: 0)

        def fail_check(*_args: object, **_kwargs: object) -> int:
            raise AssertionError("アラート確認を呼ばないはず")

        monkeypatch.setattr(  # pylint: disable=protected-access
            _wi_alerts,  # pylint: disable=protected-access
            "check_and_submit_alerts",
            fail_check,
        )

        def fake_wait(*_args: object, **_kwargs: object) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(_pl_watch, "wait_for_changes", fake_wait)
        with pytest.raises(SystemExit):
            atk.main(
                ["wi", "process-loop", f"--target-repo={myrepo}", "--no-update", "--no-alerts"],
                home=tmp_path,
            )

    def test_alert_submission_triggers_immediate_reiteration(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """投入件数が正なら待機せず次反復のclaude起動へ進む。"""
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        counts = iter([0, 1])
        claude_calls: list[dict[str, Any]] = []
        monkeypatch.setattr(subprocess, "run", fake_run_with_remote_url(myrepo, claude_calls, 2))
        monkeypatch.setattr(_wi_readiness, "count_pending_entries", lambda *_a, **_k: next(counts))
        monkeypatch.setattr(  # pylint: disable=protected-access
            _wi_alerts,  # pylint: disable=protected-access
            "check_and_submit_alerts",
            lambda *_a, **_k: _wi_alerts.AlertCheckResult(1, ()),
        )

        def fail_wait(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("待機ループへ入らないはず")

        monkeypatch.setattr(_pl_watch, "wait_for_changes", fail_wait)
        with pytest.raises(SystemExit):
            atk.main(["wi", "process-loop", f"--target-repo={myrepo}", "--no-update"], home=tmp_path)
        assert len(claude_calls) == 1

    def test_alert_interval_suppresses_repeated_checks(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """指定間隔未経過の反復ではアラート確認を呼ばない。"""
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        monkeypatch.setattr(subprocess, "run", fake_run_with_remote_url(myrepo, [], 0))
        monkeypatch.setattr(_wi_readiness, "count_pending_entries", lambda *_a, **_k: 0)
        times = iter([1000.0, 1010.0])
        monkeypatch.setattr(time, "monotonic", lambda: next(times))
        calls: list[str] = []

        def fake_check(*_args: object, **_kwargs: object) -> _wi_alerts.AlertCheckResult:
            calls.append("checked")
            return _wi_alerts.AlertCheckResult(0, ())

        monkeypatch.setattr(  # pylint: disable=protected-access
            _wi_alerts,  # pylint: disable=protected-access
            "check_and_submit_alerts",
            fake_check,
        )
        wait_calls: list[int] = []

        def fake_wait(*_args: object, **_kwargs: object) -> None:
            wait_calls.append(1)
            if len(wait_calls) >= 2:
                raise KeyboardInterrupt

        monkeypatch.setattr(_pl_watch, "wait_for_changes", fake_wait)
        with pytest.raises(SystemExit):
            atk.main(
                [
                    "wi",
                    "process-loop",
                    f"--target-repo={myrepo}",
                    "--no-update",
                    "--alert-interval=3600",
                ],
                home=tmp_path,
            )
        assert calls == ["checked"]

    def test_ci_failure_during_pending_work_is_submitted_before_next_session(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """キューに項目がある間も、セッションの開始前にCI失敗を確認して投入し、数え直した件数で起動する。

        確認が件数0の間だけだと、セッションが続く期間に起きた失敗はキューが空になるまでAWIへ入らない。
        この確認ではDependabotアラートを数えない（起動するセッションの監査が扱う）。
        """
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        claude_calls: list[dict[str, Any]] = []
        monkeypatch.setattr(subprocess, "run", fake_run_with_remote_url(myrepo, claude_calls, 2))
        counts = iter([1, 2])
        monkeypatch.setattr(_wi_readiness, "count_pending_entries", lambda *_a, **_k: next(counts))
        calls: list[str] = []

        def fake_check(*_args: object, **_kwargs: object) -> _wi_alerts.AlertCheckResult:
            calls.append("checked")
            return _wi_alerts.AlertCheckResult(1, ())

        monkeypatch.setattr(  # pylint: disable=protected-access
            _wi_alerts,  # pylint: disable=protected-access
            "check_and_submit_alerts",
            fake_check,
        )

        def fail_dependabot(_repository: str) -> dict[str, Any]:
            raise AssertionError("セッション開始前の確認ではDependabotアラートを数えないはず")

        monkeypatch.setattr(  # pylint: disable=protected-access
            _review_audit_module,  # pylint: disable=protected-access
            "dependabot_pending",
            fail_dependabot,
        )
        with pytest.raises(SystemExit):
            atk.main(["wi", "process-loop", f"--target-repo={myrepo}", "--no-update"], home=tmp_path)
        assert calls == ["checked"]
        assert len(claude_calls) == 1
        log = (tmp_path / "state" / "agent-toolkit" / "process-wi.log").read_text(encoding="utf-8")
        assert "event=alert_check submitted=1 dependabot_pending=0 session_started=False" in log
        assert "event=loop_iter_start count=2" in log

    @pytest.mark.parametrize(
        "extra_argv",
        [pytest.param(["--no-alerts"], id="no-alerts"), pytest.param(["--alert-interval=3600"], id="within-interval")],
    )
    def test_check_before_session_follows_interval_and_no_alerts(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, extra_argv: list[str]
    ) -> None:
        """`--no-alerts`では確認せず、直前の確認から`--alert-interval`未満ではセッション開始前も確認しない。"""
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        claude_calls: list[dict[str, Any]] = []
        monkeypatch.setattr(subprocess, "run", fake_run_with_remote_url(myrepo, claude_calls, 2))
        counts = iter([0, 1])
        monkeypatch.setattr(_wi_readiness, "count_pending_entries", lambda *_a, **_k: next(counts))
        monkeypatch.setattr(time, "monotonic", lambda: 1000.0)
        calls: list[str] = []

        def fake_check(*_args: object, **_kwargs: object) -> _wi_alerts.AlertCheckResult:
            calls.append("checked")
            return _wi_alerts.AlertCheckResult(0, ())

        monkeypatch.setattr(  # pylint: disable=protected-access
            _wi_alerts,  # pylint: disable=protected-access
            "check_and_submit_alerts",
            fake_check,
        )
        monkeypatch.setattr(  # pylint: disable=protected-access
            _review_audit_module,  # pylint: disable=protected-access
            "dependabot_pending",
            lambda _repository: {"status": "available", "alerts": []},
        )
        monkeypatch.setattr(_pl_watch, "wait_for_changes", lambda *_a, **_k: True)
        with pytest.raises(SystemExit):
            atk.main(["wi", "process-loop", f"--target-repo={myrepo}", "--no-update", *extra_argv], home=tmp_path)
        assert len(claude_calls) == 1
        assert calls == ([] if "--no-alerts" in extra_argv else ["checked"])

    def test_unjudged_dependabot_alerts_start_session_without_submitting_awi(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """待機中に未判定のDependabotアラートがあれば、AWIを投入せずにprocess-wiを1回実行させ、起動の有無を記録する。"""
        notes = _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        claude_calls: list[dict[str, Any]] = []
        monkeypatch.setattr(subprocess, "run", fake_run_with_remote_url(myrepo, claude_calls, 2))
        monkeypatch.setattr(_wi_readiness, "count_pending_entries", lambda *_a, **_k: 0)
        repositories: list[str] = []

        def fake_pending(repository: str) -> dict[str, Any]:
            repositories.append(repository)
            return {"status": "available", "alerts": [{"number": 48, "category": "inaccurate"}]}

        monkeypatch.setattr(  # pylint: disable=protected-access
            _review_audit_module,  # pylint: disable=protected-access
            "dependabot_pending",
            fake_pending,
        )

        def fail_wait(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("待機ループへ入らないはず")

        monkeypatch.setattr(_pl_watch, "wait_for_changes", fail_wait)
        with pytest.raises(SystemExit):
            atk.main(["wi", "process-loop", f"--target-repo={myrepo}", "--no-update"], home=tmp_path)
        assert repositories == ["example/myrepo"]
        assert len(claude_calls) == 1
        assert not list((notes / "inbox").iterdir())
        log = (tmp_path / "state" / "agent-toolkit" / "process-wi.log").read_text(encoding="utf-8")
        assert "event=alert_check submitted=0 dependabot_pending=1 session_started=True" in log

    def test_judged_dependabot_alerts_do_not_start_session(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """全アラートが判定済みで未判定件数が0なら、process-wiを実行させず待機へ進む。"""
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        claude_calls: list[dict[str, Any]] = []
        monkeypatch.setattr(subprocess, "run", fake_run_with_remote_url(myrepo, claude_calls, 2))
        monkeypatch.setattr(_wi_readiness, "count_pending_entries", lambda *_a, **_k: 0)
        monkeypatch.setattr(  # pylint: disable=protected-access
            _wi_alerts,  # pylint: disable=protected-access
            "check_and_submit_alerts",
            lambda *_a, **_k: _wi_alerts.AlertCheckResult(0, ()),
        )
        monkeypatch.setattr(  # pylint: disable=protected-access
            _review_audit_module,  # pylint: disable=protected-access
            "dependabot_pending",
            lambda _repository: {"status": "available", "alerts": []},
        )

        def fake_wait(*_args: object, **_kwargs: object) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(_pl_watch, "wait_for_changes", fake_wait)
        with pytest.raises(SystemExit):
            atk.main(["wi", "process-loop", f"--target-repo={myrepo}", "--no-update"], home=tmp_path)
        assert not claude_calls
