# セッション状態ファイルとフラグ：Function hooksの終了要求：利用上限による中断の再開予約

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| Function hooks用ファイルの配置とSessionEnd・clear・期限切れ回収を確認する前 | `session-state-and-flags-function-hooks.md` |
| 各状態の共通設計・生存期間・排他・継承を適用する前 | `session-state-and-flags-common.md` |
| 呼び出し主体の判別方法を確かめる前 | `agent-basics:writing-standards/references/claude-hooks.md` |
| run-skillの再開入力と寿命を変更・調査する前 | `agents-server-shared-state-run-skill.md` |

## 利用上限による中断の再開予約

`rate-limit-{session_id}.txt`は`session-state-and-flags-function-hooks.md`で定めるFunction hooks用ディレクトリに配置する。
このファイルは、StopFailureコマンドhookから`turn.complete`へ利用上限（レート制限）の発生を通知するために使用する。
この処理はメインエージェントのClaude Codeのみを対象とし、委譲先、`agent_id`が付与された入力、およびCodexは除外する。

- 書き込み元: StopFailureコマンドhookが、`error`として`rate_limit`を受け取った際に`requested`を書き込む
- 消費元: 通常のClaude会話は`hooks/usage_limit_resume.ts`の`turn.complete`が`requested`を`consumed`に変え、既存の再開予約処理へ接続する。
  process-loopの同じ会話IDに対応する管理印、およびrun-skillの実行IDがある会話では起動側が再開を担当するため、modによる予約を省く。
  process-loopは`requested`を読んで旧CLIの終了を確認し、要求ファイルを削除して同じIDの接続を選び直す。
  run-skillはCLIのstream-jsonから上限を観測する
- 初期化と生存期間: 初期状態ではファイルを作成せず、`requested`の記録だけを予約実行の根拠とする。
  通常のSessionEndではファイルを保持し、`clear`や期限切れ回収時の扱いは`session-state-and-flags-function-hooks.md`で定めるFunction hooks用ファイルと同様とする
