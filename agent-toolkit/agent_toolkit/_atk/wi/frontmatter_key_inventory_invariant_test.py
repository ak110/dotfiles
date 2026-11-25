import ast
import pathlib

import pytest

from agent_toolkit._atk.wi import add, branches, priority

_FIELD_USES = {
    "alert_keys": ("alerts.existing_alert_keys", "同じ警告を重ねて起票しないために記録する"),
    "choices": ("uwiの回答形式処理", "問いで選べる答えを依頼ごとに指定する"),
    "cooldown_until": ("readiness", "その項目の着手を待つ期限を指定する"),
    "depends_on": ("readiness", "処理を前提項目の終了まで待たせる"),
    "effect_check": ("effect_checkとlisting", "必要な項目の効果確認の結果を追跡する"),
    "important": ("filtersの重要度選択", "優先的に閲覧したい項目を区別する"),
    "legacy_operation_approval": ("operation_permissionsの旧承認処理", "旧形式の承認の消費を記録する。手入力しない"),
    "merge_branch": ("merge_verification", "取り込むブランチを識別する"),
    "merge_ci_workflow": ("merge_verification", "マージ前に成功を求めるCIを識別する"),
    "merge_commit": ("merge_verification", "取り込むコミットを固定する"),
    "merge_local_verification": ("merge_verification", "マージ前に行ったローカル検証を記録する"),
    "operation_approval": ("operation_permissions", "対象操作への回答と承認状態を結び付ける。手入力しない"),
    "operation_approval_last_use": ("atk wi showで消費記録を読む", "承認を消費した実行を追跡する。手入力しない"),
    "permission_request": ("operation_permissions", "承認対象の操作を限定する。手入力しない"),
    "priority": ("priorityとlisting", "通常順を変える緊急項目を示す"),
    "priority_evidence": ("priority.reason_of", "旧形式の緊急化の観測根拠を読む。手入力しない"),
    "priority_impact": ("priority.reason_of", "旧形式の緊急化の影響を読む。手入力しない"),
    "priority_release": ("priority.reason_of", "旧形式の緊急化の解除条件を読む。手入力しない"),
    "proposal_id": ("addの重複検査", "同じ提案の再投入を防ぐ識別子を指定する"),
    "question_type": ("uwiの回答形式処理", "問いの回答方式を決める"),
    "queue_schedule": ("readinessの旧スケジュール読取", "旧形式の再開期限を維持する。手入力しない"),
    "repair_kind": ("readinessの修復対象選定", "処理を再開するための是正種別を識別する"),
    "repair_target": ("readinessの修復対象選定", "修復UWIが対応する元項目を識別する"),
    "run_skill_report": ("submit", "報告用UWIと判断を求めるUWIを区別する"),
    "run_skill_run_id": ("add_reportsとsubmit", "報告を起動した実行に関連付ける。手入力しない"),
    "scope": ("add_reportsとsubmit", "問いと結果を対象作業へ対応付ける"),
    "source": ("filtersとlisting", "項目の由来を区別する"),
    "state": ("layoutとreadiness", "キューの現在状態を状態遷移で更新する"),
    "submitter_session": ("uwi_scanの回答通知", "回答を届ける起票元を識別する。手入力しない"),
    "target_branch": ("branchesの対象の選択", "リポジトリの初期値とは別の処理系統を指定する"),
    "target_commit": ("targetsの対象の選択", "起票時の実装版を保存する。手入力しない"),
    "target_repo": ("listingの対象の選択", "処理対象のリポジトリを指定する"),
    "type": ("entriesとuwi_scan", "依頼を実装候補とユーザーへの問いに分ける"),
    "weekly_slot": ("add_reportsの週次結果検索", "同じ週次枠の結果に対応付ける。手入力しない"),
}

_NON_ACTIVE_FIELDS = {
    "origin_session",
    "origin_locator",
    "plan_file",
    "reservation",
    "reservation_companion",
    "target_commit_history",
    "target_filename",
    "token_hash",
    "wi_submit_bindings",
}
_METADATA_NAMES = {"frontmatter_data", "data", "metadata", "updated_data", "updates", "stored_data", "frontmatter"}
_RESERVED_FIELD_TABLES = {"_RESERVED_ADD_KEY_NEXT_ACTIONS", "_RESERVED_EDIT_KEY_NEXT_ACTIONS"}


def _metadata_name(node: ast.expr) -> bool:
    return isinstance(node, ast.Name) and node.id in _METADATA_NAMES


def _literal_metadata_keys(path: pathlib.Path) -> set[str]:
    keys: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        literals: list[ast.expr | None] = []
        if isinstance(node, ast.Subscript) and _metadata_name(node.value):
            literals.append(node.slice)
        elif isinstance(node, ast.Assign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if isinstance(node.value, ast.Dict) and any(
                isinstance(target, ast.Name) and target.id in _METADATA_NAMES | _RESERVED_FIELD_TABLES for target in targets
            ):
                literals.extend(node.value.keys)
        elif isinstance(node, ast.Call) and isinstance(method := node.func, ast.Attribute) and _metadata_name(method.value):
            if method.attr in {"get", "pop", "setdefault"} and node.args:
                literals.append(node.args[0])
            elif method.attr == "update":
                keys.update(item.arg for item in node.keywords if item.arg is not None)
                if node.args and isinstance(node.args[0], ast.Dict):
                    literals.extend(node.args[0].keys)
        keys.update(key.value for key in literals if isinstance(key, ast.Constant) and isinstance(key.value, str))
    return keys


# test-copy-contract: 実装から抽出したWIキーに消費箇所と変更理由を求める。  # pyfltr: allow-comment
@pytest.mark.cpu_limit(
    0.53, reason="全WI実装のASTを調べる。判定CPU秒はLinux単独0.180〜0.198、4並列0.194〜0.217、CI失敗時0.73（上限の係数1.4）"
)
def test_each_wi_frontmatter_key_has_a_consumer_and_a_reason_to_change_it() -> None:
    root = pathlib.Path(__file__).resolve().parent
    keys = {
        branches.KEY,
        priority.PRIORITY_KEY,
        "alert_keys",
        add.PROPOSAL_ID_KEY,
    }
    for path in root.rglob("*.py"):
        if not path.name.endswith("_test.py"):
            keys.update(_literal_metadata_keys(path))
    missing = {key for key in keys - _NON_ACTIVE_FIELDS if not all(part.strip() for part in _FIELD_USES.get(key, ("", "")))}
    assert not missing, f"消費箇所と変更理由を確認できないWI frontmatterキー: {sorted(missing)}"
