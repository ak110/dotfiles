"""公開履歴比較を隔離したGit履歴で検証する。"""

import json
import os
import pathlib
import subprocess

import pytest

from agent_toolkit import atk
from agent_toolkit._plan import commit_mapping


def _git(repo: pathlib.Path, *argv: str) -> str:
    result = subprocess.run(
        ["git", "-c", "user.name=履歴試験", "-c", "user.email=test@example.com", "-C", str(repo), *argv],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        env={**os.environ, "GIT_SEQUENCE_EDITOR": "true", "GIT_EDITOR": "true"},
        timeout=30,
    )
    return result.stdout.strip()


def _commit(repo: pathlib.Path, filename: str, text: str, subject: str) -> str:
    (repo / filename).write_text(text, encoding="utf-8")
    _git(repo, "add", "--", filename)
    _git(repo, "commit", "--no-verify", "-m", subject)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture(name="repo")
def _repo(tmp_path: pathlib.Path) -> pathlib.Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _commit(repo, "base.txt", "基準", "基準")
    return repo


def _compare(repo: pathlib.Path, output: pathlib.Path, operation: str, bases: tuple[str, str, str, str]) -> int:
    argv = ["run-script", "history-compare", "--", "--operation", operation, "--work-dir", str(repo), "--output", str(output)]
    for name, value in zip(("old-base", "old-head", "new-base", "new-head"), bases, strict=True):
        argv.extend((f"--{name}", value))
    with pytest.raises(SystemExit) as raised:
        atk.main(argv)
    assert isinstance(raised.value.code, int)
    return raised.value.code


@pytest.mark.parametrize("no_prefix", [False, True])
def test_rebase_cli_accepts_only_equal_complete_series(
    repo: pathlib.Path, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], no_prefix: bool
) -> None:
    """接頭辞設定によらず全一致を受理し、変更・追加・欠落を保存せず拒否する。"""
    base = _git(repo, "rev-parse", "HEAD")
    first = _commit(repo, "first.txt", "一", "一つ目")
    old_head = _commit(repo, "second.txt", "二", "二つ目")
    _git(repo, "checkout", "-b", "other", base)
    new_base = _commit(repo, "upstream.txt", "上流", "上流")
    _git(repo, "cherry-pick", first, old_head)
    _git(repo, "commit", "--amend", "--no-verify", "-m", "変更内容は同じで説明だけ更新")
    new_first = _git(repo, "rev-parse", "HEAD~1")
    new_head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "config", "diff.noprefix", str(no_prefix).lower())
    output = tmp_path / "valid.json"
    assert _compare(repo, output, "rebase", (base, old_head, new_base, new_head)) == 0
    assert json.loads(output.read_text(encoding="utf-8")) == {first: new_first, old_head: new_head}
    missing = tmp_path / "missing.json"
    assert _compare(repo, missing, "rebase", (base, old_head, new_base, new_first)) != 0
    added_head = _commit(repo, "extra.txt", "追加", "追加")
    added = tmp_path / "added.json"
    assert _compare(repo, added, "rebase", (base, old_head, new_base, added_head)) != 0
    (repo / "second.txt").write_text("変更", encoding="utf-8")
    _git(repo, "add", "--", "second.txt")
    _git(repo, "commit", "--amend", "--no-verify", "--no-edit")
    changed = tmp_path / "changed.json"
    assert _compare(repo, changed, "rebase", (base, old_head, new_base, _git(repo, "rev-parse", "HEAD"))) != 0
    assert not any(path.exists() for path in (missing, added, changed))
    assert "次の操作:" in capsys.readouterr().err


def test_rebase_accepts_changed_context_and_hunk_position(repo: pathlib.Path, tmp_path: pathlib.Path) -> None:
    """上流による周辺行と位置の変化を、同じ変更行から区別する。"""
    base = _commit(repo, "content.txt", "前\n対象\n後\n", "準備")
    old_head = _commit(repo, "content.txt", "前\n修正\n後\n", "修正")
    _git(repo, "checkout", "-b", "context", base)
    new_base = _commit(repo, "content.txt", "追加\n前改訂\n対象\n後改訂\n", "上流")
    new_head = _commit(repo, "content.txt", "追加\n前改訂\n修正\n後改訂\n", "別の説明")
    output = tmp_path / "context.json"
    assert _compare(repo, output, "rebase", (base, old_head, new_base, new_head)) == 0
    assert json.loads(output.read_text(encoding="utf-8")) == {old_head: new_head}


@pytest.mark.parametrize("violation", ["whitespace", "path", "mode", "type", "binary", "ambiguous"])
def test_rebase_rejects_content_and_ambiguous_correspondence(
    repo: pathlib.Path, tmp_path: pathlib.Path, violation: str
) -> None:
    """表示上の類似で変更内容の差や曖昧なcommit対応を受理しない。"""
    base = _git(repo, "rev-parse", "HEAD")
    old_head = _commit(repo, "content.txt", "value\n", "元")
    if violation == "ambiguous":
        _git(repo, "revert", "--no-edit", old_head)
        old_head = _commit(repo, "content.txt", "value\n", "再追加")
    _git(repo, "checkout", "-b", "different", base)
    filename = "other.txt" if violation == "path" else "content.txt"
    text = " value\n" if violation == "whitespace" else "value\n"
    _commit(repo, filename, text, "新")
    path = repo / filename
    if violation == "mode":
        path.chmod(0o755)
    elif violation == "type":
        path.unlink()
        path.symlink_to("value")
    elif violation == "binary":
        path.write_bytes(b"value\x00\n")
    if violation in {"mode", "type", "binary"}:
        _git(repo, "add", "--", filename)
        _git(repo, "commit", "--amend", "--no-verify", "--no-edit")
    output = tmp_path / "rejected.json"
    assert _compare(repo, output, "rebase", (base, old_head, base, _git(repo, "rev-parse", "HEAD"))) != 0
    assert not output.exists()


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
        "patch": "patch-id",
        "ambiguous": "一意",
        "outside": "一意",
    }[violation]
    assert expected in capsys.readouterr().err


@pytest.mark.parametrize("changed", [False, True])
def test_rebase_compares_binary_payload(repo: pathlib.Path, tmp_path: pathlib.Path, changed: bool) -> None:
    """バイナリー同士の比較でも同じ内容だけを受理し、対応表の保存を分ける。"""
    base = _git(repo, "rev-parse", "HEAD")
    path = repo / "image.bin"
    path.write_bytes(b"original\x00payload")
    _git(repo, "add", "--", "image.bin")
    _git(repo, "commit", "--no-verify", "-m", "元の画像")
    old_head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-b", "binary", base)
    new_base = _commit(repo, "upstream.txt", "上流", "上流")
    path.write_bytes(b"changed\x00payload" if changed else b"original\x00payload")
    _git(repo, "add", "--", "image.bin")
    _git(repo, "commit", "--no-verify", "-m", "説明の異なる画像")
    new_head = _git(repo, "rev-parse", "HEAD")
    output = tmp_path / "binary.json"
    result = _compare(repo, output, "rebase", (base, old_head, new_base, new_head))
    if changed:
        assert result != 0 and not output.exists()
    else:
        assert result == 0
        assert json.loads(output.read_text(encoding="utf-8")) == {old_head: new_head}


@pytest.mark.parametrize("operation", ["rebase", "autosquash"])
def test_generated_mapping_is_consumed_by_public_record_commands(
    repo: pathlib.Path,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    operation: str,
) -> None:
    """公開比較の一対一・多対1対応表を加工せず、計画のWI対応と証拠参照へ渡せる。"""
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
    if operation == "rebase":
        _git(repo, "checkout", "-b", "upstream", base)
        new_base = _commit(repo, "first.txt", "追加\n前改訂\n対象\n後改訂\n", "上流の文脈変更")
        _commit(repo, "first.txt", "追加\n前改訂\n一\n後改訂\n", "一つ目の説明を訂正")
        _commit(repo, "first.txt", "追加\n前改訂\n一修正\n後改訂\n", "修正の説明を訂正")
    else:
        _git(repo, "rebase", "--autosquash", "--no-update-refs", base)
    new_head = _git(repo, "rev-parse", "HEAD")
    mapping = tmp_path / "mapping.json"
    assert _compare(repo, mapping, operation, (base, old_head, new_base, new_head)) == 0
    before = mapping.read_bytes()
    pairs = json.loads(before)
    with pytest.raises(SystemExit) as raised:
        atk.main(
            [
                "run-script",
                "plan-rewrite",
                "--",
                "--worktree",
                str(repo),
                "--previous-head",
                old_head,
                "--rewrite-map",
                str(mapping),
                "--completed-step",
                "履歴検収",
                "--result",
                "公開対応表を消費",
                "--plan",
                str(plan),
            ]
        )
    assert raised.value.code == 0
    events = commit_mapping.read_events(plan, plan.read_text(encoding="utf-8"))
    expected = [_git(repo, "rev-parse", "--short", value) for value in dict.fromkeys(pairs.values())]
    assert commit_mapping.get_commits(repo, events, [wi], {wi}) == {wi: expected}
    evidence = tmp_path / "evidence.json"
    original = {
        "awi": wi,
        "condition": "対応を継承する。",
        "source": f"{wi} 完成条件1",
        "outcome": "達成",
        "reviewed_head": old_head,
        "evidence": f"現行commit:{target} と修正commit:{old_head}。取得版 {target} と比較元 {old_head}",
    }
    evidence.write_text(json.dumps({"wi_conditions": [original], "user_requirements": []}), encoding="utf-8")
    with pytest.raises(SystemExit) as raised:
        atk.main(["run-script", "exec-review-evidence-check", "--", str(evidence), "--rewrite-map", str(mapping)])
    assert raised.value.code == 0
    updated = json.loads(evidence.read_text(encoding="utf-8"))["wi_conditions"][0]
    assert updated["evidence"] == (
        f"現行commit:{pairs[target]} と修正commit:{pairs[old_head]}。取得版 {target} と比較元 {old_head}"
    )
    assert updated["reviewed_head"] == old_head and updated["outcome"] == "達成"
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
    _git(repo, "rebase", "--autosquash", "--no-update-refs", base)
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
