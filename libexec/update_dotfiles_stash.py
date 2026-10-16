"""更新元checkoutの退避実装を、update-dotfilesと同じPython環境で実行する。

退避操作に必要な依存はupdate-dotfilesが既に持つ。更新前の保護処理でuvの依存同期と
atk全体の起動前処理を繰り返すことを避け、Gitの退避ロックと共有GC保護は共通実装へ委ねる。
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "agent-toolkit"))

from agent_toolkit._atk import outcome, worktree_stash  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from agent_toolkit._common import file_lock, private_notes  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from agent_toolkit._git import command as git_command  # noqa: E402  # pylint: disable=wrong-import-position,import-error


def _git_output(arguments: list[str], worktree: pathlib.Path, *, input_text: str | None = None) -> str | None:
    """Gitの一括処理を実行し、失敗を退避データの確認失敗として報告する。"""
    result = git_command.run(arguments, worktree, capture_output=True, text=True, input=input_text)
    if result.returncode != 0 or result.stderr:
        outcome.report_failure(
            f"過去の退避データを確認できませんでした: {result.stderr.strip()}",
            next_action="git statusとupdate-dotfiles logsで原因を確認してから再実行してください。",
        )
        return None
    return result.stdout


def _parents(oids: set[str], worktree: pathlib.Path) -> dict[str, tuple[str, ...]] | None:
    """指定したcommitの親を、履歴をたどらず1回で取得する。"""
    if not oids:
        return {}
    output = _git_output(["log", "--no-walk", "--stdin", "--format=%H %P"], worktree, input_text="\n".join(sorted(oids)))
    if output is None:
        return None
    return {parts[0]: tuple(parts[1:]) for line in output.splitlines() if (parts := line.split())}


def _generated_only(oids: set[str], worktree: pathlib.Path) -> tuple[set[str], dict[str, str]] | None:
    """mise.lockだけの退避と、その元履歴を保持する参照の読取値を返す。"""
    parents = _parents(oids, worktree)
    if parents is None:
        return None
    candidates = {oid: values for oid, values in parents.items() if len(values) in {2, 3}}
    auxiliary = _parents({parent for values in candidates.values() for parent in values[1:]}, worktree)
    if auxiliary is None:
        return None
    candidates = {
        oid: values
        for oid, values in candidates.items()
        if auxiliary.get(values[1]) == (values[0],) and (len(values) == 2 or auxiliary.get(values[2]) == ())
    }
    requests: dict[str, str] = {}
    for oid, values in candidates.items():
        # --stdinの比較結果は先頭commitのOIDを見出しにする。indexと未追跡も独立に調べる。
        requests[oid] = f"{oid} {values[0]}"
        requests[values[1]] = f"{values[1]} {values[0]}"
        if len(values) == 3:
            requests[values[2]] = values[2]
    if not requests:
        return set(), {}
    output = _git_output(
        ["-c", "core.quotepath=true", "diff-tree", "--stdin", "--root", "--raw", "--no-abbrev", "--no-renames", "-r"],
        worktree,
        input_text="\n".join(requests.values()) + "\n",
    )
    if output is None:
        return None
    user_content: set[str] = set()
    current = ""
    for line in output.splitlines():
        if line.startswith(":"):
            if line.partition("\t")[2] != "mise.lock":
                user_content.add(current)
        elif line.strip():
            current = line.split()[0]
            if current not in requests:
                outcome.report_failure(
                    "退避の変更内容を判定できませんでした。退避データは保持しています。",
                    next_action="update-dotfiles logsでGitの出力を確認してください。",
                )
                return None
    generated = {oid for oid, values in candidates.items() if not ({oid, *values[1:]} & user_content)}
    if not generated:
        return set(), {}
    # 退避がローカルcommitの唯一の参照になっている場合は、その履歴も保全する。
    root_output = _git_output(
        ["for-each-ref", "--format=%(refname) %(objectname) %(symref)", "refs/heads/", "refs/remotes/"], worktree
    )
    if root_output is None:
        return None
    # symbolic refは実体側だけを検証し、同じ参照を二重にロックすることを避ける。
    roots = {parts[0]: parts[1] for line in root_output.splitlines() if len(parts := line.split()) == 2}
    revisions = [*(candidates[oid][0] for oid in generated), *(f"^{oid}" for oid in roots.values())]
    unreachable = _git_output(["rev-list", "--stdin"], worktree, input_text="\n".join(revisions) + "\n")
    if unreachable is None:
        return None
    unique_history = set(unreachable.splitlines())
    return {oid for oid in generated if candidates[oid][0] not in unique_history}, roots


def _migrate_old(worktree: pathlib.Path, protection_prefix: str) -> int:
    """固定ロック下で旧退避を一括分類し、回収・保護を1つのGitトランザクションで行う。"""
    output = _git_output(
        ["for-each-ref", "--format=%(refname) %(objectname)", "refs/worktree/", "refs/atk/worktree-stash/"], worktree
    )
    if output is None:
        return 1
    refs = dict(line.split(" ", 1) for line in output.splitlines())
    old_refs = {ref: oid for ref, oid in refs.items() if ref.startswith("refs/worktree/update-dotfiles-")}
    if not old_refs:
        return 0
    cleanup = _generated_only(set(old_refs.values()), worktree)
    if cleanup is None:
        return 1
    generated, retention_roots = cleanup
    commands: list[str] = []
    details: list[str] = []
    removed = protected = 0
    for ref, oid in old_refs.items():
        shared_ref = protection_prefix + ref.removeprefix("refs/worktree/")
        shared_oid = refs.get(shared_ref)
        if shared_oid is not None and shared_oid != oid:
            outcome.report_failure(
                f"過去の退避と保護用データの内容が一致しません: {ref}。退避データは保持しています。",
                next_action=(
                    "git for-each-ref refs/worktree/ refs/atk/worktree-stash/で両参照を確認し、"
                    "update-dotfiles logsで停止した操作を調べてください。"
                ),
            )
            return 1
        if oid in generated:
            commands.append(f"delete {ref} {oid}")
            if shared_oid is not None:
                commands.append(f"delete {shared_ref} {oid}")
            details.append(f"mise.lockだけの旧退避を削除: {ref} oid={oid}")
            removed += 1
        elif shared_oid is None:
            commands.extend((f"verify {ref} {oid}", f"create {shared_ref} {oid}"))
            details.append(f"復旧用の旧退避を保護: {ref} oid={oid}")
            protected += 1
    if commands:
        if removed:
            # 到達性判定の根拠も同じトランザクションで検証し、途中のbranch移動による履歴喪失を防ぐ。
            commands = [*(f"verify {ref} {oid}" for ref, oid in retention_roots.items()), *commands]
        transaction = "\n".join(("start", *commands, "prepare", "commit", ""))
        if _git_output(["update-ref", "--stdin"], worktree, input_text=transaction) is None:
            return 1
        for detail in details:
            print(detail)
        summary = f"mise.lockだけの過去の退避{removed}件を削除しました。" if removed else ""
        retained = len(old_refs) - removed
        if retained:
            summary += f"復旧用の退避{retained}件を保持しました（今回保護: {protected}件）。"
        print(summary + "更新を続けます。")
    return 0


def protect_old(*, cwd: pathlib.Path | None = None) -> int:
    """旧退避を一括整理し、生成差分の蓄積と保護済み退避の再処理を防ぐ。"""
    worktree = (cwd or pathlib.Path.cwd()).resolve()
    if worktree_stash._is_queue_repository(worktree, private_notes.default_private_notes()):  # pylint: disable=protected-access
        outcome.report_failure(
            worktree_stash._QUEUE_REPOSITORY_ERROR,  # pylint: disable=protected-access
            next_action=worktree_stash._QUEUE_REPOSITORY_NEXT_ACTION,  # pylint: disable=protected-access
        )
        return 2
    common_dir = worktree_stash._common_dir(worktree)  # pylint: disable=protected-access
    if common_dir is None:
        return 1
    protection_prefix = worktree_stash._protection_ref(  # pylint: disable=protected-access
        "refs/worktree/", worktree, common_dir
    )
    if protection_prefix is None:
        return 1
    try:
        with (common_dir / worktree_stash._LOCK_NAME).open("a+", encoding="utf-8") as lock_file:  # pylint: disable=protected-access
            file_lock.acquire_lock(lock_file)
            try:
                return _migrate_old(worktree, protection_prefix)
            finally:
                file_lock.release_lock(lock_file)
    except OSError as error:
        outcome.report_failure(
            f"過去の退避データを整理できませんでした: {error}",
            next_action="update-dotfiles logsでロックやGitの起動失敗の原因を確認してから再実行してください。",
        )
        return 1


def main(argv: list[str] | None = None) -> int:
    """共通CLIと同じ入力・保護条件で退避操作だけを実行する。"""
    outcome.force_utf8_stdio()
    arguments = sys.argv[1:] if argv is None else argv
    if arguments == ["protect-old"]:
        return protect_old()
    parser = argparse.ArgumentParser(description=__doc__)
    worktree_stash.build_parser(parser)
    return worktree_stash.dispatch(parser.parse_args(arguments), private_notes=private_notes.default_private_notes())


if __name__ == "__main__":
    raise SystemExit(main())
