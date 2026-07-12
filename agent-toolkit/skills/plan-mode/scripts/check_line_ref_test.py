"""agent-toolkit/skills/plan-mode/scripts/check_line_ref.py のテスト。

行番号への参照検査スクリプトをsubprocessで起動し、
違反検出・除外・語境界・出力形式・複数ファイル・ディレクトリ再帰を検証する。
パス実在検査・スキル名・サブエージェント名実在検査・件数表現検出（FB4）も併せて検証する。
"""

import pathlib
import subprocess
import sys

_SCRIPT = pathlib.Path(__file__).resolve().parent / "check_line_ref.py"


def _run(*args: str, cwd: pathlib.Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
        cwd=cwd,
    )


def _write(path: pathlib.Path, content: str) -> pathlib.Path:
    path.write_text(content, encoding="utf-8")
    return path


class TestCheckLineRef:
    """行番号への参照検査の主要シナリオをまとめて検証する。"""

    def test_l_single_form_is_detected(self, tmp_path: pathlib.Path) -> None:
        """`L34`単独形式は違反として報告される。"""
        path = _write(tmp_path / "doc.md", "地の文にL34が含まれる\n")
        result = _run(str(path))
        assert result.returncode == 1
        assert "line-ref" in result.stderr

    def test_l_range_form_is_detected(self, tmp_path: pathlib.Path) -> None:
        """`L29-30`範囲形式は違反として報告される。"""
        path = _write(tmp_path / "doc.md", "地の文にL29-30が含まれる\n")
        result = _run(str(path))
        assert result.returncode == 1

    def test_investigation_section_is_detected_without_marker(self, tmp_path: pathlib.Path) -> None:
        """`## 調査結果`配下でもマーカーが無ければ違反として報告される。"""
        path = _write(
            tmp_path / "doc.md",
            "## 調査結果\n\n地の文にL34が含まれる\n",
        )
        result = _run(str(path))
        assert result.returncode == 1

    def test_investigation_section_with_marker_is_suppressed(self, tmp_path: pathlib.Path) -> None:
        """`## 調査結果`配下かつ同一行マーカーは違反として報告されない。"""
        path = _write(
            tmp_path / "doc.md",
            "## 調査結果\n\nL34確定<!-- line-ref-ok -->\n",
        )
        result = _run(str(path))
        assert result.returncode == 0

    def test_marker_outside_investigation_is_ignored(self, tmp_path: pathlib.Path) -> None:
        """`## 調査結果`外の節ではマーカー付与でも違反として報告される。"""
        path = _write(
            tmp_path / "doc.md",
            "## 変更内容\n\nL34確定<!-- line-ref-ok -->\n",
        )
        result = _run(str(path))
        assert result.returncode == 1

    def test_japanese_adjacent_l_is_detected(self, tmp_path: pathlib.Path) -> None:
        """日本語隣接時も`L34`は違反として報告される。"""
        path = _write(tmp_path / "doc.md", "文中L34を含む\n")
        result = _run(str(path))
        assert result.returncode == 1

    def test_fenced_code_block_is_excluded(self, tmp_path: pathlib.Path) -> None:
        """フェンス付きコードブロック内のL34は違反として報告されない。"""
        path = _write(tmp_path / "doc.md", "```text\nL34\n```\n")
        result = _run(str(path))
        assert result.returncode == 0

    def test_inline_code_is_excluded(self, tmp_path: pathlib.Path) -> None:
        """インラインコード内の`L34`は違反として報告されない。"""
        path = _write(tmp_path / "doc.md", "地の文に`L34`を含む\n")
        result = _run(str(path))
        assert result.returncode == 0

    def test_html5_is_not_detected(self, tmp_path: pathlib.Path) -> None:
        """語境界により`HTML5`は違反として報告されない。"""
        path = _write(tmp_path / "doc.md", "HTML5仕様に基づく\n")
        result = _run(str(path))
        assert result.returncode == 0

    def test_url2_is_not_detected(self, tmp_path: pathlib.Path) -> None:
        """語境界により`URL2`は違反として報告されない。"""
        path = _write(tmp_path / "doc.md", "URL2の仕様\n")
        result = _run(str(path))
        assert result.returncode == 0

    def test_output_format(self, tmp_path: pathlib.Path) -> None:
        """出力形式は`path:line:col`形式で報告される。"""
        path = _write(tmp_path / "doc.md", "L34を含む\n")
        result = _run(str(path))
        assert f"{path}:1:" in result.stderr

    def test_multiple_files_and_directory(self, tmp_path: pathlib.Path) -> None:
        """複数ファイル・ディレクトリ展開・非対象拡張子スキップ。"""
        (tmp_path / "a.md").write_text("L34\n", encoding="utf-8")
        (tmp_path / "b.txt").write_text("L34\n", encoding="utf-8")
        result = _run(str(tmp_path))
        assert result.returncode == 1
        assert "a.md" in result.stderr
        assert "b.txt" not in result.stderr

    def test_missing_file_silently_skipped(self, tmp_path: pathlib.Path) -> None:
        """存在しないファイルを渡してもexit 0（読み込み失敗は無視）。"""
        result = _run(str(tmp_path / "nope.md"))
        assert result.returncode == 0

    def test_directory_includes_md_tmpl(self, tmp_path: pathlib.Path) -> None:
        """ディレクトリ走査時に`.md.tmpl`二重拡張子も対象に含む。

        `.tmpl`単独は対象外であることも確認する。
        """
        md_tmpl = _write(tmp_path / "note.md.tmpl", "L34\n")
        plain_tmpl = _write(tmp_path / "raw.tmpl", "L34\n")
        result = _run(str(tmp_path))
        assert result.returncode == 1
        assert str(md_tmpl) in result.stderr
        assert str(plain_tmpl) not in result.stderr

    def test_directory_excludes_known_dirs(self, tmp_path: pathlib.Path) -> None:
        """`.git`等の既知の除外ディレクトリ配下はスキャン対象外。"""
        for excluded in (".git", ".venv", "node_modules", "__pycache__"):
            d = tmp_path / excluded
            d.mkdir()
            _write(d / "x.md", "L34\n")
        kept = _write(tmp_path / "kept.md", "L34\n")
        result = _run(str(tmp_path))
        assert result.returncode == 1
        assert str(kept) in result.stderr
        for excluded in (".git", ".venv", "node_modules", "__pycache__"):
            assert excluded not in result.stderr

    def test_nested_fence_with_shorter_inner_marker_is_ignored(self, tmp_path: pathlib.Path) -> None:
        """開始4個・内側3個のネストしたフェンスで、内側フェンス内の`L34`は誤検出されない。

        内側の閉じ候補（3個）は開始フェンス（4個）より短いため閉じ判定に使われず、
        外側の閉じフェンス（4個）以降の地の文でのみ`L34`が検出される。
        """
        path = _write(
            tmp_path / "doc.md",
            "````text\n```\nL34\n```\n````\nその後L34を含む\n",
        )
        result = _run(str(path))
        assert result.returncode == 1
        assert f"{path}:3:" not in result.stderr
        assert f"{path}:6:" in result.stderr

    def test_different_fence_kind_inside_is_ignored(self, tmp_path: pathlib.Path) -> None:
        """開始3個のバッククォートフェンス内部に`~~~`（別種フェンス）が出現しても無視される。"""
        path = _write(
            tmp_path / "doc.md",
            "```text\n~~~\nL34\n~~~\n```\nその後L34を含む\n",
        )
        result = _run(str(path))
        assert result.returncode == 1
        assert f"{path}:3:" not in result.stderr
        assert f"{path}:6:" in result.stderr

    def test_line_number_suffix_form_is_detected(self, tmp_path: pathlib.Path) -> None:
        """`85行目`形式は違反として報告される。"""
        path = _write(tmp_path / "doc.md", "地の文に85行目が含まれる\n")
        result = _run(str(path))
        assert result.returncode == 1

    def test_line_range_suffix_form_is_detected(self, tmp_path: pathlib.Path) -> None:
        """`85-90行`形式は違反として報告される。"""
        path = _write(tmp_path / "doc.md", "地の文に85-90行が含まれる\n")
        result = _run(str(path))
        assert result.returncode == 1

    def test_line_range_kara_form_is_detected(self, tmp_path: pathlib.Path) -> None:
        """`85から90行`形式は違反として報告される。"""
        path = _write(tmp_path / "doc.md", "地の文に85から90行が含まれる\n")
        result = _run(str(path))
        assert result.returncode == 1

    def test_l_with_trailing_alnum_is_not_detected(self, tmp_path: pathlib.Path) -> None:
        """後方英数字境界により`L34a`は違反として報告されない。"""
        path = _write(tmp_path / "doc.md", "識別子L34aを含む\n")
        result = _run(str(path))
        assert result.returncode == 0

    def test_all_patterns_suppressed_in_investigation_with_marker(self, tmp_path: pathlib.Path) -> None:
        """全パターンが`## 調査結果`配下でマーカー付き行は違反として報告されない。"""
        path = _write(
            tmp_path / "doc.md",
            "## 調査結果\n\n"
            "L34確定<!-- line-ref-ok -->\n"
            "85行目確定<!-- line-ref-ok -->\n"
            "85-90行確定<!-- line-ref-ok -->\n"
            "85から90行確定<!-- line-ref-ok -->\n",
        )
        result = _run(str(path))
        assert result.returncode == 0

    def test_all_patterns_detected_outside_investigation_with_marker(self, tmp_path: pathlib.Path) -> None:
        """全パターンが`## 調査結果`外の節ではマーカー付与でも違反として報告される。"""
        path = _write(
            tmp_path / "doc.md",
            "## 変更内容\n\n"
            "L34確定<!-- line-ref-ok -->\n"
            "85行目確定<!-- line-ref-ok -->\n"
            "85-90行確定<!-- line-ref-ok -->\n"
            "85から90行確定<!-- line-ref-ok -->\n",
        )
        result = _run(str(path))
        assert result.returncode == 1
        assert len(result.stderr.splitlines()) == 4

    def test_directory_argument_with_excluded_name_is_scanned(self, tmp_path: pathlib.Path) -> None:
        """引数ディレクトリ自身の名前が除外集合と一致しても配下は走査する。

        境界値: 除外判定は引数ディレクトリからの相対パス成分のみで行うべきで、
        絶対パス全体に`site`等の汎用名が含まれても誤除外しない。
        """
        root = tmp_path / "site"
        root.mkdir()
        target = _write(root / "doc.md", "L34\n")
        result = _run(str(root))
        assert result.returncode == 1
        assert str(target) in result.stderr

    def test_check_path_existence_detects_missing_path(self, tmp_path: pathlib.Path) -> None:
        """存在しないパス記載を検出する。"""
        path = _write(tmp_path / "doc.md", "対象は`docs/missing.md`である。\n")
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 1
        assert "docs/missing.md" in result.stderr
        assert "実在しない" in result.stderr

    def test_check_path_existence_passes_for_existing_path(self, tmp_path: pathlib.Path) -> None:
        """実在するパス記載は違反として報告されない。"""
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "existing.md").write_text("x\n", encoding="utf-8")
        path = _write(tmp_path / "doc.md", "対象は`docs/existing.md`である。\n")
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 0

    def test_check_path_existence_ignores_url_scheme(self, tmp_path: pathlib.Path) -> None:
        """`://`を含むURLトークンはリポジトリパスとして誤検出しない。"""
        path = _write(
            tmp_path / "doc.md",
            "詳細は`https://example.com/docs/guide.md`を参照する。\n",
        )
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 0

    def test_check_path_existence_detects_missing_path_for_extended_extensions(self, tmp_path: pathlib.Path) -> None:
        """`.sh`・`.yaml`・`.yml`・`.cmd`・`.ps1`・`.tmpl`拡張子の存在しないパス記載も検出する。"""
        path = _write(
            tmp_path / "doc.md",
            "対象は`scripts/missing.sh`・`config/missing.yaml`・`config/missing.yml`・"
            "`bin/missing.cmd`・`bin/missing.ps1`・`templates/missing.tmpl`である。\n",
        )
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 1
        for missing in (
            "scripts/missing.sh",
            "config/missing.yaml",
            "config/missing.yml",
            "bin/missing.cmd",
            "bin/missing.ps1",
            "templates/missing.tmpl",
        ):
            assert missing in result.stderr

    def test_check_skill_name_existence_detects_unknown_skill(self, tmp_path: pathlib.Path) -> None:
        """実在しないスキル名記載を検出する。"""
        path = _write(tmp_path / "doc.md", "`agent-toolkit:no-such-skill`を呼び出す。\n")
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 1
        assert "agent-toolkit:no-such-skill" in result.stderr
        assert "実在しない" in result.stderr

    def test_check_skill_name_existence_passes_for_existing_skill(self, tmp_path: pathlib.Path) -> None:
        """実在するスキル名記載は違反として報告されない。"""
        skill_dir = tmp_path / "agent-toolkit" / "skills" / "existing-skill"
        skill_dir.mkdir(parents=True)
        path = _write(tmp_path / "doc.md", "`agent-toolkit:existing-skill`を呼び出す。\n")
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 0

    def test_check_skill_name_existence_passes_for_existing_subagent(self, tmp_path: pathlib.Path) -> None:
        """実在するサブエージェント名記載は違反として報告されない。"""
        agents_dir = tmp_path / "agent-toolkit" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "plan-impl-executor.md").write_text("x\n", encoding="utf-8")
        path = _write(tmp_path / "doc.md", "`agent-toolkit:plan-impl-executor`を起動する。\n")
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 0

    def test_check_skill_name_existence_detects_unknown_subagent(self, tmp_path: pathlib.Path) -> None:
        """`agent-toolkit/agents/`配下・`.claude/agents/`配下いずれにも存在しないサブエージェント名を検出する。"""
        path = _write(tmp_path / "doc.md", "`agent-toolkit:nonexistent`を起動する。\n")
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 1
        assert "agent-toolkit:nonexistent" in result.stderr
        assert "スキル名・サブエージェント名`agent-toolkit:nonexistent`が実在しない" in result.stderr

    def test_check_count_expressions_detects_forbidden_wording(self, tmp_path: pathlib.Path) -> None:
        """「以下N件」等の件数表現を検出する。"""
        path = _write(tmp_path / "doc.md", "以下7件の指摘を反映する。\n")
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 1
        assert "7件" in result.stderr

    def test_check_count_expressions_detects_standalone_wording(self, tmp_path: pathlib.Path) -> None:
        """「以下」を伴わない単独「N件」「N点」形式の件数表現も検出する。"""
        path = _write(tmp_path / "doc.md", "今回は5件の指摘と3点の改善案がある。\n")
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 1
        assert "5件" in result.stderr
        assert "3点" in result.stderr

    def test_check_count_expressions_marker_suppresses_aggregate_value(self, tmp_path: pathlib.Path) -> None:
        """`<!-- line-ref-ok -->`マーカー付き行の集計値は件数表現として検出しない。"""
        path = _write(
            tmp_path / "doc.md",
            "既存違反7件・修正5件を解消した。<!-- line-ref-ok -->\n",
        )
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 0

    def test_check_count_expressions_detects_kanten_wording(self, tmp_path: pathlib.Path) -> None:
        """「N観点」形式の件数表現を検出する。"""
        path = _write(tmp_path / "doc.md", "3観点で確認する。\n")
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 1
        assert "3観点" in result.stderr

    def test_check_count_expressions_passes_without_count_wording(self, tmp_path: pathlib.Path) -> None:
        """件数表現を含まない本文は違反として報告されない。"""
        path = _write(tmp_path / "doc.md", "複数の観点で確認する。\n")
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 0

    def test_next_n_files_detected(self, tmp_path: pathlib.Path) -> None:
        """「次の3ファイル」の件数表現を検出する。"""
        path = _write(tmp_path / "doc.md", "次の3ファイルを改訂する。\n")
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 1
        assert "次の3ファイル" in result.stderr

    def test_below_n_variants_detected(self, tmp_path: pathlib.Path) -> None:
        """「以下の2バリアント」の件数表現を検出する。"""
        path = _write(tmp_path / "doc.md", "以下の2バリアントを比較する。\n")
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 1
        assert "以下の2バリアント" in result.stderr

    def test_next_prefix_with_existing_vocabulary(self, tmp_path: pathlib.Path) -> None:
        """「次の5件」が既存語彙パターンの接頭辞拡張として検出される。"""
        path = _write(tmp_path / "doc.md", "次の5件を反映する。\n")
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 1
        assert "5件" in result.stderr

    def test_next_n_no_duplicate_report(self, tmp_path: pathlib.Path) -> None:
        """「次の5件」に対する違反行が重複出力されない。"""
        path = _write(tmp_path / "doc.md", "次の5件を反映する。\n")
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 1
        assert len(result.stderr.splitlines()) == 1

    def test_check_path_existence_excludes_newly_created_marker(self, tmp_path: pathlib.Path) -> None:
        """対象ファイル一覧の新設マーカー付きパスは実在確認から除外する。"""
        path = _write(
            tmp_path / "doc.md",
            "## 変更内容\n\n"
            "### 対象ファイル一覧\n\n"
            "- [ ] `docs/new-doc.md`（新設, 見込み10行）\n\n"
            "### `docs/new-doc.md`\n\n"
            "対象は`docs/new-doc.md`である。\n",
        )
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 0

    def test_check_path_existence_resolves_repo_root_from_git_ancestor(self, tmp_path: pathlib.Path) -> None:
        """`.git`を持つ祖先ディレクトリをリポジトリルートとして解決し、cwdが下位でも実在判定が揺れない。"""
        (tmp_path / ".git").mkdir()
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "existing.md").write_text("x\n", encoding="utf-8")
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        path = _write(subdir / "doc.md", "対象は`docs/existing.md`である。\n")
        result = _run(str(path), cwd=subdir)
        assert result.returncode == 0

    def test_check_skill_name_existence_excludes_planned_new_skill(self, tmp_path: pathlib.Path) -> None:
        """同一計画内で新設予定と明記されたスキル名は実在確認から除外する。"""
        path = _write(
            tmp_path / "doc.md",
            "## 変更内容\n\n"
            "### 対象ファイル一覧\n\n"
            "- [ ] `agent-toolkit/skills/new-skill/SKILL.md`（新設, 見込み50行）\n\n"
            "### `agent-toolkit/skills/new-skill/SKILL.md`\n\n"
            "`agent-toolkit:new-skill`を新設する。\n",
        )
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 0

    def test_check_path_existence_ignores_home_prefixed_path(self, tmp_path: pathlib.Path) -> None:
        """`~/`始まりのホームディレクトリパスはリポジトリ相対パスとして検査対象外とする。"""
        path = _write(tmp_path / "doc.md", "対象は`~/foo/bar.md`である。\n")
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 0

    def test_check_path_existence_ignores_glob_pattern(self, tmp_path: pathlib.Path) -> None:
        """`*`・`?`・`[`を含むglobパターンは検査対象外とする。"""
        path = _write(tmp_path / "doc.md", "対象は`agent-toolkit/**/*.md`である。\n")
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 0

    def test_check_path_existence_ignores_absolute_path(self, tmp_path: pathlib.Path) -> None:
        """`/`始まりの絶対パスはリポジトリ相対パスとして検査対象外とする。"""
        path = _write(tmp_path / "doc.md", "対象は`/absolute/missing/path.md`である。\n")
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 0

    def test_check_path_existence_detects_relative_missing_path(self, tmp_path: pathlib.Path) -> None:
        """通常のリポジトリ相対パスは検査対象として実在確認する。"""
        path = _write(tmp_path / "doc.md", "対象は`foo/bar.md`である。\n")
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 1
        assert "foo/bar.md" in result.stderr
