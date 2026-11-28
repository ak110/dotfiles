"""共有した許可の回数を、private-notesの独立した2 cloneの合計で守ることを検証する。  pyfltr: allow-comment

外部の消費は`codex_auto_reset_test`の模擬RPCで置き換え、実在アカウントとcreditには接続しない。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import pathlib
import subprocess
import uuid

import pytest

from agent_toolkit._agents_server import codex_auto_reset
from agent_toolkit._agents_server.codex_auto_reset import check_auto_reset
from agent_toolkit._agents_server.codex_auto_reset_test import _RPC
from agent_toolkit._atk import config_settings, reset_reservations
from agent_toolkit._atk.wi import sync
from agent_toolkit._testing import git_repository


@pytest.fixture(name="clones")
def _clones(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    remote = git_repository.init_bare_repository(tmp_path / "remote.git")
    seed = git_repository.init_repository(tmp_path / "seed", initial_branch="main", origin=str(remote), files={".gitkeep": ""})
    git_repository.commit_all(seed, "initialize")
    git_repository.run_git(seed, "push", "-u", "origin", "main")
    clones = []
    for name in ("a", "b"):
        clone = tmp_path / f"notes-{name}"
        git_repository.run_git(tmp_path, "clone", str(remote), str(clone))
        git_repository.run_git(clone, "config", "user.name", name)
        git_repository.run_git(clone, "config", "user.email", f"{name}@example.invalid")
        clones.append(clone)
    return clones[0], clones[1], remote


def _publish_settings(notes: pathlib.Path, *, configured: bool = True) -> None:
    path = notes / config_settings.SHARED_SETTINGS_FILENAME
    path.write_text(json.dumps({"codex_auto_reset": str(configured).lower()}), encoding="utf-8")
    git_repository.commit_all(notes, "test: register reset setting")
    git_repository.run_git(notes, "push")


def _upstream_records(clone: pathlib.Path) -> list[dict]:
    git_repository.run_git(clone, "fetch")
    text = git_repository.git_output(clone, "show", f"@{{u}}:{reset_reservations.FILENAME}")
    return json.loads(text)["reservations"]


@pytest.mark.parametrize(("outcome", "terminal"), [("reset", "success"), ("noCredit", "closed")])
@pytest.mark.asyncio
async def test_restart_after_shared_completion_does_not_repeat_a_finished_attempt(
    tmp_path, monkeypatch, clones, outcome, terminal
):
    notes_a, notes_b, _ = clones
    state = tmp_path / "state"
    _publish_settings(notes_a)
    first = _RPC()
    first.outcomes = [outcome]
    window = first.limits["rateLimits"]["secondary"]["resetsAt"]
    monkeypatch.setattr(codex_auto_reset.time, "time", lambda: window - 600)
    write_attempt = codex_auto_reset._write_attempt  # noqa: SLF001  # pylint: disable=protected-access

    def interrupted(path, attempt):
        if attempt.status == terminal:
            raise OSError("共有確定後の停止")
        write_attempt(path, attempt)

    async def run(rpc):
        return await check_auto_reset(rpc, state_dir=state, configured=True, shared_notes=notes_a)

    with monkeypatch.context() as fault:
        fault.setattr(codex_auto_reset, "_write_attempt", interrupted)
        with pytest.raises(OSError, match="共有確定後の停止"):
            await run(first)
    [record] = _upstream_records(notes_b)
    assert record["status"] == terminal
    assert len(first.consumptions) == 1
    [local] = (state / "agents-server" / "codex-auto-reset").glob("*.json")
    assert json.loads(local.read_text(encoding="utf-8"))["status"] == "pending"

    restarted = _RPC()
    assert (await run(restarted)).reset_confirmed == (terminal == "success")
    assert restarted.consumptions == []
    assert json.loads(local.read_text(encoding="utf-8"))["status"] == terminal
    if terminal == "success":
        other = _RPC()
        await check_auto_reset(other, state_dir=tmp_path / "other", configured=True, shared_notes=notes_b)
        assert other.consumptions == []
    monkeypatch.setattr(codex_auto_reset.time, "time", lambda: window + 1)
    restarted.limits["rateLimits"]["secondary"]["resetsAt"] = window + 7 * 86400
    assert (await run(restarted)).reset_confirmed
    assert len(restarted.consumptions) == 1
    assert _upstream_records(notes_b)[-1]["key"] != record["key"]


@pytest.mark.asyncio
async def test_success_cannot_be_spent_twice_even_from_another_clone(tmp_path, monkeypatch, clones):
    """片方の端末で消費した週次窓を、他方の端末から再消費しない。  pyfltr: allow-comment"""
    notes_a, notes_b, _ = clones
    monkeypatch.setattr(codex_auto_reset.time, "time", lambda: 100.0)
    _publish_settings(notes_a)
    _pulled(notes_b)
    rpc_a, rpc_b = _RPC(used=100), _RPC(used=100)
    result = await check_auto_reset(rpc_a, state_dir=tmp_path / "state-a", configured=True, shared_notes=notes_a)
    assert result.reset_confirmed
    assert [item["status"] for item in _upstream_records(notes_a)] == ["success"]
    result = await check_auto_reset(rpc_b, state_dir=tmp_path / "state-b", configured=True, shared_notes=notes_b)
    assert not result.reset_confirmed
    assert not rpc_b.consumptions


def _pulled(clone: pathlib.Path) -> pathlib.Path:
    git_repository.run_git(clone, "pull", "--ff-only")
    return clone


@pytest.mark.asyncio
async def test_parallel_hosts_only_one_key_reaches_rpc(tmp_path, monkeypatch, clones):
    """両端末が古い台帳で判定しても、共有先の受理順で後の予約が拒否され、消費は送られない。  pyfltr: allow-comment"""
    notes_a, notes_b, _ = clones
    monkeypatch.setattr(codex_auto_reset.time, "time", lambda: 100.0)
    _publish_settings(notes_a)
    _pulled(notes_b)
    rpc_a, rpc_b = _RPC(used=100), _RPC(used=100)
    rpc_a.release = asyncio.Event()
    task = asyncio.create_task(check_auto_reset(rpc_a, state_dir=tmp_path / "state-a", configured=True, shared_notes=notes_a))
    await rpc_a.entered.wait()
    # 端末bは端末aの予約を取り込む前の作業ツリーで判定を始める。  # pyfltr: allow-comment
    monkeypatch.setattr(sync, "pull", lambda _notes: None)
    result_b = await check_auto_reset(rpc_b, state_dir=tmp_path / "state-b", configured=True, shared_notes=notes_b)
    rpc_a.release.set()
    result_a = await task
    assert result_a.reset_confirmed
    assert not result_b.reset_confirmed
    assert "消費を見送った" in result_b.diagnostic
    assert not rpc_b.consumptions
    assert git_repository.git_output(notes_b, "status", "--porcelain") == ""


@pytest.mark.asyncio
async def test_unsynced_reservation_does_not_consume(tmp_path, monkeypatch, clones):
    """共有先が予約を受理したと確認できない間は、消費を送らない。  pyfltr: allow-comment"""
    notes_a, _, _ = clones
    monkeypatch.setattr(codex_auto_reset.time, "time", lambda: 100.0)
    _publish_settings(notes_a)
    git = sync.run_git

    def fail_push(args, cwd, **kwargs):
        if args[:1] == ["push"]:
            raise subprocess.CalledProcessError(1, ["git", "push"])
        return git(args, cwd, **kwargs)

    monkeypatch.setattr(sync, "run_git", fail_push)
    rpc = _RPC(used=100)
    result = await check_auto_reset(rpc, state_dir=tmp_path / "state-a", configured=True, shared_notes=notes_a)
    assert not result.reset_confirmed
    assert not rpc.consumptions
    saved = list((tmp_path / "state-a" / "agents-server" / "codex-auto-reset").glob("*.json"))
    assert len(saved) == 1 and json.loads(saved[0].read_text())["status"] == "pending"
    assert git_repository.git_output(notes_a, "rev-list", "--count", "@{u}..HEAD") == "0"


@pytest.mark.asyncio
async def test_lost_response_retries_with_same_payload_on_original_host(tmp_path, monkeypatch, clones):
    """応答を失った消費は成功扱いせず、予約を数えたまま同じ冪等キーで再送する。  pyfltr: allow-comment"""
    notes_a, notes_b, _ = clones
    monkeypatch.setattr(codex_auto_reset.time, "time", lambda: 100.0)
    _publish_settings(notes_a)
    rpc = _RPC(used=100)
    rpc.outcomes = [TimeoutError("lost"), "alreadyRedeemed"]
    with pytest.raises(TimeoutError):
        await check_auto_reset(rpc, state_dir=tmp_path / "state-a", configured=True, shared_notes=notes_a)
    assert [item["status"] for item in _upstream_records(notes_a)] == ["pending"]
    # 応答不明の予約は他の端末でも回数に数える。  # pyfltr: allow-comment
    rpc_b = _RPC(used=100)
    result_b = await check_auto_reset(rpc_b, state_dir=tmp_path / "state-b", configured=True, shared_notes=notes_b)
    assert not result_b.reset_confirmed and not rpc_b.consumptions
    result = await check_auto_reset(rpc, state_dir=tmp_path / "state-a", configured=True, shared_notes=notes_a)
    assert result.reset_confirmed
    first, second = rpc.consumptions
    assert first["idempotencyKey"] == second["idempotencyKey"]
    assert [item["status"] for item in _upstream_records(notes_a)] == ["success"]


@pytest.mark.asyncio
async def test_rejected_consumption_releases_reservation(tmp_path, monkeypatch, clones):
    notes_a, _, _ = clones
    monkeypatch.setattr(codex_auto_reset.time, "time", lambda: 100.0)
    _publish_settings(notes_a)
    rpc = _RPC(used=100)
    rpc.outcomes = ["noCredit"]
    result = await check_auto_reset(rpc, state_dir=tmp_path / "state-a", configured=True, shared_notes=notes_a)
    assert not result.reset_confirmed
    assert [item["status"] for item in _upstream_records(notes_a)] == ["closed"]


@pytest.mark.asyncio
async def test_only_original_host_replays_frozen_legacy_payload(tmp_path, clones):
    notes_a, notes_b, _ = clones
    _publish_settings(notes_a)
    state = tmp_path / "state-a"
    identity = hashlib.sha256(b"example@example.invalid").hexdigest()
    local = state / "agents-server" / "codex-auto-reset" / f"{identity}.json"
    local.parent.mkdir(parents=True)
    key = str(uuid.uuid4())
    local.write_text(
        json.dumps(
            {
                "resets_at": 1900000000,
                "idempotency_key": key,
                "status": "pending",
                "permission_id": "old",
                "credit_id": "credit-original",
            }
        ),
        encoding="utf-8",
    )
    (notes_a / reset_reservations.FILENAME).write_text(
        json.dumps(
            {
                "reservations": [
                    {
                        "permission_id": "old",
                        "key": key,
                        "account": identity,
                        "resets_at": 1900000000,
                        "status": "pending",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    git_repository.commit_all(notes_a, "test: old pending")
    git_repository.run_git(notes_a, "push")
    other = _RPC()
    assert not (
        await check_auto_reset(other, state_dir=tmp_path / "state-b", configured=True, shared_notes=notes_b)
    ).reset_confirmed
    assert not other.consumptions
    original = _RPC()
    original.outcomes = ["alreadyRedeemed"]
    assert (await check_auto_reset(original, state_dir=state, configured=True, shared_notes=notes_a)).reset_confirmed
    assert original.consumptions == [{"idempotencyKey": key, "creditId": "credit-original"}]
    assert _upstream_records(notes_a)[0]["status"] == "success"


@pytest.mark.asyncio
async def test_disabled_flags_and_missing_remote_never_send(tmp_path, clones):
    notes_a, _, _ = clones
    _publish_settings(notes_a, configured=False)
    for configured, notes in ((False, notes_a), (True, notes_a), (True, None)):
        rpc = _RPC()
        result = await check_auto_reset(rpc, state_dir=tmp_path / "state", configured=configured, shared_notes=notes)
        assert not result.reset_confirmed and not rpc.consumptions
