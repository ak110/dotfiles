"""agent_toolkit/_agents_server/ のテストが共有するfixture。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import asyncio
from typing import Any

import pytest

from agent_toolkit._agents_server import (
    launch_requests,
    state,
)
from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._common import wait_schedule as _wait_schedule


async def _skipped_plugin_preflight(_cwd: str) -> None:
    """事前確認を省く。イベントループへ制御を返さず、従来の起動と再開の順序を保つ。"""


async def _forbidden_backend_process(*args: Any, **_kwargs: Any) -> Any:
    """実backendの子プロセス起動を拒否し、差し替えていない呼び出しを実行環境によらず失敗させる。"""
    raise AssertionError(f"テストが実backendの子プロセスを起動しようとしました: {args}")


@pytest.fixture
def _skip_plugin_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    """起動と再開の事前確認を通常は省き、実コマンドの所要時間とスレッド切替を各テストの時間制約へ持ち込まない。

    事前確認そのものを確かめるテストは`_use_real_plugin_preflight`で実装へ戻す。
    """
    monkeypatch.setattr(launch_requests, "check_plugin_commands", _skipped_plugin_preflight)


@pytest.fixture
def _forbid_backend_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """Codex・Antigravityの実backendが子プロセスを起動する処理を通常は拒否する。

    backendを差し替えたengineが候補のengineと一致しないと、実backendが`codex app-server`などを起動する。
    CLIを導入した開発機ではそれが成功して欠陥が隠れ、導入していないCIだけが失敗するため、
    起動そのものを失敗させて両環境の結果をそろえる。起動引数を確かめるテストはテスト内で改めて差し替える。
    """
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _forbidden_backend_process)


@pytest.fixture
def _restore_session_listeners() -> Any:
    """テストが登録した共有リスナーを、テスト終了時に元の集合へ戻す。

    `SessionState.touch`のリスナー集合はプロセス全体で共有される。
    有効化したままの`StatusFileWriter`が残ると、後続の別モジュールのテストが`touch`を呼んだ時点で
    実行中のイベントループを要求して失敗するため、テストごとに登録を元へ戻す。
    """
    touch_listeners = set(state._TOUCH_LISTENERS)
    terminal_listeners = set(state._TERMINAL_LISTENERS)
    lifecycle_listeners = set(state._LIFECYCLE_LISTENERS)
    yield
    state._TOUCH_LISTENERS.clear()
    state._TOUCH_LISTENERS.update(touch_listeners)
    state._TERMINAL_LISTENERS.clear()
    state._TERMINAL_LISTENERS.update(terminal_listeners)
    state._LIFECYCLE_LISTENERS.clear()
    state._LIFECYCLE_LISTENERS.update(lifecycle_listeners)


@pytest.fixture
def _short_start_availability_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """起動直後の終端確認を短縮し、実行中のまま返るsessionでテストを待たせない。"""
    monkeypatch.setattr(server_manager, "START_AVAILABILITY_TIMEOUT", 0.05)


@pytest.fixture
def _immediate_wait_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """待機上限の導出を省き、未終端sessionの待機でテストを待たせない。

    `wait`は待機上限を入力として受け取らないため、上限の導出だけをテスト用の値へ差し替える。
    """
    monkeypatch.setattr(_wait_schedule, "get_wait_timeout", lambda request_bucket: 0.0)


@pytest.fixture
def _owner_session_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """所有セッションの解決に使う環境変数を未設定の状態から始める。"""
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)


@pytest.fixture
def agents_server_isolation(
    _skip_plugin_preflight: None,
    _forbid_backend_process: None,
    _restore_session_listeners: None,
    _short_start_availability_timeout: None,
    _immediate_wait_timeout: None,
) -> None:
    """agents_serverのmanagerとMCPツールのテストを、事前確認・子プロセス・共有リスナー・待機時間から切り離す。"""
