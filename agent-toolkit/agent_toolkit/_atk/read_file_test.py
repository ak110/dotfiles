"""公開本文読取で文字と改行を失わず、応答予算と継続位置を保つ。"""

import json
import pathlib

import pytest

from agent_toolkit import atk


@pytest.mark.parametrize(
    "text",
    ["日本語\r\n" * 3000, '{"値":"' + "長" * 12000 + '"}', '\x00\t"\\\n' * 3000, "", "\ufeff本文"],
    ids=["japanese-crlf", "single-line-json", "controls", "empty", "bom"],
)
def test_read_file_cli_roundtrips_utf8_with_bounded_complete_responses(
    text: str, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """返却の位置だけで終端へ進み、元のバイト列を復元できる。"""
    path = tmp_path / "本文.txt"
    path.write_bytes(text.encode("utf-8"))
    position = 0
    parts: list[str] = []
    while True:
        with pytest.raises(SystemExit, match="0"):
            atk.main(["read-file", "--start", str(position), "--", str(path)])
        captured = capsys.readouterr()
        assert not captured.err
        assert len(captured.out.encode("utf-8")) <= 4096
        response = json.loads(captured.out)
        assert response["start"] == position
        assert response["end"] == position + len(response["text"])
        parts.append(response["text"])
        if response["eof"]:
            assert response["next"] is None
            assert response["end"] == len(text)
            break
        assert response["next"] == response["end"] > position
        position = response["next"]
    assert "".join(parts).encode("utf-8") == path.read_bytes()
    with pytest.raises(SystemExit, match="0"):
        atk.main(["read-file", "--start", str(len(text)), "--", str(path)])
    assert json.loads(capsys.readouterr().out)["text"] == ""


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
