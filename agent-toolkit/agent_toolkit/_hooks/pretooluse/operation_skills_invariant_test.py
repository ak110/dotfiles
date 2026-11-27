"""共有の委譲先の起動区分が各backendの子環境へ渡ることを確かめる。  pyfltr: allow-comment"""

from __future__ import annotations

import pathlib
from typing import Any

import pytest

from agent_toolkit._agents_server import claude as claude_backend
from agent_toolkit._agents_server import claude_settings
from agent_toolkit._agents_server import codex as codex_backend
from agent_toolkit._common import delegated_session

_LAUNCH_KIND = delegated_session.LAUNCH_KIND_ENV


@pytest.mark.parametrize("launch_kind", ["delegate", "explore", "shell", "write"])
def test_backends_pass_launch_kind_to_delegate_environment(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, launch_kind: claude_backend.LaunchKind
) -> None:
    """Claude backendは委譲先の環境へ起動区分を設定し、継承した値を上書きする。"""
    monkeypatch.setenv(_LAUNCH_KIND, "explore" if launch_kind == "delegate" else "delegate")
    options = claude_backend._build_options(str(tmp_path), None, None, launch_kind=launch_kind)  # pylint: disable=protected-access
    claude_settings.cleanup_settings(options)
    assert options.env[_LAUNCH_KIND] == launch_kind
    assert delegated_session.is_lightweight_delegated(options.env) is (launch_kind != "delegate")


@pytest.mark.asyncio
@pytest.mark.parametrize("launch_kind", ["delegate", "explore", None])
async def test_codex_app_server_environment_carries_launch_kind(
    monkeypatch: pytest.MonkeyPatch, launch_kind: codex_backend.LaunchKind | None
) -> None:
    """Codex backendはsessionのApp Serverの子環境へ起動区分を設定し、照会用の接続では継承した値を除く。

    所有接続の取得（開始・再開）は起動区分を渡し、モデル一覧などの照会用の接続は起動区分を持たない。
    """
    captured: list[dict[str, str]] = []

    async def fake_exec(*_args: Any, env: dict[str, str], **_kwargs: Any) -> Any:
        captured.append(env)
        raise OSError("起動しない")

    async def close_unstarted(*_args: Any, **_kwargs: Any) -> list[str]:
        return []

    monkeypatch.setenv(_LAUNCH_KIND, "shell")
    monkeypatch.setattr(codex_backend.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(codex_backend.JsonRpcProcess, "close", close_unstarted)
    async with codex_backend.AppServerManager() as manager:
        with pytest.raises(codex_backend.AppServerError):
            if launch_kind is None:
                await manager._ensure_client()  # pylint: disable=protected-access
            else:
                await manager._acquire_owned_client("session", launch_kind)  # pylint: disable=protected-access
        assert captured[0].get(_LAUNCH_KIND) == launch_kind
