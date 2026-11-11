"""計画・選定・履歴比較の試験で共有する入力と補助処理。"""

import contextlib
import io
import pathlib
import sys
import typing

import pytest
import yaml

from agent_toolkit._plan import bundle_kinds as _bundle_kinds
from agent_toolkit._plan import locations as _plan_file
from agent_toolkit._plan import structure as _plan_format
from agent_toolkit._testing import git_repository
from agent_toolkit._testing import plan_fixture as _plan_fixture

# 検査本体はスキルのscriptsにあり、パッケージの外のため検索パスへ加えてから取り込む。
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "skills/write-plan/scripts"))
import check_plan_file  # noqa: E402  # pylint: disable=wrong-import-position,wrong-import-order,import-error

_TWO_FILE_MIGRATION = "旧二ファイル書式である。新規作成・改訂では現行の1ファイル書式へ移行する"


@pytest.fixture(name="repo")
def fixture_repo(tmp_path: pathlib.Path) -> tuple[pathlib.Path, str]:
    """計画の確認に使うGitリポジトリを作成する。"""
    git_repository.init_repository(tmp_path)
    git_repository.git_output(tmp_path, "config", "user.email", "test@example.com")
    git_repository.git_output(tmp_path, "config", "user.name", "Test")
    (tmp_path / "README.md").write_text("test\n", encoding="utf-8")
    git_repository.git_output(tmp_path, "add", "README.md")
    git_repository.git_output(tmp_path, "commit", "-qm", "base")
    return tmp_path, git_repository.git_output(tmp_path, "rev-parse", "HEAD")


def _plan(repo: pathlib.Path, base: str, *, bug: bool = False, exclusions: bool = True) -> str:
    """旧単一ファイル形式の正規計画を組み立てる。"""
    return _plan_fixture.single_file_plan(repo=repo.resolve(), base=base, bug=bug, exclusions=exclusions)


def _new_format_plan(
    repo: pathlib.Path, base: str, *, bug: bool = False, detail_name: str = "plan.detail.md"
) -> tuple[str, str]:
    """旧二ファイル形式（計画ファイル（メイン）・計画ファイル（詳細））の正規計画を組み立てて返す。"""
    work_type = "バグ対応" if bug else "通常変更"
    main = _plan_fixture.two_file_main(
        repo=repo.resolve(),
        base=base,
        detail_name=detail_name,
        work_type=work_type,
    )
    bug_section = ""
    if bug:
        bug_stem = detail_name.removesuffix(_bundle_kinds.DETAIL.suffix)
        bug_section = _plan_fixture.bug_reference_section((repo / f"{bug_stem}.bugs.md").resolve())
    return main, _plan_fixture.two_file_detail(bug_section=bug_section)


def human_new_format_plan(repo: pathlib.Path) -> tuple[str, str]:
    """新規作成用の人間向け計画ファイル（メイン）・計画ファイル（詳細）fixtureを返す。"""
    return _plan_fixture.human_main(repo=repo.resolve()), _plan_fixture.human_detail()


def _check_new(
    repo: pathlib.Path,
    main_content: str,
    detail_content: str,
    *,
    plan_name: str = "plan.md",
    create_bug_file: bool = True,
    bug_file_content: str | None = None,
    reject_migration_warnings: bool = False,
    reject_progress_log_rows: bool = False,
) -> tuple[list[str], list[str]]:
    """新書式の計画（計画ファイル（メイン）・計画ファイル（詳細））を一時ファイルへ保存し、基準を満たすか確かめる。"""
    path = repo / plan_name
    path.write_text(main_content, encoding="utf-8")
    detail_path = repo / f"{path.stem}.detail.md"
    detail_path.write_text(detail_content, encoding="utf-8")
    reference = _plan_format.extract_bug_file_reference(detail_content)
    if create_bug_file and reference is not None:
        if _plan_file.is_plan_adjunct_reference(reference):
            bug_path = _plan_file.resolve_plan_adjunct_reference(reference, plan_path=path)
        else:
            bug_path = pathlib.Path(reference)
        bug_path.write_text(bug_file_content or _plan_fixture.bug_file(), encoding="utf-8")
    return check_plan_file.check(
        path,
        repo,
        reject_migration_warnings=reject_migration_warnings,
        reject_progress_log_rows=reject_progress_log_rows,
    )


def _check(repo: pathlib.Path, content: str) -> tuple[list[str], list[str]]:
    """計画を一時ファイルへ保存し、基準を満たすか確かめる。"""
    path = repo / "plan.md"
    path.write_text(content, encoding="utf-8")
    return check_plan_file.check(path, repo)


def _run_plan_cli(
    work_dir: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    plans: typing.Sequence[pathlib.Path],
    decisions: list[dict[str, str]],
    wi_names: typing.Iterable[str],
    *extra: str,
) -> tuple[int, str]:
    """計画を公開CLIへ1回渡し、終了コードと標準エラーを返す。

    CLIは由来を比べるWIファイルをprivate-notesから探すため、実行環境の実物を参照しないよう
    一時のprivate-notesへ計画の人間由来行が指すWIファイルを置く。
    """
    inbox = work_dir / "private-notes" / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    for name in wi_names:
        (inbox / name).write_text("---\nstatus: inbox\n---\n\n# 要求\n\n本文。\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(work_dir / "private-notes"))
    selection_path = work_dir / "selection.yaml"
    selection_path.write_text(yaml.safe_dump({"decisions": decisions}, allow_unicode=True), encoding="utf-8")
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        code = check_plan_file.main(
            [
                "--reject-migration-warnings",
                "--selection-file",
                str(selection_path),
                "--lane",
                "lane-01",
                "--work-dir",
                str(work_dir),
                *extra,
                *(str(plan) for plan in plans),
            ]
        )
    return code, stderr.getvalue()


_RESUMED_AWI = "20260831-000000-002.md"


# メインがAWI本文の再開記録から返す観測のみの再開位置（`selection-format.md`「選定結果の書式」の書式）。
_OBSERVATION_RESUME = "反映後の観測だけが残る（再開記録: AWI本文の最後の`## 反映後の観測の再開記録`、計画: {plan}）"


_RESUMED_DECISION = {
    "awi": _RESUMED_AWI,
    "lane": "lane-01",
    "再開位置": _OBSERVATION_RESUME.format(plan="2026/10/01-1526_process-wi_レーン01.md"),
}
