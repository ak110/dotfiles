import argparse
import pathlib
import re

import pytest
from agent_toolkit._atk import run_script
from agent_toolkit._testing import git_repository

_PLAN_FORMAT = pathlib.Path(__file__).resolve().with_name("plan-format.md")


def _documented_plan(repo: pathlib.Path) -> tuple[str, list[str]]:
    text = _PLAN_FORMAT.read_text(encoding="utf-8")
    template = text.split("\n## 初回起草の雛形\n", 1)[1].split("```md\n", 1)[1].split("\n```", 1)[0]
    names = re.findall(r"[0-9]{8}-[0-9]{6}-[0-9]{3}\.md", template)
    return re.sub(r"<[^>]+>", "契約の検査", template).replace("/absolute/repository/path", str(repo)), names


@pytest.mark.parametrize(
    ("case", "diagnostic"),
    [
        ("documented", None),
        ("section", "見出しをH1だけにする"),
        ("explanation", "H1の後に本文を置かない"),
    ],
)
def test_documented_plan_is_checked_by_public_command(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    case: str,
    diagnostic: str | None,
) -> None:
    repo = git_repository.init_repository(tmp_path / "repo")
    content, names = _documented_plan(repo)
    notes = tmp_path / "notes"
    (notes / "processing").mkdir(parents=True)
    for name in names:
        (notes / "processing" / name).write_text(
            "---\ntype: awi\nsource: add-awi-by-user\n---\n# 要求\n\n## 反映内容と反映先\n\n内容\n", encoding="utf-8"
        )
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
    if case == "section":
        content += "\n## 反映内容と反映先\n\n内容\n"
    elif case == "explanation":
        content += "\n起草者向けの補足説明。\n"
    path = tmp_path / "plan.md"
    path.write_text(content, encoding="utf-8")
    before = path.read_bytes()
    result = run_script.dispatch(
        argparse.Namespace(
            script_name="plan-check", script_args=[str(path), "--work-dir", str(repo), "--reject-migration-warnings"]
        )
    )
    output = capsys.readouterr()
    assert result == (0 if diagnostic is None else 1), output.err
    if diagnostic is not None:
        assert diagnostic in output.err
    assert path.read_bytes() == before
