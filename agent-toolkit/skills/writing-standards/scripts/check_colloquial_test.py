"""agent-toolkit/skills/writing-standards/scripts/check_colloquial.py のテスト。

CLIスクリプトの動作確認。subprocess経由で起動しexit code・stderrを検証する。
辞書ファイルから動的にサンプルを生成するため、テスト本体には口語表現を直接書かない。
出力フォーマットの分岐（置換候補の有無）も実辞書とsubprocess経由で検証する。
"""

import pathlib
import re
import subprocess
import sys

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parent / "check_colloquial.py"
_AGENT_TOOLKIT_SCRIPTS = pathlib.Path(__file__).resolve().parents[3] / "scripts"
_DENY_PATH = _AGENT_TOOLKIT_SCRIPTS / "_colloquial_words.txt"
_ALLOW_PATH = _AGENT_TOOLKIT_SCRIPTS / "_colloquial_words_allow.txt"
_TONE_EXAMPLES = pathlib.Path(__file__).resolve().parents[1] / "references" / "tone-examples.md"

# 辞書パース処理は本番ロジックの`_colloquial_check.load_patterns`を共有する。
# `check_colloquial.py`が同一の`sys.path`操作を実行するため副作用を許容する。
sys.path.insert(0, str(_AGENT_TOOLKIT_SCRIPTS))
import _colloquial_check  # noqa: E402  # pylint: disable=wrong-import-position,import-error


def _expand(pattern_str: str) -> str:
    return re.sub(r"\[([^\]]+)\]", lambda m: m.group(1)[0], pattern_str)


@pytest.fixture(name="deny_substring")
def _deny_substring() -> str:
    """辞書ファイルからdenyリスト検出に当たる最短サンプルを動的生成する。

    `load_patterns`経由でパターン部のみを取り出すため、タブ区切り行の置換候補列は混入しない。
    """
    deny_patterns = [pat for pat, _ in _colloquial_check.load_patterns(_DENY_PATH)]
    for line in _ALLOW_PATH.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        sample = _expand(stripped)
        for dp in deny_patterns:
            m = dp.search(sample)
            if m:
                return m.group(0)
    pytest.skip("no overlap between denylist and allowlist; cannot generate test sample")
    return ""  # unreachable


def _run(*paths: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *(str(p) for p in paths)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_detects_violation(tmp_path: pathlib.Path, deny_substring: str):
    target = tmp_path / "doc.md"
    target.write_text(f"# 表題\n\n概要は{deny_substring}該当する。\n", encoding="utf-8")
    result = _run(target)
    assert result.returncode == 1
    assert "colloquial" in result.stderr
    assert ":3:" in result.stderr


def test_clean_file_passes(tmp_path: pathlib.Path):
    target = tmp_path / "clean.md"
    target.write_text("# header\n\nplain ASCII content here.\n", encoding="utf-8")
    result = _run(target)
    assert result.returncode == 0
    assert result.stderr == ""


def test_missing_file_silently_skipped(tmp_path: pathlib.Path):
    result = _run(tmp_path / "nope.md")
    assert result.returncode == 0


def test_multiple_files_aggregates(tmp_path: pathlib.Path, deny_substring: str):
    a = tmp_path / "a.md"
    b = tmp_path / "b.md"
    a.write_text(f"概要は{deny_substring}該当する。\n", encoding="utf-8")
    b.write_text("clean.\n", encoding="utf-8")
    result = _run(a, b)
    assert result.returncode == 1
    assert str(a) in result.stderr
    assert str(b) not in result.stderr  # bは検出なし


def test_directory_recurses(tmp_path: pathlib.Path, deny_substring: str):
    """ディレクトリを渡すと再帰的に対象拡張子のファイルを走査する。"""
    sub = tmp_path / "docs"
    sub.mkdir()
    target = sub / "note.md"
    other = sub / "other.txt"
    target.write_text(f"概要は{deny_substring}該当する。\n", encoding="utf-8")
    other.write_text(f"説明は{deny_substring}記載する。\n", encoding="utf-8")
    # 対象外拡張子は無視される。
    skipped = sub / "ignore.bin"
    skipped.write_text(f"{deny_substring}\n", encoding="utf-8")
    result = _run(tmp_path)
    assert result.returncode == 1
    assert str(target) in result.stderr
    assert str(other) in result.stderr
    assert str(skipped) not in result.stderr


def test_directory_includes_md_tmpl(tmp_path: pathlib.Path, deny_substring: str):
    """ディレクトリ走査時に`.md.tmpl`二重拡張子もmd相当として走査対象に含む。

    `.tmpl`単独はテンプレート構文の誤検出が多いため対象外であることも確認する。
    """
    md_tmpl = tmp_path / "note.md.tmpl"
    md_tmpl.write_text(f"概要は{deny_substring}該当する。\n", encoding="utf-8")
    plain_tmpl = tmp_path / "raw.tmpl"
    plain_tmpl.write_text(f"{deny_substring}\n", encoding="utf-8")
    result = _run(tmp_path)
    assert result.returncode == 1
    assert str(md_tmpl) in result.stderr
    assert str(plain_tmpl) not in result.stderr


def test_directory_excludes_known_dirs(tmp_path: pathlib.Path, deny_substring: str):
    """`.git`等の既知の除外ディレクトリ配下はスキャン対象外。"""
    for excluded in (".git", ".venv", "node_modules", "__pycache__"):
        d = tmp_path / excluded
        d.mkdir()
        (d / "x.md").write_text(f"{deny_substring}\n", encoding="utf-8")
    target = tmp_path / "kept.md"
    target.write_text(f"概要は{deny_substring}該当する。\n", encoding="utf-8")
    result = _run(tmp_path)
    assert result.returncode == 1
    assert str(target) in result.stderr
    for excluded in (".git", ".venv", "node_modules", "__pycache__"):
        assert excluded not in result.stderr


def test_dictionary_files_are_skipped():
    """辞書ファイル本体を直接渡しても検査されずexit 0（自己誘発検出を避ける）。"""
    result = _run(_DENY_PATH, _ALLOW_PATH)
    assert result.returncode == 0
    assert result.stderr == ""


def test_tone_examples_file_is_skipped():
    """対比集ファイルを直接渡しても検査されずexit 0（悪い例を含むため除外対象）。"""
    if not _TONE_EXAMPLES.exists():
        pytest.skip("tone-examples.mdが未配置のためスキップ")
    result = _run(_TONE_EXAMPLES)
    assert result.returncode == 0
    assert result.stderr == ""


def test_self_test_file_is_skipped():
    """自テストファイルを直接渡しても検査されずexit 0（意図的に違反テキストを含むため）。"""
    result = _run(pathlib.Path(__file__).resolve())
    assert result.returncode == 0
    assert result.stderr == ""


def test_output_includes_replacement_when_available(tmp_path: pathlib.Path):
    """辞書に置換候補列を持つパターンの検出時は出力に`-> [候補]`が含まれる。

    辞書`_colloquial_words.txt`内の置換候補定義（`書き出 -> 出力`）に依存する統合検証。
    辞書側の定義を変更した場合は本テストも見直す。
    """
    target = tmp_path / "doc.md"
    target.write_text("設定を書き出す。\n", encoding="utf-8")
    result = _run(target)
    assert result.returncode == 1
    assert "-> [出力]" in result.stderr


def test_tone_examples_excluded_from_directory_scan(tmp_path: pathlib.Path):
    """ディレクトリ走査時に対比集ファイルが除外される。

    対比集ファイルをクリーンなファイルのみと同じディレクトリに置き、
    ディレクトリ走査でも対比集が除外されてexit 0になることを確認する。
    """
    if not _TONE_EXAMPLES.exists():
        pytest.skip("tone-examples.mdが未配置のためスキップ")
    # 対比集ファイルのシンボリックリンクをクリーンなファイルと同じ一時ディレクトリに配置する。
    # 違反のないファイルのみの構成でexit 0になることを確認する。
    tone_link = tmp_path / "tone-examples.md"
    tone_link.symlink_to(_TONE_EXAMPLES)
    clean = tmp_path / "clean.md"
    clean.write_text("# header\n\nplain content.\n", encoding="utf-8")
    result = _run(tmp_path)
    assert str(tone_link) not in result.stderr
    assert result.returncode == 0
