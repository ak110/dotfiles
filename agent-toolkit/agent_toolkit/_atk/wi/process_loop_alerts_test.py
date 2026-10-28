"""`atk wi process-loop`のアラートの確認のテスト。"""

import argparse
import json
import pathlib
import time
from typing import Any

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import review_audit as _review_audit_module
from agent_toolkit._atk.wi import alerts as _wi_alerts
from agent_toolkit._atk.wi import listing
from agent_toolkit._atk.wi import process_loop as loop
from agent_toolkit._atk.wi import process_loop_alerts as _pl_alerts
from agent_toolkit._atk.wi import process_loop_watch as _pl_watch
from agent_toolkit._atk.wi.layout import iter_entry_paths
from agent_toolkit._atk.wi.process_loop_test import _start_entry
from agent_toolkit._common import next_action as _next_action
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.process_loop_support import isolate_process_loop_commands
from agent_toolkit.atk_test import _setup_notes


@pytest.fixture(autouse=True)
def _isolate_process_loop_commands(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """外部コマンド・Claude設定・初期値のTTLをユーザー環境から分離する。"""
    isolate_process_loop_commands(monkeypatch, tmp_path)


class TestAlertMonitoring:
    """登録全体のループで、候補とアラートによる起動判定を検証する。"""

    @pytest.mark.parametrize(
        ("no_alerts", "pending", "initial", "submitted", "launch"),
        [
            (False, [], 0, 0, False),
            (True, [48], 0, 0, False),
            (False, [48], 0, 0, True),
            (False, [], 1, 0, True),
            (False, [], 0, 1, True),
            (False, [], 1, 1, True),
            (True, [], 1, 0, True),
        ],
    )
    def test_registered_alert_and_candidate_launch(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        no_alerts: bool,
        pending: list[int],
        initial: int,
        submitted: int,
        launch: bool,
    ) -> None:
        """未判定だけでも起動し、CI投入後は再取得した候補を使い、無効化はCI確認を抑止する。"""
        notes = _setup_notes(tmp_path)
        repository = git_repository.init_repository(tmp_path / "repo", origin="https://github.com/example/myrepo.git")
        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_PROCESS_LOOP_REPOSITORIES", json.dumps([str(repository)]))
        if initial:
            _start_entry(notes, "initial.md", kind="awi")
        checks: list[str] = []
        launches: list[tuple[listing.RepositorySnapshot, ...]] = []
        observed: list[str] = []

        def check(*_args: Any, **_kwargs: Any) -> _wi_alerts.AlertCheckResult:
            checks.append("checked")
            if submitted:
                _start_entry(notes, "submitted.md", kind="awi")
            return _wi_alerts.AlertCheckResult(submitted, ())

        def dependabot(repository: str) -> dict[str, Any]:
            observed.append(repository)
            return {"status": "available", "alerts": [{"number": number} for number in pending]}

        def coordinate(
            _args: argparse.Namespace, snapshots: tuple[listing.RepositorySnapshot, ...], **_kwargs: Any
        ) -> dict[str, dict[str, str]]:
            assert launch
            launches.append(snapshots)
            return {snapshots[0].repository.repo_id: {"status": "completed"}}

        def wait(*_args: Any, **_kwargs: Any) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(_wi_alerts, "check_and_submit_alerts", check)
        monkeypatch.setattr(_review_audit_module, "dependabot_pending", dependabot)
        monkeypatch.setattr(loop, "run_coordinator", coordinate)
        monkeypatch.setattr(_pl_watch, "wait_for_changes", wait)
        with pytest.raises(SystemExit) as ended:
            atk.main(["wi", "process-loop", "--no-update", *(["--no-alerts"] if no_alerts else [])], home=tmp_path)
        assert ended.value.code == 0
        assert bool(launches) is launch
        assert checks == ([] if no_alerts else ["checked"])
        if launch:
            assert len(launches[0][0].candidates) == initial + submitted
        if not no_alerts:
            assert observed == ["example/myrepo"]
        if not initial and not submitted:
            assert not list(iter_entry_paths(notes))

    def test_registered_alert_interval(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """同じ登録先の確認時刻を反復間で保持し、間隔内の二重照会を抑止する。"""
        _setup_notes(tmp_path)
        repository = git_repository.init_repository(tmp_path / "repo", origin="https://github.com/example/myrepo.git")
        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_PROCESS_LOOP_REPOSITORIES", json.dumps([str(repository)]))
        clock = [1000.0]
        monkeypatch.setattr(time, "monotonic", lambda: clock[0])
        checks: list[str] = []
        waits: list[int] = []

        def check(*_args: Any, **_kwargs: Any) -> _wi_alerts.AlertCheckResult:
            checks.append("checked")
            return _wi_alerts.AlertCheckResult(0, ())

        def wait(*_args: Any, **_kwargs: Any) -> None:
            waits.append(1)
            clock[0] = 1010.0
            if len(waits) == 2:
                raise KeyboardInterrupt

        monkeypatch.setattr(_wi_alerts, "check_and_submit_alerts", check)
        monkeypatch.setattr(_review_audit_module, "dependabot_pending", lambda *_args: {"status": "available", "alerts": []})
        monkeypatch.setattr(_pl_watch, "wait_for_changes", wait)
        with pytest.raises(SystemExit) as ended:
            atk.main(["wi", "process-loop", "--no-update", "--alert-interval=3600"], home=tmp_path)
        assert ended.value.code == 0 and len(waits) == 2
        assert checks == ["checked"]


@pytest.mark.parametrize("case", ["observed", "failed", "unauthorized", "interval", "no-alerts", "not-counted"])
def test_dependabot_observation_distinguishes_unobserved(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    """取得に成功した反復だけが番号集合を返し、失敗・確認間隔内・無効化・数えない呼び出しは未観測を返す。"""
    monkeypatch.setattr(_wi_alerts, "check_and_submit_alerts", lambda *_a, **_k: _wi_alerts.AlertCheckResult(0, ()))

    def pending(_repository: str) -> dict[str, Any]:
        if case == "failed":
            raise _next_action.ActionableError("APIの応答が不正", next_action="再試行する")
        if case == "unauthorized":
            return {"status": "unauthorized", "alerts": [], "message": "権限不足"}
        return {"status": "available", "alerts": [{"number": 3}, {"number": 9}]}

    monkeypatch.setattr(_review_audit_module, "dependabot_pending", pending)
    args = argparse.Namespace(no_alerts=case == "no-alerts", alert_interval=3600, alert_forge="github")
    last = time.monotonic() if case == "interval" else None
    _checked, _submitted, observed = _pl_alerts.check_process_loop_alerts(
        args, tmp_path, "github.com/owner/repo", tmp_path, last, count_dependabot=case != "not-counted"
    )
    assert observed == (frozenset({3, 9}) if case == "observed" else None)
