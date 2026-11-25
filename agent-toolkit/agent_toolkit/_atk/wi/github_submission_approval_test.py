import pathlib

import pytest

from agent_toolkit import atk
from agent_toolkit._atk.wi import alerts, frontmatter, layout
from agent_toolkit._testing import git_repository
from agent_toolkit.atk_test import _FIXED_DT, _setup_notes

_REPO = "github.com/example/repo"
_URL = f"https://{_REPO}/pull/16"


@pytest.mark.parametrize(("answer", "result"), [("取り込む", "adopted"), ("取り込まない", "rejected")])
def test_answer_imports_github_submission_at_most_once(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    answer: str,
    result: str,
) -> None:
    notes = _setup_notes(tmp_path)
    git_repository.init_repository(notes)
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
    for key in ("AI_AGENT", "CODEX_CI", "CLAUDECODE", "CURSOR_AGENT"):
        monkeypatch.delenv(key, raising=False)
    question = "# GitHub PR #16の取り込み\n\n>     外部入力"
    for index in (1, 2):
        filename = f"20261125-122400-{index:03d}.md"
        path = layout.entry_path(notes, filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            frontmatter.serialize_frontmatter(
                {
                    "type": "uwi",
                    "state": "inbox",
                    "target_repo": _REPO,
                    "source": "github-submission",
                    "github_source": _URL,
                    "question_type": "choice",
                    "choices": "取り込む,取り込まない",
                },
                f"\n## 質問\n\n{question}\n\n## 回答\n\n<!-- ユーザーはこの行以降に回答を追記する -->\n",
            ),
            encoding="utf-8",
        )
        with pytest.raises(SystemExit) as raised:
            atk.main(["wi", "answer", filename, answer], home=tmp_path, now=_FIXED_DT)
        assert raised.value.code == 0
        assert layout.entry_state_of(path) == result
        assert f"\n{answer}\n" in path.read_text(encoding="utf-8")
    assert alerts.existing_github_sources(notes, _REPO, entry_type="awi") == ({_URL} if result == "adopted" else set())
    awis = []
    for path in layout.iter_entry_paths(notes):
        parsed = frontmatter.parse_frontmatter(path.read_text(encoding="utf-8"))
        assert parsed is not None
        if parsed[0].get("type") == "awi":
            awis.append(parsed[1])
    assert [body.strip() for body in awis] == ([question] if result == "adopted" else [])
