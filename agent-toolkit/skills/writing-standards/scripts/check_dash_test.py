"""agent-toolkit/skills/writing-standards/scripts/check_dash.py のテスト。

ダッシュ系禁止文字検査スクリプトをfork-server経由（フォールバック時はsubprocess）で起動し、
違反検出・除外・出力形式・ディレクトリ再帰・拡張子フィルタを検証する。
"""

import pathlib
import runpy
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "scripts"))
import _fork_runner  # noqa: E402  # pylint: disable=wrong-import-position,import-error

_SCRIPT = pathlib.Path(__file__).resolve().parent / "check_dash.py"

# 検出対象文字（テスト内でのリテラル直書き）
_EM_DASH = "—"  # —
_HORIZ_BAR = "―"  # ―
_BOX_SINGLE = "─"  # ─
_BOX_DOUBLE = "──"  # ── (2倍ダッシュ)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return _fork_runner.run_script(_SCRIPT, argv=args)


def _write(path: pathlib.Path, content: str) -> pathlib.Path:
    path.write_text(content, encoding="utf-8")
    return path


class TestCheckDash:
    """ダッシュ系禁止文字検査の主要シナリオをまとめて検証する。"""

    # ---- 違反検出 ----

    def test_em_dash_is_detected(self, tmp_path: pathlib.Path) -> None:
        """U+2014（EM DASH）を地の文で使用すると違反として報告する。"""
        path = _write(tmp_path / "doc.md", f"本文{_EM_DASH}続き\n")
        result = _run(str(path))
        assert result.returncode == 1
        assert "em-dash(U+2014)" in result.stderr
        assert f"{path}:1:" in result.stderr

    def test_horizontal_bar_is_detected(self, tmp_path: pathlib.Path) -> None:
        """U+2015（HORIZONTAL BAR）を地の文で使用すると違反として報告する。"""
        path = _write(tmp_path / "doc.md", f"# 見出し{_HORIZ_BAR}説明\n")
        result = _run(str(path))
        assert result.returncode == 1
        assert "horizontal-bar(U+2015)" in result.stderr
        assert f"{path}:1:" in result.stderr

    def test_double_box_dash_is_detected(self, tmp_path: pathlib.Path) -> None:
        """U+2500の2連続（2倍ダッシュ）を地の文で使用すると違反として報告する。"""
        path = _write(tmp_path / "doc.md", f"区切り{_BOX_DOUBLE}線\n")
        result = _run(str(path))
        assert result.returncode == 1
        assert "double-dash(U+2500x2)" in result.stderr
        assert f"{path}:1:" in result.stderr

    def test_output_includes_line_and_column(self, tmp_path: pathlib.Path) -> None:
        """出力にパス・行番号・列番号・抜粋（ダブルクォート付き）が含まれる。"""
        path = _write(tmp_path / "doc.md", f"abc{_EM_DASH}def\n")
        result = _run(str(path))
        assert result.returncode == 1
        # 列番号は4（"abc"の後）、抜粋はダブルクォートで囲まれる。
        assert f"{path}:1:4: em-dash(U+2014)" in result.stderr
        assert '"' in result.stderr  # 抜粋のダブルクォートが存在する。

    def test_output_excerpt_is_double_quoted(self, tmp_path: pathlib.Path) -> None:
        """出力の抜粋部分がダブルクォートで囲まれている。"""
        content = f"本文{_EM_DASH}続き\n"
        path = _write(tmp_path / "doc.md", content)
        result = _run(str(path))
        assert result.returncode == 1
        # 出力形式: `path:line:col: kind "excerpt"`
        assert ': em-dash(U+2014) "' in result.stderr

    def test_violation_on_second_line_reports_correct_lineno(self, tmp_path: pathlib.Path) -> None:
        """2行目の違反は行番号2で報告する。"""
        path = _write(tmp_path / "doc.md", f"1行目\n2行目{_EM_DASH}続き\n")
        result = _run(str(path))
        assert result.returncode == 1
        assert f"{path}:2:" in result.stderr

    # ---- 非違反・除外 ----

    def test_clean_file_passes(self, tmp_path: pathlib.Path) -> None:
        """禁止文字を含まないファイルはexit 0・stderrなし。"""
        path = _write(tmp_path / "clean.md", "# header\n\n通常のテキスト。\n")
        result = _run(str(path))
        assert result.returncode == 0
        assert result.stderr == ""

    def test_en_dash_is_not_detected(self, tmp_path: pathlib.Path) -> None:
        """U+2013（EN DASH）は検出対象外。"""
        path = _write(tmp_path / "doc.md", "範囲: 1–3\n")
        result = _run(str(path))
        assert result.returncode == 0
        assert result.stderr == ""

    def test_hyphen_is_not_detected(self, tmp_path: pathlib.Path) -> None:
        """通常のハイフン（U+002D）は検出対象外。"""
        path = _write(tmp_path / "doc.md", "2024-01-01\n")
        result = _run(str(path))
        assert result.returncode == 0
        assert result.stderr == ""

    def test_single_box_dash_is_not_detected(self, tmp_path: pathlib.Path) -> None:
        """U+2500（BOX DRAWINGS LIGHT HORIZONTAL）の単体（1文字）は検出対象外。"""
        path = _write(tmp_path / "doc.md", f"単独{_BOX_SINGLE}文字\n")
        result = _run(str(path))
        assert result.returncode == 0
        assert result.stderr == ""

    def test_backtick_fenced_code_is_excluded(self, tmp_path: pathlib.Path) -> None:
        """バッククォートフェンス内の禁止文字は無視する。"""
        path = _write(
            tmp_path / "code.md",
            f"通常文\n```text\n{_EM_DASH}\n```\n本文\n",
        )
        result = _run(str(path))
        assert result.returncode == 0
        assert result.stderr == ""

    def test_tilde_fenced_code_is_excluded(self, tmp_path: pathlib.Path) -> None:
        """チルダフェンス内の禁止文字は無視する。"""
        path = _write(
            tmp_path / "code.md",
            f"~~~\n{_EM_DASH}\n~~~\n",
        )
        result = _run(str(path))
        assert result.returncode == 0
        assert result.stderr == ""

    def test_four_backtick_fence_is_excluded(self, tmp_path: pathlib.Path) -> None:
        """4個のバッククォートフェンス内の禁止文字も無視する（Markdown拡張）。"""
        path = _write(
            tmp_path / "code.md",
            f"````\n{_EM_DASH}\n````\n",
        )
        result = _run(str(path))
        assert result.returncode == 0
        assert result.stderr == ""

    def test_four_tilde_fence_is_excluded(self, tmp_path: pathlib.Path) -> None:
        """4個のチルダフェンス内の禁止文字も無視する。"""
        path = _write(
            tmp_path / "code.md",
            f"~~~~\n{_EM_DASH}\n~~~~\n",
        )
        result = _run(str(path))
        assert result.returncode == 0
        assert result.stderr == ""

    def test_nested_fence_with_shorter_inner_marker_is_ignored(self, tmp_path: pathlib.Path) -> None:
        """開始4個・内側3個のネストしたフェンスで、内側フェンス内の禁止文字は誤検出されない。

        内側の閉じ候補（3個）は開始フェンス（4個）より短いため閉じ判定に使われず、
        外側の閉じフェンス（4個）以降の地の文でのみ禁止文字が検出される。
        """
        path = _write(
            tmp_path / "doc.md",
            f"````text\n```\n{_EM_DASH}\n```\n````\nその後{_EM_DASH}を含む\n",
        )
        result = _run(str(path))
        assert result.returncode == 1
        assert f"{path}:3:" not in result.stderr
        assert f"{path}:6:" in result.stderr

    def test_fence_close_candidate_with_info_string_is_not_closing(self, tmp_path: pathlib.Path) -> None:
        """閉じ候補行が情報文字列を伴う場合は閉じフェンスと認識せず、フェンス内側のまま扱う。

        開始フェンスと同種・同長以上のマーカーでも、末尾に情報文字列（`text`等）が
        付いている行はMarkdown仕様上の閉じフェンスではない。閉じ判定を誤ると、
        本来フェンス内側にある禁止文字を誤って地の文として検出してしまう。
        """
        path = _write(
            tmp_path / "doc.md",
            f"```\n```text\n{_EM_DASH}\n```\n```\n",
        )
        result = _run(str(path))
        assert result.returncode == 0
        assert result.stderr == ""

    def test_different_fence_kind_inside_is_ignored(self, tmp_path: pathlib.Path) -> None:
        """開始3個のバッククォートフェンス内部に`~~~`（別種フェンス）が出現しても無視される。"""
        path = _write(
            tmp_path / "doc.md",
            f"```text\n~~~\n{_EM_DASH}\n~~~\n```\nその後{_EM_DASH}を含む\n",
        )
        result = _run(str(path))
        assert result.returncode == 1
        assert f"{path}:3:" not in result.stderr
        assert f"{path}:6:" in result.stderr

    def test_inline_code_is_excluded(self, tmp_path: pathlib.Path) -> None:
        """インラインコード（バッククォートペア）内の禁止文字は無視する。"""
        path = _write(tmp_path / "doc.md", f"`{_EM_DASH}`の使い方\n")
        result = _run(str(path))
        assert result.returncode == 0
        assert result.stderr == ""

    def test_inline_code_only_excludes_inside(self, tmp_path: pathlib.Path) -> None:
        """インラインコード内は除外されるがコード外の禁止文字は検出する。"""
        path = _write(tmp_path / "doc.md", f"`ok`{_EM_DASH}外側\n")
        result = _run(str(path))
        assert result.returncode == 1
        assert "em-dash(U+2014)" in result.stderr

    def test_escaped_backticks_do_not_create_inline_code(self, tmp_path: pathlib.Path) -> None:
        """エスケープされたバッククォート間の禁止文字は通常本文として検出する。"""
        path = _write(tmp_path / "doc.md", f"\\`{_EM_DASH}\\`を含む\n")
        result = _run(str(path))
        assert result.returncode == 1
        assert "em-dash(U+2014)" in result.stderr

    def test_code_span_closes_after_literal_backslash(self, tmp_path: pathlib.Path) -> None:
        """コードスパン内のバックスラッシュは閉じバッククォートをエスケープしない。"""
        path = _write(tmp_path / "doc.md", f"`{_EM_DASH}\\`を含む\n")
        result = _run(str(path))
        assert result.returncode == 0
        assert result.stderr == ""

    def test_longer_backtick_run_does_not_close_code_span(self, tmp_path: pathlib.Path) -> None:
        """開き列より長い最大バッククォート列への部分一致ではコードスパンを閉じない。"""
        path = _write(tmp_path / "doc.md", f"`{_EM_DASH}``を含む\n")
        result = _run(str(path))
        assert result.returncode == 1
        assert "em-dash(U+2014)" in result.stderr

    def test_multiline_code_span_is_excluded(self, tmp_path: pathlib.Path) -> None:
        """複数行コードスパン内の禁止文字は無視する。"""
        path = _write(tmp_path / "doc.md", f"``\n{_EM_DASH}\n``\n")
        result = _run(str(path))
        assert result.returncode == 0
        assert result.stderr == ""

    def test_dash_after_multiline_code_span_keeps_position(self, tmp_path: pathlib.Path) -> None:
        """複数行コードスパン後の禁止文字は元の行番号と列番号で検出する。"""
        path = _write(tmp_path / "doc.md", f"``\n{_EM_DASH}\n``\n外側{_EM_DASH}\n")
        result = _run(str(path))
        assert result.returncode == 1
        assert f'{path}:4:3: em-dash(U+2014) "外側{_EM_DASH}"' in result.stderr

    def test_code_span_does_not_cross_blank_line(self, tmp_path: pathlib.Path) -> None:
        """別段落のバッククォート列を閉じ区切りとして扱わない。"""
        path = _write(tmp_path / "doc.md", f"`\n{_EM_DASH}\n\n`\n")
        result = _run(str(path))
        assert result.returncode == 1
        assert f'{path}:2:1: em-dash(U+2014) "{_EM_DASH}"' in result.stderr

    @pytest.mark.parametrize(
        "body",
        [
            f"- `{_EM_DASH}\n- second`\n",
            f"`{_EM_DASH}\n### heading `\n",
            f"`{_EM_DASH}\n> quote `\n",
            f"`{_EM_DASH}\n***\ntext `\n",
        ],
    )
    def test_code_span_does_not_cross_inline_block_boundary(self, tmp_path: pathlib.Path, body: str) -> None:
        """別のCommonMarkインラインブロックにある閉じ列と対応付けない。"""
        path = _write(tmp_path / "doc.md", body)
        result = _run(str(path))
        assert result.returncode == 1
        assert "em-dash(U+2014)" in result.stderr

    @pytest.mark.parametrize(
        "body",
        [
            f"- item\n    `{_EM_DASH}`\n",
            f"- ``\n  {_EM_DASH}\n  ``\n",
            f"> ``\n> {_EM_DASH}\n> ``\n",
            f"`{_EM_DASH}\nheading `\n---\n",
            f"    {_EM_DASH}\n",
            f"```\n{_EM_DASH}\n```\n",
        ],
    )
    def test_commonmark_inline_and_code_blocks_are_excluded(self, tmp_path: pathlib.Path, body: str) -> None:
        """正当なコードスパンとコードブロック内の禁止文字は除外する。"""
        path = _write(tmp_path / "doc.md", body)
        result = _run(str(path))
        assert result.returncode == 0
        assert result.stderr == ""

    def test_non_ascii_dashes_end_url_and_are_detected(self, tmp_path: pathlib.Path) -> None:
        """URL風文字列内でもASCII外のダッシュ系禁止文字は検出する。"""
        path = _write(
            tmp_path / "doc.md",
            f"https://example.com/{_EM_DASH}/{_HORIZ_BAR}/{_BOX_DOUBLE}\n",
        )
        result = _run(str(path))
        assert result.returncode == 1
        assert "em-dash(U+2014)" in result.stderr
        assert "horizontal-bar(U+2015)" in result.stderr
        assert "double-dash(U+2500x2)" in result.stderr

    @pytest.mark.parametrize(
        "url",
        [
            "ftp://example.com/path",
            "custom+scheme://example.com/path",
            "www.example.com/path",
            "http://[::1]:8080/path",
        ],
    )
    def test_url_pattern_recognizes_supported_forms(self, url: str) -> None:
        """URLパターンが非HTTPスキーム・`www.`記法・IPv6を認識する。"""
        url_pattern = runpy.run_path(str(_SCRIPT))["_URL_RE"]
        assert url_pattern.fullmatch(url)

    @pytest.mark.parametrize(
        "url_prefix",
        [
            "ftp://example.com/path",
            "custom+scheme://example.com/path",
            "www.example.com/path",
            "http://[::1]:8080/path",
        ],
    )
    def test_non_http_and_ipv6_url_boundaries(self, tmp_path: pathlib.Path, url_prefix: str) -> None:
        """非HTTPスキーム・`www.`記法・IPv6でも日本語をURL境界として扱う。"""
        prefix = f"{url_prefix}を参照する"
        path = _write(tmp_path / "doc.md", f"{prefix}{_EM_DASH}外側\n")
        result = _run(str(path))
        assert result.returncode == 1
        assert f"{path}:1:{len(prefix) + 1}: em-dash(U+2014)" in result.stderr

    def test_url_exclusion_stops_at_japanese_text(self, tmp_path: pathlib.Path) -> None:
        """URL直後の日本語をURLに含めず、その後の禁止文字を検出する。"""
        prefix = "https://example.com/aを参照する"
        path = _write(tmp_path / "doc.md", f"{prefix}{_EM_DASH}外側\n")
        result = _run(str(path))
        assert result.returncode == 1
        assert f"{path}:1:{len(prefix) + 1}: em-dash(U+2014)" in result.stderr

    def test_relative_link_is_not_excluded(self, tmp_path: pathlib.Path) -> None:
        """スキームと`www.`を持たない相対リンク内は禁止文字を検出する。"""
        path = _write(tmp_path / "doc.md", f"docs/{_EM_DASH}/guide.md\n")
        result = _run(str(path))
        assert result.returncode == 1
        assert "em-dash(U+2014)" in result.stderr

    @pytest.mark.parametrize("delimiter", [">", " "])
    def test_url_exclusion_ends_at_delimiter(self, tmp_path: pathlib.Path, delimiter: str) -> None:
        """URL終端記号より後のダッシュは元の列番号で検出する。"""
        prefix = f"https://example.com/path{delimiter}"
        path = _write(tmp_path / "doc.md", f"{prefix}{_EM_DASH}外側\n")
        result = _run(str(path))
        assert result.returncode == 1
        assert f"{path}:1:{len(prefix) + 1}: em-dash(U+2014)" in result.stderr
        assert result.stderr.count("em-dash(U+2014)") == 1

    def test_four_space_indented_fence_is_not_excluded(self, tmp_path: pathlib.Path) -> None:
        """4文字インデントしたフェンス風の行はフェンスとして扱わない。"""
        path = _write(
            tmp_path / "doc.md",
            f"    ```text\n地の文{_EM_DASH}続き\n    ```\n",
        )
        result = _run(str(path))
        assert result.returncode == 1
        assert f"{path}:2:" in result.stderr

    def test_missing_file_silently_skipped(self, tmp_path: pathlib.Path) -> None:
        """存在しないファイルを渡してもexit 0（読み込み失敗は無視）。"""
        result = _run(str(tmp_path / "nope.md"))
        assert result.returncode == 0

    # ---- ディレクトリ再帰 ----

    def test_directory_recurses(self, tmp_path: pathlib.Path) -> None:
        """ディレクトリを渡すと再帰的に対象拡張子のファイルを走査する。"""
        sub = tmp_path / "docs"
        sub.mkdir()
        bad = _write(sub / "ng.md", f"{_EM_DASH}\n")
        skipped = _write(sub / "ignore.txt", f"{_EM_DASH}\n")
        result = _run(str(tmp_path))
        assert result.returncode == 1
        assert str(bad) in result.stderr
        assert str(skipped) not in result.stderr

    def test_directory_includes_md_tmpl(self, tmp_path: pathlib.Path) -> None:
        """ディレクトリ走査時に`.md.tmpl`二重拡張子も対象に含む。

        `.tmpl`単独は対象外であることも確認する。
        """
        md_tmpl = _write(tmp_path / "note.md.tmpl", f"{_EM_DASH}\n")
        plain_tmpl = _write(tmp_path / "raw.tmpl", f"{_EM_DASH}\n")
        result = _run(str(tmp_path))
        assert result.returncode == 1
        assert str(md_tmpl) in result.stderr
        assert str(plain_tmpl) not in result.stderr

    def test_directory_excludes_known_dirs(self, tmp_path: pathlib.Path) -> None:
        """`.git`等の既知の除外ディレクトリ配下はスキャン対象外。"""
        for excluded in (".git", ".venv", "node_modules", "__pycache__"):
            d = tmp_path / excluded
            d.mkdir()
            _write(d / "x.md", f"{_EM_DASH}\n")
        kept = _write(tmp_path / "kept.md", f"{_EM_DASH}\n")
        result = _run(str(tmp_path))
        assert result.returncode == 1
        assert str(kept) in result.stderr
        for excluded in (".git", ".venv", "node_modules", "__pycache__"):
            assert excluded not in result.stderr

    def test_directory_argument_with_excluded_name_is_scanned(self, tmp_path: pathlib.Path) -> None:
        """引数ディレクトリ自身の名前が除外集合と一致しても配下は走査する。

        境界値: 除外判定は引数ディレクトリからの相対パス成分のみで行うべきで、
        絶対パス全体に`site`等の汎用名が含まれても誤除外しない。
        """
        root = tmp_path / "site"
        root.mkdir()
        target = _write(root / "doc.md", f"{_EM_DASH}\n")
        result = _run(str(root))
        assert result.returncode == 1
        assert str(target) in result.stderr

    def test_multiple_files_aggregated(self, tmp_path: pathlib.Path) -> None:
        """複数ファイルの違反を集約して報告し、終了コード1。"""
        good = _write(tmp_path / "good.md", "正常なテキスト\n")
        bad = _write(tmp_path / "bad.md", f"{_EM_DASH}\n")
        result = _run(str(good), str(bad))
        assert result.returncode == 1
        assert str(bad) in result.stderr
        assert str(good) not in result.stderr
