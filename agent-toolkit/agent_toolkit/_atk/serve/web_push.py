from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import re
import threading
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import pywebpush
import quart
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._common.state_paths import state_dir

_LOGGER = logging.getLogger(__name__)
_FIXED_HOSTS = frozenset({"fcm.googleapis.com", "updates.push.services.mozilla.com", "web.push.apple.com"})
_DOMAIN = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?:notify\.windows\.com|push\.apple\.com)")
_KEY = re.compile(r"[A-Za-z0-9_-]+")


def _endpoint(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"https://[a-z0-9.-]+/[!-~]{1,4096}", value):
        raise ValueError
    parts = urlsplit(value)
    if (
        (parts.netloc not in _FIXED_HOSTS and not _DOMAIN.fullmatch(parts.netloc))
        or parts.fragment
        or "#" in value
        or "\\" in value
    ):
        raise ValueError
    return value


def _decode_key(value: object, length: int) -> bytes:
    if not isinstance(value, str) or len(value) != (length * 8 + 5) // 6 or not _KEY.fullmatch(value):
        raise ValueError
    decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    if len(decoded) != length:
        raise ValueError
    return decoded


def _subscription(data: dict) -> dict:
    endpoint = _endpoint(data.get("endpoint"))
    keys = data.get("keys")
    if not isinstance(keys, dict):
        raise ValueError
    ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), _decode_key(keys.get("p256dh"), 65))
    _decode_key(keys.get("auth"), 16)
    return {"endpoint": endpoint, "keys": {key: keys[key] for key in ("p256dh", "auth")}}


def _send(subscription: dict, payload: str, key_path: Path) -> int:
    endpoint = _endpoint(subscription["endpoint"])
    host = urlsplit(endpoint).netloc

    class NoRedirectSession(requests.Session):
        def send(self, request: requests.PreparedRequest, **kwargs) -> requests.Response:
            _endpoint(request.url)
            kwargs["allow_redirects"] = False
            return super().send(request, **kwargs)

    try:
        with NoRedirectSession() as session:
            session.trust_env = False
            response = pywebpush.webpush(
                subscription_info=subscription,
                data=payload,
                vapid_private_key=str(key_path),
                vapid_claims={"sub": "mailto:ak110@users.noreply.github.com"},
                requests_session=session,
                headers={"X-WNS-Type": "wns/raw"} if host.endswith(".notify.windows.com") else None,
                timeout=5,
                ttl=3600,
            )
            if isinstance(response, requests.Response):
                return response.status_code
            raise RuntimeError("Web PushサービスからHTTPの応答を取得できません")
    except pywebpush.WebPushException as error:
        if error.response is not None:
            return error.response.status_code
        raise


def _target(entry: dict) -> bool:
    return entry.get("state") in ("inbox", "processing") and (
        (entry.get("kind") == "uwi" and entry.get("answered") is False)
        or (entry.get("kind") == "awi" and entry.get("needs_verify") is True)
    )


def _payload(entry: dict, *, details: bool) -> str:
    body = ""
    if details:
        # summaryは見出しが無い項目では本文冒頭を含むので、見出しだけを外へ出す。 pyfltr: allow-comment
        summary = entry.get("summary")
        title = summary[2:] if isinstance(summary, str) and summary.startswith("# ") else ""
        repo = entry.get("target_repo")
        body = " / ".join(value[:160] for value in (title, repo) if isinstance(value, str) and value)
    return json.dumps(
        {
            "title": "atk serveに新しい確認がある",
            "body": body,
            "url": "./?" + urlencode({"filename": entry["filename"]}),
        },
        ensure_ascii=False,
    )


class WebPush:
    def __init__(self, directory: Path, *, details: bool = False) -> None:
        self.key_path = directory / "vapid.pem"
        self.known_path = directory / "known.json"
        self.subscriptions_path = directory / "subscriptions.json"
        self.details = details
        self.lock = threading.Lock()

    def public_key(self) -> str:
        with self.lock:
            try:
                raw = self.key_path.read_bytes()
            except FileNotFoundError:
                key = ec.generate_private_key(ec.SECP256R1())
                raw = key.private_bytes(
                    serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()
                )
                atomic_write(self.key_path, raw, fsync=True)
            loaded = serialization.load_pem_private_key(raw, password=None)
            if not isinstance(loaded, ec.EllipticCurvePrivateKey):
                raise ValueError
            data = loaded.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")

    def _subscriptions(self) -> dict:
        try:
            return json.loads(self.subscriptions_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}

    def update_subscription(self, endpoint: str, subscription: dict | None = None, *, remove: bool = False) -> bool:
        with self.lock:
            subscriptions = self._subscriptions()
            if subscription is not None:
                subscriptions[endpoint] = subscription
            elif remove:
                subscriptions.pop(endpoint, None)
            else:
                return endpoint in subscriptions
            atomic_write(self.subscriptions_path, json.dumps(subscriptions))
            return endpoint in subscriptions

    def scan(self, entries: list[dict]) -> None:
        with self.lock:
            try:
                known = set(json.loads(self.known_path.read_text(encoding="utf-8")))
            except FileNotFoundError:
                known = None
            current = {entry["filename"]: entry for entry in entries}
            added = [] if known is None else [entry for name, entry in current.items() if name not in known and _target(entry)]
            seen = (known or set()) | current.keys()
            if seen != known:
                atomic_write(self.known_path, json.dumps(sorted(seen)), fsync=True)
            subscriptions = self._subscriptions() if added else {}
        for entry in added:
            payload = _payload(entry, details=self.details)
            for endpoint, subscription in subscriptions.items():
                try:
                    with self.lock:
                        if self._subscriptions().get(endpoint) != subscription:
                            continue
                    status = _send(subscription, payload, self.key_path)
                    if status in (404, 410):
                        with self.lock:
                            latest = self._subscriptions()
                            if latest.get(endpoint) == subscription:
                                del latest[endpoint]
                                atomic_write(self.subscriptions_path, json.dumps(latest))
                    elif not 200 <= status < 300:
                        _LOGGER.warning("Web Pushの送信に失敗しました (HTTP %d)", status)
                except Exception:
                    # ライブラリの例外文字列には宛先や応答本文が含まれ得る。 pyfltr: allow-comment
                    _LOGGER.warning("Web Pushの送信に失敗しました")


def register(
    app: quart.Quart,
    load_entries: Callable[[], list[dict]],
    *,
    enabled: bool = False,
    details: bool = False,
    directory: Path | None = None,
) -> None:
    push = (
        WebPush(directory if directory is not None else state_dir() / "serve" / "web-push", details=details)
        if enabled
        else None
    )

    @app.route("/api/web-push", methods=["GET", "POST", "DELETE"])
    async def push_settings():
        if push is None:
            return {"enabled": False}, 200 if quart.request.method == "GET" else 404, {"Cache-Control": "no-store"}
        try:
            if quart.request.method == "GET":
                return (
                    {"enabled": True, "public_key": await asyncio.to_thread(push.public_key)},
                    200,
                    {"Cache-Control": "no-store"},
                )
            if quart.request.headers.get("Sec-Fetch-Site") in ("cross-site", "same-site"):
                return {"error": "別のサイトから購読を変更できません", "next_action": "atk serveのWI画面から操作する"}, 403
            if not quart.request.is_json or (quart.request.content_length or 0) > 8192:
                raise ValueError
            raw = await quart.request.get_data()
            if len(raw) > 8192:
                raise ValueError
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError
            endpoint = _endpoint(data.get("endpoint"))
            remove = quart.request.method == "DELETE"
            subscription = None if remove or data.get("check") is True else _subscription(data)
            saved = await asyncio.to_thread(push.update_subscription, endpoint, subscription, remove=remove)
            return {"subscribed": saved}
        except ValueError:
            return {"error": "購読情報が不正です", "next_action": "WI画面を開き直して購読状態を確認する"}, 400
        except Exception:
            _LOGGER.warning("Web Pushの購読情報を処理できませんでした")
            return {
                "error": "Web Pushの購読情報を処理できませんでした",
                "next_action": "WI画面を開き直して購読状態を確認し、続く場合はatk serveのログを確認する",
            }, 500

    if push is None:
        return

    @app.get("/wi-push-worker.js")
    async def push_worker():
        return await quart.send_file(
            Path(__file__).with_name("static") / "wi_push_worker.js",
            mimetype="text/javascript",
            cache_timeout=0,
        )

    @app.while_serving
    async def scan_for_new_entries():
        stop = asyncio.Event()

        def scan():
            try:
                push.scan(load_entries())
            except Exception:
                _LOGGER.warning("Web Pushの通知対象を取得できませんでした")

        async def run():
            while not stop.is_set():
                await asyncio.to_thread(scan)
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=5)

        try:
            await asyncio.to_thread(push.public_key)
        except Exception:
            _LOGGER.warning("Web Pushの鍵を準備できませんでした")
        task = asyncio.create_task(run())
        yield
        stop.set()
        await task
