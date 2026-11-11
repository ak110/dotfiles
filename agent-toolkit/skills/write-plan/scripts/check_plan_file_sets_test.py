# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""初回計画集合の検査を検証する。"""

import collections.abc
import pathlib
import subprocess
import typing

import check_plan_file
import pytest
import yaml

from agent_toolkit._plan import structure as _plan_format
from agent_toolkit._testing import plan_fixture as _plan_fixture
from agent_toolkit._testing.plan_file_check_support import _RESUMED_AWI, _RESUMED_DECISION, _run_plan_cli
from agent_toolkit._testing.plan_file_check_support import fixture_repo as fixture_repo
from agent_toolkit._testing.plugin_environment_support import prepare_plugin_environment

_SET_AWIS = ("20260831-000000-101.md", "20260831-000000-102.md", "20260831-000000-103.md")


def _write_set_plan(work_dir: pathlib.Path, name: str, awi: str, *, padding: int = 0) -> pathlib.Path:
    """1件のAWIを担当する自己完結した現行形式の計画を書き、そのパスを返す。

    実施内容の人間由来の行も担当AWIを指すように置き換え、計画ごとの関連WIと由来を一致させる。
    `padding`行の本文を加えると、計画の行数の警告の境界を計画ごとに作成できる。
    """
    content = _plan_fixture.current_plan(repo=work_dir.resolve(), related_wi=((awi, "分割した要求"),)).replace(
        _plan_fixture.WI_FILES[0][0], awi
    )
    if padding:
        content = content.replace("対象の公開契約を更新する。", "\n".join(["対象の公開契約を更新する。"] * padding), 1)
    path = work_dir / name
    path.write_text(content, encoding="utf-8")
    return path


def _run_set_cli(
    work_dir: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    plans: typing.Sequence[pathlib.Path],
    decisions: list[dict[str, str]],
    *extra: str,
) -> tuple[int, str]:
    """計画の集合を公開CLIへ1回渡す。集合の各計画が由来に使うWI本文を一時のprivate-notesへ置く。"""
    wi_names = {*_SET_AWIS, _RESUMED_AWI, "20260831-000000-104.md"}
    return _run_plan_cli(work_dir, monkeypatch, plans, decisions, wi_names, *extra)


def _set_decisions(*names: str) -> list[dict[str, str]]:
    return [{"awi": name, "lane": "lane-01"} for name in names]


def _run_plan_launcher(
    work_dir: pathlib.Path,
    plans: list[pathlib.Path],
    host_environ: collections.abc.Callable[[], dict[str, str]],
    *extra: str,
) -> subprocess.CompletedProcess[str]:
    """隔離したWI・計画を実ランチャーへ渡し、集合比較の結果を受け取る。"""
    queue = work_dir / "private-notes"
    inbox = queue / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    names: set[str] = set()
    for plan in work_dir.glob("*.md"):
        metadata, _errors = _plan_format.parse_plan_metadata(plan.read_text(encoding="utf-8"))
        if metadata is not None:
            names.update(name for name, _summary in metadata.related_wi)
    for name in names:
        (inbox / name).write_text("# 要求\n\n本文。\n", encoding="utf-8")
    env = host_environ()
    env["AGENT_TOOLKIT_PRIVATE_NOTES"] = str(queue)
    env["AGENT_TOOLKIT_RUNTIME_DATA"] = str(work_dir / "runtime")
    # CI全体のfrozen指定は、公開ランチャーのno-project起動へ持ち込まない。
    env.pop("UV_FROZEN", None)
    root = pathlib.Path(check_plan_file.__file__).resolve().parents[3]
    prepare_plugin_environment(root, env)
    launcher = root / "scripts" / "atk"
    return subprocess.run(
        [str(launcher), "run-script", "plan-check", "--", "--work-dir", str(work_dir), *extra, *(str(p) for p in plans)],
        cwd=work_dir,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )


@pytest.mark.parametrize("case", ["complete", "missing", "extra"])
def test_cli_checks_expected_wi_set(
    repo: tuple[pathlib.Path, str], case: str, host_environ: collections.abc.Callable[[], dict[str, str]]
) -> None:
    """全99件と比較し、5件だけの計画の欠落94件と余剰WIを実ランチャーから検出する。"""
    work_dir, _base = repo
    expected = tuple(f"20261018-000000-{index:03}.md" for index in range(1, 100))
    extra = "20261018-000000-100.md"
    actual = expected[:5] if case == "missing" else (*expected, extra) if case == "extra" else expected
    path = _write_set_plan(work_dir, "expected.md", actual[0])
    content = _plan_fixture.current_plan(repo=work_dir.resolve(), related_wi=tuple((name, "要求") for name in actual))
    path.write_text(content.replace(_plan_fixture.WI_FILES[0][0], actual[0]), encoding="utf-8")
    result = _run_plan_launcher(work_dir, [path], host_environ, *(arg for name in expected for arg in ("--expected-wi", name)))
    assert result.returncode == (0 if case == "complete" else 1), result.stderr
    if case == "missing":
        assert all(name in result.stderr for name in expected[5:]), result.stderr
        assert "欠落=" in result.stderr
    elif case == "extra":
        assert f"余剰=['{extra}']" in result.stderr


def test_cli_expected_wi_tracks_explicit_addition(
    repo: tuple[pathlib.Path, str], host_environ: collections.abc.Callable[[], dict[str, str]]
) -> None:
    """同じworktreeの計画対象だけを比較し、明示追加の前後で計画の不足と充足を判定する。"""
    work_dir, _base = repo
    first, second, added = _SET_AWIS
    plans = [_write_set_plan(work_dir, "first.md", first), _write_set_plan(work_dir, "second.md", second)]
    args = ["--expected-wi", first, "--expected-wi", second]
    assert _run_plan_launcher(work_dir, plans, host_environ, *args).returncode == 0
    args.extend(["--expected-wi", added])
    missing = _run_plan_launcher(work_dir, plans, host_environ, *args)
    assert missing.returncode == 1, missing.stderr
    assert added in missing.stderr
    plans.append(_write_set_plan(work_dir, "added.md", added))
    result = _run_plan_launcher(work_dir, plans, host_environ, *args)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("case", ["match", "missing", "extra", "resumed", "prior", "multiple"])
def test_cli_preserves_lane_selection_with_expected_wi_extension(
    repo: tuple[pathlib.Path, str], case: str, host_environ: collections.abc.Callable[[], dict[str, str]]
) -> None:
    """実ランチャーでも既存選定YAMLの集合比較・再開・先行計画・複数計画を保つ。"""
    work_dir, _base = repo
    first, second, third = _SET_AWIS
    plan = _write_set_plan(work_dir, "first.md", first)
    plans = [plan]
    decisions = _set_decisions(first)
    extra_args: list[str] = []
    if case == "missing":
        decisions.extend(_set_decisions(second))
    elif case == "extra":
        decisions = _set_decisions(second)
    elif case == "resumed":
        decisions.append({"awi": second, "lane": "lane-01", "再開位置": "既存計画から再開"})
    elif case in {"prior", "multiple"}:
        other = _write_set_plan(work_dir, "second.md", second)
        decisions.extend(_set_decisions(second))
        if case == "prior":
            extra_args = ["--prior-plan", str(other)]
        else:
            plans.append(other)
    decisions.append({"awi": third, "lane": "lane-02"})
    selection = work_dir / "selection.yaml"
    selection.write_text(yaml.safe_dump({"decisions": decisions}), encoding="utf-8")
    result = _run_plan_launcher(
        work_dir, plans, host_environ, "--selection-file", str(selection), "--lane", "lane-01", *extra_args
    )
    assert result.returncode == (1 if case in {"missing", "extra"} else 0), result.stderr
    if case in {"missing", "extra"}:
        assert second in result.stderr


@pytest.mark.parametrize("case", ["invalid", "combined", "duplicate-expected", "duplicate-related"])
def test_cli_expected_wi_reports_invalid_or_duplicate_input(
    repo: tuple[pathlib.Path, str], case: str, host_environ: collections.abc.Callable[[], dict[str, str]]
) -> None:
    """新入力の形式・併用誤りを終了2、重複を終了1として対象と原因を示す。"""
    work_dir, _base = repo
    name = _SET_AWIS[0]
    plan = _write_set_plan(work_dir, "first.md", name)
    args = ["--expected-wi", "../invalid.md" if case == "invalid" else name]
    if case == "combined":
        selection = work_dir / "selection.yaml"
        selection.write_text(yaml.safe_dump({"decisions": _set_decisions(name)}), encoding="utf-8")
        args.extend(["--selection-file", str(selection), "--lane", "lane-01"])
    elif case == "duplicate-expected":
        args.extend(["--expected-wi", name])
    elif case == "duplicate-related":
        duplicate = _write_set_plan(work_dir, "duplicate.md", name)
        args.extend(["--prior-plan", str(duplicate)])
    result = _run_plan_launcher(work_dir, [plan], host_environ, *args)
    assert result.returncode == (2 if case in {"invalid", "combined"} else 1), result.stderr
    assert ("invalid.md" if case == "invalid" else "--expected-wi" if case == "combined" else "重複") in result.stderr


def test_cli_checks_initial_plan_set_once(repo: tuple[pathlib.Path, str], monkeypatch: pytest.MonkeyPatch) -> None:
    """初回に分割した3計画を1回の公開CLIで確かめ、各計画の構造とレーンのWI集合が一致すれば成功する。

    先行計画の逐次指定ではまだ確かめていない後続計画のWIが集合に入らず途中の計画で失敗するため、集合を1回で受け取る。
    """
    work_dir, _base = repo
    plans = [_write_set_plan(work_dir, f"plan-{index}.md", awi) for index, awi in enumerate(_SET_AWIS)]
    code, stderr = _run_set_cli(work_dir, monkeypatch, plans, _set_decisions(*_SET_AWIS))
    assert code == 0, stderr
    assert not stderr


@pytest.mark.parametrize(
    ("case", "expected_fragments"),
    [
        ("broken-structure", ("plan-1.md: ",)),
        ("missing-wi", ("欠落=['20260831-000000-104.md']",)),
        ("extra-wi", ("余剰=['20260831-000000-103.md']",)),
        ("duplicate-wi", ("計画間で関連WIが重複する", "20260831-000000-101.md")),
        ("missing-reason", ("plan-2.md: 人間由来行の根拠がない",)),
    ],
)
def test_cli_plan_set_reports_failing_plan_or_wi(
    repo: tuple[pathlib.Path, str],
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    expected_fragments: tuple[str, ...],
) -> None:
    """集合の任意の1計画の構造不備と、WIの不足・余分・計画間の重複・根拠の欠落を、原因の計画かWIを示して失敗にする。"""
    work_dir, _base = repo
    awis: list[str] = list(_SET_AWIS)
    selected: list[str] = list(_SET_AWIS)
    if case == "duplicate-wi":
        awis[2] = awis[0]
        selected = selected[:2]
    plans = [_write_set_plan(work_dir, f"plan-{index}.md", awi) for index, awi in enumerate(awis)]
    if case == "broken-structure":
        text = plans[1].read_text(encoding="utf-8")
        plans[1].write_text(text.replace(f"## {_plan_format.PLAN_H2_TERMINATION}\n", "", 1), encoding="utf-8")
    elif case == "missing-wi":
        selected.append("20260831-000000-104.md")
    elif case == "extra-wi":
        selected = selected[:2]
    elif case == "missing-reason":
        text = plans[2].read_text(encoding="utf-8")
        plans[2].write_text(text.replace(_plan_fixture.WI_ACTION_REASON, "-", 1), encoding="utf-8")
    code, stderr = _run_set_cli(work_dir, monkeypatch, plans, _set_decisions(*selected))
    assert code == 1, stderr
    for fragment in expected_fragments:
        assert fragment in stderr, stderr


def test_cli_plan_set_excludes_resumed_and_includes_prior_plan(
    repo: tuple[pathlib.Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """再開位置を持つ項目を集合から外し、凍結済み先行計画の関連WIを和集合へ含めて判定する。"""
    work_dir, _base = repo
    prior = _write_set_plan(work_dir, "prior.md", _SET_AWIS[0])
    plans = [_write_set_plan(work_dir, f"plan-{index}.md", awi) for index, awi in enumerate(_SET_AWIS[1:])]
    decisions = [*_set_decisions(*_SET_AWIS), _RESUMED_DECISION]
    code, stderr = _run_set_cli(work_dir, monkeypatch, plans, decisions, "--prior-plan", str(prior))
    assert code == 0, stderr
    assert not stderr


def test_cli_plan_set_rejects_duplicate_paths(repo: tuple[pathlib.Path, str], monkeypatch: pytest.MonkeyPatch) -> None:
    """確かめる計画どうし、確かめる計画と先行計画に同じファイルを指定した入力は入力誤り（終了コード2）にする。"""
    work_dir, _base = repo
    plans = [_write_set_plan(work_dir, f"plan-{index}.md", awi) for index, awi in enumerate(_SET_AWIS[:2])]
    code, stderr = _run_set_cli(work_dir, monkeypatch, [*plans, plans[0]], _set_decisions(*_SET_AWIS[:2]))
    assert code == 2, stderr
    code, stderr = _run_set_cli(work_dir, monkeypatch, plans, _set_decisions(*_SET_AWIS[:2]), "--prior-plan", str(plans[1]))
    assert code == 2, stderr


def test_cli_plan_set_accepts_agent_rule_path_in_any_plan(
    repo: tuple[pathlib.Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """返却予定のエージェント向け文書は、その文書を変更する計画にだけあれば受理し、どの計画にも無ければ失敗にする。"""
    work_dir, _base = repo
    plans = [_write_set_plan(work_dir, f"plan-{index}.md", awi) for index, awi in enumerate(_SET_AWIS[:2])]
    documented = "agent-basics/skills/search/SKILL.md"
    text = plans[1].read_text(encoding="utf-8")
    plans[1].write_text(text.replace("対象の公開契約を更新する。", f"`{documented}`を更新する。", 1), encoding="utf-8")
    decisions = _set_decisions(*_SET_AWIS[:2])
    code, stderr = _run_set_cli(work_dir, monkeypatch, plans, decisions, "--agent-rule-path", documented)
    assert code == 0, stderr
    missing = "agent-toolkit/skills/" + "absent-skill/SKILL.md"
    code, stderr = _run_set_cli(work_dir, monkeypatch, plans, decisions, "--agent-rule-path", missing)
    assert code == 1, stderr
    assert f"どの計画の本文にもない: {missing}" in stderr


@pytest.mark.parametrize(("total_lines", "warned"), [(1200, False), (1201, True)])
def test_cli_plan_set_keeps_line_warning_boundary_per_plan(
    repo: tuple[pathlib.Path, str], monkeypatch: pytest.MonkeyPatch, total_lines: int, warned: bool
) -> None:
    """集合の中でも行数の警告を計画ごとに判定し、1200行は警告せず1201行は対象の計画を示して警告する。"""
    work_dir, _base = repo
    base_lines = len(_write_set_plan(work_dir, "probe.md", _SET_AWIS[0]).read_text(encoding="utf-8").splitlines())
    (work_dir / "probe.md").unlink()
    plans = [
        _write_set_plan(work_dir, "plan-0.md", _SET_AWIS[0], padding=total_lines - base_lines + 1),
        _write_set_plan(work_dir, "plan-1.md", _SET_AWIS[1]),
    ]
    assert len(plans[0].read_text(encoding="utf-8").splitlines()) == total_lines
    code, stderr = _run_set_cli(work_dir, monkeypatch, plans, _set_decisions(*_SET_AWIS[:2]))
    assert code == 0, stderr
    assert (f"[warn] {plans[0]}: 計画の行数が閾値を超えている" in stderr) is warned, stderr
    assert str(plans[1]) not in stderr


__all__ = ["fixture_repo"]
