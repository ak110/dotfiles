"""pytools.media_remoteのテスト。"""

import asyncio
import contextlib
import ctypes
import pathlib
import queue
import subprocess
import threading
from collections.abc import Callable
from typing import Any

import pytest

from pytools.media_remote import _app, _assets, _cli, _keys, _token, _window_api, _window_move

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


class _WindowBackend:
    """決定論的なイベント配送とWindows境界をserveへ与える。"""

    def __init__(self) -> None:
        self.events: queue.Queue[Any] = queue.Queue()
        self.stopped = threading.Event()
        self.mouse: Any = None
        self.window: Any = None
        self.key: Any = None
        self.click_interval_ms = 500
        self.moves: list[tuple[int, tuple[int, int]]] = []
        self.targets = {100: (10, 20), 200: (11, 21)}
        self.uia_active = False
        self.failure = False

    def physical_coordinates(self) -> Any:
        return contextlib.nullcontext()

    @contextlib.contextmanager
    def uia(self) -> Any:
        self.uia_active = True
        try:
            yield self
        finally:
            self.uia_active = False

    def is_task_selection(self, point: tuple[int, int], root: str) -> bool:
        return root in ("Shell_TrayWnd", "TaskListThumbnailWnd") and point == (10, 10)

    def run_hooks(self, mouse: Any, window: Any, key: Any, ready: Any) -> None:
        self.mouse, self.window = mouse, window
        self.key = key
        ready()
        assert self.stopped.wait(10)

    def stop_hooks(self) -> None:
        self.stopped.set()

    def identity(self, hwnd: int) -> tuple[int, int] | None:
        return self.targets.get(hwnd)

    def move(self, hwnd: int, identity: tuple[int, int], point: tuple[int, int]) -> None:
        if self.failure or self.targets.get(hwnd) != identity:
            raise OSError("対象は移動できない")
        self.moves.append((hwnd, point))

    def click(self, root: str = "Shell_TrayWnd", point: tuple[int, int] = (10, 10), time: int = 50) -> None:
        assert not self.mouse(_window_api.WM_LBUTTONDOWN, point, time, root)
        self.events.join()

    def select(self, hwnd: int = 100, event: int = _window_api.EVENT_SYSTEM_FOREGROUND, time: int = 51) -> None:
        self.window(event, hwnd, time)
        self.events.join()

    def middle(self, point: tuple[int, int] = (-400, 250), time: int = 52) -> tuple[bool, bool]:
        down = self.mouse(_window_api.WM_MBUTTONDOWN, point, time, "")
        up = self.mouse(_window_api.WM_MBUTTONUP, point, time + 1, "")
        self.events.join()
        return down, up


@pytest.fixture(name="window_serve")
def _window_serve(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> Any:
    backend = _WindowBackend()
    original = _window_move.WindowMover
    monkeypatch.setattr(_cli.sys, "platform", "win32")

    def create_monitor(**kwargs: Any) -> Any:
        del kwargs
        return original(api=backend, uia_factory=backend.uia, events=backend.events)

    monkeypatch.setattr(_window_move, "WindowMover", create_monitor)

    def run(scenario: Any, *, omitted: bool = False) -> None:
        backend.stopped.clear()

        async def server(app: Any, config: Any, **kwargs: Any) -> None:
            del config, kwargs
            assert backend.uia_active
            await scenario(backend, app)

        monkeypatch.setattr(_cli.hypercorn.asyncio, "serve", server)
        args = [] if omitted else ["serve", "--token-file", str(tmp_path / "token")]
        try:
            assert _cli.main(args) == 0
        finally:
            assert backend.stopped.is_set()
            assert not backend.uia_active
            assert not _cli.default_pid_path().exists()
            assert backend.middle() == (False, False)

    return run


def test_serve_moves_taskbar_selection_once(window_serve: Any) -> None:
    async def scenario(backend: _WindowBackend, app: Any) -> None:
        del app
        backend.click()
        backend.select()
        assert backend.middle() == (True, True)
        assert backend.moves == [(100, (-400, 250))]
        assert backend.middle() == (False, False)
        assert len(backend.moves) == 1

    window_serve(scenario, omitted=True)


@pytest.mark.parametrize(
    "root,point", [("Application", (10, 10)), ("Shell_TrayWnd", (20, 10)), ("NotifyIconOverflowWindow", (10, 10))]
)
def test_serve_passes_unselected_middle_click(window_serve: Any, root: str, point: tuple[int, int]) -> None:
    async def scenario(backend: _WindowBackend, app: Any) -> None:
        del app
        backend.click(root, point)
        backend.select()
        assert backend.middle() == (False, False)
        assert not backend.moves

    window_serve(scenario)


@pytest.mark.parametrize("root,event", [("TaskListThumbnailWnd", 3), ("Shell_TrayWnd", 0x16)])
def test_serve_tracks_selected_window_events(window_serve: Any, root: str, event: int) -> None:
    async def scenario(backend: _WindowBackend, app: Any) -> None:
        del app
        backend.click(root)
        backend.select(999)  # Shell側HWNDをアプリとして採らない。
        backend.select(100, event)
        backend.select(200)  # 最小化後の別の前面を使わない。
        assert backend.middle() == (True, True)
        assert backend.moves == [(100, (-400, 250))]

    window_serve(scenario)


def test_serve_consumes_failed_selection(window_serve: Any) -> None:
    async def scenario(backend: _WindowBackend, app: Any) -> None:
        del app
        backend.click()
        backend.select()
        backend.targets.pop(100)
        assert backend.middle() == (True, True)
        assert not backend.moves
        assert backend.middle() == (False, False)

    window_serve(scenario)


def test_serve_consumes_api_failure(window_serve: Any) -> None:
    async def scenario(backend: _WindowBackend, app: Any) -> None:
        del app
        backend.click()
        backend.select()
        backend.failure = True
        assert backend.middle() == (True, True)
        assert not backend.moves
        assert backend.middle() == (False, False)

    window_serve(scenario)


def test_serve_rejects_old_events_and_previous_selection(window_serve: Any) -> None:
    async def scenario(backend: _WindowBackend, app: Any) -> None:
        del app
        backend.click()
        backend.select(time=49)
        assert backend.middle() == (False, False)
        backend.click()
        backend.select()
        backend.click(point=(20, 10))
        assert backend.middle() == (False, False)
        assert not backend.moves

    window_serve(scenario)


@pytest.mark.parametrize("event", [_window_api.EVENT_SYSTEM_FOREGROUND, _window_api.EVENT_SYSTEM_MINIMIZESTART])
def test_serve_passes_delayed_unrelated_window_event(window_serve: Any, event: int) -> None:
    async def scenario(backend: _WindowBackend, app: Any) -> None:
        del app
        backend.click(time=50)
        backend.select(200, event=event, time=1000)
        assert backend.middle(time=1001) == (False, False)
        assert not backend.moves

    window_serve(scenario)


def test_serve_keyboard_interrupt_cannot_select_another_window(window_serve: Any) -> None:
    async def scenario(backend: _WindowBackend, app: Any) -> None:
        del app
        backend.click()
        backend.key()  # Alt+Tab等の新しい操作。
        backend.select(200, time=51)
        assert backend.middle() == (False, False)
        assert not backend.moves

    window_serve(scenario)


def test_serve_selected_window_survives_later_keyboard_input(window_serve: Any) -> None:
    async def scenario(backend: _WindowBackend, app: Any) -> None:
        del app
        backend.click()
        backend.select()
        backend.key()
        backend.select(200, time=2000)
        assert backend.middle(time=2001) == (True, True)
        assert backend.moves == [(100, (-400, 250))]

    window_serve(scenario)


def test_serve_long_button_hold_uses_release_time(window_serve: Any) -> None:
    async def scenario(backend: _WindowBackend, app: Any) -> None:
        del app
        backend.click(time=50)
        assert not backend.mouse(_window_api.WM_LBUTTONUP, (10, 10), 1000, "")
        backend.events.join()
        backend.select(time=1001)
        assert backend.middle(time=1002) == (True, True)
        assert backend.moves == [(100, (-400, 250))]

    window_serve(scenario)


@pytest.mark.parametrize("click_time,event_time", [(50, 550), (0xFFFFFFFA, 2)])
def test_serve_accepts_click_interval_boundary_and_tick_wrap(window_serve: Any, click_time: int, event_time: int) -> None:
    async def scenario(backend: _WindowBackend, app: Any) -> None:
        del app
        backend.click(time=click_time)
        backend.select(time=event_time)
        assert backend.middle(time=event_time + 1) == (True, True)
        assert backend.moves == [(100, (-400, 250))]

    window_serve(scenario)


@pytest.mark.parametrize("error", [RuntimeError("server failed"), asyncio.CancelledError()])
def test_serve_releases_monitor_on_failure_or_cancellation(window_serve: Any, error: BaseException) -> None:
    async def scenario(backend: _WindowBackend, app: Any) -> None:
        del app
        backend.click()
        backend.select()
        raise error

    with pytest.raises(type(error)):
        window_serve(scenario)


def test_serve_restart_does_not_keep_selection(window_serve: Any) -> None:
    async def select_scenario(backend: _WindowBackend, app: Any) -> None:
        del app
        backend.click()
        backend.select()

    async def restart_scenario(backend: _WindowBackend, app: Any) -> None:
        del app
        assert backend.middle() == (False, False)
        assert not backend.moves

    window_serve(select_scenario)
    window_serve(restart_scenario)


def test_serve_stops_monitor_and_keeps_media_api(window_serve: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    keys: list[str] = []
    monkeypatch.setattr(_keys, "send_key", keys.append)

    # 認証値を生成する境界だけを固定し、HTTPから送信までの実装を通す。
    monkeypatch.setattr(_token, "load_or_create_token", lambda *args, **kwargs: VALID_TOKEN)

    async def authenticated_scenario(backend: _WindowBackend, app: Any) -> None:
        backend.click()
        backend.select()
        client = app.test_client()
        response = await client.post("/api/key/play_pause", query_string={"t": VALID_TOKEN})
        assert response.status_code == 204
        assert keys == ["play_pause"]

    window_serve(authenticated_scenario)


def test_doctor_subcommand_rejects_non_windows(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(_cli.sys, "platform", "linux")
    token_path = tmp_path / "token.txt"
    assert _cli.main(["doctor", "--token-file", str(token_path)]) == 1


def test_doctor_subcommand_renders_all_sections(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    """Windowsモック実行でlisten・process・profile・firewall・url・recommendationsが揃って出力される。"""
    monkeypatch.setattr(_cli.sys, "platform", "win32")
    # PIDファイルを事前配置（プロセスセクションで参照される）。
    pid_path = _cli.default_pid_path()
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text("4242", encoding="utf-8")

    token_path = tmp_path / "token.txt"
    token_path.write_text(VALID_TOKEN + "\n", encoding="utf-8")

    # 各PowerShell呼び出しのスクリプト内容で分岐してダミー出力を返す。
    def fake_run(
        cmd: list[str],
        *,
        timeout: float | None = None,
        cwd: pathlib.Path | None = None,
        tag: str | None = None,
        **kwargs: Any,
    ) -> Any:
        del timeout, cwd, tag, kwargs
        script = " ".join(cmd)
        if "Get-NetTCPConnection" in script:
            stdout = "0.0.0.0:29123 (PID=4242)\n"
        elif "Get-CimInstance" in script:
            stdout = "PID=4242 CommandLine=dotfiles-media-remote.exe serve\n"
        elif "Get-NetConnectionProfile" in script:
            stdout = "Ethernet: Private\nWi-Fi: Public\n"
        elif "Get-NetFirewallRule" in script:
            stdout = "NONE\n"
        else:
            stdout = ""
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(_cli.claude_common, "run_subprocess", fake_run)
    monkeypatch.setattr(_cli, "detect_local_ip", lambda: "192.168.1.10")

    exit_code = _cli.main(["doctor", "--token-file", str(token_path), "--port", "29123"])
    out = capsys.readouterr().out
    assert exit_code == 0
    # 全セクション見出しが含まれる。
    for header in ("== listen ==", "== process ==", "== profile ==", "== firewall ==", "== url ==", "== recommendations =="):
        assert header in out
    # listenの実体・プロセス情報・FW規則なし表示・URL組み立て結果。
    assert "0.0.0.0:29123" in out
    assert "PID=4242" in out
    assert "該当するFW規則なし" in out
    assert f"http://192.168.1.10:29123/?t={VALID_TOKEN}" in out
    # 推奨修復: FW規則未登録 + Publicプロファイル該当の2件が出る。
    assert "New-NetFirewallRule" in out
    assert "Set-NetConnectionProfile" in out
    # listen中なのでサーバー起動コマンドは推奨されない。
    assert "Start-Process" not in out


def test_assets_index_html_references_all_keys():
    for name in _keys.VK_CODES:
        assert f'data-key="{name}"' in _assets.INDEX_HTML
