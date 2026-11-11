"""文体検査の主題別契約を検証する。"""

from __future__ import annotations

import json
import pathlib

import check_agent_doc_tone
import pytest
from _scripts_test_helpers import _write


@pytest.mark.parametrize(
    ("name", "body", "term", "line"),
    [
        ("doc.md", "正本を参照する。\n", "正本", 1),
        ("doc.md", "正本文書を参照する。\n", "正本", 1),
        ("doc.md", "# 既定の値\n", "既定", 1),
        ("doc.md", "| 対象 |\n| --- |\n| 照合する値 |\n", "照合", 3),
        ("doc.md", "`正本`を参照する。\n", "正本", 1),
        ("doc.md", "```text\n既定の値を使う。\n```\n", "既定", 2),
        ("doc.md", "```text\n悪い例: 正本を読む。\n```\n", "正本", 2),
        ("doc.md", "```text\n冗長な例: 正本を読む。\n```\n", "正本", 2),
        ("doc.md", "```text\n対象語: 正本\n```\n", "正本", 2),
        ("doc.md", "```text\n解説: 「正本」を使う。\n```\n", "正本", 2),
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
    assert f"{path}:{line}: error: " in output.err
    assert term in output.err
    assert "書き直す" in output.err


@pytest.mark.parametrize(
    ("body", "term"),
    [
        ("経路を記録する。\n", "経路"),
        ("混ざった。\n", "混ざる"),
        ("焼き込んだ。\n", "焼く"),
        ("崩した。\n", "崩す"),
        ("疑った。\n", "疑う"),
        ("速度よりむしろ精度を優先する。\n", "むしろ"),
        ("単なる保存先の変更ではない。\n", "単なる"),
        ("整理というより改訂に近い。\n", "というより"),
    ],
)
def test_cli_warns_caution_terms_without_failing(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], body: str, term: str
) -> None:
    """注意を促す語は活用形でも位置付きの警告を出力し、その語だけでは終了コードを0のまま保つ。"""
    path = _write(tmp_path, body)

    assert check_agent_doc_tone.main([str(path)]) == 0

    output = capsys.readouterr()
    assert f"{path}:1: warning: 警告: 「{term}」" in output.err
    assert "（終了コードには影響しない）" in output.err


@pytest.mark.parametrize(
    ("name", "body", "warns"),
    [
        ("doc.md", "「速度よりむしろ精度を優先する」と説明する。\n", True),
        ("doc.md", "```text\n例文: 速度よりむしろ精度を優先する。\n説明: 優先順位を示す。\n```\n", True),
        ("doc.md", "```text\n逐語引用: 速度よりむしろ精度を優先する。\n```\n", False),
        ("config.py", 'labels = {"むしろ": 1}\n', False),
        ("sample_test.py", 'import pytest\n\nbody = "速度よりむしろ精度を優先する。"\n', False),
        ("doc.md", "```text\n例文: 精度を優先する。\n説明: 優先する対象を直接示す。\n```\n", False),
    ],
    ids=["quoted-prose", "authored-example", "verbatim", "identifier", "detector-input", "positive-example"],
)
def test_cli_contrast_distinguishes_prose_from_preserved_data(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], name: str, body: str, warns: bool
) -> None:
    """自作の本文と例を警告し、原文・識別子・検出入力を保持する。"""
    assert check_agent_doc_tone.main([str(_write(tmp_path, body, name=name))]) == 0
    assert ("警告: 「むしろ」" in capsys.readouterr().err) is warns


@pytest.mark.parametrize(
    ("term", "category", "reason", "direction"),
    [
        (
            term,
            category,
            *check_agent_doc_tone._CAUTION_MESSAGES[category].split("。", maxsplit=1),  # pylint: disable=protected-access
        )
        for term, (_pattern, category) in check_agent_doc_tone._CAUTION_PATTERNS.items()  # pylint: disable=protected-access
    ],
)
def test_cli_explains_every_caution_term(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    term: str,
    category: str,
    reason: str,
    direction: str,
) -> None:
    """全注意語の警告が分類に対応する理由と書き換え方向を伝える。"""
    contextual_examples = {
        "入口": "APIの入口を説明する。\n",
        "受け口": "要求の受け口を説明する。\n",
        "拾う": "夜間の処理が失敗を拾う。\n",
        "引く": "本番データを引く。\n",
        "通る": "書き込みも通る。\n",
        "効く": "構成を選ぶときに効く。\n",
        "響く": "全クライアントに響く。\n",
        "足す": "中継しても値を足す。\n",
    }
    path = _write(tmp_path, contextual_examples.get(term, f"{term}を説明する。\n"))

    assert check_agent_doc_tone.main([str(path)]) == 0

    warning = capsys.readouterr().err
    assert category in {  # pylint: disable=protected-access
        check_agent_doc_tone._METAPHORICAL,  # pylint: disable=protected-access
        check_agent_doc_tone._ABSTRACT,  # pylint: disable=protected-access
        check_agent_doc_tone._CONTRAST,  # pylint: disable=protected-access
    }
    assert reason in warning
    assert direction in warning


def test_cli_ignores_approved_replacement_term(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """承認済みの置換先「突き合わせる」は失敗にも警告にもしない。"""
    assert check_agent_doc_tone.main([str(_write(tmp_path, "記録と入力を突き合わせた。\n"))]) == 0
    assert capsys.readouterr().err == ""


def test_cli_fails_on_denied_term_beside_caution_term(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """失敗対象の語と警告対象の語が並ぶ場合は、両方を報告して失敗する。"""
    path = _write(tmp_path, "既定の経路を使う。\n")

    assert check_agent_doc_tone.main([str(path)]) == 1

    output = capsys.readouterr().err
    assert f"{path}:1: error: 「既定」" in output
    assert f"{path}:1: warning: 警告: 「経路」" in output


@pytest.mark.parametrize(
    ("name", "body"),
    [
        ("doc.md", "是正本文を読む。\n起動経路を記録する。\n| 消費主体と入口 |\n| --- |\n| CLI |\n"),
        ("app.py", '既定 = 4\ndefault_route = 1\nprint("是正本文")\n'),
        ("app.py", 'fields = ("起動経路", "利用者と入口", "計画検査完了")\n'),
        ("doc.md", "```text\n書込対象の検査: 終了コード0\n```\n"),
        ("app.py", 'fields = ("選択肢と帰結",)\n'),
        ("doc.md", "第2走査は「前提を疑う観点」とし、採用案の前提を確かめる。\n"),
        ("app.py", 'import re\npattern = re.compile("正本")\n'),
        ("doc.md", "`terminal_order`が`既定`の項目を読む。\n"),
        ("doc.md", "```yaml\nterminal_order: <省略時は「既定」>\n```\n"),
        ("doc.md", "`プロジェクト固有の公開後の操作の順序`が`既定`の項目を読む。\n"),
        ("doc.md", "```yaml\nプロジェクト固有の公開後の操作の順序: <省略時は「既定」>\n```\n"),
        ("config.json", '{"既定": "是正本文", "起動経路": "CLI"}\n'),
        ("config.toml", '"正本" = "是正本文"\n'),
        ("doc.md", "> 他者が記した正本・既定・照合の説明。\n"),
        ("doc.md", "```text\n逐語引用: 正本を読む。\n既定の値を照合する。\n```\n"),
        ("app_test.py", 'def test_fixture():\n    assert "正本" == "正本"\n'),
    ],
)
def test_cli_preserves_data_and_structural_names(tmp_path: pathlib.Path, name: str, body: str) -> None:
    """文章ではない一致、他者の引用と検出用の入力を変更させない。"""
    path = _write(tmp_path, body, name=name)

    assert check_agent_doc_tone.main([str(path)]) == 0
    assert path.read_text(encoding="utf-8") == body


@pytest.mark.parametrize("ending", ["", "\n"])
def test_json_diagnostics_keep_value_start_lines_and_ignore_keys_and_paths(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], ending: str
) -> None:
    """改行したキーとcolon、配列、エスケープを含むJSONでも説明値の開始行を返す。"""
    body = "\n".join(
        [
            "{",
            '  "正本"',
            '  : "空白 : colon",',
            '  "input_paths": ["既定.md", "nested/照合.md"],',
            '  "items": [',
            '    {"description": "正本を参照する。"},',
            r'    {"description": "引用符\"と\n照合する。"},',
            '    {"description": "入口を示す。"}',
            "  ],",
            '  "description"',
            "  :",
            '  "既定の動作。"',
            "}",
        ]
    )
    path = _write(tmp_path, body + ending, name="config.json")

    assert check_agent_doc_tone.main([str(path)]) == 1

    diagnostics = capsys.readouterr().err.splitlines()
    expected = [(6, "error", "正本"), (7, "error", "照合"), (12, "error", "既定"), (8, "warning", "入口")]
    assert len(diagnostics) == len(expected)
    for diagnostic, (line, severity, term) in zip(diagnostics, expected, strict=True):
        assert diagnostic.startswith(f"{path}:{line}: {severity}:")
        assert f"「{term}」" in diagnostic


@pytest.mark.parametrize("body", ["「前提を疑う観点」で実装を疑う。\n", "「前提を疑う観点」を使う。\n前提を疑う。\n"])
def test_cli_warns_other_uses_of_doubt_beside_defined_viewpoint_name(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], body: str
) -> None:
    """定義済みの名前だけを除き、同じ行と別の行にある同じ動詞の他の用法は警告する。"""
    assert check_agent_doc_tone.main([str(_write(tmp_path, body))]) == 0
    assert "警告: 「疑う」" in capsys.readouterr().err


def test_cli_does_not_warn_defined_viewpoint_name_alone(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """定義済みの名前「前提を疑う観点」だけの行は警告しない。"""
    assert check_agent_doc_tone.main([str(_write(tmp_path, "「前提を疑う観点」を使う。\n"))]) == 0
    assert capsys.readouterr().err == ""


def test_picker_result_label_preserves_checks_on_explanations(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """返却欄の名前を保持し、同じ行の説明は警告対象として確認する。"""
    assert check_agent_doc_tone.main([str(_write(tmp_path, "書込対象の検査: 未確認の経路を返す。\n"))]) == 0
    assert "警告: 「経路」" in capsys.readouterr().err


def test_cli_checks_good_examples_and_test_comments(tmp_path: pathlib.Path) -> None:
    """例のファイルとテストのファイルでも、説明文への再使用を検出する。"""
    example = tmp_path / "agent-basics/skills/writing-standards/references/tone-examples.md"
    example.parent.mkdir(parents=True)
    example.write_text("```text\n例文: 正本を読む。\n説明: 「照合」を用いる。\n```\n", encoding="utf-8")
    test_file = _write(tmp_path, '# 正本を参照する。\nvalue = "照合"\n', name="app_test.py")

    assert check_agent_doc_tone.main([str(example)]) == 1
    assert check_agent_doc_tone.main([str(test_file)]) == 1


@pytest.mark.parametrize(
    "term",
    ["拾う", "引く", "通る", "効く", "響く", "足す", "入口", "受け口"],
)
def test_cli_warns_machine_processing_metaphors(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], term: str) -> None:
    """機械処理へ当てはめた動詞と場所の比喩名詞を警告する。"""
    examples = {
        "拾う": "夜間の処理が失敗を拾う。\n",
        "引く": "本番データを引く。\n",
        "通る": "書き込みも通る。\n",
        "効く": "構成を選ぶときに効く。\n",
        "響く": "全クライアントに響く。\n",
        "足す": "中継しても値を足す。\n",
        "入口": "APIの入口を説明する。\n",
        "受け口": "要求の受け口を説明する。\n",
    }
    assert check_agent_doc_tone.main([str(_write(tmp_path, examples[term]))]) == 0
    assert f"警告: 「{term}」" in capsys.readouterr().err


@pytest.mark.parametrize("term", ["足すものがない", "失効が効く"])
def test_cli_rejects_literal_translation_phrases(tmp_path: pathlib.Path, term: str) -> None:
    """英語の定型句を逐語訳した表現を拒否する。"""
    assert check_agent_doc_tone.main([str(_write(tmp_path, f"この中継は{term}。\n"))]) == 1


def test_cli_allows_dependency_terms(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """WIとパッケージのdependencyを示す用語は検出しない。"""
    assert check_agent_doc_tone.main([str(_write(tmp_path, "depends_onはWIの依存先を示す。\n"))]) == 0
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize(
    "body",
    [
        "本番データを引く。",
        "消えた分は夜間の処理が拾う。",
        "書き込みも通る。",
        "構成を選ぶときに効く。",
        "全クライアントに響く。",
        "入口を確認する。",
        "受け口を用意する。",
        "トークンを出す口を用意する。",
        "`本番データを引く`と記述する。",
    ],
)
def test_cli_warns_original_machine_metaphor_examples(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], body: str
) -> None:
    """原要求の例と通常本文のコード表記も、警告の対象として保持する。"""
    assert check_agent_doc_tone.main([str(_write(tmp_path, body + "\n"))]) == 0
    assert "警告:" in capsys.readouterr().err


@pytest.mark.parametrize(
    "body",
    [
        "機能の説明を補足する。処理の条件を満足する。",
        "必要な条件が不足する場合は、別の条件を充足する。",
        "処理が長引く場合の所要から、必要な待機を差し引く。",
        "データを引き継ぐ。",
        "操作は説明した通りである。",
        "設定は有効かを確認する。",
        "機能の影響から原因を調べる。",
    ],
)
def test_cli_preserves_other_words_containing_verb_characters(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], body: str
) -> None:
    """別の複合語や名詞に含まれる文字列を、禁止する動詞へ誤分類しない。"""
    path = _write(tmp_path, body + "\n")
    assert check_agent_doc_tone.main([str(path)]) == 0
    assert capsys.readouterr().err == ""


def test_cli_masks_only_explicit_prohibited_examples(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """明示した禁止例だけを除外し、一般本文にある直訳句は検出する。"""
    masked = "<!-- agent-doc-tone: example-mask:start -->\n失効が効く。\n<!-- agent-doc-tone: example-mask:end -->\n"
    assert check_agent_doc_tone.main([str(_write(tmp_path, masked))]) == 0
    assert capsys.readouterr().err == ""
    assert check_agent_doc_tone.main([str(_write(tmp_path, "失効が効く。\n"))]) == 1


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
