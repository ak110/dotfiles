# セッション状態ファイルとフラグ：hookの診断計測

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 各状態の共通設計・生存期間・排他・継承を適用する前 | `session-state-and-flags-common.md` |
| 呼び出し主体の判別方法を確かめる前 | `agent-basics:writing-standards/references/claude-hooks.md` |
| run-skillの再開入力と寿命を変更・調査する前 | `agents-server-shared-state-run-skill.md` |

## hookの診断計測

- `command_timing`: `_hooks/command_timing.py`がシェルコマンドのPreToolUse時刻をsession・呼出主体・tool_use_idで対応付ける。
  PostToolUse/FailureとClaude背景完了のUserPromptSubmitが、全体の実行時間または観測経過時間を一度だけ通知する。
  保持対象は、実行中の開始時刻と背景task-id、完了後の重複判定用tool_use_idとdoneに限定する。
  見直し用履歴は主体ごとの同じ計測種別の直近3件だけとし、60秒未満・未計測・種別変更でリセットする。
  識別子はsession状態と同じ寿命で保持し、再開・継承で重複を防ぎ、clear・期限切れ回収で除く。
  hook自身の診断計測とは独立する。

hookの診断計測はセッションのmanaged-tempにある`hook-timings.jsonl`へ保存する。
`timing.py`がイベント・処理・区間・単調時計の所要・成否だけを記録し、同領域の`hook-timings.lock`で並行更新を直列化する。
上限64KiBに達すると直近の完全な行だけを残す。
保存対象は処理の識別情報と測定値に限る。
セッションmanaged-tempと同じ7日保持で回収する。
計測の用途は性能診断に限る。

## 通知反復系

Codex PreToolUseの長い追加通知は、既存のsession managed-tempへ全文を保存し、短い保存先参照を提示する。
参照の提示は既存の通知反復抑止と取得案内の提示へ記録する。
資料本文の取得証拠は、当該資料の外側配送を観測した既存の記録から確認する。

- `persistent_warning_fingerprints`: `_atk/outcome.py`が、共通起動の持続状態ならびにhookの引数不整合に関する警告本文のフィンガープリント（指紋情報）を記録する。
  同じ会話で初めて報告する内容だけを出力する。
  CLIの識別子はowner、Claude Code、Codexの順序で決定し、hookは入力の`session_id`を使用する。
  委譲先のスイープに関する警告は記録と出力を省略する。
  生存期間はセッション状態ファイルと同一とする
- `notice_fingerprints`: `notice.py`が、hook、呼出主体、操作範囲、発生原因ごとに通知本文と次操作のフィンガープリントを保持する。
  整形時は候補を予約し、最終出力に採用した非空本文だけを`emit_response`・`record_delivery`で記録する。
  破棄・ホスト変換による除去・AGYでの保留では未配送状態を維持する。
  同一条件の通知は初回のみ配信し、対象・原因・対処の変化を新しい通知として扱う。
  `notice_cycle`は未判定条件を保持し、解消を確認した原因だけを`resolve_notice`で解除して再成立時に通知する。
  遮断は実際の操作条件で決め、本キーや旧`warn_notice_counts`の回数だけで昇格させない。
  生存期間はセッション状態ファイルと同一とする
- `response_language_retry_by_actor`: `pretooluse/dispatch.py`が呼出主体から直前のツール名・入力全体のSHA256へのマッピングを保持する。
  引用・素材を除く説明が日本語に適合する入力を記録し、同じ主体・同じツール・同一入力の直後の再発行で回復に使う。
  対象外の操作や説明の不適合でその主体の値を消し、別の対象入力では直前値を置き換える。
  別主体の値は回復に使わない。
  英語の説明は同一再発行でも遮断する。
  生存期間の上限はセッション状態ファイルと同一とする
- `generic_agent_retry_by_actor`: `pretooluse/agent_checks.py`が、組み込みの汎用Agent定義の起動を拒否したときに、呼出主体からツール名・入力全体のSHA256の一覧（主体ごとに直近32件）へのマッピングを保持する。
  同じ主体が同一入力で再発行した起動は通し、通した指紋は消費する。
  この状態を保存できない場合は、拒否せず警告だけを返して起動を通す。
  `agents_server`を試した後の再発行と、続けて発行した複数の起動を通すため、別の操作を挟んでも失効させない。
  生存期間はセッション状態ファイルと同一とする
