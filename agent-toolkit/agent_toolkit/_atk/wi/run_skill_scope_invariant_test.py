"""一般UWIの保存・取込scopeが、起動目的と文書の起動単位へ一致することを検査する。"""

import json
import pathlib
import subprocess

import pytest

from agent_toolkit._atk.run_skill_test import _engine_env, _records, _run, _startup_input
from agent_toolkit._atk.wi import entries, frontmatter
from agent_toolkit._atk.wi.uwi_test import _invoke_uwi_add, _make_uwi_add_fake, _setup_notes
from agent_toolkit._common import session_review, skill_modes

# pytestがモジュールの属性から取得する共有fixtureを公開する。
__all__ = ["_engine_env"]


@pytest.fixture(name="skill_invocation")
def _skill_invocation(engine_env, tmp_path, monkeypatch, capsys):
    """公開起動から実ホストへ渡すargvとSessionStartの実行データを取得する。"""

    def launch(skill, arguments, expected_scope):
        if skill == "agent-toolkit:review-session" and arguments and arguments.split()[0] == "batch":
            transcript = tmp_path / "target.jsonl"
            transcript.write_text(
                json.dumps({"type": "user", "message": {"role": "user", "content": "要求"}}) + "\n",
                encoding="utf-8",
            )
            session_review.record(
                {
                    "hook_event_name": "SessionStart",
                    "session_id": "target",
                    "transcript_path": str(transcript),
                    "cwd": str(engine_env.repo),
                },
                host="claude",
                target_repo="github.com/example/myrepo",
            )
        options = ["--target-repo", str(engine_env.repo)]
        if arguments is not None:
            options.extend(["--args", arguments])
        code, _out, error = _run([*options, skill], capsys)
        assert code == 0, error
        record = _records(engine_env, "session")[0]
        assert record["argv"][-1] == (
            f'/goal <atk-auto source="run-skill" kind="goal">\nこのセッションが担当するスキル`{expected_scope}`の工程を完遂する'
            "（起動元が終了後に行う検収と確定は含まない）\n</atk-auto>"
        )
        return _startup_input(record, monkeypatch, capsys)

    return launch


@pytest.mark.parametrize(
    "skill,arguments,other_arguments,mode",
    [
        ("agent-toolkit:review-session", "focus.md", "batch auto", None),
        ("agent-toolkit:review-session", "batch auto", "focus.md", "batch"),
        ("agent-toolkit:process-wi", "focus.md", "single-lane 追加", None),
        ("agent-toolkit:process-wi", "single-lane 追加", "all-repos", "single-lane"),
        ("agent-toolkit:process-wi", "all-repos", "single-lane 追加", "all-repos"),
    ],
)
def test_general_run_skill_uwi_uses_documented_scope_for_the_same_mode(
    tmp_path, monkeypatch, skill_invocation, skill, arguments, other_arguments, mode
):
    """文書のscope例を一般UWIへ保存し、同じモードの次回だけが取り込む境界を検査する。"""
    expected_scope = skill + (" " + mode if mode else "")
    data = skill_invocation(skill, arguments, expected_scope)
    assert data["skill"] == skill and data["mode"] == mode
    document = pathlib.Path(__file__).resolve().parents[3] / "skills/wi-standards/references/origin-and-approval.md"
    rows = [
        [cell.strip().strip("`") for cell in line.strip("|").split("|")]
        for line in document.read_text(encoding="utf-8").splitlines()
        if line.startswith("| `agent-toolkit:")
    ]
    scope = next(row[2] for row in rows if row[:2] == [data["skill"], data["mode"] or "なし"])
    assert scope == expected_scope and scope in data["accepted_scopes"]
    notes = _setup_notes(tmp_path)
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
    repository = tmp_path / "myrepo"
    repository.mkdir()
    monkeypatch.setattr(subprocess, "run", _make_uwi_add_fake(repository))
    assert (
        _invoke_uwi_add(
            tmp_path, str(repository), "--source", "run-skill", "--scope", scope, body="この操作の事前承認を求める。"
        ).code
        == 0
    )
    selected = entries.select_entries(
        notes, status="active", target_repo="github.com/example/myrepo", entry_type="uwi", answered="all", source="run-skill"
    )
    assert len(selected) == 1
    path, _repo, text, _state, _kind = selected[0]
    parsed = frontmatter.parse_frontmatter(text)
    assert parsed is not None
    saved = parsed[0]
    assert saved["scope"] == skill_modes.invocation_scope(skill, arguments)
    assert saved["source"] == "run-skill" and "run_skill_report" not in saved
    assert saved["question_type"] == "yes-no"
    assert saved["scope"] == skill_modes.invocation_scope(skill, arguments.split()[0] + " 次回の自由記述")
    assert saved["scope"] != skill_modes.invocation_scope(skill, other_arguments)
    assert (saved["scope"] == skill_modes.invocation_scope(skill, None)) is (mode is None)
    assert path.read_text(encoding="utf-8") == text


@pytest.mark.parametrize("arguments", [None, "batch auto"])
def test_renamed_review_imports_saved_scopes_without_rewriting(tmp_path, monkeypatch, skill_invocation, arguments):
    """公開起動が配送した取込対象を、保存済み3形式のUWIに非破壊で適用する。"""
    expected_scope = "agent-toolkit:review-session" + (" batch" if arguments else "")
    data = skill_invocation("agent-toolkit:review-session", arguments, expected_scope)
    notes = _setup_notes(tmp_path)
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
    repository = tmp_path / "myrepo"
    repository.mkdir()
    monkeypatch.setattr(subprocess, "run", _make_uwi_add_fake(repository))
    scopes = (
        "agent-toolkit:session-review",
        "agent-toolkit:session-review batch",
        "agent-toolkit:session-review-batch",
    )
    for scope in scopes:
        assert (
            _invoke_uwi_add(
                tmp_path, str(repository), "--source", "run-skill", "--scope", scope, body="この操作の事前承認を求める。"
            ).code
            == 0
        )
    selected = entries.select_entries(
        notes, status="active", target_repo="github.com/example/myrepo", entry_type="uwi", answered="all", source="run-skill"
    )
    accepted = data["accepted_scopes"]
    assert accepted == [expected_scope, *list(scopes[1:] if arguments else scopes[:1])]
    imported = []
    for path, _repo, text, _state, _kind in selected:
        parsed = frontmatter.parse_frontmatter(text)
        assert parsed is not None
        if parsed[0]["scope"] in accepted:
            imported.append(parsed[0]["scope"])
        assert path.read_text(encoding="utf-8") == text
    assert set(imported) == set(scopes[1:] if arguments else scopes[:1])
