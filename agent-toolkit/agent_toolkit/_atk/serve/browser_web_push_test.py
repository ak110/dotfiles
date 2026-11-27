import asyncio
import base64
import json
import pathlib
from collections.abc import AsyncIterator

import playwright.async_api
import pytest
import pytest_asyncio
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from http_ece import decrypt

from agent_toolkit._atk import config_settings
from agent_toolkit._atk.serve.browser_support_test import (  # noqa: F401  # pylint: disable=unused-import
    _browser_app,
    _browser_fixture,
    _isolate_creation_time_index,
    _screen_harness_fixture,
    _ScreenHarness,
    _serve,
    _write_entries,
)
from agent_toolkit._testing import wi_entry_files

pytestmark = [pytest.mark.browser]


@pytest_asyncio.fixture(scope="session", loop_scope="session", name="notification_browser")
async def _notification_browser() -> AsyncIterator[playwright.async_api.Browser]:
    # 通知の権限はChromium Headless Shellでは常にdeniedになる。 pyfltr: allow-comment
    async with playwright.async_api.async_playwright() as runner:
        browser = await runner.chromium.launch(channel="chromium")
        try:
            yield browser
        finally:
            await browser.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_web_push_disabled_never_registers_service_worker(screen_harness: _ScreenHarness) -> None:
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/")
    await playwright.async_api.expect(page.locator("#entry-list .entry-row").first).to_be_visible()
    await playwright.async_api.expect(page.get_by_role("button", name="閉じていても通知する")).to_be_hidden()
    assert await page.evaluate("async () => (await navigator.serviceWorker.getRegistrations()).length") == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_worker_displays_notification_without_work_item_page(
    notification_browser: playwright.async_api.Browser, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_creation_time_index(tmp_path, monkeypatch)
    config_settings.save_config({"serve_web_push": "true"})
    plans = tmp_path / "plans"
    plans.mkdir()
    _write_entries(tmp_path)
    packets: asyncio.Queue[requests.PreparedRequest] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def receive(_session, request, **_kwargs):
        loop.call_soon_threadsafe(packets.put_nowait, request)
        response = requests.Response()
        response.status_code = 201
        return response

    monkeypatch.setattr(requests.Session, "send", receive)
    app = _browser_app(tmp_path, plans)
    async with _serve(app, notification_browser) as (context, page, port):
        base = f"http://127.0.0.1:{port}"
        await context.grant_permissions(["notifications"], origin=base)
        # CDPの受信操作を使うため、外部のPushサービスとの購読だけを代用する。 pyfltr: allow-comment
        endpoint = "https://fcm.googleapis.com/fcm/send/fixture"
        receiver_key = ec.generate_private_key(ec.SECP256R1())
        public = receiver_key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        auth_secret = bytes(16)
        keys = {
            "p256dh": base64.urlsafe_b64encode(public).decode().rstrip("="),
            "auth": base64.urlsafe_b64encode(auth_secret).decode().rstrip("="),
        }
        fake = json.dumps({"endpoint": endpoint, "keys": keys})
        await page.add_init_script(
            f"""(() => {{
              let subscription;
              PushManager.prototype.getSubscription = async () => subscription;
              PushManager.prototype.subscribe = async () => subscription = {{
                endpoint: {json.dumps(endpoint)}, toJSON: () => ({fake})
              }};
            }})();"""
        )
        await page.goto(base + "/")
        subscribe = page.get_by_role("button", name="閉じていても通知する")
        await playwright.async_api.expect(subscribe).to_be_visible()
        async with page.expect_response(
            lambda response: "/api/web-push" in response.url and response.request.method == "POST"
        ) as pending:
            await subscribe.click()
        assert await (await pending.value).json() == {"subscribed": True}
        await playwright.async_api.expect(page.get_by_role("button", name="Web Pushを解除")).to_be_visible()

        holder = await context.new_page()
        await holder.goto(base + "/agents")
        await page.close()
        cdp = await context.new_cdp_session(holder)
        registered: asyncio.Future[str] = asyncio.get_running_loop().create_future()

        def on_update(event):
            for registration in event["registrations"]:
                if registration["scopeURL"] == base + "/" and not registered.done():
                    registered.set_result(registration["registrationId"])

        cdp.on("ServiceWorker.workerRegistrationUpdated", on_update)
        await cdp.send("ServiceWorker.enable")
        registration_id = await asyncio.wait_for(registered, timeout=5)
        item = "20261125-231000-001.md"
        wi_entry_files.write_uwi(tmp_path, item, state="inbox")
        app.config["SERVE_STATE"].publish()
        packet = await asyncio.wait_for(packets.get(), timeout=10)
        assert packet.url == endpoint
        assert packet.headers["Content-Encoding"] == "aes128gcm"
        assert isinstance(packet.body, bytes)
        assert item.encode() not in packet.body
        data = decrypt(packet.body, private_key=receiver_key, auth_secret=auth_secret, version="aes128gcm").decode()
        url = "./?filename=" + item
        assert json.loads(data) == {"title": "atk serveに新しい確認がある", "body": "", "url": url}
        await cdp.send(
            "ServiceWorker.deliverPushMessage",
            {
                "origin": base,
                "registrationId": registration_id,
                "data": data,
            },
        )
        await holder.wait_for_function(
            """async url => {
              const registration = await navigator.serviceWorker.getRegistration();
              const notices = await registration.getNotifications();
              return notices.some(notice => notice.title === 'atk serveに新しい確認がある' &&
                notice.data.url === url);
            }""",
            arg=url,
        )
