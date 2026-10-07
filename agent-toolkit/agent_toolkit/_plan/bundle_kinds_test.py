"""計画バンドルの種別定義（`_plan/bundle_kinds.py`）を検証する。"""

import pathlib
import typing

from agent_toolkit._plan import bundle_kinds, viewer_files

_STEM = "07-計画-ab12"


class _Expected(typing.NamedTuple):
    """種別ごとの期待値。"""

    name: str
    main_name: str | None
    listed_with_main: bool
    listed_without_main: bool
    viewable: bool
    current: bool


# 種別を追加したら本表へ行を加える。表に無い種別があると`test_plan_bundle_kinds_cover_all_suffixes`が失敗する。
_EXPECTED: dict[bundle_kinds.BundleKind, _Expected] = {
    bundle_kinds.MAIN: _Expected(f"{_STEM}.md", f"{_STEM}.md", True, True, True, True),
    bundle_kinds.DETAIL: _Expected(f"{_STEM}.detail.md", f"{_STEM}.md", False, False, True, False),
    bundle_kinds.BUGS: _Expected(f"{_STEM}.bugs.md", f"{_STEM}.md", False, False, True, True),
    bundle_kinds.PLAN_REVIEW: _Expected(f"{_STEM}.plan-review.tsv", f"{_STEM}.md", False, True, True, False),
    bundle_kinds.EXEC_REVIEW: _Expected(f"{_STEM}.exec-review.tsv", f"{_STEM}.md", False, True, True, True),
    bundle_kinds.WI_COMMITS: _Expected(f"{_STEM}.wi-commits.jsonl", f"{_STEM}.md", False, False, False, True),
    bundle_kinds.OWNER_RECORD: _Expected(f"{_STEM}.owner.json", f"{_STEM}.md", False, False, False, True),
    bundle_kinds.HANDOFF: _Expected(f"{_STEM}.handoff.md", None, True, True, True, True),
    bundle_kinds.LEGACY_REVIEW: _Expected(f"{_STEM}.review.md", f"{_STEM}.md", False, False, True, False),
    bundle_kinds.WORKAROUND_CHECK: _Expected(f"{_STEM}-workaround-check.md", f"{_STEM}.md", False, False, True, False),
    bundle_kinds.CODEX_LOG: _Expected(f"{_STEM}.codex.log", f"{_STEM}.md", False, False, False, False),
}


def test_plan_bundle_kinds_cover_all_suffixes(tmp_path: pathlib.Path) -> None:
    """全種別について、名前の判定と計画ファイル画面の一覧の判定が期待どおりの値を返す。"""
    assert set(_EXPECTED) == set(bundle_kinds.ALL_KINDS)
    for kind, expected in _EXPECTED.items():
        assert kind.name_for(_STEM) == expected.name
        assert bundle_kinds.kind_of_name(expected.name) is kind
        assert bundle_kinds.is_main_name(expected.name) is (kind is bundle_kinds.MAIN)
        assert bundle_kinds.main_name_of(expected.name) == expected.main_name
        assert bundle_kinds.is_listed_name(expected.name, main_exists=True) is expected.listed_with_main
        assert bundle_kinds.is_listed_name(expected.name, main_exists=False) is expected.listed_without_main
        assert bundle_kinds.is_viewable_name(expected.name) is expected.viewable
        assert bundle_kinds.is_viewable_name(expected.name, current_only=True) is (expected.viewable and expected.current)
        assert kind.current is expected.current

    # ローカルとSSH先の一覧は同じ`viewer_files`の判定を通る。メイン計画の有無に応じてレビュー指摘管理表を載せる。
    with_main = tmp_path / "with-main"
    without_main = tmp_path / "without-main"
    for root in (with_main, without_main):
        root.mkdir()
        for kind, expected in _EXPECTED.items():
            if kind is bundle_kinds.MAIN and root == without_main:
                continue
            (root / expected.name).write_text("本文\n", encoding="utf-8")
    for root, main_exists in ((with_main, True), (without_main, False)):
        listed = {path.name for path in root.iterdir() if viewer_files.is_listed_path(path, root, viewer_files.NEW_SOURCE_ID)}
        assert listed == {
            expected.name
            for kind, expected in _EXPECTED.items()
            if (expected.listed_with_main if main_exists else expected.listed_without_main)
            and expected.viewable
            and (root / expected.name).exists()
        }


def test_derived_kind_sets_keep_their_order() -> None:
    """計画バンドルとして保存する付属ファイルと、画面がリンクする付属ファイルの集合と並び。"""
    assert bundle_kinds.STORED_ATTACHMENTS == (bundle_kinds.BUGS, bundle_kinds.EXEC_REVIEW, bundle_kinds.WI_COMMITS)
    assert bundle_kinds.LINKED_ATTACHMENTS == (
        bundle_kinds.DETAIL,
        bundle_kinds.BUGS,
        bundle_kinds.PLAN_REVIEW,
        bundle_kinds.EXEC_REVIEW,
    )
    assert bundle_kinds.REVIEW_TABLES == (bundle_kinds.PLAN_REVIEW, bundle_kinds.EXEC_REVIEW)


def test_unknown_names_have_no_kind() -> None:
    """いずれの種別の接尾辞も持たない名前は種別を持たず、一覧と表示の対象にもならない。"""
    for name in ("note.txt", "p.tsv", "p.json"):
        assert bundle_kinds.kind_of_name(name) is None
        assert bundle_kinds.main_name_of(name) is None
        assert not bundle_kinds.is_listed_name(name, main_exists=False)
        assert not bundle_kinds.is_viewable_name(name)
