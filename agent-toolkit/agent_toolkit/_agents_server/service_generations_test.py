"""連続・同時準備で、遅れてreadyになった旧世代が受付先を戻さない。"""

import pathlib
from concurrent.futures import ThreadPoolExecutor

import pytest

from agent_toolkit._agents_server import service_transport
from agent_toolkit._agents_server.service_generations import GenerationRegistry


def test_three_generations_publish_in_reverse_order(tmp_path: pathlib.Path) -> None:
    registry = GenerationRegistry(tmp_path)
    reserved = [registry.reserve(f"generation-{number}", tmp_path / str(number), f"1.0.{number}") for number in range(3)]
    assert [item["epoch"] for item in reserved] == [1, 2, 3]
    for number in (2, 0, 1):
        accepted = registry.publish(f"generation-{number}", endpoint=f"endpoint-{number}", process=f"process-{number}")
        assert accepted["generation"] == "generation-2"
    assert registry.reserve("generation-0", tmp_path, "1.0.0")["generation"] == "generation-2"
    assert registry.reserve("unknown-older", tmp_path, "0.9.0")["generation"] == "generation-2"


def test_same_generation_startup_requests_join(tmp_path: pathlib.Path) -> None:
    def reserve(_index: int) -> dict:
        return GenerationRegistry(tmp_path).reserve("same-version", tmp_path, "1.0.0")

    with ThreadPoolExecutor(max_workers=4) as workers:
        values = list(workers.map(reserve, range(8)))
    assert {value["epoch"] for value in values} == {1}
    assert GenerationRegistry(tmp_path).current() is None
    assert GenerationRegistry(tmp_path / "isolated").current() is None


def test_failed_preparation_leaves_accepting_generation(tmp_path: pathlib.Path) -> None:
    registry = GenerationRegistry(tmp_path)
    registry.reserve("initial", tmp_path, "1.0.0")
    registry.publish("initial", endpoint="initial", process="initial")
    registry.reserve("not-ready", tmp_path, "1.0.1")
    current = registry.current()
    assert current is not None and current["generation"] == "initial"


def _published(tmp_path: pathlib.Path) -> GenerationRegistry:
    registry = GenerationRegistry(tmp_path)
    for name, version in (("old", "1.0.0"), ("new", "1.0.1")):
        registry.reserve(name, tmp_path / name, version)
        registry.publish(name, endpoint=name, process=name)
    return registry


def test_restore_returns_acceptance_to_live_generation_after_published_one_exited(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """公開後に終了した世代を受付先に残さず、生存する世代を、全世代が新しい受付先と観測できる順序で戻す。

    2026-11-18、起動確認の終了とともに終了した新世代を受付先が指し続け、旧世代の会話の保存と送達が失敗した。
    """
    registry = _published(tmp_path)
    exited = registry.current()
    assert exited is not None
    monkeypatch.setattr(service_transport, "process_alive", lambda identity: identity != "new")
    restored = registry.restore()
    assert restored is not None and restored["generation"] == "old"
    assert registry.current() == restored

    assert restored["epoch"] >= exited["epoch"]

    relaunched = registry.reserve("new", tmp_path / "new", "1.0.1")
    assert relaunched["generation"] == "new" and not relaunched["ready"]
    assert registry.publish("new", endpoint="new", process="relaunched")["generation"] == "new"


def test_restore_returns_acceptance_to_newest_of_several_live_generations(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = GenerationRegistry(tmp_path)
    for name, version in (("oldest", "1.0.0"), ("middle", "1.0.1"), ("exited", "1.0.2")):
        registry.reserve(name, tmp_path / name, version)
        registry.publish(name, endpoint=name, process=name)
    monkeypatch.setattr(service_transport, "process_alive", lambda identity: identity != "exited")
    restored = registry.restore()
    assert restored is not None and restored["generation"] == "middle"


def test_restore_keeps_newer_candidate_ahead_of_exited_generation(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = _published(tmp_path)
    registry.reserve("next", tmp_path / "next", "1.0.2")
    monkeypatch.setattr(service_transport, "process_alive", lambda identity: identity != "new")
    registry.restore()
    assert registry.publish("next", endpoint="next", process="next")["generation"] == "next"
    # 終了した旧版の配布物から接続しても、公開済みの新しい版を受付先から外さない。
    assert registry.reserve("new", tmp_path / "new", "1.0.1")["generation"] == "next"
    assert registry.publish("new", endpoint="new", process="relaunched")["generation"] == "next"


@pytest.mark.parametrize("alive", [{"old", "new"}, set()], ids=["current-alive", "no-survivor"])
def test_restore_keeps_acceptance_without_exited_current_or_survivor(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, alive: set[str]
) -> None:
    registry = _published(tmp_path)
    before = registry.generations()
    monkeypatch.setattr(service_transport, "process_alive", lambda identity: identity in alive)
    restored = registry.restore()
    assert restored is not None and restored["generation"] == "new"
    assert registry.generations() == before
