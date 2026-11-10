# セッション状態ファイルとフラグ：managed-tempの状態

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 各状態の共通設計・生存期間・排他・継承を適用する前 | `session-state-and-flags-common.md` | 判断 | `read:*/.claude/skills/agent-toolkit-edit/references/session-state-and-flags-common.md` |
| 呼び出し主体の判別方法を確かめる前 | `agent-basics:writing-standards/references/claude-hooks.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/claude-hooks.md` |
| run-skillの再開入力と寿命を変更・調査する前 | `agents-server-shared-state-run-skill.md` | 判断 | `read:*/.claude/skills/agent-toolkit-edit/references/agents-server-shared-state-run-skill.md` |

## managed-tempの状態

- 各セッションのmanaged-tempディレクトリ名は、一時ルート直下の`session-<sha256(session_id)の16進表記の先頭16桁>`とし、すべての導出で共通の関数を使って決める。
  新しい領域の状態は、一時ルート直下に実体として存在するディレクトリのみで管理する
- 旧バージョンが作成した状態ルート（`<state_dir>/managed-temp`）の`.sessions/<sha256(session_id)>`が一時ルート直下の実在ディレクトリ（非シンボリックリンク）を指す場合は、旧パスを同一会話の領域として再利用し、参照処理は読み取りのみに限定する。
  旧マーカー（`.agent-toolkit-managed-temp.json`）、旧登録簿、`.sessions`参照、cleanupロックの名前変更や一括削除は行わない。
  定期スイープが回収条件を確かめ、対象ごとのロックを非ブロッキングで取得できたものだけを個別に回収する
- 7日周期のスイープ処理における次回の確認時刻は、状態ルートの`.sweep/schedule`内に、一時ルート直下のディレクトリ名をキーとする単一のテーブル形式で記録する

## 完了報告からの状態読み取り

完了報告から状態を機械的に読む対象は、末尾の連続した記録行ブロックか最終行に限る。
本文中の引用による誤検出を防ぐためである。
記録行は既知のキー（例: `invoked_subagents`、`codex_unavailable`）の許可リストで判定する。
末尾付近の「`キー:`」形式の散文を誤検出するため、英字キー全般に一致する正規表現は使わない。
