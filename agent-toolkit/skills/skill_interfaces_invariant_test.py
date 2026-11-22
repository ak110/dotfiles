from pathlib import Path

import yaml


def test_openai_interface_display_name_matches_skill_directory() -> None:
    """Codexの入力補助へスキルのディレクトリ名を表示する。

    今は`agents/openai.yaml`を置くスキルが無く、置いた時に表示名の不一致を検出する。
    """
    for manifest in sorted(Path(__file__).resolve().parent.glob("*/agents/openai.yaml")):
        data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        assert data["interface"]["display_name"] == manifest.parents[1].name, (
            f"Codexの入力補助はinterface.display_nameを候補名として表示するため、スキルのディレクトリ名と一致させる: {manifest}"
        )
