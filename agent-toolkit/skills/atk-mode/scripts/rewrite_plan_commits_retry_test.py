"""書換えの完了後に変化した対応履歴で、同じ操作を再試行する。"""

import json
import os
import pathlib

import pytest
import rewrite_plan_commits
from agent_toolkit._plan import commit_mapping
from agent_toolkit._testing import git_repository
from rewrite_plan_commits_test import WI_A, WI_B, WI_S, _commit, _Rebased, _snapshot
from rewrite_plan_commits_test import _rebased_fixture as _rebased_fixture  # pylint: disable=useless-import-alias


@pytest.mark.parametrize("mapped", [True, False])
def test_retry_with_new_in_range_and_out_of_range_commits(
    rebased: _Rebased, capsys: pytest.CaptureFixture[str], mapped: bool
) -> None:
    table = rebased.range_map("a", "b", "u") if mapped else rebased.range_map("a", "b")
    args = rebased.argv(table)
    assert rewrite_plan_commits.main(args) == 0
    record = rebased.handoff
    commit_mapping.append_event(record, {"commit": rebased.orphan, "awi": [WI_S]})
    commit_mapping.append_event(record, {"commit": rebased.old["u"], "awi": [WI_A]})
    before = _snapshot([rebased.plan, record, rebased.unaffected])
    if not mapped:
        assert rewrite_plan_commits.main(args) == 1
        error = capsys.readouterr().err
        assert "対応表の不足" in error and rebased.old["u"] in error
        assert _snapshot([rebased.plan, record, rebased.unaffected]) == before
        return

    notice = f"範囲外として残したOID（1件）: {rebased.orphan}"
    assert rewrite_plan_commits.main(args) == 0
    assert f"追記: {record}（1件） {notice}" in capsys.readouterr().out
    events = commit_mapping.read_events(record, record.read_text(encoding="utf-8"))
    assert commit_mapping.read_mapping(rebased.repo, events, {WI_A, WI_B, WI_S}) == {
        rebased.new["b"]: {WI_B},
        rebased.new["u"]: {WI_A},
        rebased.orphan: {WI_S},
    }
    done = _snapshot([rebased.plan, record, rebased.unaffected])
    assert rewrite_plan_commits.main(args) == 0
    assert f"変更なし: {record} {notice}" in capsys.readouterr().out
    assert _snapshot([rebased.plan, record, rebased.unaffected]) == done


@pytest.mark.parametrize("edit", ["missing", "past", "reintroduced"])
def test_retry_rejects_new_omissions(rebased: _Rebased, capsys: pytest.CaptureFixture[str], edit: str) -> None:
    record = rebased.plan
    commit_mapping.append_event(record, {"commit": rebased.orphan, "awi": [WI_A]})
    args = rebased.argv(rebased.range_map("a", "b"))
    assert rewrite_plan_commits.main([arg for arg in args if not arg.startswith("--handoff=")]) == 0
    if edit == "missing":
        oid = rebased.old["u"]
        reason = "対応表の不足"
    elif edit == "reintroduced":
        oid = rebased.orphan
        commit_mapping.append_event(record, {"rewrite": {oid: rebased.new["u"]}})
        reason = "過去の書換えの未追記"
    else:
        oid = _commit(rebased.repo, "later lane")
        git_repository.git_output(rebased.repo, "switch", "--detach", rebased.new["b"])
        reason = "過去の書換えの未追記"
    commit_mapping.append_event(record, {"commit": oid, "awi": [WI_A]})
    before = _snapshot([record, rebased.handoff, rebased.unaffected])
    assert rewrite_plan_commits.main(args) == 1
    error = capsys.readouterr().err
    assert reason in error and oid in error
    if oid != rebased.orphan:
        assert rebased.orphan not in error
    assert _snapshot([record, rebased.handoff, rebased.unaffected]) == before


@pytest.mark.parametrize("replace", [False, True])
def test_retry_requires_the_original_event(rebased: _Rebased, capsys: pytest.CaptureFixture[str], replace: bool) -> None:
    args = rebased.argv(rebased.range_map("a", "b"))
    assert rewrite_plan_commits.main([arg for arg in args if not arg.startswith("--handoff=")]) == 0
    mapping = commit_mapping.mapping_path(rebased.plan)
    mapping.write_bytes(b"".join(mapping.read_bytes().splitlines(keepends=True)[:-1]))
    if replace:
        commit_mapping.append_event(rebased.plan, {"rewrite": {rebased.old["a"]: rebased.new["u"]}})
    before = _snapshot([rebased.plan, rebased.handoff])
    assert rewrite_plan_commits.main(args) == 1
    assert f"適用時の対応履歴が書換えの証拠と一致しません: {rebased.plan}" in capsys.readouterr().err
    assert _snapshot([rebased.plan, rebased.handoff]) == before


def test_retry_after_mapping_publication_processes_new_target(rebased: _Rebased, monkeypatch: pytest.MonkeyPatch) -> None:
    args = rebased.argv(rebased.range_map("a", "b", "u"))
    record = rebased.plan
    replace = os.replace

    def fail_receipt(source: str, destination: pathlib.Path) -> None:
        if destination == record.with_suffix(".wi-rewrite.receipt"):
            raise PermissionError(str(destination))
        replace(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_receipt)
        assert rewrite_plan_commits.main(args) == 1
    commit_mapping.append_event(record, {"commit": rebased.old["u"], "awi": [WI_A]})
    progress = commit_mapping.mapping_path(record).read_bytes()
    assert rewrite_plan_commits.main(args) == 0
    assert commit_mapping.mapping_path(record).read_bytes().startswith(progress)
    events = commit_mapping.read_events(record, record.read_text(encoding="utf-8"))
    assert commit_mapping.read_mapping(rebased.repo, events, {WI_A}) == {rebased.new["a"]: {WI_A}, rebased.new["u"]: {WI_A}}
    after = _snapshot([record, rebased.handoff])
    assert rewrite_plan_commits.main(args) == 0
    assert _snapshot([record, rebased.handoff]) == after


def test_retry_preserves_a_later_rewrite(rebased: _Rebased) -> None:
    record = rebased.plan
    retained = _commit(rebased.repo, "cherry-pick source")
    git_repository.git_output(rebased.repo, "switch", "--detach", rebased.new["b"])
    commit_mapping.append_event(record, {"commits": [rebased.orphan, retained], "awi": [WI_A]})
    args = rebased.argv(rebased.range_map("a", "b", "u"))
    first = [arg for arg in args if not arg.startswith("--handoff=")]
    assert rewrite_plan_commits.main(first) == 0
    # 固定名の証拠だけを持つ旧版の記録。
    operation = commit_mapping.load_range_rewrite(rebased.repo, rebased.range_map("a", "b", "u"), rebased.previous_head)
    (record.with_suffix(".wi-rewrite.history") / operation.operation_id).unlink()
    other = rebased.repo.parent / "other.json"
    other.write_text(json.dumps({rebased.orphan: rebased.new["a"]}), encoding="utf-8")
    args2 = [arg for arg in first if not arg.startswith(("--previous-head=", "--rewrite-map="))]
    # 別レーンにだけあるOIDを残し、どちらの書換えの再試行にもその時点の証拠が要るようにする。
    untouched = _commit(rebased.repo, "other branch")
    git_repository.git_output(rebased.repo, "switch", "--detach", rebased.new["b"])
    commit_mapping.append_event(record, {"commit": untouched, "awi": [WI_A]})
    assert rewrite_plan_commits.main([*args2, f"--previous-head={rebased.orphan}", f"--rewrite-map={other}"]) == 0
    before = _snapshot([record, rebased.handoff])
    assert rewrite_plan_commits.main(args) == 1
    assert _snapshot([record, rebased.handoff]) == before
    # 旧版形式の追記で新しい未追記を解決すれば、元の操作も再開できる。
    commit_mapping.append_event(record, {"rewrite": {untouched: rebased.new["u"]}})
    done = _snapshot([record])
    assert rewrite_plan_commits.main(args) == 0
    assert _snapshot([record]) == done
