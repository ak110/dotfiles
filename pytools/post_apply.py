"""`chezmoi apply`後処理のエントリポイント。

各ステップは独立して動作し、途中で失敗しても他のステップは継続する。
ステップ間の順序は先行工程の宣言（`_StepSpec.after`）で表し、互いに依存しない
ステップは同時に実行する。画面には各ステップの出力を列挙順にまとめて表示する。
"""

import argparse
import io
import logging
import logging.handlers
import os
import sys
import threading
import time
import traceback
from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path

import platformdirs

from pytools import update_ssh_config
from pytools._internal import (
    claude_common,
    cleanup_paths,
    cleanup_user_path,
    install_claude_plugins,
    install_codex_plugins,
    install_libarchive_windows,
    log_format,
    post_apply_outcome,
    prune_claude_plugin_cache,
    remove_codex_claude_mcp,
    remove_legacy_codex_mcp_from_claude,
    restore_codex_logs_linux,
    setup_agy_cli,
    setup_atk_serve_linux,
    setup_bin_path,
    setup_claude_cli,
    setup_codex_cli,
    setup_codex_links,
    setup_dotfiles_autoupdate_linux,
    setup_herdr_cli,
    setup_media_remote,
    setup_mise,
    setup_msys_env,
    setup_registry,
    setup_sendto_shortcuts,
    setup_statusline_binary,
    setup_tmux_plugins,
    sync_agent_toolkit_rules,
    update_claude_settings,
    update_npmrc,
    update_vscode_settings,
    warm_agents_server,
    warm_pyfltr_mcp,
    warmup_hook_scripts,
)
from scripts import sync_codex_plugin_manifests, sync_report

logger = logging.getLogger(__name__)

_UPDATE_LOG_PATH = Path(platformdirs.user_state_dir("agent-toolkit", appauthor=False)) / "update-dotfiles.log"
_UPDATE_RUN_ID_ENV = "UPDATE_DOTFILES_RUN_ID"
_UPDATE_LOG_MAX_BYTES = 2 * 1024 * 1024
_UPDATE_LOG_BACKUP_COUNT = 3


def _run_id() -> str:
    """`update-dotfiles`が渡した実行識別子を返す。単独実行では自プロセスから組み立てる。"""
    return os.environ.get(_UPDATE_RUN_ID_ENV, f"post-apply-{os.getpid()}")


class _BelowWarningFilter(logging.Filter):
    """WARNING未満のレコードだけを通す。"""

    def filter(self, record: logging.LogRecord) -> bool:
        """レコードがstdout側のレベル範囲なら真を返す。"""
        return record.levelno < logging.WARNING


class _ScreenFilter(logging.Filter):
    """利用者の判断に使わない行を画面から外す。永続ログのハンドラーには付けない。"""

    def filter(self, record: logging.LogRecord) -> bool:
        """画面へ出力するレコードなら真を返す。"""
        if log_format.is_log_only(record):
            return False
        # 依存ライブラリhttpxのリクエスト記録。WARNING以上は標準エラー側で表示する。
        return not (record.levelno < logging.WARNING and (record.name == "httpx" or record.name.startswith("httpx.")))


def _configure_logging() -> tuple[list[logging.Handler], int, bool]:
    """ログを出力先で分離し、符号化不能文字でレコードを欠落させない。"""
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(errors="backslashreplace")
    formatter = logging.Formatter("  %(message)s")
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(logging.DEBUG)
    stdout_handler.addFilter(_BelowWarningFilter())
    stdout_handler.addFilter(_ScreenFilter())
    stdout_handler.setFormatter(formatter)
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.WARNING)
    stderr_handler.setFormatter(formatter)
    run_id = _run_id()
    handlers: list[logging.Handler] = [stdout_handler, stderr_handler]
    persistent_log_ready = False
    try:
        _UPDATE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            _UPDATE_LOG_PATH,
            maxBytes=_UPDATE_LOG_MAX_BYTES,
            backupCount=_UPDATE_LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
    except OSError as error:
        print(f"  永続ログを開始できませんでした: {_UPDATE_LOG_PATH}: {error}", file=sys.stderr)
    else:
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(logging.Formatter(f"%(asctime)s run={run_id} %(levelname)s %(message)s"))
        handlers.append(file_handler)
        persistent_log_ready = True
    root_logger = logging.getLogger()
    previous_handlers = root_logger.handlers.copy()
    previous_level = root_logger.level
    root_logger.handlers[:] = handlers
    root_logger.setLevel(logging.INFO)
    return previous_handlers, previous_level, persistent_log_ready


# chezmoi は配布元から削除されたファイルを配布先から自動削除しないため、本テーブルで追跡する。
_REMOVED_PATHS: dict[Path, list[Path]] = {
    Path.home() / "dotfiles": [
        # dotfiles固有hookはpytoolsのconsole scriptへ移設したため、旧入口を除去する。
        Path("scripts/claude_hook.py"),
        Path("scripts/claude_hook_pretooluse.py"),
        Path("scripts/claude_hook_posttooluse.py"),
        Path("scripts/claude_hook_stop_bell.py"),
    ],
    Path.home() / ".claude": [
        # `references/`はスキル配下だけの名前としたため、旧配布先のディレクトリを削除する。
        Path("references"),
        # プロジェクトローカルに存在し、.chezmoi-source/dot_claude/ の配布対象外とする。
        Path("skills/sync-platform-pair"),
        Path("skills/sync-rule-ssot"),
        # dotfiles ローカルの ak110-projects-operations skill がこの機能を担う (15ca58b)。
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
        # 2つのスキルはagent-toolkit pluginへ移設したため、dotfiles側の旧配布先を削除する。
        Path("skills/refine-prompt"),
        Path("skills/export-session"),
        # 振り返りはagent-toolkit側のsession-reviewへ統合したため旧配布先を削除する。
        Path("skills/session-review"),
        Path("skills/session-review-dotfiles"),
        # 現在のスキル名は add-awi。旧名 feedback-add の配布先ディレクトリを削除する。
        Path("skills/feedback-add"),
        # 現在のスキル名は process-wi。旧名 process-feedback の配布先ディレクトリを削除する。
        Path("skills/process-feedback"),
        # 現在は atk wi process-loop CLI が常駐ループを担うため、
        # 旧 process-feedbacks-loop スキルの配布先ディレクトリを削除する。
        Path("skills/process-feedbacks-loop"),
        # add-awi・process-wi スキルは agent-toolkit/skills/ 配下へ移設済み。
        # 旧配布先 (dotfiles-fb 系スキル) の配布先ディレクトリを削除する。
        Path("skills/add-feedback"),
        Path("skills/process-feedbacks"),
        # 現在のスキル名は ak110-projects-operations。旧名の配布先を削除する。
        Path("skills/sync-cross-project"),
        # 02-claude-code.md / 03-styles.md / 04-terminology.md →
        # 03-claude-code.md / 04-styles.md / 05-terminology.md リネームに伴い旧ファイルを削除する。
        Path("rules/agent-toolkit/02-claude-code.md"),
        Path("rules/agent-toolkit/03-styles.md"),
        Path("rules/agent-toolkit/04-terminology.md"),
        # autopilotスキルは協調・自律の規範へ吸収し廃止。配布先から旧スキルディレクトリを削除する。
        Path("skills/autopilot"),
        # ルール層を01-agent.md / 02-claude-code.mdの2ファイルへ統合したため、
        # 統合元の旧ファイルを配布先から削除する。
        # chezmoiは配布元の削除を配布先へ伝播しないため、本一覧への登録が必要となる。
        Path("rules/agent-toolkit/02-collaboration.md"),
        Path("rules/agent-toolkit/03-claude-code.md"),
        Path("rules/agent-toolkit/04-styles.md"),
        Path("rules/agent-toolkit/05-terminology.md"),
        Path("rules/agent-toolkit/06-monitoring.md"),
    ],
    Path.home() / ".codex": [
        # Codexの rules/ は prefix_rule 形式の承認ルール用ディレクトリであり、
        # Claude Code向けMarkdownルールとは互換性がない。
        # 共有ルールは .codex/agent-toolkit/rules 配下に置く。
        Path("rules/agent-toolkit"),
        # agent定義（feedbacks-planner・plan-executor・plan-review-executor）を廃止し、
        # agent-toolkit/agents ディレクトリごと除去したため、旧配布先リンクを除去する。
        Path("agent-toolkit/agents"),
        # dotfilesリポジトリ専用スキルはプロジェクト直下の .agents/skills に置く。
        # ~/.codex/skills はグローバルに使うスキルだけを置く。
        Path("skills/sync-platform-pair"),
        Path("skills/sync-rule-ssot"),
        # 旧名careful-implの後継スキル名はplan-implだったが、
        # plan-implもagentsへ移植し廃止したため配布先リンクを除去する。
        Path("skills/careful-impl"),
        # 現在のスキル名はwriting-standards。旧名claude-code-standardsの配布先リンクを除去する。
        Path("skills/claude-code-standards"),
        # plan-impl・plan-codex-reviewはagentsへ移植し、fork型スキルとしては廃止した。
        # 旧配布先リンクを除去する。
        Path("skills/plan-impl"),
        Path("skills/plan-codex-review"),
        # 2つのスキルはagent-toolkit pluginへ移設したため、旧リンクを削除する。
        Path("skills/refine-prompt"),
        Path("skills/export-session"),
        # 振り返りはagent-toolkit側のsession-reviewへ統合したため旧配布先リンクを除去する。
        Path("skills/session-review"),
        Path("skills/session-review-dotfiles"),
        # 現在のスキル名は add-awi。旧名 feedback-add の配布先リンクを除去する。
        Path("skills/feedback-add"),
        # 現在のスキル名は process-wi。旧名 process-feedback の配布先リンクを除去する。
        Path("skills/process-feedback"),
        # 現在のスキル名は ak110-projects-operations。旧名の配布先リンクを削除する。
        Path("skills/sync-cross-project"),
    ],
    Path.home() / ".config": [
        # pyfltr v3.14.1で口語表現チェッカーが内蔵化されたため
        # dotfiles配布のカスタムコマンド定義（旧`config.toml`）を配布先から除去する。
        Path("pyfltr/config.toml"),
        # 計画ファイル閲覧は atk serve へ統合した。設定は ~/.config/agent-toolkit/serve.toml へ移した。
        Path("pytools/claude-plans-viewer.toml"),
        # 廃止した工程が配置したフラグファイル。いずれも廃止時に削除登録がなく残存していた。
        # feedback-inbox.enabled は setup_feedback_inbox（d08f8d5b で廃止）、
        # review-balance-mode.claude-heavy は setup_review_balance_mode（4c53faaa で廃止）が配置した。
        Path("agent-toolkit/feedback-inbox.enabled"),
        Path("agent-toolkit/review-balance-mode.claude-heavy"),
    ],
    Path.home() / ".ipython": [
        Path("profile_default/startup/README"),
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
    Path.home() / ".local" / "bin": [
        # 計画ファイル閲覧は atk serve へ統合したため、旧 CLI の配布先を除去する。
        Path("claude-plans-viewer"),
        Path("claude-plans-viewer.exe"),
        # 15c2e214 が atk serve 常駐用に生成したランチャー ~/.local/bin/atk は、1116f984 で
        # ~/.local/bin/atk-serve へ改名した際に旧名の削除が漏れて残存していた。
        # dotfiles ホストでは ~/dotfiles/agent-toolkit/bin（Linux は .chezmoi-source/dot_bashrc、
        # Windows は pytools/_internal/setup_bin_path.py）が PATH へ登録されるため、PATH の先頭側にある
        # ~/.local/bin 配下の atk は作業ツリー版を覆い隠す。atk.cmd は install-claude.ps1 が同じ位置へ
        # 生成する Windows 版ラッパーであり、同じ理由で除去する。atk-serve と atk-hook は対象外とする。
        Path("atk"),
        Path("atk.cmd"),
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
    notices: tuple[post_apply_outcome.PostApplyNotice, ...] = ()
    # 失敗の内容は同期結果の記録へ残すため、ログ出力とは別に保持する。
    reason: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class _StepSpec:
    """post-applyの1ステップ。

    `after`は開始前に完了している必要があるステップ名、`after_all_preceding`は列挙順で前にある
    全ステップを先行工程とする指定、`platforms`は実行対象の`sys.platform`値（空なら全OS）を表す。
    `host_resources`は、HOMEで解決されない実機の共有資源（systemdのユーザーマネージャー、`/dev/shm`など）を
    操作するステップであることを表す。HOMEを差し替えた実行（手動観測やテスト）ではこのステップを実行しない。
    `systemctl --user`はHOMEではなく`XDG_RUNTIME_DIR`とD-Busで接続先を決めるため、HOMEの差し替えでは隔離できない。
    """

    name: str
    run: Callable[[], "StepReturn"]
    after: tuple[str, ...] = ()
    after_all_preceding: bool = False
    platforms: tuple[str, ...] = ()
    host_resources: bool = False


def _cleanup_removed_paths() -> bool:
    """`_REMOVED_PATHS` / `_REMOVED_PATHS_IF_CONTENT` に従って旧配布物を削除する。"""
    total_removed = 0
    for base_dir, relative_paths in _REMOVED_PATHS.items():
        total_removed += cleanup_paths.cleanup_paths(base_dir, relative_paths)
    for base_dir, expected in _REMOVED_PATHS_IF_CONTENT.items():
        total_removed += cleanup_paths.cleanup_paths_if_content_matches(base_dir, expected)
    # 配布済みREADMEの親だけを深い順で除去する。rmdirにより利用者ファイルが残るディレクトリは保持する。
    ipython_dir = Path.home() / ".ipython"
    try:
        ipython_resolved = ipython_dir.resolve()
    except OSError as error:
        logger.warning("%s の検査に失敗したため空ディレクトリの削除をスキップします: %s", ipython_dir, error)
        return total_removed > 0
    for relative_dir in (Path("profile_default/startup"), Path("profile_default")):
        target = ipython_dir / relative_dir
        try:
            target.resolve().relative_to(ipython_resolved)
        except ValueError:
            logger.warning("%s は %s 配下ではないためスキップします", target, ipython_dir)
            continue
        except OSError as error:
            logger.warning("%s の検査に失敗したためスキップします: %s", target, error)
            continue
        try:
            target.rmdir()
        except OSError:
            continue
        logger.info(log_format.format_status(log_format.home_short(target), "空の旧配布先を削除"))
        total_removed += 1
    if total_removed == 0:
        logger.info(log_format.format_status("cleanup", "削除対象なし"))
    else:
        logger.info(log_format.format_status("cleanup", f"{total_removed} 件を削除した"))
    return total_removed > 0


# ステップ関数の戻り値型。通常ステップは bool、個別の出力を持つステップは構造化した値を返す。
StepReturn = bool | tuple[bool, list[str]] | post_apply_outcome.PostApplyOutcome

_WINDOWS = ("win32",)
_LINUX = ("linux",)
_MISE = "mise セットアップ"
_CODEX_CLI = "Codex CLI の導入と更新"
_CLAUDE_CLI = "Claude Code CLI の導入と更新"
_CLAUDE_PLUGIN = "Claude Code plugin のインストール"
_CODEX_PLUGIN = "Codex plugin のインストール"
_CODEX_LINKS = "Codex リンクの同期"
_CLEANUP = "旧配布物の削除"

# 先行工程は、同じ資源（設定ファイルの読み書き、プロセスとユーザーのPATH、npmとmiseの管理領域、
# plugin cache、Claude Code pluginの複製元である`agent-toolkit/`（`.venv`を含む）、systemd、
# codexプロセスの稼働判定）を扱うステップの組と、先行ステップが導入する実行ファイルを使うステップへ宣言する。
# 資源は子プロセスやサービスの再起動を経由して間接的に書き換える場合も含める。
# 宣言の無いステップは他と同時に実行してよい。
_DEFAULT_STEPS: list[_StepSpec] = [
    _StepSpec("bin PATH 登録 (Windows)", setup_bin_path.run, platforms=_WINDOWS),
    _StepSpec("MSYS 環境変数 (Windows)", setup_msys_env.run, platforms=_WINDOWS),
    _StepSpec("VSCode 設定", update_vscode_settings.run),
    _StepSpec("SSH config", update_ssh_config.run),
    _StepSpec(_CLEANUP, _cleanup_removed_paths),
    _StepSpec("npm/pnpm サプライチェーン対策", update_npmrc.run),
    # Windowsでは bin PATH 登録と同じユーザーPATHを読んで書き戻す。
    _StepSpec(_MISE, setup_mise.run, after=("npm/pnpm サプライチェーン対策", "bin PATH 登録 (Windows)")),
    # miseのinstalls・shimsを操作し、codexを起動するため診断ログの復元後に実行する。
    _StepSpec(_CODEX_CLI, setup_codex_cli.run, after=(_MISE, "Codex 診断ログの通常ストレージ復元 (Linux)")),
    _StepSpec("Codex の Claude MCP 登録削除", remove_codex_claude_mcp.run, after=(_CODEX_CLI,)),
    # 旧npm版の除去がmise管理のNode配下のnpmを使う。
    _StepSpec(_CLAUDE_CLI, setup_claude_cli.run, after=(_MISE,)),
    _StepSpec("Antigravity CLI の導入", setup_agy_cli.run),
    _StepSpec("Herdr CLI の導入と更新", setup_herdr_cli.run),
    # 旧配布物の削除と同じ配布先ディレクトリの旧ファイルを削除する。
    _StepSpec("agent-toolkit ルールの同期", sync_agent_toolkit_rules.run, after=(_CLEANUP,)),
    _StepSpec(_CODEX_LINKS, setup_codex_links.run),
    _StepSpec(
        "Codex 診断ログの通常ストレージ復元 (Linux)",
        restore_codex_logs_linux.run,
        after=(_CODEX_LINKS,),
        platforms=_LINUX,
        host_resources=True,
    ),
    _StepSpec("tmux プラグインの導入 (Linux)", setup_tmux_plugins.run, platforms=_LINUX),
    _StepSpec(_CLAUDE_PLUGIN, install_claude_plugins.run, after=(_CLAUDE_CLI,)),
    # installed_plugins.json が更新後の版を指してから現行版を判定する。
    _StepSpec("Claude Code plugin cache の旧版削除", prune_claude_plugin_cache.run, after=(_CLAUDE_PLUGIN,)),
    # plugin導入が`agent-toolkit/`を複製する間に同じ配下の派生ファイルを書き換えない。
    _StepSpec("Codex plugin snapshot の生成", sync_codex_plugin_manifests.sync, after=(_CLAUDE_PLUGIN,)),
    # 稼働判定の前にCodex CLI工程とMCP照会を終え、後続のwarmupのCodex照会と重ねない。
    # 診断ログの稼働判定もCodex CLI工程より先に終わるため、この順序を共有する。
    _StepSpec(
        _CODEX_PLUGIN,
        install_codex_plugins.run,
        after=("Codex の Claude MCP 登録削除", "Codex plugin snapshot の生成", _CODEX_LINKS, _CLEANUP),
    ),
    _StepSpec("agents_serverのuv環境ウォームアップ", warm_agents_server.run, after=(_CLAUDE_PLUGIN, _CODEX_PLUGIN)),
    # 両ウォームアップは同じcache版ディレクトリで`uv run --project`を実行するため順に行う。
    _StepSpec(
        "hookスクリプトのuv環境ウォームアップ",
        warmup_hook_scripts.run,
        after=("agents_serverのuv環境ウォームアップ",),
    ),
    # 両pluginの導入後の参照先にあるMCP定義を読む。uvのキャッシュだけへ作用する。
    _StepSpec("pyfltr MCPのuv環境ウォームアップ", warm_pyfltr_mcp.run, after=(_CLAUDE_PLUGIN, _CODEX_PLUGIN)),
    _StepSpec(
        "旧Codex User scope MCP登録の移行",
        remove_legacy_codex_mcp_from_claude.run,
        after=(_CLAUDE_CLI, _CODEX_PLUGIN),
    ),
    # settings.json（plugin導入）と~/.claude.json（MCP移行）を読んでマージして書き戻す。
    _StepSpec("Claude 設定", update_claude_settings.run, after=(_CLAUDE_PLUGIN, "旧Codex User scope MCP登録の移行")),
    _StepSpec("libarchive (Windows)", install_libarchive_windows.run, after=(_MISE,), platforms=_WINDOWS),
    # 開発版の取得はmise経由でcargoを使うため、Codex CLI工程のmise操作の後に行う。
    _StepSpec("claude-statusline バイナリの取得", setup_statusline_binary.run, after=(_CODEX_CLI,)),
    # サービスの再起動で起動する`uv run --project <dotfiles>/agent-toolkit`が`agent-toolkit/.venv`を再同期するため、
    # `claude plugin install`・`update`が同じ`agent-toolkit/`を複製し終えてから実行する。
    _StepSpec(
        "atk serve 自動起動セットアップ (Linux)",
        setup_atk_serve_linux.run,
        after=(_CLAUDE_PLUGIN,),
        platforms=_LINUX,
        host_resources=True,
    ),
    # 両ステップが`systemctl --user daemon-reload`と`restart`を実行する。
    _StepSpec(
        "dotfiles自動更新タイマー セットアップ (Linux)",
        setup_dotfiles_autoupdate_linux.run,
        after=("atk serve 自動起動セットアップ (Linux)",),
        platforms=_LINUX,
        host_resources=True,
    ),
    _StepSpec("Windowsレジストリ設定", setup_registry.run, platforms=_WINDOWS),
    _StepSpec("SendTo ショートカット (Windows)", setup_sendto_shortcuts.run, platforms=_WINDOWS),
    _StepSpec("メディアリモコン自動起動 (Windows/stheno)", setup_media_remote.run, platforms=_WINDOWS),
    # 他ステップが PATH 追加を行うため、それらの後に整理を実行する。
    _StepSpec("ユーザー PATH 整理 (Windows)", cleanup_user_path.run, after_all_preceding=True, platforms=_WINDOWS),
]


def main(
    argv: Sequence[str] | None = None,
    *,
    runner: Callable[[], tuple[list[_StepResult], list[str]]] | None = None,
) -> None:
    """エントリポイント。"""
    parser = argparse.ArgumentParser(description="chezmoi apply後のdotfiles設定を更新する。")
    parser.add_argument(
        "--allow-non-canonical-root",
        action="store_true",
        help="linked worktreeからの実行を明示的に許可する",
    )
    args = parser.parse_args([] if runner is not None and argv is None else argv)
    if runner is None:
        root = claude_common.find_dotfiles_root()
        if root is None:
            print("dotfilesの実行ルートを特定できませんでした。", file=sys.stderr)
            sys.exit(2)
        git_result = claude_common.run_subprocess(
            ["git", "-C", str(root), "rev-parse", "--path-format=absolute", "--git-common-dir"]
        )
        if git_result is None or git_result.returncode != 0:
            detail = claude_common.format_cli_error(git_result)
            print(f"正規のdotfilesルートを特定できませんでした: {root}: {detail}", file=sys.stderr)
            sys.exit(2)
        canonical_root = Path(git_result.stdout.strip()).parent
        if root != canonical_root and not args.allow_non_canonical_root:
            print(
                f"複製作業ツリーからの実行を停止しました: 検出したルート: {root}; "
                f"正規ルート: {canonical_root}。実行する場合は--allow-non-canonical-rootを指定してください。",
                file=sys.stderr,
            )
            sys.exit(2)
    # update-dotfiles 配下の出力であることを示すため、全ログ行を 2 スペース下げる。
    previous_handlers, previous_level, persistent_log_ready = _configure_logging()
    try:
        logger.info("post-apply開始")
        results, recommendations = (runner or run)()
        failed = [r for r in results if not r.ok]
        updated = [r for r in results if r.ok and r.changed]
        skipped = [r for r in results if r.ok and not r.changed]
        notices = _pytools_install_notices() + [notice for result in results for notice in result.notices]
        _record_sync_report(updated=updated, skipped=skipped, failed=failed, notices=notices)
        # logger.info("") だと format により末尾空白が付与されるため、stdout に直接出力する。
        print(flush=True)
        logger.info("完了: 更新 %d 件 / スキップ %d 件 / 失敗 %d 件", len(updated), len(skipped), len(failed))
        _print_plugin_recommendations(recommendations)
        if failed:
            logger.error("失敗したステップ: %s", ", ".join(r.name for r in failed))
            if persistent_log_ready:
                logger.error("永続ログ: %s", _UPDATE_LOG_PATH)
        _print_post_apply_notices(notices)
        logger.info("post-apply終了: exit=%d", 1 if failed else 0)
        sys.exit(1 if failed else 0)
    finally:
        root_logger = logging.getLogger()
        current_handlers = root_logger.handlers.copy()
        root_logger.handlers[:] = previous_handlers
        root_logger.setLevel(previous_level)
        for handler in current_handlers:
            handler.close()


def _record_sync_report(
    *,
    updated: list[_StepResult],
    skipped: list[_StepResult],
    failed: list[_StepResult],
    notices: list[post_apply_outcome.PostApplyNotice],
) -> None:
    """post-apply段の結果を同期結果の記録へ残す。

    次に起動するコーディングエージェントが、失敗したステップと例外の内容から
    AWIの処理を完遂できるかを判定するための入力とする。
    """
    sync_report.write_post_apply(
        _run_id(),
        {
            "updated": len(updated),
            "skipped": len(skipped),
            "failed": len(failed),
            "failed_steps": [{"name": result.name, "reason": result.reason, "detail": result.detail} for result in failed],
            "notices": list(dict.fromkeys(notice.message for notice in notices)),
        },
    )


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


def _print_post_apply_notices(notices: list[post_apply_outcome.PostApplyNotice]) -> None:
    """post-apply完了時の案内をstderrへ表示する。"""
    unique_notices = tuple(dict.fromkeys(notices))
    if not unique_notices:
        return
    print(file=sys.stderr, flush=True)
    for notice in unique_notices:
        logger.warning(notice.message)
        if notice.command is not None:
            print(notice.command, file=sys.stderr, flush=True)


def _pytools_install_notices() -> list[post_apply_outcome.PostApplyNotice]:
    """テンプレートから渡されたpytools再導入状態を最終案内へ変換する。"""
    state = os.environ.get("DOTFILES_PYTOOLS_INSTALL_STATE", "")
    if not state:
        return []
    messages = {
        "deferred": "pytoolsの再インストールを延期しました。",
        "failed": "pytoolsの再インストールに失敗しました。",
    }
    try:
        message = messages[state]
    except KeyError as error:
        raise ValueError(f"未知のpytools再導入状態です: {state}") from error
    detail = os.environ.get("DOTFILES_PYTOOLS_INSTALL_DETAIL", "")
    if detail:
        message = f"{message} 詳細: {detail}"
    return [post_apply_outcome.PostApplyNotice(message)]


_step_log_state = threading.local()


class _StepLogFilter(logging.Filter):
    """実行中のステップが出力したレコードを通常handlerから除外する。"""

    def filter(self, record: logging.LogRecord) -> bool:
        del record
        return not getattr(_step_log_state, "active", False)


class _StepLogCapture(logging.Handler):
    """実行中のステップが出力したレコードをステップごとに投入順で保持する。"""

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(_step_log_state, "active", False):
            _step_log_state.records.append(record)


@dataclass
class _StepOutcome:
    result: _StepResult
    recommendations: list[str]
    duration: float
    records: list[logging.LogRecord]
    # 実行しなかったステップの理由。`None`は実行したことを表す。
    skip_reason: str | None = None


def _execute_step(step: _StepSpec) -> tuple[_StepResult, list[str], float]:
    """1ステップを実行し、例外、戻り値、所要時間を共通形式へ変換する。"""
    started_at = time.monotonic()
    try:
        ret = step.run()
    except Exception as error:  # noqa: BLE001 -- 他ステップを止めないため広く捕捉する
        logger.exception("    %s: 失敗", step.name)
        failure = _StepResult(
            name=step.name,
            ok=False,
            changed=False,
            reason=f"{type(error).__name__}: {error}",
            detail=sync_report.truncate_tail(traceback.format_exc()),
        )
        return failure, [], time.monotonic() - started_at
    notices: tuple[post_apply_outcome.PostApplyNotice, ...] = ()
    recommendations: list[str] = []
    if isinstance(ret, post_apply_outcome.PostApplyOutcome):
        changed = ret.changed
        notices = ret.notices
    elif isinstance(ret, tuple):
        changed, recommendations = ret
    else:
        changed = ret
    return (
        _StepResult(name=step.name, ok=True, changed=changed, notices=notices),
        recommendations,
        time.monotonic() - started_at,
    )


def _execute_captured_step(label: str, step: _StepSpec) -> _StepOutcome:
    """ワーカースレッドで1ステップを実行し、そのステップのログレコードを捕捉する。"""
    records: list[logging.LogRecord] = []
    _step_log_state.active = True
    _step_log_state.records = records
    try:
        # 開始行は開始時刻の記録として永続ログにだけ残す。
        logger.info("%s", label, extra=log_format.LOG_ONLY)
        result, recommendations, duration = _execute_step(step)
        return _StepOutcome(result, recommendations, duration, records)
    finally:
        _step_log_state.active = False


def _normalize_step(step: _StepSpec | tuple[str, Callable[[], StepReturn]]) -> _StepSpec:
    if isinstance(step, _StepSpec):
        return step
    name, step_runner = step
    return _StepSpec(name, step_runner)


def _resolve_predecessors(steps: Sequence[_StepSpec]) -> list[frozenset[int]]:
    """各ステップの先行工程を添字の集合へ解決し、未知の名前と循環を拒否する。"""
    index_by_name = {step.name: index for index, step in enumerate(steps)}
    predecessors: list[frozenset[int]] = []
    for index, step in enumerate(steps):
        resolved = set(range(index)) if step.after_all_preceding else set()
        for name in step.after:
            if name not in index_by_name:
                raise ValueError(f"先行工程が見つかりません: {step.name} -> {name}")
            resolved.add(index_by_name[name])
        predecessors.append(frozenset(resolved))
    # 全ステップを処理できる順序が存在しなければ循環がある。
    done: set[int] = set()
    while len(done) < len(steps):
        ready = {index for index in range(len(steps)) if index not in done and predecessors[index] <= done}
        if not ready:
            names = ", ".join(steps[index].name for index in range(len(steps)) if index not in done)
            raise ValueError(f"先行工程が循環しています: {names}")
        done |= ready
    return predecessors


_SKIP_OTHER_PLATFORM = "実行中のOSは対象外のため実行しない"
_SKIP_SUBSTITUTED_HOME = "HOMEが実行ユーザーのホームと異なるため、実機の共有資源を操作せず実行しない"


def _home_is_substituted() -> bool:
    """HOMEが実行ユーザーのパスワードデータベース上のホームと異なるかを返す。

    `pwd`を持たないWindowsでは判定せず偽を返す。`host_resources`を持つステップはいずれもLinux専用である。
    """
    if sys.platform == "win32":
        return False
    import pwd  # noqa: PLC0415  # pylint: disable=import-outside-toplevel  # Windowsに存在しないモジュールのため

    return Path.home().resolve() != Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()


def _skip_reason(step: _StepSpec, *, home_substituted: bool) -> str | None:
    """ステップを実行しない理由を返す。実行する場合は`None`を返す。"""
    if step.platforms and sys.platform not in step.platforms:
        return _SKIP_OTHER_PLATFORM
    if step.host_resources and home_substituted:
        return _SKIP_SUBSTITUTED_HOME
    return None


def _emit_outcome(label: str, outcome: _StepOutcome) -> None:
    """1ステップの出力を、所要時間付きの見出しを先頭にまとめて各handlerへ送る。"""
    root_logger = logging.getLogger()
    if outcome.skip_reason == _SKIP_OTHER_PLATFORM:
        logger.info("%s: %s", label, outcome.skip_reason, extra=log_format.LOG_ONLY)
        return
    if outcome.skip_reason is not None:
        # HOMEの差し替えによる省略は、手動観測の実行者が画面で確認できるよう表示する。
        logger.info("%s: %s", label, outcome.skip_reason)
        return
    start_record, *rest = outcome.records
    root_logger.handle(start_record)
    logger.info("%s (%.1f秒)", label, outcome.duration)
    for record in rest:
        root_logger.handle(record)


def run(
    steps: Sequence[_StepSpec | tuple[str, Callable[[], StepReturn]]] | None = None,
) -> tuple[list[_StepResult], list[str]]:
    """各ステップを先行工程の順序を守って並列に実行し、`(results, recommendations)` を返す。

    先行工程が失敗しても後続ステップは実行する。出力と`results`は列挙順に並べ、
    各ステップの出力はそのステップと列挙順でそれより前の全ステップが完了した時点で出力する。
    `recommendations` は ``install_claude_plugins.run()`` が算出した推奨コマンド列。
    ``install_claude_plugins.run`` は ``tuple[bool, list[str]]`` を返すため、
    タプルの戻り値を持つステップは推奨コマンドとして収集する。
    """
    selected_steps = _DEFAULT_STEPS if steps is None else steps
    effective_steps = [_normalize_step(step) for step in selected_steps]
    predecessors = _resolve_predecessors(effective_steps)
    total = len(effective_steps)
    labels = [f"[{index}/{total}] {step.name}" for index, step in enumerate(effective_steps, start=1)]
    home_substituted = _home_is_substituted()
    root_logger = logging.getLogger()
    exclusion = _StepLogFilter()
    capture = _StepLogCapture()
    original_handlers = root_logger.handlers.copy()
    for handler in original_handlers:
        handler.addFilter(exclusion)
    root_logger.addHandler(capture)
    outcomes: dict[int, _StepOutcome] = {}
    started: set[int] = set()
    running: dict[Future[_StepOutcome], int] = {}
    emitted = 0
    try:
        with ThreadPoolExecutor(max_workers=max(total, 1)) as executor:
            while True:
                progressed = True
                while progressed:
                    progressed = False
                    for index, step in enumerate(effective_steps):
                        if index in started or not predecessors[index].issubset(outcomes):
                            continue
                        started.add(index)
                        reason = _skip_reason(step, home_substituted=home_substituted)
                        if reason is None:
                            running[executor.submit(_execute_captured_step, labels[index], step)] = index
                            continue
                        # 実行しないステップは`run`を呼ばず、成功かつ変更なしとして扱う。
                        result = _StepResult(name=step.name, ok=True, changed=False)
                        outcomes[index] = _StepOutcome(result, [], 0.0, [], skip_reason=reason)
                        progressed = True
                while emitted < total and emitted in outcomes:
                    _emit_outcome(labels[emitted], outcomes[emitted])
                    emitted += 1
                if not running:
                    break
                done, _ = wait(running, return_when=FIRST_COMPLETED)
                for future in done:
                    outcomes[running.pop(future)] = future.result()
    finally:
        root_logger.removeHandler(capture)
        for handler in original_handlers:
            handler.removeFilter(exclusion)
    ordered = [outcomes[index] for index in range(total)]
    return [outcome.result for outcome in ordered], [item for outcome in ordered for item in outcome.recommendations]


if __name__ == "__main__":
    main()
