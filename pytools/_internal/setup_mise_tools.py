"""setup_miseの管理ツールの導入・修復と整理。"""

import json
import os
import subprocess
import typing
from pathlib import Path

from agent_toolkit._common import dotfiles_mode

from pytools._internal import (
    common,
    log_format,
    setup_cli_common,
    setup_mise_common,
    winutils,
)


def working_tree_with_config() -> Path | None:
    """`mise.toml` を持つchezmoi working treeを返す。該当しない場合は`None`を返す。"""
    # `find_dotfiles_root()`を使わず`CHEZMOI_WORKING_TREE`を読む。
    # 値が作業ツリーの位置に加えて「chezmoiから起動された」ことの判定を兼ね、
    # `dotfiles-post-apply`の単独実行では作業ツリーの`mise.toml`を実行位置に使わないためである。
    if dotfiles_mode.is_minimal():
        return Path.home()
    working_tree = os.environ.get("CHEZMOI_WORKING_TREE")
    if not working_tree:
        return None
    root = Path(working_tree)
    return root if (root / "mise.toml").is_file() else None


def ensure_working_tree_trusted(mise_bin: Path) -> bool:
    """Chezmoi workingTree直下の `mise.toml` を `mise trust` 対象にする。

    未trustのままではmise CLIが毎回警告を表示して設定を無視するため、冪等にtrustする。
    workingTreeは `CHEZMOI_WORKING_TREE` 環境変数経由で受け取る（未設定ならCLI単体実行
    とみなしてスキップ）。既にtrust済みであっても `mise trust` は重複登録しない仕様のため、
    事前チェックは行わず毎回実行する。副作用がないため、成功時はchanged判定も常にTrueを
    返す（サマリで「更新」扱いになるが、ノイズよりも実行事実を確認できる方を優先する）。
    """
    # `find_dotfiles_root()`を使わず`CHEZMOI_WORKING_TREE`を読む。
    # 値が作業ツリーの位置に加えて「chezmoiから起動された」ことの判定を兼ね、
    # `dotfiles-post-apply`の単独実行ではtrustをスキップするためである。
    working_tree = os.environ.get("CHEZMOI_WORKING_TREE")
    if not working_tree:
        setup_mise_common.logger.info(
            log_format.format_status(
                "mise",
                "CHEZMOI_WORKING_TREE 未設定のため trust をスキップ。対象設定が無いため対応不要",
            ),
        )
        return False

    mise_toml = Path(working_tree) / "mise.toml"
    if not mise_toml.is_file():
        setup_mise_common.logger.info(
            log_format.format_status("mise", f"{mise_toml} が無いため trust をスキップ。対象設定が無いため対応不要"),
        )
        return False

    result = setup_mise_common.run_mise(mise_bin, ["trust", str(mise_toml)])
    if result is None or result.returncode != 0:
        raise setup_mise_common.SettingError(setup_mise_common.with_mise_output("`mise trust` に失敗", result))

    setup_mise_common.logger.info(log_format.format_status("mise", f"{mise_toml} を trust しました"))
    return True


def ensure_global_node(mise_bin: Path) -> bool:
    """global設定にnodeがなければ `mise use --global node@lts` を実行する。

    Returns:
        nodeを新たに設定した場合True。
    """
    result = setup_mise_common.run_mise(mise_bin, ["ls", "--global", "--json"])
    if result is None or result.returncode != 0:
        setup_mise_common.logger.warning(
            log_format.format_status(
                "mise",
                setup_mise_common.with_mise_output(
                    "`ls --global --json` に失敗したため node 設定をスキップ（処理は継続した）。"
                    f"`mise use --global node@lts`を手動実行する。{_MISE_REPORT_FALLBACK}",
                    result,
                ),
            ),
        )
        return False

    try:
        data = json.loads(result.stdout) if result.stdout.strip() else {}
    except json.JSONDecodeError as e:
        setup_mise_common.logger.info(
            log_format.format_status(
                "mise",
                f"`ls --global --json` の出力 JSON を解析できずスキップ: {e}。`mise use --global node@lts`を手動実行する",
            ),
        )
        return False

    if _has_global_node(data):
        setup_mise_common.logger.info(log_format.format_status("mise", "global Node は既に設定済み"))
        return False

    install_result = setup_mise_common.run_mise(mise_bin, ["use", "--global", "node@lts"])
    if install_result is None or install_result.returncode != 0:
        next_action = (
            _install_next_action(install_result)
            if install_result is not None and "os error 1314" in common.format_cli_error(install_result)
            else "表示された接続先へのネットワーク到達を確かめてから`update-dotfiles`を再実行する。"
        )
        setup_mise_common.logger.warning(
            log_format.format_status(
                "mise",
                setup_mise_common.with_mise_output(
                    "`use --global node@lts` に失敗（処理は継続した）。"
                    f"{next_action} 手動で設定する場合は`mise use --global node@lts`を実行する。"
                    f"{_MISE_REPORT_FALLBACK}",
                    install_result,
                ),
            ),
        )
        return False

    setup_mise_common.logger.info(log_format.format_status("mise", "global に node@lts を設定しました"))
    return True


def _has_global_node(data: object) -> bool:
    """`mise ls --global --json` の戻り値にnodeエントリがあるかを判定する。

    miseの出力形式はversionにより揺れがあるため、以下の全パターンに対応する。

    - ``{"node": [{...}, ...]}``: キーがツール名
    - ``[{"name": "node", ...}, ...]``: 配列の各要素に ``name``
    - 上記のネスト（例: ``{"tools": ...}``）
    """
    if isinstance(data, dict):
        if "node" in data:
            return True
        return any(_has_global_node(value) for value in data.values())
    if isinstance(data, list):
        return any(_list_item_is_node(item) for item in data)
    return False


def _list_item_is_node(item: object) -> bool:
    """`mise ls --global --json` の list 形式 1 エントリが node を指しているかを判定する。"""
    if not isinstance(item, dict):
        return False
    item_dict = typing.cast("dict[object, object]", item)
    name = item_dict.get("name")
    return isinstance(name, str) and name == "node"


# 作業ツリーを実行位置とする`mise install`だけへ与える環境変数。
_LOCKED_INSTALL_ENV = {"MISE_LOCKED": "1", "MISE_LOCKED_SCOPES": "project"}

# 案内した手動操作や再実行でも失敗が続く場合の報告先。dotfiles側で対処できない失敗は
# miseの出力が原因を示すため、その出力を添えてmiseの開発元へ報告する。
_MISE_REPORT_FALLBACK = "手動でも同じ失敗が続く場合は、表示されたmiseの出力を添えてmiseの開発元（GitHubの`jdx/mise`）へ報告する"

# 手動導入でもprojectのlockを書き戻さない。
_INSTALL_NEXT_ACTION = (
    "表示された接続先へのネットワーク到達（プロキシ設定を含む）を確かめてから`update-dotfiles`を再実行する"
    "（手動で導入する場合は、chezmoiの作業ツリーで`MISE_LOCKED=1 MISE_LOCKED_SCOPES=project mise install`を実行する。"
    "Windowsでは両環境変数を設定して`mise install`を実行する）。"
    f"{_MISE_REPORT_FALLBACK}"
)


def _install_next_action(result: subprocess.CompletedProcess[str]) -> str:
    """展開時の権限不足には、その場で必要なWindows設定を案内する。"""
    if "os error 1314" in common.format_cli_error(result):
        return (
            "シンボリックリンクの作成権限を確認する。Windowsの設定で「開発者モード」を検索して有効にし、"
            "新しいターミナルで`update-dotfiles`を再実行する。組織管理端末では管理者に設定変更を依頼する。"
            "post-applyはOS設定を自動変更しない。"
        )
    return _INSTALL_NEXT_ACTION


def ensure_tools_installed(mise_bin: Path) -> bool:
    """`mise install` で global/working-tree 設定のツールを取得する。

    miseは実行時のカレントディレクトリから設定ファイルを探索するため、chezmoi後処理の
    カレントディレクトリ（配布先ホーム）のままでは working tree の `mise.toml` が対象外になり、
    そこにだけ定義されたツールが恒久的に未導入のまま残る。working tree を実行位置に指定すると
    global設定も併合されるため、`CHEZMOI_WORKING_TREE` に `mise.toml` がある場合は
    そこを実行位置として1回だけ実行する。

    working treeの`mise.lock`を書き戻さないよう、プロジェクトのlockfileに対してlockedモード
    （`MISE_LOCKED=1`・`MISE_LOCKED_SCOPES=project`）で実行する。global設定はlockにURLを持たない
    ツールを含むため対象外とする。lockedモードで失敗しても、lockの書き戻しを招くため
    lockedを外した再実行はしない。

    終了コード非ゼロ・タイムアウト・例外は警告して吸収し、後続ステップを止めない。
    取得・導入の失敗は`PostApplyOutcome`の契約どおりスキップと数えるためFalseを返す。
    インストール差分の厳密判定は出力からは行えないため、成功時の`changed`は実行事実を記録してTrueを返す。
    """
    result = setup_mise_common.run_mise(
        mise_bin,
        ["install", *(setup_mise_common.LIGHTWEIGHT_TOOLS if dotfiles_mode.is_minimal() else ())],
        timeout=setup_mise_common.MISE_INSTALL_TIMEOUT,
        cwd=working_tree_with_config(),
        extra_env=_LOCKED_INSTALL_ENV,
    )
    if result is None:
        setup_mise_common.logger.warning(
            log_format.format_status(
                "mise",
                f"`install` が{setup_mise_common.MISE_INSTALL_TIMEOUT}秒以内に終わらないか"
                "起動できず中断した（処理は継続した）。"
                f"{_INSTALL_NEXT_ACTION}",
            ),
        )
        return False
    if result.returncode != 0:
        setup_mise_common.logger.warning(
            log_format.format_status(
                "mise",
                setup_mise_common.with_mise_output(
                    f"`install` に失敗（処理は継続した）。{_install_next_action(result)}", result
                ),
            ),
        )
        return False
    setup_mise_common.logger.info(log_format.format_status("mise", "`install` を実行しました"))
    return True


def ensure_tool_bin_paths_present(mise_bin: Path) -> bool:
    """導入済みツールの実行ファイルの配置先が欠落していれば、同じ版を強制再インストールする。

    `symlink_bins`など配置先を変えるツールオプションは導入時にだけ配置を生成し、導入済みの版へ遡及しない。
    `mise install`は導入済みの版を再導入せず、`mise install --dry-run-code`と`mise doctor`も欠落を示さないため、
    配布設定の変更後も導入済みのホストではコマンドを解決できないまま残る。
    欠落の原因となるオプションを列挙せず、mise自身が現在の設定から導く配置先（`mise bin-paths`）の実在で判定する。
    `mise bin-paths --json`は実在しない配置先を含まないため、引数なしの出力を使う。
    実行位置は`_ensure_tools_installed`と同じとし、working treeの`mise.toml`にだけあるツールも対象にする。
    版を明示して再導入するため、版の解決とlockfileは変わらない。
    検出・再導入の失敗は警告して後続のステップを止めない。

    Returns:
        再導入を1件以上実行した場合True。
    """
    cwd = working_tree_with_config()
    missing = _missing_bin_paths(mise_bin, cwd)
    if not missing:
        return False
    installs = _installed_tool_paths(mise_bin, cwd)
    if installs is None:
        return False

    targets: dict[tuple[str, str], list[Path]] = {}
    for directory in missing:
        owner = _owning_tool(directory, installs)
        if dotfiles_mode.is_minimal() and (owner is None or owner[0] not in setup_mise_common.LIGHTWEIGHT_TOOLS):
            continue
        if owner is None:
            # 共有ランタイムへのリンクで導入されるツール（dotnet-root、~/.cargo/binなど）は再導入で生成されない。
            setup_mise_common.logger.warning(
                log_format.format_status(
                    "mise",
                    "実行ファイルの配置先が無いが、導入先を特定できないため再導入しない: "
                    f"{directory}（処理は継続した）。"
                    "`mise bin-paths`と`mise ls --current --installed "
                    "--json`で配置先と導入ツールを確認し、該当ツールを手動導入する",
                ),
            )
            continue
        targets.setdefault(owner, []).append(directory)
    if not targets:
        return False

    changed = False
    for tool, version in targets:
        spec = f"{tool}@{version}"
        # lockedモードを与えない。mise 2026.9.17では、lockにURLがあってもlockedモードの`install --force <ツール>@<版>`は
        # 「No lockfile URL found」で失敗する。版を明示した再導入はlockfileを書き戻さないことを同じ版で確かめた。
        result = setup_mise_common.run_mise(
            mise_bin, ["install", "--force", spec], timeout=setup_mise_common.MISE_INSTALL_TIMEOUT, cwd=cwd
        )
        if result is None:
            setup_mise_common.logger.warning(
                log_format.format_status(
                    "mise",
                    f"`install --force {spec}` がタイムアウトまたは例外で中断。手動で`mise install --force {spec}`を実行する",
                ),
            )
            continue
        if result.returncode != 0:
            setup_mise_common.logger.warning(
                log_format.format_status(
                    "mise",
                    setup_mise_common.with_mise_output(
                        f"`install --force {spec}` に失敗（処理は継続した）。"
                        f"{_install_next_action(result)} 手動で`mise install --force {spec}`を実行する。",
                        result,
                    ),
                ),
            )
            continue
        setup_mise_common.logger.info(
            log_format.format_status(
                "mise",
                f"実行ファイルの配置先を再生成するため `install --force {spec}` を実行しました",
            ),
        )
        changed = True

    remaining = _missing_bin_paths(mise_bin, cwd)
    if remaining is None:
        return changed
    for directory in remaining:
        owner = _owning_tool(directory, installs)
        if owner is None or owner not in targets:
            continue
        tool, version = owner
        setup_mise_common.logger.warning(
            log_format.format_status(
                "mise",
                f"{tool}の実行ファイルの配置先が再導入後も見つからない: {directory}。"
                f"手動で`mise install --force {tool}@{version}`を実行する",
            ),
        )
    return changed


def _missing_bin_paths(mise_bin: Path, cwd: Path | None) -> list[Path] | None:
    """`mise bin-paths`が返す配置先のうち実在しないものを返す。取得に失敗した場合は警告して`None`を返す。"""
    result = setup_mise_common.run_mise(mise_bin, ["bin-paths"], cwd=cwd)
    if result is None:
        setup_mise_common.logger.warning(
            log_format.format_status(
                "mise",
                "`bin-paths` がタイムアウトまたは例外で中断したため配置先の確認を省いた（処理は継続した）。"
                "`update-dotfiles`を再実行する",
            ),
        )
        return None
    if result.returncode != 0:
        setup_mise_common.logger.warning(
            log_format.format_status(
                "mise",
                setup_mise_common.with_mise_output(
                    "`bin-paths` に失敗したため配置先の確認を省いた（処理は継続した）。"
                    f"`update-dotfiles`を再実行する。{_MISE_REPORT_FALLBACK}",
                    result,
                ),
            ),
        )
        return None
    return [Path(line.strip()) for line in result.stdout.splitlines() if line.strip() and not Path(line.strip()).is_dir()]


def _installed_tool_paths(mise_bin: Path, cwd: Path | None) -> dict[Path, tuple[str, str]] | None:
    """導入済みの現行版について、導入先ディレクトリからツール名と版への対応を返す。

    取得または解析に失敗した場合は警告して`None`を返す。
    """
    result = setup_mise_common.run_mise(mise_bin, ["ls", "--current", "--installed", "--json"], cwd=cwd)
    if result is None:
        setup_mise_common.logger.warning(
            log_format.format_status(
                "mise",
                "`ls --current --installed --json` がタイムアウトまたは例外で中断したため再導入を省いた（処理は継続した）。"
                "`update-dotfiles`を再実行する",
            ),
        )
        return None
    if result.returncode != 0:
        setup_mise_common.logger.warning(
            log_format.format_status(
                "mise",
                setup_mise_common.with_mise_output(
                    "`ls --current --installed --json` に失敗したため再導入を省いた（処理は継続した）。"
                    f"`update-dotfiles`を再実行する。{_MISE_REPORT_FALLBACK}",
                    result,
                ),
            ),
        )
        return None
    try:
        data = json.loads(result.stdout) if result.stdout.strip() else {}
    except json.JSONDecodeError as error:
        setup_mise_common.logger.warning(
            log_format.format_status(
                "mise",
                "`ls --current --installed --json` の出力を解析できないため再導入を省いた（処理は継続した）。"
                f"`update-dotfiles`を再実行する: {error}",
            ),
        )
        return None
    installs: dict[Path, tuple[str, str]] = {}
    if not isinstance(data, dict):
        return installs
    for tool, entries in typing.cast("dict[object, object]", data).items():
        if not isinstance(tool, str) or not isinstance(entries, list):
            continue
        for entry in typing.cast("list[object]", entries):
            if not isinstance(entry, dict):
                continue
            entry_dict = typing.cast("dict[object, object]", entry)
            install_path = entry_dict.get("install_path")
            version = entry_dict.get("version")
            if isinstance(install_path, str) and install_path and isinstance(version, str) and version:
                installs[Path(install_path)] = (tool, version)
    return installs


def _owning_tool(directory: Path, installs: dict[Path, tuple[str, str]]) -> tuple[str, str] | None:
    """配置先ディレクトリを配下に持つ導入先のうち最も深いもののツール名と版を返す。"""
    owners = [install_path for install_path in installs if directory.is_relative_to(install_path)]
    if not owners:
        return None
    return installs[max(owners, key=lambda install_path: len(install_path.parts))]


def ensure_orphan_shims_removed(mise_bin: Path) -> bool:
    """`mise prune`と`mise reshim`で管理外の孤児shimを除去する。

    shimの再生成元は`installs/`配下の実体であり、`prune`を伴わない`reshim`では
    管理外shimが再生成される。`prune`はtracked configが参照するツールの現行版を
    削除しないため、`mise install`の後に未参照実体を除去してshimを強制再構築する。
    """
    prune = setup_mise_common.run_mise(mise_bin, ["prune", "-y"], timeout=setup_mise_common.MISE_INSTALL_TIMEOUT)
    if prune is None:
        setup_mise_common.logger.warning(
            log_format.format_status(
                "mise",
                "`prune -y` がタイムアウトまたは例外で中断（処理は継続した）。`mise prune -y`を手動実行する。"
                f"{_MISE_REPORT_FALLBACK}",
            ),
        )
        return False
    if prune.returncode != 0:
        setup_mise_common.logger.warning(
            log_format.format_status(
                "mise",
                setup_mise_common.with_mise_output(
                    f"`prune -y` に失敗（処理は継続した）。`mise prune -y`を手動実行する。{_MISE_REPORT_FALLBACK}",
                    prune,
                ),
            ),
        )
        return False
    result = setup_mise_common.run_mise(mise_bin, ["reshim", "--force"])
    if result is None:
        setup_mise_common.logger.warning(
            log_format.format_status(
                "mise",
                "`reshim --force` がタイムアウトまたは例外で中断（処理は継続した）。`mise reshim --force`を手動実行する。"
                f"{_MISE_REPORT_FALLBACK}",
            ),
        )
        return False
    if result.returncode != 0:
        setup_mise_common.logger.warning(
            log_format.format_status(
                "mise",
                setup_mise_common.with_mise_output(
                    f"`reshim --force` に失敗（処理は継続した）。`mise reshim --force`を手動実行する。{_MISE_REPORT_FALLBACK}",
                    result,
                ),
            ),
        )
        return False
    setup_mise_common.logger.info(log_format.format_status("mise", "`reshim --force` を実行しました"))
    return True


def ensure_windows_user_path_has_shims() -> bool:
    r"""HKCU\Environment\Path に mise の shims ディレクトリを追加する (Windows 専用).

    Returns:
        レジストリを更新したら True。
    """
    localappdata = os.environ.get("LOCALAPPDATA")
    if not localappdata:
        setup_mise_common.logger.info(
            log_format.format_status(
                "mise",
                "LOCALAPPDATA 環境変数が無いため shims 追加をスキップ。対象設定が無いため対応不要",
            ),
        )
        return False

    shims_dir = Path(localappdata) / "mise" / "shims"
    if not shims_dir.is_dir():
        setup_mise_common.logger.info(
            log_format.format_status(
                "mise",
                f"{shims_dir} が無いため shims 追加をスキップ。対象設定が無いため対応不要",
            ),
        )
        return False

    try:
        appended = winutils.append_user_path(setup_mise_common.WINDOWS_SHIMS_ENTRY)
    except OSError as e:
        raise setup_mise_common.SettingError(f"ユーザー PATH の更新に失敗: {e}") from e
    if appended:
        setup_mise_common.logger.info(
            log_format.format_status("mise", f"ユーザー PATH に {setup_mise_common.WINDOWS_SHIMS_ENTRY} を追加しました")
        )
        winutils.broadcast_environment_change()
    else:
        setup_mise_common.logger.info(
            log_format.format_status("mise", f"ユーザー PATH に {setup_mise_common.WINDOWS_SHIMS_ENTRY} は既に登録済み")
        )

    # 現プロセスの PATH にも反映しておく (post_apply の後続ステップが shims 内の
    # コマンドを参照できるようにするため)。冪等性のため重複追加は避ける。
    # 並列に動く他工程のPATH追加を失わないよう、読み取りから書き戻しまでを直列化する。
    with setup_cli_common.PROCESS_PATH_LOCK:
        current_process_path = os.environ.get("PATH", "")
        if str(shims_dir) not in current_process_path.split(os.pathsep):
            separator = os.pathsep if current_process_path else ""
            os.environ["PATH"] = current_process_path + separator + str(shims_dir)

    return appended
