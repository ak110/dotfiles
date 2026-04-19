"""`chezmoi apply` 後の後処理を一本化するエントリポイント。

`chezmoi apply` 完了後に毎回 1 回だけ呼ばれ、以下を順に実行する。
各ステップは独立して動作し、途中でエラーが発生しても他のステップは継続する
(最後にサマリで失敗件数を出力し、失敗があれば exit 1)。

1. Claude 設定ファイルのマージ  (update_claude_settings.run)
2. VSCode 設定ファイルの更新   (update_vscode_settings.run)
3. SSH config / authorized_keys  (update_ssh_config.run)
4. 配布元から削除された旧ファイルの削除 (cleanup_paths.cleanup_paths)
5. npm/pnpm のサプライチェーン対策 (update_npmrc.run)
6. mise セットアップ (setup_mise.run)
7. Claude Code plugin の自動インストール (install_claude_plugins.run)
8. codex MCP サーバーの自動登録 (install_codex_mcp.run)
9. Windows 向け libarchive.dll の自動インストール (install_libarchive_windows.run)
10. claude-plans-viewer の自動起動セットアップ (setup_plans_viewer_windows.run)

呼び出し元は `.chezmoi-source/run_after_post-apply.{sh,ps1}.tmpl` と
直接 CLI 実行 (`dotfiles-post-apply`) の 2 系統。
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
    setup_mise,
    setup_plans_viewer_windows,
    update_claude_settings,
    update_npmrc,
    update_vscode_settings,
)

logger = logging.getLogger(__name__)

# 配布元 (dotfiles) から削除されたため destination 側から除去するパス一覧。
# chezmoi は配布元から削除されたファイルを自動で削除しないため、本テーブルで追跡する。
# キーは削除対象のベースディレクトリ、値はそれに対する相対パスのリスト。
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
    ],
}

# 内容が期待値と完全一致する場合に限り削除するパス一覧。
# ユーザーが独自に編集している可能性があるファイルを保護するため、bytes 完全一致のときのみ削除する。
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
    """`_REMOVED_PATHS` と `_REMOVED_PATHS_IF_CONTENT` に従って旧配布物を削除する。

    Returns:
        いずれかのパスを実際に削除したかどうか。
    """
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
]


def _main(runner: Callable[[], list[_StepResult]] | None = None) -> None:
    """エントリポイント。各ステップを順に実行し、サマリ後に exit。"""
    # 全ログ行に 2 列分のインデントを付与する。
    # update-dotfiles の `=== [4/4] chezmoi apply ===` の下位出力であることを
    # 視覚的に示すため、post-apply 配下の出力はすべて 2 スペース下げる。
    logging.basicConfig(format="  %(message)s", level="INFO")
    results = (runner or run)()
    failed = [r for r in results if not r.ok]
    updated = [r for r in results if r.ok and r.changed]
    skipped = [r for r in results if r.ok and not r.changed]
    # サマリ前の空行は logger.info("") だと format により末尾空白が
    # 付与されてしまうため、stdout に直接書き込んで整形された空行を出力する。
    print(flush=True)
    logger.info("完了: 更新 %d 件 / スキップ %d 件 / 失敗 %d 件", len(updated), len(skipped), len(failed))
    _print_plugin_recommendations()
    if failed:
        logger.error("失敗したステップ: %s", ", ".join(r.name for r in failed))
        sys.exit(1)


def _print_plugin_recommendations() -> None:
    """``install_claude_plugins.run()`` が算出した推奨コマンドを案内表示する。

    エンドユーザー向けの案内文は敬体に揃え、現状と推奨状態に乖離がある場合のみ出力する。
    """
    recommendations = install_claude_plugins.consume_recommendations()
    if not recommendations:
        return
    print(flush=True)
    logger.info("推奨プラグイン設定に対し以下のコマンドの実行をおすすめします:")
    for cmd in recommendations:
        logger.info("  %s", cmd)


def run(steps: list[tuple[str, Callable[[], bool]]] | None = None) -> list[_StepResult]:
    """全ステップを順に実行し、結果のリストを返す。"""
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
