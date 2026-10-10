"""公開本文読取で文字と改行を失わず、応答予算と継続位置を保つ。"""

import json
import pathlib
from collections.abc import Iterator

import pytest

from agent_toolkit import atk
from agent_toolkit._atk.output_file import AUTO_SAVE_THRESHOLD_BYTES


def _read_chunks(path: pathlib.Path, budget: int | None, capsys: pytest.CaptureFixture[str]) -> Iterator[dict]:
    """公開CLIの返却を継続位置から取得し、各応答の予算と前進を確かめる。"""
    position = 0
    while True:
        arguments = ["read-file", "--start", str(position)]
        if budget is not None:
            arguments.extend(["--max-bytes", str(budget)])
        with pytest.raises(SystemExit, match="0"):
            atk.main([*arguments, "--", str(path)])
        captured = capsys.readouterr()
        assert not captured.err
        assert len(captured.out.encode("utf-8")) <= (4096 if budget is None else budget)
        response = json.loads(captured.out)
        assert response["start"] == position
        assert response["end"] == position + len(response["text"])
        yield response
        if response["eof"]:
            assert response["next"] is None
            break
        assert response["next"] == response["end"] > position
        position = response["next"]


@pytest.mark.parametrize("budget", [None, 12000, AUTO_SAVE_THRESHOLD_BYTES], ids=["default", "larger", "save-boundary"])
@pytest.mark.parametrize(
    "text",
    ["日本語\r\n" * 3000, '{"値":"' + "長" * 12000 + '"}', '\x00\t"\\\n' * 3000, "", "\ufeff本文"],
    ids=["japanese-crlf", "single-line-json", "controls", "empty", "bom"],
)
def test_read_file_cli_roundtrips_utf8_with_bounded_complete_responses(
    text: str, budget: int | None, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """返却の位置だけで終端へ進み、元のバイト列を復元できる。"""
    path = tmp_path / "本文.txt"
    path.write_bytes(text.encode("utf-8"))
    responses = list(_read_chunks(path, budget, capsys))
    assert responses[-1]["end"] == len(text)
    assert "".join(response["text"] for response in responses).encode("utf-8") == path.read_bytes()
    with pytest.raises(SystemExit, match="0"):
        atk.main(["read-file", "--start", str(len(text)), "--", str(path)])
    assert json.loads(capsys.readouterr().out)["text"] == ""


def test_larger_budget_reduces_cli_invocations_without_losing_text(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """同じ日本語本文を読む公開CLIの起動数を、予算だけ変えた対照で比較する。"""
    path = tmp_path / "日本語.txt"
    text = "日本語の本文\r\n" * 1400
    path.write_bytes(text.encode("utf-8"))
    counts: list[int] = []
    for budget in (None, 12000):
        responses = list(_read_chunks(path, budget, capsys))
        assert "".join(response["text"] for response in responses).encode("utf-8") == path.read_bytes()
        counts.append(len(responses))
    assert counts[1] < counts[0]


@pytest.mark.parametrize("text", ["", "日", "\x00", "本文" * 500], ids=["empty", "japanese", "control", "long"])
@pytest.mark.parametrize("budget", [-1, 0, 32, AUTO_SAVE_THRESHOLD_BYTES + 1])
def test_budget_must_fit_a_complete_progressing_response(
    text: str, budget: int, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """位置だけの停滞応答と自動保存への循環を、公開境界で拒否する。"""
    path = tmp_path / "input.txt"
    path.write_bytes(text.encode("utf-8"))
    with pytest.raises(SystemExit, match="2"):
        atk.main(["read-file", "--max-bytes", str(budget), "--", str(path)])
    captured = capsys.readouterr()
    assert not captured.out
    assert "失敗:" in captured.err
    assert "次の操作:" in captured.err
    assert "--max-bytes" in captured.err


@pytest.mark.parametrize("case", ["relative", "negative", "past-end", "missing", "non-utf8"])
def test_invalid_file_or_position_has_recovery(case: str, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """不正な入力で本文を返さず、直す入力を案内する。"""
    path = tmp_path / "input.txt"
    path.write_bytes(b"abc" if case != "non-utf8" else b"\xff")
    target = "relative.txt" if case == "relative" else str(tmp_path / "missing") if case == "missing" else str(path)
    start = "-1" if case == "negative" else "4" if case == "past-end" else "0"
    with pytest.raises(SystemExit, match="2"):
        atk.main(["read-file", "--start", start, "--", target])
    captured = capsys.readouterr()
    assert not captured.out
    assert "失敗:" in captured.err
    assert "次の操作:" in captured.err
