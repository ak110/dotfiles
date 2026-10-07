"""`atk serve`の計画ファイル画面を責務別のサブモジュールに持つパッケージ。

サブモジュールは設定値と更新通知、Markdownの描画、ローカルとSSH先の走査、HTTPの応答を分担する。
計画rootの定義と対象判定、作成日時インデックスはSSH先のヘルパーと共有するため`_plan`配下に置く。
パッケージ外の呼び出し元が使う名前だけを定義元から再exportする。
"""

from agent_toolkit._atk.serve.plans.local_scan import (
    REMOTE_BOOTSTRAP,
    read_pygments_css,
)
from agent_toolkit._atk.serve.plans.roots import (
    BroadcastState,
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
from agent_toolkit._plan.viewer_files import (
    LEGACY_PORTABLE_ROOT,
    LEGACY_SOURCE_ID,
    NEW_PORTABLE_ROOT,
    NEW_SOURCE_ID,
    RootSpec,
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
