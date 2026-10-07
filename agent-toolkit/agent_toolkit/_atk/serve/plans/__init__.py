"""`atk serve`の計画ファイル画面を責務別のサブモジュールに持つパッケージ。

サブモジュールはrootの解決、Markdownの描画、作成日時インデックス、ローカルとSSH先の走査、HTTPの応答を分担する。
パッケージ外の呼び出し元が使う名前だけを定義元から再exportする。
"""

from agent_toolkit._atk.serve.plans.local_scan import (
    REMOTE_BOOTSTRAP,
    read_pygments_css,
)
from agent_toolkit._atk.serve.plans.roots import (
    LEGACY_PORTABLE_ROOT,
    LEGACY_SOURCE_ID,
    NEW_PORTABLE_ROOT,
    NEW_SOURCE_ID,
    BroadcastState,
    RootSpec,
    schedule_broadcast,
    subscribe,
    unsubscribe,
)
from agent_toolkit._atk.serve.plans.views import (
    PlanFileError,
    PlansContext,
    all_entries,
    create_context,
    is_review_table_path,
    render_file_html,
    resolve_source_id,
    resolve_text,
    search_entries,
    start_local_watchers,
    start_remote_watchers,
    stop_local_watchers,
    stop_remote_watchers,
)

__all__ = [
    "BroadcastState",
    "LEGACY_PORTABLE_ROOT",
    "LEGACY_SOURCE_ID",
    "NEW_PORTABLE_ROOT",
    "NEW_SOURCE_ID",
    "PlanFileError",
    "PlansContext",
    "REMOTE_BOOTSTRAP",
    "RootSpec",
    "all_entries",
    "create_context",
    "is_review_table_path",
    "read_pygments_css",
    "render_file_html",
    "resolve_source_id",
    "resolve_text",
    "schedule_broadcast",
    "search_entries",
    "start_local_watchers",
    "start_remote_watchers",
    "stop_local_watchers",
    "stop_remote_watchers",
    "subscribe",
    "unsubscribe",
]
