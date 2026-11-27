import json

import pytest

from agent_toolkit._agents_server import service_transport
from agent_toolkit._agents_server.service_generations import GenerationRegistry
from agent_toolkit._atk import config_repository_readers
from agent_toolkit._common import agents_generation_index, state_paths
from agent_toolkit._common.next_action import ActionableError


def test_repository_format_waits_for_first_compatible_acceptor(tmp_path, monkeypatch):
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(agents_generation_index, "generation_process_alive", lambda process: process == "new-process")
    directory = tmp_path / "services" / "agents-server"
    registry = GenerationRegistry(directory)
    assert not config_repository_readers.prepare_separated_format()
    assert not (directory / "generations.json").exists()

    registry.reserve("new", tmp_path / "new", "2.311.2")
    assert not config_repository_readers.prepare_separated_format()
    registry.publish("new", endpoint="new-endpoint", process="new-process")
    assert config_repository_readers.prepare_separated_format()
    assert registry.reserve("old", tmp_path / "old", "2.310.3")["generation"] == "new"


@pytest.mark.parametrize("other", ["empty", "reserved", "exited", "running"])
def test_repository_cutover_keeps_uninitialized_namespaces_from_accepting_legacy(tmp_path, monkeypatch, other):
    standard, elevated = tmp_path / "standard", tmp_path / "elevated"
    monkeypatch.setattr(config_repository_readers, "_registry_directories", lambda: [standard, elevated])
    running = {"new-process", "old-process"} if other == "running" else {"new-process"}
    monkeypatch.setattr(agents_generation_index, "generation_process_alive", lambda process: process in running)
    active, alternate = GenerationRegistry(standard), GenerationRegistry(elevated)
    active.reserve("new", tmp_path / "new", "2.311.2")
    active.publish("new", endpoint="new-endpoint", process="new-process")
    reservation = None if other == "empty" else alternate.reserve("old", tmp_path / "old", "2.310.3")
    if other in {"exited", "running"}:
        alternate.publish("old", endpoint="old-endpoint", process="old-process")

    if other == "running":
        assert not config_repository_readers.prepare_separated_format()
        previous = alternate.current()
        assert previous is not None and previous["generation"] == "old"
        running.remove("old-process")
    assert config_repository_readers.prepare_separated_format()
    current = active.current()
    assert current is not None and current["endpoint"] == "new-endpoint"
    recovered = alternate.reserve("old", tmp_path / "old", "2.310.3")
    assert recovered["generation"] == "new"
    assert "endpoint" not in recovered and recovered["process"] == "" and not recovered["ready"]
    if reservation is not None:
        assert reservation["epoch"] < recovered["epoch"]


def test_repository_format_changes_after_legacy_service_and_writer_finish(tmp_path, monkeypatch):
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    directory = tmp_path / "services" / "agents-server"
    registry = GenerationRegistry(directory)
    running = {"old-process", "new-process"}
    monkeypatch.setattr(service_transport, "process_alive", lambda process: process in running)
    monkeypatch.setattr(agents_generation_index, "generation_process_alive", lambda process: process in running)
    registry.reserve("old", tmp_path / "old", "2.310.3")
    registry.publish("old", endpoint="old-endpoint", process="old-process")
    registry.reserve("new", tmp_path / "new", "2.311.2")
    registry.publish("new", endpoint="new-endpoint", process="new-process")

    assert not config_repository_readers.prepare_separated_format()
    with pytest.raises(ActionableError, match="登録の変更を保留"):
        config_repository_readers.require_compatible_readers()
    running.remove("new-process")
    restored = registry.restore()
    assert restored is not None and restored["generation"] == "old"
    assert not config_repository_readers.prepare_separated_format()

    running.update({"new-restarted", "old-worker"})
    registry.publish("new", endpoint="new-endpoint", process="new-restarted")
    running.remove("old-process")
    (directory / "old.json").write_text(json.dumps({"process": "old-worker"}), encoding="utf-8")
    assert not config_repository_readers.prepare_separated_format()
    running.remove("old-worker")
    assert config_repository_readers.prepare_separated_format()
    assert registry.generations()["old"]["retired"] is True
    assert registry.reserve("old", tmp_path / "old", "2.310.3")["generation"] == "new"
    running.remove("new-restarted")
    restored = registry.restore()
    assert restored is not None and restored["generation"] == "new"
    with pytest.raises(ValueError, match="旧版を受付先"):
        registry.publish("old", endpoint="old-endpoint", process="old-restarted")
