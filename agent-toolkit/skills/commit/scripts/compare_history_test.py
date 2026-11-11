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


def _rewrite_records(
    repo: pathlib.Path, previous: str, mapping: pathlib.Path, plan: pathlib.Path, *handoffs: pathlib.Path
) -> int:
    argv = [
        "run-script",
        "plan-rewrite",
        "--",
        "--worktree",
        str(repo),
        "--previous-head",
        previous,
        "--rewrite-map",
        str(mapping),
        "--completed-step",
        "履歴検収",
        "--result",
        "公開対応表を消費",
        "--plan",
        str(plan),
    ]
    for handoff in handoffs:
        argv.extend(("--handoff", str(handoff)))
    with pytest.raises(SystemExit) as ended:
        atk.main(argv)
    assert isinstance(ended.value.code, int)
    return ended.value.code


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


@pytest.mark.parametrize("order", ["same", "swapped"])
def test_rebase_maps_repeated_patch_only_in_same_order(repo: pathlib.Path, tmp_path: pathlib.Path, order: str) -> None:
    """導入・復元・再導入で同一変更が繰り返す系列は、並びが同じときだけ位置で対応付ける。"""
    # 基準にファイルを置き、導入と再導入を同じ変更内容（内容IDの重複）にする。
    base = _commit(repo, "content.txt", "基準\n", "基準の内容")
    first = _commit(repo, "content.txt", "導入\n", "導入")
    restored = _commit(repo, "content.txt", "基準\n", "対照へ復元")
    old_head = _commit(repo, "content.txt", "導入\n", "再導入")
    _git(repo, "checkout", "-b", "rebased", base)
    new_base = _commit(repo, "upstream.txt", "上流", "上流")
    if order == "same":
        _git(repo, "cherry-pick", first, restored, old_head)
    else:
        _commit(repo, "content.txt", "導入\n", "導入")
        _commit(repo, "other.txt", "別\n", "別の変更")
        _commit(repo, "content.txt", "基準\n", "対照へ復元")
    new_head = _git(repo, "rev-parse", "HEAD")
    output = tmp_path / "repeated.json"
    result = _compare(repo, output, "rebase", (base, old_head, new_base, new_head))
    if order == "same":
        assert result == 0
        new = _git(repo, "rev-list", "--reverse", f"{new_base}..{new_head}").splitlines()
        assert json.loads(output.read_text(encoding="utf-8")) == dict(zip((first, restored, old_head), new, strict=True))
    else:
        assert result != 0
        assert not output.exists()


def test_resolved_conflict_accepts_follow_up_of_new_upstream_file(
    repo: pathlib.Path, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """統合先で新しく入ったファイルへの追随を含む競合解消を受理し、増えたパスを成功出力へ示す。"""
    base = _commit(repo, "shared.txt", "共通\n", "準備")
    resolved = _commit(repo, "shared.txt", "レーン変更\n", "名称を改める")
    _git(repo, "checkout", "-b", "upstream", base)
    _commit(repo, "shared.txt", "上流変更\n", "上流の改訂")
    new_base = _commit(repo, "added_test.py", "旧名称\n", "上流の新規試験")
    (repo / "shared.txt").write_text("レーン変更と上流変更\n", encoding="utf-8")
    (repo / "added_test.py").write_text("新名称\n", encoding="utf-8")
    _git(repo, "add", "--", "shared.txt", "added_test.py")
    _git(repo, "commit", "--no-verify", "-m", "名称を改める")
    target = _git(repo, "rev-parse", "HEAD")
    output = tmp_path / "followed.json"
    capsys.readouterr()
    assert _compare(repo, output, "rebase", (base, resolved, new_base, target), "--resolved-conflict", resolved) == 0
    assert json.loads(output.read_text(encoding="utf-8")) == {resolved: target}
    reported = capsys.readouterr()
    assert f"{resolved} -> {target}: added_test.py" in reported.out + reported.err


@pytest.mark.parametrize("old_path_remains", [False, True])
def test_resolved_conflict_accepts_follow_up_of_upstream_rename(
    repo: pathlib.Path, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], old_path_remains: bool
) -> None:
    """統合先の改名へ追随した競合解消を受理して旧パスと新パスを示し、旧パスが統合先に残る場合は拒否する。"""
    base = _commit(repo, "shared.txt", "共通\n", "準備")
    resolved = _commit(repo, "shared.txt", "レーン変更\n", "名称を改める")
    _git(repo, "checkout", "-b", "upstream", base)
    _git(repo, "mv", "--", "shared.txt", "renamed.txt")
    if old_path_remains:
        (repo / "shared.txt").write_text("同名の別ファイル\n", encoding="utf-8")
        _git(repo, "add", "--", "shared.txt")
    _git(repo, "commit", "--no-verify", "-m", "上流の改名")
    new_base = _git(repo, "rev-parse", "HEAD")
    target = _commit(repo, "renamed.txt", "レーン変更\n", "名称を改める")
    output = tmp_path / "renamed.json"
    capsys.readouterr()
    result = _compare(repo, output, "rebase", (base, resolved, new_base, target), "--resolved-conflict", resolved)
    reported = capsys.readouterr()
    if old_path_remains:
        assert result != 0
        assert not output.exists()
        assert "次の操作:" in reported.err
    else:
        assert result == 0, reported.err
        assert json.loads(output.read_text(encoding="utf-8")) == {resolved: target}
        assert f"{resolved} -> {target}: renamed.txt" in reported.out + reported.err
        assert f"統合先に無い旧パスを外した対応: {resolved} -> {target}: shared.txt" in reported.out + reported.err


@pytest.mark.parametrize("variant", ["conflict", "reordered", "omitted"])
def test_rebase_repeated_patch_boundaries_and_rejection_keeps_output(
    repo: pathlib.Path, tmp_path: pathlib.Path, variant: str
) -> None:
    """繰り返し系列は競合解消を含めて位置で検収し、並びの崩れと曖昧な省略は既存の出力を変えずに拒否する。"""
    # 基準にファイルを置き、導入と再導入を同じ変更内容（内容IDの重複）にする。
    base = _commit(repo, "content.txt", "基準\n", "基準の内容")
    first = _commit(repo, "content.txt", "導入\n", "導入")
    restored = _commit(repo, "content.txt", "基準\n", "対照へ復元")
    again = _commit(repo, "content.txt", "導入\n", "再導入")
    old_head = _commit(repo, "other.txt", "別\n", "別の変更")
    _git(repo, "checkout", "-b", "rebased", base)
    new_base = _commit(repo, "upstream.txt", "上流", "上流")
    if variant == "conflict":
        _git(repo, "cherry-pick", first, restored)
        _commit(repo, "content.txt", "導入（解消）\n", "再導入")
        _git(repo, "cherry-pick", old_head)
    elif variant == "reordered":
        # 内容の集合は同じで、別の変更と再導入の順序だけを入れ替えた系列。
        _git(repo, "cherry-pick", first, restored, old_head, again)
    else:
        # 再導入が統合先へ省略された系列。どちらの導入が省略されたかを決められない。
        _git(repo, "cherry-pick", first, restored, old_head)
    new_head = _git(repo, "rev-parse", "HEAD")
    output = tmp_path / "repeated.json"
    output.write_text("保存済みの対応表\n", encoding="utf-8")
    extra = ("--resolved-conflict", again) if variant == "conflict" else ()
    result = _compare(repo, output, "rebase", (base, old_head, new_base, new_head), *extra)
    if variant == "conflict":
        assert result == 0
        new = _git(repo, "rev-list", "--reverse", f"{new_base}..{new_head}").splitlines()
        assert json.loads(output.read_text(encoding="utf-8")) == dict(zip((first, restored, again, old_head), new, strict=True))
    else:
        assert result == 1
        assert output.read_text(encoding="utf-8") == "保存済みの対応表\n"


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


# 除外するパスと、Gitのパターンとして展開すると同じ指定へ一致してしまう類似名のパス。
_EXCLUDED_PATH = "data [ab].txt"
_SIMILAR_PATH = "data a.txt"


def _commit_files(repo: pathlib.Path, files: dict[str, str], subject: str) -> str:
    for filename, text in files.items():
        (repo / filename).write_text(text, encoding="utf-8")
    _git(repo, "add", "--", *files)
    _git(repo, "commit", "--no-verify", "-m", subject)
    return _git(repo, "rev-parse", "HEAD")


def _separated_series(repo: pathlib.Path, violation: str) -> tuple[tuple[str, str, str, str], str, str]:
    """複数パスを変更する旧commitから1パスだけを外した新系列を作り、比較の端点・旧commit・対応先を返す。"""
    base = _git(repo, "rev-parse", "HEAD")
    first = _commit_files(repo, {"keep.txt": "保持\n", _EXCLUDED_PATH: "除外\n", _SIMILAR_PATH: "類似名\n"}, "一つ目")
    old_head = _commit(repo, "second.txt", "通常変更", "二つ目")
    _git(repo, "checkout", "-b", "separated", base)
    new_base = _commit(repo, "upstream.txt", "上流", "上流")
    if violation == "order":
        _commit(repo, "second.txt", "通常変更", "二つ目")
    files = {"keep.txt": "無断変更\n" if violation in {"content", "followed"} else "保持\n", _SIMILAR_PATH: "類似名\n"}
    if violation == "still-changed":
        files[_EXCLUDED_PATH] = "除外\n"
    elif violation == "followed":
        # 統合先のファイルへの追随で、旧commitに無い変更パスを増やす。
        files["upstream.txt"] = "追随\n"
    target = _commit_files(repo, files, "別件" if violation == "subject" else "一つ目")
    if violation != "order":
        _commit(repo, "second.txt", "通常変更", "二つ目")
    return (base, old_head, new_base, _git(repo, "rev-parse", "HEAD")), first, target


@pytest.mark.parametrize(
    ("violation", "expected"),
    [
        ("none", ""),
        ("outside", "旧系列"),
        ("unchanged-path", "変更していないパス"),
        ("pattern", "変更していないパス"),
        ("all-paths", "全ての変更パス"),
        ("still-changed", "新commitでも変更"),
        ("content", "変更内容が対応しない旧commit"),
        ("followed", "除外後の変更パス"),
        ("subject", "件名"),
        ("order", "相対順"),
    ],
)
def test_exclude_path_accepts_only_explicit_literal_exclusion(
    repo: pathlib.Path, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], violation: str, expected: str
) -> None:
    """指定した旧commitのパスだけを外して残る変更の一致を確認し、誤指定・欠落・内容差・件名や相対順の不一致を保存せず拒否する。"""
    bases, first, target = _separated_series(repo, violation)
    output = tmp_path / "excluded.json"
    # 除外を指定しない場合のパスの減少は、意図しない欠落と区別できないため受理しない。
    # 新commitが全パスを変更する系列だけは、指定なしで全変更が対応する。
    if violation == "still-changed":
        assert _compare(repo, output, "rebase", bases) == 0
        output.unlink()
    else:
        assert _compare(repo, output, "rebase", bases) == 1
        assert not output.exists()
    exclusions = ["--exclude-path", bases[0] if violation == "outside" else first[:12], _EXCLUDED_PATH]
    if violation == "unchanged-path":
        exclusions += ["--exclude-path", first, "second.txt"]
    elif violation == "pattern":
        exclusions += ["--exclude-path", first, "data*"]
    elif violation == "all-paths":
        exclusions += ["--exclude-path", first, "keep.txt", "--exclude-path", first, _SIMILAR_PATH]
    elif violation == "followed":
        # 競合解消の明示は残るパスの内容差だけを認め、除外指定のある対応では変更パスの増加を認めない。
        exclusions += ["--resolved-conflict", first]
    capsys.readouterr()
    result = _compare(repo, output, "rebase", bases, *exclusions)
    reported = capsys.readouterr()
    if violation == "none":
        assert result == 0, reported.err
        assert json.loads(output.read_text(encoding="utf-8")) == {first: target, bases[1]: bases[3]}
        assert f"{first} -> {target}: {_EXCLUDED_PATH}" in reported.out + reported.err
        return
    assert result == 1
    assert not output.exists()
    assert expected in reported.err and "次の操作:" in reported.err
    if violation == "content":
        # 残る内容の変更は除外指定だけでは受理せず、競合解消の明示を併用した場合に限り検収する。
        assert _compare(repo, output, "rebase", bases, *exclusions, "--resolved-conflict", first) == 0
        assert json.loads(output.read_text(encoding="utf-8")) == {first: target, bases[1]: bases[3]}


def test_exclude_path_is_rejected_for_other_operations(
    repo: pathlib.Path, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """除外指定はrebaseの検収だけが受け付ける。"""
    base = _git(repo, "rev-parse", "HEAD")
    old_head = _commit(repo, "first.txt", "一", "一つ目")
    output = tmp_path / "amend.json"
    assert _compare(repo, output, "amend", (base, old_head, base, old_head), "--exclude-path", old_head, "first.txt") == 1
    assert not output.exists()
    assert "--exclude-path" in capsys.readouterr().err


def test_excluded_mapping_is_consumed_by_public_record_command(
    repo: pathlib.Path, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """除外指定で保存した対応表を加工せずに公開の記録更新へ渡し、WI実装commitの対応を分離後のcommitへ継承する。"""
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(tmp_path / "private-notes"))
    wi = "20261009-043608-001.md"
    plan = tmp_path / "09-excluded-1a2b.md"
    plan.write_text(
        "# 計画\n\n## 概要\n\n### 計画メタ情報\n\n- 関連WI:\n"
        f"  - {wi}: 対応\n\n## 進捗ログ\n\n| 日時 | 完了した工程 | 結果・特記事項 |\n"
        "| --- | --- | --- |\n",
        encoding="utf-8",
    )
    bases, first, target = _separated_series(repo, "none")
    commit_mapping.append_event(plan, {"commit": first, "awi": [wi]})
    mapping = tmp_path / "excluded-map.json"
    assert _compare(repo, mapping, "rebase", bases, "--exclude-path", first, _EXCLUDED_PATH) == 0
    before = mapping.read_bytes()
    assert _rewrite_records(repo, bases[1], mapping, plan) == 0
    events = commit_mapping.read_events(plan, plan.read_text(encoding="utf-8"))
    assert commit_mapping.get_commits(repo, events, [wi], {wi}) == {wi: [_git(repo, "rev-parse", "--short", target)]}
    assert mapping.read_bytes() == before
    capsys.readouterr()
