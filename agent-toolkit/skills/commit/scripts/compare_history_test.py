"""公開コマンド`history-compare`の履歴比較を、隔離したGit履歴で検証する。"""

import json
import os
import pathlib
import subprocess

import pytest

from agent_toolkit import atk
from agent_toolkit._plan import commit_mapping

# 手順書`history-rewrite.md`がautosquashに指示する、`<base>`を除くGitの引数。
AUTOSQUASH_ARGUMENTS = ("-c", "submodule.recurse=false", "-c", "rebase.updateRefs=false", "rebase", "-i", "--autosquash")


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


@pytest.fixture(name="repo")
def _repo(tmp_path: pathlib.Path) -> pathlib.Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _commit(repo, "base.txt", "基準", "基準")
    return repo


def _compare(repo: pathlib.Path, output: pathlib.Path, operation: str, bases: tuple[str, str, str, str], *extra: str) -> int:
    argv = ["run-script", "history-compare", "--", "--operation", operation, "--work-dir", str(repo), "--output", str(output)]
    for name, value in zip(("old-base", "old-head", "new-base", "new-head"), bases, strict=True):
        argv.extend((f"--{name}", value))
    argv.extend(extra)
    with pytest.raises(SystemExit) as raised:
        atk.main(argv)
    assert isinstance(raised.value.code, int)
    return raised.value.code


@pytest.mark.parametrize("violation", ["none", "unspecified", "unchanged", "outside", "subject", "paths", "order"])
def test_conflict_resolution_requires_explicit_and_unique_mapping(
    repo: pathlib.Path, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], violation: str
) -> None:
    """競合解消で変わったcommitだけを明示し、件名・変更パス・相対順の対応を公開された呼び出し手段で検収する。"""
    base = _git(repo, "rev-parse", "HEAD")
    first = _commit(repo, "first.txt", "旧変更", "一つ目")
    old_head = _commit(repo, "second.txt", "通常変更", "二つ目")
    _git(repo, "checkout", "-b", "resolved", base)
    new_base = _commit(repo, "upstream.txt", "上流", "上流")
    if violation == "order":
        _commit(repo, "second.txt", "通常変更", "二つ目")
    target = _commit(
        repo, "other.txt" if violation == "paths" else "first.txt", "解消変更", "別件" if violation == "subject" else "一つ目"
    )
    if violation != "order":
        _commit(repo, "second.txt", "別の変更" if violation == "unspecified" else "通常変更", "二つ目")
    head = _git(repo, "rev-parse", "HEAD")
    output = tmp_path / "resolved.json"
    if violation == "none":
        assert _compare(repo, output, "rebase", (base, old_head, new_base, head)) == 1
        assert not output.exists()
        assert first in capsys.readouterr().err
    selected = old_head if violation == "unchanged" else base if violation == "outside" else first
    result = _compare(repo, output, "rebase", (base, old_head, new_base, head), "--resolved-conflict", selected[:12])
    assert result == (0 if violation == "none" else 1)
    if violation == "none":
        assert json.loads(output.read_text(encoding="utf-8")) == {first: target, old_head: head}
    else:
        assert not output.exists()


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
    """上流が周辺行と位置を変えても、変更行が同じなら対応として受理する。"""
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
    """表示が似ていても、変更内容の差や曖昧なcommit対応を受理しない。"""
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
        "patch": "変更内容",
        "ambiguous": "一意",
        "outside": "一意",
    }[violation]
    assert expected in capsys.readouterr().err


@pytest.mark.parametrize("operation", ["rebase", "autosquash"])
def test_history_compare_accepts_same_content_after_parent_change(
    repo: pathlib.Path, tmp_path: pathlib.Path, operation: str
) -> None:
    """親の変更を含む3-way適用結果が一致する履歴書換えを両操作で受理する。"""
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "content.txt").write_text("a\nb\nc\nd\ne\n", encoding="utf-8")
    _git(repo, "add", "--", "content.txt")
    _git(repo, "commit", "--no-verify", "-m", "準備")
    base = _git(repo, "rev-parse", "HEAD")
    if operation == "rebase":
        old_head = _commit(repo, "content.txt", "A\nb\nc\nd\nE\n", "変更")
        _git(repo, "checkout", "-b", "rewritten", base)
        new_base = _commit(repo, "content.txt", "A\nb\nc\nd\ne\n", "上流")
        new_head = _commit(repo, "content.txt", "A\nb\nc\nd\nE\n", "変更")
        expected = {old_head: new_head}
    else:
        target = _commit(repo, "feature.txt", "機能\n", "対象")
        changed = _commit(repo, "content.txt", "A\nb\nc\nd\nE\n", "変更")
        old_head = _commit(repo, "feature.txt", "機能と修正\n", "fixup! 対象")
        _git(repo, "checkout", "-b", "rewritten", base)
        (repo / "content.txt").write_text("A\nb\nc\nd\ne\n", encoding="utf-8")
        (repo / "feature.txt").write_text("機能と修正\n", encoding="utf-8")
        _git(repo, "add", "--", "content.txt", "feature.txt")
        _git(repo, "commit", "--no-verify", "-m", "対象")
        rewritten_target = _git(repo, "rev-parse", "HEAD")
        new_head = _commit(repo, "content.txt", "A\nb\nc\nd\nE\n", "変更")
        new_base = base
        expected = {target: rewritten_target, changed: new_head, old_head: rewritten_target}
    output = tmp_path / f"{operation}.json"
    assert _compare(repo, output, operation, (base, old_head, new_base, new_head)) == 0
    assert json.loads(output.read_text(encoding="utf-8")) == expected


@pytest.mark.parametrize("operation", ["rebase", "autosquash"])
def test_history_compare_rejects_whitespace_only_content_change(
    repo: pathlib.Path, tmp_path: pathlib.Path, operation: str
) -> None:
    """最終treeが一致しても、空白だけ異なる変更内容は両操作で拒否する。"""
    base = _git(repo, "rev-parse", "HEAD")
    if operation == "rebase":
        old_head = _commit(repo, "content.txt", "  value\n", "変更")
        _git(repo, "checkout", "-b", "rewritten", base)
        new_base = _commit(repo, "content.txt", "value \n", "上流")
        new_head = _commit(repo, "content.txt", " value\n", "変更")
    else:
        _commit(repo, "content.txt", "  value\n", "対象")
        old_head = _commit(repo, "content.txt", " value\n", "変更")
        _commit(repo, "feature.txt", "修正\n", "amend! 対象")
        old_head = _git(repo, "rev-parse", "HEAD")
        _git(repo, "checkout", "-b", "rewritten", base)
        (repo / "content.txt").write_text("value \n", encoding="utf-8")
        (repo / "feature.txt").write_text("修正\n", encoding="utf-8")
        _git(repo, "add", "--", "content.txt", "feature.txt")
        _git(repo, "commit", "--no-verify", "-m", "対象")
        new_base = base
        new_head = _commit(repo, "content.txt", " value\n", "変更")
    output = tmp_path / f"{operation}.json"
    assert _compare(repo, output, operation, (base, old_head, new_base, new_head)) == 1
    assert not output.exists()


def test_rebase_rejects_resolved_conflict_with_same_content_after_parent_change(
    repo: pathlib.Path, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """3-way適用で同じ内容になる競合解消commitを指定しても内容変化としては扱わない。"""
    base = _commit(repo, "content.txt", "a\nb\nc\nd\ne\n", "準備")
    old_head = _commit(repo, "content.txt", "A\nb\nc\nd\nE\n", "解消")
    _git(repo, "checkout", "-b", "rewritten", base)
    new_base = _commit(repo, "content.txt", "A\nb\nc\nd\ne\n", "上流")
    new_head = _commit(repo, "content.txt", "A\nb\nc\nd\nE\n", "解消")
    output = tmp_path / "resolved.json"
    assert _compare(repo, output, "rebase", (base, old_head, new_base, new_head), "--resolved-conflict", old_head) == 1
    assert not output.exists()
    assert "内容が変わっていません" in capsys.readouterr().err


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


@pytest.mark.parametrize("operation", ["rebase", "autosquash"])
def test_history_compare_accepts_blank_line_layout_change(repo: pathlib.Path, tmp_path: pathlib.Path, operation: str) -> None:
    """親による空行配置の差を含んでも、3-way適用後のtreeが同じなら受理する。"""
    base = _commit(repo, "content.txt", "first\nsecond\nthird\n", "準備")
    old_layout = "first\n\nsecond\n\nthird\n"
    parent_layout = "first\n\nsecond\nthird\n"
    if operation == "rebase":
        old_head = _commit(repo, "content.txt", old_layout, "空行を追加")
        _git(repo, "checkout", "-b", "rewritten", base)
        new_base = _commit(repo, "content.txt", parent_layout, "上流の空行")
        new_head = _commit(repo, "content.txt", old_layout, "空行を追加")
        expected = {old_head: new_head}
    else:
        padding = "anchor0\nanchor1\nanchor2\nanchor3\nanchor4\nanchor5\n"
        base = _commit(repo, "content.txt", "target-old\n" + padding + "first\nsecond\nthird\n", "同一ファイルの準備")
        target = _commit(repo, "content.txt", "TARGET\n" + padding + "first\nsecond\nthird\n", "対象")
        changed = _commit(repo, "content.txt", "TARGET\n" + padding + old_layout, "空行を追加")
        fixed_layout = "TARGET-FIXED\n" + padding + old_layout
        old_head = _commit(repo, "content.txt", fixed_layout, "fixup! 対象")
        _git(repo, "checkout", "-b", "rewritten", base)
        rewritten_target = _commit(repo, "content.txt", "TARGET-FIXED\n" + padding + parent_layout, "対象")
        new_head = _commit(repo, "content.txt", fixed_layout, "空行を追加")
        new_base = base
        expected = {target: rewritten_target, changed: new_head, old_head: rewritten_target}
    output = tmp_path / f"blank-{operation}.json"
    assert _compare(repo, output, operation, (base, old_head, new_base, new_head)) == 0
    assert json.loads(output.read_text(encoding="utf-8")) == expected


@pytest.mark.parametrize("changed", [False, True])
def test_rebase_compares_binary_payload(repo: pathlib.Path, tmp_path: pathlib.Path, changed: bool) -> None:
    """バイナリー同士の比較でも同じ内容だけを受理し、内容が異なれば対応表を保存しない。"""
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


def test_amend_mapping_flows_to_rewrite_consumers(
    repo: pathlib.Path, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """amendの旧新HEADを1件の対応表へ保存し、両記録のcommit標識だけを新OIDへ更新させ、裸の取得版は保つ。"""
    base = _git(repo, "rev-parse", "HEAD")
    old_head = _commit(repo, "first.txt", "一", "一つ目")
    (repo / "first.txt").write_text("一改訂", encoding="utf-8")
    _git(repo, "add", "--", "first.txt")
    _git(repo, "commit", "--amend", "--no-verify", "--no-edit")
    new_head = _git(repo, "rev-parse", "HEAD")
    mapping = tmp_path / "amend.json"
    assert _compare(repo, mapping, "amend", (base, old_head, base, new_head)) == 0
    assert json.loads(mapping.read_text(encoding="utf-8")) == {old_head: new_head}
    evidence_text = f"現行commit:{old_head} を確認。取得版 {old_head} の実行記録を適用"
    row = {"source": "原文", "outcome": "", "reviewed_head": "", "evidence": evidence_text}
    wi = "20261017-000000-001.md"
    pending = tmp_path / "pending.json"
    pending.write_text(
        json.dumps({"wi_conditions": [{"awi": wi, "condition": "保存する。", **row}], "user_requirements": []}),
        encoding="utf-8",
    )
    evidence = tmp_path / "evidence.json"
    reviewed = {**row, "awi": wi, "condition": "保存する。", "outcome": "達成", "reviewed_head": old_head}
    evidence.write_text(json.dumps({"wi_conditions": [reviewed], "user_requirements": []}), encoding="utf-8")
    for argv in (
        ["verification-record", "--", "--output", str(pending), "--rewrite-map", str(mapping)],
        ["exec-review-evidence-check", "--", str(evidence), "--rewrite-map", str(mapping)],
    ):
        with pytest.raises(SystemExit) as raised:
            atk.main(["run-script", *argv])
        assert raised.value.code == 0
    expected = f"現行commit:{new_head} を確認。取得版 {old_head} の実行記録を適用"
    for path in (pending, evidence):
        assert json.loads(path.read_text(encoding="utf-8"))["wi_conditions"][0]["evidence"] == expected
    assert json.loads(evidence.read_text(encoding="utf-8"))["wi_conditions"][0]["reviewed_head"] == old_head
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
