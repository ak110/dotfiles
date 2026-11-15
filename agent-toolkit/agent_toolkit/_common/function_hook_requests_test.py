"""mod応答との相関と一時リクエスト回収。"""

import asyncio
import json

import pytest

from agent_toolkit._common import function_hook_requests as requests


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "interactive,expected_version,actual_version,expected",
    [
        (True, None, None, True),
        (False, None, "2.1.325", False),
        (True, "2.1.325", "2.1.325", True),
        (True, "2.1.325", "2.1.324", False),
        (True, "2.1.325", "2.1.325-dev.20261115", False),
        (True, "2.1.325", None, False),
        (False, "2.1.325", "2.1.325", False),
    ],
)
@pytest.mark.parametrize("context_ok", [False, True])
async def test_interactive_requires_actual_mod_response(interactive, expected_version, actual_version, expected, context_ok):
    """古いready印を使わず、画面の実取得を返した同じ要求だけを採用する。"""
    task = asyncio.create_task(
        asyncio.to_thread(
            requests.interactive_available,
            "interactive-session",
            expected_version=expected_version,
            context_check=lambda reply: reply.get("context_ok") is True,
        )
    )
    base = requests.state_path("interactive-session", "ui-request")
    try:
        while not (paths := list(base.parent.glob(f"{base.stem}-*.txt"))):
            await asyncio.sleep(0)
        pending = json.loads(paths[0].read_text(encoding="utf-8"))
        assert pending["operation"] == "interactive_available"
        requests.request_path("interactive-session", "ui-response", pending["id"]).write_text(
            json.dumps(
                {
                    "id": pending["id"],
                    "ok": True,
                    "interactive": interactive,
                    "version": actual_version,
                    "context_ok": context_ok,
                }
            ),
            encoding="utf-8",
        )
        assert await task is (expected and context_ok)
    finally:
        await task
    assert not list(base.parent.glob(f"{base.stem}-*.txt"))


@pytest.mark.asyncio
async def test_concurrent_requests_do_not_overwrite_each_other():
    """異なる生成主体の要求を固有ファイルに置き、全件の応答と回収を分離する。"""
    tasks = [asyncio.create_task(requests.request("parallel", "send_to_user", {"message": str(index)})) for index in range(2)]
    base = requests.state_path("parallel", "ui-request")
    try:
        while len(paths := list(base.parent.glob(f"{base.stem}-*.txt"))) != 2:
            await asyncio.sleep(0)
        identifiers = set()
        for path in paths:
            pending = json.loads(path.read_text(encoding="utf-8"))
            identifiers.add(pending["id"])
            requests.request_path("parallel", "ui-response", pending["id"]).write_text(
                json.dumps({"id": pending["id"], "ok": True}), encoding="utf-8"
            )
        assert len(identifiers) == 2
        assert len(await asyncio.gather(*tasks)) == 2
    finally:
        await asyncio.gather(*tasks)
    assert not list(base.parent.glob(f"{base.stem}-*.txt"))


@pytest.mark.asyncio
async def test_missing_acknowledgement_is_not_success():
    """modが応答しない場合は成功を返さず回収する。"""
    with pytest.raises(TimeoutError, match="期限内"):
        await requests.request("conversation", "send_to_user", {}, timeout=0)
    assert not requests.state_path("conversation", "ui-request").exists()


def test_session_path_validation():
    """会話IDによるディレクトリ外への書込みを拒否する。"""
    with pytest.raises(ValueError, match="会話ID"):
        requests.state_path("../outside", "ui-request")


def test_interactive_missing_reply_is_false_even_with_ready_marker(monkeypatch):
    """古いready印と要求の受付だけでは対話画面の証拠にしない。"""
    base = requests.state_path("missing-ui", "ui-request")
    base.parent.mkdir(parents=True, exist_ok=True)
    (base.parent / "marker-missing-ui.txt").write_text("ready", encoding="utf-8")
    enqueue = requests.enqueue
    monkeypatch.setattr(
        requests,
        "enqueue",
        lambda session, operation, inputs, **kwargs: enqueue(session, operation, inputs, timeout=0, detached=False),
    )
    assert requests.interactive_available("missing-ui") is False
    assert not list(base.parent.glob(f"{base.stem}-*.txt"))


def test_cleanup_does_not_collect_session_with_common_prefix():
    """会話IDが前方一致する別会話の要求・応答を回収しない。"""
    # -SのASCII和は128で、prefixと同じ分割先になる。
    identifier, _ = requests.enqueue("prefix-S", "periodic_recheck", {}, timeout=0)
    source = requests.request_path("prefix-S", "ui-request", identifier)
    response = requests.request_path("prefix-S", "ui-response", identifier)
    response.write_text(json.dumps({"id": identifier, "expires": 0}), encoding="utf-8")
    requests.cleanup("prefix")
    assert source.exists() and response.exists()
    requests.cleanup("prefix-S")
    assert not source.exists() and not response.exists()
