"""`check_agent_doc_tone.py`の文の抽出、指標の計数及び閾値判定を検証する。"""

from __future__ import annotations

import pathlib

import check_agent_doc_tone
import pytest


def _write(tmp_path: pathlib.Path, body: str, *, name: str = "doc.md") -> pathlib.Path:
    """検査対象のMarkdownを作成し、そのパスを返す。"""
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


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


def test_metrics_count_each_indicator(tmp_path: pathlib.Path) -> None:
    """否定形終端と「当該」をそれぞれ数える。"""
    text = "当該対象は変更しない。当該値を根拠にしない。当該条件だけを判定しない。肯定形の文である。\n"

    metrics = check_agent_doc_tone.Metrics(_write(tmp_path, text), text)

    assert metrics.sentences == 4
    assert metrics.negative_endings == 3
    assert metrics.subject_words == 3


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

    captured = capsys.readouterr().out
    assert str(path) in captured
    assert "否定形終端率" in captured


def test_main_accepts_compliant_document(tmp_path: pathlib.Path) -> None:
    """閾値を満たすファイルは終了コード0で終わる。"""
    path = _write(tmp_path, "対象を実測してから確定する。実測した値を計画へ書く。\n")

    assert check_agent_doc_tone.main([str(path)]) == 0


def test_report_mode_prints_metrics_without_judging(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`--report`は判定せず、3指標とファイル名を表で出力する。"""
    path = _write(tmp_path, "その値を根拠にしない。\n")

    assert check_agent_doc_tone.main(["--report", str(path)]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split("\t") == ["文数", "否定形終端", "当該", "ファイル"]
    assert lines[1].split("\t") == ["1", "1", "0", str(path)]


def test_excluded_paths_are_skipped(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """意図的な違反例を収録するファイルは検査対象から外す。"""
    excluded = tmp_path / "agent-toolkit/skills/writing-standards/references/tone-examples.md"
    excluded.parent.mkdir(parents=True)
    excluded.write_text("その値を根拠にしない。\n", encoding="utf-8")

    assert check_agent_doc_tone.main([str(excluded)]) == 0
    assert capsys.readouterr().out == ""
