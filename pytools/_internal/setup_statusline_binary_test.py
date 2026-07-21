"""pytools._internal.setup_statusline_binary のテスト。"""

import httpx
import pytest

from pytools._internal import setup_statusline_binary as mod


def _client(handler) -> httpx.Client:
    """MockTransportで擬似応答を返すHTTPクライアントを生成する。"""
    return httpx.Client(transport=httpx.MockTransport(handler))


class TestRun:
    """`run()`のダウンロード・冪等スキップ・失敗時フォールバックを検証する。"""

    def test_fresh_install_writes_binary_and_etag(self, tmp_path, monkeypatch: pytest.MonkeyPatch):
        install_dir = tmp_path / "bin"
        monkeypatch.setattr(mod, "_INSTALL_DIR", install_dir)
        monkeypatch.setattr(mod, "_INSTALL_PATH", install_dir / "claude-statusline")
        monkeypatch.setattr(mod, "_ETAG_PATH", install_dir / ".claude-statusline.etag")

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"BINARY", headers={"etag": '"abc123"'})

        assert mod.run(client=_client(handler)) is True
        assert (install_dir / "claude-statusline").read_bytes() == b"BINARY"
        assert (install_dir / ".claude-statusline.etag").read_text(encoding="utf-8") == '"abc123"'

    def test_matching_etag_returns_304_and_skips_write(self, tmp_path, monkeypatch: pytest.MonkeyPatch):
        install_dir = tmp_path / "bin"
        install_dir.mkdir()
        binary_path = install_dir / "claude-statusline"
        binary_path.write_bytes(b"OLD")
        etag_path = install_dir / ".claude-statusline.etag"
        etag_path.write_text('"abc123"', encoding="utf-8")
        monkeypatch.setattr(mod, "_INSTALL_DIR", install_dir)
        monkeypatch.setattr(mod, "_INSTALL_PATH", binary_path)
        monkeypatch.setattr(mod, "_ETAG_PATH", etag_path)

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers.get("if-none-match") == '"abc123"'
            return httpx.Response(304)

        assert mod.run(client=_client(handler)) is False
        assert binary_path.read_bytes() == b"OLD"

    def test_network_failure_returns_false_without_raising(self, tmp_path, monkeypatch: pytest.MonkeyPatch):
        install_dir = tmp_path / "bin"
        monkeypatch.setattr(mod, "_INSTALL_DIR", install_dir)
        monkeypatch.setattr(mod, "_INSTALL_PATH", install_dir / "claude-statusline")
        monkeypatch.setattr(mod, "_ETAG_PATH", install_dir / ".claude-statusline.etag")

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom", request=request)

        assert mod.run(client=_client(handler)) is False
