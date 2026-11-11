"""agents_serverの分割試験で共有する明示fixture。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界、配布物cacheも直接検証する。
# pylint: disable=protected-access
import json
import pathlib

import pytest

from agent_toolkit._agents_server import logging_config, shared_layout
from agent_toolkit._agents_server import plugin_root as plugin_roots
from agent_toolkit._common import state_paths
from agent_toolkit._testing import agents_wait_support
from agent_toolkit._testing.codex_provider_support import set_connections
from agent_toolkit._testing.managed_temp_support import setattr_in_managed_temp_modules


@pytest.fixture
def session_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> pathlib.Path:
    """1件の実行中sessionを持つ共有状態を準備する。"""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "root-session")
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    directory = shared_layout.status_directory("root-session", tmp_path)
    directory.mkdir(parents=True)
    (directory / "root.json").write_text(
        json.dumps(
            {
                "version": 1,
                "sessions": [
                    {
                        "session_id": "session-1",
                        "status": "running",
                        "cwd": "/worktree",
                        "prompt": "調査せよ",
                        "label": "調査レーン",
                        "model_type": "execute",
                        "launch_kind": "delegate",
                        "engine": "codex",
                        "model": "model",
                        "effort": "medium",
                        "progress": "進捗",
                        "last_action": "Bash: git status",
                        "created_at": "2026-09-12T00:00:00+00:00",
                        "future_internal": "内部診断",
                        "started_at": "2026-09-13T00:00:00+00:00",
                        "updated_at": "2026-09-13T00:01:00+00:00",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return directory


@pytest.fixture(name="diagnostic_log")
def _diagnostic_log(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """診断ログ設定を隔離した状態ディレクトリへ向ける。handlerの回収は`pytest_plugin`の自動fixtureが行う。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    return logging_config.configure_logging()


@pytest.fixture(name="codex_stable_plugin_roots")
def _isolate_stable_plugin_roots(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """複製先の記録とmanaged-tempの作成先をテストごとに分離する。"""
    monkeypatch.setattr(plugin_roots, "_stable_plugin_roots", {})
    setattr_in_managed_temp_modules(monkeypatch, "_state_root_path", lambda: tmp_path / "managed-temp-state")
    monkeypatch.setenv("TMPDIR", str(tmp_path / "managed-temp"))
    (tmp_path / "managed-temp").mkdir()


@pytest.fixture(name="provider_environment")
def _configure_provider_environment(monkeypatch):
    """資格情報は架空のAPI値だけとし、開発機の設定を使わない。"""
    set_connections(monkeypatch, "subscription,credits,payg")
    monkeypatch.setenv("TEST_PROVIDER_KEY", "isolated-fake-api-key")
    monkeypatch.delenv("UNSET_PROVIDER_KEY", raising=False)


_wait_environment = agents_wait_support._wait_environment
