"""公開コマンド`atk run-script wi-quote-check`で、逐語引用の一致判定と引用位置の出力を検証する。"""

from __future__ import annotations

import argparse
import json
import pathlib
import re

import pytest

from agent_toolkit._atk import run_script

_UTTERANCE = "検索画面の表示を直して。 最近の記録も調べて。手順を簡潔にして。"
_POSITION = re.compile(r"^逐語引用 text\[(\d+)\] 文字(\d+)-(\d+): (.+)$")


def _body(*fences: str) -> str:
    """逐語引用の節に`text`フェンスを並べたAWI本文を返す。"""
    blocks = "\n\n".join(f"```text\n{fence}\n```" for fence in fences)
    return (
        "---\ntype: awi\nsource: agent\n---\n\n# 題\n\n## 完成条件\n\n- 条件\n\n"
        f"## ユーザー指摘の逐語引用\n\n出所: 会話\n\n{blocks}\n\n回答済み: 問いには回答済みである。\n"
    )


def _user_events(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], *texts: str) -> pathlib.Path:
    """生成側`session-review-evidence --user-events`を実行して、発話ごとの行を持つ出力を保存する。"""
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        "".join(
            json.dumps(
                {"type": "user", "timestamp": f"2026-10-06T00:00:0{index}Z", "message": {"role": "user", "content": text}},
                ensure_ascii=False,
            )
            + "\n"
            for index, text in enumerate(texts, start=1)
        ),
        encoding="utf-8",
    )
    args = argparse.Namespace(
        script_name="session-review-evidence",
        script_args=["--", str(transcript), "--user-events", "--since", "2026-10-06T00:00:00Z"],
    )
    assert run_script.dispatch(args) == 0
    output = tmp_path / "user-events.txt"
    output.write_text(capsys.readouterr().out, encoding="utf-8")
    return output


def _run(body_path: pathlib.Path, *sources: pathlib.Path) -> int:
    args = argparse.Namespace(
        script_name="wi-quote-check",
        script_args=["--", str(body_path), *(item for source in sources for item in ("--source", str(source)))],
    )
    return run_script.dispatch(args)


def _positions(output: str) -> list[tuple[int, int, int, str]]:
    return [
        (int(match[1]), int(match[2]), int(match[3]), match[4])
        for match in (_POSITION.match(line) for line in output.splitlines())
        if match
    ]


def test_matching_quote_reports_record_and_unit_positions(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """原文と一致する本文は終了コード0で、一致した`record`・`line`と、各単位の本文を指す位置を出力する。

    位置がずれると、起草者が要求単位と反映先の対応へ写した範囲が別の文を指し、読み手が原文の該当箇所を誤る。
    """
    events = _user_events(tmp_path, capsys, "前置きの発話", _UTTERANCE)
    body = tmp_path / "body.md"
    body.write_text(_body(_UTTERANCE), encoding="utf-8")

    assert _run(body, events) == 0
    output = capsys.readouterr().out
    user = [json.loads(line) for line in events.read_text(encoding="utf-8").splitlines() if line.strip()]
    matched = next(event for event in user if event.get("text") == _UTTERANCE)
    assert f"text[1] 本文: 一致 出所={events} record={matched['record']} line={matched['line']}" in output
    positions = _positions(output)
    assert [unit for _, _, _, unit in positions] == [
        "検索画面の表示を直して。",
        "最近の記録も調べて。",
        "手順を簡潔にして。",
    ]
    assert [(start, end) for _, start, end, _ in positions] == [(1, 12), (14, 23), (24, 32)]
    for _, start, end, unit in positions:
        assert _UTTERANCE[start - 1 : end] == unit


def test_one_character_difference_is_reported_with_fence_number(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """1文字でも原文と異なるフェンスは終了コード1とし、そのフェンスの番号と直し方を標準エラーへ示す。"""
    events = _user_events(tmp_path, capsys, _UTTERANCE, "二つ目の発話です。")
    body = tmp_path / "body.md"
    body.write_text(_body("二つ目の発話です。", _UTTERANCE.replace("最近の", "最新の")), encoding="utf-8")

    assert _run(body, events) == 1
    captured = capsys.readouterr()
    assert "text[2]" in captured.err and "text[1]" not in captured.err
    assert "\n次の操作: " in captured.err
    assert "逐語引用 text[" not in captured.out


# 確認回答の行は、2026年10月6日の`session-review-evidence --user-events`の実際の出力（`kind`、`text`、
# `assistant_context`、`user_response`を持つ行）から形を写した。`text`は回答と自由記述を改行で連結した値である。
_ANSWER_EVENT = {
    "kind": "user",
    "text": "失敗扱い\n確認を省く\n夜間だけ止めて。",
    "runtime_inserted": False,
    "assistant_context": [{"question": "一時障害をどう扱いますか。", "options": [{"label": "失敗扱い"}]}],
    "user_response": [{"answers": ["失敗扱い", "確認を省く"], "notes": "夜間だけ止めて。"}],
    "line": 557,
    "sequence": 3,
    "record": "claude:1ff08ff1-0acb-49df-8ecc-b117d41b44fb",
}
_ANSWER_RECORD = (
    "質問: 共通前提: 掲載はdocs.python.orgで確かめます。\n"
    "一時障害をどう扱いますか。\n"
    "選択肢: 失敗扱い: 定期実行を失敗させます。\n"
    "選択肢: 確認を省く: 掲載を確かめません。\n"
    "回答: 失敗扱い\n確認を省く\n"
    "自由記述: 夜間だけ止めて。"
)


@pytest.mark.parametrize(
    ("record", "expected"),
    [(_ANSWER_RECORD, 0), (_ANSWER_RECORD.replace("回答: 失敗扱い", "回答: 警告扱い"), 1)],
    ids=["answer-matches", "answer-differs"],
)
def test_answer_record_compares_only_answers_and_free_text(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], record: str, expected: int
) -> None:
    """確認回答の記録は、質問と選択肢が原文に無くても回答と自由記述の値が一致すれば一致とし、回答が異なれば不一致とする。

    質問と選択肢まで原文と比べると、エージェントの文を持たない現行の出力に対して正しい引用が常に不一致になる。
    """
    events = tmp_path / "user-events.txt"
    events.write_text(json.dumps(_ANSWER_EVENT, ensure_ascii=False) + "\n", encoding="utf-8")
    body = tmp_path / "body.md"
    body.write_text(_body(record), encoding="utf-8")

    assert _run(body, events) == expected
    captured = capsys.readouterr()
    if expected == 0:
        assert f"text[1] 回答: 一致 出所={events} record={_ANSWER_EVENT['record']} line=557" in captured.out
        positions = _positions(captured.out)
        assert [unit for _, _, _, unit in positions] == ["失敗扱い 確認を省く", "夜間だけ止めて。"]
        for _, start, end, unit in positions:
            assert "".join(record[start - 1 : end].split()) == "".join(unit.split())
    else:
        assert "text[1]の回答" in captured.err


def test_slash_command_arguments_match_text_with_command_elements(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """スラッシュコマンドの引数だけを引用したフェンスは、`text`が`<command-message>`などの要素を含んでいても一致とする。"""
    arguments = "AWIの逐語引用の確認をスクリプトにまとめて。"
    # 2026年10月6日の`--user-events`の出力の7行目が持つ、スラッシュコマンドの要素付きの`text`の形を写した。
    text = (
        "<command-message>agent-toolkit:add-awi-by-user</command-message>\n"
        "<command-name>/agent-toolkit:add-awi-by-user</command-name>\n"
        f"<command-args>{arguments}</command-args>"
    )
    events = tmp_path / "user-events.txt"
    event = {"kind": "user", "text": text, "runtime_inserted": False, "line": 9, "record": "claude:example"}
    events.write_text(json.dumps(event, ensure_ascii=False) + "\n", encoding="utf-8")
    body = tmp_path / "body.md"
    body.write_text(_body(arguments), encoding="utf-8")

    assert _run(body, events) == 0
    assert "text[1] 本文: 一致" in capsys.readouterr().out


def test_multiline_unit_position_covers_first_to_last_character(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """複数行にまたがる要求単位の位置も、その単位の最初の文字から最後の文字までを含む。

    要求単位は行を空白で連結してから文に分けるため、単位の本文はフェンスの連続した部分文字列にならない。
    改行を数えずに位置を求めると、範囲が単位の末尾を欠くか次の文へ広がる。
    """
    content = "設定画面の保存を\n直して。一覧から\n開けるようにして。\n\n- 再読込後も維持して。"
    source = tmp_path / "conversation.md"
    source.write_text(f"会話の記録\n{content}\n以上\n", encoding="utf-8")
    body = tmp_path / "body.md"
    body.write_text(_body(content), encoding="utf-8")

    assert _run(body, source) == 0
    output = capsys.readouterr().out
    assert f"text[1] 本文: 一致 出所={source}" in output
    positions = _positions(output)
    assert [unit for _, _, _, unit in positions] == [
        "設定画面の保存を 直して。",
        "一覧から 開けるようにして。",
        "再読込後も維持して。",
    ]
    for _, start, end, unit in positions:
        covered = content[start - 1 : end]
        assert covered[0] == unit[0] and covered[-1] == unit[-1]
        assert "".join(covered.split()) == "".join(unit.split())


@pytest.mark.parametrize(
    ("body_text", "relative", "missing_source"),
    [
        ("# 題\n\n本文だけ\n", False, False),
        (_body("発話"), True, False),
        (_body("発話"), False, True),
    ],
    ids=["no-quote-section", "relative-path", "missing-source"],
)
def test_unusable_input_exits_with_two(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    body_text: str,
    relative: bool,
    missing_source: bool,
) -> None:
    """逐語引用の節が無い本文、相対パス、読めない出所は原文との比較を始めず終了コード2とする。"""
    body = tmp_path / "body.md"
    body.write_text(body_text, encoding="utf-8")
    source = tmp_path / ("absent.txt" if missing_source else "source.txt")
    if not missing_source:
        source.write_text("発話\n", encoding="utf-8")
    if relative:
        monkeypatch.chdir(tmp_path)
        body = pathlib.Path("body.md")
    assert _run(body, source) == 2
    assert capsys.readouterr().err
