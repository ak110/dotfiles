"""同じ生成元の派生JSONが、別プロセスでも同じバイト列になることを確認する。"""

import os
import pathlib
import shutil
import sys

from agent_toolkit._common import process_tree
from agent_toolkit._testing import git_repository

from pytools._internal import codex_plugin_manifests


def test_generated_manifests_are_identical_across_hash_seeds(host_environ, tmp_path: pathlib.Path):
    """集合を含む設定情報から生成しても、全派生JSONの順序を確定させる。"""
    root = pathlib.Path(__file__).resolve().parents[2]
    input_root = tmp_path / "generation-input"
    # 旧試験と同じ実生成元のmetadata・hooks・MCP・品質原本・スキル集合を隔離して使う。
    sources = [
        codex_plugin_manifests.PLUGIN_SOURCE,
        codex_plugin_manifests.BASICS_PLUGIN_SOURCE,
        codex_plugin_manifests.MARKETPLACE_SOURCE,
        codex_plugin_manifests.HOOKS_SOURCE,
        codex_plugin_manifests.MCP_SOURCE,
        codex_plugin_manifests.QUALITY_SOURCE,
    ]
    for distribution in ("agent-toolkit", "agent-basics"):
        sources.extend(path.relative_to(root) for path in (root / distribution / "skills").glob("*/SKILL.md"))
    for relative in sources:
        target = input_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / relative, target)
    code = (
        "import pathlib,sys; from pytools._internal import codex_plugin_manifests; "
        "codex_plugin_manifests.sync(pathlib.Path(sys.argv[1]))"
    )
    outputs = []
    for seed in ("1", "2"):
        fixture = tmp_path / f"seed-{seed}"
        shutil.copytree(input_root, fixture)
        git_repository.init_repository(fixture)
        environment = host_environ()
        environment.update(
            {
                "PYTHONHASHSEED": seed,
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": str(root) + os.pathsep + str(root / "agent-toolkit"),
            }
        )
        process_tree.run_with_timeout(
            [sys.executable, "-c", code, str(fixture)],
            cwd=fixture,
            env=environment,
            capture_output=True,
            encoding="utf-8",
            timeout=30,
            check=True,
        )
        generated_paths = [
            fixture / relative
            for relative in (
                codex_plugin_manifests.AGENT_PLUGIN_TARGET,
                codex_plugin_manifests.BASICS_AGENT_PLUGIN_TARGET,
                codex_plugin_manifests.PLUGIN_TARGET,
                codex_plugin_manifests.BASICS_PLUGIN_TARGET,
                codex_plugin_manifests.MARKETPLACE_TARGET,
                codex_plugin_manifests.MCP_CODEX_TARGET,
                codex_plugin_manifests.AGENT_MCP_TARGET,
                codex_plugin_manifests.HOOKS_TARGET,
            )
        ]
        generated_paths.extend(fixture.glob("*/skills/*/references/quality-standards.md"))
        generated_paths.extend(path for path in (fixture / "agent-toolkit-codex").rglob("*") if path.is_file())
        generated = {path.relative_to(fixture).as_posix(): path.read_bytes() for path in generated_paths}
        assert "agent-toolkit-codex/.mcp.codex.json" in generated
        assert "agent-toolkit-codex/.codex-plugin/plugin.json" in generated
        assert "agent-toolkit-codex/hooks/hooks.codex.json" in generated
        outputs.append(generated)
    assert outputs[0] == outputs[1]
