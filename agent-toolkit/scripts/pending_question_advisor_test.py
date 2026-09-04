"""地の文の問いかけを検出するStopフックの公開契約を検証する。"""

import json
import os
import pathlib
import subprocess

import _fork_runner
from _test_helpers import _write_transcript

_SCRIPT = pathlib.Path(__file__).resolve().parent / "hook.py"


def _run(payload: dict, *, state_dir: pathlib.Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update({"TMPDIR": str(state_dir), "TEMP": str(state_dir), "TMP": str(state_dir)})
    return _fork_runner.run_script(
        _SCRIPT,
        argv=("pending_question_advisor",),
        input=json.dumps(payload, ensure_ascii=False),
        env=env,
    )


def _assistant_entry(content: list[dict]) -> dict:
    return {"type": "assistant", "message": {"id": "msg-1", "content": content}}


def _transcript_with_response(
    directory: pathlib.Path,
    text: str,
    *,
    ask_user_question: bool = False,
) -> pathlib.Path:
    """指定した本文を直前のアシスタント応答として持つtranscriptを生成する。"""
    content: list[dict] = [{"type": "text", "text": text}]
    if ask_user_question:
        content.append({"type": "tool_use", "name": "AskUserQuestion", "input": {}})
    return _write_transcript(directory, [_assistant_entry(content)])


def _decision(result: subprocess.CompletedProcess[str]) -> dict:
    assert result.returncode == 0
    return json.loads(result.stdout)


def test_blocks_when_plain_text_asks_user(tmp_path: pathlib.Path) -> None:
    """疑問符で終わる文で判断を求めたまま終えようとすると遮断する。"""
    transcript = _transcript_with_response(tmp_path, "実装を進めました。次はどちらの案を採用しますか？")

    result = _decision(_run({"session_id": "ask", "transcript_path": str(transcript)}, state_dir=tmp_path))

    assert result["decision"] == "block"
    assert "AskUserQuestion" in result["reason"]


def test_blocks_when_plain_text_requests_instruction(tmp_path: pathlib.Path) -> None:
    """依頼形の問いかけで判断を求めたまま終えようとすると遮断する。"""
    transcript = _transcript_with_response(tmp_path, "2案を検討しました。採用する案をお知らせください。")

    result = _decision(_run({"session_id": "request", "transcript_path": str(transcript)}, state_dir=tmp_path))

    assert result["decision"] == "block"


def test_allows_delegated_session_to_return_question(tmp_path: pathlib.Path) -> None:
    """委譲先では問いかけを含む差し戻し報告の終了を許可する。"""
    transcript = _transcript_with_response(tmp_path, "どちらの案を採用しますか？")
    env = os.environ.copy()
    env["AGENT_TOOLKIT_DELEGATED_SESSION"] = "1"
    env.update({"TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path)})

    result = _fork_runner.run_script(
        _SCRIPT,
        argv=("pending_question_advisor",),
        input=json.dumps({"session_id": "delegated", "transcript_path": str(transcript)}, ensure_ascii=False),
        env=env,
    )

    assert not _decision(result)


def test_allows_when_a_question_mark_is_inside_a_sentence(tmp_path: pathlib.Path) -> None:
    """疑問符の直後に語が続く文は、判断を求める文として扱わない。"""
    transcript = _transcript_with_response(
        tmp_path,
        "この関数は引数が3個ですか？と一瞬迷ったが、定義を読んで確認した。",
    )

    result = _run({"session_id": "inline", "transcript_path": str(transcript)}, state_dir=tmp_path)

    assert not _decision(result)


def test_blocks_when_a_question_is_followed_by_another_sentence(tmp_path: pathlib.Path) -> None:
    """疑問符の後ろへ別の文が続く場合も、問いかけの文を検出して遮断する。"""
    transcript = _transcript_with_response(tmp_path, "どちらを採用しますか？ 指示があるまで待機する。")

    result = _decision(_run({"session_id": "followed", "transcript_path": str(transcript)}, state_dir=tmp_path))

    assert result["decision"] == "block"


def test_allows_when_ask_user_question_used(tmp_path: pathlib.Path) -> None:
    """同じ応答が`AskUserQuestion`を呼び出している場合は通過する。"""
    transcript = _transcript_with_response(
        tmp_path,
        "2案を検討しました。どちらを採用しますか？",
        ask_user_question=True,
    )

    result = _run({"session_id": "asked", "transcript_path": str(transcript)}, state_dir=tmp_path)

    assert not _decision(result)


def test_allows_when_question_is_in_code_block(tmp_path: pathlib.Path) -> None:
    """疑問符がコードブロックとインラインコードの内側にだけある場合は通過する。"""
    transcript = _transcript_with_response(
        tmp_path,
        "検査コマンドの結果を示します。\n```sh\ntest -e foo? && echo done?\n```\n判定は`ok?`で表す。",
    )

    result = _run({"session_id": "code", "transcript_path": str(transcript)}, state_dir=tmp_path)

    assert not _decision(result)


def test_allows_when_question_is_in_quote(tmp_path: pathlib.Path) -> None:
    """疑問符が引用行にだけある場合は通過する。"""
    transcript = _transcript_with_response(
        tmp_path,
        "受領したAWIの本文を引用します。\n> この工程は必要ですか？\n該当箇所を修正しました。",
    )

    result = _run({"session_id": "quote", "transcript_path": str(transcript)}, state_dir=tmp_path)

    assert not _decision(result)


def test_allows_when_stop_hook_active(tmp_path: pathlib.Path) -> None:
    """遮断の再帰を避けるため、`stop_hook_active`が真の入力は通過する。"""
    transcript = _transcript_with_response(tmp_path, "どちらを採用しますか？")

    result = _run(
        {"session_id": "active", "transcript_path": str(transcript), "stop_hook_active": True},
        state_dir=tmp_path,
    )

    assert not _decision(result)


def test_allows_when_transcript_missing(tmp_path: pathlib.Path) -> None:
    """記録の取得に失敗した場合は例外を送出せず通過する。"""
    result = _run(
        {"session_id": "missing", "transcript_path": str(tmp_path / "absent.jsonl")},
        state_dir=tmp_path,
    )

    assert not _decision(result)
    assert not result.stderr
