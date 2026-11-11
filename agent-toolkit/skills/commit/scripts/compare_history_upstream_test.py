# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""履歴比較で上流に存在する変更の保持を検証する。"""

import json
import pathlib

import pytest

from agent_toolkit import atk
from agent_toolkit._plan import commit_mapping
from agent_toolkit._testing.history_compare_support import _commit, _commit_files, _compare, _git, _rewrite_records
from agent_toolkit._testing.history_compare_support import _repo as _repo


@pytest.mark.parametrize("upstream_text", ["依存変更\n", "別変更\n", "依存変更 \n"])
def test_rebase_keeps_mapping_for_dependency_already_upstream(
    repo: pathlib.Path, tmp_path: pathlib.Path, upstream_text: str
) -> None:
    """省略された依存変更は、統合先の同一変更だけへ対応付ける。"""
    base = _git(repo, "rev-parse", "HEAD")
    dependency = _commit(repo, "dependency.txt", "依存変更\n", "依存更新")
    old_head = _commit(repo, "implementation.txt", "実装\n", "実装")
    _git(repo, "checkout", "-b", "upstream", base)
    new_base = _commit(repo, "dependency.txt", upstream_text, "上流の依存更新")
    new_head = _commit(repo, "implementation.txt", "実装\n", "実装")
    output = tmp_path / "dependency-map.json"

    result = _compare(repo, output, "rebase", (base, old_head, new_base, new_head))

    if upstream_text == "依存変更\n":
        assert result == 0
        assert json.loads(output.read_text()) == {dependency: new_base, old_head: new_head}
    else:
        assert result == 1
        assert not output.exists()


@pytest.mark.parametrize("upstream_text", ["依存変更\n", "依存変更 \n"])
def test_rebase_all_commits_already_upstream(repo: pathlib.Path, tmp_path: pathlib.Path, upstream_text: str) -> None:
    """全件省略でも保持された同一変更だけを既存commitへ対応付ける。"""
    base = _git(repo, "rev-parse", "HEAD")
    old_head = _commit(repo, "dependency.txt", "依存変更\n", "依存更新")
    _git(repo, "checkout", "-b", "upstream", base)
    new_base = _commit(repo, "dependency.txt", upstream_text, "上流の依存更新")
    output = tmp_path / "all-upstream-map.json"

    result = _compare(repo, output, "rebase", (base, old_head, new_base, new_base))

    if upstream_text == "依存変更\n":
        assert result == 0
        assert json.loads(output.read_text(encoding="utf-8")) == {old_head: new_base}
    else:
        assert result == 1
        assert not output.exists()


@pytest.mark.parametrize("loss", ["revert", "merge-discard"])
def test_rebase_rejects_upstream_dependency_not_retained_at_base(repo: pathlib.Path, tmp_path: pathlib.Path, loss: str) -> None:
    """履歴内の同一変更が新baseで失われている場合は、省略を受理しない。"""
    base = _git(repo, "rev-parse", "HEAD")
    _commit(repo, "dependency.txt", "依存変更\n", "依存更新")
    old_head = _commit(repo, "implementation.txt", "実装\n", "実装")
    _git(repo, "checkout", "-b", "upstream", base)
    dependency = _commit(repo, "dependency.txt", "依存変更\n", "上流の依存更新")
    if loss == "revert":
        _git(repo, "revert", "--no-edit", dependency)
    else:
        _git(repo, "checkout", "-b", "integration", base)
        _git(repo, "merge", "--no-ff", "-s", "ours", "upstream", "-m", "依存変更を不採用とする統合")
    new_base = _git(repo, "rev-parse", "HEAD")
    new_head = _commit(repo, "implementation.txt", "実装\n", "実装")
    output = tmp_path / "lost-dependency-map.json"

    assert _compare(repo, output, "rebase", (base, old_head, new_base, new_head)) == 1
    assert not output.exists()


def test_rebase_rejects_ambiguous_upstream_mapping(repo: pathlib.Path, tmp_path: pathlib.Path) -> None:
    """同一変更の追加・取消・再追加は新baseで保持されても対応を推測しない。"""
    base = _git(repo, "rev-parse", "HEAD")
    old_head = _commit(repo, "dependency.txt", "依存変更\n", "依存更新")
    _git(repo, "checkout", "-b", "upstream", base)
    first = _commit(repo, "dependency.txt", "依存変更\n", "最初の追加")
    _git(repo, "revert", "--no-edit", first)
    new_base = _commit(repo, "dependency.txt", "依存変更\n", "再追加")
    output = tmp_path / "ambiguous-map.json"
    assert _compare(repo, output, "rebase", (base, old_head, new_base, new_base)) == 1
    assert not output.exists()


_SHARED_LINES = [f"行{index}" for index in range(1, 13)]


def _omitted_upstream_series(
    repo: pathlib.Path, later_edit: str, all_skipped: bool
) -> tuple[tuple[str, str, str, str], str, str]:
    """統合先の同一変更の後に`later_edit`の後続編集がある系列を作り、端点・省略される旧commit・統合先の同一変更を返す。"""
    before = _SHARED_LINES
    changed = ["行1を変更", *before[1:]]
    base = _commit(repo, "shared.txt", "".join(f"{line}\n" for line in before), "準備")
    omitted = _commit(repo, "shared.txt", "".join(f"{line}\n" for line in changed), "対象変更")
    old_head = omitted if all_skipped else _commit(repo, "feature.txt", "成果\n", "実装")
    _git(repo, "checkout", "-b", "upstream", base)
    upstream = _commit(repo, "shared.txt", "".join(f"{line}\n" for line in changed), "上流対象変更")
    edited = list(changed)
    if later_edit == "other-path":
        new_base = _commit(repo, "unrelated.txt", "独立編集\n", "独立した後続編集")
    elif later_edit == "revert-and-context":
        # 取消しの後に文脈を書き換えると、順適用も逆適用もできず書換えと区別できない。
        _git(repo, "revert", "--no-edit", upstream)
        reverted = list(before)
        reverted[1] = "後続編集"
        new_base = _commit(repo, "shared.txt", "".join(f"{line}\n" for line in reverted), "取消し後の文脈編集")
    else:
        index = {"same-path-far": 11, "same-path-context": 1, "same-path-overlap": 0}[later_edit]
        edited[index] = "後続編集"
        new_base = _commit(repo, "shared.txt", "".join(f"{line}\n" for line in edited), "後続編集")
    new_head = new_base if all_skipped else _commit(repo, "feature.txt", "成果\n", "実装")
    return (base, old_head, new_base, new_head), omitted, upstream


@pytest.mark.parametrize(
    ("later_edit", "accepted"),
    [
        # 別pathの後続編集は省略された変更に触れない。
        ("other-path", True),
        # 同じpathでも、変更箇所と前後3行の文脈から離れた行の編集は変更を残す。
        ("same-path-far", True),
        # 変更箇所の文脈（直後の行）の書換えは、取消しと差分の適用で区別できない。
        ("same-path-context", False),
        # 変更箇所そのものの書換えも、取消しと区別できない。
        ("same-path-overlap", False),
        # 取消し後の文脈編集は、書換えと同じ適用結果になる。
        ("revert-and-context", False),
    ],
)
@pytest.mark.parametrize("all_skipped", [False, True])
def test_upstream_retention_judges_omitted_change_itself(
    repo: pathlib.Path, tmp_path: pathlib.Path, later_edit: str, all_skipped: bool, accepted: bool
) -> None:
    """省略された変更が新baseに残るかを変更そのもので判定し、同じpathの離れた後続編集では統合を止めない。"""
    bases, omitted, upstream = _omitted_upstream_series(repo, later_edit, all_skipped)
    output = tmp_path / "retention-map.json"
    code = _compare(repo, output, "rebase", bases)
    if accepted:
        assert code == 0
        expected = {omitted: upstream}
        if not all_skipped:
            expected[bases[1]] = bases[3]
        assert json.loads(output.read_text(encoding="utf-8")) == expected
    else:
        assert code == 1
        assert not output.exists()


@pytest.mark.parametrize("later_edit", ["same-path-context", "same-path-overlap"])
@pytest.mark.parametrize("all_skipped", [False, True])
def test_upstream_rewrite_is_accepted_only_when_resolved_conflict_names_it(
    repo: pathlib.Path, tmp_path: pathlib.Path, later_edit: str, all_skipped: bool
) -> None:
    """統合先で書き換えられた同一変更は、`--resolved-conflict`で明示した旧commitだけを既存commitへ対応付ける。"""
    bases, omitted, upstream = _omitted_upstream_series(repo, later_edit, all_skipped)
    output = tmp_path / "rewrite-map.json"
    assert _compare(repo, output, "rebase", bases, "--resolved-conflict", omitted) == 0
    expected = {omitted: upstream}
    if not all_skipped:
        expected[bases[1]] = bases[3]
    assert json.loads(output.read_text(encoding="utf-8")) == expected


def test_upstream_rewrite_acknowledgement_rejects_exclude_path(
    repo: pathlib.Path, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """書換えの明示と除外指定を同じ旧commitへ併用すると、除外の検収を迂回するため拒否する。"""
    shared = "".join(f"{line}\n" for line in _SHARED_LINES)
    changed = "".join(f"{line}\n" for line in ["行1を変更", *_SHARED_LINES[1:]])
    base = _commit_files(repo, {"shared.txt": shared, "extra.txt": "元\n"}, "準備")
    omitted = _commit_files(repo, {"shared.txt": changed, "extra.txt": "変更\n"}, "対象変更")
    _git(repo, "checkout", "-b", "upstream", base)
    _commit_files(repo, {"shared.txt": changed, "extra.txt": "変更\n"}, "上流対象変更")
    rewritten = "".join(f"{line}\n" for line in ["後続編集", *_SHARED_LINES[1:]])
    new_base = _commit(repo, "shared.txt", rewritten, "後続編集")
    output = tmp_path / "rewrite-exclude-map.json"
    capsys.readouterr()
    code = _compare(
        repo,
        output,
        "rebase",
        (base, omitted, new_base, new_base),
        "--resolved-conflict",
        omitted,
        "--exclude-path",
        omitted,
        "extra.txt",
    )
    assert code == 1
    assert "併用できません" in capsys.readouterr().err
    assert not output.exists()


def test_upstream_rewrite_acknowledgement_does_not_accept_plain_revert(repo: pathlib.Path, tmp_path: pathlib.Path) -> None:
    """明示しても、新baseへ順適用できる（変更が丸ごと失われた）取消しは受理しない。"""
    base = _commit(repo, "shared.txt", "".join(f"{line}\n" for line in _SHARED_LINES), "準備")
    changed = "".join(f"{line}\n" for line in ["行1を変更", *_SHARED_LINES[1:]])
    omitted = _commit(repo, "shared.txt", changed, "対象変更")
    old_head = _commit(repo, "feature.txt", "成果\n", "実装")
    _git(repo, "checkout", "-b", "upstream", base)
    upstream = _commit(repo, "shared.txt", changed, "上流対象変更")
    _git(repo, "revert", "--no-edit", upstream)
    new_base = _git(repo, "rev-parse", "HEAD")
    new_head = _commit(repo, "feature.txt", "成果\n", "実装")
    output = tmp_path / "revert-map.json"
    assert _compare(repo, output, "rebase", (base, old_head, new_base, new_head), "--resolved-conflict", omitted) == 1
    assert not output.exists()


def test_upstream_retention_ignores_diff_prefix_configuration(repo: pathlib.Path, tmp_path: pathlib.Path) -> None:
    """`diff.noprefix`を設定したリポジトリでも、接頭辞を明示した差分で保持を判定する。"""
    _git(repo, "config", "diff.noprefix", "true")
    base = _commit(repo, "shared.txt", "".join(f"{line}\n" for line in _SHARED_LINES), "準備")
    changed = "".join(f"{line}\n" for line in ["行1を変更", *_SHARED_LINES[1:]])
    omitted = _commit(repo, "shared.txt", changed, "対象変更")
    _git(repo, "checkout", "-b", "upstream", base)
    upstream = _commit(repo, "shared.txt", changed, "上流対象変更")
    output = tmp_path / "noprefix-map.json"
    assert _compare(repo, output, "rebase", (base, omitted, upstream, upstream)) == 0
    assert json.loads(output.read_text(encoding="utf-8")) == {omitted: upstream}


@pytest.mark.parametrize("all_skipped", [False, True])
def test_skipped_changes_merge_wis_in_plan_and_handoff(
    repo: pathlib.Path,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    all_skipped: bool,
) -> None:
    """省略変更のWIを上流の既存WIへ合併し、WIなし依存変更を含む比較表を消費する。"""
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(tmp_path / "private-notes"))
    wis = ["20261009-043608-001.md", "20261009-043608-002.md"]
    plan = tmp_path / "09-skipped-1a2b.md"
    plan.write_text(
        "# 計画\n\n## 概要\n\n### 計画メタ情報\n\n- 関連WI:\n"
        + "".join(f"  - {wi}: 対応\n" for wi in wis)
        + "\n## 進捗ログ\n\n| 日時 | 完了した工程 | 結果・特記事項 |\n| --- | --- | --- |\n",
        encoding="utf-8",
    )
    handoff = tmp_path / "handoff.md"
    handoff.write_text("# 引き継ぎ\n", encoding="utf-8")
    base = _git(repo, "rev-parse", "HEAD")
    dependency = _commit(repo, "dependency.txt", "依存\n", "依存変更")
    skipped = _commit(repo, "feature.txt", "成果\n", "実装")
    old_head = skipped if all_skipped else _commit(repo, "extra.txt", "追加\n", "追加実装")
    _git(repo, "checkout", "-b", "upstream", base)
    upstream_dependency = _commit(repo, "dependency.txt", "依存\n", "上流依存")
    new_base = _commit(repo, "feature.txt", "成果\n", "上流実装")
    new_head = new_base if all_skipped else _commit(repo, "extra.txt", "追加\n", "追加実装")
    for record in (plan, handoff):
        commit_mapping.append_event(record, {"commit": skipped, "awi": [wis[0]]})
        commit_mapping.append_event(record, {"commit": new_base, "awi": [wis[1]]})
    mapping = tmp_path / "skipped-map.json"
    assert _compare(repo, mapping, "rebase", (base, old_head, new_base, new_head)) == 0
    pairs = json.loads(mapping.read_text(encoding="utf-8"))
    assert pairs[dependency] == upstream_dependency
    assert pairs[skipped] == new_base
    # 部分省略では新baseがHEADの親である。消費側がHEADの親以前への対応を拒否する変異は、
    # 比較成功を維持したまま次の公開plan-rewriteの成功検査で検出する。
    if not all_skipped:
        assert _git(repo, "rev-parse", "HEAD^") == new_base
    assert _rewrite_records(repo, old_head, mapping, plan, handoff) == 0
    for record in (plan, handoff):
        events = commit_mapping.read_events(record, record.read_text(encoding="utf-8"))
        inherited = commit_mapping.read_mapping(repo, events, set(wis))
        assert inherited == {new_base: set(wis)}
        capsys.readouterr()
        argv = ["run-script", "plan-commits", "--", str(record), "--worktree", str(repo)]
        if record == handoff:
            argv.append("--handoff")
            for wi in wis:
                argv.extend(("--allowed-awi", wi))
        with pytest.raises(SystemExit) as queried:
            atk.main(argv)
        assert queried.value.code == 0
        rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        assert {row["awi"]: row["commits"] for row in rows} == {
            wi: [_git(repo, "rev-parse", "--short", new_base)] for wi in wis
        }
