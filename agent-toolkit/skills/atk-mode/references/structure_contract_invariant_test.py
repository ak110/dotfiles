"""規範由来の計画入力を公開検査へ渡し、構造定義の片側改訂を検出する。"""

import argparse
import pathlib
import re

import pytest

from agent_toolkit._atk import run_script
from agent_toolkit._plan.structure import sections as _sections
from agent_toolkit._testing import git_repository

_PLAN_FORMAT = pathlib.Path(__file__).resolve().with_name("plan-format.md")


def _after_heading(heading: str) -> str:
    """`plan-format.md`の`heading`のH2より後ろの本文を返す。"""
    return _PLAN_FORMAT.read_text(encoding="utf-8").split(f"\n## {heading}\n", 1)[1]


def _layout() -> str:
    """`plan-format.md`「計画ファイルの構成」の本文を返す。"""
    return _after_heading("計画ファイルの構成").split("\n## ", 1)[0]


def _required_heading() -> str:
    """規範が必須とする要求を書く節の見出し行を返す。"""
    required = re.search(r"`(## [^`]+)`を必須とし", _layout())
    assert required is not None
    return required[1]


def _documented_plan(repo: pathlib.Path) -> str:
    """製品定数や共通fixtureを使わず、規範の順序と雛形から記入済み本文を作る。"""
    layout = _layout()
    # 雛形はコードブロックの中にH2を持つため、次のH2までを節とみなさず、見出しの後の最初のコードブロックを取り出す。
    template = _after_heading("初回起草の雛形").split("```md\n", 1)[1].split("\n```", 1)[0]
    parts = re.split(r"(?m)^(## [^\n]+)\n", template)
    sections = dict(zip(parts[1::2], parts[2::2], strict=True))
    request = re.search(r"((?:`## [^`]+`、)+`## [^`]+`)の順で", layout)
    assert request is not None
    request_order = re.findall(r"`(## [^`]+)`", request[1])
    order: list[str] = []
    for item in re.findall(r"(?m)^\d+\. (.+)$", layout):
        if item == "要求を書く節":
            order.extend(heading for heading in request_order if heading in sections)
        elif (fixed := re.fullmatch(r"`(## [^`]+)`", item)) is not None:
            order.append(fixed[1])
    assert sorted(order) == sorted(sections), "雛形のH2が規範の構成の一覧に無い"
    content = parts[0] + "".join(heading + "\n" + sections[heading] for heading in order)
    return re.sub(r"<[^>]+>", "契約の検査", content).replace("/absolute/repository/path", str(repo))


@pytest.mark.parametrize("case", ["documented", "explanation", "missing-heading", "receiver-change"])
def test_documented_plan_is_checked_by_public_command(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], case: str
) -> None:
    """規範どおりの計画と説明追記を受理し、必須の見出しの欠落と受理側だけの変更は診断する。"""
    repo = git_repository.init_repository(tmp_path / "repo")
    path = tmp_path / "plan.md"
    content = _documented_plan(repo)
    diagnostic: str | None = None
    if case == "explanation":
        content = content.replace("\n### 計画メタ情報\n", "\n起草者向けの補足説明。\n\n### 計画メタ情報\n", 1)
    elif case == "missing-heading":
        required = _required_heading()
        assert required + "\n" in content
        content = content.replace(required + "\n", "", 1)
        diagnostic = required.removeprefix("## ")
    elif case == "receiver-change":
        # 入力は規範のまま固定し、受理側だけの改訂が見逃されないことを確認する。
        monkeypatch.setattr(_sections, "PLAN_H2_ORDER_TAIL", (*_sections.PLAN_H2_ORDER_TAIL, "未合意の節"))
        diagnostic = "固定H2`## 未合意の節`"
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
