"""miseセットアップをまとめるモジュール。

`chezmoi apply`後処理（`pytools.post_apply`）から呼ばれる。
"""

import json
import os
from pathlib import Path

import platformdirs
from agent_toolkit._common import dotfiles_mode

from pytools._internal import (
    log_format,
    post_apply_cache,
    post_apply_outcome,
    setup_mise_binary,
    setup_mise_common,
    setup_mise_tools,
)
from pytools._internal.setup_mise_binary import ensure_mise_installed, find_mise_binary
from pytools._internal.setup_mise_tools import (
    ensure_tools_installed,
    ensure_windows_user_path_has_shims,
    working_tree_with_config,
)


def run() -> post_apply_outcome.PostApplyOutcome:
    """Mise セットアップを実行する。

    mise本体と管理ツールの導入・更新・整理の失敗は警告を出力してスキップと数え、
    `mise trust`とユーザーPATHの書き換えの失敗は失敗と数える。
    """
    mise_bin = find_mise_binary()
    if mise_bin is None:
        setup_mise_binary.ensure_mise_installed()
        mise_bin = find_mise_binary()
        if mise_bin is None:
            setup_mise_common.logger.info(
                log_format.format_status(
                    "mise",
                    "未検出のためスキップ。"
                    "https://mise.jdx.dev/getting-started.html の手順でmiseを導入して`update-dotfiles`を再実行する",
                ),
            )
            return post_apply_outcome.PostApplyOutcome()

    changed = False
    failures: list[str] = []
    changed |= setup_mise_binary.ensure_mise_up_to_date(mise_bin)
    try:
        changed |= setup_mise_tools.ensure_working_tree_trusted(mise_bin)
    except setup_mise_common.SettingError as error:
        failures.append(str(error))
    if not dotfiles_mode.is_minimal():
        changed |= setup_mise_tools.ensure_global_node(mise_bin)
        changed |= setup_mise_binary.ensure_gcloud_plugin_usable(mise_bin)
    maintenance_key = _maintenance_key(mise_bin)
    unchanged = maintenance_key is not None and post_apply_cache.matches("mise-tools", maintenance_key)
    installed = False
    if unchanged:
        setup_mise_common.logger.info(
            log_format.format_status("mise", "設定・lock・導入状態が同じためinstall・prune・reshimを省略")
        )
    else:
        installed = setup_mise_tools.ensure_tools_installed(mise_bin)
        changed |= installed
    repaired = setup_mise_tools.ensure_tool_bin_paths_present(mise_bin)
    changed |= repaired
    if not unchanged or repaired:
        pruned = dotfiles_mode.is_minimal() or setup_mise_tools.ensure_orphan_shims_removed(mise_bin)
        changed |= pruned and not dotfiles_mode.is_minimal()
        if installed and pruned and (key := _maintenance_key(mise_bin)) is not None:
            post_apply_cache.save("mise-tools", key)
    if setup_mise_common.is_windows():
        try:
            changed |= setup_mise_tools.ensure_windows_user_path_has_shims()
        except setup_mise_common.SettingError as error:
            failures.append(str(error))
    return post_apply_outcome.PostApplyOutcome(
        changed=changed,
        failure=" / ".join(failures) or None,
        next_action="問題を解消した後に`update-dotfiles`を再実行する。" if failures else None,
    )


def _maintenance_key(mise_bin: Path) -> str | None:
    """mise自身が列挙する設定と導入状態を、成功済みの整理結果と比較する。"""
    cwd = setup_mise_tools.working_tree_with_config()
    configs = setup_mise_common.run_mise(mise_bin, ["config", "ls", "--json", "--tracked-configs"], cwd=cwd)
    installed = setup_mise_common.run_mise(mise_bin, ["ls", "--json"], cwd=cwd)
    version = setup_mise_common.run_mise(mise_bin, ["--version"])
    if (
        configs is None
        or installed is None
        or version is None
        or configs.returncode
        or installed.returncode
        or version.returncode
    ):
        return None
    try:
        entries = json.loads(configs.stdout)
        tools = json.loads(installed.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(entries, list) or not isinstance(tools, dict):
        return None
    paths = [
        Path(__file__),
        Path(setup_mise_binary.__file__),
        Path(setup_mise_common.__file__),
        Path(setup_mise_tools.__file__),
    ]
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            return None
        config = Path(entry["path"])
        paths.extend((config, config.with_suffix(".lock"), config.parent / "mise.lock"))
    data = Path(os.environ.get("MISE_DATA_DIR") or platformdirs.user_data_dir("mise", appauthor=False))
    paths.append(data / "shims")
    # bin-path欠落は毎回別途検査し、ツールの削除・追加はmise lsの実状態から検出する。
    return post_apply_cache.fingerprint(
        paths,
        values=[
            str(mise_bin),
            version.stdout,
            str(cwd),
            str(dotfiles_mode.is_minimal()),
            json.dumps(tools, sort_keys=True),
            json.dumps(setup_mise_common.mise_env_overrides(), sort_keys=True),
        ],
    )


__all__ = [
    "ensure_mise_installed",
    "ensure_tools_installed",
    "ensure_windows_user_path_has_shims",
    "working_tree_with_config",
    "find_mise_binary",
]
