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


def test_pending_tool_uses_track_tools_outside_agents_server() -> None:
    """`agents_server`以外のツール呼び出しも未完了として記録し、結果の到着で除く。"""
    session = state.SessionState("parent-1", "/tmp")

    state.consume_claude_agents_server_message(
        session,
        {"content": [{"id": "toolu_1", "name": "Bash", "input": {"command": "secret --token=abc"}}]},
    )

    assert [entry["name"] for entry in session.active_tool_uses()] == ["Bash"]

    state.consume_claude_agents_server_message(
        session,
        {"content": [{"tool_use_id": "toolu_1", "content": "done"}]},
    )

    assert not session.active_tool_uses()


def test_active_tool_uses_include_input_detail_and_sort_by_start() -> None:
    """未完了のツール呼び出しは開始時刻の昇順で返し、入力の1行要約を`detail`として載せる。"""
    session = state.SessionState("parent-1", "/tmp")
    session.pending_tool_uses.update(
        {
            "toolu_2": ("Read", "2026-09-15T00:00:02+00:00", "file_path=/tmp/a.py"),
            "toolu_1": ("Bash", "2026-09-15T00:00:01+00:00", "command=rg -n foo"),
            "toolu_3": ("TodoWrite", "2026-09-15T00:00:03+00:00", ""),
        }
    )

    assert session.active_tool_uses() == [
        {"name": "Bash", "started_at": "2026-09-15T00:00:01+00:00", "detail": "command=rg -n foo"},
        {"name": "Read", "started_at": "2026-09-15T00:00:02+00:00", "detail": "file_path=/tmp/a.py"},
        {"name": "TodoWrite", "started_at": "2026-09-15T00:00:03+00:00"},
    ]


def test_current_item_is_projected_as_active_tool_use() -> None:
    """Codex backendの進行中itemを、`type`・`id`・開始時刻と入力の要約として返す。"""
    session = state.SessionState("thread-1", "/tmp", engine="codex")

    session.record_current_item_start({"type": "commandExecution", "id": "item-1", "command": "rg -n foo"})
    entries = session.active_tool_uses()

    assert len(entries) == 1
    assert entries[0]["type"] == "commandExecution"
    assert entries[0]["id"] == "item-1"
    # `type`と`id`は別の項目として既に返すため、要約からは除く。
    assert entries[0]["detail"] == "command=rg -n foo"
    assert entries[0]["started_at"]
    assert session.last_action == "commandExecution: command=rg -n foo"

    session.record_current_item_start(None)

    assert not session.active_tool_uses()


def test_last_action_prefers_tool_use_issued_after_text() -> None:
    """同じメッセージがテキストとツール呼び出しを持つ場合は、後のツール呼び出しを最後の行動とする。"""
    session = state.SessionState("parent-1", "/tmp")

    session.set_progress("調査を続ける")
    state.consume_claude_agents_server_message(
        session,
        {"content": [{"id": "toolu_1", "name": "Bash", "input": {}}]},
    )

    assert session.last_action == "Bash"


def test_last_action_changes_between_repeated_calls_of_the_same_tool() -> None:
    """同じツール名を繰り返す区間でも、入力が異なれば最後の行動の値が変わる。"""
    session = state.SessionState("parent-1", "/tmp")

    state.consume_claude_agents_server_message(
        session,
        {"content": [{"id": "toolu_1", "name": "Bash", "input": {"command": "git status"}}]},
    )
    first = session.last_action
    state.consume_claude_agents_server_message(
        session,
        {"content": [{"id": "toolu_2", "name": "Bash", "input": {"command": "git diff"}}]},
    )

    assert first == "Bash: command=git status"
    assert session.last_action == "Bash: command=git diff"


def test_action_detail_collapses_whitespace_and_truncates_at_limit() -> None:
    """入力の要約は改行と連続空白を1個へ畳み、上限を超えた分を省略記号で切り詰める。"""
    session = state.SessionState("parent-1", "/tmp")

    session.record_tool_use_start("toolu_1", "Write", {"path": "a.py", "content": "x\r\ny  z", "line": 12})

    assert session.last_action == "Write: path=a.py content=x y z line=12"

    session.record_tool_use_start("toolu_2", "Write", {"content": "y" * 300})
    detail = session.active_tool_uses()[-1]["detail"]

    assert detail == f"content={'y' * 192}…"


def test_activity_projection_decides_stall_by_activity_time() -> None:
    """停滞の印は活動時刻からの経過だけで決め、テキスト出力の停止では付けない。"""
    text_silent = state.activity_projection(
        updated_at="2099-01-01T00:00:00+00:00",
        output_updated_at="2000-01-01T00:00:00+00:00",
        started_at="2000-01-01T00:00:00+00:00",
    )
    inactive = state.activity_projection(
        updated_at="2000-01-01T00:00:00+00:00",
        output_updated_at="2099-01-01T00:00:00+00:00",
        started_at="2000-01-01T00:00:00+00:00",
    )
    unreadable = state.activity_projection(updated_at=None, output_updated_at=None, started_at="2026-09-15T00:00:00")

    assert text_silent["seconds_since_output"] >= state.STALL_NOTICE_SECONDS
    assert text_silent["seconds_since_activity"] == 0
    assert "stalled" not in text_silent
    assert inactive["stalled"] is True
    assert not unreadable


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
