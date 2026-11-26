import argparse
import json
import pathlib
import subprocess

import pytest

from agent_toolkit._atk.serve import app as serve_app
from agent_toolkit._atk.serve import command, config, state, versions
from agent_toolkit._common.service_code_identity import code_digest


def git(repo: pathlib.Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, encoding="utf-8", capture_output=True, timeout=10
    ).stdout.strip()


def publish(repo: pathlib.Path, version: str) -> str:
    manifest = repo / "agent-toolkit" / ".claude-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({"version": version}), encoding="utf-8")
    module = repo / "agent-toolkit" / "agent_toolkit" / "main.py"
    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_text(f"VERSION = {version!r}\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", version)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture(name="publication")
def _publication(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path, str]:
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-b", "develop")
    first = publish(origin, "1.0.0")
    checkout = tmp_path / "dotfiles"
    git(tmp_path, "clone", str(origin), str(checkout))
    return origin, checkout, first


def test_versions_command_passes_published_oid(monkeypatch, tmp_path):
    def show(repo, *, published, serve_address, agents):
        assert repo == tmp_path
        assert published == "1" * 40
        assert serve_address is None
        assert agents == (True, None)
        return 1

    monkeypatch.setattr(versions, "dotfiles_checkout", lambda _home: tmp_path)
    monkeypatch.setattr(versions, "show_versions", show)
    parser = argparse.ArgumentParser()
    command.build_parser(parser)
    args = parser.parse_args(["versions", "--published", "1" * 40, "--skip-serve"])
    assert command.dispatch(args, home=tmp_path) == 1


@pytest.mark.asyncio
async def test_two_publications_require_runtime_activation(publication, tmp_path):
    origin, checkout, first = publication
    running_agents = versions.published_plugin(checkout, first)
    assert running_agents is not None
    notes = tmp_path / "notes"
    notes.mkdir()

    def start():
        app = serve_app.create_app(notes, config.ServeConfig("127.0.0.1", 28766), state.ServeState(notes))
        serve_app.register_version_routes(
            app, checkout, versions.startup_revision(checkout), agents_version=lambda: (True, running_agents)
        )
        return app.test_client()

    client = start()
    previous = first
    for version in ("1.0.1", "1.0.2"):
        published = publish(origin, version)
        assert versions.latest_published(checkout) == published
        assert versions.published_plugin(checkout, published) is None
        unavailable = await (await client.get("/api/runtime-versions")).get_json()
        assert "agents_server: 版を取得できません" in unavailable[1]
        git(checkout, "pull", "--ff-only")

        assert await (await client.get("/api/runtime-identity")).get_json() == {"revision": previous}
        warnings = await (await client.get("/api/runtime-versions")).get_json()
        assert warnings == [
            f"atk serve: 公開版と不一致（未反映）（稼働版: {previous}、公開版: {published}）",
            versions.VersionCheck("agents_server", running_agents, versions.published_plugin(checkout, published)).message(),
        ]
        assert warnings[1].startswith("agents_server: 公開版と不一致")
        assert version in warnings[1]

        client = start()
        assert await (await client.get("/api/runtime-versions")).get_json() == warnings[1:]
        running_agents = versions.published_plugin(checkout, published)
        assert await (await client.get("/api/runtime-identity")).get_json() == {"revision": published}
        assert not await (await client.get("/api/runtime-versions")).get_json()
        previous = published

    publish(checkout, "1.0.3")
    assert versions.latest_published(checkout) == previous
    assert not await (await client.get("/api/runtime-versions")).get_json()
    git(checkout, "remote", "set-url", "origin", str(tmp_path / "missing"))
    warnings = await (await client.get("/api/runtime-versions")).get_json()
    assert len(warnings) == 2
    assert all("版を取得できません" in warning for warning in warnings)


def test_agents_digest_matches_published_modules_and_detects_local_changes(publication):
    _origin, checkout, published = publication
    root = checkout / "agent-toolkit"
    module = root / "agent_toolkit" / "main.py"
    version = "1.0.0"
    generation = code_digest(version, [(module.relative_to(root), module.read_bytes())])
    expected = versions.published_plugin(checkout, published)
    assert expected == f"{version}/{generation}"

    changed_generation = code_digest(version, [(module.relative_to(root), b"locally modified")])
    assert versions.VersionCheck("agents_server", f"{version}/{changed_generation}", expected).status == "mismatch"


def test_startup_revision_does_not_identify_dirty_package(publication):
    _origin, checkout, published = publication
    assert versions.startup_revision(checkout) == published
    (checkout / "agent-toolkit" / "agent_toolkit" / "local.py").touch()
    assert versions.startup_revision(checkout) is None
