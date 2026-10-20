"""Claude Code 2.1.304の組織向け設定が省くFunctionイベントの登録を検出する。"""

import pathlib
import re


def test_registered_events_survive_organization_security_defaults() -> None:
    """本体のPASSED_OVER_EVENTS・PASSED_OVER_DOORSとclassic prefixを登録時に避ける。"""
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
        if re.search(r'\bon\("session.append"[^;]*door:\s*"(?:attachment|hook-context|compaction)"', text, re.DOTALL):
            violations.append(f"{path.name}: session.appendの保護対象door")
    assert not violations, violations
