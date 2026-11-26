# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。

"""`atk wi add`・`atk wi edit`が示す先送りの候補（`情報:`の行）の試験。

候補の行は、過去に投入前レビューを合格したAWIの本文から抜き出した実例（先送り14行、処理へ残してよい記述4行）で組み立てる。
"""

import pathlib
import subprocess

import pytest

from agent_toolkit import atk
from agent_toolkit._atk.wi import add_reports
from agent_toolkit._testing.wi_add_support import (
    _cmd_add_args,
    _patch_cmd_add_operations,
    _run_public_add,
    _write_origin_review,
)
from agent_toolkit._testing.wi_add_support import _isolate_managed_temp as _isolate_managed_temp
from agent_toolkit._testing.wi_bodies import AGENT_AWI_BODY
from agent_toolkit._testing.wi_mutations_content_support import _edit_body_args
from agent_toolkit.atk_test import _FIXED_DT, _make_subprocess_fake, _setup_notes, _write_awi_file

_DEFERRED = [
    "- MR の宛先ブランチを条件にしているジョブの rules を確かめて加える。",
    "- 手順書の置き場所は docs/dev/CI-CD運用.md か .claude/skills/mr とし、どちらに置くかは計画で決める。",
    "- ユーザーは「適宜？定期的に？」と迷っているので、比較して推奨を決める。",
    "- 競合の解消、検査、レビューの扱いを決める。",
    "- 次の候補を比べて採否を決める。ほかの案があれば加える。",
    "- 次期版で直しても、develop 側の修正と重なって取り込みが競合しないよう、扱いを決める。",
    "- 起草時の見込み（比較して採否を決める）: 切替の後は、先行した仕組みのコードを next の所有とする。",
    "- 反映先は手順書と規範の2か所とし、どちらに置くかと重複の整理は計画で決める。",
    "- 開発機能一覧のExcelの対応行は、着手時に本環境の照合で特定し、本項目の完了報告に書く。",
    "- 検索の対象に引数を含めるか、切り詰める長さは実装で決め、テストで固定する。",
    "- 候補は28030と28031で、空いていることを実機で確かめてから確定する。",
    "- 候補は28032と28033で、空いていることを実機で確かめてから確定する。",
    "- 振り分けの置き場所は計画工程で選ぶ。",
    "- テキストとして直接返すかは計画工程で選ぶ。",
]
_ALLOWED = [
    "- リリースMRがマージされ、本番配備が成功したことを確認して着手する。",
    "- 先行するリリースMRがマージされ、本番配備が成功したことを確認して着手する。",
    "- 3件目のリリースMRがマージされ、本番配備が成功したことを確認して着手する。",
    "- 基準を手順書に書き、AWIの起草・計画で宛先の系統を決める時に読める位置へ置く。",
]
_MATCHED = _DEFERRED + _ALLOWED


def _draft() -> str:
    """一致する18行を反映内容と完成条件へ分けて置き、対象外の節にも同じ言い回しを置いた原稿。"""
    return "\n".join(
        [
            "# 先送りの候補の試験",
            "",
            "冒頭でも計画で決めると書くが、H1直下は対象にしない。",
            "",
            "## 反映内容と反映先",
            "",
            *_MATCHED[:9],
            "- 一致しない行。",
            "",
            "## 調査結果と判断根拠",
            "",
            "- 実機で確かめてから確定する、という記述は観測の記録であり対象にしない。",
            "",
            "## 完成条件",
            "",
            *_MATCHED[9:],
            "",
            "```text",
            "## 反映内容と反映先",
            "```",
            "",
            "## ユーザー指摘の逐語引用",
            "",
            "> 計画で決めずに今比較して結論を出して",
            "",
        ]
    )


def _expected_numbers(text: str) -> list[int]:
    lines = text.split("\n")
    return [lines.index(line) + 1 for line in _MATCHED]


def _info_lines(output: str) -> list[str]:
    return [line for line in output.splitlines() if line.startswith("情報: ")]


def _assert_lists_all(output: str, text: str) -> None:
    candidates = [line for line in _info_lines(output) if line.startswith("情報: 先送りの候補 ")]
    expected = [
        f"情報: 先送りの候補 {number}行目: {line}" for number, line in zip(_expected_numbers(text), _MATCHED, strict=True)
    ]
    assert candidates == expected
    assert len(_info_lines(output)) == len(_MATCHED) + 1


def test_candidates_cover_only_target_sections() -> None:
    text = _draft()

    candidates = add_reports.deferral_candidates(text)

    assert candidates == list(zip(_expected_numbers(text), _MATCHED, strict=True))


def test_candidate_line_numbers_count_frontmatter() -> None:
    text = "---\ntarget_repo: github.com/example/repo\n---\n" + _draft()

    candidates = add_reports.deferral_candidates(text)

    assert [number for number, _line in candidates] == _expected_numbers(text)


@pytest.mark.parametrize("dry_run", [True, False])
def test_add_lists_candidates_without_changing_result(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    dry_run: bool,
) -> None:
    notes = _setup_notes(tmp_path)
    _patch_cmd_add_operations(monkeypatch)
    text = _draft()

    args = _cmd_add_args(tmp_path, text, source="test", dry_run=dry_run)
    review = _write_origin_review(tmp_path, text, "計画で決めずに今比較して結論を出して")
    args.origin_review = [review]
    _run_public_add(args, notes, _FIXED_DT, tmp_path)

    output = capsys.readouterr()
    _assert_lists_all(output.out, text)
    assert not [line for line in (output.out + output.err).splitlines() if line.startswith(("警告: ", "失敗: "))]
    saved = list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))
    assert len(saved) == (0 if dry_run else 1)


@pytest.mark.parametrize("dry_run", [True, False])
def test_add_without_candidates_prints_no_info(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    dry_run: bool,
) -> None:
    notes = _setup_notes(tmp_path)
    _patch_cmd_add_operations(monkeypatch)

    _run_public_add(_cmd_add_args(tmp_path, AGENT_AWI_BODY, source="test", dry_run=dry_run), notes, _FIXED_DT, tmp_path)

    assert _info_lines(capsys.readouterr().out) == []


def test_add_uwi_prints_no_info(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    notes = _setup_notes(tmp_path)
    _patch_cmd_add_operations(monkeypatch)

    _run_public_add(
        _cmd_add_args(tmp_path, "計画で決めますか？\n\n## 完成条件\n計画で決めますか？", entry_type="uwi", dry_run=True),
        notes,
        _FIXED_DT,
        tmp_path,
    )

    assert _info_lines(capsys.readouterr().out) == []


@pytest.mark.parametrize("dry_run", [True, False])
def test_edit_lists_candidates_without_changing_result(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    dry_run: bool,
) -> None:
    notes = _setup_notes(tmp_path)
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    path = _write_awi_file(notes, "deferral.md", body=AGENT_AWI_BODY)
    text = _draft()

    review = _write_origin_review(tmp_path, text, "計画で決めずに今比較して結論を出して")
    args = [*_edit_body_args(tmp_path, path.name, text), "--origin-review", review]
    with pytest.raises(SystemExit) as finished:
        atk.main([*args, *(["--dry-run"] if dry_run else [])], home=tmp_path)

    output = capsys.readouterr()
    assert finished.value.code == 0, output.err
    _assert_lists_all(output.out, text)
    assert not [line for line in (output.out + output.err).splitlines() if line.startswith(("警告: ", "失敗: "))]
    assert ("先送りの候補の試験" in path.read_text(encoding="utf-8")) is not dry_run


@pytest.mark.parametrize(("agent_environment", "heading"), [(False, ""), (True, ""), (True, "## 完成条件\n\n")])
def test_append_reports_only_new_candidates_in_target_sections(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    agent_environment: bool,
    heading: str,
) -> None:
    notes = _setup_notes(tmp_path)
    existing = "- 手順は計画で決める。"
    added = "- 反映先は計画で決める。"
    path = _write_awi_file(notes, "sample.md", body=f"# AWI\n\n## 完成条件\n\n{existing}")
    for name in ("AI_AGENT", "CODEX_CI", "CLAUDECODE", "CURSOR_AGENT"):
        monkeypatch.delenv(name, raising=False)
    if agent_environment:
        monkeypatch.setenv("AI_AGENT", "1")
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    args = _edit_body_args(tmp_path, path.name, heading + added, append=True)

    with pytest.raises(SystemExit) as finished:
        atk.main([*args, "--target-repo=github.com/example/foo"], home=tmp_path)

    output = capsys.readouterr()
    assert finished.value.code == 0, output.err
    saved = path.read_text(encoding="utf-8")
    assert added in saved
    assert existing not in output.out
    if agent_environment and not heading:
        assert _info_lines(output.out) == []
    else:
        line = saved.splitlines().index(added) + 1
        assert any(f"{line}行目: {added}" in info for info in _info_lines(output.out))
