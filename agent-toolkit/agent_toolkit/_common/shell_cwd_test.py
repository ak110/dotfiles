"""Bashの作用範囲と読取・書込先への実行位置の受け渡しを確かめる。"""

import pathlib
import shlex

import pytest

from agent_toolkit._common import shell_cwd


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("cd {next} && echo x > out", "next"),
        ("cd sub; echo x > out", "sub"),
        ("(cd {next}); echo x > out", "base"),
        ("{{ cd {next}; }}; echo x > out", "next"),
        ("if true; then cd {next}; fi; echo x > out", "next"),
        ("cd {next} | tee out", "base"),
        ("cd {next} & echo x > out", "base"),
        ('echo "$(cd {next})" > out', "base"),
        ('cd "$D"; echo x > out', None),
        ("popd; echo x > out", None),
        ('cd "$D"; cd {next}; echo x > out', "next"),
    ],
)
def test_invocation_cwds_preserve_shell_scope(tmp_path: pathlib.Path, command: str, expected: str | None) -> None:
    """シェルの子環境から移動を持ち越さず、不明の位置から相対パスを推定しない。"""
    base = tmp_path / "base"
    next_dir = tmp_path / "next"
    source = command.format(next=shlex.quote(next_dir.as_posix()))
    positions = [
        current
        for invocation, current in shell_cwd.bash_invocation_cwds(source, str(base))
        if invocation.segment.tokens[0] in {"echo", "tee"}
    ]
    assert len(positions) == 1
    if expected is None:
        assert not positions[0].resolved
    else:
        target = base / "sub" if expected == "sub" else next_dir if expected == "next" else base
        assert positions[0].resolved and pathlib.Path(positions[0].path) == target


def test_unknown_marker_is_not_a_directory() -> None:
    """展開の標識をパスへ混ぜず、後続の位置を不明として渡す。"""
    positions = list(shell_cwd.bash_invocation_cwds('cd "$D" && cat file', "/base"))
    assert not positions[-1][1].resolved


def test_substitution_inherits_cwd_before_outer_directory_change(tmp_path: pathlib.Path) -> None:
    """外側のcdを実行する前のcwdを置換へ渡す。"""
    positions = list(shell_cwd.bash_invocation_cwds('cd "$(cd sub; echo target)"; cat file', str(tmp_path)))
    inner = next(current for invocation, current in positions if invocation.segment.tokens[0] == "echo")
    assert inner.resolved and pathlib.Path(inner.path) == tmp_path / "sub"
    assert not positions[-1][1].resolved
