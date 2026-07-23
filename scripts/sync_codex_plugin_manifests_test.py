"""sync_codex_plugin_manifestsのテスト。"""

import json
from pathlib import Path
from typing import Any

import pytest

from pytools._internal import claude_common
from scripts import sync_codex_plugin_manifests as subject


@pytest.fixture(name="manifest_root")
def manifest_root_fixture(tmp_path: Path) -> Path:
    """最小正本fixtureを作成する。"""
    plugin = {
        "name": "agent-toolkit",
        "version": "1.2.3",
        "description": "desc",
        "author": {"name": "aki"},
        "homepage": "h",
        "repository": "r",
        "license": "MIT",
        "keywords": ["k"],
    }
    marketplace = {"name": "ak110-dotfiles", "plugins": [{**plugin, "source": "./agent-toolkit"}]}
    fixtures: tuple[tuple[Path, dict[str, Any]], ...] = (
        (subject.PLUGIN_SOURCE, plugin),
        (subject.MARKETPLACE_SOURCE, marketplace),
        (subject.HOOKS_SOURCE, {"hooks": {}}),
    )
    for path, value in fixtures:
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value), encoding="utf-8")
    return tmp_path


def test_sync_and_check_are_deterministic(manifest_root: Path) -> None:
    assert subject.sync(manifest_root) is True
    assert subject.sync(manifest_root) is False
    assert subject.sync(manifest_root, check=True) is False
    generated = json.loads((manifest_root / subject.PLUGIN_TARGET).read_text())
    assert generated["version"] == "1.2.3"
    assert generated["hooks"] == {"hooks": {}}
    assert (manifest_root / subject.PLUGIN_TARGET).read_text().endswith("\n")


def test_check_detects_stale_and_extra_hooks(manifest_root: Path) -> None:
    subject.sync(manifest_root)
    (manifest_root / subject.PLUGIN_TARGET).write_text("{}")
    extra = manifest_root / subject.HOOKS_TARGET
    extra.parent.mkdir(parents=True, exist_ok=True)
    extra.write_text("{}")
    assert subject.sync(manifest_root, check=True) is True


def test_rejects_mismatched_sources(manifest_root: Path) -> None:
    data = json.loads((manifest_root / subject.MARKETPLACE_SOURCE).read_text())
    data["plugins"][0]["version"] = "9.9.9"
    (manifest_root / subject.MARKETPLACE_SOURCE).write_text(json.dumps(data))
    with pytest.raises(ValueError, match="version"):
        subject.sync(manifest_root)


def test_atomic_write_failure_is_reported(manifest_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """共通atomic writeが失敗した場合は同期成功として扱わない。"""
    monkeypatch.setattr(claude_common, "atomic_write_text", lambda *args, **kwargs: False)
    with pytest.raises(OSError, match="書き込みに失敗"):
        subject.sync(manifest_root)
