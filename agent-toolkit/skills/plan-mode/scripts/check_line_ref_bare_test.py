"""agent-toolkit/skills/plan-mode/scripts/check_line_ref.py のテスト（裸節名参照の実在照合、追加シナリオ）。

`check_line_ref_test.py`からの責務分割先とし、裸節名参照の実在照合（FB5対応）の
主要シナリオをまとめて扱う。ヘルパー関数は`check_line_ref_test`から再利用する。
"""

import pathlib

from check_line_ref_test import _run, _write


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

    def test_bare_ref_qualified_by_other_file_mention_is_excluded(self, tmp_path: pathlib.Path) -> None:
        """H3対象ファイルとは異なる`.md`ファイルへの明示的言及を伴う裸節名参照は検査対象外。

        計画ファイルのサンプル・記述例内で、H3対象ファイル自身ではなく別ファイルの節を
        「`path`の「節名」節」（助詞「の」が介在し`_SECTION_REF_PATTERN`にマッチしない形）で
        参照するケースを扱う。対象は`sample.md`のような、他の計画ファイルの完成形文面を
        サンプルとして含むファイルへの改訂を計画するケース。
        """
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "guide.md").write_text("## 仕様\n\n本文。\n", encoding="utf-8")
        path = _write(
            tmp_path / "plan.md",
            "## 変更内容\n\n### `docs/guide.md`\n\n"
            "```text\n[置換後]\n`docs/other.md`の「存在しない節」節へ全称禁止形バレットを追加する。\n```\n",
        )
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 0

    def test_bare_ref_qualified_by_same_target_path_mention_still_checked(self, tmp_path: pathlib.Path) -> None:
        """H3対象ファイル自身へのパス言及を伴う裸節名参照は、実在確認をスキップせず引き続き検査する。"""
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "guide.md").write_text("## 仕様\n\n本文。\n", encoding="utf-8")
        path = _write(
            tmp_path / "plan.md",
            "## 変更内容\n\n### `docs/guide.md`\n\n"
            "```text\n[置換後]\n`docs/guide.md`の対応として「存在しない節」節へ全称禁止形バレットを追加する。\n```\n",
        )
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 1
        assert "節名不在" in result.stderr

    def test_bare_ref_with_two_path_mentions_uses_nearest_one(self, tmp_path: pathlib.Path) -> None:
        """同一行に複数`.md`言及がある場合、マッチ直前に最も近い言及で判定する。

        H3対象ファイルへの言及が先、別ファイルへの言及が後（マッチ直前）にある行では、
        直近の別ファイル言及を優先しスキップする。
        """
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "guide.md").write_text("## 仕様\n\n本文。\n", encoding="utf-8")
        path = _write(
            tmp_path / "plan.md",
            "## 変更内容\n\n### `docs/guide.md`\n\n"
            "```text\n[置換後]\n`docs/guide.md`の対応として`docs/other.md`の「存在しない節」節へバレットを追加する。\n```\n",
        )
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 0

    def test_bare_ref_qualified_by_other_file_mention_outside_fence_still_checked(self, tmp_path: pathlib.Path) -> None:
        """ラベル付きフェンス外の地の文では別ファイル言及によるスキップを適用せず引き続き検査する。

        `## 変更内容`H3配下でも通常の説明文（`[置換後]`・`[追記]`ラベル付き`text`フェンス外）は
        偶発的な別ファイル言及で実在確認がすり抜けないよう、常にH3対象ファイル自身の節として検査する。
        """
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "guide.md").write_text("## 仕様\n\n本文。\n", encoding="utf-8")
        path = _write(
            tmp_path / "plan.md",
            "## 変更内容\n\n### `docs/guide.md`\n\n`docs/other.md`の内容を確認した。次に「存在しない節」節を追加する。\n",
        )
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 1
        assert "節名不在" in result.stderr

    def test_bare_ref_with_angle_bracket_placeholder_is_excluded(self, tmp_path: pathlib.Path) -> None:
        """山括弧完結型のプレースホルダー参照は擬陽性として検査対象から除外される。"""
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "guide.md").write_text("## 仕様\n\n本文。\n", encoding="utf-8")
        path = _write(
            tmp_path / "plan.md",
            "## 変更内容\n\n### `docs/guide.md`\n\n"
            "```text\n[追記]\n計画本文で「<節名>」節形式のプレースホルダーを使う。\n```\n",
        )
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 0

    def test_bare_ref_with_angle_bracket_mixed_is_excluded(self, tmp_path: pathlib.Path) -> None:
        """山括弧混在型のプレースホルダー参照も擬陽性として検査対象から除外される。"""
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "guide.md").write_text("## 仕様\n\n本文。\n", encoding="utf-8")
        path = _write(
            tmp_path / "plan.md",
            "## 変更内容\n\n### `docs/guide.md`\n\n```text\n[追記]\n「対象ファイル<ファイル名>」節形式で記述する。\n```\n",
        )
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 0

    def test_bare_ref_with_angle_bracket_placeholder_and_unresolvable_target_is_excluded(self, tmp_path: pathlib.Path) -> None:
        """対象H3パスが解決不能（見出し集合が読み込み不能または非該当）でも、山括弧含み裸参照は擬陽性除外される。

        `headings is None`ケースと、`headings`が非`None`かつ対象節を含まないケースは
        実装上同一分岐（`_is_angle_bracket_placeholder`経由の除外）を辿るため、
        本テストはH3見出しをバッククォート無し（裸パス、`_check_path_existence`の
        実在確認対象から外れる）で記述し、対象H3パスが解決不能な状況での擬陽性除外挙動を固定する。
        """
        path = _write(
            tmp_path / "plan.md",
            "## 変更内容\n\n### docs/missing.md\n\n"
            "```text\n[追記]\n計画本文で「<節名>」節形式のプレースホルダーを使う。\n```\n",
        )
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 0

    def test_bare_ref_mistyped_without_brackets_to_bracketed_heading_is_detected(self, tmp_path: pathlib.Path) -> None:
        """山括弧記号を含む実在見出しへの誤参照（山括弧を欠いた転記）は引き続き節名不在違反として検出される。"""
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "guide.md").write_text("### <ファイル名>: <提案要約>\n\n本文。\n", encoding="utf-8")
        path = _write(
            tmp_path / "plan.md",
            "## 変更内容\n\n### `docs/guide.md`\n\n「ファイル名: 提案要約」節を参照する。\n",
        )
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 1
        assert "節名不在" in result.stderr

    def test_bare_ref_exact_match_to_bracketed_heading_is_not_flagged(self, tmp_path: pathlib.Path) -> None:
        """山括弧記号を含む実在見出しへの完全一致参照は違反として検出されない。"""
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "guide.md").write_text("### <ファイル名>: <提案要約>\n\n本文。\n", encoding="utf-8")
        path = _write(
            tmp_path / "plan.md",
            "## 変更内容\n\n### `docs/guide.md`\n\n「<ファイル名>: <提案要約>」節を参照する。\n",
        )
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 0

    def test_bare_ref_violation_suggests_other_file_with_same_heading(self, tmp_path: pathlib.Path) -> None:
        """対象H3ファイル内に節が無くても、他Markdownファイルに同名見出しがあれば補完候補を付記する。

        違反判定自体は解除されない（`_find_bare_section_ref_violations`の設計要件）。
        """
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "guide.md").write_text("### 別の節\n\n本文。\n", encoding="utf-8")
        (tmp_path / "docs" / "other.md").write_text("### 対象節\n\n本文。\n", encoding="utf-8")
        path = _write(
            tmp_path / "plan.md",
            "## 変更内容\n\n### `docs/guide.md`\n\n「対象節」節を参照する。\n",
        )
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 1
        assert "節名不在" in result.stderr
        assert "同名見出しがある他候補" in result.stderr
        assert "docs/other.md" in result.stderr

    def test_bare_ref_violation_without_other_file_omits_suggestion(self, tmp_path: pathlib.Path) -> None:
        """他Markdownファイルにも同名見出しが無い場合は補完候補を付記しない。"""
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "guide.md").write_text("### 別の節\n\n本文。\n", encoding="utf-8")
        path = _write(
            tmp_path / "plan.md",
            "## 変更内容\n\n### `docs/guide.md`\n\n「存在しない節」節を参照する。\n",
        )
        result = _run(str(path), cwd=tmp_path)
        assert result.returncode == 1
        assert "節名不在" in result.stderr
        assert "同名見出しがある他候補" not in result.stderr
