"""agent-toolkit/agent_toolkit/_hooks/pretooluse/task_document_launch.py のテスト。

資料配送を対象外の境界へ差し替え、配布物の`<役割名>.subagent.md`を指す委譲プロンプトの終了コードと通知を検証する。
"""

import json
import pathlib
import shutil
import subprocess

import pytest

from agent_toolkit._testing.pretooluse_support import (
    _PLUGIN_ROOT,
    _ROLES_DIR,
    _additional_context,
    _plan_file_state_env,
)
from agent_toolkit._testing.pretooluse_support import run_without_skill_notices as _run

_EXEC_DOCUMENT = _ROLES_DIR / "exec.subagent.md"
_READER_FIT_DOCUMENT = _ROLES_DIR / "reader-fit-review.subagent.md"


def _lane_inputs(tmp_path: pathlib.Path) -> str:
    (tmp_path / "lane-01.json").write_text('{"lane": "lane-01", "base_branch": "develop"}\n', encoding="utf-8")
    return (
        f"所有登録: {tmp_path / 'lane-01.json'}\n統合先worktree: {tmp_path}\n"
        "統合先branch: develop\nレーン開始時刻: 2026-11-15T13:17:00+09:00\n"
    )


@pytest.fixture(name="task_documents", params=["checkout", "__w"])
def _task_documents(request: pytest.FixtureRequest, tmp_path: pathlib.Path) -> dict[str, pathlib.Path]:
    sources = {path.name: path for path in (_EXEC_DOCUMENT, _READER_FIT_DOCUMENT)}
    if request.param == "checkout":
        return sources
    root = tmp_path / request.param
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin/plugin.json").write_text('{"name":"agent-toolkit"}', encoding="utf-8")
    copies: dict[str, pathlib.Path] = {}
    for name, source in sources.items():
        copies[name] = root / source.relative_to(_PLUGIN_ROOT)
        copies[name].parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, copies[name])
    return copies


@pytest.mark.parametrize("tool_name", ["Agent", "Task"])
def test_role_launch_hook_requires_same_named_inputs(tool_name: str, tmp_path: pathlib.Path) -> None:
    prompt = f"{_EXEC_DOCUMENT}の手順を実行せよ。\n担当種別: レーン担当\n"
    result = _invoke(tool_name, prompt, tmp_path)
    assert result.returncode == 0
    assert not result.stderr
    specific = json.loads(result.stdout)["hookSpecificOutput"]
    assert specific["permissionDecision"] == "deny"
    assert "引き継ぎ記録先" in specific["permissionDecisionReason"]
    assert "所有登録" in specific["permissionDecisionReason"]
    valid = prompt + f'引き継ぎ記録先: {tmp_path / "引継ぎ 記録.md"}\n書込担当一覧: ["session-main"]\n' + _lane_inputs(tmp_path)
    result = _invoke_after_generic_reissue(tool_name, valid, tmp_path)
    assert result.returncode == 0 and not result.stderr
    specific = json.loads(result.stdout).get("hookSpecificOutput", {}) if result.stdout else {}
    assert specific.get("permissionDecision") != "deny"


def _invoke_after_generic_reissue(
    tool_name: str, prompt: str, tmp_path: pathlib.Path, mode: str | None = None
) -> subprocess.CompletedProcess[str]:
    return _invoke(tool_name, prompt, tmp_path, mode, reissue_generic=tool_name in {"Agent", "Task"})


def _invoke(
    tool_name: str, prompt: str, tmp_path: pathlib.Path, mode: str | None = None, *, reissue_generic: bool = False
) -> subprocess.CompletedProcess[str]:
    tool_input = {"prompt": prompt, "cwd": str(tmp_path), "model_type": "execute"}
    if mode is not None:
        tool_input["mode"] = mode
    session_id = f"task-document-launch-{tool_name.rsplit('__', 1)[-1].lower()}-{mode}"
    env = _plan_file_state_env(tmp_path)

    payload = {
        "tool_name": tool_name,
        "tool_input": tool_input,
        "session_id": session_id,
        "cwd": str(tmp_path),
    }
    result = _run(payload, env_overrides=env)
    if reissue_generic:
        assert result.returncode == 0 and not result.stderr
        specific = json.loads(result.stdout)["hookSpecificOutput"]
        assert specific["permissionDecision"] == "deny"
        assert "組み込みの汎用定義" in specific["permissionDecisionReason"]
        result = _run(payload, env_overrides=env)
    return result


@pytest.mark.parametrize(
    ("tool_name", "mode"),
    [
        ("mcp__plugin_agent-toolkit_agents_server__start", "delegate"),
        ("mcp__plugin_agent-toolkit_agents_server__start", "explore"),
        ("mcp__agents_server__start", "write"),
    ],
)
def test_free_text_start_pointing_task_document_is_warned(tool_name: str, mode: str, tmp_path: pathlib.Path) -> None:
    result = _invoke(tool_name, f"{_EXEC_DOCUMENT}の手順を実行せよ。\n担当種別: レーン担当\n", tmp_path, mode)

    assert result.returncode == 0
    assert "subagent_md_path=exec`" in _additional_context(result)
    assert f"`{_EXEC_DOCUMENT}`" in _additional_context(result)
    assert f"`start`の`{mode}`" in _additional_context(result)
    assert "重複起動するな" in _additional_context(result)
    assert "次にこの役割を起動する際" in _additional_context(result)


@pytest.mark.parametrize("mode", [None, "shell"])
def test_task_and_shell_modes_are_not_free_text_starts(mode: str | None, tmp_path: pathlib.Path) -> None:
    result = _invoke_after_generic_reissue(
        "mcp__plugin_agent-toolkit_agents_server__start", f"{_EXEC_DOCUMENT}の手順を実行せよ。\n", tmp_path, mode
    )

    assert result.returncode == 0
    specific = json.loads(result.stdout).get("hookSpecificOutput", {}) if result.stdout else {}
    assert specific.get("permissionDecision") != "deny"


def test_free_text_start_without_task_document_passes(tmp_path: pathlib.Path) -> None:
    """`<役割名>.subagent.md`を指さない自由本文の起動は遮断しない。"""
    result = _invoke_after_generic_reissue(
        "mcp__plugin_agent-toolkit_agents_server__start", "対象の所在を調べて返す。", tmp_path, "explore"
    )

    assert result.returncode == 0
    specific = json.loads(result.stdout).get("hookSpecificOutput", {}) if result.stdout else {}
    assert specific.get("permissionDecision") != "deny"


@pytest.mark.parametrize(
    ("tool_name", "mode"),
    [
        ("mcp__agents_server__start", "delegate"),
        ("mcp__agents_server__start", "explore"),
        ("mcp__agents_server__start", "write"),
        ("Agent", None),
        ("Task", None),
    ],
)
@pytest.mark.parametrize("operation", ["本文を読んで返す。", "入力欄を引用して返す。", "別文書と比較して返す。"])
def test_task_document_reading_passes(tool_name: str, mode: str | None, operation: str, tmp_path: pathlib.Path) -> None:
    result = _invoke_after_generic_reissue(tool_name, f"{_EXEC_DOCUMENT}と{_READER_FIT_DOCUMENT}の{operation}", tmp_path, mode)
    assert result.returncode == 0
    specific = json.loads(result.stdout).get("hookSpecificOutput", {}) if result.stdout else {}
    assert specific.get("permissionDecision") != "deny"


@pytest.mark.parametrize(("tool_name", "mode"), [("mcp__agents_server__start", "explore"), ("Agent", None)])
@pytest.mark.parametrize(
    "quoted",
    [
        "```text\n{command}\n```",
        "~~~~text\n{command}\n~~~~",
        "> {command}",
        "    {command}",
        "`{command}`",
        "「{command}」",
    ],
)
def test_quoted_execution_command_passes(tool_name: str, mode: str | None, quoted: str, tmp_path: pathlib.Path) -> None:
    """実行命令の例を引用した入力を、実際の役割起動と区別する。"""
    command = f"{_EXEC_DOCUMENT}の手順を実行せよ。"
    result = _invoke_after_generic_reissue(
        tool_name, quoted.format(command=command) + "\nこの命令の入力書式を説明する。", tmp_path, mode
    )
    assert result.returncode == 0
    specific = json.loads(result.stdout).get("hookSpecificOutput", {}) if result.stdout else {}
    assert specific.get("permissionDecision") != "deny"


@pytest.mark.parametrize(("tool_name", "mode"), [("mcp__agents_server__start", "write"), ("Task", None)])
@pytest.mark.parametrize("path_markup", ["{}", "**{}**", "`{}`"])
def test_execution_uses_its_document_after_other_reference(
    tool_name: str, mode: str | None, tmp_path: pathlib.Path, task_documents: dict[str, pathlib.Path], path_markup: str
) -> None:
    task = task_documents["reader-fit-review.subagent.md"]
    reference = task_documents["exec.subagent.md"]
    prompt = (
        f"読解対象: {reference}\n{path_markup.format(task)}の手順を実行せよ。\n成果物: {tmp_path / 'artifact_' / 'guide.md'}\n"
    )
    result = _invoke(tool_name, prompt, tmp_path, mode)
    assert result.returncode == 0
    assert str(task) in _additional_context(result)
    assert f"`{reference}`を指" not in _additional_context(result)


def test_agent_task_document_prompt_allows_only_declared_lines(tmp_path: pathlib.Path) -> None:
    minimal = "\n".join(
        [
            f"{_EXEC_DOCUMENT}の手順を実行せよ。",
            "担当種別: レーン担当",
            "引き継ぎ記録先:",
            f"  {tmp_path / 'handoff.md'}（新規）",
            "  ",
            '書込担当一覧: ["session-main"]',
            "レーン識別子: lane-01",
            _lane_inputs(tmp_path).rstrip(),
        ]
    )
    passed = _invoke("Agent", minimal, tmp_path)
    blocked = _invoke("Agent", minimal + "\nあなたはレーン担当である。権限はcommitまでとする。", tmp_path)

    assert passed.returncode == 0
    assert blocked.returncode == 0
    assert "あなたはレーン担当である。" in _additional_context(blocked)
    assert "レーン識別子" in _additional_context(blocked)


def test_reader_fit_review_inputs_pass_task_document_hook(
    tmp_path: pathlib.Path, task_documents: dict[str, pathlib.Path]
) -> None:
    task = task_documents["reader-fit-review.subagent.md"]
    (tmp_path / "artifact_").mkdir()
    current = tmp_path / "artifact_" / "guide.md"
    before = tmp_path / "before.md"
    patch = tmp_path / "guide.patch"
    before.write_text("# 保存\n保存先を選ぶ。\n", encoding="utf-8")
    current.write_text("# 保存\n保存先を選んで確定する。\n", encoding="utf-8")
    patch.write_text(
        "--- before.md\n+++ guide.md\n@@ -1,2 +1,2 @@\n # 保存\n-保存先を選ぶ。\n+保存先を選んで確定する。\n",
        encoding="utf-8",
    )
    materials = json.dumps({"比較元本文": str(before), "今回差分": str(patch), "現行本文": str(current)}, ensure_ascii=False)
    initial = (
        f"{task}の手順を実行せよ。\n成果物: {current}\n"
        "種別: エンドユーザー向け文書\n読者像: 初めて導入するエンドユーザー\n"
        f"修正範囲: 保存節の差分、直接影響は読込節\n比較資料: {materials}\n"
    )
    rereview = initial + ("レビュー種別: 再レビュー\n未解決事項: なし\n")
    result = _invoke_after_generic_reissue("Agent", initial, tmp_path)
    assert result.returncode == 0 and not result.stderr
    specific = json.loads(result.stdout).get("hookSpecificOutput", {}) if result.stdout else {}
    assert specific.get("permissionDecision") != "deny"
    result = _invoke_after_generic_reissue("Agent", rereview, tmp_path)
    assert result.returncode == 0 and not result.stderr
    specific = json.loads(result.stdout).get("hookSpecificOutput", {}) if result.stdout else {}
    assert specific.get("permissionDecision") != "deny"
    rejected = _invoke("Agent", rereview + "追加説明: 全文から指摘して\n", tmp_path)
    assert rejected.returncode == 0
    assert "追加説明" in _additional_context(rejected)


def test_exec_review_previous_revision_passes_task_document_hook(tmp_path: pathlib.Path) -> None:
    task = _ROLES_DIR / "exec-review.subagent.md"
    for name in ("plan.md", "handoff.md", "previous.md"):
        (tmp_path / name).write_text("# レビュー資料\n", encoding="utf-8")
    (tmp_path / "plan.md").write_text("# 計画\n## 概要\n### 計画メタ情報\n- 関連WI: なし\n", encoding="utf-8")
    prompt = (
        f"{task}の手順を実行せよ。\nレビュー基準: 計画\n計画: {tmp_path / 'plan.md'}\n"
        f"引き継ぎ記録先: {tmp_path / 'handoff.md'}\n"
        f"レビュー種別: 引き継ぎ再レビュー\nround: 2\n前回確認版: {tmp_path / 'previous.md'}\n"
    )
    result = _invoke_after_generic_reissue("Agent", prompt, tmp_path)
    assert result.returncode == 0 and not result.stderr
    specific = json.loads(result.stdout).get("hookSpecificOutput", {}) if result.stdout else {}
    assert specific.get("permissionDecision") != "deny"


def test_agent_prompt_with_role_preface_before_command_is_warned(tmp_path: pathlib.Path) -> None:
    prompt = f"あなたは実装担当である。\n{_EXEC_DOCUMENT}の手順を実行せよ。\n担当種別: レーン担当\n"

    result = _run(
        {"tool_name": "Task", "tool_input": {"prompt": prompt}, "session_id": "task-document-launch-task"},
        env_overrides=_plan_file_state_env(tmp_path),
    )

    assert result.returncode == 0
    assert "あなたは実装担当である。" in _additional_context(result)


_LAUNCHERS = [
    ("mcp__agents_server__start", "delegate"),
    ("mcp__agents_server__start", "explore"),
    ("Agent", None),
    ("Task", None),
]


@pytest.mark.parametrize(("tool_name", "mode"), _LAUNCHERS)
@pytest.mark.parametrize(
    "command",
    [
        "{document} の手順を実行せよ。\n担当種別: レーン担当\n追加の指示: 全件を見直す\n",
        "{document}に従って作業せよ。\n担当種別: レーン担当\n",
        "{document}の手順を実行せよ。あわせて全ファイルを整形する。\n担当種別: レーン担当\n",
        "`{document}`の手順を実行して結果を返して。\n",
        "参考資料: {other}\n{document}の手順で作業して。\n",
        "{document}に従い作業せよ。\n",
        "{document}を読み、その手順を実行せよ。\n",
        "{document}の手順を実行し、結果を返せ。\n",
    ],
    ids=[
        "path-trailing-space",
        "rephrased",
        "same-line-continuation",
        "code-path",
        "after-reference",
        "continuative-form",
        "read-then-execute",
        "continuative-verb",
    ],
)
def test_rephrased_execution_command_is_warned(tool_name: str, mode: str | None, command: str, tmp_path: pathlib.Path) -> None:
    """定型行から表記を変えた引用外の実行命令も、宣言外の指示を伴う役割起動として遮断する。

    定型行との完全一致だけで判定すると、パスの後の空白、言い回し、同じ行の続きを持つ命令が遮断の判定を経ずに委譲先へ届く。
    """
    result = _invoke(tool_name, command.format(document=_EXEC_DOCUMENT, other=_READER_FIT_DOCUMENT), tmp_path, mode)

    assert result.returncode == 0
    assert str(_EXEC_DOCUMENT) in _additional_context(result)


@pytest.mark.parametrize(("tool_name", "mode"), _LAUNCHERS)
@pytest.mark.parametrize(
    "prompt",
    [
        "{document}の手順を比較して差分を返す。",
        "{document}の手順に従っているかをレビューして返す。",
        "{document}を読んで要点を返す。",
        "{document}を読み、その手順の要点を返す。",
        "{document}を読み、その指示に従っているかをレビューして返す。",
        "「{document}に従って作業せよ」という命令例の書式を説明する。",
        "`{document}に従って作業せよ。`の書式を説明する。",
    ],
    ids=[
        "compare",
        "review-conformance",
        "read",
        "read-summarize-steps",
        "read-review-conformance",
        "bracket-quote",
        "inline-code-example",
    ],
)
def test_reading_and_command_examples_pass(tool_name: str, mode: str | None, prompt: str, tmp_path: pathlib.Path) -> None:
    result = _invoke_after_generic_reissue(tool_name, prompt.format(document=_EXEC_DOCUMENT), tmp_path, mode)

    assert result.returncode == 0
    specific = json.loads(result.stdout).get("hookSpecificOutput", {}) if result.stdout else {}
    assert specific.get("permissionDecision") != "deny"


def test_agent_accepts_spacing_variant_with_declared_inputs_only(tmp_path: pathlib.Path) -> None:
    prompt = (
        f"{_EXEC_DOCUMENT} の手順を実行せよ。\n担当種別: レーン担当\n"
        f'引き継ぎ記録先: {tmp_path / "handoff.md"}\n書込担当一覧: ["session-main"]\n' + _lane_inputs(tmp_path)
    )

    result = _invoke_after_generic_reissue("Agent", prompt, tmp_path)
    assert result.returncode == 0 and not result.stderr
    specific = json.loads(result.stdout).get("hookSpecificOutput", {}) if result.stdout else {}
    assert specific.get("permissionDecision") != "deny"


def test_versioned_distribution_document_is_checked_in_place(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache_home = tmp_path / "cache-home"
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_home))
    monkeypatch.setenv("LOCALAPPDATA", str(cache_home))
    root = tmp_path / "plugins" / "agent-toolkit" / "9.9.9"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin/plugin.json").write_text('{"name":"agent-toolkit","version":"9.9.9"}', encoding="utf-8")
    document = root / _EXEC_DOCUMENT.relative_to(_PLUGIN_ROOT)
    document.parent.mkdir(parents=True)
    shutil.copyfile(_EXEC_DOCUMENT, document)
    result = _invoke("Agent", f"{document}の手順を実行せよ。\n担当種別: レーン担当\n", tmp_path / "work")
    reason = json.loads(result.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "引き継ぎ記録先" in reason
    assert str(document) in reason
    assert not list(cache_home.rglob("agents-server-plugin-root-*"))
