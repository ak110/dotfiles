"""CLI導入のHTTPクライアントは内部生成時だけ所有し、中断でも閉じる。"""

import contextlib
import types
from pathlib import Path

import httpx
import pytest

from pytools._internal import setup_cli_common, setup_codex_cli, setup_mise, setup_statusline_binary


@pytest.mark.parametrize("installer", ["common", "codex", "mise", "statusline"])
@pytest.mark.parametrize("borrowed", [False, True])
def test_http_client_ownership_survives_interruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, installer: str, borrowed: bool
) -> None:
    """同じHTTP中断でも、内部生成した接続だけを閉じ、注入値の寿命を呼出元へ保つ。"""

    def interrupt(_request: httpx.Request) -> httpx.Response:
        raise KeyboardInterrupt

    with contextlib.closing(httpx.Client(transport=httpx.MockTransport(interrupt))) as client:
        monkeypatch.setattr(httpx, "Client", lambda **_kwargs: client)
        monkeypatch.setattr(setup_cli_common, "sys", types.SimpleNamespace(platform="linux"))
        monkeypatch.setattr(setup_codex_cli, "sys", types.SimpleNamespace(platform="linux"))
        monkeypatch.setattr(setup_mise, "_is_windows", lambda: False)
        monkeypatch.setattr(setup_statusline_binary, "_INSTALL_DIR", tmp_path)
        monkeypatch.setattr(setup_statusline_binary, "_INSTALL_PATH", tmp_path / "statusline")
        monkeypatch.setattr(setup_statusline_binary, "_ETAG_PATH", tmp_path / "etag")
        supplied = client if borrowed else None
        with pytest.raises(KeyboardInterrupt):
            if installer == "common":
                setup_cli_common.run_official_installer(
                    supplied, posix_url="https://example.test/install.sh", windows_url="unused", tag="test", timeout=1
                )
            elif installer == "codex":
                setup_codex_cli._run_installer(supplied)  # noqa: SLF001  # pylint: disable=protected-access
            elif installer == "mise":
                setup_mise._ensure_mise_installed(supplied)  # noqa: SLF001  # pylint: disable=protected-access
            else:
                setup_statusline_binary._download_release(supplied)  # noqa: SLF001  # pylint: disable=protected-access
        assert client.is_closed is not borrowed
