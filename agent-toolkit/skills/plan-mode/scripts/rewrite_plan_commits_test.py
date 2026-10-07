"""`atk run-script plan-rewrite`が範囲全体の対応表を全記録へ1回で追記する契約を実Gitで確かめる。"""

import argparse
import dataclasses
import json
import pathlib
import subprocess

import get_plan_commits
import pytest
import rewrite_plan_commits

from agent_toolkit._atk import run_script
from agent_toolkit._plan import commit_mapping

WI_A = "20261007-040310-002.md"
WI_B = "20261007-040310-003.md"
WI_S = "20261007-040310-004.md"


def _git(repo: pathlib.Path, *args: str) -> str:
    """隔離した一時repoでGitを実行し、標準出力を返す。"""
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True, timeout=30).stdout.strip()


def _commit(repo: pathlib.Path, message: str) -> str:
    """空のcommitを作成し、その完全OIDを返す。"""
    _git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


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
    """記録本文と対応記録ファイルのバイト列を返す。"""
    paths = [*records, *(commit_mapping.mapping_path(record) for record in records)]
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
    _git(repo, "init", "--initial-branch=main")
    root = _commit(repo, "root")
    unaffected = _handoff(tmp_path / "handoff-stable.md")
    _commit(repo, "stable")
    _record(repo, unaffected, root, [WI_S])
    stable = _git(repo, "rev-parse", "HEAD")
    orphan = _commit(repo, "orphan")
    _git(repo, "reset", "--hard", stable)
    plan = _plan(tmp_path / "plans" / "07-example-1a2b.md", [WI_A])
    handoff = _handoff(tmp_path / "handoff-lane.md")
    old = {"a": _commit(repo, "a")}
    _record(repo, plan, stable, [WI_A])
    old["u"] = _commit(repo, "unrecorded")
    previous = old["u"]
    old["b"] = _commit(repo, "b")
    _record(repo, handoff, previous, [WI_B])
    previous_head = old["b"]
    _git(repo, "reset", "--hard", stable)
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


def test_range_map_appends_to_all_records(rebased: _Rebased, capsys: pytest.CaptureFixture[str]) -> None:
    """WI対応の無いcommitを含む範囲全体の対応表で計画と引き継ぎ記録の両方へ追記し、`plan-commits`が新OIDを返す。"""
    rewrite = rebased.range_map("a", "u", "b")

    assert rewrite_plan_commits.main(rebased.argv(rewrite)) == 0
    output = capsys.readouterr().out.splitlines()
    assert f"追記: {rebased.plan}（1件）" in output
    assert f"追記: {rebased.handoff}（1件）" in output
    assert "範囲全体の対応を追記" in rebased.plan.read_text(encoding="utf-8")
    assert rebased.handoff.read_text(encoding="utf-8").endswith("レーン統合のrebase: 範囲全体の対応を追記\n")

    short_a = _git(rebased.repo, "rev-parse", "--short", rebased.new["a"])
    short_b = _git(rebased.repo, "rev-parse", "--short", rebased.new["b"])
    assert get_plan_commits.main(_commits(rebased, rebased.plan, WI_A, handoff=False)) == 0
    assert _read_commits(capsys) == [short_a]
    assert get_plan_commits.main(_commits(rebased, rebased.handoff, WI_B, handoff=True)) == 0
    assert _read_commits(capsys) == [short_b]


def test_unaffected_record_is_unchanged(rebased: _Rebased, capsys: pytest.CaptureFixture[str]) -> None:
    """書換え対象のcommitを持たない記録はバイト列を変えず、標準出力に変更なしと出る。"""
    before = _snapshot([rebased.unaffected])

    assert rewrite_plan_commits.main(rebased.argv(rebased.range_map("a", "u", "b"))) == 0

    assert f"変更なし: {rebased.unaffected}" in capsys.readouterr().out.splitlines()
    assert _snapshot([rebased.unaffected]) == before


def test_fixup_squash_inherits_union(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """記録済みのcommitとそのfixupを1件へ統合した対応表で、統合後のcommitが両方のAWI集合を継承する。"""
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(tmp_path / "private-notes"))
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--initial-branch=main")
    base = _commit(repo, "base")
    plan = _plan(tmp_path / "plans" / "07-squash-1a2b.md", [WI_A, WI_B])
    target = _commit(repo, "target")
    _record(repo, plan, base, [WI_A])
    fixup = _commit(repo, "fixup! target")
    _record(repo, plan, target, [WI_B])
    _git(repo, "reset", "--hard", base)
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
    short = _git(repo, "rev-parse", "--short", squashed)
    assert commit_mapping.get_commits(repo, events, [WI_A, WI_B], {WI_A, WI_B}) == {WI_A: [short], WI_B: [short]}


def test_missing_recorded_oid_fails_without_writes(rebased: _Rebased, capsys: pytest.CaptureFixture[str]) -> None:
    """書換え前のHEADから到達できる記録済みOIDが対応表に無いと、全記録を変えずに失敗し、記録・OID・次の操作を示す。"""
    records = [rebased.plan, rebased.handoff, rebased.unaffected]
    before = _snapshot(records)

    assert rewrite_plan_commits.main(rebased.argv(rebased.range_map("u", "b"))) == 1

    error = capsys.readouterr().err
    assert f"{rebased.plan}: 対応表の不足" in error and rebased.old["a"] in error
    assert "過去の書換えの未追記" not in error
    assert "次の操作: " in error and "git range-diff" in error
    assert _snapshot(records) == before


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
    assert "plan-progress" in error
    assert _snapshot(records) == before


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


def test_run_script_entry_rewrites_records(rebased: _Rebased, capsys: pytest.CaptureFixture[str]) -> None:
    """`atk run-script plan-rewrite`の公開名から起動し、計画の対応が新OIDへ移る。"""
    args = argparse.Namespace(script_name="plan-rewrite", script_args=["--", *rebased.argv(rebased.range_map("a", "u", "b"))])

    assert run_script.dispatch(args) == 0
    capsys.readouterr()
    events = commit_mapping.read_events(rebased.plan, rebased.plan.read_text(encoding="utf-8"))
    short = _git(rebased.repo, "rev-parse", "--short", rebased.new["a"])
    assert commit_mapping.get_commits(rebased.repo, events, [WI_A], {WI_A}) == {WI_A: [short]}
