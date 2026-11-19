"""chezmoiとpost-applyの配布物から外した旧パスの一覧と、配布先からの削除。

post-applyの「旧配布物の削除」のステップが`cleanup`を実行する。
配布元のファイルを削除・改名した時と、post-applyが配置するファイルの配置先を変えた時は、同じ変更で旧パスを本書の表へ登録する。
"""

import datetime
import logging
from pathlib import Path

from pytools._internal import cleanup_paths, log_format, post_apply_outcome

logger = logging.getLogger(__name__)

# chezmoi は配布元から削除されたファイルを配布先から自動削除しないため、本テーブルで追跡する。
# 基点は配布先（`~/.claude`などpost-applyやchezmoiが書き込むディレクトリ）だけとし、作業ツリー内を指定しない。
# 各項目の登録日と期限の規則は`removal_registry`が定め、期限を過ぎた項目は不変条件のテストが検出する。
_REMOVED_PATHS: dict[Path, list[cleanup_paths.RemovedPath]] = {
    Path.home() / ".claude": [
        # `references/`はスキル配下だけの名前としたため、旧配布先のディレクトリを削除する。
        cleanup_paths.RemovedPath(Path("references"), datetime.date(2026, 9, 18)),
        # 2つのスキルはagent-toolkit pluginへ移設したため、dotfiles側の旧配布先を削除する。
        cleanup_paths.RemovedPath(Path("skills/refine-prompt"), datetime.date(2026, 9, 29)),
        cleanup_paths.RemovedPath(Path("skills/export-session"), datetime.date(2026, 9, 29)),
        # 振り返りはagent-toolkit側のreview-sessionへ統合したため旧配布先を削除する。
        cleanup_paths.RemovedPath(Path("skills/session-review-dotfiles"), datetime.date(2026, 8, 7)),
        # 現在のスキル名は add-awi。旧名 feedback-add の配布先ディレクトリを削除する。
        cleanup_paths.RemovedPath(Path("skills/feedback-add"), datetime.date(2026, 6, 16)),
        # 現在のスキル名は process-wi。旧名 process-feedback の配布先ディレクトリを削除する。
        cleanup_paths.RemovedPath(Path("skills/process-feedback"), datetime.date(2026, 6, 16)),
        # 現在は atk wi process-loop CLI が常駐ループを担うため、
        # 旧 process-feedbacks-loop スキルの配布先ディレクトリを削除する。
        cleanup_paths.RemovedPath(Path("skills/process-feedbacks-loop"), datetime.date(2026, 7, 5)),
        # add-awi・process-wi スキルは agent-toolkit/skills/ 配下へ移設済み。
        # 旧配布先 (dotfiles-fb 系スキル) の配布先ディレクトリを削除する。
        cleanup_paths.RemovedPath(Path("skills/add-feedback"), datetime.date(2026, 7, 8)),
        cleanup_paths.RemovedPath(Path("skills/process-feedbacks"), datetime.date(2026, 7, 8)),
        # 現在のスキル名は operate-ak110-projects。旧名の配布先を削除する。
        cleanup_paths.RemovedPath(Path("skills/sync-cross-project"), datetime.date(2026, 9, 13)),
        cleanup_paths.RemovedPath(Path("skills/ak110-projects-operations"), datetime.date(2026, 11, 1)),
        # 追従台帳の索引はproject-synchronization.md「台帳への追記」へ統合したため、旧配布先のファイルを削除する。
        cleanup_paths.RemovedPath(
            Path("skills/operate-ak110-projects/references/followup-ledger-settings.md"), datetime.date(2026, 11, 19)
        ),
        # 02-claude-code.md / 03-styles.md / 04-terminology.md →
        # 03-claude-code.md / 04-styles.md / 05-terminology.md リネームに伴い旧ファイルを削除する。
        cleanup_paths.RemovedPath(Path("rules/agent-toolkit/02-claude-code.md"), datetime.date(2026, 7, 14)),
        cleanup_paths.RemovedPath(Path("rules/agent-toolkit/03-styles.md"), datetime.date(2026, 7, 14)),
        cleanup_paths.RemovedPath(Path("rules/agent-toolkit/04-terminology.md"), datetime.date(2026, 7, 14)),
        # autopilotスキルは協調・自律の規範へ吸収し廃止。配布先から旧スキルディレクトリを削除する。
        cleanup_paths.RemovedPath(Path("skills/autopilot"), datetime.date(2026, 7, 14)),
        # ルール層を01-agent.md / 02-claude-code.mdの2ファイルへ統合したため、
        # 統合元の旧ファイルを配布先から削除する。
        # chezmoiは配布元の削除を配布先へ伝播しないため、本一覧への登録が必要となる。
        cleanup_paths.RemovedPath(Path("rules/agent-toolkit/02-collaboration.md"), datetime.date(2026, 7, 26)),
        cleanup_paths.RemovedPath(Path("rules/agent-toolkit/03-claude-code.md"), datetime.date(2026, 7, 26)),
        cleanup_paths.RemovedPath(Path("rules/agent-toolkit/04-styles.md"), datetime.date(2026, 7, 26)),
        cleanup_paths.RemovedPath(Path("rules/agent-toolkit/05-terminology.md"), datetime.date(2026, 7, 26)),
        cleanup_paths.RemovedPath(Path("rules/agent-toolkit/06-monitoring.md"), datetime.date(2026, 7, 26)),
        # 常時規範の配布を廃止し、入口のスキル`atk-mode`と手順書へ移したため、post-applyの同期が
        # 所有していた配布先を丸ごと削除する。残すと廃止したスキルの起動を求める旧い規範が読まれ続ける。
        cleanup_paths.RemovedPath(Path("rules/agent-toolkit"), datetime.date(2026, 11, 20)),
    ],
    Path.home() / ".codex": [
        # agent定義（feedbacks-planner・plan-executor・plan-review-executor）を廃止し、
        # agent-toolkit/agents ディレクトリごと除去したため、旧配布先リンクを除去する。
        cleanup_paths.RemovedPath(Path("agent-toolkit/agents"), datetime.date(2026, 9, 3)),
        # plan-impl・plan-codex-reviewはagentsへ移植し、fork型スキルとしては廃止した。
        # 旧配布先リンクを除去する。
        cleanup_paths.RemovedPath(Path("skills/plan-impl"), datetime.date(2026, 7, 11)),
        cleanup_paths.RemovedPath(Path("skills/plan-codex-review"), datetime.date(2026, 7, 11)),
        # 2つのスキルはagent-toolkit pluginへ移設したため、旧リンクを削除する。
        cleanup_paths.RemovedPath(Path("skills/refine-prompt"), datetime.date(2026, 9, 29)),
        cleanup_paths.RemovedPath(Path("skills/export-session"), datetime.date(2026, 9, 29)),
        # 振り返りはagent-toolkit側のreview-sessionへ統合したため旧配布先リンクを除去する。
        cleanup_paths.RemovedPath(Path("skills/session-review-dotfiles"), datetime.date(2026, 8, 7)),
        # 現在のスキル名は add-awi。旧名 feedback-add の配布先リンクを除去する。
        cleanup_paths.RemovedPath(Path("skills/feedback-add"), datetime.date(2026, 6, 16)),
        # 現在のスキル名は process-wi。旧名 process-feedback の配布先リンクを除去する。
        cleanup_paths.RemovedPath(Path("skills/process-feedback"), datetime.date(2026, 6, 16)),
        # 現在のスキル名は operate-ak110-projects。旧名の配布先リンクを削除する。
        cleanup_paths.RemovedPath(Path("skills/sync-cross-project"), datetime.date(2026, 9, 13)),
        cleanup_paths.RemovedPath(Path("skills/ak110-projects-operations"), datetime.date(2026, 11, 1)),
        # 常時規範の配布の廃止に伴い、post-applyの同期が所有していたCodex向けの配布先を削除する。
        # 親は他に配布物が無いため、空になった場合だけ削除する。
        cleanup_paths.RemovedPath(Path("agent-toolkit/rules"), datetime.date(2026, 11, 20)),
        cleanup_paths.RemovedPath(Path("agent-toolkit"), datetime.date(2026, 11, 20), empty_dir_only=True),
    ],
    Path.home() / ".config": [
        # pyfltr v3.14.1で口語表現チェッカーが内蔵化されたため
        # dotfiles配布のカスタムコマンド定義（旧`config.toml`）を配布先から除去する。
        cleanup_paths.RemovedPath(Path("pyfltr/config.toml"), datetime.date(2026, 7, 1)),
        # 計画ファイル閲覧は atk serve へ統合した。設定は atk config の serve_* キーへ移した。
        cleanup_paths.RemovedPath(Path("pytools/claude-plans-viewer.toml"), datetime.date(2026, 9, 2)),
        # 廃止した工程が配置したフラグファイル。いずれも廃止時に削除登録がなく残存していた。
        # feedback-inbox.enabled は setup_feedback_inbox（d08f8d5b で廃止）、
        # review-balance-mode.claude-heavy は setup_review_balance_mode（4c53faaa で廃止）が配置した。
        cleanup_paths.RemovedPath(Path("agent-toolkit/feedback-inbox.enabled"), datetime.date(2026, 9, 26)),
        cleanup_paths.RemovedPath(Path("agent-toolkit/review-balance-mode.claude-heavy"), datetime.date(2026, 9, 26)),
    ],
    Path.home() / ".ipython": [
        cleanup_paths.RemovedPath(Path("profile_default/startup/README"), datetime.date(2026, 8, 14)),
        # 配布済みREADMEの親だけを深い順で除去する。dotfilesユーザーのファイルが残るディレクトリは保持する。
        cleanup_paths.RemovedPath(Path("profile_default/startup"), datetime.date(2026, 8, 14), empty_dir_only=True),
        cleanup_paths.RemovedPath(Path("profile_default"), datetime.date(2026, 8, 14), empty_dir_only=True),
    ],
    Path.home() / ".local" / "bin": [
        # 計画ファイル閲覧は atk serve へ統合したため、旧 CLI の配布先を除去する。
        cleanup_paths.RemovedPath(Path("claude-plans-viewer"), datetime.date(2026, 9, 2)),
        cleanup_paths.RemovedPath(Path("claude-plans-viewer.exe"), datetime.date(2026, 9, 2)),
        # 15c2e214 が atk serve 常駐用に生成したランチャー ~/.local/bin/atk は、1116f984 で
        # ~/.local/bin/atk-serve へ改名した際に旧名の削除が漏れて残存していた。
        # dotfiles ホストでは ~/dotfiles/agent-toolkit/scripts（Linux は .chezmoi-source/dot_bashrc、
        # Windows は pytools/_internal/setup_bin_path.py）が PATH へ登録されるため、PATH の先頭側にある
        # ~/.local/bin 配下の atk は作業ツリー版を覆い隠す。atk.cmd は install-claude.ps1 が同じ位置へ
        # 生成する Windows 版ラッパーであり、同じ理由で除去する。atk-serve と atk-hook は対象外とする。
        cleanup_paths.RemovedPath(Path("atk"), datetime.date(2026, 9, 26)),
        cleanup_paths.RemovedPath(Path("atk.cmd"), datetime.date(2026, 9, 26)),
    ],
}

# ユーザーの独自編集を保護するため、内容が期待値と完全一致するときのみ削除する。
# 期待値は撤去前の内容のbytesか、大きな内容ではそのSHA-256の16進文字列で書く。
_REMOVED_PATHS_IF_CONTENT: dict[Path, list[cleanup_paths.RemovedPathIfContent]] = {
    Path.home(): [
        # 使われていないscreenの設定の配布をやめた。
        cleanup_paths.RemovedPathIfContent(
            Path(".screenrc"), datetime.date(2026, 10, 7), b"\ndefscrollback 1000000\n\nshell -${SHELL}\n"
        ),
    ],
    # 使われていないxonsh・yapf・Poetry・REST Clientの設定の配布をやめた。
    # rc.xshの値は`git show d074bebbe:.chezmoi-source/dot_config/xonsh/rc.xsh`のSHA-256。
    Path.home() / ".config": [
        cleanup_paths.RemovedPathIfContent(
            Path("xonsh/rc.xsh"),
            datetime.date(2026, 10, 7),
            "d60e88ba9557971564d0b58f2f2483b2306182bd21069c377f5d1156b82e9278",
        ),
        cleanup_paths.RemovedPathIfContent(
            Path("yapf/style"), datetime.date(2026, 10, 7), b"[style]\nbased_on_style = google\ncolumn_limit = 256\n"
        ),
        cleanup_paths.RemovedPathIfContent(
            Path("pypoetry/config.toml"),
            datetime.date(2026, 10, 7),
            b"[virtualenvs]\nin-project = false\n\n[installer]\n",
        ),
        cleanup_paths.RemovedPathIfContent(Path("rest-client/environment.json"), datetime.date(2026, 10, 7), b""),
    ],
    Path.home() / ".ipython": [
        # IPythonが生成した全行コメントの初期設定であり、設定として働かないため配布をやめた。
        # 値は`git show 89ce8990a:.chezmoi-source/dot_ipython/profile_ipy/ipython_kernel_config.py`のSHA-256。
        cleanup_paths.RemovedPathIfContent(
            Path("profile_ipy/ipython_kernel_config.py"),
            datetime.date(2026, 10, 7),
            "0b6c81745268a31c9f862651f671f179ae5966d39c12e80b9c1f6034ce783760",
        ),
    ],
}


def cleanup() -> post_apply_outcome.PostApplyOutcome:
    """`_REMOVED_PATHS` / `_REMOVED_PATHS_IF_CONTENT` に従って旧配布物を削除する。検査と削除の失敗は失敗と数える。"""
    total_removed = 0
    failures: list[str] = []
    for base_dir, entries in _REMOVED_PATHS.items():
        total_removed += cleanup_paths.cleanup_paths(
            base_dir, [e.path for e in entries if not e.empty_dir_only], failures=failures
        )
        # 空ディレクトリの項目は、同じ基点の他の項目を削除した後に列挙順で判定する。
        total_removed += cleanup_paths.cleanup_empty_dirs(
            base_dir, [e.path for e in entries if e.empty_dir_only], failures=failures
        )
    for base_dir, content_entries in _REMOVED_PATHS_IF_CONTENT.items():
        total_removed += cleanup_paths.cleanup_paths_if_content_matches(
            base_dir, {entry.path: entry.expected for entry in content_entries}, failures=failures
        )
    if total_removed == 0:
        logger.info(log_format.format_status("cleanup", "削除対象なし"))
    else:
        logger.info(log_format.format_status("cleanup", f"{total_removed} 件を削除した"))
    return post_apply_outcome.PostApplyOutcome(
        changed=total_removed > 0,
        failure=" / ".join(failures) or None,
        next_action="失敗した配布先の権限と使用中のプロセスを確認し、update-dotfilesを再実行する" if failures else None,
    )
