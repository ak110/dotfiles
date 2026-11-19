"""定期再確認の`CronCreate`へ渡すpromptの共通本文の唯一の定義元。

`atk wait-schedule --format json`がcron式とともにこの本文を出力し、Function hooks module
（`agent-toolkit/hooks/periodic_recheck.ts`）とモデルが自ら装着する手順はどちらもその出力を使う。
本文をPythonへ置くのは、modとモデルが同じ定義へシェルから到達するためである。
TypeScriptの定数に置くと、モデルはplugin rootを解決してファイルの文字列を組み立てる必要がある。

1行目の標識`PERIODIC_RECHECK_MARKER`も本モジュールが定め、UserPromptSubmitフックはこの標識で発火を機械注入と判定する。
`atk`とフックの双方が読むため、`_common`に置く。
待機対象ID、成果物の絶対パス、コミット識別子、残工程と完了済み工程は、工程が進むと事実と一致しなくなるため本文へ含めない。
セッション固有の経過時間起動の義務は、taskを持つ主体が測定コマンドと判定閾値を同じ要素の終了タグの前へ加えてtaskを作成し直す
（`agent-toolkit:atk-mode`の`playbooks/host-claude-code.md`「待機中の定期再確認と背景転換」）。
"""

from __future__ import annotations

from agent_toolkit._common import message_format as _message_format

PERIODIC_RECHECK_MARKER = _message_format.opening_tag(
    _message_format.AUTO_INSERTED_ELEMENT, {"source": "periodic-recheck", "kind": "periodic-recheck"}
)
"""定期再確認のpromptの1行目へ置く役割標識。

装着するpromptの1行目はこの標識だけの行とする。
modの同じ標識（`hooks/periodic_recheck.ts`の`PERIODIC_RECHECK_MARKER`）との一致は試験が確かめる。
"""
LEGACY_PERIODIC_RECHECK_MARKER = _message_format.opening_tag(
    _message_format.LEGACY_AUTO_INSERTED_ELEMENT, {"source": "agent-toolkit/periodic-recheck", "kind": "periodic-recheck"}
)
"""旧形式の役割標識。過去に装着したtaskの発火を同じく機械注入と判定するために読む。"""

_PERIODIC_RECHECK_BODY_LINES: tuple[str, ...] = (
    "待機対象を現行の作業記録から読み直し、終了通知と経過時間に応じた義務を再確認せよ。",
    "完了した対象があれば結果を受領せよ。全ての対象が未完了なら、閾値に達した経過時間の義務だけを実行し、"
    "該当が無ければ報告を出さずに待機を続けよ。この発火自体を完了や停滞の根拠にしない。",
)

PERIODIC_RECHECK_PROMPT = _message_format.auto_message(
    "\n".join(_PERIODIC_RECHECK_BODY_LINES), source="periodic-recheck", kind="periodic-recheck"
)
PERIODIC_RECHECK_PROMPT_LINES: tuple[str, ...] = tuple(PERIODIC_RECHECK_PROMPT.splitlines())
