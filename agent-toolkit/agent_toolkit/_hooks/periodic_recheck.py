"""定期再確認の`CronCreate`へ渡すpromptの共通本文の唯一の定義元。

`atk wait-schedule --format json`がcron式とともにこの本文を出力し、Function hooks module
（`agent-toolkit/hooks/periodic_recheck.ts`）とモデルが自ら装着する手順はどちらもその出力を使う。
本文をPythonへ置くのは、modとモデルが同じ定義へシェルから到達するためである。
TypeScriptの定数に置くと、モデルはplugin rootを解決してファイルの文字列を組み立てる必要がある。

1行目の標識は`user_prompt_submit.PERIODIC_RECHECK_MARKER`を使い、UserPromptSubmitが発火を機械注入と判定する。
待機対象ID、成果物の絶対パス、コミット識別子、残工程と完了済み工程は、工程が進むと事実と一致しなくなるため本文へ含めない。
セッション固有の経過時間起動の義務は、taskを持つ主体が測定コマンドと判定閾値をこの本文の後へ加えてtaskを作成し直す
（`agent-toolkit:delegation`の`references/claude-code-runtime.md`「待機中の定期再確認と背景転換」）。
"""

from __future__ import annotations

from agent_toolkit._hooks.user_prompt_submit import PERIODIC_RECHECK_MARKER

PERIODIC_RECHECK_PROMPT_LINES: tuple[str, ...] = (
    PERIODIC_RECHECK_MARKER,
    "定期再確認の発火である。待機中の対象を次の順に再確認する。",
    "1. 待機対象を記録側から列挙する。agents_serverのsessionは保持した`session_id`と`atk agents list`、"
    "バックグラウンドタスクは起動結果が返した識別子と出力ファイル、その他は計画ファイルや引き継ぎ記録など"
    "現在の作業状態を正とする記録から読み直す。",
    "2. `atk agents wait`かそれを起動したバックグラウンドタスクが終端の`status`を返していない間は、"
    "それが所有する対象へ状態照会を発行せず、その終端応答か完了通知で受け取る。",
    "3. 所有されていない対象は記録側の状態と完了通知を確かめ、完了した対象があれば既存の受領手順へ進む。",
    '4. 成果物の状況は、その回の記録から解決したGit作業ツリーなら`atk watch --worktree "$worktree_path"`、'
    '通常のファイルなら`atk watch --file "$artifact_path"`で補う。'
    "終端は待機対象ごとの終了状態と完了通知から判定する。",
    "5. このpromptの末尾に経過時間起動の義務の行がある場合は、"
    "各行の測定コマンドで経過を測定し、判定閾値へ到達した義務を実行する。"
    "行が無い場合も、そのセッションに適用される経過時間起動の義務（定期報告、cooldown解除、期限監視、"
    "投入済みで未回答のUWIの`atk wi`による回答確認など）を記録と規範から確かめ、"
    "確定した義務はその行を加えたpromptでこのtaskを作成し直す。",
    "6. 全対象が未完了で到達した義務も無ければ、ユーザー向けの報告を出力せずに待機を続ける。"
    "この発火を完了と停滞の判定の入力に含めない。",
    "7. 待機する全対象の終端を確認したら、このtaskのIDを`CronDelete`へ渡して削除する。",
    "詳細は`agent-toolkit:delegation`の`references/claude-code-runtime.md`「待機中の定期再確認と背景転換」と"
    "`references/waiting-and-monitoring.md`「完了通知を待ってターンを終える場合」に従う。",
)

PERIODIC_RECHECK_PROMPT = "\n".join(PERIODIC_RECHECK_PROMPT_LINES)
