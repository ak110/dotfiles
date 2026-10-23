# Claude Code auto modeのカスタムルール

品質基準の正式な提供元は`agent-basics:writing-standards`の`references/auto-mode.md`である。呼出元が指定した条件と読込範囲を、同名の正式な品質資料へ引き継ぐ。節指定がある場合は移設先の実見出しへ接続し、指定がない場合は従来の全文読込契約を保つ。

以下はagent-toolkitが所有する運用条件である。呼出元の条件と読込範囲に従い、ここに残る運用節を補う。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/auto-mode.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/auto-mode.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../user-confirmation-and-report/references/permission-denial.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/permission-denial.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../user-confirmation-and-report/SKILL.md#「確認要否の判定」` | 判断 | `section:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md#「確認要否の判定」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../review-standards/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/review-standards/SKILL.md` |

## 運用条件

拒否に遭遇したときの行動手順は`agent-toolkit:user-confirmation-and-report`の`references/permission-denial.md`が定める。

## CLIサブコマンド

- `claude auto-mode defaults`: デフォルトの全ルールをJSON形式で出力する
- `claude auto-mode config`: 現在有効な設定（デフォルト＋カスタム）を表示する
- `claude auto-mode critique`: カスタムルールをAIがレビューし、曖昧・冗長・偽陽性のリスクを指摘する。出力は非決定的で、exit 0のまま末尾が欠落する場合が観測されているため、ファイルへ保存して欠落を確かめ、欠落時は再実行する

## カスタムルール追加のワークフロー

1. `claude auto-mode config`が示す有効なルール、配布側の設定ファイルのsource、`claude auto-mode defaults`が示す標準のルールと`clears when ...`の例外条件を読み、誤拒否されている領域を特定する
2. 提案と有効なルールを、対象操作、適用条件および信頼境界の包含関係で比べ、未被覆の操作だけを追加対象にする。同じ範囲を持つローカルルールの重複撤去は、実効的な許可・拒否範囲と信頼境界を保つなら自ら確定して報告し、セキュリティ効力・認可範囲を変える場合やユーザーだけの選好が残る場合は`agent-toolkit:user-confirmation-and-report`「確認要否の判定」に従って確認する。権限拒否の迂回には用いない
3. 配布側の設定ファイルがあればそのsourceを、無ければ`~/.claude/settings.json`を編集し、`autoMode.allow`へ追加する。偽陽性として実行を承認した後は、`permission-denial.md`「偽陽性と判断できる拒否への対応」の手順6の分類（環境固有・汎用など）をこの一般則より優先する
4. 再ロード後に`claude auto-mode config`でその項目が有効なことと、`"$defaults"`を含む区分で標準の項目の件数が維持されていることを確認する
5. `claude auto-mode critique`の指摘は変更前後で対応付けられる場合だけ新規・悪化差分を評価する。全件採用を必須とせず、`agent-toolkit:review-standards`の問題と手段の比例性で採否を決める。配布元がある場合は、配布元が所有する契約を既存の対象固有テストで確認する

配布元の`autoMode.allow`からラベルごと廃止したルールは、配列のunionマージのためユーザー環境の設定に残る。廃止したラベルの旧文面を除去する処理を配布処理へ登録する。

認証を要するClaude Codeの検証では`CLAUDE_CONFIG_DIR`を既存の値のまま使い、標準の認証解決を保つ。`--settings <file-or-json>`は既存設定への追加読込として扱う。
