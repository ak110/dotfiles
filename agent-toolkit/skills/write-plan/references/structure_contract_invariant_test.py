"""規範由来の計画入力を公開検査へ渡し、構造定義の片側改訂を検出する。"""

import argparse
import pathlib
import re

import pytest

from agent_toolkit._atk import run_script
from agent_toolkit._plan.structure import sections as _sections
from agent_toolkit._testing import git_repository


def _documented_plan(repo: pathlib.Path) -> str:
    """製品定数や共通fixtureを使わず、規範の順序と雛形から記入済み本文を作る。"""
    directory = pathlib.Path(__file__).resolve().parent
    layout = (directory / "plan-file-standards-structure.md").read_text(encoding="utf-8")
    design = (directory / "plan-file-standards-design.md").read_text(encoding="utf-8")
    template = (directory / "plan-file-standards-template.md").read_text(encoding="utf-8")
    template = template.split("```md\n", 1)[1].split("\n```", 1)[0]
    parts = re.split(r"(?m)^(## [^\n]+)\n", template)
    sections = dict(zip(parts[1::2], parts[2::2], strict=True))
    headings = re.findall(r"^\d+\. `(## [^`]+)`$", layout, re.M)
    assert headings
    optional = re.search(r"任意H2`([^`]+)`", layout)
    assert optional is not None
    headings.insert(3, optional[1])
    permanence = re.search(r"`(## [^`]+)`の直下には(.+?)だけをこの順に置く", design)
    assert permanence is not None
    required_h3 = re.findall(r"`(### [^`]+)`", permanence[2])
    assert required_h3
    section = sections[permanence[1]]
    existing = re.findall(r"^### .+$", section, re.M)
    for old, new in zip(existing, required_h3, strict=True):
        section = section.replace(old, new)
    sections[permanence[1]] = section
    user_heading = re.search(r"`(### [^`<]+)<1から始まる連番>`", design)
    assert user_heading is not None
    history = next(heading for heading, section in sections.items() if "### 初版\n" in section)
    sections[history] += f"\n{user_heading[1]}1\n\n```text\n公開契約を更新する。\n```\n\n要求の全範囲を反映する。\n\n"
    content = parts[0] + "".join(heading + "\n" + sections.get(heading, "\n規範で指定した節。\n\n") for heading in headings)
    content = re.sub(r"<[^>]+>", "契約の検査", content).replace("/absolute/repository/path", str(repo))
    content = content.replace("- 関連WI:\n  - 20260831-000000-001.md: 契約の検査", "- 関連WI: なし")
    return content.replace("エージェント由来のWI (20260831-000000-001.md)", "ユーザー指示")


@pytest.mark.parametrize("case", ["documented", "explanation", "missing-heading", "receiver-change"])
def test_documented_plan_is_checked_by_public_command(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], case: str
) -> None:
    """規範どおりの計画と説明追記を受理し、見出し欠落と受理側だけの変更は診断する。"""
    repo = git_repository.init_repository(tmp_path / "repo")
    path = tmp_path / "plan.md"
    content = _documented_plan(repo)
    if case == "explanation":
        content = content.replace("## 概要\n", "## 概要\n\n起草者向けの補足説明。\n", 1)
    elif case == "missing-heading":
        heading = re.findall(r"^## .+$", content, re.M)[1]
        content = content.replace(heading + "\n", "", 1)
    elif case == "receiver-change":
        # 入力は規範のまま固定し、受理側だけの改訂が見逃されないことを確認する。
        monkeypatch.setattr(_sections, "PLAN_SINGLE_FILE_H2_ORDER", (*_sections.PLAN_SINGLE_FILE_H2_ORDER, "未合意の節"))
    path.write_text(content, encoding="utf-8")
    before = path.read_bytes()
    result = run_script.dispatch(
        argparse.Namespace(
            script_name="plan-check", script_args=[str(path), "--work-dir", str(repo), "--reject-migration-warnings"]
        )
    )
    output = capsys.readouterr()
    assert result == (1 if case in {"missing-heading", "receiver-change"} else 0), output.err
    if result:
        assert "固定H2" in output.err
    assert path.read_bytes() == before
