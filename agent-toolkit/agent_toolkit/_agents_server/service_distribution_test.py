import json
import pathlib
import shutil

import pytest

from agent_toolkit._agents_server import service_distribution, service_transport
from agent_toolkit._common import runtime_environment, state_paths


@pytest.mark.parametrize("changed", ["agent_toolkit/service.py", "scripts/launch.py", "skills/role.subagent.md"])
def test_only_published_source_and_distribution_contents_use_normal_service(tmp_path, monkeypatch, changed):
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path / "state")
    normal = tmp_path / "normal"
    paths = ["agent_toolkit/service.py", "scripts/launch.py", "skills/role.subagent.md"]
    for name in paths:
        path = normal / name
        path.parent.mkdir(parents=True)
        path.write_text(name, encoding="utf-8")
    caller = runtime_environment.CallerContext({}, tmp_path)
    base = service_transport.namespace(caller)

    def route(root: pathlib.Path, context=caller) -> pathlib.Path:
        return service_transport.namespace(service_transport.plugin_caller(context, root))

    assert route(normal) != base
    assert not base.exists()
    base.mkdir(parents=True)
    service_distribution.publish(base, service_distribution.identity(normal))
    original = (base / "published-distributions.json").read_bytes()
    assert route(normal) == base
    development = tmp_path / "development"
    shutil.copytree(normal, development)
    assert route(development) != base
    held = tmp_path / "copy" / "normal"
    shutil.copytree(normal, held)
    (held.parent / ".agent-toolkit-plugin-root.json").write_text(
        json.dumps({"source": str(normal), "root_name": "normal"}), encoding="utf-8"
    )
    assert route(held) == base
    (held / changed).write_text("開発中の内容", encoding="utf-8")
    assert route(held) != base
    (normal / changed).write_text("開発中の内容", encoding="utf-8")
    assert route(normal) == route(held)
    observation = runtime_environment.CallerContext({service_distribution.SCOPE_ENV: "verification"}, tmp_path)
    assert route(normal, observation) == route(development, observation) != base
    assert (base / "published-distributions.json").read_bytes() == original
    assert not (base / "generations.json").exists()
