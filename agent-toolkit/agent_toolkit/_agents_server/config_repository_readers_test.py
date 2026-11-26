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
