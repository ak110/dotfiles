"""連続・同時準備で、遅れてreadyになった旧世代が受付先を戻さない。"""

import pathlib
from concurrent.futures import ThreadPoolExecutor

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
