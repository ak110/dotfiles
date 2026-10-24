"""`atk info`が配布形式ごとの正規manifestから版数を表示する契約のテスト。"""

import json
import pathlib

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import info


def _write_manifest(root: pathlib.Path, relative: str, content: object) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(content), encoding="utf-8")


def _info_output(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], root: pathlib.Path) -> str:
    monkeypatch.setattr(info, "_PLUGIN_ROOT", root)
    atk.main(["info"])
    return capsys.readouterr().out


def test_info_reports_codex_distribution_version(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """root直下のmanifestを持たないCodex配布物でも、同じrootのmanifestの版数を表示する。"""
    root = tmp_path / "agent-toolkit" / "9.8.7"
    _write_manifest(root, ".codex-plugin/plugin.json", {"name": "agent-toolkit", "version": "9.8.7"})
    _write_manifest(root, ".claude-plugin/plugin.json", {"name": "agent-toolkit", "version": "9.8.7"})

    output = _info_output(monkeypatch, capsys, root)

    assert f"plugin root: {root}" in output
    assert "plugin version (plugin.json): 9.8.7\n" in output


def test_info_reports_source_layout_version(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """root直下のmanifestだけを持つ配置でも版数を表示する。"""
    _write_manifest(tmp_path, "plugin.json", {"name": "agent-toolkit", "version": "1.2.3"})

    output = _info_output(monkeypatch, capsys, tmp_path)

    assert "plugin version (plugin.json): 1.2.3\n" in output


@pytest.mark.parametrize(
    "manifests",
    [
        {},
        {".codex-plugin/plugin.json": {"name": "agent-toolkit"}, "plugin.json": ["not", "object"]},
    ],
    ids=["no-manifest", "no-version"],
)
def test_info_reports_unknown_without_manifest_version(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    manifests: dict[str, object],
) -> None:
    """実行rootのmanifestから版数を得られなければ、作業ツリーや別の導入版を推測せず不明と表示する。"""
    for relative, content in manifests.items():
        _write_manifest(tmp_path, relative, content)

    output = _info_output(monkeypatch, capsys, tmp_path)

    assert "plugin version (plugin.json): 不明\n" in output
