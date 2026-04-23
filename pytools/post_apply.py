"""`chezmoi apply` 後処理を集約するエントリポイント。

`chezmoi apply` 完了後に毎回 1 回呼ばれ、各ステップは独立して動作する。
途中でエラーが発生しても他のステップは継続し、最後のサマリで失敗件数を出力して
失敗があれば exit 1 する。実行順・対象ステップは `_DEFAULT_STEPS` を SSOT とする。
"""

import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pytools import update_ssh_config
from pytools._internal import (
    cleanup_paths,
    install_claude_plugins,
    install_codex_mcp,
    install_libarchive_windows,
    log_format,
    setup_bin_path,
    setup_mise,
    setup_plans_viewer_windows,
    setup_winget_dsc,
    update_claude_settings,
    update_npmrc,
    update_vscode_settings,
)

logger = logging.getLogger(__name__)

# chezmoi は配布元から削除されたファイルを destination から自動削除しないため、本テーブルで追跡する。
_REMOVED_PATHS: dict[Path, list[Path]] = {
    Path.home() / ".claude": [
        # 過去に .chezmoi-source/dot_claude/ から配布していたが、プロジェクトローカルへ移したもの
        Path("skills/sync-platform-pair"),
        Path("skills/sync-rule-ssot"),
        # 配布 agent から dotfiles ローカルの sync-cross-project skill へ移行 (15ca58b)
        Path("agents/cross-project-sync-checker.md"),
        # agent-basics から agent-toolkit へのディレクトリ名リネームに伴う旧ディレクトリ削除。
        # cleanup_paths.cleanup_paths は is_dir() の場合 shutil.rmtree を呼ぶため、
        # 配下の旧ルールファイル (python.md / claude-rules.md / markdown.md ほか) ごと一括で除去される。
        Path("rules/agent-basics"),
    ],
    Path.home() / "bin": [
        # 過去に .chezmoi-source/bin/ から配布していたが、pre-commit からしか呼ばれない
        # 開発者向けツールのため scripts/ に移し、配布を停止したもの
        Path("check-cmd-encoding"),
        Path("check-templates"),
        Path("run-psscriptanalyzer"),
        # bin/ をリポジトリ直下へ移し、~/dotfiles/bin を PATH に通す方式へ変更したため
        # 旧配布物 (~/bin/ 配下) を削除する。Linux 用と Windows 用 (.cmd) を共通キーで列挙する。
        Path("c"),
        Path("c.cmd"),
        Path("ccusage"),
        Path("ccusage.cmd"),
        Path("check-gh-actions"),
        Path("claude-code-viewer"),
        Path("claude-code-viewer.cmd"),
        Path("countfiles"),
        Path("git_find_big.sh"),
        Path("gpuwatch"),
        Path("ipy"),
        Path("lab"),
        Path("lab-bg"),
        Path("rdp"),
        Path("remote-plans.cmd"),
        Path("sonnet"),
        Path("sonnet.cmd"),
        Path("sudoll"),
        Path("update-dotfiles"),
        Path("update-dotfiles.cmd"),
    ],
}

# ユーザーの独自編集を保護するため、内容が期待値と bytes 完全一致するときのみ削除する。
_REMOVED_PATHS_IF_CONTENT: dict[Path, dict[Path, bytes]] = {
    Path.home() / ".claude": {
        # `.chezmoi-source/dot_claude/CLAUDE.md` を削除したため、未編集の配布先を除去する。
        # 「簡潔に」応答を強制する指示はハルシネーション耐性を下げるため撤廃 (Giskard Phare)。
        Path("CLAUDE.md"): ("# カスタム指示\n\n- シンプルに要点のみを述べる\n".encode()),
    },
}


@dataclass
class _StepResult:
    name: str
    ok: bool
    changed: bool


def _cleanup_removed_paths() -> bool:
    """`_REMOVED_PATHS` / `_REMOVED_PATHS_IF_CONTENT` に従って旧配布物を削除する。"""
    total_removed = 0
    for base_dir, relative_paths in _REMOVED_PATHS.items():
        total_removed += cleanup_paths.cleanup_paths(base_dir, relative_paths)
    for base_dir, expected in _REMOVED_PATHS_IF_CONTENT.items():
        total_removed += cleanup_paths.cleanup_paths_if_content_matches(base_dir, expected)
    if total_removed == 0:
        logger.info(log_format.format_status("cleanup", "削除対象なし"))
    else:
        logger.info(log_format.format_status("cleanup", f"{total_removed} 件を削除しました"))
    return total_removed > 0


_DEFAULT_STEPS: list[tuple[str, Callable[[], bool]]] = [
    ("bin PATH 登録 (Windows)", setup_bin_path.run),
    ("Claude 設定", update_claude_settings.run),
    ("VSCode 設定", update_vscode_settings.run),
    ("SSH config", update_ssh_config.run),
    ("旧配布物の削除", _cleanup_removed_paths),
    ("npm/pnpm サプライチェーン対策", update_npmrc.run),
    ("mise セットアップ", setup_mise.run),
    ("Claude Code plugin のインストール", install_claude_plugins.run),
    ("codex MCP サーバーの登録", install_codex_mcp.run),
    ("libarchive (Windows)", install_libarchive_windows.run),
    ("claude-plans-viewer 自動起動セットアップ", setup_plans_viewer_windows.run),
    ("winget configure (Windows)", setup_winget_dsc.run),
]


def _main(runner: Callable[[], list[_StepResult]] | None = None) -> None:
    """エントリポイント。"""
    # update-dotfiles 配下の出力であることを示すため、全ログ行を 2 スペース下げる。
    logging.basicConfig(format="  %(message)s", level="INFO")
    results = (runner or run)()
    failed = [r for r in results if not r.ok]
    updated = [r for r in results if r.ok and r.changed]
    skipped = [r for r in results if r.ok and not r.changed]
    # logger.info("") だと format により末尾空白が付与されるため、stdout に直接書き出す。
    print(flush=True)
    logger.info("完了: 更新 %d 件 / スキップ %d 件 / 失敗 %d 件", len(updated), len(skipped), len(failed))
    _print_plugin_recommendations()
    if failed:
        logger.error("失敗したステップ: %s", ", ".join(r.name for r in failed))
        sys.exit(1)


def _print_plugin_recommendations() -> None:
    """``install_claude_plugins.run()`` が算出した推奨コマンドを案内表示する。"""
    # エンドユーザー向け案内のため敬体。
    recommendations = install_claude_plugins.consume_recommendations()
    if not recommendations:
        return
    print(flush=True)
    logger.info("推奨プラグイン設定に対し以下のコマンドの実行をおすすめします:")
    for cmd in recommendations:
        logger.info("  %s", cmd)


def run(steps: list[tuple[str, Callable[[], bool]]] | None = None) -> list[_StepResult]:
    """各ステップを順に実行し、結果のリストを返す。"""
    effective_steps = steps if steps is not None else _DEFAULT_STEPS
    results: list[_StepResult] = []
    total = len(effective_steps)
    for index, (name, func) in enumerate(effective_steps, start=1):
        logger.info("[%d/%d] %s", index, total, name)
        try:
            changed = func()
        except Exception:  # noqa: BLE001 -- 他ステップを止めないため広く捕捉する
            logger.exception("    %s: 失敗", name)
            results.append(_StepResult(name=name, ok=False, changed=False))
            continue
        results.append(_StepResult(name=name, ok=True, changed=changed))
    return results


if __name__ == "__main__":
    _main()
