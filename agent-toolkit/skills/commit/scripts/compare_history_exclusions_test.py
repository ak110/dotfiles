# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""履歴比較の明示的なパス除外を検証する。"""

import json
import pathlib

import pytest

from agent_toolkit._plan import commit_mapping
from agent_toolkit._testing.history_compare_support import _commit, _commit_files, _compare, _git, _rewrite_records
from agent_toolkit._testing.history_compare_support import _repo as _repo

# 除外するパスと、Gitのパターンとして展開すると同じ指定へ一致してしまう類似名のパス。
_EXCLUDED_PATH = "data [ab].txt"


_SIMILAR_PATH = "data a.txt"


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
