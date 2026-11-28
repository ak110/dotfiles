# pylint: disable=protected-access

import base64
import json

import pytest
import quart
import requests

from agent_toolkit._atk.serve import web_push

ENDPOINT = "https://fcm.googleapis.com/fcm/send/test"
UWI = {"state": "inbox", "kind": "uwi", "answered": False}


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://fcm.googleapis.com/fcm/send/test",
        "https://fcm.googleapis.com.evil.example/push",
        "https://fcm.googleapis.com@evil.example/push",
        "https://fcm.googleapis.com:443/push",
        "https://foo.evilnotify.windows.com/push",
        "https://fcm.googleapis.com/push#",
        "https://fcm.googleapis.com/push\\anything",
    ],
)
def test_endpoint_rejects_unapproved_destination(endpoint):
    with pytest.raises(ValueError):
        web_push._endpoint(endpoint)


@pytest.mark.parametrize(
    "endpoint",
    [
        ENDPOINT,
        "https://updates.push.services.mozilla.com/wpush/v2/test",
        "https://db5.notify.windows.com/w/?token=abc",
        "https://web.push.apple.com/QTest",
    ],
)
def test_endpoint_accepts_provider(endpoint):
    assert web_push._endpoint(endpoint) == endpoint


def test_known_items_survive_state_changes_removal_and_restart(tmp_path, monkeypatch):
    push = web_push.WebPush(tmp_path)
    push.update_subscription(ENDPOINT, {"endpoint": ENDPOINT})
    sent = []

    def send(_subscription, payload, _key_path):
        sent.append(json.loads(payload)["url"])
        return 201

    monkeypatch.setattr(web_push, "_send", send)
    known = {**UWI, "filename": "known.md", "answered": True}
    new = {**UWI, "filename": "new.md"}
    awi = {"kind": "awi", "awaiting_observation": True, "state": "inbox", "filename": "review.md"}
    processing_awi = {**awi, "state": "processing", "filename": "review-processing.md"}
    normal_awi = {**awi, "awaiting_observation": False, "filename": "work.md"}
    answered = {**UWI, "filename": "answered.md", "answered": True}
    push.scan([known])
    push.scan([{**known, "answered": False}, new, answered, awi, processing_awi, normal_awi])
    push.scan([])
    web_push.WebPush(tmp_path).scan([known, new, awi, processing_awi, normal_awi])
    assert sent == ["./?filename=new.md"]


def test_only_expired_subscriptions_are_deleted_and_processing_continues(tmp_path, monkeypatch, caplog):
    push = web_push.WebPush(tmp_path)
    outcomes = [404, 410, 500, 429, None, 201]
    endpoints = [f"{ENDPOINT}-{index}" for index in range(len(outcomes))]
    for endpoint in endpoints:
        push.update_subscription(endpoint, {"endpoint": endpoint})
    sent = []

    def send(subscription, _payload, _key_path):
        endpoint = subscription["endpoint"]
        sent.append(endpoint)
        status = outcomes[endpoints.index(endpoint)]
        if status is None:
            raise OSError("機密の宛先: " + endpoint)
        return status

    monkeypatch.setattr(web_push, "_send", send)
    push.scan([])
    push.scan([{**UWI, "filename": "new.md"}])
    assert sent == endpoints
    assert list(push._subscriptions()) == endpoints[2:]
    assert len(caplog.records) == 3
    assert ENDPOINT not in caplog.text


def test_payload_omits_details_by_default_and_never_uses_body_as_title():
    entry = {"filename": "a+b.md", "summary": "# 内部の表題", "target_repo": "秘密のリポジトリ"}
    basic = json.loads(web_push._payload(entry, details=False))
    detail = json.loads(web_push._payload(entry, details=True))
    assert basic == {"title": "atk serveに新しい確認がある", "body": "", "url": "./?filename=a%2Bb.md"}
    assert detail["body"] == "内部の表題 / 秘密のリポジトリ"
    assert "本文" not in web_push._payload({**entry, "summary": "本文の冒頭"}, details=True)


@pytest.mark.asyncio
async def test_disabled_does_not_read_entries_generate_keys_or_serve_worker(tmp_path):
    app = quart.Quart(__name__)

    def load():
        pytest.fail("無効時に通知対象を取得してはいけない")

    web_push.register(app, load, directory=tmp_path)
    async with app.test_app():  # ty: ignore[invalid-context-manager]
        client = app.test_client()
        response = await client.get("/api/web-push")
        assert await response.get_json() == {"enabled": False}
        assert (await client.get("/wi-push-worker.js")).status_code == 404
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_enabled_routes_save_and_remove_subscription_without_disclosing_it(tmp_path):
    app = quart.Quart(__name__)
    web_push.register(app, lambda: [], enabled=True, directory=tmp_path)
    await app.startup()
    try:
        client = app.test_client()
        settings = await (await client.get("/api/web-push")).get_json()
        assert set(settings) == {"enabled", "public_key"}
        assert settings["enabled"] is True
        worker = await client.get("/wi-push-worker.js")
        assert worker.status_code == 200
        assert worker.mimetype == "text/javascript"
        worker_data = await worker.get_data()
        assert isinstance(worker_data, bytes)
        assert b"showNotification" in worker_data
        data = {
            "endpoint": ENDPOINT,
            "keys": {
                "p256dh": settings["public_key"],
                "auth": base64.urlsafe_b64encode(b"0123456789abcdef").decode().rstrip("="),
            },
        }
        invalid = await client.post("/api/web-push", json={"endpoint": ENDPOINT})
        assert invalid.status_code == 400
        assert (await invalid.get_json())["next_action"] == "WI画面を開き直して購読状態を確認する"
        cross_site = await client.delete("/api/web-push", json=data, headers={"Sec-Fetch-Site": "cross-site"})
        assert cross_site.status_code == 403
        assert (await cross_site.get_json())["next_action"] == "atk serveのWI画面から操作する"
        added = await client.post("/api/web-push", json=data)
        assert await added.get_json() == {"subscribed": True}
        check = await client.post("/api/web-push", json={"endpoint": ENDPOINT, "check": True})
        assert await check.get_json() == {"subscribed": True}
        removed = await client.delete("/api/web-push", json={"endpoint": ENDPOINT})
        assert await removed.get_json() == {"subscribed": False}
    finally:
        await app.shutdown()
    assert web_push.WebPush(tmp_path).public_key() == settings["public_key"]


def test_encrypts_with_pywebpush_without_following_redirect(tmp_path, monkeypatch):
    push = web_push.WebPush(tmp_path)
    subscription = {
        "endpoint": ENDPOINT,
        "keys": {
            "p256dh": push.public_key(),
            "auth": base64.urlsafe_b64encode(b"0123456789abcdef").decode().rstrip("="),
        },
    }
    prepared = []

    def send(_session, request, **kwargs):
        prepared.append(request)
        assert kwargs["allow_redirects"] is False
        response = requests.Response()
        response.status_code = 307
        response.headers["Location"] = "https://example.invalid/"
        response._content = b""
        return response

    monkeypatch.setattr(requests.Session, "send", send)
    assert web_push._send(subscription, "秘密の通知", push.key_path) == 307
    assert len(prepared) == 1
    assert prepared[0].url == ENDPOINT
    assert prepared[0].headers["Content-Encoding"] == "aes128gcm"
    assert "秘密の通知".encode() not in prepared[0].body
