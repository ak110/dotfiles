"""plugins/agent-toolkit/scripts/stop_advisor.py のテスト。

Stop hook のテスト。transcript 分析と codex resume count による
CLAUDE.md 更新提案の判定を検証する。
"""

import json
import os
import pathlib
import subprocess
import sys
from typing import Any

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "stop_advisor.py"


def _run(
    payload: object,
    *,
    state_dir: pathlib.Path | None = None,
) -> subprocess.CompletedProcess[str]:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    env = os.environ.copy()
    if state_dir is not None:
        env["TMPDIR"] = str(state_dir)
        env["TEMP"] = str(state_dir)
        env["TMP"] = str(state_dir)
    return subprocess.run(
        [sys.executable, str(_SCRIPT)],
        input=text,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _parse_decision(result: subprocess.CompletedProcess[str]) -> dict:
    return json.loads(result.stdout)


def _write_state(state_dir: pathlib.Path, session_id: str, state: dict) -> None:
    path = state_dir / f"claude-agent-toolkit-{session_id}.json"
    path.write_text(json.dumps(state), encoding="utf-8")


def _write_transcript(tmp_path: pathlib.Path, user_texts: str | list[str]) -> pathlib.Path:
    """ユーザー発話を JSONL 形式の transcript として書き出す。

    文字列 1 つを渡すと user turn 1 つとして、リストを渡すと複数の user turn として書き出す。
    """
    if isinstance(user_texts, str):
        user_texts = [user_texts]
    lines = [
        json.dumps({"type": "user", "message": {"role": "user", "content": text}}, ensure_ascii=False) for text in user_texts
    ]
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return transcript


def _write_raw_transcript(tmp_path: pathlib.Path, lines: list[str]) -> pathlib.Path:
    """任意の JSONL 行を transcript として書き出す (異種エントリを含む検証用)。"""
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return transcript


class TestKeywordDetection:
    """修正キーワードによる発火判定。"""

    def test_below_threshold_approves(self, tmp_path: pathlib.Path):
        # 2 keywords (threshold is 3)
        transcript = _write_transcript(tmp_path, "user: \u9055\u3046\u3001\u305d\u308c\u306f\u9593\u9055\u3044\u3060")
        result = _run(
            {"session_id": "kw-low", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"

    def test_at_threshold_blocks(self, tmp_path: pathlib.Path):
        # 3 keywords
        transcript = _write_transcript(
            tmp_path,
            "user: \u9055\u3046\u3001\u305d\u308c\u306f\u9593\u9055\u3044\u3060\u3002\u3084\u308a\u76f4\u3057\u3066",
        )
        result = _run(
            {"session_id": "kw-hit", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        decision = _parse_decision(result)
        assert decision["decision"] == "block"
        assert "reason" in decision
        assert "correction" in decision["reason"].lower()
        # LLM 宛てメッセージ規約: プレフィックスとサフィックスが付与されていること。
        assert "[auto-generated: agent-toolkit/stop_advisor]" in decision["reason"]
        assert "Auto-generated hook notice" in decision["reason"]

    def test_system_reminder_not_counted(self, tmp_path: pathlib.Path):
        """user turn に注入される system-reminder タグ内の語は集計対象外。

        CLAUDE.md やルール本文が system-reminder 経由で注入された際の false positive を防ぐ。
        """
        reminder = (
            "<system-reminder>\u9055\u3046 \u9593\u9055\u3044 \u3084\u308a\u76f4\u3057 "
            "\u623b\u3057\u3066 \u3058\u3083\u306a\u304f</system-reminder>"
        )
        transcript = _write_transcript(tmp_path, reminder)
        result = _run(
            {"session_id": "kw-reminder", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"

    def test_assistant_messages_not_counted(self, tmp_path: pathlib.Path):
        """assistant turn のテキストは集計対象外。"""
        assistant_entries = [
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "\u9055\u3046 \u9593\u9055\u3044 \u3084\u308a\u76f4\u3057"}],
                    },
                },
                ensure_ascii=False,
            ),
        ]
        transcript = _write_raw_transcript(tmp_path, assistant_entries)
        result = _run(
            {"session_id": "kw-assistant", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"

    def test_tool_result_not_counted(self, tmp_path: pathlib.Path):
        """user turn に含まれる tool_result ブロックは集計対象外。

        ツール出力 (読み込んだファイル本文など) が user role で echo されても拾わない。
        """
        entries = [
            json.dumps(
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "content": "\u9055\u3046 \u9593\u9055\u3044 \u3084\u308a\u76f4\u3057 \u623b\u3057\u3066",
                            },
                        ],
                    },
                },
                ensure_ascii=False,
            ),
        ]
        transcript = _write_raw_transcript(tmp_path, entries)
        result = _run(
            {"session_id": "kw-tool", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"

    def test_sidechain_not_counted(self, tmp_path: pathlib.Path):
        """subagent (isSidechain=true) の user turn は集計対象外。"""
        entries = [
            json.dumps(
                {
                    "type": "user",
                    "isSidechain": True,
                    "message": {
                        "role": "user",
                        "content": "\u9055\u3046 \u9593\u9055\u3044 \u3084\u308a\u76f4\u3057 \u623b\u3057\u3066",
                    },
                },
                ensure_ascii=False,
            ),
        ]
        transcript = _write_raw_transcript(tmp_path, entries)
        result = _run(
            {"session_id": "kw-sidechain", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"


class TestCodexResumeDetection:
    """codex resume count による発火判定。"""

    def test_below_threshold_approves(self, tmp_path: pathlib.Path):
        _write_state(tmp_path, "cr-low", {"codex_resume_count": 1})
        transcript = _write_transcript(tmp_path, "no corrections here")
        result = _run(
            {"session_id": "cr-low", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"

    def test_at_threshold_blocks(self, tmp_path: pathlib.Path):
        _write_state(tmp_path, "cr-hit", {"codex_resume_count": 2})
        transcript = _write_transcript(tmp_path, "no corrections here")
        result = _run(
            {"session_id": "cr-hit", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "block"
        assert "codex" in decision.get("reason", "").lower()


class TestBothConditions:
    """両方の条件を同時に満たす場合。"""

    def test_both_triggered(self, tmp_path: pathlib.Path):
        _write_state(tmp_path, "both", {"codex_resume_count": 3})
        transcript = _write_transcript(
            tmp_path,
            "\u9055\u3046 \u9593\u9055 \u3084\u308a\u76f4\u3057\u3066 \u623b\u3057\u3066",
        )
        result = _run(
            {"session_id": "both", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "block"
        reason = decision.get("reason", "")
        assert "correction" in reason.lower()
        assert "codex" in reason.lower()


class TestStopAdviceOnce:
    """1 セッション 1 回の制限。"""

    def test_second_stop_approves(self, tmp_path: pathlib.Path):
        _write_state(tmp_path, "once", {"stop_advice_given": True})
        result = _run(
            {"session_id": "once", "transcript_path": "/nonexistent"},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"


class TestEdgeCases:
    """エッジケース。"""

    def test_invalid_json_approves(self, tmp_path: pathlib.Path):
        result = _run("not json", state_dir=tmp_path)
        assert result.returncode == 0
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"

    def test_missing_transcript_approves(self, tmp_path: pathlib.Path):
        result = _run(
            {"session_id": "no-transcript", "transcript_path": "/nonexistent/file"},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"

    def test_empty_session_id_approves(self, tmp_path: pathlib.Path):
        result = _run({"session_id": "", "transcript_path": "/x"}, state_dir=tmp_path)
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"


class TestUncommittedChanges:
    """未コミット変更の検出。"""

    def _make_dirty_repo(self, tmp_path: pathlib.Path) -> pathlib.Path:
        """変更ありの git リポジトリを作成する。"""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@test"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=str(repo), capture_output=True, check=True)
        (repo / "file.txt").write_text("initial")
        subprocess.run(["git", "add", "file.txt"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), capture_output=True, check=True)
        # tracked file を変更して未コミット状態にする
        (repo / "file.txt").write_text("modified")
        return repo

    def _make_clean_repo(self, tmp_path: pathlib.Path) -> pathlib.Path:
        """変更なしの git リポジトリを作成する。"""
        repo = tmp_path / "clean"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@test"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=str(repo), capture_output=True, check=True)
        (repo / "file.txt").write_text("clean")
        subprocess.run(["git", "add", "file.txt"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), capture_output=True, check=True)
        return repo

    def test_blocks_with_uncommitted_changes(self, tmp_path: pathlib.Path):
        repo = self._make_dirty_repo(tmp_path)
        transcript = _write_transcript(tmp_path, "no corrections")
        result = _run(
            {"session_id": "dirty", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "block"
        reason = decision.get("reason", "")
        assert "uncommitted" in reason.lower()
        # LLM 宛てメッセージ規約: プレフィックスとサフィックスが付与されていること。
        assert "[auto-generated: agent-toolkit/stop_advisor]" in reason
        assert "Auto-generated hook notice" in reason

    def test_approves_clean_repo(self, tmp_path: pathlib.Path):
        repo = self._make_clean_repo(tmp_path)
        transcript = _write_transcript(tmp_path, "no corrections")
        result = _run(
            {"session_id": "clean", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"

    def test_allows_after_block_limit(self, tmp_path: pathlib.Path):
        repo = self._make_dirty_repo(tmp_path)
        transcript = _write_transcript(tmp_path, "no corrections")
        # 2 回ブロック後、3 回目は通過する
        _write_state(tmp_path, "limit", {"uncommitted_block_count": 2})
        result = _run(
            {"session_id": "limit", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"

    def test_untracked_only_approves(self, tmp_path: pathlib.Path):
        """untracked ファイルのみの場合はブロックしない。"""
        repo = self._make_clean_repo(tmp_path)
        (repo / "untracked.txt").write_text("new file")
        transcript = _write_transcript(tmp_path, "no corrections")
        result = _run(
            {"session_id": "untracked", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"


class TestQuestionSuppressesUncommittedBlock:
    """Claude がユーザーに質問中の場合は未コミット変更ブロックを抑制する。"""

    def _make_dirty_repo(self, tmp_path: pathlib.Path) -> pathlib.Path:
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@test"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=str(repo), capture_output=True, check=True)
        (repo / "file.txt").write_text("initial")
        subprocess.run(["git", "add", "file.txt"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), capture_output=True, check=True)
        (repo / "file.txt").write_text("modified")
        return repo

    def _write_transcript_with_assistant_last(self, tmp_path: pathlib.Path, assistant_content: list[dict]) -> pathlib.Path:
        """アシスタントターンを末尾に持つ transcript を書き出す。"""
        lines = [
            json.dumps({"type": "user", "message": {"role": "user", "content": "hello"}}),
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"role": "assistant", "content": assistant_content},
                },
                ensure_ascii=False,
            ),
        ]
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return transcript

    def test_ask_user_question_tool_suppresses_block(self, tmp_path: pathlib.Path):
        """AskUserQuestion ツール呼び出しが最後にある場合はブロックしない。"""
        repo = self._make_dirty_repo(tmp_path)
        content: list[dict[str, Any]] = [
            {"type": "text", "text": "どちらを選びますか？"},
            {"type": "tool_use", "id": "x", "name": "AskUserQuestion", "input": {}},
        ]
        transcript = self._write_transcript_with_assistant_last(tmp_path, content)
        result = _run(
            {"session_id": "ask-q", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"

    def test_fullwidth_question_mark_suppresses_block(self, tmp_path: pathlib.Path):
        """テキストが全角 ？ で終わる場合はブロックしない。"""
        repo = self._make_dirty_repo(tmp_path)
        content = [{"type": "text", "text": "ステージ済みファイルをどうしますか？"}]
        transcript = self._write_transcript_with_assistant_last(tmp_path, content)
        result = _run(
            {"session_id": "fw-q", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"

    def test_halfwidth_question_mark_suppresses_block(self, tmp_path: pathlib.Path):
        """テキストが半角 ? で終わる場合はブロックしない。"""
        repo = self._make_dirty_repo(tmp_path)
        content = [{"type": "text", "text": "Which option do you prefer?"}]
        transcript = self._write_transcript_with_assistant_last(tmp_path, content)
        result = _run(
            {"session_id": "hw-q", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"

    def test_mid_text_question_mark_suppresses_block(self, tmp_path: pathlib.Path):
        """テキストの末尾でなくとも ? や ？ が含まれていれば質問扱いでブロックしない。"""
        repo = self._make_dirty_repo(tmp_path)
        content = [
            {
                "type": "text",
                "text": "続行してよいですか？ 判断をいただき次第、残タスクを進めます。",
            }
        ]
        transcript = self._write_transcript_with_assistant_last(tmp_path, content)
        result = _run(
            {"session_id": "mid-q", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"

    def test_question_in_earlier_block_suppresses_block(self, tmp_path: pathlib.Path):
        """同一ターンの先頭テキストブロックに ? があり、末尾ブロックに無い場合も質問扱いでブロックしない。"""
        repo = self._make_dirty_repo(tmp_path)
        content = [
            {"type": "text", "text": "この方針で進めますか？"},
            {"type": "text", "text": "ご判断をお願いします。"},
        ]
        transcript = self._write_transcript_with_assistant_last(tmp_path, content)
        result = _run(
            {"session_id": "split-q", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"

    def test_non_question_text_still_blocks(self, tmp_path: pathlib.Path):
        """? を含まないテキストの場合は通常通りブロックする。"""
        repo = self._make_dirty_repo(tmp_path)
        content = [{"type": "text", "text": "コミットします。"}]
        transcript = self._write_transcript_with_assistant_last(tmp_path, content)
        result = _run(
            {"session_id": "no-q", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "block"
        assert "uncommitted" in decision.get("reason", "").lower()

    def test_split_entry_question_suppresses_block(self, tmp_path: pathlib.Path):
        """同一 message.id のエントリが分割された場合、前のエントリの質問テキストを検出する。

        テキストエントリの後にツール呼び出しのみのエントリが来る場合（競合状態:
        ツールエントリが最後に flush された状態でフックが発火）、
        前のエントリの質問テキストを確認してブロックを抑制する。
        """
        repo = self._make_dirty_repo(tmp_path)
        msg_id = "msg_test_split123"
        lines = [
            json.dumps({"type": "user", "message": {"role": "user", "content": "hello"}}),
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "id": msg_id,
                        "role": "assistant",
                        "content": [{"type": "text", "text": "コミットしますか？"}],
                    },
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "id": msg_id,
                        "role": "assistant",
                        "content": [{"type": "tool_use", "id": "x", "name": "Bash", "input": {}}],
                    },
                },
                ensure_ascii=False,
            ),
        ]
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text("\n".join(lines) + "\n", encoding="utf-8")
        result = _run(
            {"session_id": "split-entry", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"

    def test_different_turn_tool_use_does_not_suppress_block(self, tmp_path: pathlib.Path):
        """前のターンに質問があっても、最新ターンが質問でなければブロックする。

        異なる message.id を持つエントリは別ターンとして扱い、
        ユーザー応答を挟んだ後のツール呼び出しのみのエントリではブロックを通過させる。
        """
        repo = self._make_dirty_repo(tmp_path)
        lines = [
            json.dumps({"type": "user", "message": {"role": "user", "content": "hello"}}),
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "id": "msg_old_turn",
                        "role": "assistant",
                        "content": [{"type": "text", "text": "コミットしますか？"}],
                    },
                },
                ensure_ascii=False,
            ),
            json.dumps({"type": "user", "message": {"role": "user", "content": "はい"}}),
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "id": "msg_new_turn",
                        "role": "assistant",
                        "content": [{"type": "tool_use", "id": "y", "name": "Bash", "input": {}}],
                    },
                },
                ensure_ascii=False,
            ),
        ]
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text("\n".join(lines) + "\n", encoding="utf-8")
        result = _run(
            {"session_id": "diff-turn", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "block"
        assert "uncommitted" in decision.get("reason", "").lower()


class TestGitStatusDisplay:
    """approve 時の git status 表示。"""

    def _make_dirty_repo(self, tmp_path: pathlib.Path) -> pathlib.Path:
        repo = tmp_path / "dirty"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@test"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=str(repo), capture_output=True, check=True)
        (repo / "file.txt").write_text("initial")
        subprocess.run(["git", "add", "file.txt"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), capture_output=True, check=True)
        (repo / "file.txt").write_text("modified")
        return repo

    def _make_clean_repo(self, tmp_path: pathlib.Path) -> pathlib.Path:
        repo = tmp_path / "clean"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@test"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=str(repo), capture_output=True, check=True)
        (repo / "file.txt").write_text("clean")
        subprocess.run(["git", "add", "file.txt"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), capture_output=True, check=True)
        return repo

    def test_dirty_repo_shows_git_status(self, tmp_path: pathlib.Path):
        """未コミット変更がある場合、approve 時に systemMessage で git status を表示する。"""
        repo = self._make_dirty_repo(tmp_path)
        transcript = _write_transcript(tmp_path, "no corrections")
        # ブロック上限を超過させて approve パスに到達させる
        _write_state(tmp_path, "gs-dirty", {"uncommitted_block_count": 2})
        result = _run(
            {"session_id": "gs-dirty", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"
        assert "systemMessage" in decision
        assert "git status" in decision["systemMessage"]
        assert "file.txt" in decision["systemMessage"]

    def test_clean_repo_no_system_message(self, tmp_path: pathlib.Path):
        """clean repo では systemMessage を出力しない。"""
        repo = self._make_clean_repo(tmp_path)
        transcript = _write_transcript(tmp_path, "no corrections")
        result = _run(
            {"session_id": "gs-clean", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"
        assert "systemMessage" not in decision

    def test_no_cwd_no_system_message(self, tmp_path: pathlib.Path):
        """cwd 未指定時は systemMessage を出力しない。"""
        transcript = _write_transcript(tmp_path, "no corrections")
        result = _run(
            {"session_id": "gs-nocwd", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"
        assert "systemMessage" not in decision

    def test_untracked_only_no_system_message(self, tmp_path: pathlib.Path):
        """untracked ファイルのみの場合は systemMessage を出力しない。"""
        repo = self._make_clean_repo(tmp_path)
        (repo / "untracked.txt").write_text("new file")
        transcript = _write_transcript(tmp_path, "no corrections")
        result = _run(
            {"session_id": "gs-untracked", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"
        assert "systemMessage" not in decision
