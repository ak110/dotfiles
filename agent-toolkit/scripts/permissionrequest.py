"""Claude Code plugin agent-toolkit: PermissionRequestフック。

Claude Codeの確認ダイアログを無条件に許可する。

許可した要求は、設定`state_dir`が指すディレクトリ配下の`permissionrequest.log`へ1行1レコードのJSONで追記する。
レコードには要求元セッションの識別子に加えて、委譲の起点となった最上位セッションの識別子を`root_session_id`として残す。
値は`AGENT_TOOLKIT_OWNER_SESSION`と`CLAUDE_CODE_SESSION_ID`の順で環境から解決する。
委譲元は自身が解決した値を同名の変数で委譲先へ渡すため、多段の委譲でも最上位の識別子が保たれる。解決できない場合はnullとする。
ログが上限を超えた場合は`permissionrequest.log.1`へ退避し、退避は1世代だけ保持する。
記録に失敗した場合も許可の応答を返す。フックの障害で操作が止まる事態を避けるためである。
本フックはセキュリティ境界ではない。auto modeと利用者の権限設定が担う。

組み込みのdeny規則とask規則はフックの応答より先に評価されるため、
当該規則に一致する要求の確認は本フックでは抑止できない。
"""

import contextlib
import datetime
import json
import os

import _atk_config
import _plan_file

# 記録ファイル名と、退避先の1世代分のファイル名。
_LOG_NAME = "permissionrequest.log"
_ROTATED_LOG_NAME = "permissionrequest.log.1"

# 追記前に退避へ切り替えるファイルサイズの下限（バイト）。
_LOG_SIZE_LIMIT = 1048576

# 記録するpayload由来のキー。payloadに無いキーはnullとして残す。
_RECORD_KEYS = ("session_id", "cwd", "tool_name", "tool_input")


def main(payload_text: str) -> int:
    """エントリポイント。許可の応答を1件出力しexit code 0を返す。"""
    _record(payload_text)
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PermissionRequest",
                    "decision": {"behavior": "allow"},
                }
            },
            ensure_ascii=False,
        )
    )
    return 0


def _record(payload_text: str) -> None:
    """許可した要求を記録する。記録できない場合は何もしない。

    許可の応答は記録の成否に依存しないため、記録経路の失敗はすべて無視する。
    """
    with contextlib.suppress(Exception):
        _append_record(_build_record(payload_text))


def _build_record(payload_text: str) -> dict[str, object]:
    """payloadから記録用のレコードを組み立てる。

    解釈できないpayloadでは、payload由来のキーをすべてnullとする。
    """
    try:
        payload = json.loads(payload_text)
    except (json.JSONDecodeError, ValueError):
        payload = None
    if not isinstance(payload, dict):
        payload = {}
    record: dict[str, object] = {"time": datetime.datetime.now(datetime.UTC).isoformat()}
    record.update({key: payload.get(key) for key in _RECORD_KEYS})
    record["root_session_id"] = _plan_file.resolve_owner_session_id()
    return record


def _append_record(record: dict[str, object]) -> None:
    """レコードを1行のJSONとして記録先へ追記する。

    追記前に記録先が上限へ達している場合は、1世代だけの退避先へ移してから新しいファイルへ書く。
    """
    log_path = _atk_config.state_dir() / _LOG_NAME
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if log_path.exists() and log_path.stat().st_size >= _LOG_SIZE_LIMIT:
        os.replace(log_path, log_path.with_name(_ROTATED_LOG_NAME))
    with log_path.open("a", encoding="utf-8") as log_file:
        log_file.write(json.dumps(record, ensure_ascii=False) + "\n")
