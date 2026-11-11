# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""amendとautosquashの履歴対応を検証する。"""

import json
import os
import pathlib
import subprocess

import pytest

from agent_toolkit._plan import commit_mapping
from agent_toolkit._testing.history_compare_support import _commit, _compare, _git, _rewrite_records
from agent_toolkit._testing.history_compare_support import _repo as _repo

# 手順書`history-rewrite.md`がautosquashに指示する、`<base>`を除くGitの引数。
AUTOSQUASH_ARGUMENTS = ("-c", "submodule.recurse=false", "-c", "rebase.updateRefs=false", "rebase", "-i", "--autosquash")


def _autosquash(repo: pathlib.Path, base: str) -> None:
    """手順書と同じ引数で、編集を素通しする`GIT_SEQUENCE_EDITOR`のautosquashを実行する。"""
    _git(repo, "config", "rebase.updateRefs", "true")
    _git(repo, "branch", "retained", "HEAD~1")
    retained = _git(repo, "rev-parse", "refs/heads/retained")
    tree = _git(repo, "rev-parse", "HEAD^{tree}")
    subprocess.run(
        ["git", "-C", str(repo), *AUTOSQUASH_ARGUMENTS, base],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        env={**os.environ, "GIT_SEQUENCE_EDITOR": ":", "GIT_EDITOR": "true"},
        timeout=30,
    )
    assert _git(repo, "rev-parse", "refs/heads/retained") == retained
    assert _git(repo, "rev-parse", "HEAD^{tree}") == tree
    subjects = _git(repo, "log", "--format=%s", f"{base}..HEAD").splitlines()
    assert not any(subject.startswith(("fixup! ", "squash! ", "amend! ")) for subject in subjects)


@pytest.mark.parametrize("violation", ["tree", "count", "control", "patch", "ambiguous", "outside"])
def test_autosquash_rejects_each_independent_violation(
    repo: pathlib.Path, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], violation: str
) -> None:
    """最終treeが同じ場合も、件数・制御件名・非対象patchと不明な対象を拒否する。"""
    base = _git(repo, "rev-parse", "HEAD")
    subject = "同じ件名" if violation == "ambiguous" else "一つ目"
    first = _commit(repo, "first.txt", "一", subject)
    second_subject = subject if violation == "ambiguous" else "二つ目"
    _commit(repo, "second.txt", "二", second_subject)
    target = base if violation == "outside" else subject if violation == "ambiguous" else first
    old_head = _commit(repo, "first.txt", "一修正", f"fixup! {target}")
    _git(repo, "checkout", "-b", "invalid", base)
    if violation in {"count", "patch"}:
        (repo / "second.txt").write_text("二", encoding="utf-8")
        _git(repo, "add", "--", "second.txt")
    _commit(repo, "first.txt", "一途中" if violation == "patch" else "一修正", subject)
    if violation != "count":
        if violation == "patch":
            _commit(repo, "first.txt", "一修正", second_subject)
        else:
            _commit(
                repo,
                "second.txt",
                "害" if violation == "tree" else "二",
                "fixup! 残存" if violation == "control" else second_subject,
            )
    output = tmp_path / "invalid.json"
    assert _compare(repo, output, "autosquash", (base, old_head, base, _git(repo, "rev-parse", "HEAD"))) != 0
    assert not output.exists()
    expected = {
        "tree": "tree",
        "count": "新件数",
        "control": "制御件名",
        "patch": "変更内容",
        "ambiguous": "一意",
        "outside": "一意",
    }[violation]
    assert expected in capsys.readouterr().err


def test_autosquash_accepts_fixup_near_untouched_change(repo: pathlib.Path, tmp_path: pathlib.Path) -> None:
    """近傍へのfixupで文脈が変わっても、非対象commitの内容が同じなら受理する。"""
    base = _commit(repo, "content.txt", "a\nb\nc\nd\ne\n", "準備")
    target = _commit(repo, "content.txt", "A\nb\nc\nd\ne\n", "対象")
    unchanged = _commit(repo, "content.txt", "A\nB\nc\nd\ne\n", "近傍の変更")
    old_head = _commit(repo, "content.txt", "AA\nB\nc\nd\ne\n", "fixup! 対象")
    _git(repo, "checkout", "-b", "rewritten", base)
    rewritten_target = _commit(repo, "content.txt", "AA\nb\nc\nd\ne\n", "対象")
    new_head = _commit(repo, "content.txt", "AA\nB\nc\nd\ne\n", "近傍の変更")
    output = tmp_path / "autosquash.json"
    assert _compare(repo, output, "autosquash", (base, old_head, base, new_head)) == 0
    assert json.loads(output.read_text(encoding="utf-8")) == {
        target: rewritten_target,
        unchanged: new_head,
        old_head: rewritten_target,
    }


def test_autosquash_accepts_conflict_resolved_later_change(repo: pathlib.Path, tmp_path: pathlib.Path) -> None:
    """fixup競合の解消後も、後続commitの変更内容を保つ系列を受理する。"""
    base = _commit(repo, "content.txt", "before\ntarget\nlater\nafter\n", "準備")
    target = _commit(repo, "content.txt", "before\ntarget-old\nlater\nafter\n", "対象")
    later = _commit(repo, "content.txt", "before\ntarget-old\nLATER\nafter\n", "後続の変更")
    old_head = _commit(repo, "content.txt", "before\ntarget-fixed\nLATER\nafter\n", "fixup! 対象")
    _git(repo, "checkout", "-b", "resolved", base)
    rewritten_target = _commit(repo, "content.txt", "before\ntarget-fixed\nlater\nafter\n", "対象")
    new_head = _commit(repo, "content.txt", "before\ntarget-fixed\nLATER\nafter\n", "後続の変更")
    output = tmp_path / "resolved-autosquash.json"
    assert _compare(repo, output, "autosquash", (base, old_head, base, new_head)) == 0
    assert json.loads(output.read_text(encoding="utf-8")) == {
        target: rewritten_target,
        later: new_head,
        old_head: rewritten_target,
    }


def test_amend_mapping_is_saved_as_one_pair(
    repo: pathlib.Path, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """amendの旧新HEADを1件の対応表へ保存する。"""
    base = _git(repo, "rev-parse", "HEAD")
    old_head = _commit(repo, "first.txt", "一", "一つ目")
    (repo / "first.txt").write_text("一改訂", encoding="utf-8")
    _git(repo, "add", "--", "first.txt")
    _git(repo, "commit", "--amend", "--no-verify", "--no-edit")
    new_head = _git(repo, "rev-parse", "HEAD")
    mapping = tmp_path / "amend.json"
    assert _compare(repo, mapping, "amend", (base, old_head, base, new_head)) == 0
    assert json.loads(mapping.read_text(encoding="utf-8")) == {old_head: new_head}
    capsys.readouterr()


@pytest.mark.parametrize("violation", ["base", "count", "same"])
def test_amend_rejects_invalid_series(
    repo: pathlib.Path, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], violation: str
) -> None:
    """baseの相違・2件以上の系列・同じOIDのamendは対応表を保存せず、amendの端点の指定を次の操作で示す。"""
    base = _git(repo, "rev-parse", "HEAD")
    old_head = _commit(repo, "first.txt", "一", "一つ目")
    if violation == "base":
        _git(repo, "checkout", "-b", "moved", base)
        new_base = _commit(repo, "upstream.txt", "上流", "上流")
        new_head = _commit(repo, "first.txt", "一改訂", "一つ目")
    elif violation == "count":
        new_base = base
        new_head = _commit(repo, "second.txt", "二", "二つ目")
    else:
        new_base, new_head = base, old_head
    output = tmp_path / "amend.json"
    assert _compare(repo, output, "amend", (base, old_head, new_base, new_head)) != 0
    assert not output.exists()
    error = capsys.readouterr().err
    assert {"base": "base", "count": "1件", "same": "同じOID"}[violation] in error
    assert "次の操作:" in error and "--old-head" in error.split("次の操作:", 1)[1]


@pytest.mark.parametrize("operation", ["rebase", "rebase-conflict", "autosquash"])
def test_generated_mapping_is_consumed_by_public_record_commands(
    repo: pathlib.Path,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    operation: str,
) -> None:
    """`history-compare`が保存した一対一・多対1の対応表を、加工せずに計画のWI対応と証拠参照へ渡せる。"""
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(tmp_path / "private-notes"))
    wi = "20261009-043608-001.md"
    plan = tmp_path / "09-history-1a2b.md"
    plan.write_text(
        "# 計画\n\n## 概要\n\n### 計画メタ情報\n\n- 関連WI:\n"
        f"  - {wi}: 対応\n\n## 進捗ログ\n\n| 日時 | 完了した工程 | 結果・特記事項 |\n"
        "| --- | --- | --- |\n",
        encoding="utf-8",
    )
    base = _commit(repo, "first.txt", "前\n対象\n後\n", "対象を準備")
    target = _commit(repo, "first.txt", "前\n一\n後\n", "一つ目")
    commit_mapping.append_event(plan, commit_mapping.commit_event(repo, "HEAD", base, [wi], {wi}))
    old_head = _commit(repo, "first.txt", "前\n一修正\n後\n", "fixup! 一つ目")
    commit_mapping.append_event(plan, commit_mapping.commit_event(repo, "HEAD", target, [wi], {wi}))
    new_base = base
    conflict_options: list[str] = []
    if operation == "rebase-conflict":
        _git(repo, "checkout", "-b", "upstream", base)
        new_base = _commit(repo, "first.txt", "前\n上流\n後\n", "競合する上流変更")
        _git(repo, "checkout", "--detach", old_head)
        stopped = subprocess.run(
            ["git", "-C", str(repo), "rebase", "--onto", new_base, base],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=30,
            env={**os.environ, "GIT_EDITOR": "true"},
        )
        assert stopped.returncode == 1, stopped.stdout + stopped.stderr
        assert _git(repo, "diff", "--name-only", "--diff-filter=U") == "first.txt"
        (repo / "first.txt").write_text("前\n上流と一\n後\n", encoding="utf-8")
        _git(repo, "add", "--", "first.txt")
        # 後続のfixupも同じ行を変えるため、その競合まで実際のrebaseで進める。
        continued = subprocess.run(
            ["git", "-C", str(repo), "-c", "user.name=履歴試験", "-c", "user.email=test@example.com", "rebase", "--continue"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=30,
            env={**os.environ, "GIT_EDITOR": "true"},
        )
        assert continued.returncode == 1, continued.stdout + continued.stderr
        assert _git(repo, "diff", "--name-only", "--diff-filter=U") == "first.txt"
        (repo / "first.txt").write_text("前\n上流と一修正\n後\n", encoding="utf-8")
        _git(repo, "add", "--", "first.txt")
        _git(repo, "rebase", "--continue")
        operation = "rebase"
        conflict_options = ["--resolved-conflict", target, "--resolved-conflict", old_head]
    elif operation == "rebase":
        _git(repo, "checkout", "-b", "upstream", base)
        new_base = _commit(repo, "first.txt", "追加\n前改訂\n対象\n後改訂\n", "上流の文脈変更")
        _commit(repo, "first.txt", "追加\n前改訂\n一\n後改訂\n", "一つ目の説明を訂正")
        _commit(repo, "first.txt", "追加\n前改訂\n一修正\n後改訂\n", "修正の説明を訂正")
    else:
        _autosquash(repo, base)
    new_head = _git(repo, "rev-parse", "HEAD")
    mapping = tmp_path / "mapping.json"
    if conflict_options:
        assert _compare(repo, mapping, operation, (base, old_head, new_base, new_head)) == 1
        assert not mapping.exists()
        error = capsys.readouterr().err
        assert target in error and "--resolved-conflict" in error
    assert _compare(repo, mapping, operation, (base, old_head, new_base, new_head), *conflict_options) == 0
    before = mapping.read_bytes()
    pairs = json.loads(before)
    assert set(pairs) == {target, old_head}
    assert _rewrite_records(repo, old_head, mapping, plan) == 0
    events = commit_mapping.read_events(plan, plan.read_text(encoding="utf-8"))
    expected = [_git(repo, "rev-parse", "--short", value) for value in dict.fromkeys(pairs.values())]
    assert commit_mapping.get_commits(repo, events, [wi], {wi}) == {wi: expected}
    assert mapping.read_bytes() == before
    capsys.readouterr()


def test_autosquash_cli_validates_invariants_and_many_to_one_mapping(
    repo: pathlib.Path, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """複数対象へのfixupを検収し、tree・件数・制御件名・非対象差分を拒否する。"""
    base = _git(repo, "rev-parse", "HEAD")
    first = _commit(repo, "first.txt", "一", "一つ目")
    second = _commit(repo, "second.txt", "二", "二つ目")
    untouched = _commit(repo, "untouched.txt", "三", "三つ目")
    fix_first = _commit(repo, "first.txt", "一修正", f"fixup! {first}")
    old_head = _commit(repo, "second.txt", "二修正", "fixup! 二つ目")
    control = tmp_path / "control.json"
    assert _compare(repo, control, "autosquash", (base, old_head, base, old_head)) != 0
    _autosquash(repo, base)
    series = _git(repo, "rev-list", "--reverse", f"{base}..HEAD").splitlines()
    output = tmp_path / "valid.json"
    assert _compare(repo, output, "autosquash", (base, old_head, base, series[-1])) == 0
    mapping = json.loads(output.read_text(encoding="utf-8"))
    assert mapping == {first: series[0], second: series[1], untouched: series[2], fix_first: series[0], old_head: series[1]}
    wrong_tree = _commit(repo, "extra.txt", "害", "余分")
    invalid = tmp_path / "invalid.json"
    assert _compare(repo, invalid, "autosquash", (base, old_head, base, wrong_tree)) != 0
    assert not control.exists() and not invalid.exists()
    assert "次の操作:" in capsys.readouterr().err
