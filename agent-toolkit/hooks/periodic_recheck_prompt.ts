// 定期再確認の`CronCreate`へ渡すpromptの本文の唯一の定義元。
// `periodic_recheck.ts`が自動装着で使い、モデルが自ら装着する場合も
// `agent-toolkit/skills/delegation/references/claude-code-runtime.md`「Cronによる定期再確認」の指示で
// `PERIODIC_RECHECK_PROMPT_LINES`の各行を改行で連結した本文を使う。
// 1行目の標識は`agent-toolkit/agent_toolkit/_hooks/user_prompt_submit.py`の`PERIODIC_RECHECK_MARKER`と同じリテラルとし、
// 一致は`agent-toolkit/skills/delegation/references/runtime_contract_invariant_test.py`が確かめる。
// 待機対象ID、成果物の絶対パス、コミット識別子、残工程と完了済み工程は、工程が進むと事実と一致しなくなるため本文へ含めない。

export const PERIODIC_RECHECK_MARKER = '<atk-auto source="periodic-recheck" kind="periodic-recheck">';

export const PERIODIC_RECHECK_PROMPT_LINES: readonly string[] = [
  PERIODIC_RECHECK_MARKER,
  "定期再確認の発火である。待機中の対象を次の順に再確認する。",
  "1. 待機対象を記録側から列挙する。agents_serverのsessionは保持した`session_id`と`atk agents list`、バックグラウンドタスクは起動結果が返した識別子と出力ファイル、その他は計画ファイルや引き継ぎ記録など現在の作業状態を正とする記録から読み直す。",
  "2. `atk agents wait`かそれを起動したバックグラウンドタスクが終端の`status`を返していない間は、それが所有する対象へ状態照会を発行せず、その終端応答か完了通知で受け取る。",
  "3. 所有されていない対象は記録側の状態と完了通知を確かめ、完了した対象があれば既存の受領手順へ進む。",
  '4. 成果物の状況は、その回の記録から解決したGit作業ツリーなら`atk watch --worktree "$worktree_path"`、通常のファイルなら`atk watch --file "$artifact_path"`で補う。終端は待機対象ごとの終了状態と完了通知から判定する。',
  "5. そのセッションに適用される経過時間起動の義務（定期報告、cooldown解除、期限監視、投入済みで未回答のUWIの`atk wi`による回答確認など）を記録と規範から列挙し、各義務の経過を測定して判定閾値へ到達した義務を実行する。",
  "6. 全対象が未完了で到達した義務も無ければ、ユーザー向けの報告を出力せずに待機を続ける。この発火を完了と停滞の判定の入力に含めない。",
  "7. 待機する全対象の終端を確認したら、このtaskのIDを`CronDelete`へ渡して削除する。",
  "詳細は`agent-toolkit:delegation`の`references/claude-code-runtime.md`「Cronによる定期再確認」と`references/waiting-and-monitoring.md`「完了通知を待ってターンを終える場合」に従う。",
];

export const PERIODIC_RECHECK_PROMPT = PERIODIC_RECHECK_PROMPT_LINES.join("\n");
