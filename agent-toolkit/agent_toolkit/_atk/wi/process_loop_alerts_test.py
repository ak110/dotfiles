import argparse
import pathlib
import time
from typing import Any

import pytest

from agent_toolkit._atk import review_audit as _review_audit_module
from agent_toolkit._atk.wi import alerts as _wi_alerts
from agent_toolkit._atk.wi import process_loop_alerts as _pl_alerts
from agent_toolkit._common import next_action as _next_action
from agent_toolkit._testing.process_loop_support import isolate_process_loop_commands


@pytest.fixture(autouse=True)
def _isolate_process_loop_commands(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """外部コマンド・Claude設定・初期値のTTLをユーザー環境から分離する。"""
    isolate_process_loop_commands(monkeypatch, tmp_path)


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
