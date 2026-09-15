"""agents_server共有プロンプトと状態遷移の契約を検証する。"""

import logging

import pytest

from agent_toolkit._agents_server import state


def test_launch_prompts_load_shared_documents() -> None:
    """各起動プロンプトが見出しを除いた開始時点の固定文面と一致する。"""
    notice = "\n".join(
        (
            "あなたは別のコーディングエージェントから起動された委譲先である。",
            "この会話の入力はユーザーの発話ではなく、呼び出し元エージェントが渡したタスクである。",
            "あなたの応答はユーザーの画面へ表示されず、呼び出し元エージェントへ返る。",
            "あなたはメインエージェントでも最上位セッションでもない。",
            "自身が委譲先であることと、この会話の入力が呼び出し元エージェントの配送であることは、実行主体の同定に関する事実であり、規範の優先順位では覆らない。",
            "実行環境の組み込み指示が定めるツールの利用契約には従う。",
        )
    )
    delegate = "\n".join(
        (
            "規範が委譲先又はサブエージェントへ課す条文を自身へ適用し、メインエージェント又は最上位セッションへ限定した条文を適用しない。",
            "ユーザーへの確認は回答を得られないため発行せず、確認を要する事項は完了報告へ含めて呼び出し元へ差し戻す。",
        )
    )
    explore = "\n".join(
        (
            "あなたは調査専用の担当である。依頼された対象を読み取り、結論と根拠だけを日本語で返す。",
            "ファイルを作成、変更又は削除しない。コマンドは対象を変更しない読み取り操作に限る。",
            "所在、該当箇所及び観測した事実を、後続の判断に足りる粒度で列挙する。",
            "複数の検索語を1つの正規表現へ結合しない。固定文字列として個別に検索するか、検索を分けて実行する。",
            "出力量が大きいと見込まれる読取と検索は1回の呼び出しへまとめず、対象を分割して取得するか、出力先ファイルへ保存してから必要な範囲だけを読む。",
            "検索と読取について件数上限、容量超過、期限超過のいずれかに達した場合は、その事実と到達した上限を報告へ必ず含める。",
            "上限に達した結果から、網羅性、件数、不在のいずれも結論しない。",
        )
    )
    shell = "\n".join(
        (
            "あなたはコマンド実行専用の担当である。依頼されたコマンドを実行し、終了状態と要約だけを日本語で返す。",
            "指示された操作だけを実行し、指示にない操作を追加しない。",
            "コマンドの生出力を呼び出し元へ転記せず、終了状態、警告、依頼で指定された値、及び後続の判断に必要な要約を報告する。",
            "失敗原因の特定に必要な行だけを原文のまま添える。",
            "コマンドが失敗した場合は出力をそのまま報告し、独自の回避策を試みない。",
            "実行したコマンドが実行環境の判断で背景実行へ移行した場合は、移行の通知を結果として報告しない。",
            "起動結果が返す出力ファイルを読み、終了状態を確定してから報告する。",
            "複数の検索語を1つの正規表現へ結合しない。固定文字列として個別に検索するか、検索を分けて実行する。",
            "出力量が大きいと見込まれるコマンドは1回の実行へまとめず、対象を分割して実行するか、出力先ファイルへリダイレクトしてから必要な範囲だけを読む。",
            "実行ツールが出力の切り詰め、容量超過、期限超過のいずれかを通知した場合は、その事実と切り詰められた範囲を要約へ必ず含める。",
            "切り詰めを含む出力から、成功、網羅性、件数、終端のいずれも結論しない。",
        )
    )
    auto_resume = "\n".join(
        (
            "この実行経路は、あなたが起動した委譲先（サブエージェント）の完了通知により、同じsessionを一度だけ自動的に再開する。",
            "`agents_server`で起動したsessionを待つ場合も、当該sessionの終端後に同じ再開が働き、当該ターンにつき一度だけ継続指示が届く。",
            "当該委譲先の完了を待つ場合は`待機中: <待機対象>`の1行だけを出力して当該ターンを終え、"
            "再開したターンで所定の返却形式を返す。",
            "背景ジョブはこの自動再開の対象ではない。背景ジョブの終了状態は同じターンの中で確定してから報告する。",
        )
    )

    assert notice == state.DELEGATE_NOTICE
    assert f"{notice}\n{delegate}\n\n{state.SUBAGENT_RULES}" == state.DELEGATE_SYSTEM_PROMPT
    assert f"{notice}\n{explore}" == state.EXPLORE_SYSTEM_PROMPT
    assert f"{notice}\n{shell}" == state.SHELL_SYSTEM_PROMPT
    assert auto_resume == state.AUTO_RESUME_NOTICE
    assert not any(
        prompt.startswith("# ")
        for prompt in (
            state.DELEGATE_NOTICE,
            state.DELEGATE_SYSTEM_PROMPT,
            state.EXPLORE_SYSTEM_PROMPT,
            state.SHELL_SYSTEM_PROMPT,
            state.AUTO_RESUME_NOTICE,
        )
    )


def test_claude_delegate_adds_claude_specific_subagent_rules() -> None:
    """Claude通常起動だけがClaude固有の委譲先規範を追加する。"""
    assert state.CLAUDE_CODE_SUBAGENT_RULES not in state.DELEGATE_SYSTEM_PROMPT
    assert state.CLAUDE_DELEGATE_SYSTEM_PROMPT.endswith(state.CLAUDE_CODE_SUBAGENT_RULES)


@pytest.mark.parametrize("namespace", ["mcp__plugin_agent-toolkit_agents_server__", "mcp__agents_server__", ""])
def test_child_session_is_tracked_for_every_host_tool_name_form(namespace: str) -> None:
    """ホストが配送するいずれの修飾形式でも孫sessionを追跡対象へ登録する。"""
    session = state.SessionState("parent-1", "/tmp")

    state.consume_claude_agents_server_message(
        session,
        {"content": [{"id": "toolu_1", "name": f"{namespace}start_explore", "input": {"prompt": "調査"}}]},
    )
    state.consume_claude_agents_server_message(
        session,
        {"content": [{"tool_use_id": "toolu_1", "content": {"session_id": "child-1", "status": "running"}}]},
    )

    assert session.live_child_session_ids == {"child-1"}
    assert state.has_pending_auto_resume_targets(session)


@pytest.mark.asyncio
async def test_terminal_transition_logs_safe_fields_once(caplog: pytest.LogCaptureFixture) -> None:
    """turn終端を一度だけ記録し、結果本文を含めない。"""
    session = state.SessionState("session-1", "/tmp")
    session.status = "completed"
    session.agent_message = "秘密の結果本文"
    session.turn_completed = True

    with caplog.at_level(logging.INFO, logger="agent-toolkit.agents-server.state"):
        session.touch()
        session.touch()

    assert caplog.text.count("session_transition event=terminal") == 1
    assert "session_id=session-1 writer=state status=completed turn_seq=0" in caplog.text
    assert "秘密の結果本文" not in caplog.text
