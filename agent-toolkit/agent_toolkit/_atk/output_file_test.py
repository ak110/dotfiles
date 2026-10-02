"""標準出力のファイル保存処理を検証する。"""

from __future__ import annotations

import argparse
import json
import pathlib

import pytest

from agent_toolkit import atk  # noqa: E402  # pylint: disable=wrong-import-position
from agent_toolkit._atk import output_file  # noqa: E402  # pylint: disable=wrong-import-position


def test_redirect_writes_stdout_to_file_and_prints_path_and_line_count(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output_path = tmp_path / "output.txt"

    with output_file.redirect(output_path):
        print("1行目")
        print("2行目")

    assert output_path.read_text(encoding="utf-8") == "1行目\n2行目\n"
    assert capsys.readouterr().out == f"保存先: {output_path.resolve()}\n行数: 2\n"


def test_relative_output_file_is_rejected(tmp_path: pathlib.Path) -> None:
    parser = argparse.ArgumentParser()
    output_file.add_output_file_arg(parser)
    assert parser.parse_args(["--output-file", "relative.txt"]).output_file == pathlib.Path("relative.txt")

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["plans", "list", "--output-file", "relative.txt"], home=tmp_path)

    assert exc_info.value.code == 2


def test_redirect_reports_saved_output_when_system_exit_propagates(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output_path = tmp_path / "system-exit.txt"

    with pytest.raises(SystemExit) as exc_info, output_file.redirect(output_path):
        print("終了前")
        raise SystemExit(7)

    assert exc_info.value.code == 7
    assert output_path.read_text(encoding="utf-8") == "終了前\n"
    assert capsys.readouterr().out == f"保存先: {output_path.resolve()}\n行数: 1\n"


def _run_atk(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int | str | None, str, str]:
    """`atk`を利用者が使うコマンドとして実行し、終了コード、標準出力および標準エラーを返す。"""
    with pytest.raises(SystemExit) as exc_info:
        atk.main(argv)
    captured = capsys.readouterr()
    return exc_info.value.code, captured.out, captured.err


@pytest.fixture
def _long_output_argv(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """managed-tempをテスト内へ隔離し、16384バイトを超える標準出力を生む`atk run-script`の引数を返す。"""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-app-data"))
    transcript = tmp_path / "transcript.jsonl"
    entries = [
        {"type": "user", "message": {"role": "user", "content": f"検索語{index} " + "本文" * 600}} for index in range(20)
    ]
    transcript.write_text("\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries) + "\n", encoding="utf-8")
    return ["run-script", "session-review-evidence", "--", str(transcript), "--grep", "検索語"]


def test_agent_environment_auto_saves_long_output_to_new_files(
    _long_output_argv: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """エージェント環境の長い標準出力は、実行ごとに新しいファイルへ全量を保存して要約行だけを返す。

    ユーザーが直接呼んだ場合と短い出力はそのまま表示する。保存先を再利用すると先の結果を上書きし、
    ユーザーの実行まで要約行へ置き換えると、端末で結果を読めなくなる。
    """
    code, direct_output, direct_error = _run_atk(_long_output_argv, capsys)
    assert code == 0
    assert len(direct_output.encode("utf-8")) > output_file.AUTO_SAVE_THRESHOLD_BYTES
    assert "次の操作: " not in direct_error

    monkeypatch.setenv("CLAUDECODE", "1")
    saved_paths = []
    for _ in range(2):
        code, output, error = _run_atk(_long_output_argv, capsys)
        assert code == 0
        assert any(line.startswith("次の操作: ") and "保存先" in line for line in error.splitlines())
        saved_line, count_line = output.splitlines()
        saved = pathlib.Path(saved_line.removeprefix("保存先: "))
        assert saved_line.startswith("保存先: ")
        assert count_line == f"行数: {len(direct_output.splitlines())}"
        assert saved.read_text(encoding="utf-8") == direct_output
        saved_paths.append(saved)
    assert saved_paths[0] != saved_paths[1]

    code, short_output, _short_error = _run_atk([*_long_output_argv[:-1], "検索語3 "], capsys)
    assert code == 0
    assert not short_output.startswith("保存先: ")
    assert '"kind": "match"' in short_output


def test_auto_save_writes_full_output_when_directory_cannot_be_created(capsys: pytest.CaptureFixture[str]) -> None:
    """保存先を作成できない場合は、警告を標準エラーへ書いて全量を標準出力へ残す。"""

    def fail() -> pathlib.Path:
        raise OSError("作成できない")

    text = "x" * (output_file.AUTO_SAVE_THRESHOLD_BYTES + 1)
    with pytest.raises(SystemExit) as exc_info, output_file.auto_save(fail):
        print(text)
        raise SystemExit(5)

    assert exc_info.value.code == 5
    captured = capsys.readouterr()
    assert captured.out == text + "\n"
    assert "作成できない" in captured.err
    assert "次の操作: 対応不要（処理は継続した）" in captured.err
