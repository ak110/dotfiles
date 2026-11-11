"""文体検査の主題別契約を検証する。"""

from __future__ import annotations

import json
import pathlib
import subprocess
from collections.abc import Callable

import check_agent_doc_tone
import pytest
from _scripts_test_helpers import _DENSITY_BODY, _write


def test_split_sentences_excludes_non_prose_blocks() -> None:
    """frontmatter・コードブロック・見出し・表・HTMLコメントを文数から除く。"""
    text = (
        "---\n"
        "title: メタ情報\n"
        "---\n"
        "\n"
        "# 見出し\n"
        "\n"
        "<!-- 注記の文である。 -->\n"
        "本文の1文目である。本文の2文目である。\n"
        "\n"
        "```python\n"
        "print('コード中の文である。')\n"
        "```\n"
        "\n"
        "| 列 | 値 |\n"
        "| --- | --- |\n"
        "| 表の中の文である。 | 値 |\n"
        "\n"
        "- 箇条書きの文である。\n"
    )

    sentences = check_agent_doc_tone.split_sentences(text)

    assert sentences == ["本文の1文目である。", "本文の2文目である。", "箇条書きの文である。"]


@pytest.mark.parametrize(
    ("body", "expected", "lines", "negative"),
    [
        ("- 値は変えない\n- 値を記録する\n", ["値は変えない", "値を記録する"], [1, 2], 1),
        ("値は変えない\n\n次の段落を読む\n", ["値は変えない", "次の段落を読む"], [1, 3], 1),
        ("段落の文が\n折り返して続く。\n", ["段落の文が折り返して続く。"], [2], 0),
        ("- 値は変え\n  ない\n- 結果を記録する\n", ["値は変えない", "結果を記録する"], [2, 3], 1),
        ("> 値は変えない\n>\n> 結果を読む\n", ["値は変えない", "結果を読む"], [1, 3], 1),
        ("値は変えない\n---\n\n本文を読む\n", ["本文を読む"], [4], 0),
        ("    値は変えない\n\n次の段落を読む\n", ["次の段落を読む"], [3], 0),
        (
            "- 値は変えない\n  - 子の値を読む\n\n次の段落を読む\n",
            ["値は変えない", "子の値を読む", "次の段落を読む"],
            [1, 2, 4],
            1,
        ),
    ],
    ids=[
        "items",
        "paragraphs",
        "wrapped-paragraph",
        "wrapped-item",
        "quote",
        "setext-heading",
        "indented-code",
        "nested-items",
    ],
)
def test_report_counts_markdown_blocks_and_preserves_physical_positions(
    tmp_path: pathlib.Path,
    host_environ: Callable[[], dict[str, str]],
    body: str,
    expected: list[str],
    lines: list[int],
    negative: int,
) -> None:
    """公開reportの文数と否定形終端にブロック末を含め、折返しの文末は物理行を指す。"""
    path = _write(tmp_path, body, name="AGENTS.md")
    arguments = ["uv", "run", "--frozen", "python", str(pathlib.Path(check_agent_doc_tone.__file__)), "--report", str(path)]

    result = subprocess.run(
        arguments, capture_output=True, text=True, encoding="utf-8", timeout=30, env=host_environ(), check=False
    )

    print(
        json.dumps(
            {"argv": arguments, "exit": result.returncode, "stdout": result.stdout, "stderr": result.stderr}, ensure_ascii=False
        )
    )
    assert result.returncode == 0, result.stderr
    assert not result.stderr
    assert result.stdout.splitlines()[0].split("\t") == ["文数", "否定形終端", "当該", "ファイル"]
    assert result.stdout.splitlines()[1].split("\t") == [str(len(expected)), str(negative), "0", str(path)]
    sentences = check_agent_doc_tone.sentences_with_positions(body)
    assert [sentence.text for sentence in sentences] == expected
    assert [sentence.line for sentence in sentences] == lines
    assert all(sentence.excerpt in body.splitlines()[sentence.line - 1] for sentence in sentences)


def test_metrics_count_each_indicator(tmp_path: pathlib.Path) -> None:
    """否定形終端と「当該」をそれぞれ数える。"""
    text = "当該対象は変更しない。当該値を根拠にしない。当該条件だけを判定しない。肯定形の文である。\n"

    metrics = check_agent_doc_tone.Metrics(_write(tmp_path, text), text)

    assert metrics.sentences == 4
    assert metrics.negative_endings == 3
    assert metrics.subject_words == 3


@pytest.mark.parametrize(
    "data",
    [
        "```text\n当該の検出データ。\n```\n",
        "    当該のコードは変更しない。\n",
        "<!-- 当該のコメントは変更しない。 -->\n",
        "# 当該の見出し\n",
        "| 値 |\n| --- |\n| 当該の表 |\n",
        "---\ntitle: 当該の属性\n---\n",
    ],
    ids=["fence", "indented-code", "comment", "heading", "table", "frontmatter"],
)
def test_density_keeps_detection_data_out_of_rewrite_candidates(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], data: str
) -> None:
    """本文以外の対象語だけを変えても密度と診断は変わらず、本文の出現だけを編集候補にする。"""
    prose = "本文を読む。\n" * 20
    for prefix in (data, data.replace("当該", "別の")):
        body = prefix + "\n" + prose
        path = _write(tmp_path, body)
        assert check_agent_doc_tone.main([str(path)]) == 0
        assert capsys.readouterr().err == ""
        metrics = check_agent_doc_tone.Metrics(path, body)
        assert metrics.sentences == 20
        assert metrics.subject_words == 0
        assert metrics.negative_endings == 0

    body = data + "\n" + "当該の対象を読む。当該の値を読む。\n" + prose
    path = _write(tmp_path, body)
    assert check_agent_doc_tone.main([str(path)]) == 1
    diagnostics = _diagnostic_lines(capsys.readouterr().err, path)
    counted = [(line, message) for line, message in diagnostics if "数えた「当該」: " in message]
    prose_line = len(data.splitlines()) + 2
    assert [line for line, _ in counted] == [prose_line, prose_line]
    assert all("読む" in message for _, message in counted)


def test_density_preserves_inline_code_data(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """本文のインラインコード内の検出データを密度に含めない。"""
    body = "`当該`を記録する。\n" * 20
    assert check_agent_doc_tone.main([str(_write(tmp_path, body))]) == 0
    assert capsys.readouterr().err == ""


def test_density_counts_quote_paragraphs_in_every_indicator(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """引用内の本文を文数・否定形終端・対象語の全てへ含め、保存データを除く。"""
    body = "> 当該の値は変更しない\n>\n> 結果を読む\n\n" + "本文を読む。\n" * 18
    body += "\n```text\n当該のデータは変更しない。\n```\n"
    path = _write(tmp_path, body)
    assert check_agent_doc_tone.main(["--report", str(path)]) == 0
    report = capsys.readouterr()
    assert not report.err
    assert report.out.splitlines()[1].split("\t") == ["20", "1", "1", str(path)]
    assert check_agent_doc_tone.main([str(path)]) == 1
    counted = _diagnostic_lines(capsys.readouterr().err, path)
    assert [(line, message) for line, message in counted if "数えた「当該」" in message] == [
        (1, "「当該」の出現率の超過で数えた「当該」: 「当該の値は変更しない」。指す対象を直接書く平易な語へ書き換える候補。")
    ]


def test_short_document_skips_ratio_thresholds(tmp_path: pathlib.Path) -> None:
    """文数が20未満のファイルへは割合の閾値を適用しない。"""
    text = "当該対象は変更しない。\n"

    metrics = check_agent_doc_tone.Metrics(_write(tmp_path, text), text)

    assert metrics.sentences < 20
    assert not metrics.violations()


def test_ratio_thresholds_apply_to_long_document(tmp_path: pathlib.Path) -> None:
    """文数が20以上のファイルでは否定形終端率と「当該」の出現率を判定する。"""
    text = "当該対象は変更しない。" * 20

    metrics = check_agent_doc_tone.Metrics(_write(tmp_path, text), text)
    problems = metrics.violations()

    assert metrics.sentences == 20
    assert any("否定形終端率" in problem for problem in problems)
    assert any("「当該」の出現率" in problem for problem in problems)


def test_main_reports_violation_and_returns_one(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """閾値を超えたファイルを標準出力へ書いて終了コード1で終わる。"""
    path = _write(tmp_path, "当該対象は変更しない。" * 20)

    assert check_agent_doc_tone.main([str(path)]) == 1

    captured = capsys.readouterr().err
    assert str(path) in captured
    assert "否定形終端率" in captured


def _diagnostic_lines(output: str, path: pathlib.Path) -> list[tuple[int, str]]:
    """標準エラーの診断から対象ファイルの行番号と本文を取り出し、全件が`error`の重大度を持つことを確かめる。"""
    diagnostics: list[tuple[int, str]] = []
    for row in output.splitlines():
        line, _, rest = row.removeprefix(f"{path}:").partition(": ")
        severity, _, message = rest.partition(": ")
        assert severity == "error", row
        diagnostics.append((int(line), message))
    return diagnostics


def test_density_lists_every_counted_element_with_physical_line(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """密度の指標ごとに、数えた要素を入力ファイルの物理行の位置付きで全件列挙する。"""
    path = _write(tmp_path, _DENSITY_BODY)

    assert check_agent_doc_tone.main([str(path)]) == 1

    diagnostics = _diagnostic_lines(capsys.readouterr().err, path)
    negative = [(line, message) for line, message in diagnostics if "否定形で終わる文: " in message]
    assert [line for line, _ in negative] == [7, 9, 10]
    assert "「対象は変更しない。」" in negative[0][1]
    assert "「ない。」" in negative[1][1]
    assert "「値は書かない。」" in negative[2][1]
    subject = [line for line, message in diagnostics if "数えた「当該」: " in message]
    assert subject == [11, 11]
    summaries = [message for line, message in diagnostics if line == 1]
    assert any(
        "否定形終端率 13.6%（上限 8%、3/22文）" in message and "少なくとも2件を書き換える" in message for message in summaries
    )
    assert any("「当該」の出現率 9.1%（上限 3%、2回/22文）" in message and "少なくとも2件" in message for message in summaries)


def test_density_rewriting_required_sentences_clears_the_metric(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """要約が示す最少件数の文を、文数を変えずに肯定形へ書き換えると、その指標の診断は出力されない。"""
    body = _DENSITY_BODY.replace("対象は変更しない。", "対象を保つ。").replace("値は書かない。", "値を保つ。")
    path = _write(tmp_path, body)

    check_agent_doc_tone.main([str(path)])

    output = capsys.readouterr().err
    assert "否定形" not in output
    assert "数えた「当該」" in output


@pytest.mark.parametrize(
    "body",
    [_DENSITY_BODY.replace("観測した値を記録する。\n" * 15, ""), "観測した値を記録する。" * 30],
    ids=["below-min-sentences", "within-limit"],
)
def test_density_elements_are_not_listed_within_threshold(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], body: str
) -> None:
    """文数20未満のファイルと上限以下のファイルでは、要素ごとの診断を出力しない。"""
    assert check_agent_doc_tone.main([str(_write(tmp_path, body))]) == 0
    assert capsys.readouterr().err == ""


def test_main_accepts_compliant_document(tmp_path: pathlib.Path) -> None:
    """閾値を満たすファイルは終了コード0で終わる。"""
    path = _write(tmp_path, "対象を実際に動かしてから確定する。観測した値を計画へ書く。\n")

    assert check_agent_doc_tone.main([str(path)]) == 0


def test_report_mode_prints_metrics_without_judging(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`--report`は判定せず、3指標とファイル名を表で出力する。"""
    path = _write(tmp_path, "その値を根拠にしない。\n")

    assert check_agent_doc_tone.main(["--report", str(path)]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split("\t") == ["文数", "否定形終端", "当該", "ファイル"]
    assert lines[1].split("\t") == ["1", "1", "0", str(path)]


@pytest.mark.parametrize("name", ["tone-examples.md", "tone-examples-llm-tone.md", "textlint-violations.md"])
def test_norm_example_paths_are_checked(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], name: str) -> None:
    """例を置く各ファイルも密度の判定へ到達し、超過した指標を報告する。"""
    owner = (
        "agent-toolkit/skills/delegation/references"
        if name == "textlint-violations.md"
        else "agent-basics/skills/writing-standards/references"
    )
    example = tmp_path / owner / name
    example.parent.mkdir(parents=True)
    example.write_text("その値を根拠にしない。\n" * 20, encoding="utf-8")

    assert check_agent_doc_tone.main([str(example)]) == 1
    assert "否定形終端率" in capsys.readouterr().err


def test_term_scope_preserves_existing_density_check(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """語の判定を限定した場合も、従来の文体の密度は同じ対象で判定する。"""
    path = _write(tmp_path, "当該対象は変更しない。" * 20)
    scope = tmp_path / "separate"
    scope.mkdir()

    assert check_agent_doc_tone.main(["--term-root", str(scope), str(path)]) == 1
    assert "否定形終端率" in capsys.readouterr().err


def test_read_error_is_a_positioned_diagnostic(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """読み取れない入力は対象パスと代替位置を持つ診断で終了コード2を返す。"""
    path = tmp_path / "missing.md"

    assert check_agent_doc_tone.main([str(path)]) == 2

    assert capsys.readouterr().err.startswith(f"{path}:1: error: 入力を読み取れない:")


def test_parse_error_is_a_positioned_diagnostic(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """構文不正は対象パスと利用可能な位置を持つ診断で終了コード2を返す。"""
    path = _write(tmp_path, "{\n", name="config.json")

    assert check_agent_doc_tone.main([str(path)]) == 2

    diagnostic = capsys.readouterr().err
    assert diagnostic.startswith(f"{path}:2: error: 入力の構文を読み取れない:")
