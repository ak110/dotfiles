"""`chezmoi apply` 後の後処理を一本化するエントリポイント。

`chezmoi apply` 完了後に毎回 1 度だけ呼ばれ、以下を順に実行する。
各ステップは独立して動き、途中でエラーが出ても他のステップは継続する
(最後にサマリで失敗件数を出し、失敗があれば exit 1)。

1. Claude 設定ファイルのマージ  (_update_claude_settings.run)
2. SSH config / authorized_keys  (update_ssh_config.run)
3. 配布元から消えた旧ファイルの掃除 (_cleanup_paths.cleanup_paths)
4. npm/pnpm のサプライチェーン対策 (_update_npmrc.run)
5. mise セットアップ (_setup_mise.run)
6. Claude Code plugin の自動インストール (_install_claude_plugins.run)

呼び出し元は `.chezmoi-source/run_after_post-apply.{sh,ps1}.tmpl` と
直接 CLI 実行 (`dotfiles-post-apply`) の 2 系統。
"""

import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pytools import (
    _cleanup_paths,
    _install_claude_plugins,
    _log_format,
    _setup_mise,
    _update_claude_settings,
    _update_npmrc,
    update_ssh_config,
)

logger = logging.getLogger(__name__)

# 配布元 (dotfiles) から削除されたため destination 側から除去したいパス一覧。
# chezmoi は配布元から消えたファイルを自動では削除しないため、本テーブルで追跡する。
# キーは掃除対象のベースディレクトリ、値はそれに対する相対パスのリスト。
_REMOVED_PATHS: dict[Path, list[Path]] = {
    Path.home() / ".claude": [
        # 過去に .chezmoi-source/dot_claude/ から配布していたが、プロジェクトローカルへ移したもの
        Path("skills/sync-platform-pair"),
        Path("skills/sync-rule-ssot"),
    ],
}


@dataclass
class _StepResult:
    name: str
    ok: bool
    changed: bool


def _main() -> None:
    """エントリポイント。各ステップを順に実行し、サマリ後に exit。"""
    # 全ログ行に列 2 のインデントを付与する。
    # update-dotfiles の `=== [4/4] chezmoi apply ===` の下位階層であることを
    # 視覚的に示すため、post-apply 配下の出力はすべて 2 スペース下げる。
    logging.basicConfig(format="  %(message)s", level="INFO")
    results = run()
    failed = [r for r in results if not r.ok]
    updated = [r for r in results if r.ok and r.changed]
    skipped = [r for r in results if r.ok and not r.changed]
    # サマリ前の空行は logger.info("") だと format により trailing whitespace が
    # 入るため、stdout に直接書き込んでクリーンな空行を出す。
    print(flush=True)
    logger.info("完了: 更新 %d 件 / スキップ %d 件 / 失敗 %d 件", len(updated), len(skipped), len(failed))
    if failed:
        logger.error("失敗したステップ: %s", ", ".join(r.name for r in failed))
        sys.exit(1)


def run() -> list[_StepResult]:
    """全ステップを順に実行し、結果のリストを返す。"""
    steps: list[tuple[str, Callable[[], bool]]] = [
        ("Claude 設定", _update_claude_settings.run),
        ("SSH config", update_ssh_config.run),
        ("旧配布物の掃除", _cleanup_removed_paths),
        ("npm/pnpm サプライチェーン対策", _update_npmrc.run),
        ("mise セットアップ", _setup_mise.run),
        ("Claude Code plugin のインストール", _install_claude_plugins.run),
    ]
    results: list[_StepResult] = []
    total = len(steps)
    for index, (name, func) in enumerate(steps, start=1):
        logger.info("[%d/%d] %s", index, total, name)
        try:
            changed = func()
        except Exception:  # noqa: BLE001 -- 他ステップを止めないため広く捕捉する
            logger.exception("    %s: 失敗", name)
            results.append(_StepResult(name=name, ok=False, changed=False))
            continue
        results.append(_StepResult(name=name, ok=True, changed=changed))
    return results


def _cleanup_removed_paths() -> bool:
    """`_REMOVED_PATHS` に従って旧配布物を掃除する。

    Returns:
        いずれかのパスを実際に削除したかどうか。
    """
    total_removed = 0
    for base_dir, relative_paths in _REMOVED_PATHS.items():
        total_removed += _cleanup_paths.cleanup_paths(base_dir, relative_paths)
    if total_removed == 0:
        logger.info(_log_format.format_status("cleanup", "削除対象なし"))
    else:
        logger.info(_log_format.format_status("cleanup", f"{total_removed} 件を削除しました"))
    return total_removed > 0


if __name__ == "__main__":
    _main()
