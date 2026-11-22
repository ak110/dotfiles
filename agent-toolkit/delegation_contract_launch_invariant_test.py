"""委譲契約の主題別不変条件を検証する。"""

import pathlib

import pytest
from agent_toolkit._testing.delegation_contract_support import (
    _RETIRED_LAUNCH_DELEGATION,
    _launch_role_errors,
    _parent_body,
    _write_pair,
)


def test_parent_launch_sections_name_subagent_role() -> None:
    """配布する全ての`<役割名>.parent.md`が、起動を述べる節で`subagent_md_path`へ渡す役割名を書く。"""
    errors = _launch_role_errors(pathlib.Path(__file__).resolve().parent)

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


# test-copy-contract: 生成器が読書きする返却契約のstart/end保存標識を一意に保ち、委譲先本文の先読みを防ぐ。
@pytest.mark.parametrize(
    "relative,role",
    [
        ("skills/atk-mode/roles/exec.parent.md", "exec"),
        ("skills/atk-mode/playbooks/lane-receipt.md", "lane-integration"),
        ("skills/atk-mode/roles/exec-review.parent.md", "exec-review"),
        ("skills/atk-mode/roles/reader-fit-review.parent.md", "reader-fit-review"),
        ("skills/atk-mode/roles/wi-draft-review.parent.md", "wi-draft-review"),
        ("skills/atk-mode/roles/add-wi.parent.md", "add-wi"),
        ("skills/atk-mode/playbooks/process-wi-review-audit.md", "copilot-review-audit"),
        ("skills/atk-mode/playbooks/refine-prompt.md", "refine-prompt"),
        ("skills/atk-mode/playbooks/review-external-writes.md", "external-write-review"),
        ("skills/atk-mode/playbooks/investigation-delegation.md", "defect-investigation"),
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
