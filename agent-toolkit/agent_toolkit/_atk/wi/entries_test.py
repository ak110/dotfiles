"""WIエントリの走査、対象リポジトリによる限定と種別の判定のテスト。"""

import pathlib
from unittest import mock

import pytest

from agent_toolkit import atk
from agent_toolkit._atk.wi import constants as _wi_constants
from agent_toolkit._atk.wi import entries as _wi_entries
from agent_toolkit._atk.wi import entry_text, formatters, frontmatter
from agent_toolkit._atk.wi import readiness as _readiness
from agent_toolkit._atk.wi.mutations import content as _mutations_content
from agent_toolkit._atk.wi.mutations import targets as _mutations_targets
from agent_toolkit._git import remote as _git_remote
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.wi_entry_files import write_uwi


class TestIterEntriesRepoFilter:
    """保存済みのリポジトリ表記を正規化して同じ対象へ限定する。"""

    def test_local_path_filter_includes_legacy_and_current_repo_forms(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """生のローカルパス指定でも旧パス形とURL形を含み、不在のパスは除く。"""
        local_repo = tmp_path / "repo"
        git_repository.init_repository(local_repo, origin="git@github.com:example/repo.git")
        write_uwi(tmp_path, "legacy.md", target_repo=str(local_repo), question="旧形式")
        write_uwi(tmp_path, "current.md", target_repo="github.com/example/repo", question="現行形式")
        write_uwi(tmp_path, "missing.md", target_repo=str(tmp_path / "missing"), question="対象外")

        entries = list(_wi_entries.iter_entries(tmp_path, ("inbox",), str(local_repo), _wi_constants.WI_TYPE_UWI))
        assert {entry[0].name for entry in entries} == {"legacy.md", "current.md"}

    def test_filter_resolves_each_raw_repo_value_once(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """1回の反復では同じ保存値を複数項目が持っても解決を1回に限定する。"""
        write_uwi(tmp_path, "first.md", target_repo="/legacy/repo")
        write_uwi(tmp_path, "second.md", target_repo="/legacy/repo")
        calls: list[str] = []

        def resolve(value: str) -> str | None:
            calls.append(value)
            return "github.com/example/repo" if value in {"/legacy/repo", "github.com/example/repo"} else None

        monkeypatch.setattr(_git_remote, "resolve_repo_identifier", resolve)

        entries = list(_wi_entries.iter_entries(tmp_path, ("inbox",), "github.com/example/repo", _wi_constants.WI_TYPE_UWI))

        assert [entry[0].name for entry in entries] == ["first.md", "second.md"]
        assert calls.count("/legacy/repo") == 1


_TYPELESS_ENTRY = "---\ntarget_repo: github.com/example/foo\n---\n\n# 本文\n"


def _next_action_of(capsys: pytest.CaptureFixture[str]) -> str:
    """標準エラーの次の操作の行の本文を返す。"""
    lines = [line for line in capsys.readouterr().err.splitlines() if line.startswith("次の操作: ")]
    assert len(lines) == 1
    return lines[0]


def test_typeless_entry_guides_to_report_instead_of_edit(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """typeが欠落した項目は`atk wi edit`でも同じ理由で拒否されるため、確認と報告を案内する。"""
    path = tmp_path / "20260930-000000-001.md"
    path.write_text(_TYPELESS_ENTRY, encoding="utf-8")

    with pytest.raises(SystemExit):
        _wi_entries.entry_type_from_metadata(path, {"target_repo": "github.com/example/foo"})
    common_next_action = _next_action_of(capsys)
    with pytest.raises(SystemExit):
        _readiness._require_type(path, _TYPELESS_ENTRY)  # pylint: disable=protected-access
    readiness_next_action = _next_action_of(capsys)
    # 案内した`atk wi edit`に`--body-file`で本文を渡した場合は、同じ検証で拒否されることを確かめる。
    with pytest.raises(SystemExit):
        _mutations_content._build_noninteractive_edit_content(path, _TYPELESS_ENTRY, "# 新しい本文\n")  # pylint: disable=protected-access
    capsys.readouterr()

    for next_action in (common_next_action, readiness_next_action):
        assert f"`atk wi show {path.name}`" in next_action
        assert "ユーザーへ報告する" in next_action
        assert "でtypeを追記" not in next_action


@pytest.mark.parametrize(
    "content",
    [
        _TYPELESS_ENTRY,
        _TYPELESS_ENTRY.replace("---\n\n", "type: foo\n---\n\n"),
        "---\ntarget_repo: [\n---\n\n# 本文\n",
        "---\ntype: awi\n---\n\n# 本文\n",
    ],
)
@pytest.mark.parametrize("state", ["inbox", "adopted"])
def test_broken_entry_next_action_can_show_saved_content(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    content: str,
    state: str,
) -> None:
    """不正な管理情報の項目でも、公開showが同じ保存本文を表示する。"""
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(tmp_path))
    directory = tmp_path / state
    directory.mkdir()
    filename = "20261013-000000-001.md"
    (directory / filename).write_text(content, encoding="utf-8")
    if "target_repo: [" in content or "target_repo:" not in content:
        with pytest.raises(SystemExit):
            _mutations_targets.entry_target_repo(directory / filename, content)
        assert f"atk wi show {filename}" in _next_action_of(capsys)
    with pytest.raises(SystemExit) as shown:
        atk.main(["wi", "show", filename, "--skip-pull"], home=tmp_path, _output_capture_active=True)
    assert shown.value.code == 0
    assert content in capsys.readouterr().out
    if "type: awi" not in content and "target_repo: [" not in content:
        with pytest.raises(SystemExit) as listed:
            atk.main(["wi", "list", "--state", "all", "--skip-pull"], home=tmp_path, _output_capture_active=True)
        assert listed.value.code == 2
        assert f"atk wi show {filename}" in capsys.readouterr().err


def test_unparsable_frontmatter_guides_to_report_instead_of_edit(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """frontmatterを解析できない項目は、確認と報告を案内する。"""
    path = tmp_path / "20260930-000000-002.md"
    broken = "---\ntarget_repo: [\n---\n\n# 本文\n"
    path.write_text(broken, encoding="utf-8")

    with pytest.raises(SystemExit):
        _mutations_targets.entry_target_repo(path, broken)  # pylint: disable=protected-access
    next_action = _next_action_of(capsys)

    assert f"`atk wi show {path.name}`" in next_action
    assert "書式を直す" not in next_action


def test_named_read_preserves_request_order_filters_and_state_priority(tmp_path: pathlib.Path) -> None:
    """明示名の共通読取は要求順と状態順を保ち、条件外の項目を不在へ分ける。"""
    first, second, absent = (f"20261008-000000-00{index}.md" for index in (1, 2, 3))
    for state in _wi_constants.WI_STATES:
        (tmp_path / state).mkdir()
    for index, state in enumerate(_wi_constants.WI_STATES[:2]):
        (tmp_path / state / first).write_text(
            f"---\ntarget_repo: github.com/example/foo\ntype: awi\nsource: agent\n---\n# 候補{index}\n",
            encoding="utf-8",
        )
    (tmp_path / _wi_constants.WI_STATES[-1] / second).write_text(
        "---\ntarget_repo: github.com/example/bar\ntype: uwi\nsource: user\n---\n# 別項目\n",
        encoding="utf-8",
    )
    names = _wi_entries.validate_named_filenames(tmp_path, [second, first, first, absent])
    selected, missing = _wi_entries.read_named_entries(tmp_path, names, target_repo=None)
    assert [item[0].name for item in selected] == [second, first]
    assert selected[1][3] == _wi_constants.WI_STATES[0]
    assert missing == [absent]
    selected, missing = _wi_entries.read_named_entries(
        tmp_path, names, target_repo="github.com/example/foo", entry_type=("awi",), source="agent"
    )
    assert [item[0].name for item in selected] == [first]
    assert missing == [second, absent]
    selected, missing = _wi_entries.read_named_entries(tmp_path, names, target_repo=None, source="user")
    assert [item[0].name for item in selected] == [second]
    assert missing == [first, absent]


@pytest.mark.parametrize("operation", ["select", "named"])
def test_entry_metadata_is_parsed_once_per_acquisition(tmp_path: pathlib.Path, operation: str) -> None:
    """属性の抽出と消費を共有し、別操作では本文を読み直す。"""
    path = tmp_path / "inbox" / "20261018-000000-001.md"
    path.parent.mkdir()
    text = "\ufeff---\ntarget_repo: github.com/example/repo\ntype: awi\nsource: 001\n---\n\n要約\n"
    path.write_text(text, encoding="utf-8")

    def acquire() -> list[_wi_entries.EntryRecord]:
        if operation == "named":
            selected, missing = _wi_entries.read_named_entries(
                tmp_path, [(path.name, path.name)], target_repo=None, source="001"
            )
            assert missing == ([] if selected else [path.name])
            return selected
        return _wi_entries.select_entries(
            tmp_path, status="inbox", target_repo=None, entry_type="awi", answered="all", source="001"
        )

    with mock.patch.object(frontmatter, "parse_frontmatter", wraps=frontmatter.parse_frontmatter) as parser:
        for count in (1, 2):
            selected = acquire()
            assert [(item[1], item[4]) for item in selected] == [("github.com/example/repo", "awi")]
            assert formatters.parse_source(selected[0][2]) == "001"
            assert formatters.body_summary(selected[0][2]) == "要約"
            assert parser.call_count == count
        path.write_text(text.replace("001\n---", "002\n---"), encoding="utf-8")
        assert not acquire()
        assert parser.call_count == 3


@pytest.mark.parametrize(
    ("text", "kind", "repo"),
    [
        ("\ufeff---\ntype: awi\ntarget_repo: 001\n---\n本文\n", "awi", "001"),
        ("---\ntype: [\n---\n本文\n", None, "(unknown)"),
        ("---\ntype: unexpected\n---\n本文\n", None, "(unknown)"),
    ],
)
def test_named_read_preserves_special_input_values(tmp_path: pathlib.Path, text: str, kind: str | None, repo: str) -> None:
    """指定名読取は破損と不正種別を表示可能な値として保つ。"""
    path = tmp_path / "inbox" / "20261018-000000-001.md"
    path.parent.mkdir()
    path.write_text(text, encoding="utf-8")
    with mock.patch.object(frontmatter, "parse_frontmatter", wraps=frontmatter.parse_frontmatter) as parser:
        selected, missing = _wi_entries.read_named_entries(tmp_path, [(path.name, path.name)], target_repo=None)
        assert not missing
        assert selected[0][1:] == (repo, text, "inbox", kind)
        assert parser.call_count == 1


def test_acquired_text_preserves_literal_scalars_and_typed_schedule(tmp_path: pathlib.Path) -> None:
    """解析結果を受け取る消費側で字面とqueue_scheduleの型を保持する。"""
    path = tmp_path / "inbox" / "entry.md"
    path.parent.mkdir()
    path.write_text(
        "---\ntype: awi\ntarget_repo: github.com/example/repo\nsource: false\ntarget_commit: 001\n"
        "queue_schedule:\n  carry_count: 2\n  enabled: true\n---\n\n本文\n",
        encoding="utf-8",
    )
    selected = list(_wi_entries.iter_entries(tmp_path, ("inbox",), None))
    parsed = entry_text.parse_frontmatter(selected[0][2])
    assert parsed == (
        {
            "type": "awi",
            "target_repo": "github.com/example/repo",
            "source": "false",
            "target_commit": "001",
            "queue_schedule": {"carry_count": 2, "enabled": True},
        },
        "\n本文\n",
    )
