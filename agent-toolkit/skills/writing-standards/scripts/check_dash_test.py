"""agent-toolkit/skills/writing-standards/scripts/check_dash.py のテスト。

ダッシュ系禁止文字検査スクリプトをsubprocessで起動し、
違反検出・除外・出力形式・ディレクトリ再帰・拡張子フィルタを検証する。
"""

import pathlib
import subprocess
import sys

_SCRIPT = pathlib.Path(__file__).resolve().parent / "check_dash.py"

# 検出対象文字（テスト内でのリテラル直書き）
_EM_DASH = "—"  # —
_HORIZ_BAR = "―"  # ―
_BOX_SINGLE = "─"  # ─
_BOX_DOUBLE = "──"  # ── (2倍ダッシュ)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


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
