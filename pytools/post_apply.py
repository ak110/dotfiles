"""`chezmoi apply`後処理のエントリポイント。

各ステップは独立して動作し、途中で失敗しても他のステップは継続する。
"""

import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pytools import update_ssh_config
from pytools._internal import (
    cleanup_paths,
    cleanup_user_path,
    install_claude_plugins,
    install_codex_mcp,
    install_libarchive_windows,
    log_format,
    setup_bin_path,
    setup_codex_links,
    setup_feedback_inbox,
    setup_media_remote,
    setup_mise,
    setup_msys_env,
    setup_plans_viewer_linux,
    setup_registry,
    setup_sendto_shortcuts,
    setup_tmux_plugins,
    sync_agent_toolkit_rules,
    update_claude_settings,
    update_npmrc,
    update_vscode_settings,
)

logger = logging.getLogger(__name__)

# chezmoi は配布元から削除されたファイルを配布先から自動削除しないため、本テーブルで追跡する。
_REMOVED_PATHS: dict[Path, list[Path]] = {
    Path.home() / ".claude": [
        # プロジェクトローカルに存在し、.chezmoi-source/dot_claude/ の配布対象外とする。
        Path("skills/sync-platform-pair"),
        Path("skills/sync-rule-ssot"),
        # dotfiles ローカルの sync-cross-project skill が当該機能を担う (15ca58b)。
        Path("agents/cross-project-sync-checker.md"),
        # agent-basics → agent-toolkit のディレクトリ名リネームに伴い旧ディレクトリを削除する。
        # cleanup_paths.cleanup_paths は is_dir() の場合 shutil.rmtree を呼ぶため、
        # 配下の旧ルールファイル (python.md / claude-rules.md / markdown.md ほか) ごと一括で除去される。
        Path("rules/agent-basics"),
        # 再レビューは careful-spec-reviewer / careful-impl-reviewer の followup モードが担うため、
        # 配布先から旧エージェント定義を削除する。
        Path("agents/careful-followup-reviewer.md"),
        # 現在のスキル名は refine-prompt。配布先から旧スキルディレクトリを削除する。
        Path("skills/empirical-prompt-tuning"),
        # 現在の配布元は session-review-dotfiles（dotfiles 個人環境側）と
        # agent-toolkit プラグイン側の session-review に分かれている。
        # 旧配布先ディレクトリを削除する。
        Path("skills/session-review"),
        # 現在のスキル名は add-feedback。旧名 feedback-add の配布先ディレクトリを削除する。
        Path("skills/feedback-add"),
        # 現在のスキル名は process-feedbacks。旧名 process-feedback の配布先ディレクトリを削除する。
        Path("skills/process-feedback"),
        # 現在は dotfiles-fb process-loop CLI が常駐ループを担うため、
        # 旧 process-feedbacks-loop スキルの配布先ディレクトリを削除する。
        Path("skills/process-feedbacks-loop"),
    ],
    Path.home() / ".codex": [
        # Codexの rules/ は prefix_rule 形式の承認ルール用ディレクトリであり、
        # Claude Code向けMarkdownルールとは互換性がない。
        # 共有ルールは .codex/agent-toolkit/rules 配下に置く。
        Path("rules/agent-toolkit"),
        # dotfilesリポジトリ専用スキルはプロジェクト直下の .agents/skills に置く。
        # ~/.codex/skills はグローバルに使うスキルだけを置く。
        Path("skills/sync-platform-pair"),
        Path("skills/sync-rule-ssot"),
        # 現在のスキル名は plan-impl。旧名 careful-impl の配布先リンクを除去する。
        Path("skills/careful-impl"),
        # 現在のスキル名は agent-standards。旧名 claude-code-standards の配布先リンクを除去する。
        Path("skills/claude-code-standards"),
        # 現在のスキル名は session-review-dotfiles。旧名 session-review の配布先リンクを除去する。
        Path("skills/session-review"),
        # 現在のスキル名は add-feedback。旧名 feedback-add の配布先リンクを除去する。
        Path("skills/feedback-add"),
        # 現在のスキル名は process-feedbacks。旧名 process-feedback の配布先リンクを除去する。
        Path("skills/process-feedback"),
    ],
    Path.home() / ".config": [
        # pyfltr v3.14.1で口語表現チェッカーが内蔵化されたため
        # dotfiles配布のカスタムコマンド定義（旧`config.toml`）を配布先から除去する。
        Path("pyfltr/config.toml"),
    ],
    Path.home() / "bin": [
        # pre-commit からしか呼ばれない開発者向けツールのため scripts/ 配下に置き、
        # .chezmoi-source/bin/ の配布対象外とする。
        Path("check-cmd-encoding"),
        Path("check-templates"),
        Path("run-psscriptanalyzer"),
        # bin/ はリポジトリ直下に置き、~/dotfiles/bin を PATH に通す方式を採用する。
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
        # pytools/ パッケージ化 (fe09fa3) 以降 .chezmoi-source/bin/ の配布対象外となった旧配布物。
        # 現在は pytools/ の CLI として uv tool install 経由で ~/.local/bin 等に配置される。
        Path("check-image-sizes.py"),
        Path("dpkg-licenses"),
        Path("git-justify.py"),
        Path("mvdir.py"),
        Path("update-ssh-config"),
        Path("update-ssh-config.cmd"),
        Path("update-ssh-config.py"),
    ],
}

# ユーザーの独自編集を保護するため、内容が期待値と bytes 完全一致するときのみ削除する。
_REMOVED_PATHS_IF_CONTENT: dict[Path, dict[Path, bytes]] = {
    Path.home() / ".claude": {
        # `.chezmoi-source/dot_claude/CLAUDE.md` は配布対象外。未編集の配布先を除去する。
        # 「簡潔に」応答を強制する指示はハルシネーション耐性を下げるため不要 (Giskard Phare)。
        Path("CLAUDE.md"): ("# カスタム指示\n\n- シンプルに要点のみを述べる\n".encode()),
    },
    # claude-plans-viewer 自動起動セットアップ（旧 setup_plans_viewer_windows）で
    # スタートアップフォルダーへ配置していた .cmd を、未編集なら除去する。
    # 旧モジュールの削除に伴い配布物としての保守元がないため。
    Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup": {
        Path("claude-plans-viewer.cmd"): (b'@echo off\r\nstart "" "%USERPROFILE%\\.local\\bin\\claude-plans-viewer.exe"\r\n'),
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
        logger.info(log_format.format_status("cleanup", f"{total_removed} 件を削除した"))
    return total_removed > 0


# ステップ関数の戻り値型。通常ステップは bool、install_claude_plugins だけ推奨コマンドを含むタプルを返す。
StepReturn = bool | tuple[bool, list[str]]

_DEFAULT_STEPS: list[tuple[str, Callable[[], StepReturn]]] = [
    ("bin PATH 登録 (Windows)", setup_bin_path.run),
    ("MSYS 環境変数 (Windows)", setup_msys_env.run),
    ("Claude 設定", update_claude_settings.run),
    ("VSCode 設定", update_vscode_settings.run),
    ("SSH config", update_ssh_config.run),
    ("旧配布物の削除", _cleanup_removed_paths),
    ("npm/pnpm サプライチェーン対策", update_npmrc.run),
    ("mise セットアップ", setup_mise.run),
    ("agent-toolkit ルールの同期", sync_agent_toolkit_rules.run),
    ("Codex リンクの同期", setup_codex_links.run),
    ("tmux プラグインの導入 (Linux)", setup_tmux_plugins.run),
    ("Claude Code plugin のインストール", install_claude_plugins.run),
    ("codex MCP サーバーの登録", install_codex_mcp.run),
    ("libarchive (Windows)", install_libarchive_windows.run),
    ("claude-plans-viewer 自動起動セットアップ (Linux)", setup_plans_viewer_linux.run),
    ("Windowsレジストリ設定", setup_registry.run),
    ("SendTo ショートカット (Windows)", setup_sendto_shortcuts.run),
    ("フィードバック蓄積セットアップ (特定ホスト)", setup_feedback_inbox.run),
    ("メディアリモコン自動起動 (Windows/stheno)", setup_media_remote.run),
    # 他ステップが PATH 追加を行うため、それらの後に整理を実行する。
    ("ユーザー PATH 整理 (Windows)", cleanup_user_path.run),
]


def main(runner: Callable[[], tuple[list[_StepResult], list[str]]] | None = None) -> None:
    """エントリポイント。"""
    # update-dotfiles 配下の出力であることを示すため、全ログ行を 2 スペース下げる。
    logging.basicConfig(format="  %(message)s", level="INFO")
    results, recommendations = (runner or run)()
    failed = [r for r in results if not r.ok]
    updated = [r for r in results if r.ok and r.changed]
    skipped = [r for r in results if r.ok and not r.changed]
    # logger.info("") だと format により末尾空白が付与されるため、stdout に直接出力する。
    print(flush=True)
    logger.info("完了: 更新 %d 件 / スキップ %d 件 / 失敗 %d 件", len(updated), len(skipped), len(failed))
    _print_plugin_recommendations(recommendations)
    if failed:
        logger.error("失敗したステップ: %s", ", ".join(r.name for r in failed))
        sys.exit(1)
    sys.exit(0)


def _print_plugin_recommendations(recommendations: list[str]) -> None:
    """``install_claude_plugins.run()`` が算出した推奨コマンドを案内表示する。"""
    # エンドユーザー向け案内のため敬体。
    if not recommendations:
        return
    print(flush=True)
    logger.info("推奨プラグイン設定:")
    # コマンド行はそのままコピー&ペーストで実行されるため、basicConfig のインデントを避けて
    # stdout に直接出力する。cmd.exe では `^` 継続後に行頭空白が前行へ連結されたまま残り、
    # `&& <空白>...` の空白がコマンド名として解釈されて貼り付けが失敗するため、行頭は無インデントとする。
    if len(recommendations) == 1:
        print(recommendations[0], flush=True)
        return
    # 利用者がコピペ1回で全件実行できるよう && で連結し、可読性のため行末継続記号で改行する。
    # 継続記号はシェル別に切り替える (bash: \, cmd: ^)。
    continuation = "^" if sys.platform == "win32" else "\\"
    last_index = len(recommendations) - 1
    for index, cmd in enumerate(recommendations):
        if index == last_index:
            print(cmd, flush=True)
        else:
            print(f"{cmd} && {continuation}", flush=True)


def run(steps: list[tuple[str, Callable[[], StepReturn]]] | None = None) -> tuple[list[_StepResult], list[str]]:
    """各ステップを順に実行し、`(results, recommendations)` を返す。

    `recommendations` は ``install_claude_plugins.run()`` が算出した推奨コマンド列。
    ``install_claude_plugins.run`` は ``tuple[bool, list[str]]`` を返すため、
    タプルの戻り値を持つステップは推奨コマンドとして収集する。
    """
    effective_steps = steps if steps is not None else _DEFAULT_STEPS
    results: list[_StepResult] = []
    recommendations: list[str] = []
    total = len(effective_steps)
    for index, (name, func) in enumerate(effective_steps, start=1):
        logger.info("[%d/%d] %s", index, total, name)
        try:
            ret = func()
        except Exception:  # noqa: BLE001 -- 他ステップを止めないため広く捕捉する
            logger.exception("    %s: 失敗", name)
            results.append(_StepResult(name=name, ok=False, changed=False))
            continue
        # install_claude_plugins.run() は (changed, recommendations) を返す。
        # 他のステップは bool を返すため isinstance で振り分ける。
        if isinstance(ret, tuple):
            changed, step_recs = ret
            recommendations.extend(step_recs)
        else:
            changed = ret
        results.append(_StepResult(name=name, ok=True, changed=changed))
    return results, recommendations


if __name__ == "__main__":
    main()
