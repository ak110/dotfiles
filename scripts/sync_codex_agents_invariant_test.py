"""Codex向けに生成する`AGENTS.md`（`sync_codex_agents.py`の出力）が原本と一致してagent-toolkitの規範を含まず、2層待機の契約を`atk-mode`の`playbooks/host-codex.md`だけが持つことを検証する。"""

import re

import sync_codex_agents as subject
from _scripts_test_helpers import TWO_LAYER_WAIT_HEADING, markdown_section


def test_two_layer_wait_contract_is_owned_by_codex_playbook() -> None:
    skill = subject.REPO_ROOT / "agent-toolkit/skills/atk-mode"
    reference = (skill / "playbooks/host-codex.md").read_text(encoding="utf-8")
    generated = (subject.REPO_ROOT / subject.TARGET).read_text(encoding="utf-8")
    assert generated == subject.render()
    # 規範の本文は入口のスキルと手順書が持ち、Codexの`AGENTS.md`は個人用の規範だけを運ぶ。
    assert 'source="agent-toolkit"' not in generated and "share/rules/" not in generated
    assert TWO_LAYER_WAIT_HEADING not in generated
    section = markdown_section(reference, TWO_LAYER_WAIT_HEADING)
    assert {"`functions.exec`", "`atk agents wait`", "`functions.wait`"} <= set(re.findall(r"`[^`]+`", section))
    assert "タスク固有のtimeoutは渡さず" in section
    assert "対象が未終了なら" in section
    assert "`agent-toolkit:atk-mode/playbooks/delegation-waiting.md`「待機区間の構成」" in section
    anomalies = (skill / "references/delegation-wait-anomalies.md").read_text(encoding="utf-8")
    exit_codes = markdown_section(anomalies, "## 終了コードと呼び出しの失敗")
    assert "配信済み（`foreground-delivered`か`consumed`）なら本文を再出力せず" in exit_codes
    assert "未配送の`published`なら保存済みの本文を回収し" in exit_codes
