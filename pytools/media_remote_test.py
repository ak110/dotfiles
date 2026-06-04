"""pytools.media_remoteのテスト。"""

# pylint: disable=protected-access

import ctypes
import pathlib
from collections.abc import Callable
from typing import Any

import pytest

from pytools.media_remote import _app, _assets, _cli, _keys, _token

# token_urlsafe(32)が生成する形式（43字、URL-safe base64）に合致する固定値。
VALID_TOKEN = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJ-_0123A"


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """`LOCALAPPDATA`等の環境変数を`tmp_path`配下へ隔離する。

    `default_token_path()`/`default_pid_path()`は`LOCALAPPDATA`を参照するため、
    テスト実行環境の値が漏れ込まないよう全テストで一括隔離する。
    """
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))
    monkeypatch.setenv("HOME", str(tmp_path))


def _make_client(
    send_key: Callable[[str], None] | None = None,
) -> tuple[Any, list[str]]:
    captured: list[str] = []

    def _stub(name: str) -> None:
        captured.append(name)

    app = _app.create_app(VALID_TOKEN, send_key=send_key if send_key is not None else _stub)
    return app.test_client(), captured


@pytest.mark.asyncio
async def test_index_without_token_returns_401():
    client, _ = _make_client()
    resp = await client.get("/")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_index_with_query_token_sets_cookie():
    client, _ = _make_client()
    resp = await client.get(f"/?t={VALID_TOKEN}")
    assert resp.status_code == 200
    cookies = resp.headers.get_all("Set-Cookie")
    assert any(_app.COOKIE_NAME in c and VALID_TOKEN in c for c in cookies)


@pytest.mark.asyncio
async def test_index_with_cookie_token_allows_access():
    client, _ = _make_client()
    client.set_cookie("localhost", _app.COOKIE_NAME, VALID_TOKEN)
    resp = await client.get("/")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_index_with_invalid_token_returns_401():
    client, _ = _make_client()
    resp = await client.get("/?t=invalid")
    assert resp.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("name", list(_keys.VK_CODES.keys()))
async def test_api_key_dispatches_to_send_key(name: str):
    client, captured = _make_client()
    client.set_cookie("localhost", _app.COOKIE_NAME, VALID_TOKEN)
    resp = await client.post(f"/api/key/{name}")
    assert resp.status_code == 204
    assert captured == [name]


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["", "unknown", "playpause"])
async def test_api_key_unknown_returns_404(name: str):
    client, captured = _make_client()
    client.set_cookie("localhost", _app.COOKIE_NAME, VALID_TOKEN)
    resp = await client.post(f"/api/key/{name}")
    # 空名はルーティング自体が404になる。既知名のみ204を返すという挙動の境界を担保する。
    assert resp.status_code == 404
    assert not captured


@pytest.mark.asyncio
async def test_manifest_returns_json():
    client, _ = _make_client()
    client.set_cookie("localhost", _app.COOKIE_NAME, VALID_TOKEN)
    resp = await client.get("/manifest.json")
    assert resp.status_code == 200
    body = await resp.get_json()
    assert body["name"] == "Media Remote"
    assert any(icon["src"].endswith("icon.svg") for icon in body["icons"])


class _FakeUser32:
    def __init__(self, return_count: int = 2) -> None:
        self.calls: list[tuple[int, list[int], int]] = []
        self.return_count = return_count

    def SendInput(self, n_inputs, inputs, cb_size):  # noqa: N802  Windows API名に合わせる
        vks = [inputs[i].ki.wVk for i in range(n_inputs)]
        flags = [inputs[i].ki.dwFlags for i in range(n_inputs)]
        self.calls.append((n_inputs, vks, cb_size))
        # 押下イベントはKEYEVENTF_KEYUP無、解放イベントはKEYEVENTF_KEYUP有を確認する。
        assert flags[0] & _keys.KEYEVENTF_KEYUP == 0
        assert flags[1] & _keys.KEYEVENTF_KEYUP
        return self.return_count


@pytest.mark.parametrize("name,vk", list(_keys.VK_CODES.items()))
def test_send_key_emits_press_and_release(name: str, vk: int):
    fake = _FakeUser32()
    _keys.send_key(name, user32=fake)
    assert len(fake.calls) == 1
    n_inputs, vks, cb_size = fake.calls[0]
    assert n_inputs == 2
    assert vks == [vk, vk]
    assert cb_size == ctypes.sizeof(_keys.INPUT)


def test_send_key_raises_on_non_windows(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(_keys.sys, "platform", "linux")
    with pytest.raises(RuntimeError):
        _keys.send_key("play_pause")


def test_send_key_unknown_name_raises():
    with pytest.raises(KeyError):
        _keys.send_key("nonexistent", user32=_FakeUser32())


def test_send_key_raises_when_sendinput_returns_unexpected_count():
    """`SendInput`が想定外件数を返したとき`OSError`を送出する。"""
    fake = _FakeUser32(return_count=1)
    with pytest.raises(OSError):
        _keys.send_key("play_pause", user32=fake)


def test_load_or_create_token_returns_existing(tmp_path: pathlib.Path):
    token_path = tmp_path / "token.txt"
    token_path.write_text(VALID_TOKEN + "\n", encoding="utf-8")
    assert _token.load_or_create_token(token_path) == VALID_TOKEN


def test_load_or_create_token_replaces_invalid(tmp_path: pathlib.Path):
    token_path = tmp_path / "token.txt"
    token_path.write_text("not-a-valid-token\n", encoding="utf-8")
    result = _token.load_or_create_token(token_path)
    # 旧不正値が破棄され、新規生成値がディスク上に永続化されている。
    assert result != "not-a-valid-token"
    assert token_path.read_text(encoding="utf-8").strip() == result
    # 再呼び出しで同値を返す（永続化された値が有効と判定される）ことを確認する。
    assert _token.load_or_create_token(token_path) == result


def test_load_or_create_token_creates_when_missing(tmp_path: pathlib.Path):
    token_path = tmp_path / "sub" / "token.txt"
    result = _token.load_or_create_token(token_path)
    assert token_path.read_text(encoding="utf-8").strip() == result
    # 再呼び出しで同値を返す（永続化された値が有効と判定される）ことを確認する。
    assert _token.load_or_create_token(token_path) == result


def test_build_access_url_includes_token():
    url = _cli.build_access_url("192.168.1.10", 29123, VALID_TOKEN)
    assert url == f"http://192.168.1.10:29123/?t={VALID_TOKEN}"


def test_render_qr_ansi_returns_nonempty_string():
    out = _cli.render_qr_ansi("http://example/")
    assert isinstance(out, str)
    assert out.strip() != ""


def test_url_subcommand_prints_url_and_qr(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]):
    token_path = tmp_path / "token.txt"
    token_path.write_text(VALID_TOKEN + "\n", encoding="utf-8")
    exit_code = _cli.main(["url", "--host", "10.0.0.1", "--port", "29123", "--token-file", str(token_path)])
    captured = capsys.readouterr()
    assert exit_code == 0
    expected_url = f"http://10.0.0.1:29123/?t={VALID_TOKEN}"
    assert expected_url in captured.out
    # `render_qr_ansi`の出力（ANSIブロック文字）が末尾に含まれることを確認する。
    assert _cli.render_qr_ansi(expected_url).strip() in captured.out


def test_serve_subcommand_rejects_non_windows(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(_cli.sys, "platform", "linux")
    token_path = tmp_path / "token.txt"
    assert _cli.main(["serve", "--token-file", str(token_path)]) == 1


def test_assets_index_html_references_all_keys():
    for name in _keys.VK_CODES:
        assert f'data-key="{name}"' in _assets.INDEX_HTML
