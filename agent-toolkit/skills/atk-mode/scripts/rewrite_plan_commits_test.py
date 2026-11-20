"""`atk run-script plan-rewrite`が範囲全体の対応表を全記録へ1回で追記する契約を実Gitで確かめる。"""

import argparse
import dataclasses
import json
import os
import pathlib
import re
from typing import Any

import get_plan_commits
import pytest
import rewrite_plan_commits
from agent_toolkit import atk
from agent_toolkit._atk import managed_temp, run_script
from agent_toolkit._common import next_action
from agent_toolkit._plan import commit_mapping
from agent_toolkit._testing import git_repository

WI_A = "20261007-040310-002.md"
WI_B = "20261007-040310-003.md"
WI_S = "20261007-040310-004.md"


@pytest.mark.cpu_limit(
    2.5,
    reason=(
        "公開操作で未指定の実記録を検出し、成果物内の同名記録は探索しない。 実"
        + "Gitの旧/新履歴とWI別の対応記録を保持し、公開保存と拒否後の全本文"
        + "不変を確認する。Linux同群CPU最大0.909秒。"
    ),
)
@pytest.mark.parametrize("location", ["working", "legacy", "saved", "handoff"])
def test_public_discovery_visits_only_record_locations(
    rebased: _Rebased, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], location: str
) -> None:
    """公開操作で未指定の実記録を検出し、成果物内の同名記録は探索しない。"""
    root = rebased.repo.parent
    config = root / "bounded-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    session = managed_temp.create_managed_temp("session")
    working = config / "plans"
    saved = root / "private-notes/plans"
    places = {
        "working": working,
        "legacy": working / "2026/10",
        "saved": saved / "2026/10",
        "handoff": session,
    }
    directory = places[location]
    directory.mkdir(parents=True, exist_ok=True)
    omitted = _handoff(directory / "omitted.md") if location == "handoff" else _plan(directory / "07-omitted.md", [WI_A])
    commit_mapping.append_event(omitted, {"commit": rebased.old["a"], "awi": [WI_A]})
    # 同じ有効OIDを持つ記録を配置し、単に不正記録として除外する実装との差を観測する。
    ignored = []
    for area in (session / "artifacts", session / "agents-server-plugin-root", working / ".venv", saved / "artifacts"):
        area.mkdir(parents=True, exist_ok=True)
        record = _handoff(area / "hidden.md")
        commit_mapping.append_event(record, {"commit": rebased.old["a"], "awi": [WI_A]})
        ignored.append(record)
    scan = os.scandir
    forbidden = {record.parent for record in ignored}

    # CLI起動時の資源掃引は別契約。記録の探索範囲だけを下の監視で検収する。
    monkeypatch.setattr(managed_temp, "sweep_managed_temp", lambda **_kwargs: managed_temp.SweepResult([], ()))

    def bounded_scan(path: Any = ".", **kwargs: Any) -> Any:
        if isinstance(path, (str, os.PathLike)) and pathlib.Path(path) in forbidden:
            pytest.fail(f"記録所在外への走査: {path}")
        return scan(path, **kwargs)

    monkeypatch.setattr(os, "scandir", bounded_scan)
    before = _snapshot([rebased.plan, rebased.handoff, omitted, *ignored])
    argv = rebased.argv(rebased.range_map("a", "u", "b"))
    with pytest.raises(SystemExit) as ended:
        atk.main(["run-script", "plan-rewrite", "--", *argv])
    assert ended.value.code == 1
    error = capsys.readouterr().err
    assert str(omitted) in error
    assert all(str(record) not in error for record in ignored)
    assert _snapshot([rebased.plan, rebased.handoff, omitted, *ignored]) == before
    # 実記録を明示すれば探索による拒否が解除され、成果物内の候補へ入力追加を要求しない。
    flag = "--handoff" if location == "handoff" else "--plan"
    if location != "saved":
        with pytest.raises(SystemExit) as resumed:
            atk.main(["run-script", "plan-rewrite", "--", *argv, flag, str(omitted)])
        assert resumed.value.code == 0


@pytest.mark.cpu_limit(
    2.5,
    reason=(
        "実Gitで1件のcommitを2件へ分けた対応表を`plan-"
        + "rewrite`へ渡し、AWIの割当が欠ければ全記録のバイト列を保ち、"
        + "欠けなければ`plan-commits`がAWIごとの新commitを"
        + "返す。 実Gitの旧/新履歴とWI別の対応記録を保持し、公開保存と拒否"
        + "後の全本文不変を確認する。Linux同群CPU最大0.909秒。"
    ),
)
@pytest.mark.parametrize("missing", [False, True])
def test_public_split_assigns_each_awi_and_rejects_loss_before_writing(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], missing: bool
) -> None:
    """実Gitで1件のcommitを2件へ分けた対応表を`plan-rewrite`へ渡し、AWIの割当が欠ければ全記録のバイト列を保ち、欠けなければ`plan-commits`がAWIごとの新commitを返す。"""
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(tmp_path / "private-notes"))
    repo = tmp_path / "repo"
    repo.mkdir()
    git_repository.init_repository(repo, initial_branch="main")
    root = _commit(repo, "root")
    plan = _plan(tmp_path / "plans/plan.md", [WI_A, WI_B])
    old = _commit(repo, "combined")
    _record(repo, plan, root, [WI_A, WI_B])
    other = _handoff(tmp_path / "handoff.md")
    _record(repo, other, root, [WI_A, WI_B])
    git_repository.git_output(repo, "reset", "--hard", root)
    first, second = _commit(repo, "first"), _commit(repo, "second")
    rewrite = tmp_path / "split.json"
    rewrite.write_text(
        json.dumps({old: [{"commit": first, "awi": [WI_A]}, {"commit": second, "awi": [] if missing else [WI_B]}]}),
        encoding="utf-8",
    )
    before = _snapshot([plan, other])
    args = [
        "--worktree",
        str(repo),
        "--previous-head",
        old,
        "--rewrite-map",
        str(rewrite),
        "--completed-step",
        "分割",
        "--result",
        "AWIごとに継承",
        "--plan",
        str(plan),
        "--handoff",
        str(other),
    ]
    assert run_script.dispatch(argparse.Namespace(script_name="plan-rewrite", script_args=args)) == int(missing)
    captured = capsys.readouterr()
    if missing:
        assert _snapshot([plan, other]) == before
        assert WI_B in captured.err and old in captured.err and "次の操作" in captured.err
        return
    for wi, oid in ((WI_A, first), (WI_B, second)):
        assert get_plan_commits.main([str(plan), "--worktree", str(repo), "--awi", wi]) == 0
        assert json.loads(capsys.readouterr().out)["commits"] == [commit_mapping.short_oid(repo, oid)]


def _commit(repo: pathlib.Path, message: str) -> str:
    """空のcommitを作成し、その完全OIDを返す。"""
    git_repository.git_output(
        repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-m", message
    )
    return git_repository.git_output(repo, "rev-parse", "HEAD")


def _plan(path: pathlib.Path, wis: list[str]) -> pathlib.Path:
    """関連WIと空の進捗ログを持つ計画を作成する。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    related = "".join(f"  - {wi}: 対応\n" for wi in wis)
    path.write_text(
        "# 計画\n\n## 概要\n\n### 計画メタ情報\n\n- 関連WI:\n"
        + related
        + "\n## 進捗ログ\n\n| 日時 | 完了した工程 | 結果・特記事項 |\n| --- | --- | --- |\n",
        encoding="utf-8",
    )
    return path


def _handoff(path: pathlib.Path) -> pathlib.Path:
    """計画なしの引き継ぎ記録を作成する。"""
    path.write_text("# 引き継ぎ記録\n", encoding="utf-8")
    return path


def _record(repo: pathlib.Path, record: pathlib.Path, previous: str, wis: list[str]) -> None:
    """`plan-progress --commit`と同じ対応を対応記録ファイルへ追記する。"""
    commit_mapping.append_event(record, commit_mapping.commit_event(repo, "HEAD", previous, wis, set(wis)))


def _snapshot(records: list[pathlib.Path]) -> dict[pathlib.Path, bytes | None]:
    """本文、対応履歴、再試行の証拠と準備中のファイルを比較する。"""
    paths = [
        path
        for record in records
        for path in (
            record,
            commit_mapping.mapping_path(record),
            record.with_suffix(".wi-rewrite.receipt"),
            *record.with_suffix(".wi-rewrite.history").glob("*"),
            *record.with_suffix(".wi-rewrite.pending").glob("*"),
        )
    ]
    return {path: path.read_bytes() if path.exists() else None for path in paths}


@dataclasses.dataclass(frozen=True)
class _Rebased:
    """rebaseで3件のcommitのOIDが変わった後の状態。"""

    repo: pathlib.Path
    plan: pathlib.Path
    handoff: pathlib.Path
    unaffected: pathlib.Path
    previous_head: str
    old: dict[str, str]
    new: dict[str, str]
    orphan: str

    def range_map(self, *names: str) -> pathlib.Path:
        """指定したcommitだけを含む範囲全体の対応表を保存し、その絶対パスを返す。"""
        path = self.repo.parent / "rewrite.json"
        path.write_text(json.dumps({self.old[name]: self.new[name] for name in names}), encoding="utf-8")
        return path

    def argv(self, rewrite: pathlib.Path, *, extra: tuple[str, ...] = ()) -> list[str]:
        """計画1件と引き継ぎ記録2件を渡す引数を返す。"""
        return [
            f"--worktree={self.repo}",
            f"--previous-head={self.previous_head}",
            f"--rewrite-map={rewrite}",
            "--completed-step=レーン統合のrebase",
            "--result=範囲全体の対応を追記",
            f"--plan={self.plan}",
            f"--handoff={self.handoff}",
            f"--handoff={self.unaffected}",
            *extra,
        ]


@pytest.fixture(name="rebased")
def _rebased_fixture(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> _Rebased:
    """計画（WI_A）、引き継ぎ記録（WI_B）、書換え対象を持たない引き継ぎ記録（WI_S）と、WI対応の無いcommitを含むrebaseを用意する。"""
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(tmp_path / "private-notes"))
    repo = tmp_path / "repo"
    repo.mkdir()
    git_repository.init_repository(repo, initial_branch="main")
    root = _commit(repo, "root")
    unaffected = _handoff(tmp_path / "handoff-stable.md")
    _commit(repo, "stable")
    _record(repo, unaffected, root, [WI_S])
    stable = git_repository.git_output(repo, "rev-parse", "HEAD")
    orphan = _commit(repo, "orphan")
    git_repository.git_output(repo, "reset", "--hard", stable)
    plan = _plan(tmp_path / "plans" / "07-example-1a2b.md", [WI_A])
    handoff = _handoff(tmp_path / "handoff-lane.md")
    old = {"a": _commit(repo, "a")}
    _record(repo, plan, stable, [WI_A])
    old["u"] = _commit(repo, "unrecorded")
    previous = old["u"]
    old["b"] = _commit(repo, "b")
    _record(repo, handoff, previous, [WI_B])
    previous_head = old["b"]
    git_repository.git_output(repo, "reset", "--hard", stable)
    _commit(repo, "upstream")
    new = {name: _commit(repo, message) for name, message in (("a", "a"), ("u", "unrecorded"), ("b", "b"))}
    return _Rebased(repo, plan, handoff, unaffected, previous_head, old, new, orphan)


def _commits(rebased: _Rebased, record: pathlib.Path, wi: str, *, handoff: bool) -> list[str]:
    """`plan-commits`の公開実装で記録から現在のcommitを取得する。"""
    argv = [str(record), f"--worktree={rebased.repo}", f"--awi={wi}"]
    if handoff:
        argv += ["--handoff", f"--allowed-awi={wi}"]
    return argv


def _read_commits(capsys: pytest.CaptureFixture[str]) -> list[str]:
    output = json.loads(capsys.readouterr().out)
    assert isinstance(output["commits"], list)
    return output["commits"]


def test_rewrite_keeps_cherry_pick_source_and_unmerged_commit(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """同じAWIのcherry-pick元と別AWIの未統合commitを残し、取り込んだcommitだけを付け替える。"""
    repo = git_repository.init_repository(tmp_path / "repo", initial_branch="main")
    base = _commit(repo, "base")
    source = _commit(repo, "feature")
    git_repository.git_output(repo, "reset", "--hard", base)
    git_repository.git_output(repo, "switch", "-c", "lane")
    pending = _commit(repo, "pending")
    git_repository.git_output(repo, "switch", "main")
    git_repository.git_output(repo, "cherry-pick", "--allow-empty", "-x", source)
    old = git_repository.git_output(repo, "rev-parse", "HEAD")
    record = _handoff(tmp_path / "handoff.md")
    commit_mapping.append_event(record, {"commits": [source, old], "awi": [WI_A]})
    commit_mapping.append_event(record, {"commit": pending, "awi": [WI_B]})
    records_before = commit_mapping.mapping_path(record).read_bytes()

    git_repository.git_output(repo, "commit", "--amend", "--allow-empty", "-m", "renamed feature")
    new = git_repository.git_output(repo, "rev-parse", "HEAD")
    rewrite = tmp_path / "rewrite.json"
    rewrite.write_text(json.dumps({old: new}), encoding="utf-8")
    args = [
        f"--worktree={repo}",
        f"--previous-head={old}",
        f"--rewrite-map={rewrite}",
        "--completed-step=amend",
        "--result=件名変更",
        f"--handoff={record}",
    ]
    assert run_script.dispatch(argparse.Namespace(script_name="plan-rewrite", script_args=args)) == 0
    line = next(line for line in capsys.readouterr().out.splitlines() if line.startswith(f"追記: {record}（1件）"))
    assert "範囲外" in line and "2件" in line and source in line and pending in line
    assert commit_mapping.mapping_path(record).read_bytes().startswith(records_before)
    events = commit_mapping.read_events(record, record.read_text(encoding="utf-8"))
    assert commit_mapping.read_mapping(repo, events, {WI_A, WI_B}) == {source: {WI_A}, new: {WI_A}, pending: {WI_B}}


@pytest.mark.parametrize("failure", ["body", "mapping", "receipt"])
def test_retry_after_second_record_write_failure(
    rebased: _Rebased, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], failure: str
) -> None:
    """2件目の本文・対応・証拠の書込失敗後、同じ引数で残りだけを1回更新できる。"""
    commit_mapping.append_event(rebased.plan, {"commit": rebased.orphan, "awi": [WI_A]})
    table = rebased.range_map("a", "u", "b")
    pairs = json.loads(table.read_text(encoding="utf-8"))
    pairs[rebased.old["a"]] = [{"commit": rebased.new["a"], "awi": [WI_A]}, {"commit": rebased.new["u"], "awi": [WI_S]}]
    table.write_text(json.dumps(pairs), encoding="utf-8")
    args = rebased.argv(table)
    before = _snapshot([rebased.handoff, rebased.unaffected])
    blocked = {
        "body": rebased.handoff,
        "mapping": commit_mapping.mapping_path(rebased.handoff),
        "receipt": rebased.handoff.with_suffix(".wi-rewrite.receipt"),
    }[failure]
    replace = os.replace

    def fail_replace(source: str, destination: pathlib.Path) -> None:
        if destination == blocked:
            raise PermissionError(str(blocked))
        replace(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_replace)
        assert rewrite_plan_commits.main(args) == 1
    assert str(blocked) in capsys.readouterr().err
    with rebased.plan.open("a", encoding="utf-8") as stream:
        stream.write("\n書込みの失敗を調査した。\n")
    commit_mapping.append_event(rebased.plan, {"commit": rebased.new["u"], "awi": [WI_A]})
    first_done = _snapshot([rebased.plan])
    if failure != "receipt":
        assert _snapshot([rebased.handoff, rebased.unaffected]) == before
    git_repository.git_output(rebased.repo, "config", "core.abbrev", "16")
    assert rewrite_plan_commits.main(args) == 0
    assert f"変更なし: {rebased.plan} 範囲外として残したOID（1件）: {rebased.orphan}" in capsys.readouterr().out
    assert _snapshot([rebased.plan]) == first_done
    events = commit_mapping.read_events(rebased.handoff, rebased.handoff.read_text(encoding="utf-8"))
    assert commit_mapping.read_mapping(rebased.repo, events, {WI_B}) == {rebased.new["b"]: {WI_B}}
    assert rebased.handoff.read_text(encoding="utf-8").count("レーン統合のrebase") == 1

    done = _snapshot([rebased.plan, rebased.handoff, rebased.unaffected])
    pairs[rebased.old["a"]].reverse()
    table.write_text(json.dumps({oid[:12]: pairs[oid] for oid in reversed(pairs)}), encoding="utf-8")
    assert rewrite_plan_commits.main(args) == 0
    assert _snapshot([rebased.plan, rebased.handoff, rebased.unaffected]) == done


@pytest.mark.parametrize("boundary", ["prepare", "body", "mapping", "receipt"])
def test_interrupted_rewrite_resumes_only_its_own_operation(
    rebased: _Rebased, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    """各置換の直後に中断しても、別の書換えは進めず、元の操作だけが残りを1回公開する。"""
    record = rebased.plan
    commit_mapping.append_event(record, {"commit": rebased.orphan, "awi": [WI_A]})
    table = rebased.range_map("a", "u", "b")
    args = rebased.argv(table)
    mapping = commit_mapping.mapping_path(record)
    destination = {
        "prepare": record.with_suffix(".wi-rewrite.pending"),
        "body": record,
        "mapping": mapping,
        "receipt": record.with_suffix(".wi-rewrite.receipt"),
    }[boundary]
    replace = os.replace

    def interrupt(source: str, target: pathlib.Path) -> None:
        replace(source, target)
        if target == destination:
            raise KeyboardInterrupt

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", interrupt)
        with pytest.raises(KeyboardInterrupt):
            rewrite_plan_commits.main(args)
    if boundary == "prepare":
        # 旧版が同じ本文と対応を書いた場合でも、この操作による置換の完了とは区別する。
        staging = record.with_suffix(".wi-rewrite.pending")
        original_body, original_mapping = record.read_bytes(), mapping.read_bytes()
        record.write_bytes((staging / "body").read_bytes())
        mapping.write_bytes((staging / "mapping").read_bytes())
        other = _snapshot([record, rebased.handoff, rebased.unaffected])
        assert rewrite_plan_commits.main(args) == 1
        assert _snapshot([record, rebased.handoff, rebased.unaffected]) == other
        record.write_bytes(original_body)
        mapping.write_bytes(original_mapping)
    before = _snapshot([record, rebased.handoff, rebased.unaffected])
    rebased.range_map("a", "b")
    assert rewrite_plan_commits.main(args) == 1
    assert _snapshot([record, rebased.handoff, rebased.unaffected]) == before
    rebased.range_map("a", "u", "b")
    assert rewrite_plan_commits.main(args) == 0
    assert record.read_text(encoding="utf-8").count("レーン統合のrebase") == 1
    rewrites = [event for event in commit_mapping.file_events(mapping) if "rewrite" in event]
    assert len(rewrites) == 1 and set(rewrites[0]) == {"rewrite"}
    after = _snapshot([record, rebased.handoff, rebased.unaffected])
    assert rewrite_plan_commits.main(args) == 0
    assert _snapshot([record, rebased.handoff, rebased.unaffected]) == after


@pytest.mark.parametrize("difference", ["previous-head", "current-head", "unrecorded-commit", "other-awi"])
def test_distinct_operation_with_same_record_projection(
    rebased: _Rebased, capsys: pytest.CaptureFixture[str], difference: str
) -> None:
    """記録1件の旧新OIDと割当が一致しても、両端のHEAD・全対応・全割当の違う回は未追記として拒否する。"""
    commit_mapping.append_event(rebased.plan, {"commit": rebased.orphan, "awi": [WI_A]})
    table = rebased.range_map("a", "u", "b")
    pairs = json.loads(table.read_text(encoding="utf-8"))
    pairs[rebased.old["a"]] = [{"commit": rebased.new["a"], "awi": [WI_A]}]
    table.write_text(json.dumps(pairs), encoding="utf-8")
    args = rebased.argv(table)
    plan_only = [arg for arg in args if not arg.startswith("--handoff=")]
    assert rewrite_plan_commits.main(plan_only) == 0
    capsys.readouterr()

    if difference == "previous-head":
        git_repository.git_output(rebased.repo, "switch", "--detach", rebased.previous_head)
        other = _commit(rebased.repo, "later old tip")
        git_repository.git_output(rebased.repo, "switch", "--detach", rebased.new["b"])
        args = [f"--previous-head={other}" if arg.startswith("--previous-head=") else arg for arg in args]
    elif difference == "current-head":
        _commit(rebased.repo, "later new tip")
    elif difference == "unrecorded-commit":
        del pairs[rebased.old["u"]]
    else:
        pairs[rebased.old["a"]][0]["awi"].append(WI_B)
    table.write_text(json.dumps(pairs), encoding="utf-8")

    before = _snapshot([rebased.plan, rebased.handoff, rebased.unaffected])
    assert rewrite_plan_commits.main(args) == 1
    assert "過去の書換えの未追記" in capsys.readouterr().err
    assert _snapshot([rebased.plan, rebased.handoff, rebased.unaffected]) == before


@pytest.mark.parametrize("history", ["new-only", "different-target", "different-assignment", "matching-legacy"])
def test_rewrite_destination_is_not_proof_of_application(
    rebased: _Rebased, capsys: pytest.CaptureFixture[str], history: str
) -> None:
    """新OIDの存在や、宛先・AWI割当の異なる書換えは今回の適用を示さない。"""
    stale = _handoff(rebased.repo.parent / "stale.md")
    commit_mapping.append_event(stale, {"commit": rebased.orphan, "awi": [WI_S]})
    rewrite = rebased.range_map("a", "u", "b")
    if history == "new-only":
        commit_mapping.append_event(stale, {"commit": rebased.new["b"], "awi": [WI_S]})
    else:
        old = rebased.old["a"]
        commit_mapping.append_event(stale, {"commit": old, "awi": [WI_S]})
        if history == "matching-legacy":
            commit_mapping.append_event(stale, {"rewrite": {old: rebased.new["a"]}})
        elif history == "different-target":
            commit_mapping.append_event(stale, {"rewrite": {old: rebased.new["b"]}})
        else:
            first, second = rebased.new["a"], rebased.new["b"]
            commit_mapping.append_event(
                stale, {"rewrite": {old: [{"commit": first, "awi": [WI_S]}, {"commit": second, "awi": []}]}}
            )
            pairs = json.loads(rewrite.read_text(encoding="utf-8"))
            pairs[old] = [{"commit": first, "awi": [WI_A]}, {"commit": second, "awi": [WI_S]}]
            rewrite.write_text(json.dumps(pairs), encoding="utf-8")
    before = _snapshot([rebased.plan, rebased.handoff, rebased.unaffected, stale])
    assert rewrite_plan_commits.main(rebased.argv(rewrite, extra=(f"--handoff={stale}",))) == 1
    assert f"{stale}: 過去の書換えの未追記" in capsys.readouterr().err
    assert _snapshot([rebased.plan, rebased.handoff, rebased.unaffected, stale]) == before


@pytest.mark.cpu_limit(
    2.5,
    reason=(
        "WI対応の無いcommitを含む範囲全体の対応表で計画と引き継ぎ記録の"
        + "両方へ追記し、`plan-commits`が新OIDを返す。 実Git"
        + "の旧/新履歴とWI別の対応記録を保持し、公開保存と拒否後の全本文不変を"
        + "確認する。Linux同群CPU最大0.909秒。"
    ),
)
def test_range_map_appends_to_all_records(rebased: _Rebased, capsys: pytest.CaptureFixture[str]) -> None:
    """WI対応の無いcommitを含む範囲全体の対応表で計画と引き継ぎ記録の両方へ追記し、`plan-commits`が新OIDを返す。"""
    rewrite = rebased.range_map("a", "u", "b")

    before = _snapshot([rebased.plan, rebased.handoff, rebased.unaffected])
    assert (
        run_script.dispatch(
            argparse.Namespace(
                script_name="plan-progress",
                script_args=[
                    str(rebased.plan),
                    "--worktree",
                    str(rebased.repo),
                    "--rewrite-map",
                    str(rewrite),
                    "--completed-step",
                    "範囲更新",
                    "--result",
                    "対応表を適用",
                ],
            )
        )
        == 1
    )
    assert "plan-rewrite" in capsys.readouterr().err
    assert _snapshot([rebased.plan, rebased.handoff, rebased.unaffected]) == before

    assert rewrite_plan_commits.main(rebased.argv(rewrite)) == 0
    output = capsys.readouterr().out.splitlines()
    assert f"追記: {rebased.plan}（1件）" in output
    assert f"追記: {rebased.handoff}（1件）" in output
    assert "範囲全体の対応を追記" in rebased.plan.read_text(encoding="utf-8")
    assert rebased.handoff.read_text(encoding="utf-8").endswith("レーン統合のrebase: 範囲全体の対応を追記\n")

    short_a = git_repository.git_output(rebased.repo, "rev-parse", "--short", rebased.new["a"])
    short_b = git_repository.git_output(rebased.repo, "rev-parse", "--short", rebased.new["b"])
    assert get_plan_commits.main(_commits(rebased, rebased.plan, WI_A, handoff=False)) == 0
    assert _read_commits(capsys) == [short_a]
    assert get_plan_commits.main(_commits(rebased, rebased.handoff, WI_B, handoff=True)) == 0
    assert _read_commits(capsys) == [short_b]


@pytest.mark.cpu_limit(
    2.5,
    reason=(
        "書換え対象のcommitを持たない記録はバイト列を変えず、標準出力に変"
        + "更なしと出る。 実Gitの旧/新履歴とWI別の対応記録を保持し、公開保"
        + "存と拒否後の全本文不変を確認する。Linux同群CPU最大0.909秒"
        + "。"
    ),
)
def test_unaffected_record_is_unchanged(rebased: _Rebased, capsys: pytest.CaptureFixture[str]) -> None:
    """書換え対象のcommitを持たない記録はバイト列を変えず、標準出力に変更なしと出る。"""
    before = _snapshot([rebased.unaffected])

    assert rewrite_plan_commits.main(rebased.argv(rebased.range_map("a", "u", "b"))) == 0

    assert f"変更なし: {rebased.unaffected}" in capsys.readouterr().out.splitlines()
    assert _snapshot([rebased.unaffected]) == before


@pytest.mark.cpu_limit(
    2.5,
    reason=(
        "記録済みのcommitとそのfixupを1件へ統合した対応表で、統合後"
        + "のcommitが両方のAWI集合を継承する。 実Gitの旧/新履歴と"
        + "WI別の対応記録を保持し、公開保存と拒否後の全本文不変を確認する。"
        + "Linux同群CPU最大0.909秒。"
    ),
)
def test_fixup_squash_inherits_union(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """記録済みのcommitとそのfixupを1件へ統合した対応表で、統合後のcommitが両方のAWI集合を継承する。"""
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(tmp_path / "private-notes"))
    repo = tmp_path / "repo"
    repo.mkdir()
    git_repository.init_repository(repo, initial_branch="main")
    base = _commit(repo, "base")
    plan = _plan(tmp_path / "plans" / "07-squash-1a2b.md", [WI_A, WI_B])
    target = _commit(repo, "target")
    _record(repo, plan, base, [WI_A])
    fixup = _commit(repo, "fixup! target")
    _record(repo, plan, target, [WI_B])
    git_repository.git_output(repo, "reset", "--hard", base)
    squashed = _commit(repo, "target")
    rewrite = tmp_path / "rewrite.json"
    rewrite.write_text(json.dumps({target: squashed, fixup: squashed}), encoding="utf-8")

    assert (
        rewrite_plan_commits.main(
            [
                f"--worktree={repo}",
                f"--previous-head={fixup}",
                f"--rewrite-map={rewrite}",
                "--completed-step=autosquash",
                "--result=統合",
                f"--plan={plan}",
            ]
        )
        == 0
    )
    events = commit_mapping.read_events(plan, plan.read_text(encoding="utf-8"))
    short = git_repository.git_output(repo, "rev-parse", "--short", squashed)
    assert commit_mapping.get_commits(repo, events, [WI_A, WI_B], {WI_A, WI_B}) == {WI_A: [short], WI_B: [short]}


@pytest.mark.cpu_limit(
    2.5,
    reason=(
        "書換え前のHEADから到達できる記録済みOIDが対応表に無いと、全記録"
        + "を変えずに失敗し、記録・OID・次の操作を示す。 実Gitの旧/新履歴"
        + "とWI別の対応記録を保持し、公開保存と拒否後の全本文不変を確認する。"
        + "Linux同群CPU最大0.909秒。"
    ),
)
def test_missing_recorded_oid_fails_without_writes(rebased: _Rebased, capsys: pytest.CaptureFixture[str]) -> None:
    """書換え前のHEADから到達できる記録済みOIDが対応表に無いと、全記録を変えずに失敗し、記録・OID・次の操作を示す。"""
    commit_mapping.append_event(rebased.plan, {"commit": rebased.orphan, "awi": [WI_A]})
    records = [rebased.plan, rebased.handoff, rebased.unaffected]
    before = _snapshot(records)

    assert rewrite_plan_commits.main(rebased.argv(rebased.range_map("u", "b"))) == 1

    error = capsys.readouterr().err
    assert f"{rebased.plan}: 対応表の不足" in error and rebased.old["a"] in error
    assert "過去の書換えの未追記" not in error
    guided = re.search(r"次の操作: .*atk run-script history-compare -- --operation <([a-z|]+)>", error)
    assert guided is not None and "amend" in guided.group(1).split("|")
    assert run_script.dispatch(argparse.Namespace(script_name="history-compare", script_args=["--help"])) == 0
    accepted = re.search(r"--operation \{([a-z,]+)\}", capsys.readouterr().out)
    assert accepted is not None and set(guided.group(1).split("|")) <= set(accepted.group(1).split(","))
    assert _snapshot(records) == before


@pytest.mark.cpu_limit(
    2.5,
    reason=(
        "書換え前後のどちらのHEADにも無い記録済みOIDを持つ記録は、対応表"
        + "の不足と区別して失敗し、全記録を変えない。 実Gitの旧/新履歴とWI"
        + "別の対応記録を保持し、公開保存と拒否後の全本文不変を確認する。"
        + "Linux同群CPU最大0.909秒。"
    ),
)
def test_past_append_omission_is_distinguished(rebased: _Rebased, capsys: pytest.CaptureFixture[str]) -> None:
    """書換え前後のどちらのHEADにも無い記録済みOIDを持つ記録は、対応表の不足と区別して失敗し、全記録を変えない。"""
    stale = _handoff(rebased.repo.parent / "handoff-stale.md")
    commit_mapping.append_event(stale, {"commits": [rebased.orphan], "awi": [WI_S]})
    records = [rebased.plan, rebased.handoff, rebased.unaffected, stale]
    before = _snapshot(records)

    assert rewrite_plan_commits.main(rebased.argv(rebased.range_map("a", "u", "b"), extra=(f"--handoff={stale}",))) == 1

    error = capsys.readouterr().err
    assert f"{stale}: 過去の書換えの未追記" in error and rebased.orphan in error
    assert "対応表の不足（" not in error
    assert "plan-rewrite" in error and "反復指定" in error
    assert _snapshot(records) == before


@pytest.mark.cpu_limit(
    2.5,
    reason=(
        "書換え前のHEADから到達できない旧OID、または現在のHEADに無い"
        + "新OIDを持つ対応表は、全記録を変えずに失敗する。 実Gitの旧/新履"
        + "歴とWI別の対応記録を保持し、公開保存と拒否後の全本文不変を確認する。"
        + "Linux同群CPU最大0.909秒。"
    ),
)
@pytest.mark.parametrize("entry", ["old-unreachable", "new-outside-head"])
def test_invalid_map_entry_fails_without_writes(rebased: _Rebased, capsys: pytest.CaptureFixture[str], entry: str) -> None:
    """書換え前のHEADから到達できない旧OID、または現在のHEADに無い新OIDを持つ対応表は、全記録を変えずに失敗する。"""
    pairs = {rebased.old[name]: rebased.new[name] for name in ("a", "u", "b")}
    if entry == "old-unreachable":
        pairs[rebased.orphan] = rebased.new["a"]
    else:
        pairs[rebased.old["u"]] = rebased.old["b"]
    rewrite = rebased.repo.parent / "invalid.json"
    rewrite.write_text(json.dumps(pairs), encoding="utf-8")
    records = [rebased.plan, rebased.handoff, rebased.unaffected]
    before = _snapshot(records)

    assert rewrite_plan_commits.main(rebased.argv(rewrite)) == 1

    assert "次の操作: " in capsys.readouterr().err
    assert _snapshot(records) == before


@pytest.mark.cpu_limit(
    2.5,
    reason=(
        "保存済み計画の領域にある計画を渡すと、全記録を変えずに失敗し、取得の操"
        + "作を示す。 実Gitの旧/新履歴とWI別の対応記録を保持し、公開保存と"
        + "拒否後の全本文不変を確認する。Linux同群CPU最大0.909秒。"
    ),
)
def test_saved_plan_is_rejected(rebased: _Rebased, capsys: pytest.CaptureFixture[str]) -> None:
    """保存済み計画の領域にある計画を渡すと、全記録を変えずに失敗し、取得の操作を示す。"""
    saved = rebased.repo.parent / "private-notes" / "plans" / "2026" / "10" / rebased.plan.name
    saved.parent.mkdir(parents=True)
    saved.write_bytes(rebased.plan.read_bytes())
    commit_mapping.mapping_path(saved).write_bytes(commit_mapping.mapping_path(rebased.plan).read_bytes())
    records = [saved, rebased.handoff, rebased.unaffected]
    before = _snapshot(records)
    argv = [arg for arg in rebased.argv(rebased.range_map("a", "u", "b")) if not arg.startswith("--plan=")]

    assert rewrite_plan_commits.main([*argv, f"--plan={saved}"]) == 1

    error = capsys.readouterr().err
    assert str(saved) in error and "atk plans checkout" in error
    assert _snapshot(records) == before


@pytest.mark.cpu_limit(
    2.5,
    reason=(
        "`atk run-script plan-rewrite`の公開名から"
        + "起動し、計画の対応が新OIDへ移る。 実Gitの旧/新履歴とWI別の対"
        + "応記録を保持し、公開保存と拒否後の全本文不変を確認する。Linux同群"
        + "CPU最大0.909秒。"
    ),
)
def test_run_script_entry_rewrites_records(rebased: _Rebased, capsys: pytest.CaptureFixture[str]) -> None:
    """`atk run-script plan-rewrite`の公開名から起動し、計画の対応が新OIDへ移る。"""
    args = argparse.Namespace(script_name="plan-rewrite", script_args=["--", *rebased.argv(rebased.range_map("a", "u", "b"))])

    assert run_script.dispatch(args) == 0
    capsys.readouterr()
    events = commit_mapping.read_events(rebased.plan, rebased.plan.read_text(encoding="utf-8"))
    short = git_repository.git_output(rebased.repo, "rev-parse", "--short", rebased.new["a"])
    assert commit_mapping.get_commits(rebased.repo, events, [WI_A], {WI_A}) == {WI_A: [short]}


@pytest.mark.cpu_limit(
    2.5,
    reason=(
        "記録のcommitを持たない別の作業ツリーを`--worktree`へ"
        + "渡すと、公開名の起動は全記録を変えずに終了1となる。 実Gitの旧/新"
        + "履歴とWI別の対応記録を保持し、公開保存と拒否後の全本文不変を確認する"
        + "。Linux同群CPU最大0.909秒。"
    ),
)
def test_run_script_entry_with_other_worktree_fails_without_writes(
    rebased: _Rebased, capsys: pytest.CaptureFixture[str]
) -> None:
    """記録のcommitを持たない別の作業ツリーを`--worktree`へ渡すと、公開名の起動は全記録を変えずに終了1となる。

    診断の`次の操作:`は`--worktree`が対象作業ツリーを指すかの確認を示す。
    """
    other = rebased.repo.parent / "other"
    other.mkdir()
    git_repository.init_repository(other, initial_branch="main")
    _commit(other, "other")
    records = [rebased.plan, rebased.handoff, rebased.unaffected]
    before = _snapshot(records)
    argv = [
        f"--worktree={other}" if arg.startswith("--worktree=") else arg
        for arg in rebased.argv(rebased.range_map("a", "u", "b"))
    ]

    assert run_script.dispatch(argparse.Namespace(script_name="plan-rewrite", script_args=["--", *argv])) == 1

    error = capsys.readouterr().err
    assert "次の操作:" in error and "--worktreeが対象作業ツリーを指すか確かめて再実行する" in error
    assert _snapshot(records) == before


@pytest.mark.cpu_limit(
    2.5,
    reason=(
        "探索先ごとの未指定記録を公開コマンドで検出し、全入力の内容を保持する。"
        + " 実Gitの旧/新履歴とWI別の対応記録を保持し、公開保存と拒否後の全"
        + "本文不変を確認する。Linux同群CPU最大0.909秒。"
    ),
)
@pytest.mark.parametrize("location", ["working", "saved", "handoff"])
def test_omitted_record_is_detected_before_any_writes(
    rebased: _Rebased, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], location: str
) -> None:
    """探索先ごとの未指定記録を公開コマンドで検出し、全入力の内容を保持する。"""
    root = rebased.repo.parent
    if location == "handoff":
        directory = managed_temp.create_managed_temp("rewrite-handoff-test")
        omitted = _handoff(directory / "handoff.md")
    else:
        directory = root / ("working" if location == "working" else "private-notes/plans/2026/10")
        omitted = _plan(directory / "07-omitted.md", [WI_A])
        if location == "working":
            monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root / "config"))
            directory.rename(root / "config")
            (root / "config/plans").mkdir()
            (root / "config/07-omitted.md").rename(root / "config/plans/07-omitted.md")
            omitted = root / "config/plans/07-omitted.md"
    commit_mapping.append_event(omitted, {"commit": rebased.old["a"], "awi": [WI_A]})
    before = _snapshot([rebased.plan, rebased.handoff, omitted])
    assert rewrite_plan_commits.main(rebased.argv(rebased.range_map("a", "u", "b"))) == 1
    error = capsys.readouterr().err
    assert str(omitted) in error and rebased.old["a"] in error
    assert _snapshot([rebased.plan, rebased.handoff, omitted]) == before
    if location != "saved":
        flag = "--handoff" if location == "handoff" else "--plan"
        assert rewrite_plan_commits.main(rebased.argv(rebased.range_map("a", "u", "b"), extra=(flag, str(omitted)))) == 0
    else:
        assert "atk plans checkout" in error
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root / "checkout-config"))
        notes = root / "private-notes"
        git_repository.init_repository(notes, initial_branch="main")
        git_repository.commit_all(notes, "保存済み計画")
        with pytest.raises(SystemExit) as raised:
            atk.main(["plans", "checkout", "2026/10/07-omitted.md"])
        assert raised.value.code == 0
        capsys.readouterr()
        working = root / "checkout-config/plans/07-omitted.md"
        assert working.read_bytes() == before[omitted]
        assert rewrite_plan_commits.main(rebased.argv(rebased.range_map("a", "u", "b"), extra=("--plan", str(working)))) == 0
        events = commit_mapping.read_events(working, working.read_text(encoding="utf-8"))
        assert commit_mapping.get_commits(rebased.repo, events, [WI_A], {WI_A}) == {
            WI_A: [commit_mapping.short_oid(rebased.repo, rebased.new["a"])]
        }


@pytest.mark.cpu_limit(
    2.5,
    reason=(
        "4回の書換えを順に渡し、中間OIDがHEADから消えても最終OIDへ継"
        + "承できる。 実Gitの旧/新履歴とWI別の対応記録を保持し、公開保存と"
        + "拒否後の全本文不変を確認する。Linux同群CPU最大0.909秒。"
    ),
)
def test_four_maps_compose_before_head_validation(rebased: _Rebased, capsys: pytest.CaptureFixture[str]) -> None:
    """4回の書換えを順に渡し、中間OIDがHEADから消えても最終OIDへ継承できる。"""
    root = rebased.repo.parent
    stable = git_repository.git_output(rebased.repo, "rev-parse", "HEAD~4")
    previous = rebased.old
    maps = []
    last_previous_head = rebased.previous_head
    for index in range(4):
        if index:
            last_previous_head = previous["b"]
            git_repository.git_output(rebased.repo, "reset", "--hard", stable)
            _commit(rebased.repo, f"upstream-{index}")
            current = {name: _commit(rebased.repo, f"{name}-{index}") for name in ("a", "u", "b")}
        else:
            current = rebased.new
        path = root / f"rewrite-{index}.json"
        path.write_text(json.dumps({previous[name]: current[name] for name in previous}), encoding="utf-8")
        maps.append(path)
        previous = current
    before = _snapshot([rebased.plan, rebased.handoff, rebased.unaffected])
    latest_only = [arg for arg in rebased.argv(maps[-1]) if not arg.startswith("--previous-head=")]
    assert rewrite_plan_commits.main([*latest_only, "--previous-head", last_previous_head]) == 1
    error = capsys.readouterr().err
    assert "過去の書換えの未追記" in error and "反復指定" in error
    assert rebased.old["a"] in error
    assert _snapshot([rebased.plan, rebased.handoff, rebased.unaffected]) == before
    args = rebased.argv(maps[0], extra=tuple(value for path in maps[1:] for value in ("--rewrite-map", str(path))))
    assert rewrite_plan_commits.main(args) == 0
    capsys.readouterr()
    events = commit_mapping.read_events(rebased.plan, rebased.plan.read_text(encoding="utf-8"))
    assert commit_mapping.get_commits(rebased.repo, events, [WI_A], {WI_A}) == {
        WI_A: [commit_mapping.short_oid(rebased.repo, previous["a"])]
    }


@pytest.mark.cpu_limit(
    2.5,
    reason=(
        "他repo・過去の書換え・HEADに残るcommitの記録は入力の追加"
        + "を要求しない。 実Gitの旧/新履歴とWI別の対応記録を保持し、公開保"
        + "存と拒否後の全本文不変を確認する。Linux同群CPU最大0.909秒"
        + "。"
    ),
)
def test_discovery_ignores_unrelated_and_unchanged_commits(
    rebased: _Rebased, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """他repo・過去の書換え・HEADに残るcommitの記録は入力の追加を要求しない。"""
    config = rebased.repo.parent / "config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    stable = git_repository.git_output(rebased.repo, "rev-parse", "HEAD~4")
    for index, oid in enumerate(("f" * 40, rebased.orphan, stable)):
        record = _plan(config / "plans" / f"07-unrelated-{index}.md", [WI_A])
        commit_mapping.append_event(record, {"commit": oid, "awi": [WI_A]})
    rewrite = rebased.range_map("a", "u", "b")
    pairs = json.loads(rewrite.read_text(encoding="utf-8"))
    pairs[stable] = stable
    rewrite.write_text(json.dumps(pairs), encoding="utf-8")
    assert rewrite_plan_commits.main(rebased.argv(rewrite)) == 0
    assert "入力に未指定の記録" not in capsys.readouterr().err


@pytest.mark.cpu_limit(
    2.5,
    reason=(
        "探索中の不正な対応記録は、指定済み記録への継承を妨げない。 実Gitの"
        + "旧/新履歴とWI別の対応記録を保持し、公開保存と拒否後の全本文不変を確"
        + "認する。Linux同群CPU最大0.909秒。"
    ),
)
@pytest.mark.parametrize("source", ["file", "body"])
def test_discovery_skips_candidate_with_invalid_events(rebased: _Rebased, monkeypatch: pytest.MonkeyPatch, source: str) -> None:
    """探索中の不正な対応記録は、指定済み記録への継承を妨げない。"""
    config = rebased.repo.parent / "config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    malformed = _plan(config / "plans" / f"06-malformed-{source}.md", [WI_A])
    mapping = commit_mapping.mapping_path(malformed)
    if source == "file":
        mapping.write_text("{", encoding="utf-8")
    else:
        mapping.write_text("", encoding="utf-8")
        malformed.write_text(
            malformed.read_text(encoding="utf-8") + commit_mapping.PREFIX + "{" + commit_mapping.SUFFIX + "\n",
            encoding="utf-8",
        )

    assert rewrite_plan_commits.main(rebased.argv(rebased.range_map("a", "u", "b"))) == 0


@pytest.mark.cpu_limit(
    2.5,
    reason=(
        "UTF-8として読めない探索候補は、指定済み記録への継承を妨げない。 "
        + "実Gitの旧/新履歴とWI別の対応記録を保持し、公開保存と拒否後の全本"
        + "文不変を確認する。Linux同群CPU最大0.909秒。"
    ),
)
@pytest.mark.parametrize("source", ["file", "body"])
def test_discovery_skips_unreadable_candidate(rebased: _Rebased, monkeypatch: pytest.MonkeyPatch, source: str) -> None:
    """UTF-8として読めない探索候補は、指定済み記録への継承を妨げない。"""
    config = rebased.repo.parent / "config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    unreadable = _plan(config / "plans" / f"06-unreadable-{source}.md", [WI_A])
    mapping = commit_mapping.mapping_path(unreadable)
    if source == "file":
        mapping.write_bytes(b"\xff\xfe{")
    else:
        mapping.write_text("", encoding="utf-8")
        unreadable.write_bytes(unreadable.read_bytes() + b"\xff\xfe\n")

    assert rewrite_plan_commits.main(rebased.argv(rebased.range_map("a", "u", "b"))) == 0
    assert rebased.new["a"][:7] in commit_mapping.mapping_path(rebased.plan).read_text(encoding="utf-8")


@pytest.mark.cpu_limit(
    2.5,
    reason=(
        "候補評価の最終段階から送出された候補不正も候補単位で除外し、指定済み記"
        + "録の継承を完了する。 実Gitの旧/新履歴とWI別の対応記録を保持し、"
        + "公開保存と拒否後の全本文不変を確認する。Linux同群CPU最大0."
        + "909秒。"
    ),
)
@pytest.mark.parametrize(
    "error",
    [next_action.ActionableError("将来の検証段階が候補を不正と判定", next_action="候補を直す"), ValueError("将来の検証段階")],
    ids=["actionable", "value"],
)
def test_discovery_excludes_failures_from_any_candidate_stage(
    rebased: _Rebased, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    """候補評価の最終段階から送出された候補不正も候補単位で除外し、指定済み記録の継承を完了する。

    将来の検証段階が候補評価へ加わり、既存の除外例外以外を送出する形の持ち込み例を、
    候補評価の最後の段階の差し替えで再現する。
    """
    config = rebased.repo.parent / "config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    candidate = _plan(config / "plans" / "06-candidate.md", [WI_A])
    commit_mapping.append_event(candidate, {"commit": rebased.orphan, "awi": [WI_A]})
    calls: list[pathlib.Path] = []

    def _fail_stage(*args: Any, **kwargs: Any) -> list[str]:
        del args, kwargs
        calls.append(candidate)
        raise error

    monkeypatch.setattr(commit_mapping, "rewritten_commits", _fail_stage)

    assert rewrite_plan_commits.main(rebased.argv(rebased.range_map("a", "u", "b"))) == 0
    assert calls
    assert rebased.new["a"][:7] in commit_mapping.mapping_path(rebased.plan).read_text(encoding="utf-8")


@pytest.mark.cpu_limit(
    2.5,
    reason=(
        "明示指定した不正な対応記録は、保存前に失敗する。 実Gitの旧/新履歴"
        + "とWI別の対応記録を保持し、公開保存と拒否後の全本文不変を確認する。"
        + "Linux同群CPU最大0.909秒。"
    ),
)
@pytest.mark.parametrize("source", ["file", "body"])
def test_explicit_record_with_invalid_events_fails_without_writes(
    rebased: _Rebased, capsys: pytest.CaptureFixture[str], source: str
) -> None:
    """明示指定した不正な対応記録は、保存前に失敗する。"""
    malformed = _plan(rebased.repo.parent / f"malformed-{source}.md", [WI_A])
    mapping = commit_mapping.mapping_path(malformed)
    if source == "file":
        mapping.write_text("{", encoding="utf-8")
    else:
        mapping.write_text("", encoding="utf-8")
        malformed.write_text(
            malformed.read_text(encoding="utf-8") + commit_mapping.PREFIX + "{" + commit_mapping.SUFFIX + "\n",
            encoding="utf-8",
        )
    records = [rebased.plan, rebased.handoff, rebased.unaffected, malformed]
    before = _snapshot(records)

    assert rewrite_plan_commits.main(rebased.argv(rebased.range_map("a", "u", "b"), extra=("--plan", str(malformed)))) == 1

    assert "commit対応のJSONが不正です" in capsys.readouterr().err
    assert _snapshot(records) == before
