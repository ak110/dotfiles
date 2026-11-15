"""組織ガードがイベント単位で通知する登録を、理由付きの許可と区別する。"""

import pathlib
import re

_ALLOWED = {
    ("compact_conversation.ts", "session.compact"): (
        "要約省略の登録を維持する要求。組織modでない環境ではガードの省略通知が出る。PreCompactへ短文化を依頼する。"
    ),
    ("compact_conversation.ts", "session.append"): (
        "Claude Code 2.1.325の背景Bash完了通知はsession.receiveへ配送されず、appendのqueue/deliveryで観測した。"
        "doorがdelivery/queueだけでも組織ガードの省略通知が出る。"
    ),
}


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
        "session.append",
        "settings.read",
    }
    violations = []
    for path in pathlib.Path(__file__).parent.glob("*.ts*"):
        if ".test." in path.name:
            continue
        text = path.read_text(encoding="utf-8")
        for event in re.findall(r'\bon\("([^"]+)"', text):
            if (path.name, event) in _ALLOWED:
                continue
            if event.startswith("classic.") or event in skipped:
                violations.append(f"{path.name}: {event}")
    assert not violations, violations
