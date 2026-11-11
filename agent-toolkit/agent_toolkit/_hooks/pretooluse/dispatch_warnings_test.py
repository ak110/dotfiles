# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
# pylint: disable=protected-access
"""dispatch_warningsの契約テスト。"""

import json
import pathlib
import textwrap

import pytest

from agent_toolkit import hook
from agent_toolkit._hooks.pretooluse import dispatch as pretooluse
from agent_toolkit._hooks.pretooluse.decision import Decision
from agent_toolkit._testing.dispatch_support import _existing_work_contract as _existing_work_contract
from agent_toolkit._testing.pretooluse_support import (
    _additional_context,
    _plan_file_state_env,
    _stderr_warn_offenders,
    _write_session_state,
)
from agent_toolkit._testing.pretooluse_support import run_after_read_delivery as _run


@pytest.mark.parametrize("module_name", sorted(hook._SUBCOMMANDS))  # noqa: SLF001  # pylint: disable=protected-access
def test_warn_notices_are_not_written_to_stderr(module_name: str) -> None:
    """exit 0で届かないstderrへwarn通知を出力する実装の再混入を検出する。"""
    package_dir = pathlib.Path(pretooluse.__file__).parent
    if module_name == "pretooluse":
        source = "\n".join(
            path.read_text(encoding="utf-8") for path in sorted(package_dir.glob("*.py")) if not path.name.endswith("_test.py")
        )
    else:
        source = (package_dir.parent / f"{module_name}.py").read_text(encoding="utf-8")
    offenders = _stderr_warn_offenders(source)
    if module_name == "pretooluse":
        assert offenders == []
    else:
        assert offenders == [], module_name


def test_stderr_warn_offenders_detects_indirect_binding() -> None:
    """warn通知を返す関数の結果を束縛してstderrへ渡す形を、定義順によらず検出する。"""
    source = textwrap.dedent(
        """\
        import sys


        def _llm_notice(text, tag):
            return f"{tag}: {text}"


        def _compose_notice():
            return _warn_remote_change()


        def _warn_remote_change():
            return _llm_notice("body", tag="warn")


        def main():
            notice = _compose_notice()
            print(notice, file=sys.stderr)
            print("plain", file=sys.stderr)
        """
    )
    expected_lineno = source.splitlines().index("    print(notice, file=sys.stderr)") + 1
    assert _stderr_warn_offenders(source) == [expected_lineno]


def test_second_removable_warning_is_suppressed(tmp_path: pathlib.Path) -> None:
    """同じ対象・原因・対処の通知は初回に届け、継続中の2回目を抑止する。"""
    payload = {
        "tool_name": "Write",
        "tool_input": {"file_path": str(tmp_path / "uv.lock"), "content": "version = 1\n"},
        "session_id": "dispatch-repeated-removable-warning",
    }
    environment = _plan_file_state_env(tmp_path)
    _write_session_state(
        tmp_path,
        "dispatch-repeated-removable-warning",
        {},
    )

    first = _run(payload, env_overrides=environment)
    second = _run(payload, env_overrides=environment)

    assert first.returncode == 0
    assert second.returncode == 0
    assert "uv add" in _additional_context(first)
    assert _additional_context(second) == ""
    assert second.stderr == ""


def test_irremovable_warning_does_not_advance_removable_repeat_count(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """除去可能性の区分が変わっても同じ原因・本文・対処の反復を抑止する。"""
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))

    def emit_controlled_warning(_tool_name: str, tool_input: dict, _cwd: str) -> Decision:
        notice = pretooluse._llm_notice(  # noqa: SLF001  # pylint: disable=protected-access
            "controlled warning",
            tag=pretooluse._WARN_TAG,  # noqa: SLF001  # pylint: disable=protected-access
            fix="retry",
            removable_cause=bool(tool_input["removable"]),
        )
        return Decision(context=notice)

    monkeypatch.setattr(pretooluse, "_decide_edit_tool", emit_controlled_warning)

    def run(removable: bool) -> int:
        return pretooluse.main(
            json.dumps(
                {
                    "tool_name": "SyntheticEdit",
                    "tool_input": {"removable": removable},
                    "session_id": "dispatch-mixed-removability",
                }
            )
        )

    irremovable = run(False)
    first_removable = run(True)
    second_removable = run(True)

    captured = capsys.readouterr()
    assert irremovable == 0
    assert first_removable == 0
    assert second_removable == 0
    assert "この通知は同一セッションで2件目である" not in captured.out
    assert captured.out.count("controlled warning") == 1
    assert captured.err == ""
