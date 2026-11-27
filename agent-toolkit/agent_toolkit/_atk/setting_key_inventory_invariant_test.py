from agent_toolkit._atk.config_settings import MUTABLE_KEY_DEFAULTS

_MODEL_CONSUMER = "config.resolve_model_candidatesとorchestratorの候補解決"
_CONNECTION_CONSUMER = "config_resolution の役割別接続解決"

_SETTING_USES = {
    "claude_credits_env": ("claude_connectionの共有接続環境", "有限クレジットの認証と接続先を編集する"),
    "claude_payg_env": ("claude_connectionの共有接続環境", "従量課金APIの認証と接続先を編集する"),
    "weekly_research_x_env": ("research_sources.x_token", "X APIの共有認証を編集する"),
    "execute_model": (_MODEL_CONSUMER, "実装で使うモデルの品質・料金を変える"),
    "review_model": (_MODEL_CONSUMER, "実行レビューの観点と品質を変える"),
    "review_fast_model": (_MODEL_CONSUMER, "短いレビューの応答時間を短くする"),
    "chores_model": (_MODEL_CONSUMER, "定型処理に使うモデルの費用を変える"),
    "chores_fast_model": (_MODEL_CONSUMER, "短い定型処理で応答時間を短くする"),
    "orchestrate_model": (_MODEL_CONSUMER, "統括の長い作業に合うモデルを選ぶ"),
    "write_model": (_MODEL_CONSUMER, "執筆に使うモデルを選ぶ"),
    "codex_fast_mode": ("codex_instructionsのservice_tier", "速い応答に追加料金を許す"),
    "usage_reset_permissions": ("config_usageとcodex_reset", "許可する使用量リセット操作を限定する"),
    "claude_orchestrator_connections": (_CONNECTION_CONSUMER, "Claude統括の予算に合う接続順を選ぶ"),
    "claude_worker_connections": (_CONNECTION_CONSUMER, "Claude作業担当の予算に合う接続順を選ぶ"),
    "codex_orchestrator_connections": (_CONNECTION_CONSUMER, "Codex統括の予算に合う接続順を選ぶ"),
    "codex_worker_connections": (_CONNECTION_CONSUMER, "Codex作業担当の予算に合う接続順を選ぶ"),
    "codex_credits_provider": ("codex_connectionのprovider解決", "クレジット枠を持つproviderを指定する"),
    "codex_payg_provider": ("codex_connectionのprovider解決", "従量課金を許したproviderを指定する"),
    "claude_usage_limit_wait": ("config_usageのClaude使用量上限待機", "Claudeの上限待ちの時間枠を選ぶ"),
    "codex_usage_limit_wait": ("config_usageのCodex使用量上限待機", "Codexの上限待ちの時間枠を選ぶ"),
    "repositories": ("config_settings.resolve_configured_repositories", "巡回と選定の対象を構成する"),
    "session_review_auto_start": ("run_skill_batchのセッションレビュー起動", "個別の振り返りを自動起動するか決める"),
    "weekly_meta_auto_start": ("weekly_scheduleのmeta起動判定", "週次のメタレビューに資源を使うか決める"),
    "weekly_architecture_auto_start": ("weekly_scheduleの起動判定", "週次の設計の点検に資源を使うか決める"),
    "weekly_ui_sweep_auto_start": ("weekly_scheduleの起動判定", "週次の画面の巡回に資源を使うか決める"),
    "weekly_research_auto_start": ("weekly_scheduleのresearch起動判定", "週次の外部調査に資源を使うか決める"),
    "weekly_research_x_api": ("research_sourcesのX取得判定", "料金の掛かるX APIの利用を明示する"),
    "agents_concurrency": ("_agents_server.admission.configured_limit", "ホストの利用可能な資源に同時稼働数を合わせる"),
    "dotfiles_mode": ("_common.dotfiles_modeとpost_apply", "端末の用途に応じて導入範囲を変える"),
    "serve_host": ("serve.configとserve.commandの待受設定", "アクセスを許す待受アドレスを決める"),
    "serve_port": ("serve.configとserve.commandの待受設定", "同じホストの起動済みサービスとポートを分ける"),
    "serve_plans_root": ("serve.configのローカル計画一覧", "設定ディレクトリ以外に保存した計画を表示する"),
    "remote_hosts": ("serveのリモート計画・セッション・設定", "この端末から扱うSSH接続先を指定する"),
    "serve_web_push": ("serve.appのWeb Push初期化", "WI画面を閉じていても通知を受け取る"),
    "serve_web_push_details": ("serve.appのWeb Push初期化", "通知にWIのタイトルと対象リポジトリを含める"),
    "wi_attachment_file_mib": ("wi.attachmentsのファイルサイズ検査", "1ファイルの上限を変える"),
    "wi_attachment_item_mib": ("wi.attachmentsのWI別サイズ検査", "1件のWIの添付の合計上限を変える"),
    "wi_attachment_total_mib": ("wi.attachmentsの累計サイズ検査", "履歴へ追加する添付の累計上限を変える"),
}


def _undocumented(keys: set[str]) -> set[str]:
    return {key for key in keys if not all(part.strip() for part in _SETTING_USES.get(key, ("", "")))}


def test_each_active_setting_has_a_consumer_and_a_reason_to_change_it() -> None:
    undocumented = _undocumented(set(MUTABLE_KEY_DEFAULTS))
    assert not undocumented, f"消費箇所と変更する理由を確認できない設定キー: {sorted(undocumented)}"
