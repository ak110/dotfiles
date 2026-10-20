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


def _run(body_path: pathlib.Path, *sources: pathlib.Path, import_spec: pathlib.Path | None = None) -> int:
    args = argparse.Namespace(
        script_name="wi-quote-check",
        script_args=[
            "--",
            str(body_path),
            *(item for source in sources for item in ("--source", str(source))),
            *(["--import-spec", str(import_spec)] if import_spec is not None else []),
        ],
    )
    return run_script.dispatch(args)


def _spec(tmp_path: pathlib.Path, *selections: dict[str, object]) -> pathlib.Path:
    path = tmp_path / "selection.json"
    path.write_text(json.dumps(selections, ensure_ascii=False), encoding="utf-8")
    return path


def test_public_import_uses_saved_source_and_checks_result(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """生成側が保存した出所を`--import-spec`で取り込み、原稿の更新、一致判定と単位の位置を同じ実行で得る。"""
    events = _user_events(tmp_path, capsys, _UTTERANCE)
    event = json.loads(events.read_text(encoding="utf-8").splitlines()[0])
    body = tmp_path / "body.md"
    before = _body("旧引用") + "\n## 次の節\n\n残す本文。\n"
    body.write_text(before, encoding="utf-8")
    spec = _spec(tmp_path, {"source": str(events), "record": event["record"], "line": event["line"]})

    assert _run(body, import_spec=spec) == 0
    output = capsys.readouterr().out
    after = body.read_text(encoding="utf-8")
    assert after.partition("## ユーザー指摘の逐語引用")[0] == before.partition("## ユーザー指摘の逐語引用")[0]
    assert after.partition("## 次の節")[2] == before.partition("## 次の節")[2]
    assert f"record={event['record']} line={event['line']}" in after
    assert str(events) not in after
    assert f"```text\n{_UTTERANCE}\n```" in after
    assert [unit for _, _, _, unit in _positions(output)] == [
        "検索画面の表示を直して。",
        "最近の記録も調べて。",
        "手順を簡潔にして。",
    ]
    assert _run(body, events) == 0


def test_duplicate_quote_reports_all_sources_but_import_uses_selected_event(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """引用と一致する全位置を示し、選択importは同じ引用でもフェンスごとの元イベントを保つ。"""
    events = tmp_path / "events.jsonl"
    values = [
        {"kind": "user", "text": text, "record": "claude:example", "line": line, "sequence": sequence}
        for text, line, sequence in (("前置き。同じ要求。", 1, 1), ("同じ要求。", 2, 2), ("同じ要求。", 2, 3))
    ]
    events.write_text("".join(json.dumps(value, ensure_ascii=False) + "\n" for value in values), encoding="utf-8")
    body = tmp_path / "body.md"
    body.write_text(_body("同じ要求。"), encoding="utf-8")
    assert _run(body, events) == 0
    output = capsys.readouterr().out
    assert all(f"line={value['line']} sequence={value['sequence']}" in output for value in values)
    spec = _spec(tmp_path, {"source": str(events), "record": "claude:example", "line": 2, "sequence": 3})
    assert _run(body, import_spec=spec) == 0
    output = capsys.readouterr().out
    assert "line=2 sequence=3" in output and "line=1" not in output and "sequence=2" not in output
    assert str(events) not in body.read_text(encoding="utf-8")


def test_import_preserves_source_modes_and_answer_context(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """複数出所・同位置の選択を配列順で取り込み、確認の文脈とユーザー値を保つ。"""
    events = _user_events(tmp_path, capsys, "前置き。指定部分。後置き。", "全体を残して。\n```text\n## 内側の見出し\n```")
    user = [json.loads(line) for line in events.read_text(encoding="utf-8").splitlines() if line.strip()]
    extra = tmp_path / "answers.jsonl"
    answer = {
        **_ANSWER_EVENT,
        "assistant_context": [
            {
                "question": "一時障害をどう扱いますか。",
                "options": [
                    {"label": "失敗扱い", "description": "失敗にします。"},
                    {"label": "確認を省く", "description": "確認しません。"},
                    {"label": "維持", "description": "現状を保ちます。"},
                ],
            }
        ],
    }
    command = {
        "kind": "user",
        "record": answer["record"],
        "line": 557,
        "sequence": 4,
        "text": "<command-name>/指示</command-name>\n<command-args>引数だけを残して。\n二行目。</command-args>",
    }
    extra.write_text(
        json.dumps(answer, ensure_ascii=False) + "\n" + json.dumps(command, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    body = tmp_path / "body.md"
    body.write_text(_body("旧引用") + "\n## 次の節\n残す。\n", encoding="utf-8")
    spec = _spec(
        tmp_path,
        {"source": str(extra), "record": answer["record"], "line": 557, "sequence": 4, "mode": "command-args"},
        {"source": str(events), "record": user[0]["record"], "line": user[0]["line"], "mode": "range", "start": 5, "end": 9},
        {"source": str(extra), "record": answer["record"], "line": 557, "sequence": 3, "mode": "answer"},
        {"source": str(events), "record": user[1]["record"], "line": user[1]["line"]},
    )
    assert _run(body, import_spec=spec) == 0
    after = body.read_text(encoding="utf-8")
    order = [after.index(text) for text in ("引数だけを残して。", "指定部分。", "質問: 一時障害", "全体を残して。")]
    assert order == sorted(order)
    assert "選択肢: 維持: 現状を保ちます。" in after
    assert "回答: 失敗扱い\n確認を省く\n自由記述: 夜間だけ止めて。" in after
    assert "````text\n全体を残して。\n```text\n## 内側の見出し\n```\n````" in after
    positions = _positions(capsys.readouterr().out)
    assert any(number == 3 and unit == "夜間だけ止めて。" for number, _, _, unit in positions)
    assert not any("一時障害" in unit or "現状を保ちます" in unit for _, _, _, unit in positions)
    assert _run(body, extra, events) == 0


@pytest.mark.parametrize("failure", ["missing", "ambiguous", "range", "invalid-json", "invalid-answer", "invalid-sequence"])
def test_import_failure_keeps_draft_unchanged(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], failure: str) -> None:
    """後続の選択が不成立でも先行分を書かず、非0と入力の修正案内を返す。"""
    events = tmp_path / "events.jsonl"
    event = {"kind": "user", "text": "残す原文。", "record": "claude:example", "line": 9, "sequence": 1}
    records = [event, {**event, "sequence": 2}]
    if failure == "invalid-answer":
        records[1] = {**records[1], "assistant_context": [], "user_response": []}
    events.write_text("".join(json.dumps(value, ensure_ascii=False) + "\n" for value in records), encoding="utf-8")
    body = tmp_path / "body.md"
    before = (_body("旧引用") + "\n## 次の節\n残す。\n").replace("\n", "\r\n").encode("utf-8")
    body.write_bytes(before)
    first: dict[str, object] = {"source": str(events), "record": "claude:example", "line": 9, "sequence": 1}
    second = {**first, "sequence": 2}
    if failure == "missing":
        second["line"] = 10
    elif failure == "ambiguous":
        del second["sequence"]
    elif failure == "range":
        second.update(mode="range", start=2, end=999)
    elif failure == "invalid-answer":
        second["mode"] = "answer"
    elif failure == "invalid-sequence":
        second["sequence"] = True
    spec = _spec(tmp_path, first, second)
    if failure == "invalid-json":
        spec.write_text("[", encoding="utf-8")
    assert _run(body, import_spec=spec) == 2
    assert body.read_bytes() == before
    assert "次の操作:" in capsys.readouterr().err


def test_import_preserves_verbatim_line_endings_and_draft_bytes(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CRLFの原稿の節外をバイト列で保ち、原文の改行を原稿への取り込みと原文の比較で維持する。"""
    content = "一行目を残して。\r\n二行目も残して。\n"
    # 保存済みJSONLを入力にする。抽出前のtranscriptでは抽出側が末尾改行を除くため、末尾の保全を検証できない。
    event = {"kind": "user", "text": content, "record": "claude:example", "line": 9}
    events = tmp_path / "events.jsonl"
    events.write_text(json.dumps(event, ensure_ascii=False) + "\n", encoding="utf-8")
    body = tmp_path / "body.md"
    prefix = b"\xef\xbb\xbf" + "# 題\r\n\r\n## ユーザー指摘の逐語引用\r\n".encode()
    suffix = "## 次の節\r\n保持する。\r\n".encode()
    body.write_bytes(prefix + "\r\n旧引用\r\n\r\n".encode() + suffix)
    spec = _spec(tmp_path, {"source": str(events), "record": event["record"], "line": event["line"]})
    assert _run(body, import_spec=spec) == 0
    after = body.read_bytes()
    assert after.startswith(prefix) and after.endswith(suffix)
    assert ("```text\n" + content + "\n```").encode("utf-8") in after
    assert "文字11-18" in capsys.readouterr().out
    assert _run(body, events) == 0


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


def test_stale_reference_after_adding_option_reports_line_and_new_positions(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """位置を写してから確認回答へ選択肢を追加すると、古い位置を拒否して同じ実行で修正値を示す。"""
    source = tmp_path / "source.txt"
    source.write_text(_UTTERANCE, encoding="utf-8")
    before = "質問: 対象は？\n回答: " + _UTTERANCE
    after = before.replace("回答: ", "選択肢: 表示と手順\n回答: ")
    old_start = before.index("検索") + 1
    reference = f"逐語引用 text[1] 文字{old_start}-{old_start + 11}"
    draft = "## 反映内容と反映先\n\n" + reference + "を直す。\n\n" + _body(after)
    body = tmp_path / "body.md"
    body.write_text(draft, encoding="utf-8")

    assert _run(body, source) == 1
    captured = capsys.readouterr()
    assert f"3行目: {reference}" in captured.err
    assert "標準出力の位置の一覧" in captured.err and "同じコマンドを再実行" in captured.err
    new_start = after.index("検索") + 1
    assert f"逐語引用 text[1] 文字{new_start}-{new_start + 11}: 検索画面の表示を直して。" in captured.out
    assert body.read_text(encoding="utf-8") == draft


@pytest.mark.parametrize(
    ("fences", "reference"),
    [
        (("追加した発話。", _UTTERANCE), "逐語引用 text[1] 文字14-23"),
        ((_UTTERANCE,), "逐語引用 text[2] 文字1-12"),
        ((_UTTERANCE,), "逐語引用 text[0] 文字1-12"),
        ((_UTTERANCE,), "逐語引用 text[1] 文字2-12"),
        ((_UTTERANCE,), "逐語引用 text[1] 文字1-11"),
        ((_UTTERANCE,), "逐語引用text[1]文字24-12"),
    ],
    ids=["inserted-fence", "missing-fence", "zero-fence", "partial-start", "partial-end", "reversed-range"],
)
def test_invalid_reference_is_rejected(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], fences: tuple[str, ...], reference: str
) -> None:
    """フェンスの番号と要求単位の境界のずれを、引用節の後ろにある参照でも検出する。"""
    source = tmp_path / "source.txt"
    source.write_text("\n".join(fences), encoding="utf-8")
    body = tmp_path / "body.md"
    draft = _body(*fences) + "\n## 反映内容と反映先\n\n" + reference + "を直す。\n"
    body.write_text(draft, encoding="utf-8")

    assert _run(body, source) == 1
    captured = capsys.readouterr()
    line = draft.splitlines().index(reference + "を直す。") + 1
    assert f"{line}行目: {reference}" in captured.err
    assert "逐語引用 text[1] 文字1-" in captured.out


def test_valid_references_preserve_output(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """単位どおりと連続単位の参照は、語間の空白にかかわらず参照が無い本文と同じ出力を返す。"""
    source = tmp_path / "source.txt"
    source.write_text(_UTTERANCE, encoding="utf-8")
    body = tmp_path / "body.md"
    body.write_text(_body(_UTTERANCE), encoding="utf-8")
    assert _run(body, source) == 0
    baseline = capsys.readouterr()
    references = "逐語引用text[1]文字1-12、逐語引用 text[1] 文字14-32、逐語引用  text[1]  文字24-32"
    body.write_text(_body(_UTTERANCE) + "\n## 反映内容と反映先\n\n" + references, encoding="utf-8")

    assert _run(body, source) == 0
    assert capsys.readouterr() == baseline


def test_reference_scan_excludes_only_quote_text_fences(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """引用中の参照文字列はユーザーの原文として保ち、引用節の地の文と別節のフェンスは検査する。"""
    content = "逐語引用 text[99] 文字1-2を使って。"
    source = tmp_path / "source.txt"
    source.write_text(content, encoding="utf-8")
    body = tmp_path / "body.md"
    body.write_text(_body(content), encoding="utf-8")
    assert _run(body, source) == 0
    capsys.readouterr()
    body.write_text(
        _body(content) + "\n地の文: 逐語引用 text[98] 文字1-2\n\n## 別節\n\n```text\n逐語引用 text[97] 文字1-2\n```\n",
        encoding="utf-8",
    )

    assert _run(body, source) == 1
    captured = capsys.readouterr()
    assert "text[98]" in captured.err and "text[97]" in captured.err
    assert "text[99]" not in captured.err


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
