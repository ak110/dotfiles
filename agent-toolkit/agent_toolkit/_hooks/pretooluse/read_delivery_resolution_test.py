"""宣言一覧の解決再利用をhookの要求単位で検証する。"""

import contextlib
import importlib
import json
import os
import pathlib
import subprocess
import sys

import markdown_it
import pytest

from agent_toolkit._common import read_triggers
from agent_toolkit._hooks.pretooluse import read_delivery, read_delivery_guidance, task_document_launch
from agent_toolkit._testing import pretooluse_support


def test_declarations_preserve_order_and_refresh_next_request(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """再利用の有無で宣言の順序と内容を保ち、次要求では転送先の変更を反映する。"""
    root = tmp_path / "agent-toolkit"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin/plugin.json").write_text('{"name":"agent-toolkit"}', encoding="utf-8")
    skill = root / "skills/sample/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "| 条件 | 全文読む資料 | 契機 | 配送操作 |\n| --- | --- | --- | --- |\n"
        "| 前 | `redirect.md` | 操作 | `tool:Judge` |\n"
        "| 後 | `redirect.md` | 操作 | `tool:Read` |\n",
        encoding="utf-8",
    )
    first, second, redirect = (skill.parent / name for name in ("first.md", "second.md", "redirect.md"))
    first.write_text("# 一\n", encoding="utf-8")
    second.write_text("# 二\n", encoding="utf-8")
    redirect.write_text(f"<!-- read-source: {'agent-toolkit'}:sample/first.md -->\n", encoding="utf-8")
    monkeypatch.setattr(read_delivery.plugin_resources, "plugin_root", lambda: root)
    monkeypatch.setattr(read_delivery, "sibling_plugin_root", lambda *_args: None)
    monkeypatch.setattr(read_triggers, "document_sources", lambda *_args: [skill])
    with monkeypatch.context() as baseline:
        baseline.setattr(read_triggers, "resolution_scope", contextlib.nullcontext)
        expected = read_delivery.declarations(str(tmp_path))
    actual = read_delivery.declarations(str(tmp_path))
    assert actual == expected
    assert len(actual) == 2 and all(row.targets == (read_triggers.ReadTarget(first),) for row in actual)
    redirect.write_text(f"<!-- read-source: {'agent-toolkit'}:sample/second.md -->\n", encoding="utf-8")
    refreshed = read_delivery.declarations(str(tmp_path))
    assert [row.operations for row in refreshed] == [row.operations for row in actual]
    assert all(row.targets == (read_triggers.ReadTarget(second),) for row in refreshed)


def test_public_small_operation_does_not_resolve_unselected_material_or_initialize_markdown(tmp_path, monkeypatch):
    def unexpected_initialization(*_args, **_kwargs):
        pytest.fail("非該当の資料解決またはMarkdown初期化を実行した")

    try:
        with monkeypatch.context() as cold:
            cold.setattr(markdown_it, "MarkdownIt", unexpected_initialization)
            importlib.reload(task_document_launch)
        with monkeypatch.context() as operation:
            operation.setattr(read_triggers, "material_targets", unexpected_initialization)
            operation.setattr(task_document_launch, "_markdown", unexpected_initialization)
            result = pretooluse_support.run_hook_with_notice_details(
                "pretooluse",
                {
                    "session_id": "small-operation",
                    "cwd": str(tmp_path),
                    "tool_name": "Bash",
                    "tool_input": {"command": "ls"},
                },
            )
        assert result.returncode == 0 and not result.stderr
        assert "想定外のエラーが起きた" not in result.stdout
    finally:
        importlib.reload(task_document_launch)


def test_cold_small_operation_does_not_initialize_tool_only_dependencies(tmp_path, host_environ):
    """依存名を固定せず、非該当チェッカーから実行されたモジュール初期化を観測する。"""
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import contextlib,io,json,pathlib,sys\n"
        "initialized = set()\n"
        "checkers = {'agent_toolkit._hooks.pretooluse.task_document_launch', "
        "'agent_toolkit._hooks.pretooluse.content_checks'}\n"
        "def profile(frame, event, arg):\n"
        "    name = frame.f_globals.get('__name__', '')\n"
        "    if event != 'call' or frame.f_code.co_name != '<module>' "
        "or name.startswith(('agent_toolkit', 'importlib', '_frozen_importlib')):\n"
        "        return\n"
        "    parent = frame.f_back\n"
        "    while parent is not None:\n"
        "        if parent.f_globals.get('__name__') in checkers:\n"
        "            initialized.add(name)\n"
        "            break\n"
        "        parent = parent.f_back\n"
        "sys.setprofile(profile)\n"
        "from agent_toolkit import hook\n"
        "payload = {'session_id':'cold-small-operation','cwd':sys.argv[1],"
        "'tool_name':'Bash','tool_input':{'command':'ls'}}\n"
        "sys.stdin = io.TextIOWrapper(io.BytesIO(json.dumps(payload).encode('utf-8')), encoding='utf-8')\n"
        "output, errors = io.StringIO(), io.StringIO()\n"
        "with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):\n"
        "    rc = hook.main(['pretooluse'])\n"
        "sys.setprofile(None)\n"
        "print(json.dumps({'returncode':rc,'stdout':output.getvalue(),'stderr':errors.getvalue(),"
        "'initialized':sorted(initialized)}))\n",
        encoding="utf-8",
    )
    environment = host_environ()
    # Python自体の起動前提だけを復元し、状態と設定は親試験の隔離先を引き継ぐ。
    environment.update(os.environ)
    result = subprocess.run(
        [sys.executable, str(probe), str(tmp_path)],
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["returncode"] == 0 and not observed["stderr"], observed
    assert "想定外のエラーが起きた" not in observed["stdout"], observed
    assert not observed["initialized"], observed


def test_unselected_material_does_not_replace_selected_acquisition_failure(tmp_path, monkeypatch):
    source = tmp_path / "SKILL.md"
    target = tmp_path / "selected.md"
    target.write_text("# 必要な資料\n", encoding="utf-8")
    source.write_text(
        "| 条件 | 全文読む資料 | 契機 | 配送操作 |\n| --- | --- | --- | --- |\n"
        "| 編集時 | `selected.md` | 操作 | `tool:Write` |\n"
        "| 委譲時 | `missing.md` | 操作 | `tool:Agent` |\n",
        encoding="utf-8",
    )
    rows = read_triggers.parse_tables(source, tmp_path, defer_material=True)
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: rows)
    original = read_delivery.acquisition_step

    def acquisition_step(selected, *args, **kwargs):
        if selected.path == target:
            raise PermissionError("選択資料の取得失敗")
        return original(selected, *args, **kwargs)

    monkeypatch.setattr(read_delivery_guidance, "acquisition_step", acquisition_step)
    result = pretooluse_support.run_hook_with_notice_details(
        "pretooluse",
        {
            "session_id": "selected-acquisition-failure",
            "cwd": str(tmp_path),
            "tool_name": "Write",
            "tool_input": {"file_path": str(tmp_path / "output.txt"), "content": "本文"},
        },
    )
    assert result.returncode == 0 and not result.stderr
    assert "選択資料の取得失敗" in result.stdout
    assert "想定外のエラーが起きた" not in result.stdout and "missing.md" not in result.stdout


def _project_skills(project: pathlib.Path, rows: str) -> tuple[pathlib.Path, pathlib.Path]:
    """読込表がリポジトリの根からのパスで別スキルを指す、プロジェクトのスキルの配置を作る。

    資料列の書き方は、宣言をリポジトリ相対パスで書く規約を持つユーザーのプロジェクトの実例に合わせた。
    行は`tool:Read`の操作で選ばれるため、どの読取でもhookが資料を解決する。
    """
    (project / ".git").mkdir(parents=True, exist_ok=True)
    backend = project / ".claude/skills/backend/SKILL.md"
    database = project / ".claude/skills/database/SKILL.md"
    for path in (backend, database):
        path.parent.mkdir(parents=True, exist_ok=True)
    database.write_text("# データベース\n", encoding="utf-8")
    backend.write_text(
        "# バックエンド\n\n| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |\n| --- | --- | --- | --- |\n" + rows,
        encoding="utf-8",
    )
    (project / "notes.txt").write_text("作業メモ\n", encoding="utf-8")
    return backend.resolve(), database.resolve()


def _pretooluse(project: pathlib.Path, session_id: str, tool_name: str, tool_input: dict) -> subprocess.CompletedProcess[str]:
    return pretooluse_support.run_hook_with_notice_details(
        "pretooluse",
        {"session_id": session_id, "cwd": str(project), "tool_name": tool_name, "tool_input": tool_input},
    )


def _write_notes(project: pathlib.Path, session_id: str) -> subprocess.CompletedProcess[str]:
    """`tool:Write`の行を選ぶ書込で、読込案内の配送を観測する。"""
    return _pretooluse(project, session_id, "Write", {"file_path": str(project / "notes.txt"), "content": "追記\n"})


_DATABASE_ROW = "| DBを書く前 | `.claude/skills/database/SKILL.md` | 操作 | `tool:Write` |\n"


@pytest.mark.parametrize("nested", [False, True])
def test_project_skill_resolves_repository_relative_material(tmp_path: pathlib.Path, nested: bool) -> None:
    """`.claude/`で始まる資料は、最も近い`.claude/skills`を持つリポジトリの根から解決し、hookを止めない。"""
    main = tmp_path / "main"
    if nested:
        # 主checkout側にも同名のスキルを置き、入れ子のworktree側の実体へ解決することを確かめる。
        _project_skills(main, "")
    project = main / ".claude/worktrees/lane" if nested else main
    backend, database = _project_skills(project, _DATABASE_ROW)
    rows = read_triggers.parse_tables(backend, read_delivery.plugin_resources.plugin_root(), project=str(project))
    assert [row.documents for row in rows] == [(database,)]
    result = _pretooluse(project, f"repository-relative-{nested}", "Read", {"file_path": str(backend)})
    assert result.returncode == 0 and not result.stderr
    assert "想定外のエラーが起きた" not in result.stdout


def test_project_skill_missing_material_row_is_skipped_with_single_warning(tmp_path: pathlib.Path) -> None:
    """ユーザー側の表で、改名で消えたスキルを指す行だけを除き、他の行を保ち、同じ会話で警告を繰り返さない。"""
    project = tmp_path / "project"
    backend, database = _project_skills(
        project, _DATABASE_ROW + "| 移行の前 | `.claude/skills/retired/SKILL.md` | 操作 | `tool:Write` |\n"
    )
    with read_triggers.resolution_scope():
        rows = read_triggers.parse_tables(
            backend, read_delivery.plugin_resources.plugin_root(), project=str(project), defer_material=True
        )
        assert [row.targets for row in rows] == [(read_triggers.ReadTarget(database),), ()]
        issues = read_triggers.declaration_issues()
    assert len(issues) == 1 and issues[0].startswith(f"{backend}:6: 資料が存在しない")
    assert str(backend.parents[1] / "retired/SKILL.md") in issues[0]
    first = _write_notes(project, "missing-material")
    assert first.returncode == 0 and not first.stderr
    assert "想定外のエラーが起きた" not in first.stdout
    assert f"{backend}:6" in first.stdout and f"{backend}:5" not in first.stdout
    # 不備の行を除いた後も、同じ表の正常な行の読込案内は同じ呼び出しで配送される。
    assert "`Skill`で`database`を起動せよ" in first.stdout
    repeated = _write_notes(project, "missing-material")
    assert repeated.returncode == 0 and f"{backend}:6" not in repeated.stdout
