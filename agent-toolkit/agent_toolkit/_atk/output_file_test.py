"""atkが標準出力と標準エラーを分けて保存することと、保存先を用意できないwaitが結果の回収を始めないことを検証する。"""

from __future__ import annotations

import io
import json
import pathlib
import sys

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import output_file


def test_saved_streams_report_path_and_line_count(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """標準出力と標準エラーの保存先と行数だけを示し、示した行数の範囲で保存した全文を読める。"""
    bodies = ("標準出力\r\n" * 3000, "標準エラー\n" * 3000)
    with output_file.auto_save(lambda: tmp_path):
        sys.stdout.write(bodies[0])
        sys.stderr.write(bodies[1])
    captured = capsys.readouterr()
    for output, expected, prefix in zip((captured.out, captured.err), bodies, ("", "標準エラー"), strict=True):
        lines = output.splitlines()
        assert len(lines) == 2
        assert lines[0].startswith(f"{prefix}保存先: ")
        assert lines[1] == f"{prefix}行数: 3000"
        saved = pathlib.Path(lines[0].removeprefix(f"{prefix}保存先: "))
        assert saved.read_bytes() == expected.encode("utf-8")


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
    """出力が小さいwaitでも結果の回収前に保存先を開き、例外で終わるまでの結果をファイルへ保持する。

    非0で終わった本体の出力には成功時の要約を適用せず、保存先と行数だけを示す。
    """
    summarized = []
    with (
        pytest.raises(SystemExit) as raised,
        output_file.auto_save(lambda: tmp_path, force_stdout=True, after_save=summarized.append),
    ):
        assert (tmp_path / "output.txt").is_file()
        print('{"session_id":"child","status":"completed"}', flush=True)
        raise SystemExit(7)
    assert raised.value.code == 7
    assert (tmp_path / "output.txt").read_text(encoding="utf-8") == '{"session_id":"child","status":"completed"}\n'
    assert not summarized
    assert capsys.readouterr().out.splitlines()[1:] == ["行数: 1"]


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


@pytest.mark.parametrize("subcommand", ["add", "edit"])
@pytest.mark.parametrize("agent", [False, True])
def test_main_draft_output_preserves_large_raw_text(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, subcommand: str, agent: bool
) -> None:
    """公開mainのadd/edit dry-runで大容量原稿を直接表示し、Windows相当の改行変換も抑える。"""
    opening = "# 表題\r\n\r\n" + "原文\r\n単独CR\r末尾空白  \n" * 2000
    body = opening + "## 反映内容と反映先\n対象へ反映する。\n"
    draft = tmp_path / "draft.md"
    draft.write_bytes(body.encode("utf-8"))
    notes = tmp_path / "notes"
    entry = notes / "2026/10/20261031-000000-001.md"
    entry.parent.mkdir(parents=True)
    entry.write_text(
        "---\nstate: hold\ntype: awi\ntarget_repo: github.com/example/test\nsource: test\n---\n\n# 元の表題\n",
        encoding="utf-8",
    )
    original = entry.read_bytes()
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
    if agent:
        monkeypatch.setenv("AI_AGENT", "1")
    argv = [
        "wi",
        subcommand,
        "--dry-run",
        "--source=test",
        "--target-repo=github.com/example/test",
        "--body-file",
        str(draft),
    ]
    if subcommand == "edit":
        argv.remove("--source=test")
        argv.append(entry.name)
    buffer = io.BytesIO()
    with io.TextIOWrapper(buffer, encoding="utf-8", newline="\r\n") as stream, monkeypatch.context() as patch:
        patch.setattr(sys, "stdout", stream)
        with pytest.raises(SystemExit) as raised:
            atk.main(argv)
        stream.flush()
        actual = buffer.getvalue()
    assert raised.value.code == 0
    assert opening.encode("utf-8") in actual
    assert not actual.startswith("保存先: ".encode())
    saved = next(
        line.removeprefix("冒頭保存先: ") for line in actual.decode("utf-8").splitlines() if line.startswith("冒頭保存先: ")
    )
    assert pathlib.Path(saved).read_bytes() == opening.encode("utf-8")
    assert draft.read_bytes() == body.encode("utf-8")
    assert entry.read_bytes() == original


@pytest.mark.parametrize("agent", [False, True])
@pytest.mark.parametrize(
    ("query", "passes_file"),
    [
        (["--detail", "1"], False),
        (["--grep", "検索語"], False),
        (["--user-events", "--since", "2026-09-01T00:00:00Z"], True),
        (["--user-events"], True),
    ],
)
def test_evidence_saves_only_output_passed_as_file(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    agent: bool,
    query: list[str],
    passes_file: bool,
) -> None:
    """エージェントは逐語引用の出所として渡す`--user-events`だけを新規ファイルで受け取り、短い単発照会は直接読む。

    単発照会まで保存すると短い結果を読むたびに往復が増え、`--user-events`を直接表示すると
    WI投入担当へ渡す出所ファイルが無くなる。人の端末はどの照会も直接本文を読む。
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    if agent:
        monkeypatch.setenv("CLAUDECODE", "1")
    trace = tmp_path / "trace.jsonl"
    entry = {"type": "user", "timestamp": "2026-10-01T00:00:00Z", "message": {"role": "user", "content": "検索語"}}
    trace.write_text(json.dumps(entry, ensure_ascii=False) + "\n", encoding="utf-8")
    code, output, error = run_atk(["run-script", "session-review-evidence", "--", str(trace), *query], capsys)
    assert code == 0, error
    assert output.startswith("保存先: ") is (agent and passes_file)
    assert ("発話: " in output) is (agent and passes_file)
    if agent and passes_file:
        summary = output.splitlines()
        path = pathlib.Path(summary[0].removeprefix("保存先: "))
        assert path.is_absolute()
        output = path.read_text(encoding="utf-8")
        # 保存後の表示は保存ファイルの発話行と同じ順・同じ記録位置を示し、呼び出し元は保存先を開かずに出所を選べる。
        users = [event for event in map(json.loads, output.splitlines()) if event["kind"] == "user"]
        assert summary[2:] == [f"発話: record={event['record']} line={event['line']} {event['text']}" for event in users]
    assert "検索語" in output


@pytest.mark.parametrize("mode", ["--grep", "--tool-calls"])
def test_evidence_saved_matches_list_positions_and_heads(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], mode: str
) -> None:
    """公開照会の長い結果だけに位置と冒頭を付け、全量JSONLと元の順序を保つ。"""
    monkeypatch.setenv("AI_AGENT", "1")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    trace = tmp_path / "trace.jsonl"
    # 既存のClaude Code記録fixtureと同じmessage/tool_use形式で入力境界を通す。
    command = "echo 検索語" + ("\n" if mode == "--tool-calls" else "") + "長" * 1000
    entries = [
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": f"call-{index}", "name": "Bash", "input": {"command": command + "\n末尾"}}
                ],
            },
        }
        for index in range(6)
    ]
    trace.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries), encoding="utf-8")
    query = [mode, "検索語"] if mode == "--grep" else [mode]
    code, output, error = run_atk(["run-script", "session-review-evidence", "--", str(trace), *query], capsys)
    assert code == 0, error
    lines = output.splitlines()
    assert lines[0].startswith("保存先: ")
    saved = pathlib.Path(lines[0].removeprefix("保存先: "))
    events = [json.loads(line) for line in saved.read_text(encoding="utf-8").splitlines()]
    kind = "match" if mode == "--grep" else "tool-call"
    matches = [event for event in events if event["kind"] == kind]
    assert len(matches) == 6
    expected = []
    for event in matches:
        fields = "".join(f" {key}={event[key]}" for key in ("role", "tool") if key in event)
        head = "echo 検索語" + (" " if mode == "--tool-calls" else "") + "長" * (151 if mode == "--tool-calls" else 152) + "…"
        expected.append(f"一致: record={event['record']} line={event['line']} kind={kind}{fields} {head}")
    assert lines[2:] == expected
    assert [event["text"] for event in matches] == [command + ("\n末尾" if mode == "--tool-calls" else "")] * 6


@pytest.mark.parametrize("mode", ["--grep", "--tool-calls"])
def test_evidence_saved_many_matches_show_bounded_summary(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], mode: str
) -> None:
    """多数の一致を保存した後は件数の内訳と先頭20件だけを返し、全一致を保存ファイルへ残す。"""
    monkeypatch.setenv("AI_AGENT", "1")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    trace = tmp_path / "trace.jsonl"
    entries = [
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": f"call-{index}",
                        "name": "Bash",
                        "input": {"command": f"echo 検索語{index} " + "長" * 300},
                    }
                ],
            },
        }
        for index in range(500)
    ]
    trace.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries), encoding="utf-8")
    query = [mode, "検索語"] if mode == "--grep" else [mode]
    code, output, error = run_atk(["run-script", "session-review-evidence", "--", str(trace), *query], capsys)
    assert code == 0, error
    assert len(output.encode("utf-8")) <= output_file.AUTO_SAVE_THRESHOLD_BYTES
    lines = output.splitlines()
    saved = pathlib.Path(lines[0].removeprefix("保存先: "))
    kind = "match" if mode == "--grep" else "tool-call"
    events = [json.loads(line) for line in saved.read_text(encoding="utf-8").splitlines()]
    matches = [event for event in events if event["kind"] == kind]
    assert len(matches) == 500
    assert lines[2] == "一致: 全500件（保存順の先頭20件を表示）"
    assert lines[3].startswith("内訳: kind=" + kind)
    assert lines[4].startswith("記録別: ")
    listed = [line for line in lines if line.startswith("一致: record=")]
    assert len(listed) == 20
    assert listed[0].startswith(f"一致: record={matches[0]['record']} line={matches[0]['line']} kind={kind}")
    assert lines[-1].startswith("残り480件は保存先のJSONL（1行1イベント）にある。") and str(saved) in lines[-1]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        (["--grep", "存在しない"], ["一致: 0件"]),
        (["--tool-calls"], ["一致: 0件"]),
        (["--grep", "検索語", "--summary-only"], []),
        (["--tool-calls", "--summary-only"], []),
        (["--fixed-string", "検索語"], []),
    ],
)
def test_evidence_saved_empty_and_summary_modes(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    query: list[str],
    expected: list[str],
) -> None:
    """保存量の境界だけを下げて、空結果と詳細を持たない照会の表示を区別する。"""
    monkeypatch.setenv("AI_AGENT", "1")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(output_file, "AUTO_SAVE_THRESHOLD_BYTES", 1)
    trace = tmp_path / "trace.jsonl"
    trace.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "検索語"}}), encoding="utf-8")
    code, output, error = run_atk(["run-script", "session-review-evidence", "--", str(trace), *query], capsys)
    assert code == 0, error
    assert output.startswith("保存先: ")
    assert output.splitlines()[2:] == expected


def test_evidence_failure_preserves_output_and_exit_when_saved(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """失敗した照会の両ストリームと終了コードは保存前後で同じにする。"""
    monkeypatch.setenv("AI_AGENT", "1")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    argv = ["run-script", "session-review-evidence", "--", str(tmp_path / "missing.jsonl"), "--grep", "検索語"]
    code, output, error = run_atk(argv, capsys)
    assert code != 0
    assert output or error
    monkeypatch.setattr(output_file, "AUTO_SAVE_THRESHOLD_BYTES", 1)
    saved_code, saved_output, saved_error = run_atk(argv, capsys)
    assert saved_code == code
    for actual, expected, prefix in (
        (saved_output, output, ""),
        (saved_error, error, "標準エラー"),
    ):
        if not expected:
            assert not actual
            continue
        lines = actual.splitlines()
        assert len(lines) == 2
        path = pathlib.Path(lines[0].removeprefix(f"{prefix}保存先: "))
        assert path.read_text(encoding="utf-8") == expected


def test_forced_save_without_result_discards_only_failed_call(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """結果を書かずに非0で終わった強制保存は保存先を片付けて要約を出力せず、成功した空の結果は保存先を返す。"""
    discarded: list[pathlib.Path] = []
    failed_directory = tmp_path / "failed"
    failed_directory.mkdir()
    with (
        pytest.raises(SystemExit) as raised,
        output_file.auto_save(lambda: failed_directory, force_stdout=True, discard_directory=discarded.append),
    ):
        raise SystemExit(10)
    assert raised.value.code == 10
    assert discarded == [failed_directory.resolve()]
    assert not (failed_directory / "output.txt").exists()
    assert not capsys.readouterr().out

    succeeded_directory = tmp_path / "succeeded"
    succeeded_directory.mkdir()
    with output_file.auto_save(lambda: succeeded_directory, force_stdout=True, discard_directory=discarded.append):
        pass
    assert discarded == [failed_directory.resolve()]
    assert capsys.readouterr().out.startswith("保存先: ")


@pytest.mark.parametrize("argv", [["--help"], ["info"], ["commit", "--help"], ["setup-project", "--help"]])
def test_help_and_early_returns_use_same_capture(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    """有限で終了するヘルプ表示と、結果を表示して終わるサブコマンドの実出力も、小さい境界で同じく保存する。"""
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


def test_failed_user_events_query_shows_error_instead_of_zero_count(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """失敗した`--user-events`の照会は「発話: 0件」を出さず、エラーの本文と次の操作を表示して終了コードを保つ。

    成功して発話が無い照会は、従来どおり「発話: 0件」を表示する。
    """
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    missing = "00000000-0000-4000-8000-000000000000"
    argv = ["run-script", "session-review-evidence", "--", "--claude-session-id", missing, "--user-events"]
    code, output, _error = run_atk(argv, capsys)
    assert code == 2
    lines = output.splitlines()
    assert lines[0].startswith("保存先: ")
    assert "発話: 0件" not in output
    saved = [
        json.loads(line) for line in pathlib.Path(lines[0].removeprefix("保存先: ")).read_text(encoding="utf-8").splitlines()
    ]
    assert [event["kind"] for event in saved] == ["error"]
    assert lines[2:] == [f"失敗: {saved[0]['text']}", f"次の操作: {saved[0]['next_action']}"]

    trace = tmp_path / "trace.jsonl"
    entry = {
        "type": "assistant",
        "timestamp": "2026-10-01T00:00:00Z",
        "message": {"role": "assistant", "content": [{"type": "text", "text": "応答だけの記録"}]},
    }
    trace.write_text(json.dumps(entry, ensure_ascii=False) + "\n", encoding="utf-8")
    code, output, error = run_atk(["run-script", "session-review-evidence", "--", str(trace), "--user-events"], capsys)
    assert code == 0, error
    assert output.splitlines()[2:] == ["発話: 0件"]
