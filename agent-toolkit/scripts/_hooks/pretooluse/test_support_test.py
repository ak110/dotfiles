# ruff: noqa: F401,I001
# pylint: disable=unused-import
"""agent-toolkit/scripts/_hooks/pretooluse.py のテスト。

subprocessで起動しexit code・stderr・stdoutを検証する。
"""

import ast
import json
import os
import pathlib
import re
import shlex
import subprocess
import tempfile
import textwrap
import time
from collections.abc import Callable

from _atk import managed_temp as _managed_temp
import hook
import pytest
from _testing import fork_runner as _fork_runner
from _testing.helpers import SESSION_STATE_FILENAME_TEMPLATE
from pyfltr.colloquial import check as _colloquial_check


_SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "hook.py"


_PLUGIN_MANIFEST = pathlib.Path(__file__).resolve().parents[3] / ".claude-plugin" / "plugin.json"


_MARKETPLACE_MANIFEST = pathlib.Path(__file__).resolve().parents[4] / ".claude-plugin" / "marketplace.json"


_SHARE_DIR = pathlib.Path(__file__).resolve().parents[3] / "share"


_SECRETS_COPY_GUIDANCE = "Bashの`cp`で原本を複製"


_SECRETS_VALUE_EDIT_GUIDANCE = "Bashの`echo ... >>`または`sed -i`"


_EXECUTE_REVIEW_TASK_NAMES: tuple[str, ...] = ("exec-review.subagent.md",)


_NOTICE_PREFIX = "[auto-generated: agent-toolkit/pretooluse][warn] "


_NOTICE_SUFFIX = " （自動生成のhook通知。行動する前に会話コンテキストとの関連性を評価すること。）"


def _run(payload: object, env_overrides: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    return _fork_runner.run_script(_SCRIPT, argv=("pretooluse",), input=text, env=env)


def _run_posttooluse(payload: object, env_overrides: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """PostToolUse hookを同じ一時状態ディレクトリで実行する。"""
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    env = os.environ.copy()
    env.update(env_overrides)
    return _fork_runner.run_script(_SCRIPT, argv=("posttooluse",), input=text, env=env)


def _additional_context(result: subprocess.CompletedProcess[str]) -> str:
    """stdoutのJSONから`hookSpecificOutput.additionalContext`を取り出す。"""
    stdout = result.stdout.strip()
    if not stdout:
        return ""
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return ""
    context = payload.get("hookSpecificOutput", {}).get("additionalContext")
    return context if isinstance(context, str) else ""


def _notice_body(context: str) -> str:
    """標準プレフィックスとサフィックスを検証し、通知本文を返す。"""
    assert context.startswith(_NOTICE_PREFIX)
    assert context.endswith(_NOTICE_SUFFIX)
    return context.removeprefix(_NOTICE_PREFIX).removesuffix(_NOTICE_SUFFIX)


def _agent_messages(result: subprocess.CompletedProcess[str]) -> str:
    """コーディングエージェントへ届く本文を連結して返す。

    Claude Codeの公式仕様では、exit 0で終了したフックのstderrはデバッグログだけに送られ
    コーディングエージェントへ渡らない。そのためstderrはexit 2（ブロック）の場合だけ含める。
    """
    stderr = result.stderr if result.returncode == 2 else ""
    return f"{stderr}\n{_additional_context(result)}"


def _stderr_warn_offenders(source: str) -> list[int]:
    """warn通知をstderrへ出力する箇所の行番号を返す。

    検出するのは`print(..., file=sys.stderr)`の引数へwarn通知が現れる2形とする。
    1つは`tag="warn"`の呼び出しを引数へ直接書く形、
    もう1つはwarn通知を変数へ束縛してから引数へ渡す形である。
    後者には束縛元がwarn通知を返す関数の呼び出しである場合も含め、
    定義順に依存しないよう関数名と変数名の収集を不動点まで繰り返す。
    warn通知を関数の引数として別のヘルパーへ渡し、渡された先のヘルパーがstderrへ出力する
    間接的な受け渡しは検出しない。仮引数へ渡る値の由来を追跡しないためである。
    """
    tree = ast.parse(source)

    def has_warn_tag(node: ast.AST) -> bool:
        return any(
            isinstance(inner, ast.keyword) and inner.arg == "tag" and getattr(inner.value, "value", None) == "warn"
            for inner in ast.walk(node)
        )

    # warn通知を保持する変数名と、warn通知を返す関数名。
    warn_names: set[str] = set()
    warn_functions: set[str] = set()

    def is_warn_value(node: ast.expr) -> bool:
        if has_warn_tag(node):
            return True
        if isinstance(node, ast.Name) and node.id in warn_names:
            return True
        return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in warn_functions

    while True:
        before = (len(warn_names), len(warn_functions))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and is_warn_value(node.value):
                warn_names.update(target.id for target in node.targets if isinstance(target, ast.Name))
            elif (
                isinstance(node, ast.AnnAssign)
                and node.value is not None
                and is_warn_value(node.value)
                and isinstance(node.target, ast.Name)
            ):
                warn_names.add(node.target.id)
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and any(
                isinstance(inner, ast.Return) and inner.value is not None and is_warn_value(inner.value)
                for inner in ast.walk(node)
            ):
                warn_functions.add(node.name)
        if (len(warn_names), len(warn_functions)) == before:
            break

    offenders: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id != "print":
            continue
        if not any(keyword.arg == "file" and ast.unparse(keyword.value) == "sys.stderr" for keyword in node.keywords):
            continue
        if has_warn_tag(node) or any(is_warn_value(arg) for arg in node.args):
            offenders.append(node.lineno)
    return sorted(set(offenders))


def _write_session_state(state_dir: pathlib.Path, session_id: str, state: dict) -> None:
    path = state_dir / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=session_id)
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def _read_session_state(state_dir: pathlib.Path, session_id: str) -> dict:
    path = state_dir / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=session_id)
    return json.loads(path.read_text(encoding="utf-8"))


def _home_path() -> str:
    """検査対象プロセスが解決するホームディレクトリを実行時に返す。

    `_run`が起動するフックは呼び出し時点の環境変数からホームを解決するため、
    テスト側も同じ時点で解決する。収集時に評価したクラス変数は、実行環境のホームを
    差し替えるfixtureの適用前の値を保持するため、フックが解決する値と一致しない。
    """
    return str(pathlib.Path.home())


def _stage_model_env(tmp_path: pathlib.Path, value: str) -> dict[str, str]:
    """工程別モデル設定を隔離したXDG設定ディレクトリへ保存する。"""
    config_home = tmp_path / "config"
    config_dir = config_home / "agent-toolkit"
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text(
        json.dumps({"execute_review_model": value}),
        encoding="utf-8",
    )
    return {"XDG_CONFIG_HOME": str(config_home)}


@pytest.fixture(name="deny_substring")
def _deny_substring_fixture() -> str:
    """辞書ファイルから口語表現の検出サンプルを生成する。

    テスト本体へ口語表現を直接書かないため、allowlistの最初のオーバーラップサンプルから
    denylist部分文字列を抽出する。本番ロジック`_colloquial_check.load_patterns`と同じ解釈で
    タブ区切りの置換候補列を除外する。
    """
    deny_patterns = [pattern for pattern, _ in _colloquial_check.load_patterns(_colloquial_check.DENY_PATH)]
    for raw in _colloquial_check.ALLOW_PATH.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        sample = re.sub(r"\[([^\]]+)\]", lambda m: m.group(1)[0], stripped)
        for pattern in deny_patterns:
            match = pattern.search(sample)
            if match:
                return match.group(0)
    pytest.skip("no overlap between denylist and allowlist; cannot generate test sample")
    return ""  # unreachable


def _user_facing_payload(field: str, value: str) -> dict:
    """指定したユーザー向け本文欄だけへ値を設定したpayloadを返す。"""
    if field == "plan":
        return {"tool_name": "ExitPlanMode", "tool_input": {"plan": value}}
    option = {"label": "選択肢", "description": "説明文"}
    question = {"question": "質問文", "header": "見出し", "options": [option]}
    if field in {"question", "header"}:
        question[field] = value
    else:
        option[field] = value
    return {"tool_name": "AskUserQuestion", "tool_input": {"questions": [question]}}


def _plan_file_state_env(
    tmp_path: pathlib.Path,
    home_dir: pathlib.Path | None = None,
) -> dict[str, str]:
    env = {"TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path)}
    if home_dir is not None:
        env["HOME"] = str(home_dir)
        env["USERPROFILE"] = str(home_dir)
    return env


def _make_plan_file(home_dir: pathlib.Path, name: str = "test.md") -> pathlib.Path:
    plans = home_dir / ".claude" / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    plan = plans / name
    plan.write_text("# t\n", encoding="utf-8")
    return plan


def _make_private_notes_plan_file(private_notes: pathlib.Path, name: str = "test.md") -> pathlib.Path:
    """新しいprivate-notes計画root配下の計画ファイルを作成する。"""
    plans = private_notes / "plans" / "2026" / "08"
    plans.mkdir(parents=True, exist_ok=True)
    plan = plans / name
    plan.write_text("# t\n", encoding="utf-8")
    return plan


def _write_tmp_file(tmp_path: pathlib.Path, relative_path: str, content: str) -> pathlib.Path:
    path = tmp_path / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


_VALID_H2_PLAN_CONTENT = (
    "## 概要\n\nx\n\n"
    "### 計画メタ情報\n\n"
    f"- ベースコミット: `{'a' * 40}`\n\n"
    "## 実装資料\n\n### 変更説明\n\nREADMEを更新する。\n\n"
    "## 完了条件\n\nx\n\n"
    "## 進捗ログ\n\nx\n"
)


_HOOKS_JSON_PATH = pathlib.Path(__file__).resolve().parents[3] / "hooks" / "hooks.json"


_SCRIPTS_DIR_PATH = pathlib.Path(__file__).resolve().parents[2]


def _hook_entry_point_names() -> list[str]:
    """hooks.json の command 文字列から entry point スクリプト名を抽出する。"""
    text = _HOOKS_JSON_PATH.read_text(encoding="utf-8")
    pattern = re.compile(r"\$\{CLAUDE_PLUGIN_ROOT\}/scripts/([^\"\s]+\.py)")
    return sorted(set(pattern.findall(text)))


def _init_git_repo(path: pathlib.Path) -> None:
    """一括ステージ警告テスト用の最小git repo初期化。"""
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "test"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "commit.gpgsign", "false"], check=True)


def _make_repo_with_optional_remote(path: pathlib.Path, remote_url: str | None) -> str:
    """検査除外条件の判定用に、remote設定の有無を選べるgit repoを作成する。

    `remote_url`が`None`の場合は`git remote`が空リストを返すrepoになる。
    """
    path.mkdir(parents=True, exist_ok=True)
    _init_git_repo(path)
    if remote_url is not None:
        subprocess.run(["git", "-C", str(path), "remote", "add", "origin", remote_url], check=True)
    return str(path)


def _make_managed_temp_git_case(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    condition: str,
) -> str:
    """管理対象一時領域のGit除外判定に与える条件別の作業場所を作成する。"""
    state_home = tmp_path / "managed-temp-state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    monkeypatch.setenv("LOCALAPPDATA", str(state_home))
    if condition == "unmanaged":
        return _make_repo_with_optional_remote(tmp_path / "unmanaged" / "repo", None)

    managed = _managed_temp.create_managed_temp(f"pretooluse-{condition}", root=tmp_path)
    repo = managed / "repo"
    if condition == "git-query-failure":
        repo.mkdir()
        return str(repo)

    remote_url = "https://example.invalid/x.git" if condition == "remote" else None
    result = _make_repo_with_optional_remote(repo, remote_url)
    if condition == "invalid-marker":
        (managed / ".agent-toolkit-managed-temp.json").write_text("{}\n", encoding="utf-8")
    return result


def _git_commit_initial(path: pathlib.Path, files: dict[str, str]) -> None:
    """指定ファイルを追加してinitial commitを作成する。"""
    for rel, content in files.items():
        target = path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "init"], check=True)


def _process_loop_log_env(tmp_path: pathlib.Path) -> dict[str, str]:
    return {
        "AGENT_TOOLKIT_PROCESS_LOOP_SESSION": "1",
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "LOCALAPPDATA": str(tmp_path / "state"),
    }


def _path_section_build_content(recorded_path: str) -> str:
    """撤去済みの末尾パス節検査へ与える計画本文を組み立てる。"""
    return (
        "## 概要\n\nx\n\n"
        "## 実装資料\n\n### 変更説明\n\nREADMEを更新する。\n\n"
        "## 完了条件\n\nx\n\n"
        "## 進捗ログ\n\nx\n\n"
        "## 計画ファイル（本ファイル）のパス\n\n"
        f"`{recorded_path}`\n"
    )


_HANGUL_SAMPLE = "\uac00"  # ハングル音節1文字（エスケープ表記で構成する）


_CYRILLIC_SAMPLE = "\u0430"  # キリル小文字1文字（同上）


def _patch(*sections: str) -> str:
    """Codexの`apply_patch`入力本文を組み立てる。"""
    body = "".join(sections)
    return f"*** Begin Patch\n{body}*** End Patch\n"


def _codex_payload(patch_text: str, cwd: pathlib.Path, session_id: str = "codex-edit") -> dict:
    """Codexの編集payloadを組み立てる。"""
    return {
        "tool_name": "apply_patch",
        "tool_input": {"command": patch_text},
        "cwd": str(cwd),
        "session_id": session_id,
        "turn_id": "turn-1",
    }


__all__ = [
    "_CYRILLIC_SAMPLE",
    "_EXECUTE_REVIEW_TASK_NAMES",
    "_HANGUL_SAMPLE",
    "_HOOKS_JSON_PATH",
    "_MARKETPLACE_MANIFEST",
    "_NOTICE_PREFIX",
    "_NOTICE_SUFFIX",
    "_PLUGIN_MANIFEST",
    "_SCRIPT",
    "_SCRIPTS_DIR_PATH",
    "_SECRETS_COPY_GUIDANCE",
    "_SECRETS_VALUE_EDIT_GUIDANCE",
    "_SHARE_DIR",
    "_VALID_H2_PLAN_CONTENT",
    "_additional_context",
    "_agent_messages",
    "_codex_payload",
    "_deny_substring_fixture",
    "_git_commit_initial",
    "_home_path",
    "_hook_entry_point_names",
    "_init_git_repo",
    "_make_managed_temp_git_case",
    "_make_plan_file",
    "_make_private_notes_plan_file",
    "_make_repo_with_optional_remote",
    "_notice_body",
    "_patch",
    "_path_section_build_content",
    "_plan_file_state_env",
    "_process_loop_log_env",
    "_read_session_state",
    "_run",
    "_run_posttooluse",
    "_stage_model_env",
    "_stderr_warn_offenders",
    "_user_facing_payload",
    "_write_session_state",
    "_write_tmp_file",
]
