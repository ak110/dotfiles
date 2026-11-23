# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。

import datetime
import json
import pathlib
import subprocess
from unittest import mock

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import config
from agent_toolkit._atk.wi import frontmatter, listing
from agent_toolkit._atk.wi.layout import entry_path
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.process_loop_support import set_process_loop_repositories
from agent_toolkit._testing.wi_listing_support import _clear_agent_environment as _clear_agent_environment
from agent_toolkit.atk_test import _FIXED_TIMESTAMP, _make_subprocess_fake, _setup_notes, _write_awi_file, _write_uwi_file


@pytest.mark.parametrize(
    ("important", "expected"),
    [("all", {"important.md", "normal.md"}), ("yes", {"important.md"}), ("no", {"normal.md"})],
)
def test_list_filters_important_entries(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    important: str,
    expected: set[str],
) -> None:
    notes = _setup_notes(tmp_path)
    marked = _write_awi_file(notes, "important.md")
    marked.write_text(marked.read_text(encoding="utf-8").replace("type: awi", "important: true\ntype: awi"), encoding="utf-8")
    _write_awi_file(notes, "normal.md")
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    with pytest.raises(SystemExit) as result:
        atk.main(["wi", "list", "--jsonl", "--skip-pull", f"--important={important}"], home=tmp_path)
    assert result.value.code == 0
    assert {json.loads(line)["filename"] for line in capsys.readouterr().out.splitlines()} == expected


@pytest.mark.parametrize(
    ("user_comment", "expected"),
    [
        ("all", {"commented.md", "plain.md", "fenced.md", "empty.md"}),
        ("yes", {"commented.md"}),
        ("no", {"plain.md", "fenced.md", "empty.md"}),
    ],
)
def test_list_filters_entries_with_user_comment(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    user_comment: str,
    expected: set[str],
) -> None:
    notes = _setup_notes(tmp_path)
    _write_awi_file(notes, "commented.md", body="本文\n\n## ユーザーコメント\n\n指摘")
    _write_awi_file(notes, "plain.md", body="本文")
    _write_awi_file(notes, "fenced.md", body="本文\n\n```text\n## ユーザーコメント\n指摘\n```")
    _write_awi_file(notes, "empty.md", body="本文\n\n## ユーザーコメント\n")
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    with pytest.raises(SystemExit) as result:
        atk.main(["wi", "list", "--jsonl", "--skip-pull", f"--user-comment={user_comment}"], home=tmp_path)
    assert result.value.code == 0
    assert {json.loads(line)["filename"] for line in capsys.readouterr().out.splitlines()} == expected


def test_candidate_records_match_filtered_candidates(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    notes = _setup_notes(tmp_path)
    repo = git_repository.init_repository(tmp_path / "repo", origin="https://github.com/example/foo.git")
    set_process_loop_repositories(monkeypatch, [repo])
    _write_awi_file(notes, "ready.md")
    blocked = _write_awi_file(notes, "blocked.md", body="候補外の依存待ち")
    blocked.write_text(
        blocked.read_text(encoding="utf-8").replace("type: awi\n", "type: awi\ndepends_on: [missing.md]\n"), encoding="utf-8"
    )
    _write_uwi_file(notes, "unanswered.md", question="候補外の未回答", answer="")
    report = _write_uwi_file(notes, "run-skill.md", question="候補外の報告", answer="回答", source="run-skill")
    report.write_text(
        report.read_text(encoding="utf-8").replace("type: uwi\n", "type: uwi\nrun_skill_report: true\n"), encoding="utf-8"
    )
    snapshot = listing.configured_repo_snapshot(notes, config.resolve_configured_repositories())[0]
    records = snapshot.candidate_records()
    assert {record["filename"] for record in records} == {"ready.md"}
    assert len(records) == len(snapshot.candidates) and snapshot.lane_limit == 1
    assert records[0]["body"] == entry_path(notes, "ready.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("output", ["--jsonl", "--no-jsonl", "--summary-only", "--count"])
def test_list_shares_one_parse_across_readiness_selection_and_display(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], output: str
) -> None:
    notes = _setup_notes(tmp_path)
    _write_awi_file(notes, "first.md", source="session-review", body="要約")
    _write_uwi_file(notes, "second.md", source="session-review", question="質問", answer="回答")
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    with mock.patch.object(frontmatter, "parse_frontmatter", wraps=frontmatter.parse_frontmatter) as parser:
        for count in (2, 4):
            with pytest.raises(SystemExit) as result:
                atk.main(
                    ["wi", "list", "--skip-pull", "--source=session-review", output],
                    home=tmp_path,
                    _output_capture_active=True,
                )
            assert result.value.code == 0
            captured = capsys.readouterr()
            assert not captured.err
            if output == "--count":
                assert captured.out == "2\n"
            else:
                assert "first.md" in captured.out and "second.md" in captured.out
                assert "要約" in captured.out and "質問" in captured.out
            assert parser.call_count == count


def test_repeatable_filters_use_or_within_kind_and_and_across_kinds(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    notes = _setup_notes(tmp_path)
    _write_awi_file(notes, "foo.md", target_repo="github.com/example/foo", source="agent")
    held = _write_uwi_file(
        notes,
        "bar.md",
        target_repo="github.com/example/bar",
        answer="承認する",
        source="human",
    )
    held.write_text(held.read_text(encoding="utf-8").replace("state: inbox", "state: hold"), encoding="utf-8")
    _write_awi_file(notes, "excluded.md", target_repo="github.com/example/baz", source="agent")
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

    with pytest.raises(SystemExit) as exc_info:
        atk.main(
            [
                "wi",
                "list",
                "--skip-pull",
                "--target-repo=github.com/example/foo",
                "--target-repo=github.com/example/bar",
                "--target-repo=github.com/example/foo",
                "--type=awi",
                "--type=uwi",
                "--status=inbox",
                "--status=hold",
                "--answered=all",
                "--answered=yes",
                "--source=agent",
                "--source=human",
                "--no-jsonl",
            ],
            home=tmp_path,
        )

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert output.count("foo.md:") == 1
    assert output.count("bar.md:") == 1
    assert "excluded.md:" not in output


class TestListSourceFilter:
    @pytest.mark.parametrize(
        ("source_filter", "expected_filename", "excluded_filename"),
        [
            ("session-review", "fb-001.md", "fb-002.md"),
            ("!session-review", "fb-002.md", "fb-001.md"),
        ],
    )
    def test_source_filter(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        source_filter: str,
        expected_filename: str,
        excluded_filename: str,
    ) -> None:
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md", source="session-review")
        _write_awi_file(notes, "fb-002.md", source=None)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", f"--source={source_filter}"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert expected_filename in captured.out
        assert excluded_filename not in captured.out

    @pytest.mark.parametrize("value", ["--source=", "--source=!"])
    def test_empty_source_value_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        value: str,
    ) -> None:
        _setup_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", value], home=tmp_path)

        assert exc_info.value.code == 2

    def test_filter_matches_exact_source_for_uwi(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        notes = _setup_notes(tmp_path)
        _write_uwi_file(notes, "uwi-001.md", source="session-review")
        _write_uwi_file(notes, "uwi-002.md", source=None)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", "--type=uwi", "--status=all", "--source=session-review"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "uwi-001.md" in captured.out
        assert "uwi-002.md" not in captured.out


class TestListLegacyTypeValues:
    def test_legacy_type_values_are_listed_as_current_types(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        notes = _setup_notes(tmp_path)
        path = _write_awi_file(notes, "fb-001.md")
        path.write_text(
            "---\nstate: inbox\ntarget_repo: github.com/example/foo\ntype: feedback\n---\n\n本文1\n",
            encoding="utf-8",
        )
        path = _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-001.md")
        path.write_text(
            "---\nstate: inbox\ntarget_repo: github.com/example/foo\ntype: tbd\n---\n\nq1\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == (
            "# awi\nfb-001.md: github.com/example/foo [inbox/normal/ready] 本文1\n"
            f"# uwi\n{_FIXED_TIMESTAMP}-001.md: github.com/example/foo [inbox/unanswered] q1\n"
        )

    def test_legacy_type_values_match_current_type_filter(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        notes = _setup_notes(tmp_path)
        path = _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-001.md")
        path.write_text(
            "---\nstate: inbox\ntarget_repo: github.com/example/foo\ntype: tbd\n---\n\nq1\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", "--type=uwi"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == f"# uwi\n{_FIXED_TIMESTAMP}-001.md: github.com/example/foo [inbox/unanswered] q1\n"

        with pytest.raises(SystemExit) as legacy_info:
            atk.main(["wi", "list", "--type=tbd"], home=tmp_path)

        assert legacy_info.value.code == 2


class TestListTypeFilter:
    def test_type_awi_outputs_only_awi_section(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md", target_repo="github.com/example/foo", body="本文1")
        _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", "--type=awi"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == "# awi\nfb-001.md: github.com/example/foo [inbox/normal/ready] 本文1\n"

    def test_type_uwi_outputs_status_label(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        notes = _setup_notes(tmp_path)
        _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", "--type=uwi", "--answered=no"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == f"# uwi\n{_FIXED_TIMESTAMP}-001.md: github.com/example/foo [inbox/unanswered] q1\n"

    def test_answered_uwi_displays_blocked_reason(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        notes = _setup_notes(tmp_path)
        path = _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="回答済み")
        path.write_text(
            path.read_text(encoding="utf-8").replace("type: uwi\n", "type: uwi\ndepends_on: [missing.md]\n"),
            encoding="utf-8",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", "--type=uwi", "--answered=yes"], home=tmp_path)

        assert exc_info.value.code == 0
        output = capsys.readouterr().out
        assert "[inbox/answered/blocked]" in output
        assert "blocked_reason=missing-dependency" in output

    def test_answered_uwi_status_label_omits_type(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        notes = _setup_notes(tmp_path)
        _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="回答")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", "--type=uwi", "--answered=yes"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert f"{_FIXED_TIMESTAMP}-001.md: github.com/example/foo [inbox/answered]" in captured.out
        assert "[uwi/" not in captured.out

    def test_type_all_omits_empty_section_header(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md", body="本文1")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "# awi" in captured.out
        assert "# uwi" not in captured.out


class TestMultipleFiltersCombinedAsAnd:
    """target-repo・source・type・status・answeredの同時指定がAND条件で対象を限定する。

    `--answered`はAWIを無条件除外する仕様（`_answered_matches`が`entry_type != WI_TYPE_UWI`時に
    `False`を返す）のため、`--answered=no`とtype不一致（awi）を1回の呼び出しで同時に指定すると
    type条件の除外効果がanswered条件の除外効果と区別できなくなる。
    target-repo・source・type・statusの4条件は`--answered=all`（無効化）の下で検証し、
    answered条件は同一の4条件（uwiのみ）を満たすエントリ同士の回答有無差分で別途検証する。
    """

    def test_target_repo_source_type_status_combined_narrows_to_intersection(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        matching_repo = "github.com/example/matching"
        notes = _setup_notes(tmp_path)

        _write_uwi_file(notes, "uwi-matching.md", target_repo=matching_repo, source="session-review")

        _write_uwi_file(
            notes,
            "uwi-other-repo.md",
            target_repo="github.com/example/other",
            source="session-review",
        )

        _write_uwi_file(notes, "uwi-other-source.md", target_repo=matching_repo, source="user-issue")

        _write_awi_file(notes, "fb-other-type.md", target_repo=matching_repo, source="session-review")

        processing_dir = entry_path(notes, "uwi-other-status.md", fallback_date=datetime.date(2026, 10, 1)).parent
        processing_dir.mkdir(parents=True, exist_ok=True)
        (processing_dir / "uwi-other-status.md").write_text(
            f"---\nstate: processing\ntarget_repo: {matching_repo}\ntype: uwi\nquestion_type: free-form\n"
            "source: session-review\n---\n\n## 質問\n\n本文\n\n## 回答\n\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                [
                    "wi",
                    "list",
                    f"--target-repo={matching_repo}",
                    "--source=session-review",
                    "--type=uwi",
                    "--status=inbox",
                    "--answered=all",
                ],
                home=tmp_path,
            )

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "uwi-matching.md" in captured.out
        assert "uwi-other-repo.md" not in captured.out
        assert "uwi-other-source.md" not in captured.out
        assert "fb-other-type.md" not in captured.out
        assert "uwi-other-status.md" not in captured.out

    def test_answered_narrows_within_already_matching_four_conditions(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        matching_repo = "github.com/example/matching"
        notes = _setup_notes(tmp_path)
        _write_uwi_file(notes, "uwi-unanswered.md", target_repo=matching_repo, source="session-review", answer="")
        _write_uwi_file(
            notes,
            "uwi-answered.md",
            target_repo=matching_repo,
            source="session-review",
            answer="回答済み",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                [
                    "wi",
                    "list",
                    f"--target-repo={matching_repo}",
                    "--source=session-review",
                    "--type=uwi",
                    "--status=inbox",
                    "--answered=no",
                ],
                home=tmp_path,
            )

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "uwi-unanswered.md" in captured.out
        assert "uwi-answered.md" not in captured.out
