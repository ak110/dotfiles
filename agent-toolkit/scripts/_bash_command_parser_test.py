"""agent-toolkit/scripts/_bash_command_parser.py のテスト。

`split_bash_segments`と`extract_git_events`の挙動を、
セグメント分割・cd/pushd追跡・git -C 解決・グローバルオプション分離の各観点で検証する。
"""

from __future__ import annotations

import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from _bash_command_parser import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    GitEvent,
    extract_git_events,
    split_bash_segments,
)


class TestSplitBashSegments:
    """`split_bash_segments`によるセグメント分割。"""

    @pytest.mark.parametrize(
        ("command", "expected"),
        [
            ("git log", ["git log"]),
            ("git log; git status", ["git log", "git status"]),
            ("git log && git status", ["git log", "git status"]),
            ("git log || true", ["git log", "true"]),
            ("git log | head -1", ["git log", "head -1"]),
            ("a & b", ["a", "b"]),
            ("", []),
            ("echo 'a; b' && git log", ["echo 'a; b'", "git log"]),
            ('echo "a&b" ; git log', ['echo "a&b"', "git log"]),
        ],
    )
    def test_split(self, command: str, expected: list[str]) -> None:
        assert split_bash_segments(command) == expected


class TestExtractGitEvents:
    """`extract_git_events`によるgit呼び出しイベント抽出。"""

    def test_no_git_returns_empty(self) -> None:
        assert not extract_git_events("echo hello", "/cwd")

    def test_empty_command_returns_empty(self) -> None:
        assert not extract_git_events("", "/cwd")

    def test_empty_command_with_empty_cwd(self) -> None:
        assert not extract_git_events("", "")

    def test_single_log_inherits_payload_cwd(self) -> None:
        events = extract_git_events("git log --oneline", "/cwd")
        assert len(events) == 1
        assert events[0].subcommand == "log"
        assert events[0].cwd == "/cwd"
        assert "--oneline" in events[0].subcommand_args

    def test_empty_payload_cwd_yields_empty_event_cwd(self) -> None:
        events = extract_git_events("git log", "")
        assert events == [GitEvent(subcommand="log", cwd="", global_options=[], subcommand_args=[], cwd_resolved=False)]

    def test_dash_capital_c_absolute(self) -> None:
        events = extract_git_events("git -C /abs/path log", "/cwd")
        assert len(events) == 1
        assert events[0].subcommand == "log"
        assert events[0].cwd == os.path.normpath("/abs/path")
        assert "-C" in events[0].global_options
        assert "/abs/path" in events[0].global_options

    def test_dash_capital_c_relative_resolves_against_payload_cwd(self) -> None:
        events = extract_git_events("git -C sub log", "/cwd")
        assert events[0].cwd == os.path.normpath("/cwd/sub")

    def test_cd_then_git_log(self) -> None:
        events = extract_git_events("cd sub && git log", "/cwd")
        assert events[0].cwd == os.path.normpath("/cwd/sub")

    def test_cd_then_git_dash_capital_c_combines(self) -> None:
        """`cd a; git -C b log` は a/b を実効cwdとして抽出する。"""
        events = extract_git_events("cd a && git -C b log", "/cwd")
        assert events[0].cwd == os.path.normpath("/cwd/a/b")

    def test_multiple_git_calls_with_dash_capital_c(self) -> None:
        events = extract_git_events("git -C /repo/a log; git -C /repo/b status", "/cwd")
        assert [e.subcommand for e in events] == ["log", "status"]
        assert events[0].cwd == os.path.normpath("/repo/a")
        assert events[1].cwd == os.path.normpath("/repo/b")

    def test_cd_persists_across_segments(self) -> None:
        """セグメント間で現在cwdを持ち回ること。"""
        events = extract_git_events("cd a; git log; cd b; git status", "/cwd")
        assert events[0].subcommand == "log"
        assert events[0].cwd == os.path.normpath("/cwd/a")
        assert events[1].subcommand == "status"
        assert events[1].cwd == os.path.normpath("/cwd/a/b")

    def test_mixed_non_git_commands(self) -> None:
        events = extract_git_events("echo hello; git log; ls -la", "/cwd")
        assert len(events) == 1
        assert events[0].subcommand == "log"

    def test_subcommand_args_capture(self) -> None:
        events = extract_git_events("git commit --amend --no-edit", "/cwd")
        assert events[0].subcommand == "commit"
        assert events[0].subcommand_args == ["--amend", "--no-edit"]

    def test_decorate_in_subcommand_args(self) -> None:
        events = extract_git_events("git log --oneline --decorate", "/cwd")
        assert "--decorate" in events[0].subcommand_args

    def test_no_pager_global_option(self) -> None:
        events = extract_git_events("git --no-pager log", "/cwd")
        assert events[0].subcommand == "log"
        assert "--no-pager" in events[0].global_options

    def test_paginate_global_option(self) -> None:
        events = extract_git_events("git --paginate log", "/cwd")
        assert events[0].subcommand == "log"
        assert "--paginate" in events[0].global_options

    def test_dash_c_config_global_option(self) -> None:
        events = extract_git_events("git -c color.ui=false log", "/cwd")
        assert events[0].subcommand == "log"
        assert "-c" in events[0].global_options

    def test_git_dir_with_equal_form(self) -> None:
        events = extract_git_events("git --git-dir=/repo/.git log", "/cwd")
        assert events[0].subcommand == "log"
        assert any(opt.startswith("--git-dir=") for opt in events[0].global_options)

    def test_work_tree_with_separate_value(self) -> None:
        events = extract_git_events("git --work-tree /repo log", "/cwd")
        assert events[0].subcommand == "log"
        assert "--work-tree" in events[0].global_options
        assert "/repo" in events[0].global_options

    def test_cd_no_arg_is_unresolved(self) -> None:
        """`cd`引数なしはHOMEへの遷移を静的に解決できないため解決不能とする。"""
        events = extract_git_events("cd && git log", "/cwd")
        assert events[0].cwd == ""
        assert events[0].cwd_resolved is False

    def test_cd_dash_option_is_unresolved(self) -> None:
        """`cd -`は前ディレクトリへの遷移を静的に解決できないため解決不能とする。"""
        events = extract_git_events("cd - && git log", "/cwd")
        assert events[0].cwd == ""
        assert events[0].cwd_resolved is False

    def test_cd_option_terminator_resolves_target(self) -> None:
        events = extract_git_events("cd -- /tmp && git log", "/cwd")
        assert events[0].cwd == os.path.normpath("/tmp")
        assert events[0].cwd_resolved is True

    @pytest.mark.parametrize("command", ["cd -- && git log", "cd -- -- /tmp && git log"])
    def test_cd_option_terminator_without_unique_target_is_unresolved(self, command: str) -> None:
        events = extract_git_events(command, "/cwd")
        assert events[0].cwd == ""
        assert events[0].cwd_resolved is False

    @pytest.mark.parametrize(
        "command",
        [
            "cd '/tmp/project[1]' && git log",
            'cd "literal[1]" && git log',
            r"cd /tmp/project\[1\] && git log",
            "git -C '/tmp/project[1]' log",
        ],
    )
    def test_shell_metacharacters_are_unresolved_regardless_of_quoting(self, command: str) -> None:
        """引用符・エスケープの有無によらずメタ文字を含む対象を解決不能とする。"""
        events = extract_git_events(command, "/cwd")
        assert events[0].cwd == ""
        assert events[0].cwd_resolved is False

    def test_pushd_acts_like_cd(self) -> None:
        events = extract_git_events("pushd sub && git log", "/cwd")
        assert events[0].cwd == os.path.normpath("/cwd/sub")

    def test_pushd_no_cwd_change_option_preserves_current_cwd(self) -> None:
        events = extract_git_events("pushd -n -- /tmp && git commit --amend", "/cwd")
        assert events[0].cwd == "/cwd"
        assert events[0].cwd_resolved is True

    def test_pushd_no_cwd_change_option_without_terminator_preserves_current_cwd(self) -> None:
        events = extract_git_events("pushd -n /tmp && git log", "/cwd")
        assert events[0].cwd == "/cwd"
        assert events[0].cwd_resolved is True

    @pytest.mark.parametrize("command", ["pushd +1 && git log", "pushd -1 && git log"])
    def test_pushd_stack_rotation_is_unresolved(self, command: str) -> None:
        events = extract_git_events(command, "/cwd")
        assert events[0].cwd == ""
        assert events[0].cwd_resolved is False

    def test_pushd_directory_target_remains_resolved(self) -> None:
        events = extract_git_events("pushd /tmp && git log", "/cwd")
        assert events[0].cwd == os.path.normpath("/tmp")
        assert events[0].cwd_resolved is True

    def test_popd_is_unresolved(self) -> None:
        events = extract_git_events("popd && git log", "/cwd")
        assert events[0].cwd == ""
        assert events[0].cwd_resolved is False
        assert events[0].unresolved_expression is None

    def test_unparsable_segment_skipped(self) -> None:
        """shlex解析に失敗するセグメントは無視する。"""
        events = extract_git_events('git log; echo "unterminated', "/cwd")
        assert len(events) == 1
        assert events[0].subcommand == "log"

    def test_env_assignment_prefix_skipped(self) -> None:
        events = extract_git_events("FOO=bar git log", "/cwd")
        assert events[0].subcommand == "log"

    def test_env_assignment_before_cd(self) -> None:
        events = extract_git_events("FOO=bar cd sub && git log", "/cwd")
        assert events[0].cwd == os.path.normpath("/cwd/sub")

    @pytest.mark.parametrize(
        ("command", "expected_expression"),
        [
            ("cd $VAR && git log", "$VAR"),
            ("cd `pwd` && git log", "`pwd`"),
            ("cd ~ && git log", "~"),
            ("cd '~/repo' && git log", "~/repo"),
            ('cd "$HOME" && git log', "$HOME"),
            ('cd "$HOME/repo" && git log', "$HOME/repo"),
            ("cd agent-* && git log", "agent-*"),
            ("cd foo? && git log", "foo?"),
            ("cd [ab] && git log", "[ab]"),
            ("cd {a,b} && git log", "{a,b}"),
        ],
    )
    def test_cd_shell_expansion_is_unresolved(self, command: str, expected_expression: str) -> None:
        events = extract_git_events(command, "/cwd")
        assert events[0].cwd == ""
        assert events[0].cwd_resolved is False
        assert events[0].unresolved_expression == expected_expression

    def test_pushd_shell_expansion_is_unresolved(self) -> None:
        events = extract_git_events('pushd "$HOME/repo" && git log', "/cwd")
        assert events[0].cwd == ""
        assert events[0].cwd_resolved is False
        assert events[0].unresolved_expression == "$HOME/repo"

    @pytest.mark.parametrize(
        ("command", "expected_expression"),
        [
            ("git -C $DIR log", "$DIR"),
            ("git -C `pwd` log", "`pwd`"),
            ("git -C ~ log", "~"),
            ('git -C "$HOME/repo" log', "$HOME/repo"),
        ],
    )
    def test_git_dash_capital_c_shell_expansion_is_unresolved(self, command: str, expected_expression: str) -> None:
        events = extract_git_events(command, "/cwd")
        assert events[0].cwd == ""
        assert events[0].cwd_resolved is False
        assert events[0].unresolved_expression == expected_expression

    @pytest.mark.parametrize(
        "command",
        ['cd "$HOME" && cd repo && git log', 'git -C "$HOME" -C repo log'],
    )
    def test_relative_change_preserves_prior_unresolved_expression(self, command: str) -> None:
        events = extract_git_events(command, "/cwd")
        assert events[0].cwd_resolved is False
        assert events[0].unresolved_expression == "$HOME"

    @pytest.mark.parametrize(
        ("command", "expected_cwd"),
        [
            ("git -C a -C b log", os.path.normpath("/cwd/a/b")),
            ("git -C /abs -C rel log", os.path.normpath("/abs/rel")),
        ],
    )
    def test_multiple_dash_capital_c_accumulates(self, command: str, expected_cwd: str) -> None:
        events = extract_git_events(command, "/cwd")
        assert events[0].cwd == expected_cwd

    @pytest.mark.skipif(os.name != "nt", reason="Windowsスタイルパス判定はnt環境固有")
    def test_windows_style_absolute_path(self) -> None:
        """Windowsスタイルの絶対パスはnt環境では`os.path.isabs`で判定される。"""
        events = extract_git_events("git -C C:/Users/foo log", "/cwd")
        assert events[0].cwd == os.path.normpath("C:/Users/foo")

    def test_platform_absolute_path_is_normalized(self) -> None:
        """現在プラットフォームの絶対パスは正規化されて返る。"""
        abs_path = os.path.abspath(os.sep + "tmp" + os.sep + "repo")
        events = extract_git_events(f"git -C {abs_path} log", "/cwd")
        assert events[0].cwd == os.path.normpath(abs_path)

    def test_git_without_subcommand(self) -> None:
        """サブコマンド到達せずに終了するケースは空subcommandのGitEventを返す。"""
        events = extract_git_events("git --version", "/cwd")
        assert events[0].subcommand == ""
        assert "--version" in events[0].global_options
