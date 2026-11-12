"""委譲契約の主題別不変条件を検証する。"""

import pathlib

import pytest

from agent_toolkit._testing.delegation_contract_support import (
    _GENERIC_READ_CONDITION,
    _RETIRED_LAUNCH_DELEGATION,
    _launch_read_errors,
    _launch_role_errors,
    _parent_body,
    _write_pair,
)


def test_parent_launch_sections_name_subagent_role() -> None:
    """配布する全ての`<役割名>.parent.md`が、起動を述べる節で`subagent_md_path`へ渡す役割名を書く。"""
    share = pathlib.Path(__file__).resolve().parent / "share"

    errors = _launch_role_errors(share)

    assert not errors, "\n".join(errors)


def test_parent_launch_section_without_role_is_reported(tmp_path: pathlib.Path) -> None:
    """役割名を書かず共通節へ起動の形を委ねる`<役割名>.parent.md`を報告し、役割名を書いた文書を受理する。"""
    _write_pair(
        tmp_path,
        parent_body=_parent_body(
            after_marker="\n`task.subagent.md`を指定する起動（共通節" + _RETIRED_LAUNCH_DELEGATION + "）で起動する。\n"
        ),
    )
    assert _launch_role_errors(tmp_path) == [
        "起動を述べる節に役割名が無い: task.parent.md -> task",
        "起動の形を共通節へ委ねている: task.parent.md",
    ]

    _write_pair(tmp_path, parent_body=_parent_body(after_marker="\n| `subagent_md_path` | `task` |\n"))
    assert not _launch_role_errors(tmp_path)


def test_launch_contracts_do_not_route_recipient_procedure_before_launch() -> None:
    """起動対象を宣言する全文書が、その委譲先の手順書を時点を限らない条件で読ませない。"""
    share = pathlib.Path(__file__).resolve().parent / "share"

    errors = _launch_read_errors(share)

    assert not errors, "\n".join(errors)


# test-copy-contract: 生成器が読書きする返却契約のstart/end保存標識を一意に保ち、委譲先本文の先読みを防ぐ。
@pytest.mark.parametrize(
    "relative,role",
    [
        ("share/exec.parent.md", "exec"),
        ("share/exec.parent.md", "lane-integration"),
        ("share/exec-review.parent.md", "exec-review"),
        ("share/reader-fit-review.parent.md", "reader-fit-review"),
        ("share/wi-draft-review.parent.md", "wi-draft-review"),
        ("share/recurrence-self-check.parent.md", "recurrence-self-check"),
        ("share/add-wi.parent.md", "add-wi"),
        ("skills/process-wi/references/review-audit-launch.md", "copilot-review-audit"),
        ("skills/refine-prompt/SKILL.md", "refine-prompt"),
        ("skills/delegation/references/investigation-delegation.md", "defect-investigation"),
    ],
)
def test_caller_owns_generated_return_contract_boundary(relative: str, role: str) -> None:
    """呼出側の返却契約を生成配置し、形式確認のための他担当全文読込を求めない。"""
    root = pathlib.Path(__file__).resolve().parent
    content = (root / relative).read_text(encoding="utf-8")
    assert content.count(f"<!-- return-contract:{role}:start -->") == 1
    assert content.count(f"<!-- return-contract:{role}:end -->") == 1
    for line in content.splitlines():
        if line.startswith("|"):
            assert f"{role}.subagent.md#「出力」" not in line


def test_launch_contract_generic_read_of_recipient_is_reported(tmp_path: pathlib.Path) -> None:
    """定型の条件で委譲先の手順書や入力節を読ませる行を報告し、時点を書いた行と所在参照を受理する。"""
    share = tmp_path / "share"
    generic = f"| {_GENERIC_READ_CONDITION} | `task.subagent.md` | 判断 | `read:agent-toolkit:*/share/task.subagent.md` |\n"
    section = (
        f"| {_GENERIC_READ_CONDITION} | `task.subagent.md#「入力」` | 判断 | `read:agent-toolkit:*/share/task.subagent.md` |\n"
    )
    table = "\n## 名指し参照の読込方法\n\n| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |\n| --- | --- | --- | --- |\n"
    _write_pair(
        share,
        parent_body=_parent_body(after_marker="\n委譲先の手順は`task.subagent.md`が定める。\n" + table + generic + section),
    )

    errors = _launch_read_errors(share)
    # 時点を書いた行が無い本文の名指しにも定型の条件の宣言が補われるため、本文の行も報告する。
    assert len(errors) == 3 and errors[0].endswith("-> task.subagent.md") and "task.parent.md:" in errors[0]
    assert errors[1:] == [
        "起動する委譲先の手順書を定型の条件で読ませる: task.parent.md:13 -> task.subagent.md",
        "起動する委譲先の手順書を定型の条件で読ませる: task.parent.md:14 -> task.subagent.md",
    ]

    timed = (
        "| 返却を受け取った後に形式を検収する時 | `task.subagent.md#「出力」` | 判断 "
        "| `read:agent-toolkit:*/share/task.subagent.md` |\n"
    )
    _write_pair(
        share,
        parent_body=_parent_body(after_marker="\n委譲先の手順は`task.subagent.md`が定める（所在参照）。\n" + table + timed),
    )

    assert not _launch_read_errors(share)

    # 時点を書いた行がある資料の本文の名指しには、定型の条件の宣言が補われない。
    _write_pair(
        share,
        parent_body=_parent_body(after_marker="\n検収では`task.subagent.md`の出力を確かめる。\n" + table + timed),
    )

    assert not _launch_read_errors(share)
