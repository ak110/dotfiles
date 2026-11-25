import argparse
import importlib
import pathlib
import re

import pytest
from agent_toolkit._atk import run_script
from agent_toolkit._testing import git_repository

_PLAN_FORMAT = pathlib.Path(__file__).resolve().with_name("plan-format.md")


def _after_heading(heading: str) -> str:
    """`plan-format.md`の`heading`のH2より後ろの本文を返す。"""
    return _PLAN_FORMAT.read_text(encoding="utf-8").split(f"\n## {heading}\n", 1)[1]


def _layout() -> str:
    """`plan-format.md`「計画ファイルの構成」の本文を返す。"""
    return _after_heading("計画ファイルの構成").split("\n## ", 1)[0]


def _required_heading() -> str:
    required = re.search(r"`(## [^`]+)`（必須）", _layout())
    assert required is not None
    return required[1]


def _documented_plan(repo: pathlib.Path) -> str:
    template = _after_heading("初回起草の雛形").split("```md\n", 1)[1].split("\n```", 1)[0]
    documented = re.search(r"(?m)^3\. (.+)$", _layout())
    assert documented is not None
    order = re.findall(r"`(## [^`]+)`", documented[1])
    headings = re.findall(r"(?m)^## [^\n]+", template)
    assert headings == [heading for heading in order if heading in headings]
    return re.sub(r"<[^>]+>", "契約の検査", template).replace("/absolute/repository/path", str(repo))


@pytest.mark.parametrize("case", ["documented", "explanation", "missing-heading", "receiver-change"])
def test_documented_plan_is_checked_by_public_command(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], case: str
) -> None:
    repo = git_repository.init_repository(tmp_path / "repo")
    path = tmp_path / "plan.md"
    content = _documented_plan(repo)
    diagnostic: str | None = None
    if case == "explanation":
        content = content.replace("\n## 反映内容と反映先\n", "\n起草者向けの補足説明。\n\n## 反映内容と反映先\n", 1)
    elif case == "missing-heading":
        required = _required_heading()
        assert required + "\n" in content
        content = content.replace(required + "\n", "", 1)
        diagnostic = required.removeprefix("## ")
    elif case == "receiver-change":
        request = importlib.import_module("agent_toolkit._plan.structure.request_sections")
        monkeypatch.setattr(request, "REQUEST_H2_REQUIRED", (*request.REQUEST_H2_REQUIRED, "未合意の節"))
        diagnostic = "非空の必須節がありません: 未合意の節"
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
