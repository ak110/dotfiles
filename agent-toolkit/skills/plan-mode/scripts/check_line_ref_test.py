"""agent-toolkit/skills/plan-mode/scripts/check_line_ref.py のテスト。

行番号への参照検査スクリプトをsubprocessで起動し、
違反検出・除外・語境界・出力形式・複数ファイル・ディレクトリ再帰を検証する。
パス実在検査・スキル名・サブエージェント名実在検査・件数表現検出（FB4）・
節名参照の実在照合（FB3）・裸節名参照の実在照合（FB5）も併せて検証する。
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

    def test_check_path_existence_excludes_skill_relative_suffix_of_new_path(self, tmp_path: pathlib.Path) -> None:
        """新設パスのスキル相対裸表記（`references/xxx.md`形式）も実在確認から除外する。"""
        marker_path = "agent-toolkit/skills/sample-skill/references/new-file.md"
        path = _write(
            tmp_path / "doc.md",
            "## 変更内容\n\n"
            "### 対象ファイル一覧\n\n"
            f"- [ ] `{marker_path}`（新設, 見込み10行）\n\n"
            "対象は`references/new-file.md`である。\n",
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

    def test_check_path_existence_does_not_exclude_non_references_suffix_match(self, tmp_path: pathlib.Path) -> None:
        """新設マーカーが`references/`以外のディレクトリの場合、無関係な同名サフィックスは除外されない。

        新設マーカー`agent-toolkit/skills/foo-skill/scripts/helper.py`に対し、
        本文中の無関係な`scripts/helper.py`という実在しないパス記載はサフィックス一致で
        誤って除外されず、「実在しない」として検出される。
        """
        path = _write(
            tmp_path / "doc.md",
            "## 変更内容\n\n"
            "### 対象ファイル一覧\n\n"
            "- [ ] `agent-toolkit/skills/foo-skill/scripts/helper.py`（新設, 見込み10行）\n\n"
            "対象は`scripts/helper.py`である。\n",
        )
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 1
        assert "scripts/helper.py" in result.stderr
        assert "実在しない" in result.stderr

    def test_check_path_existence_skips_excluded_dir_prefix(self, tmp_path: pathlib.Path) -> None:
        """`.venv`・`node_modules`始まりの実在しないパスは`_EXCLUDED_DIRS`除外により違反対象外。"""
        path = _write(
            tmp_path / "doc.md",
            "参考: `.venv/lib/foo.py` の記述。\n参考: `node_modules/pkg/foo.py` の記述。\n",
        )
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 0


class TestSectionNameExistence:
    """節名参照の実在照合（FB3対応）の主要シナリオをまとめて検証する。"""

    def test_existing_section_name_passes(self, tmp_path: pathlib.Path) -> None:
        """対象ファイル内に実在する節名参照は違反として報告されない。"""
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "guide.md").write_text("## 変更内容\n\n本文。\n", encoding="utf-8")
        path = _write(tmp_path / "doc.md", "詳細は`docs/guide.md`「変更内容」節を参照する。\n")
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 0

    def test_missing_section_name_is_detected(self, tmp_path: pathlib.Path) -> None:
        """対象ファイル内に存在しない節名参照は違反として報告される。"""
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "guide.md").write_text("## 変更内容\n\n本文。\n", encoding="utf-8")
        path = _write(tmp_path / "doc.md", "詳細は`docs/guide.md`「存在しない節」節を参照する。\n")
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 1
        assert "節名不在" in result.stderr
        assert "存在しない節" in result.stderr

    def test_skill_ref_path_resolution_passes_for_existing_section(self, tmp_path: pathlib.Path) -> None:
        """`agent-toolkit:<skill>`形式は`agent-toolkit/skills/<skill>/SKILL.md`へ解決され、実在節名は違反にならない。"""
        skill_dir = tmp_path / "agent-toolkit" / "skills" / "sample-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("## 使い方\n\n本文。\n", encoding="utf-8")
        path = _write(tmp_path / "doc.md", "詳細は`agent-toolkit:sample-skill`「使い方」節を参照する。\n")
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 0

    def test_skill_ref_path_resolution_detects_missing_section(self, tmp_path: pathlib.Path) -> None:
        """`agent-toolkit:<skill>`形式で解決した対象ファイルに存在しない節名は違反として報告される。"""
        skill_dir = tmp_path / "agent-toolkit" / "skills" / "sample-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("## 使い方\n\n本文。\n", encoding="utf-8")
        path = _write(tmp_path / "doc.md", "詳細は`agent-toolkit:sample-skill`「存在しない節」節を参照する。\n")
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 1
        assert "節名不在" in result.stderr
        assert "存在しない節" in result.stderr

    def test_section_ref_inside_normal_fence_is_excluded(self, tmp_path: pathlib.Path) -> None:
        """通常の言語指定付きコードフェンス内の節名参照は検査対象外。"""
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "guide.md").write_text("## 変更内容\n\n本文。\n", encoding="utf-8")
        path = _write(
            tmp_path / "doc.md",
            "```python\n# 詳細は`docs/guide.md`「存在しない節」節を参照する。\n```\n",
        )
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 0

    def test_section_ref_inside_labeled_frontmatter_subfence_is_detected(self, tmp_path: pathlib.Path) -> None:
        """`[追記（frontmatter）]`サブラベル配下の`text`フェンス内文面は検査対象に含まれる。"""
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "guide.md").write_text("## 変更内容\n\n本文。\n", encoding="utf-8")
        path = _write(
            tmp_path / "doc.md",
            "```text\n[追記（frontmatter）]\n詳細は`docs/guide.md`「存在しない節」節を参照する。\n```\n",
        )
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 1
        assert "節名不在" in result.stderr

    def test_section_ref_inside_unlabeled_text_fence_is_excluded(self, tmp_path: pathlib.Path) -> None:
        """ラベル無しの`text`フェンス内の節名参照は検査対象外。"""
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "guide.md").write_text("## 変更内容\n\n本文。\n", encoding="utf-8")
        path = _write(
            tmp_path / "doc.md",
            "```text\n詳細は`docs/guide.md`「存在しない節」節を参照する。\n```\n",
        )
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 0

    def test_section_ref_under_investigation_with_marker_is_suppressed(self, tmp_path: pathlib.Path) -> None:
        """`## 調査結果`配下かつ同一行マーカー付きの節名参照は違反として報告されない。"""
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "guide.md").write_text("## 変更内容\n\n本文。\n", encoding="utf-8")
        path = _write(
            tmp_path / "doc.md",
            "## 調査結果\n\n詳細は`docs/guide.md`「存在しない節」節を参照する。<!-- line-ref-ok -->\n",
        )
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 0

    def test_section_ref_under_background_is_excluded(self, tmp_path: pathlib.Path) -> None:
        """`## 背景`配下の原文転記領域内の節名参照は検査対象外。"""
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "guide.md").write_text("## 変更内容\n\n本文。\n", encoding="utf-8")
        path = _write(
            tmp_path / "doc.md",
            "## 背景\n\n詳細は`docs/guide.md`「存在しない節」節を参照する。\n",
        )
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 0

    def test_skill_relative_path_fallback_resolves_via_skills_search(self, tmp_path: pathlib.Path) -> None:
        """スキル相対パスが実在しない場合、agent-toolkit/skills配下から実在解決される。

        計画ファイルの`[追記]`・`[置換後]`ブロックへの転記を模すため、参照はラベル付き
        フェンス内へ置く（`_check_path_existence`はフェンス内を除外し、
        `_check_section_name_existence`のみがフェンス内文面を検査するため）。
        """
        skill_dir = tmp_path / "agent-toolkit" / "skills" / "sample-skill" / "references"
        skill_dir.mkdir(parents=True)
        (skill_dir / "guide.md").write_text("## Usage\n\nBody.\n", encoding="utf-8")
        path = _write(
            tmp_path / "doc.md",
            "```text\n[追記]\n詳細は`references/guide.md`「Usage」節を参照する。\n```\n",
        )
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 0

    def test_skill_relative_path_fallback_detects_missing_section(self, tmp_path: pathlib.Path) -> None:
        """フォールバック解決した対象ファイルに存在しない節名は違反として報告される。"""
        skill_dir = tmp_path / "agent-toolkit" / "skills" / "sample-skill" / "references"
        skill_dir.mkdir(parents=True)
        (skill_dir / "guide.md").write_text("## Usage\n\nBody.\n", encoding="utf-8")
        path = _write(
            tmp_path / "doc.md",
            "```text\n[追記]\n詳細は`references/guide.md`「Missing」節を参照する。\n```\n",
        )
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 1
        assert "節名不在" in result.stderr

    def test_skill_relative_path_fallback_can_misresolve_to_unrelated_same_name_file(self, tmp_path: pathlib.Path) -> None:
        """複数スキル配下に同名ファイルが存在する場合、意図しないファイルへ誤解決され得る。

        フォールバックは`agent-toolkit/skills/*/`配下をアルファベット順に探索し先頭一致を採用するため、
        参照元が意図した対象と異なるスキル配下の無関係な同名ファイルへ誤って解決される可能性がある。
        本テストは、意図した対象（`zzz-skill`配下のguide.md）に実在する節が、
        アルファベット順で先に一致する無関係な`aaa-skill`配下の同名ファイルへの誤解決により、
        誤って「節名不在」と判定されることを示す。
        """
        aaa_dir = tmp_path / "agent-toolkit" / "skills" / "aaa-skill" / "references"
        aaa_dir.mkdir(parents=True)
        (aaa_dir / "guide.md").write_text("## Unrelated\n\nBody.\n", encoding="utf-8")
        zzz_dir = tmp_path / "agent-toolkit" / "skills" / "zzz-skill" / "references"
        zzz_dir.mkdir(parents=True)
        (zzz_dir / "guide.md").write_text("## Usage\n\nBody.\n", encoding="utf-8")
        path = _write(
            tmp_path / "doc.md",
            "```text\n[追記]\n詳細は`references/guide.md`「Usage」節を参照する。\n```\n",
        )
        result = _run(str(path), cwd=tmp_path)
        # 意図した対象（zzz-skill配下）には「Usage」節が実在するが、アルファベット順で
        # 先に一致するaaa-skill配下の無関係な同名ファイルへ誤解決されるため誤検知が発生する。
        assert result.returncode == 1
        assert "節名不在" in result.stderr

    def test_new_marker_path_basename_only_ref_is_not_excluded(self, tmp_path: pathlib.Path) -> None:
        """新設マーカーが`references/xxx.md`形式でも、ベースファイル名のみの参照は除外されない。

        新設マーカー`agent-toolkit/skills/foo-skill/references/guide.md`に対し、
        本文中のベースファイル名一致のみの`guide.md`「概要」節という参照は
        サフィックス一致の対象外（`references/`始まりのトークンではない）となり、
        節名不在検査自体がスキップされず「節名不在」として検出される。
        """
        marker_path = "agent-toolkit/skills/foo-skill/references/guide.md"
        body = (
            "## 変更内容\n\n### 対象ファイル一覧\n\n"
            f"- [ ] `{marker_path}`（新設, 見込み20行）\n\n"
            "対象は`guide.md`「概要」節を参照する。\n"
        )
        path = _write(tmp_path / "plan.md", body)
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 1
        assert "節名不在" in result.stderr

    def test_new_marker_path_with_skill_relative_ref_is_excluded(self, tmp_path: pathlib.Path) -> None:
        """新設マーカー（フルパス）と本文のスキル相対裸表記が対応する節参照は検査対象外。"""
        skill_dir = tmp_path / "agent-toolkit" / "skills" / "sample-skill" / "references"
        skill_dir.mkdir(parents=True)
        marker_path = "agent-toolkit/skills/sample-skill/references/new-file.md"
        body = (
            "## 変更内容\n\n### 対象ファイル一覧\n\n"
            f"- [ ] `{marker_path}`（新設, 見込み20行）\n\n"
            "`references/new-file.md`「概要」節を参照する。\n"
        )
        path = _write(tmp_path / "plan.md", body)
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 0

    def test_nested_python_fence_inside_labeled_text_fence_excluded(self, tmp_path: pathlib.Path) -> None:
        """ラベル付き`text`フェンス内のpythonフェンス内にある節名参照は検査対象外。"""
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "guide.md").write_text("## 変更内容\n\n本文。\n", encoding="utf-8")
        path = _write(
            tmp_path / "doc.md",
            '````text\n[追記]\n```python\ntext = "`docs/guide.md`「存在しない節」節を参照する。"\n```\n````\n',
        )
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 0

    def test_nested_fence_close_does_not_terminate_outer_fence(self, tmp_path: pathlib.Path) -> None:
        """内側フェンスの閉じでは外側フェンスが維持される。"""
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "guide.md").write_text("## 変更内容\n\n本文。\n", encoding="utf-8")
        path = _write(
            tmp_path / "doc.md",
            "````text\n[追記]\n```python\n"
            'text = "`docs/guide.md`「存在しない節」節を参照する。"\n'
            "```\n`docs/guide.md`「存在しない節」節を参照する。\n````\n",
        )
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 1
        assert len(result.stderr.splitlines()) == 1
        assert "6行目" in result.stderr

    def test_nested_fence_still_detects_outer_text_section_ref_violation(self, tmp_path: pathlib.Path) -> None:
        """内側フェンス外・外側フェンス内の節名参照違反は引き続き検出される。"""
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "guide.md").write_text("## 変更内容\n\n本文。\n", encoding="utf-8")
        path = _write(
            tmp_path / "doc.md",
            "````text\n[追記]\n`docs/guide.md`「存在しない節」節を参照する。\n"
            "```python\n"
            'text = "`docs/guide.md`「別の存在しない節」節を参照する。"\n'
            "```\n````\n",
        )
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 1
        assert len(result.stderr.splitlines()) == 1
        assert "3行目" in result.stderr
        assert "別の存在しない節" not in result.stderr

    def test_inner_fence_single_marker_char_differs(self, tmp_path: pathlib.Path) -> None:
        """内側フェンスのマーカー文字が外側と異なる場合もネスト除外が動作する。"""
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "guide.md").write_text("## 変更内容\n\n本文。\n", encoding="utf-8")
        path = _write(
            tmp_path / "doc.md",
            '~~~text\n[追記]\n```python\ntext = "`docs/guide.md`「存在しない節」節を参照する。"\n```\n~~~\n',
        )
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 0

    def test_inner_fence_same_marker_char_same_length_closes_outer(self, tmp_path: pathlib.Path) -> None:
        """外側と同一マーカー文字・同一長のフェンスは既存ロジック通り外側フェンスを閉じる。"""
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "guide.md").write_text("## 変更内容\n\n本文。\n", encoding="utf-8")
        path = _write(
            tmp_path / "doc.md",
            "```text\n[追記]\n```\n`docs/guide.md`「存在しない節」節を参照する。\n",
        )
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 1
        assert "4行目" in result.stderr

    def test_inner_fence_longer_marker_closes_outer(self, tmp_path: pathlib.Path) -> None:
        """外側より長い同種マーカーは既存ロジック通り外側フェンスを閉じる。"""
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "guide.md").write_text("## 変更内容\n\n本文。\n", encoding="utf-8")
        path = _write(
            tmp_path / "doc.md",
            "```text\n[追記]\n````python\n`docs/guide.md`「存在しない節」節を参照する。\n",
        )
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 1
        assert "4行目" in result.stderr


class TestBareSectionNameExistence:
    """裸節名参照の実在照合（FB5対応）の主要シナリオをまとめて検証する。"""

    def test_existing_section_name_passes(self, tmp_path: pathlib.Path) -> None:
        """対象H3のファイル内に実在する裸節名参照は違反として報告されない。"""
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "guide.md").write_text("## 使い方\n\n本文。\n", encoding="utf-8")
        path = _write(
            tmp_path / "plan.md",
            "## 変更内容\n\n### `docs/guide.md`\n\n「使い方」節を参照する。\n",
        )
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 0

    def test_missing_section_name_is_detected(self, tmp_path: pathlib.Path) -> None:
        """対象H3のファイル内に存在しない裸節名参照は違反として報告される。"""
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "guide.md").write_text("## 使い方\n\n本文。\n", encoding="utf-8")
        path = _write(
            tmp_path / "plan.md",
            "## 変更内容\n\n### `docs/guide.md`\n\n「存在しない節」節を参照する。\n",
        )
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 1
        assert "節名不在" in result.stderr
        assert "存在しない節" in result.stderr

    def test_section_marker_suppresses_same_line(self, tmp_path: pathlib.Path) -> None:
        """同一行の`<!-- section-ref-ok -->`は裸節名参照違反を抑止する。"""
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "guide.md").write_text("## 使い方\n\n本文。\n", encoding="utf-8")
        path = _write(
            tmp_path / "plan.md",
            "## 変更内容\n\n### `docs/guide.md`\n\n「存在しない節」節を参照する。<!-- section-ref-ok -->\n",
        )
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 0

    def test_bare_ref_outside_change_content_is_excluded(self, tmp_path: pathlib.Path) -> None:
        """`## 変更内容`H2配下以外の裸節名参照は検査対象外。"""
        path = _write(tmp_path / "plan.md", "## 調査結果\n\n「存在しない節」節を参照する。\n")
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 0

    def test_backticked_h3_path_resolves_target(self, tmp_path: pathlib.Path) -> None:
        """バッククォート囲みパスのH3から対象ファイルを解決する。"""
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "guide.md").write_text("## 仕様\n\n本文。\n", encoding="utf-8")
        path = _write(
            tmp_path / "plan.md",
            "## 変更内容\n\n### `docs/guide.md`\n\n```text\n[追記]\n「仕様」節を参照する。\n```\n",
        )
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 0

    def test_bare_h3_path_resolves_target(self, tmp_path: pathlib.Path) -> None:
        """裸パスのH3から対象ファイルを解決する。"""
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "guide.md").write_text("## 仕様\n\n本文。\n", encoding="utf-8")
        path = _write(
            tmp_path / "plan.md",
            "## 変更内容\n\n### docs/guide.md\n\n「仕様」節を参照する。\n",
        )
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 0

    def test_annotated_backticked_h3_path_detects_existing_section(self, tmp_path: pathlib.Path) -> None:
        """行数注記付きバッククォート囲みH3から対象パスを抽出し、実在節名を照合する。"""
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "guide.md").write_text("## 仕様\n\n本文。\n", encoding="utf-8")
        path = _write(
            tmp_path / "plan.md",
            "## 変更内容\n\n### `docs/guide.md`（現行10行, 見込み12行）\n\n「仕様」節を参照する。\n",
        )
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 0

    def test_annotated_backticked_h3_path_detects_missing_section(self, tmp_path: pathlib.Path) -> None:
        """行数注記付きバッククォート囲みH3でも裸節名参照違反を検出する。"""
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "guide.md").write_text("## 仕様\n\n本文。\n", encoding="utf-8")
        path = _write(
            tmp_path / "plan.md",
            "## 変更内容\n\n### `docs/guide.md`（現行10行, 見込み12行）\n\n「存在しない節」節を参照する。\n",
        )
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 1
        assert "節名不在" in result.stderr
        assert "存在しない節" in result.stderr

    def test_annotated_bare_h3_path_resolves_target(self, tmp_path: pathlib.Path) -> None:
        """丸括弧注記が直接続く裸パスH3から対象パスを抽出する。"""
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "guide.md").write_text("## 仕様\n\n本文。\n", encoding="utf-8")
        path = _write(
            tmp_path / "plan.md",
            "## 変更内容\n\n### docs/guide.md（現行10行, 見込み12行）\n\n「仕様」節を参照する。\n",
        )
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 0

    def test_bare_ref_outside_target_h3_is_excluded(self, tmp_path: pathlib.Path) -> None:
        """対応する対象H3配下以外の裸節名参照は検査対象外。"""
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "guide.md").write_text("## 仕様\n\n本文。\n", encoding="utf-8")
        path = _write(
            tmp_path / "plan.md",
            "## 変更内容\n\n"
            "「存在しない節」節を参照する。\n\n"
            "### `docs/guide.md`\n\n"
            "「仕様」節を参照する。\n\n"
            "### 対象ファイル一覧\n\n"
            "「別の存在しない節」節を参照する。\n",
        )
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 0

    def test_newly_created_file_self_reference_is_excluded(self, tmp_path: pathlib.Path) -> None:
        """対象ファイル一覧で新設扱いのH3配下では裸節名参照を検査対象外にする。"""
        new_path = "docs/new-guide.md"
        path = _write(
            tmp_path / "plan.md",
            "## 変更内容\n\n"
            "### 対象ファイル一覧\n\n"
            f"- [ ] `{new_path}`（新設, 見込み20行）\n\n"
            f"### `{new_path}`\n\n"
            "「存在しない節」節を参照する。\n",
        )
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 0

    def test_annotated_newly_created_file_self_reference_is_excluded(self, tmp_path: pathlib.Path) -> None:
        """新設注記付きH3でも新設ファイルへの裸節名参照を検査対象外にする。"""
        new_path = "docs/new-guide.md"
        path = _write(
            tmp_path / "plan.md",
            "## 変更内容\n\n"
            "### 対象ファイル一覧\n\n"
            f"- [ ] `{new_path}`（新設, 見込み20行）\n\n"
            f"### `{new_path}`（新設）\n\n"
            "「存在しない節」節を参照する。\n",
        )
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 0

    def test_path_qualified_ref_is_not_double_reported_as_bare_ref(self, tmp_path: pathlib.Path) -> None:
        """パス付き形式の節名参照に含まれる裸パターン部分は二重検出されない。"""
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "guide.md").write_text("## 仕様\n\n本文。\n", encoding="utf-8")
        path = _write(
            tmp_path / "plan.md",
            "## 変更内容\n\n### `docs/guide.md`\n\n`docs/guide.md`「存在しない節」節を参照する。\n",
        )
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 1
        assert len(result.stderr.splitlines()) == 1
        assert "docs/guide.md 「存在しない節」" in result.stderr
