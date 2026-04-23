"""claude_session_exportモジュールのテスト。"""

import json
import logging

import pytest

from pytools.claude_session_export import (
    RenderOptions,
    encode_project_path,
    export_sessions,
    iter_turns,
    render_session,
)


class TestEncodeProjectPath:
    """encode_project_pathのテスト。"""

    @pytest.mark.parametrize(
        ("cwd", "expected"),
        [
            ("/home/aki/dotfiles", "-home-aki-dotfiles"),
            ("/home/aki/dotfiles/.claude/worktrees/exporter", "-home-aki-dotfiles--claude-worktrees-exporter"),
            ("/tmp/test", "-tmp-test"),
            ("/", "-"),
        ],
    )
    def test_encoding(self, cwd: str, expected: str) -> None:
        assert encode_project_path(cwd) == expected


def _make_record(
    *,
    type_: str = "user",
    uuid: str = "u1",
    parent_uuid: str = "",
    timestamp: str = "2026-01-01T00:00:00Z",
    session_id: str = "sess1",
    is_sidechain: bool = False,
    is_meta: bool = False,
    message: dict | None = None,
    cwd: str = "/tmp/test",
    git_branch: str = "main",
    **extra: object,
) -> dict:
    """テスト用レコードを生成するヘルパー。"""
    r: dict = {
        "type": type_,
        "uuid": uuid,
        "parentUuid": parent_uuid,
        "timestamp": timestamp,
        "sessionId": session_id,
        "isSidechain": is_sidechain,
        "cwd": cwd,
        "gitBranch": git_branch,
    }
    if is_meta:
        r["isMeta"] = True
    if message is not None:
        r["message"] = message
    r.update(extra)
    return r


class TestIterTurns:
    """iter_turnsのテスト。"""

    def test_basic_conversation(self) -> None:
        """人間→アシスタントの基本的な会話をテストする。"""
        records = [
            _make_record(
                uuid="u1",
                timestamp="2026-01-01T00:00:01Z",
                message={"role": "user", "content": "こんにちは"},
            ),
            _make_record(
                type_="assistant",
                uuid="a1",
                parent_uuid="u1",
                timestamp="2026-01-01T00:00:02Z",
                message={
                    "role": "assistant",
                    "id": "msg1",
                    "content": [{"type": "text", "text": "はい、こんにちは"}],
                },
            ),
        ]
        turns = list(iter_turns(records))
        assert len(turns) == 2
        assert turns[0].role == "human"
        assert turns[0].content_blocks[0]["text"] == "こんにちは"
        assert turns[1].role == "assistant"
        assert turns[1].content_blocks[0]["text"] == "はい、こんにちは"

    def test_sidechain_excluded(self) -> None:
        """isSidechain: trueのレコードが除外されることをテストする。"""
        records = [
            _make_record(uuid="u1", timestamp="2026-01-01T00:00:01Z", message={"role": "user", "content": "質問"}),
            _make_record(
                type_="assistant",
                uuid="a1",
                timestamp="2026-01-01T00:00:02Z",
                is_sidechain=True,
                message={"role": "assistant", "id": "msg_side", "content": [{"type": "text", "text": "中断された応答"}]},
            ),
            _make_record(
                type_="assistant",
                uuid="a2",
                timestamp="2026-01-01T00:00:03Z",
                message={"role": "assistant", "id": "msg2", "content": [{"type": "text", "text": "正しい応答"}]},
            ),
        ]
        turns = list(iter_turns(records))
        assert len(turns) == 2
        assert turns[1].content_blocks[0]["text"] == "正しい応答"

    def test_sidechain_kept_for_subagent(self) -> None:
        """サブエージェントではisSidechainフィルターが適用されないことをテストする。"""
        records = [
            _make_record(
                uuid="u1",
                timestamp="2026-01-01T00:00:01Z",
                is_sidechain=True,
                message={"role": "user", "content": "プロンプト"},
            ),
            _make_record(
                type_="assistant",
                uuid="a1",
                timestamp="2026-01-01T00:00:02Z",
                is_sidechain=True,
                message={"role": "assistant", "id": "msg1", "content": [{"type": "text", "text": "応答"}]},
            ),
        ]
        turns = list(iter_turns(records, is_subagent=True))
        assert len(turns) == 2

    def test_meta_excluded(self) -> None:
        """isMeta: trueのuserレコードが除外されることをテストする。"""
        records = [
            _make_record(
                uuid="u_meta",
                timestamp="2026-01-01T00:00:01Z",
                is_meta=True,
                message={"role": "user", "content": "<system>スキル注入</system>"},
            ),
            _make_record(uuid="u1", timestamp="2026-01-01T00:00:02Z", message={"role": "user", "content": "実際の質問"}),
        ]
        turns = list(iter_turns(records))
        assert len(turns) == 1
        assert turns[0].content_blocks[0]["text"] == "実際の質問"

    def test_tool_use_and_result(self) -> None:
        """ツール呼び出しとツール結果の紐付けをテストする。"""
        records = [
            _make_record(uuid="u1", timestamp="2026-01-01T00:00:01Z", message={"role": "user", "content": "ファイルを読んで"}),
            _make_record(
                type_="assistant",
                uuid="a1",
                timestamp="2026-01-01T00:00:02Z",
                message={
                    "role": "assistant",
                    "id": "msg1",
                    "content": [
                        {"type": "text", "text": "読みます"},
                        {"type": "tool_use", "id": "tool1", "name": "Read", "input": {"file_path": "/tmp/test.py"}},
                    ],
                },
            ),
            _make_record(
                uuid="u2",
                parent_uuid="a1",
                timestamp="2026-01-01T00:00:03Z",
                message={
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "tool1", "content": [{"type": "text", "text": "ファイル内容"}]},
                    ],
                },
            ),
        ]
        turns = list(iter_turns(records))
        assert len(turns) == 2  # human + assistant（tool_resultのuserはスキップ）
        assistant_turn = turns[1]
        assert "tool1" in assistant_turn.tool_results
        assert assistant_turn.tool_results["tool1"][0]["text"] == "ファイル内容"

    def test_tool_result_string_content(self) -> None:
        """tool_resultのcontentが文字列の場合にリスト形式に正規化されることをテストする。"""
        records = [
            _make_record(uuid="u1", timestamp="2026-01-01T00:00:01Z", message={"role": "user", "content": "スキル実行"}),
            _make_record(
                type_="assistant",
                uuid="a1",
                timestamp="2026-01-01T00:00:02Z",
                message={
                    "role": "assistant",
                    "id": "msg1",
                    "content": [
                        {"type": "tool_use", "id": "tool1", "name": "Skill", "input": {"skill": "test"}},
                    ],
                },
            ),
            _make_record(
                uuid="u2",
                timestamp="2026-01-01T00:00:03Z",
                message={
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "tool1", "content": "Launching skill: test"},
                    ],
                },
            ),
        ]
        turns = list(iter_turns(records))
        assistant_turn = turns[1]
        assert "tool1" in assistant_turn.tool_results
        assert assistant_turn.tool_results["tool1"][0]["text"] == "Launching skill: test"

    def test_assistant_grouping_by_message_id(self) -> None:
        """同一message.idのassistantレコードが1ターンにグループ化されることをテストする。"""
        records = [
            _make_record(uuid="u1", timestamp="2026-01-01T00:00:01Z", message={"role": "user", "content": "テスト"}),
            # 同一message.idで分割されたassistant（ストリーミングチャンク）
            _make_record(
                type_="assistant",
                uuid="a1",
                timestamp="2026-01-01T00:00:02Z",
                message={
                    "role": "assistant",
                    "id": "msg1",
                    "content": [{"type": "thinking", "thinking": "考え中"}],
                },
            ),
            _make_record(
                type_="assistant",
                uuid="a2",
                parent_uuid="a1",
                timestamp="2026-01-01T00:00:03Z",
                message={
                    "role": "assistant",
                    "id": "msg1",
                    "content": [{"type": "text", "text": "結果"}],
                },
            ),
        ]
        turns = list(iter_turns(records))
        assert len(turns) == 2  # human + assistant（1ターン）
        assistant_turn = turns[1]
        assert len(assistant_turn.content_blocks) == 2
        assert assistant_turn.content_blocks[0]["type"] == "thinking"
        assert assistant_turn.content_blocks[1]["type"] == "text"

    def test_queue_operation_enqueue(self) -> None:
        """queue-operation（enqueue）が人間ターンとして出力されることをテストする。"""
        records = [
            _make_record(uuid="u1", timestamp="2026-01-01T00:00:01Z", message={"role": "user", "content": "最初の質問"}),
            {
                "type": "queue-operation",
                "operation": "enqueue",
                "timestamp": "2026-01-01T00:00:05Z",
                "sessionId": "sess1",
                "content": "追加の指示",
            },
        ]
        turns = list(iter_turns(records))
        assert len(turns) == 2
        assert turns[1].role == "human"
        assert turns[1].content_blocks[0]["text"] == "追加の指示"

    def test_queue_operation_system_tag_skipped(self) -> None:
        """queue-operationのシステムタグ付きcontentがスキップされることをテストする。"""
        records = [
            {
                "type": "queue-operation",
                "operation": "enqueue",
                "timestamp": "2026-01-01T00:00:05Z",
                "sessionId": "sess1",
                "content": "<task-notification>\n<task-id>abc</task-id>\n</task-notification>",
            },
        ]
        turns = list(iter_turns(records))
        assert len(turns) == 0

    def test_non_accepted_types_filtered(self) -> None:
        """attachment, progress, systemなどのタイプが除外されることをテストする。"""
        records = [
            _make_record(type_="attachment", uuid="att1", timestamp="2026-01-01T00:00:01Z"),
            _make_record(type_="progress", uuid="p1", timestamp="2026-01-01T00:00:02Z"),
            _make_record(type_="system", uuid="s1", timestamp="2026-01-01T00:00:03Z"),
            _make_record(type_="permission-mode", uuid="pm1", timestamp="2026-01-01T00:00:04Z"),
            _make_record(uuid="u1", timestamp="2026-01-01T00:00:05Z", message={"role": "user", "content": "テスト"}),
        ]
        turns = list(iter_turns(records))
        assert len(turns) == 1
        assert turns[0].role == "human"

    def test_user_content_list_format(self) -> None:
        """userのcontent がリスト形式の場合のテスト。"""
        records = [
            _make_record(
                uuid="u1",
                timestamp="2026-01-01T00:00:01Z",
                message={
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "テキスト1"},
                        {"type": "text", "text": "テキスト2"},
                    ],
                },
            ),
        ]
        turns = list(iter_turns(records))
        assert len(turns) == 1
        assert len(turns[0].content_blocks) == 2


class TestRenderSession:
    """render_sessionのテスト。"""

    def test_basic_output_structure(self) -> None:
        """基本的な出力構造をテストする。"""
        records = [
            _make_record(
                uuid="u1",
                timestamp="2026-01-01T00:00:01Z",
                session_id="test-session-123",
                message={"role": "user", "content": "こんにちは"},
                slug="test-slug",
            ),
            _make_record(
                type_="assistant",
                uuid="a1",
                timestamp="2026-01-01T00:00:02Z",
                session_id="test-session-123",
                message={
                    "role": "assistant",
                    "id": "msg1",
                    "content": [{"type": "text", "text": "応答テキスト"}],
                },
            ),
        ]
        md = render_session(records)
        assert "# Session: test-slug" in md
        assert "## Human" in md
        assert "こんにちは" in md
        assert "## Assistant" in md
        assert "応答テキスト" in md
        assert "`test-session-123`" in md

    def test_custom_title(self) -> None:
        """custom-titleがセッションタイトルに使われることをテストする。"""
        records = [
            _make_record(uuid="u1", timestamp="2026-01-01T00:00:01Z", message={"role": "user", "content": "テスト"}),
            {
                "type": "custom-title",
                "customTitle": "カスタムタイトル",
                "sessionId": "sess1",
                "timestamp": "2026-01-01T00:00:02Z",
            },
        ]
        md = render_session(records)
        assert "# Session: カスタムタイトル" in md

    def test_thinking_excluded_by_default(self) -> None:
        """thinkingブロックが既定で除外されることをテストする。"""
        records = [
            _make_record(uuid="u1", timestamp="2026-01-01T00:00:01Z", message={"role": "user", "content": "テスト"}),
            _make_record(
                type_="assistant",
                uuid="a1",
                timestamp="2026-01-01T00:00:02Z",
                message={
                    "role": "assistant",
                    "id": "msg1",
                    "content": [
                        {"type": "thinking", "thinking": "内部思考"},
                        {"type": "text", "text": "応答"},
                    ],
                },
            ),
        ]
        md = render_session(records)
        assert "内部思考" not in md
        assert "応答" in md

    def test_thinking_included_with_option(self) -> None:
        """include_thinkingオプションでthinkingが含まれることをテストする。"""
        records = [
            _make_record(uuid="u1", timestamp="2026-01-01T00:00:01Z", message={"role": "user", "content": "テスト"}),
            _make_record(
                type_="assistant",
                uuid="a1",
                timestamp="2026-01-01T00:00:02Z",
                message={
                    "role": "assistant",
                    "id": "msg1",
                    "content": [
                        {"type": "thinking", "thinking": "内部思考テキスト"},
                        {"type": "text", "text": "応答"},
                    ],
                },
            ),
        ]
        md = render_session(records, RenderOptions(include_thinking=True))
        assert "内部思考テキスト" in md
        assert "<summary>Thinking</summary>" in md

    def test_tool_details(self) -> None:
        """ツール呼び出しのdetails表示をテストする。"""
        records = [
            _make_record(uuid="u1", timestamp="2026-01-01T00:00:01Z", message={"role": "user", "content": "テスト"}),
            _make_record(
                type_="assistant",
                uuid="a1",
                timestamp="2026-01-01T00:00:02Z",
                message={
                    "role": "assistant",
                    "id": "msg1",
                    "content": [
                        {"type": "tool_use", "id": "tool1", "name": "Bash", "input": {"command": "ls -la"}},
                    ],
                },
            ),
            _make_record(
                uuid="u2",
                timestamp="2026-01-01T00:00:03Z",
                message={
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "tool1", "content": [{"type": "text", "text": "file1\nfile2"}]},
                    ],
                },
            ),
        ]
        md = render_session(records)
        assert "<details>" in md
        assert "Bash — `ls -la`" in md
        assert "file1\nfile2" in md

    def test_no_tool_details(self) -> None:
        """no_tool_detailsオプションで簡略表示になることをテストする。"""
        records = [
            _make_record(uuid="u1", timestamp="2026-01-01T00:00:01Z", message={"role": "user", "content": "テスト"}),
            _make_record(
                type_="assistant",
                uuid="a1",
                timestamp="2026-01-01T00:00:02Z",
                message={
                    "role": "assistant",
                    "id": "msg1",
                    "content": [
                        {"type": "tool_use", "id": "tool1", "name": "Read", "input": {"file_path": "/tmp/test.py"}},
                    ],
                },
            ),
        ]
        md = render_session(records, RenderOptions(tool_details=False))
        assert "<details>" not in md
        assert "> Tool: Read" in md

    def test_metadata_table(self) -> None:
        """メタデータテーブルの出力をテストする。"""
        records = [
            _make_record(
                uuid="u1",
                timestamp="2026-01-01T00:00:01Z",
                session_id="sess-abc",
                cwd="/home/test/project",
                git_branch="feature/test",
                message={"role": "user", "content": "テスト"},
            ),
        ]
        md = render_session(records)
        assert "| セッションID | `sess-abc` |" in md
        assert "| プロジェクト | `/home/test/project` |" in md
        assert "| ブランチ | `feature/test` |" in md


class TestExportSessions:
    """export_sessionsのテスト。"""

    def test_output_filename_uses_session_start_timestamp(self, tmp_path) -> None:
        """出力ファイル名にセッション開始日時が使われることをテストする。"""
        session_path = tmp_path / "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl"
        records = [
            _make_record(
                uuid="u1",
                timestamp="2026-01-01T00:00:02Z",
                session_id="sess-abc",
                message={"role": "user", "content": "後の発言"},
            ),
            _make_record(
                type_="assistant",
                uuid="a1",
                timestamp="2026-01-01T00:00:01Z",
                session_id="sess-abc",
                message={
                    "role": "assistant",
                    "id": "msg1",
                    "content": [{"type": "text", "text": "最初の応答"}],
                },
            ),
        ]
        session_path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records), encoding="utf-8")

        output_dir = tmp_path / "out"
        export_sessions([session_path], output_dir, RenderOptions())

        assert (output_dir / "20260101_000001.md").exists()
        assert not (output_dir / "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.md").exists()

    def test_output_filename_collision_warns(self, tmp_path, caplog) -> None:
        """出力ファイル名の衝突時に警告することをテストする。"""
        session_path = tmp_path / "session.jsonl"
        records = [
            _make_record(
                uuid="u1",
                timestamp="2026-01-01T00:00:01Z",
                session_id="sess-abc",
                message={"role": "user", "content": "テスト"},
            ),
        ]
        session_path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records), encoding="utf-8")

        output_dir = tmp_path / "out"
        output_dir.mkdir()
        collided_path = output_dir / "20260101_000001.md"
        collided_path.write_text("既存ファイル\n", encoding="utf-8")

        with caplog.at_level(logging.WARNING):
            export_sessions([session_path], output_dir, RenderOptions())

        assert "出力先ファイルが既に存在するため上書きする" in caplog.text
        assert "Session: sess-abc" in collided_path.read_text(encoding="utf-8")
