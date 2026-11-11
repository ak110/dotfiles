"""明示登録の編集可否による編集ツールとcommitの保護。"""

from __future__ import annotations

import pathlib
import shlex
import subprocess

from agent_toolkit._common import private_notes, shell_cwd
from agent_toolkit._common.bash_invocations import is_unknown_word
from agent_toolkit._common.shell_tokens import is_atk_command
from agent_toolkit._hooks import tool_input
from agent_toolkit._hooks.notice import block_formatter
from agent_toolkit._hooks.pretooluse.shell_checks_git import git_subcommand_tokens
lazy from agent_toolkit._atk import config_repositories, config_settings, managed_temp

_block = block_formatter("repository-protection")


def _guidance(repo_id: str, edit: str | None) -> str:
    quoted = shlex.quote(repo_id)
    if edit == "awi":
        return f"直接編集せず、`atk wi add --target-repo {quoted}`でAWIを投入せよ（cloneは不要）。"
    if edit == "deny":
        return "編集せず、必要な変更をユーザーへ報告せよ。"
    return (
        f"登録案（識別子: {repo_id}、edit: allow）を示し、ユーザーが編集を求めている場合は"
        "「エージェントが登録して作業を続ける」を最初から選択した推奨案として確認せよ。"
        "確認手段はagent-toolkit:user-confirmation-and-reportに従え（協調は質問、自律はUWI）。"
        f"承認後にエージェントが`atk config repos add {quoted} --edit allow`を実行せよ。承認なしに登録して回避するな。"
    )


def path_restriction(path: str, *, git_options: tuple[str, ...] = (), git_commit: bool = False) -> tuple[str, str] | None:
    """Git外と記録用領域を除き、allow以外の理由と次の行動を返す。"""
    original = pathlib.Path(path).expanduser().absolute()
    # symlink自体の削除とリンク経由の書込みの両方を保護するため、入力したパスと実体を判定する。
    candidates = [original, original.resolve()]
    candidates.extend(parent.parent for parent in original.parents if parent.is_symlink())
    exempt_roots = (private_notes.default_private_notes().resolve(), managed_temp.temp_root_path().resolve())
    repositories = None
    for candidate in dict.fromkeys(candidates):
        if not git_options and not git_commit and any(candidate.is_relative_to(root) for root in exempt_roots):
            continue
        parent = candidate
        while not parent.is_dir() and parent != parent.parent:
            parent = parent.parent
        location = config_repositories.locate(parent, git_options=git_options)
        # --git-dirだけの指定ではGitのworktree表示がcwdになるため、更新するGit実体も例外領域内か確かめる。
        if (
            location is None
            or (git_options or git_commit)
            and any(
                location.worktree.is_relative_to(root) and location.common_dir.is_relative_to(root) for root in exempt_roots
            )
        ):
            continue
        if repositories is None:
            repositories = config_settings.repository_settings(check_paths=False)
        item = repositories.get(location.repo_id)
        edit = item["edit"] if item else None
        if edit == "allow":
            continue
        return (
            f"repository {location.repo_id}は{edit or '未登録（既定deny）'}のため直接編集できない。",
            _guidance(location.repo_id, edit),
        )
    return None


def check(tool_name: str, tool_input_value: dict, cwd: str) -> str | None:
    """編集対象とcommit対象を解決し、拒否を共有formatterで返す。"""
    paths: list[tuple[str, tuple[str, ...]]] = []
    if tool_name == "NotebookEdit":
        path = tool_input_value.get("notebook_path")
        if isinstance(path, str) and path:
            paths.append((str(pathlib.Path(cwd) / path), ()))
    elif tool_name == "Bash":
        command = tool_input_value.get("command")
        if not isinstance(command, str):
            return None
        repository_variables = {"GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR"}
        modified_scopes: set[tuple[object, ...]] = set()
        for invocation, current in shell_cwd.bash_invocation_cwds(command, cwd, effects=True):
            tokens = invocation.segment.tokens
            # 継続代入とexportは後続・子シェルへ作用する。分岐の実行結果やunsetによる回復は推測しない。
            assignments = invocation.environment_assignments
            if tokens and tokens[0] in {"export", "declare", "typeset", "readonly"}:
                assignments = (*assignments, *tokens[1:])
            if (not invocation.execution_known or tokens and tokens[0] in {"export", "declare", "typeset", "readonly"}) and any(
                value.partition("=")[0] in repository_variables for value in assignments
            ):
                modified_scopes.add(invocation.cwd_scope)
            parsed = git_subcommand_tokens(invocation.segment)
            if parsed is None or parsed[0] != "commit":
                continue
            if any(value.partition("=")[0] in repository_variables for value in invocation.environment_assignments) or any(
                invocation.cwd_scope[: len(scope)] == scope for scope in modified_scopes
            ):
                return _block(
                    "環境変数で変更されたコミット先のリポジトリを解決できない。",
                    fix="対象の指定には`git -C`か`--git-dir`を使い、対象を選ぶ環境変数の代入を外して再実行せよ。",
                )
            base = pathlib.Path(current.path)
            prefix = tokens[: len(tokens) - len(parsed[1]) - 1]
            if not current.resolved or any(is_unknown_word(token) or "$" in token or "`" in token for token in prefix):
                return _block(
                    "コミット先のリポジトリを解決できない。",
                    fix="作業ディレクトリと`git -C`の値を実在する絶対パスで指定して再実行せよ。",
                )
            options = () if is_atk_command(tokens) else tuple(prefix[1:])
            paths.append((str(base), options))
    else:
        operations = tool_input.parse_operations(tool_name, tool_input_value, cwd)
        if operations is None:
            return None
        for operation in operations:
            paths.extend((path, ()) for path in (operation.path, operation.source_path) if path)
    try:
        for path, options in dict.fromkeys(paths):
            restriction = path_restriction(path, git_options=options, git_commit=tool_name == "Bash")
            if restriction:
                return _block(restriction[0], fix=restriction[1])
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        return _block(
            f"リポジトリの編集可否を確認できない: {error}",
            fix="`atk config repos list`で設定の診断を確認し、登録を直して再実行せよ。",
        )
    return None


def start_notice(cwd: str) -> str | None:
    """メイン・委譲先共通の開始時に編集制約と検証OSを通知する。"""
    if not cwd:
        return None
    restriction = path_restriction(cwd)
    if restriction:
        return restriction[0] + "\n次の操作: " + restriction[1]
    location = config_repositories.locate(pathlib.Path(cwd))
    if location is None:
        return None
    item = config_settings.repository_settings(check_paths=False).get(location.repo_id)
    if not item:
        return None
    return (
        f"repository {location.repo_id}の検証OS: {', '.join(item['platforms'])}。"
        "対象外のOSでは可能なコンパイル等だけを行い、テスト合否は対象OSのCIで判断せよ。"
    )
