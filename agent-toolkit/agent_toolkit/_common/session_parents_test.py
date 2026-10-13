"""表示と活動取得が共有する親子解決の契約を検証する。"""

from agent_toolkit._common import session_parents


def test_loop_descendants() -> None:
    """別loopと同じ実行系・cwdの子を混同せず、確定した親子だけをたどる。"""
    sources = [
        session_parents.ParentSource("loop", "claude", "loop", None, None),
        session_parents.ParentSource("child", "codex", "child", None, None),
        session_parents.ParentSource("grandchild", "codex", "grandchild", None, "child"),
        session_parents.ParentSource("native", "claude", "native", "loop", None),
        session_parents.ParentSource("other-loop", "claude", "other-loop", None, None),
        session_parents.ParentSource("other-child", "codex", "other-child", None, None),
    ]
    parents = session_parents.resolve_parent_paths(
        sources,
        delegated_ids=lambda path, _engine: ("child",) if path == "loop" else (),
        launcher_of=lambda child: "other-loop" if child == "other-child" else None,
    )
    assert session_parents.descendants({"loop"}, parents) == {"loop", "child", "grandchild", "native"}
    assert parents["other-child"] == "other-loop"


def test_parent_precedence_and_cycles() -> None:
    """実記録の親を古いlauncherで上書きせず、循環を含む入力でも有限で終了する。"""
    sources = [
        session_parents.ParentSource("parent", "claude", "parent", None, None),
        session_parents.ParentSource("old", "claude", "old", None, None),
        session_parents.ParentSource("child", "codex", "child", "parent", None),
    ]
    parents = session_parents.resolve_parent_paths(
        sources,
        delegated_ids=lambda _path, _engine: (),
        launcher_of=lambda _child: "old",
    )
    assert parents["child"] == "parent"
    assert session_parents.descendants({"a"}, {"a": "b", "b": "a", "c": "missing"}) == {"a", "b"}


def test_list_limit_preserves_ancestors() -> None:
    """共通層への移動後も、表示上限で子の祖先を失わない。"""
    parents = {"child": "parent", "parent": "grandparent"}
    assert session_parents.limit_with_ancestors(
        ["child", "other", "parent", "grandparent"],
        1,
        path_of=lambda item: item,
        parent_of=parents.get,
    ) == ["child", "parent", "grandparent"]
