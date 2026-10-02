"""`check_agent_doc_tone.py`の文の抽出、指標の計数および閾値判定を検証する。"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tomllib

import check_agent_doc_tone
import pytest


def _write(tmp_path: pathlib.Path, body: str, *, name: str = "doc.md") -> pathlib.Path:
    """確認するファイルを作成し、そのパスを返す。"""
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
    path = _write(tmp_path, "対象を実際に動かしてから確定する。観測した値を計画へ書く。\n")

    assert check_agent_doc_tone.main([str(path)]) == 0


def test_report_mode_prints_metrics_without_judging(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`--report`は判定せず、3指標とファイル名を表で出力する。"""
    path = _write(tmp_path, "その値を根拠にしない。\n")

    assert check_agent_doc_tone.main(["--report", str(path)]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split("\t") == ["文数", "否定形終端", "当該", "ファイル"]
    assert lines[1].split("\t") == ["1", "1", "0", str(path)]


def test_excluded_paths_are_skipped(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """意図的な違反例を収録するファイルは密度の判定から外す。"""
    excluded = tmp_path / "agent-toolkit/skills/writing-standards/references/tone-examples.md"
    excluded.parent.mkdir(parents=True)
    excluded.write_text("その値を根拠にしない。\n", encoding="utf-8")

    assert check_agent_doc_tone.main([str(excluded)]) == 0
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    ("name", "body", "term", "line"),
    [
        ("doc.md", "正本を参照する。\n", "正本", 1),
        ("doc.md", "正本文書を参照する。\n", "正本", 1),
        ("doc.md", "# 既定の値\n", "既定", 1),
        ("doc.md", "| 対象 |\n| --- |\n| 照合する値 |\n", "照合", 3),
        ("doc.md", "`正本`を参照する。\n", "正本", 1),
        ("doc.md", "```text\n既定の値を使う。\n```\n", "既定", 2),
        ("doc.md", "```python\nprint('照合する値')\n```\n", "照合", 2),
        ("doc.md", "<!-- 正本を参照する。 -->\n", "正本", 1),
        ("app.py", "# 既定の値を使う。\n", "既定", 1),
        ("app.py", '"""正本を参照する。"""\n', "正本", 1),
        ("app.py", 'print("照合した値")\n', "照合", 1),
        ("app.py", 'message = "既" + "定の値"\nprint(message)\n', "既定", 1),
        ("app.py", 'message = ("正" "本を参照する。")\n', "正本", 1),
        ("app.py", 'def emit(value):\n    print(f"照合した{value}")\n', "照合", 2),
        ("style.css", "/* 正本を参照する。 */\n", "正本", 1),
        ("app.js", 'console.log("既定の値を使う。");\n', "既定", 1),
        ("config.toml", 'description = "照合した値"\n', "照合", 1),
        ("config.json", json.dumps({"description": "正本を参照する。"}), "正本", 1),
        ("hook", '#!/bin/sh\nprintf "%s\\n" "既定の値を使う。"\n', "既定", 2),
    ],
)
def test_cli_rejects_terms_in_explanations(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    name: str,
    body: str,
    term: str,
    line: int,
) -> None:
    """文書の構造や媒体が違っても、説明の語を行番号付きで報告して失敗する。"""
    path = _write(tmp_path, body, name=name)

    assert check_agent_doc_tone.main([str(path)]) == 1

    output = capsys.readouterr()
    assert f"{path}:{line}:" in output.err
    assert term in output.err
    assert "書き直す" in output.err


@pytest.mark.parametrize(
    "body", ["見落とした。", "突き合わせた。", "混ざった。", "切り分けた。", "焼き込んだ。", "崩した。", "疑った。"]
)
def test_cli_rejects_inflected_expressions(tmp_path: pathlib.Path, body: str) -> None:
    """辞書形以外の説明文でも、以前に書き直した表現を検出する。"""
    assert check_agent_doc_tone.main([str(_write(tmp_path, body))]) == 1


@pytest.mark.parametrize(
    ("name", "body"),
    [
        ("doc.md", "是正本文を読む。\n起動経路を記録する。\n| 利用者と入口 |\n| --- |\n| CLI |\n"),
        ("app.py", '既定 = 4\ndefault_route = 1\nprint("是正本文")\n'),
        ("app.py", 'fields = ("起動経路", "利用者と入口", "計画検査完了")\n'),
        ("app.py", 'fields = ("正本ファイル名", "選択肢と帰結")\n'),
        ("app.py", 'import re\npattern = re.compile("正本")\n'),
        ("doc.md", "`terminal_order`が`既定`の項目を読む。\n"),
        ("doc.md", "```yaml\nterminal_order: <省略時は「既定」>\n```\n"),
        ("doc.md", "`プロジェクト固有の公開後の操作の順序`が`既定`の項目を読む。\n"),
        ("doc.md", "```yaml\nプロジェクト固有の公開後の操作の順序: <省略時は「既定」>\n```\n"),
        ("config.json", '{"既定": "是正本文", "起動経路": "CLI"}\n'),
        ("config.toml", '"正本" = "是正本文"\n'),
        ("doc.md", "> 他者が記した正本・既定・照合の説明。\n"),
        ("doc.md", "```text\n逐語引用: 正本を読む。\n既定の値を照合する。\n```\n"),
        (
            "doc.md",
            "```text\n悪い例: 正本を照合する。\n"
            "書き換え: 記録した値が入力と一致するか確かめる。\n"
            "解説: 「正本」「照合」は語そのものを示す。\n```\n",
        ),
        ("app_test.py", 'def test_fixture():\n    assert "正本" == "正本"\n'),
    ],
)
def test_cli_preserves_data_and_structural_names(tmp_path: pathlib.Path, name: str, body: str) -> None:
    """文章ではない一致、他者の引用と意図的な違反入力を変更させない。"""
    path = _write(tmp_path, body, name=name)

    assert check_agent_doc_tone.main([str(path)]) == 0
    assert path.read_text(encoding="utf-8") == body


def test_cli_checks_good_examples_and_test_comments(tmp_path: pathlib.Path) -> None:
    """例のファイルとテストのファイルでも、説明文への再使用を検出する。"""
    example = tmp_path / "agent-toolkit/skills/writing-standards/references/tone-examples.md"
    example.parent.mkdir(parents=True)
    example.write_text("```text\n悪い例: 正本を読む。\n書き換え: 正本を読む。\n```\n", encoding="utf-8")
    test_file = _write(tmp_path, '# 正本を参照する。\nvalue = "照合"\n', name="app_test.py")

    assert check_agent_doc_tone.main([str(example)]) == 1
    assert check_agent_doc_tone.main([str(test_file)]) == 1


def test_term_scope_preserves_existing_density_check(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """語の判定を限定した場合も、従来の文体の密度は同じ対象で判定する。"""
    path = _write(tmp_path, "当該対象は変更しない。" * 20)
    scope = tmp_path / "separate"
    scope.mkdir()

    assert check_agent_doc_tone.main(["--term-root", str(scope), str(path)]) == 1
    assert "否定形終端率" in capsys.readouterr().out


def test_fixture_values_are_data_but_comments_are_explanations(tmp_path: pathlib.Path) -> None:
    """共有するテスト入力を保持し、同じモジュールのコメントへの再使用は検出する。"""
    path = tmp_path / "sample_data.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = '# agent-doc-tone: test-data\nsample = "既定の値を照合する。"\n'
    path.write_text(body, encoding="utf-8")

    assert check_agent_doc_tone.main([str(path)]) == 0

    path.write_text("# 正本を参照する。\n" + body, encoding="utf-8")

    assert check_agent_doc_tone.main([str(path)]) == 1


@pytest.mark.parametrize("relative", ["helpers/helper.py", "_testing/helper.py", "helper_test.py"])
def test_displayed_messages_are_checked_with_fixture_data(tmp_path: pathlib.Path, relative: str) -> None:
    """保存場所を問わず、同じモジュールの入力と表示文を区別する。"""
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    data = '# agent-doc-tone: test-data\nsample = "既定の値を照合する。"\n'
    path.write_text(data, encoding="utf-8")
    assert check_agent_doc_tone.main([str(path)]) == 0
    for message in (
        'raise RuntimeError("既定の値を照合できない")',
        'value = 1\nraise RuntimeError(f"既定の値{value}を照合できない")',
        'message = "既定の値を照合できない"\nprint(message)',
    ):
        path.write_text(data + message + "\n", encoding="utf-8")
        assert check_agent_doc_tone.main([str(path)]) == 1


@pytest.mark.parametrize(
    ("name", "body", "explanation"),
    [
        ("app.py", 'from pathlib import Path\npath = Path("照合.md")\n', 'print("照合した値")\n'),
        ("config.json", '{"input_path": "照合.md"}', '{"input_path": "照合.md", "description": "照合した値"}'),
        ("config.toml", 'input_path = "照合.md"\n', 'description = "照合した値"\n'),
        ("config.toml", 'settings = {input_paths = ["照合.md", "既定.md"]}\n', 'description = "照合した値"\n'),
        ("config.json", '{"settings": {"input_paths": ["照合.md", "既定.md"]}}', '{"description": "照合した値"}'),
    ],
)
def test_path_inputs_are_preserved_but_explanations_are_checked(
    tmp_path: pathlib.Path,
    name: str,
    body: str,
    explanation: str,
) -> None:
    """パスとして渡す値を変更させず、隣の説明文への再使用を検出する。"""
    path = _write(tmp_path, body, name=name)
    assert check_agent_doc_tone.main([str(path)]) == 0
    assert path.read_text(encoding="utf-8") == body
    path.write_text(explanation if name.endswith(".json") else body + explanation, encoding="utf-8")
    assert check_agent_doc_tone.main([str(path)]) == 1


@pytest.mark.parametrize(
    ("example_body", "application_body", "helper_tail", "expected_returncode", "expected_output"),
    [
        (
            "```text\n悪い例: 正本を読む。\n書き換え: 正本を読む。\n```\n",
            '# 既定の値を使う。\nprint("照合する値")\n',
            'raise RuntimeError("既定の値を照合できない")\n',
            1,
            ["正本", "既定", "照合", "tone-examples.md", "new_module.py", "helper.py"],
        ),
        (
            "```text\n悪い例: 正本を読む。\n書き換え: 記録した値を読む。\n```\n",
            'from pathlib import Path\npath = Path("照合.md")\nprint("是正本文")\n',
            "",
            0,
            [],
        ),
    ],
    ids=["violations", "compliant"],
)
# 子処理の時間上限（120秒）より大きいテスト単位の上限を置き、停止時は子処理の時間切れとして報告させる。
# CIで全チェックを並行して実行するジョブでは子処理に約40秒を要したため、子処理の上限は観測値の約3倍とする。
@pytest.mark.timeout(180)
def test_registered_pyfltr_check_reaches_examples_and_code(
    tmp_path: pathlib.Path,
    example_body: str,
    application_body: str,
    helper_tail: str,
    expected_returncode: int,
    expected_output: list[str],
) -> None:
    """通常の登録対象を通るチェックが、例の良い文と新しいコードの説明へ到達する。"""
    repository = pathlib.Path(__file__).resolve().parents[1]
    settings = tomllib.loads((repository / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["pyfltr"]
    command = settings["custom-commands"]["agent-doc-tone"]
    arguments_for_check = [str(repository / "scripts/check_agent_doc_tone.py"), "--term-root", str(tmp_path / "agent-toolkit")]
    config = (
        "[tool.pyfltr]\nagent-doc-tone-subproject-aware = false\n"
        f"extend-exclude = {json.dumps(settings['extend-exclude'])}\n"
        "[tool.pyfltr.custom-commands.agent-doc-tone]\ntype = 'linter'\n"
        f"path = {json.dumps(sys.executable)}\n"
        f"args = {json.dumps(arguments_for_check)}\n"
        f"targets = {json.dumps(command['targets'])}\n"
    )
    (tmp_path / "pyproject.toml").write_text(config, encoding="utf-8")
    example = tmp_path / "agent-toolkit/skills/writing-standards/references/tone-examples.md"
    application = tmp_path / "agent-toolkit" / "agent_toolkit" / "new_module.py"
    helper = application.parent / "_testing" / "helper.py"
    path_config = tmp_path / "agent-toolkit" / "config.json"
    example.parent.mkdir(parents=True)
    application.parent.mkdir(parents=True)
    helper.parent.mkdir(parents=True)
    example.write_text(example_body, encoding="utf-8")
    application.write_text(application_body, encoding="utf-8")
    fixture_data = '# agent-doc-tone: test-data\nsample = "既定の値を照合する。"\n'
    helper.write_text(fixture_data + helper_tail, encoding="utf-8")
    path_config.write_text('{"input_path": "照合.md"}', encoding="utf-8")
    arguments = [
        sys.executable,
        "-m",
        "pyfltr",
        "run",
        "--commands=agent-doc-tone",
        "--no-fix",
        "--no-cache",
        "--output-format=jsonl",
        "--work-dir",
        str(tmp_path),
        str(example),
        str(application),
        str(helper),
        str(path_config),
    ]

    result = subprocess.run(arguments, capture_output=True, text=True, encoding="utf-8", timeout=120, check=False)

    assert result.returncode == expected_returncode, result.stdout + result.stderr
    records = [json.loads(line) for line in result.stdout.splitlines()]
    decoded = json.dumps(records, ensure_ascii=False)
    for expected in expected_output:
        assert expected in decoded
