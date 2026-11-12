"""lane adoptionの契約を検証する。"""

from __future__ import annotations

import datetime
import json
import pathlib
import sys
from collections.abc import Callable
from typing import Any

import pytest

from agent_toolkit._atk import lane
from agent_toolkit._plan import commit_mapping
from agent_toolkit._testing import git_repository, wi_entry_files
from agent_toolkit._testing.lane_integration_support import (
    _adopt_argv,
    _integration_args,
    _integration_selection,
    _rebased_integration,
    _record_wi_commit,
)
from agent_toolkit._testing.lane_support import _create, _invoke


@pytest.mark.parametrize("placeholder", [False, True], ids=["literal", "placeholder"])
@pytest.mark.parametrize("expanded_note", [False, True], ids=["literal-note", "expanded-note"])
def test_integrate_adopts_same_commit_with_literal_or_placeholder(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    placeholder: bool,
    expanded_note: bool,
    record_property: Callable[[str, object], None],
) -> None:
    """commitとnote-fileの参照を組み合わせ、実adoptへ渡すcommitと採用本文を確かめる。"""
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2026-11-09T00:00:00+00:00")
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-11-09T00:00:00+00:00")
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    _integration_selection(selected, monkeypatch, repo)
    worktree = pathlib.Path(record["worktree"])
    (worktree / "feature").write_text("採用する成果", encoding="utf-8")
    head = git_repository.commit_all(worktree, "成果")
    short = git_repository.git_output(worktree, "rev-parse", "--short=7", head)
    name = "20261023-000000-001.md"
    notes = git_repository.init_repository(tmp_path / "notes", commit_message="採用前のWI")
    handoff = tmp_path / "handoff.md"
    handoff.write_text("# 引き継ぎ\n", encoding="utf-8")
    _record_wi_commit(
        capsys,
        handoff,
        worktree,
        name,
        head,
        record["initial_head"],
        step="実装",
        result="現在HEADをWIへ記録",
    )
    commit = "{{integrated_head_short}}" if placeholder else short
    note = tmp_path / "adopt-note.md"
    note_text = (
        "採用commit: {{integrated_head_short}}\n完了時刻: {{completed_at}}\n" if expanded_note else f"採用commit: {short}\n"
    )
    note.write_text(note_text, encoding="utf-8")
    note_argument = "{{expand-file:" + str(note) + "}}" if expanded_note else str(note)
    manifest = tmp_path / "integration.json"
    manifest.write_text(
        json.dumps(
            {
                "plans": [],
                "handoff": str(handoff),
                "verification": [],
                "lane_started_at": (datetime.datetime.now().astimezone() - datetime.timedelta(seconds=1)).isoformat(),
                "wis": [
                    {
                        "name": name,
                        "commit_record": str(handoff),
                        "commands": [_adopt_argv(repo, name, [commit], note_file=note_argument)],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    arguments = _integration_args(record, repo, manifest, head)
    before_dirty = git_repository.git_output(worktree, "status", "--porcelain=v1")
    before_ff: dict[str, Any] = {}
    git_run = lane.git_command.run

    def check_before_ff(arguments: list[str], cwd: pathlib.Path, *extra: Any, **kwargs: Any) -> Any:
        if arguments[:2] == ["merge", "--ff-only"] and cwd == repo:
            # 実Gitへ処理を渡す直前に、複製や最終化で確定する値を事前解析が生成していないことを確かめる。
            before_ff["note_files"] = sorted(str(path) for path in tmp_path.rglob("*adopt-note.md"))
            stored = json.loads(pathlib.Path(record["record_path"]).read_text(encoding="utf-8"))
            before_ff["finalization_values"] = stored["integration"].get("finalization_values")
            assert before_ff["note_files"] == [str(note)]
            assert before_ff["finalization_values"] is None
        return git_run(arguments, cwd, *extra, **kwargs)

    monkeypatch.setattr(lane.git_command, "run", check_before_ff)
    code, final, stderr = _invoke(capsys, *arguments)
    assert code == 0 and final["phase"] == "finalized", stderr
    assert before_ff
    assert final["finalization_values"]["integrated_head_short"] == short
    assert git_repository.git_output(repo, "rev-parse", "HEAD") == head
    adopted = wi_entry_files.entry_path(notes, name, state="adopted").read_text(encoding="utf-8")
    assert short in adopted and "{{integrated_head_short}}" not in adopted
    assert f"採用commit: {short}" in adopted
    if expanded_note:
        assert final["finalization_values"]["completed_at"] in adopted and "{{completed_at}}" not in adopted
    result = final["results"][f"wis:{name}:0"]
    actual = result["argv"]
    assert actual[actual.index("--commit") + 1] == short
    actual_note = pathlib.Path(actual[actual.index("--note-file") + 1])
    assert actual_note.is_absolute() and actual_note.is_file()
    assert (actual_note != note) is expanded_note
    assert note.read_text(encoding="utf-8") == note_text
    after_dirty = git_repository.git_output(worktree, "status", "--porcelain=v1")
    assert not before_dirty and not after_dirty
    record_property(
        "adopt_evidence",
        json.dumps(
            {
                "integrate_argv": ["atk", "lane", *arguments],
                "input_commit": commit,
                "before_ff": before_ff,
                "input_note_file": note_argument,
                "actual_note_file": str(actual_note),
                "actual_note_body": actual_note.read_text(encoding="utf-8"),
                "commit_record": commit_mapping.mapping_path(handoff).read_text(encoding="utf-8"),
                "head": head,
                "short": short,
                "actual_adopt_argv": actual,
                "before_dirty": before_dirty,
                "after_dirty": after_dirty,
                "adopted_body": adopted,
                "exit_code": code,
            },
            ensure_ascii=False,
        ),
    )


@pytest.mark.parametrize(
    "case",
    [
        "correct",
        "planned-correct",
        "foreign-repository",
        "omitted-correction",
        "stale",
        "missing",
        "no-implementation",
        "unexpected",
        "no-source",
    ],
)
def test_integrate_validates_adopt_commits_before_git_and_finalization(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], case: str
) -> None:
    """adoptのcommitが統合HEADの祖先で、対応記録の集合と一致することを確かめ、不一致ならGit統合と終了argvを実行しない。"""
    repo, record, manifest, value, original = _rebased_integration(tmp_path, monkeypatch, capsys)
    worktree = pathlib.Path(record["worktree"])
    name = "20261023-000000-001.md"
    handoff = pathlib.Path(value["handoff"])
    expected = [] if case in {"no-implementation", "unexpected"} else [original["new_head"]]
    head = original["new_head"]
    if case in {"correct", "planned-correct", "omitted-correction"}:
        (worktree / "correction").write_text("是正", encoding="utf-8")
        head = git_repository.commit_all(worktree, "是正")
        expected.append(head)
    moved = tmp_path / "saved-plan.md"
    if case == "planned-correct":
        handoff = tmp_path / "plan.md"
        handoff.write_text("# 計画\n\n## 概要\n\n### 計画メタ情報\n\n- 関連WI:\n  - " + name + ": 対応\n", encoding="utf-8")
        value["plans"] = [
            {
                "name": handoff.name,
                "commands": [
                    [
                        sys.executable,
                        "-c",
                        "import pathlib,sys;pathlib.Path(sys.argv[1]).rename(sys.argv[2])",
                        str(handoff),
                        str(moved),
                    ]
                ],
            }
        ]
    if expected:
        commit_mapping.append_event(handoff, {"commits": expected, "awi": [name]})
    if case == "foreign-repository":
        foreign = git_repository.init_repository(tmp_path / "foreign")
        (foreign / "other-feature").write_text("別リポジトリの成果", encoding="utf-8")
        foreign_head = git_repository.commit_all(foreign, "別リポジトリの実装")
        foreign_short = git_repository.git_output(foreign, "rev-parse", "--short=7", foreign_head)
        commit_mapping.append_event(handoff, {"commit": foreign_short, "awi": ["20261023-000000-002.md"]})
    supplied = expected or [original["new_head"]]
    if case in {"missing", "no-implementation"}:
        supplied = []
    elif case == "stale":
        supplied = [original["old_head"]]
    elif case == "omitted-correction":
        supplied = [original["new_head"]]
    marker = tmp_path / "finalized"
    argv = lane._atk_script(  # pylint: disable=protected-access
        "wi", "adopt", name.removesuffix(".md"), *("--commit=" + oid[:10] for oid in supplied)
    )
    row: dict[str, Any] = {"name": name, "commands": [argv], "no_merge": not expected}
    if case != "no-source":
        row["commit_record"] = str(handoff)
    value["wis"] = [row]
    manifest.write_text(json.dumps(value), encoding="utf-8")
    execute = lane.run_command.execute

    def finalization(*args: Any, **kwargs: Any) -> Any:
        # WI管理repoへの状態遷移だけを隔離し、実Git統合と対応記録・公開adoptの引数解析は実物を使う。
        if args[0] == argv:
            return execute(
                [sys.executable, "-c", "import pathlib,sys;pathlib.Path(sys.argv[1]).touch()", str(marker)],
                *args[1:],
                **kwargs,
            )
        return execute(*args, **kwargs)

    monkeypatch.setattr(lane.run_command, "execute", finalization)
    args = _integration_args(record, repo, manifest, head if head != original["new_head"] else original["old_head"])
    code, pending, stderr = _invoke(capsys, *args, "--restart-review")
    assert code == 1, stderr
    code, final, stderr = _invoke(capsys, *args, "--accept-rebased-head", pending["new_head"])
    accepted = case in {"correct", "planned-correct", "foreign-repository", "no-implementation"}
    assert code == (0 if accepted else 2), stderr
    assert marker.exists() is accepted
    assert git_repository.git_output(repo, "rev-parse", "HEAD") == (head if accepted else original["new_base"])
    assert moved.is_file() is (case == "planned-correct")
    if accepted:
        assert final["phase"] == "finalized"
    else:
        assert name in stderr and "plan-commits" in stderr
