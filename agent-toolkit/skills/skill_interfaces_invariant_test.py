"""スキルの表示名がディレクトリ名と一致することを確かめる。"""

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.repo_invariant


def test_openai_interface_display_name_matches_skill_directory() -> None:
    """Codexの入力補助へスキルのディレクトリ名を表示する。"""
    manifests = sorted(Path(__file__).resolve().parent.glob("*/agents/openai.yaml"))

    assert manifests, "対象のagents/openai.yamlが存在しない"
    for manifest in manifests:
        data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        assert data["interface"]["display_name"] == manifest.parents[1].name, (
            f"Codexの入力補助はinterface.display_nameを候補名として表示するため、スキルのディレクトリ名と一致させる: {manifest}"
        )
