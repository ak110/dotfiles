"""公開CLIの本文読取が文字と改行を失わず、応答の予算と継続位置を保つことを検証する。"""

import json
import pathlib
import subprocess
import sys
from collections.abc import Callable, Iterator

import pytest

from agent_toolkit import atk
from agent_toolkit._atk.output_file import AUTO_SAVE_THRESHOLD_BYTES


def _parse_response(output: str) -> tuple[dict, str]:
    """最初のLFでメタデータと無加工本文を分ける。"""
    metadata, separator, body = output.partition("\n")
    assert separator
    return json.loads(metadata), body


def _read_chunks(path: pathlib.Path, budget: int | None, capsys: pytest.CaptureFixture[str]) -> Iterator[tuple[dict, str]]:
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
        assert len(captured.out.encode("utf-8")) <= (16384 if budget is None else budget)
        metadata, body = _parse_response(captured.out)
        assert "text" not in metadata
        assert metadata["start"] == position
        assert metadata["end"] == position + len(body)
        yield metadata, body
        if metadata["eof"]:
            assert metadata["next"] is None
            break
        assert metadata["next"] == metadata["end"] > position
        position = metadata["next"]


@pytest.mark.parametrize(
    "budget", [None, 4096, 12000, AUTO_SAVE_THRESHOLD_BYTES], ids=["default", "smaller", "explicit", "save-boundary"]
)
@pytest.mark.parametrize(
    "text",
    [
        "日本語\r\n" * 3000,
        '{"値":"' + "長" * 12000 + '"}',
        '\x00\t"\\\n' * 3000,
        '<tag attr="値">\\path</tag>',
        "",
        "\ufeff本文",
    ],
    ids=["japanese-crlf", "single-line-json", "controls", "xml-like", "empty", "bom"],
)
def test_read_file_cli_roundtrips_utf8_with_bounded_complete_responses(
    text: str, budget: int | None, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """返却の位置だけで終端へ進み、元のバイト列を復元できる。"""
    path = tmp_path / "本文.txt"
    path.write_bytes(text.encode("utf-8"))
    responses = list(_read_chunks(path, budget, capsys))
    assert responses[-1][0]["end"] == len(text)
    assert "".join(body for _, body in responses).encode("utf-8") == path.read_bytes()
    with pytest.raises(SystemExit, match="0"):
        atk.main(["read-file", "--start", str(len(text)), "--", str(path)])
    metadata, body = _parse_response(capsys.readouterr().out)
    assert metadata["eof"] is True and body == ""


def test_read_file_process_preserves_raw_utf8_bytes(tmp_path: pathlib.Path, host_environ: Callable[[], dict[str, str]]) -> None:
    """実際のCLIプロセスが改行とエスケープ文字を変換せず返す。"""
    path = tmp_path / "process.txt"
    body = '\ufeff{"path":"C:\\\\work"}\r\n<tag>末尾</tag>\n'.encode()
    path.write_bytes(body)

    environment = host_environ()
    environment["CODEX_CI"] = "1"
    completed = subprocess.run(
        [sys.executable, "-m", "agent_toolkit.atk", "read-file", "--", str(path)],
        cwd=pathlib.Path(__file__).parents[2],
        env=environment,
        capture_output=True,
        check=False,
    )

    metadata_line, separator, actual = completed.stdout.partition(b"\n")
    assert completed.returncode == 0 and not completed.stderr
    assert separator == b"\n" and actual == body
    assert json.loads(metadata_line) == {"start": 0, "end": len(body.decode()), "next": None, "eof": True}


def test_default_budget_reduces_cli_invocations_without_losing_text(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """同じ日本語本文を読む公開CLIの起動数を、予算だけ変えた対照で比較する。"""
    path = tmp_path / "日本語.txt"
    text = "日本語の本文\r\n" * 1400
    path.write_bytes(text.encode("utf-8"))
    counts: list[int] = []
    for budget in (4096, None):
        responses = list(_read_chunks(path, budget, capsys))
        assert "".join(body for _, body in responses).encode("utf-8") == path.read_bytes()
        counts.append(len(responses))
    assert counts[1] < counts[0]


@pytest.mark.parametrize("text", ["", "日", "\x00", "本文" * 500], ids=["empty", "japanese", "control", "long"])
@pytest.mark.parametrize("budget", [-1, 0, 32, AUTO_SAVE_THRESHOLD_BYTES + 1])
def test_budget_must_fit_a_complete_progressing_response(
    text: str, budget: int, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """本文を進められず位置だけを返す予算と、返却が自動保存の対象になる予算を、公開CLIで拒否する。"""
    path = tmp_path / "input.txt"
    path.write_bytes(text.encode("utf-8"))
    with pytest.raises(SystemExit, match="2"):
        atk.main(["read-file", "--max-bytes", str(budget), "--", str(path)])
    captured = capsys.readouterr()
    assert not captured.out
    assert "失敗:" in captured.err
    assert "次の操作:" in captured.err
    assert "--max-bytes" in captured.err


@pytest.mark.parametrize("text", ["", "日本語\r\n\ufeff", "長" * 12000], ids=["empty", "japanese-crlf-bom", "long-line"])
@pytest.mark.parametrize("budget", [None, 12000, 16384])
def test_start_beyond_end_is_eof(
    text: str, budget: int | None, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """末尾ちょうどと超過は同じ終端応答を返し、終端でも返却全体が予算に収まることを確かめる。"""
    path = tmp_path / "input.txt"
    path.write_bytes(text.encode("utf-8"))
    expected = {"start": len(text), "end": len(text), "next": None, "eof": True}
    for position in (len(text), len(text) + 1000000):
        options = [] if budget is None else ["--max-bytes", str(budget)]
        with pytest.raises(SystemExit, match="0"):
            atk.main(["read-file", "--start", str(position), *options, "--", str(path)])
        captured = capsys.readouterr()
        assert not captured.err
        metadata, body = _parse_response(captured.out)
        assert metadata == expected and body == ""
        size = len(captured.out.encode("utf-8"))
        assert size <= (16384 if budget is None else budget)
        for invalid_budget in (size - 1, 16385):
            with pytest.raises(SystemExit, match="2"):
                atk.main(["read-file", "--start", str(position), "--max-bytes", str(invalid_budget), "--", str(path)])
            captured = capsys.readouterr()
            assert not captured.out
            assert "返却予算" in captured.err
            assert "次の操作:" in captured.err


@pytest.mark.parametrize("case", ["relative", "negative", "missing", "non-utf8"])
def test_invalid_file_or_position_has_recovery(case: str, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """不正な入力で本文を返さず、直す入力を案内する。"""
    path = tmp_path / "input.txt"
    path.write_bytes(b"abc" if case != "non-utf8" else b"\xff")
    target = "relative.txt" if case == "relative" else str(tmp_path / "missing") if case == "missing" else str(path)
    start = "-1" if case == "negative" else "0"
    with pytest.raises(SystemExit, match="2"):
        atk.main(["read-file", "--start", start, "--", target])
    captured = capsys.readouterr()
    assert not captured.out
    assert "失敗:" in captured.err
    assert "次の操作:" in captured.err


def test_section_cli_returns_only_selected_scope(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """見出しを選択した公開操作は、他領域の本文を取得しない。"""
    path = tmp_path / "guidelines.md"
    path.write_text("# 基準\n冒頭\n\n## 共通\n共通本文\n\n## API\nAPI本文\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="0"):
        atk.main(["read-file", "--section", "共通", "--", str(path)])
    captured = capsys.readouterr()
    metadata, body = _parse_response(captured.out)
    assert metadata["section"] == "共通"
    assert metadata["eof"] is True
    assert body == "## 共通\n共通本文\n\n"
    assert not captured.err


def test_section_cli_continuation_uses_selected_text_positions(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """選択本文の位置を継続し、他領域を混ぜず末尾へ到達する。"""
    path = tmp_path / "long.md"
    selected = "## 共通\n" + "共通本文\n" * 100
    path.write_text("# 基準\n" + selected + "## 他領域\n別本文\n", encoding="utf-8")
    position = 0
    parts = []
    while True:
        with pytest.raises(SystemExit, match="0"):
            atk.main(["read-file", "--section", "共通", "--start", str(position), "--max-bytes", "200", "--", str(path)])
        captured = capsys.readouterr()
        assert not captured.err
        metadata, body = _parse_response(captured.out)
        parts.append(body)
        assert len(captured.out.encode()) <= 200
        if metadata["eof"]:
            break
        assert metadata["next"] > position
        position = metadata["next"]
    assert "".join(parts) == selected


def test_unknown_section_cli_is_actionable_error(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """存在しない見出しを全体取得へ切り替えず、修復可能なエラーにする。"""
    path = tmp_path / "guidelines.md"
    path.write_text("# 基準\n本文\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="2"):
        atk.main(["read-file", "--section", "不存在", "--", str(path)])
    captured = capsys.readouterr()
    assert not captured.out
    assert "一意" in captured.err
