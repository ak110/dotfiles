# セッション状態ファイルとフラグ：Function hooksの終了要求：待機時圧縮の判定記録

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| Function hooks用ファイルの配置とSessionEnd・clear・期限切れ回収を確認する前 | `session-state-and-flags-function-hooks.md` | 判断 | `read:*/.claude/skills/agent-toolkit-edit/references/session-state-and-flags-function-hooks.md` |
| 各状態の共通設計・生存期間・排他・継承を適用する前 | `session-state-and-flags-common.md` | 判断 | `read:*/.claude/skills/agent-toolkit-edit/references/session-state-and-flags-common.md` |
| 呼び出し主体の判別方法を確かめる前 | `agent-basics:writing-standards/references/claude-hooks.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/claude-hooks.md` |
| run-skillの再開入力と寿命を変更・調査する前 | `agents-server-shared-state-run-skill.md` | 判断 | `read:*/.claude/skills/agent-toolkit-edit/references/agents-server-shared-state-run-skill.md` |

## 待機時圧縮の判定記録

`compaction-{session_id}.txt`は`session-state-and-flags-function-hooks.md`で定めるFunction hooks用ディレクトリに配置する。
`hooks/compact_conversation.ts`はこのファイルを背景待機の登録と自動圧縮の判定理由を残すために使用する。
process-loopは`--debug=hooks`で起動し、modの`$.ui.log`はそのdebugログに現れないため、見送り理由を後から確かめる読み先として置く。

- 書き込み元: `hooks/compact_conversation.ts`が判定ごとに、直近20件の判定（待機ID、理由、使用トークン数と上限、予約中・turn実行中の状態）をJSONで上書きする。
  書込みに失敗しても判定はそのまま続ける
- 消費元: 圧縮の判定には使わず、調査する担当が読む
- 初期化と生存期間: 最初の判定時にファイルを作成する。
  寿命は`session-state-and-flags-function-hooks.md`で定めるFunction hooks用ファイルと同じく、通常のSessionEndで保持し、`clear`と期限切れ回収で削除する
