"""rules_context_testの共通試験準備。"""

from __future__ import annotations

import json

import pytest

from agent_toolkit._hooks import rules_context


@pytest.fixture(autouse=True)
def _disable_review_model_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """規範配送の試験はモデルを起動せず、起動要求自体はbatchの結合試験で検証する。"""
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_SESSION_REVIEW_AUTO_START", "false")


def _output(capsys: pytest.CaptureFixture[str]) -> str:
    return json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]


def _session_start_length_report(output: str) -> str:
    """SessionStartの追加本文が上限を超えた場合の失敗本文を組み立てる。

    超過分と文書別の長さを示し、追随すべき対象を失敗本文から確定できるようにする。
    """
    total = len(output)
    overage = total - rules_context.CLAUDE_CODE_OUTPUT_LIMIT
    breakdown = "、".join(
        f"{path.name}={len(path.read_text(encoding='utf-8'))}文字"
        for path in (rules_context.MAIN_RULES_PATH, rules_context.MAIN_RULES_CLAUDE_CODE_PATH)
    )
    return (
        f"SessionStartの追加本文が{total}文字であり、上限{rules_context.CLAUDE_CODE_OUTPUT_LIMIT}文字を"
        f"{overage}文字超過した。文書別の長さ: {breakdown}"
    )
