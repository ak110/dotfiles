"""Codex向けに生成する`AGENTS.md`（`sync_codex_agents.py`の出力）が原本と一致してagent-toolkitの規範を含まず、2層待機の契約を`agent-toolkit:delegation`だけが持つことを検証する。"""

import re

import sync_codex_agents as subject
from _scripts_test_helpers import TWO_LAYER_WAIT_HEADING, markdown_section
from agent_toolkit._testing.normative_contract import read_contract


def test_two_layer_wait_contract_is_owned_by_delegation_skill() -> None:
    plugin = subject.REPO_ROOT / "agent-toolkit"
    reference = read_contract(
        plugin / "skills/delegation/references/codex-runtime.md",
        plugin,
        ("codex-runtime-waiting.md", "agents_serverの二層待機"),
    )
    generated = (subject.REPO_ROOT / subject.TARGET).read_text(encoding="utf-8")
    assert generated == subject.render()
    # 規範の本文は入口のスキルと手順書が持ち、Codexの`AGENTS.md`は個人用の規範だけを運ぶ。
    assert 'source="agent-toolkit"' not in generated and "share/rules/" not in generated
    assert TWO_LAYER_WAIT_HEADING not in generated
    section = markdown_section(reference, TWO_LAYER_WAIT_HEADING)
    assert {"`functions.exec`", "`atk agents wait`", "`cell_id`", "`functions.wait`"} <= set(re.findall(r"`[^`]+`", section))
    assert "タスク固有timeoutを渡さず" in section
    assert "対象が未終了なら" in section
    assert "`waiting-and-monitoring-responses.md`「`atk agents wait`の応答の扱い」" in section
    waiting = read_contract(
        plugin / "skills/delegation/references/waiting-and-monitoring.md",
        plugin,
        ("waiting-and-monitoring-responses.md", "応答の扱い"),
    )
    response_section = markdown_section(waiting, "## `atk agents wait`の応答の扱い")
    assert "前景のCLIが本文を返した後の逐次待機は新しいrunへ進む" in response_section
    assert "配信済みの`foreground-delivered`または`consumed`なら本文を再出力せず" in response_section
    assert "未配送の`published`なら保存済み本文を回収し" in response_section
