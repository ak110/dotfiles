"""Claude Code 2.1.304の組織向け設定が省くFunctionイベントの登録を検出する。"""

import pathlib
import re


def test_registered_events_survive_organization_security_defaults() -> None:
    """保護イベントの直接登録を検出する。実matcherの一致判定はTS側の全機能登録試験で検証する。"""
    skipped = {
        "prompt.section",
        "prompt.context",
        "prompt.attachment",
        "prompt.compose",
        "skill.prompt",
        "attribution.text",
        "session.compact",
        "settings.read",
    }
    violations = []
    for path in pathlib.Path(__file__).parent.glob("*.ts*"):
        if ".test." in path.name:
            continue
        text = path.read_text(encoding="utf-8")
        for event in re.findall(r'\bon\("([^"]+)"', text):
            if event.startswith("classic.") or event in skipped:
                violations.append(f"{path.name}: {event}")
    assert not violations, violations
