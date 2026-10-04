"""生成側の両出力保存と、保存不能なwaitが回収を始めないことを検証する。"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import output_file


@pytest.mark.parametrize("stderr", [False, True])
@pytest.mark.parametrize("length", [16383, 16384, 16385])
def test_each_stream_preserves_boundary_and_entire_output(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], stderr: bool, length: int
) -> None:
    """両ストリームを混ぜず、UTF-8境界の上下で同じ全量を受領する。"""
    text = "x" * length
    with output_file.auto_save(lambda: tmp_path):
        (sys.stderr if stderr else sys.stdout).write(text)
    captured = capsys.readouterr()
    actual = captured.err if stderr else captured.out
    assert (captured.out if stderr else captured.err) == ""
    if length <= output_file.AUTO_SAVE_THRESHOLD_BYTES:
        assert actual == text
    else:
        prefix = "標準エラー保存先: " if stderr else "保存先: "
        path = pathlib.Path(actual.splitlines()[0].removeprefix(prefix))
        assert path.is_absolute() and path.read_text(encoding="utf-8") == text


def test_forced_save_is_open_before_result_and_keeps_exception(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """小さいwaitでも回収前に開き、例外時の結果をファイルへ保持する。"""
    observed = []
    with (
        pytest.raises(SystemExit) as raised,
        output_file.auto_save(
            lambda: tmp_path, force_stdout=True, after_save=lambda path: observed.append(path.read_text(encoding="utf-8"))
        ),
    ):
        assert (tmp_path / "output.txt").is_file()
        print('{"session_id":"child","status":"completed"}', flush=True)
        raise SystemExit(7)
    assert raised.value.code == 7
    assert observed == ['{"session_id":"child","status":"completed"}\n']
    assert capsys.readouterr().out.startswith("保存先: ")


def test_forced_save_failure_does_not_consume_result(capsys: pytest.CaptureFixture[str]) -> None:
    """保存準備が失敗した場合、本体と結果回収へ到達しない。"""
    consumed = False

    def fail() -> pathlib.Path:
        raise OSError("保存先を作成できない")

    with pytest.raises(SystemExit) as raised, output_file.auto_save(fail, force_stdout=True):
        consumed = True
    assert raised.value.code == 1 and not consumed
    assert "呼び出しを開始しない" in capsys.readouterr().err


def test_general_save_failure_keeps_both_outputs_and_exit(capsys: pytest.CaptureFixture[str]) -> None:
    """一般の取得では保存失敗を示して全量と元の終了コードを維持する。"""

    def fail() -> pathlib.Path:
        raise OSError("作成できない")

    text = "x" * (output_file.AUTO_SAVE_THRESHOLD_BYTES + 1)
    with pytest.raises(SystemExit) as raised, output_file.auto_save(fail):
        print(text)
        print(text, file=sys.stderr)
        raise SystemExit(5)
    assert raised.value.code == 5
    captured = capsys.readouterr()
    assert captured.out == text + "\n"
    assert text in captured.err and "作成できない" in captured.err and "次の操作:" in captured.err


def test_multibyte_output_uses_utf8_size_and_preserves_callback(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """文字数が小さくてもUTF-8の長い本文を保存し、保存後の消費側へ同じ全量を渡す。"""
    text = "診断" * 4000
    observed = []
    with output_file.auto_save(lambda: tmp_path, after_save=lambda path: observed.append(path.read_text(encoding="utf-8"))):
        sys.stdout.write(text)
    assert observed == [text]
    assert capsys.readouterr().out.startswith("保存先: ")


def run_atk(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[object, str, str]:
    """公開mainから実際の終了と両出力を取得する。"""
    try:
        atk.main(argv)
    except SystemExit as error:
        code = error.code
    else:
        code = 0
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.mark.parametrize("agent", [False, True])
def test_evidence_receives_file_without_size_prediction(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], agent: bool
) -> None:
    """短い証拠でもエージェントは新規ファイルを受け取り、人の端末は直接本文を読む。"""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    if agent:
        monkeypatch.setenv("CLAUDECODE", "1")
    trace = tmp_path / "trace.jsonl"
    trace.write_text(json.dumps({"type": "user", "message": {"content": "検索語"}}) + "\n", encoding="utf-8")
    argv = ["run-script", "session-review-evidence", "--", str(trace), "--grep", "検索語"]
    code, output, error = run_atk(argv, capsys)
    assert code == 0, error
    if agent:
        path = pathlib.Path(output.splitlines()[0].removeprefix("保存先: "))
        output = path.read_text(encoding="utf-8")
        assert path.is_absolute()
    assert '"kind": "match"' in output


@pytest.mark.parametrize("argv", [["--help"], ["info"], ["commit", "--help"], ["setup-project", "--help"]])
def test_help_and_early_returns_use_same_capture(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    """有限終了のヘルプ・診断の実出力を小さい境界で保存し、早期returnを含める。"""
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(output_file, "AUTO_SAVE_THRESHOLD_BYTES", 32)
    code, output, error = run_atk(argv, capsys)
    assert code == 0, error
    saved = pathlib.Path(output.splitlines()[0].removeprefix("保存先: "))
    assert saved.is_absolute() and len(saved.read_text(encoding="utf-8")) > 32


@pytest.mark.parametrize(
    "argv",
    [
        ["wi", "list"],
        ["wi", "show", "20261004-004833-001.md"],
        ["wi", "grep", "対象"],
        ["agents", "wait"],
        ["plans", "list"],
        ["managed-temp", "list"],
        ["review-table", "show", "/some/review.tsv"],
        ["review-audit", "list", "--repo", "/some/repo"],
        ["run-script", "session-review-evidence", "--"],
    ],
)
def test_removed_output_option_is_rejected(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], argv: list[str]) -> None:
    """全受理定義の撤去後は、操作を開始せず指定先へ書き込まない。"""
    destination = tmp_path / "old.txt"
    code, _output, error = run_atk([*argv, "--output-file", str(destination)], capsys)
    assert code == 2 and "--output-file" in error
    assert not destination.exists()
