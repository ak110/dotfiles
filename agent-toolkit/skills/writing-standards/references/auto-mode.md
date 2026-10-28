# Claude Code auto modeのカスタムルール

品質基準の正式な提供元は`agent-basics:writing-standards`の`references/auto-mode.md`である。呼び出し元の取得条件と注目箇所を引き継ぎ、本書と正式な資料を全文読む。節指定がある場合は、指定された見出しを持つ資料に注目する。両方に同名の見出しがあれば両方に注目する。

以下はagent-toolkitが所有する運用条件である。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/auto-mode.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/auto-mode.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../user-confirmation-and-report/references/permission-denial.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/permission-denial.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../user-confirmation-and-report/SKILL.md#「確認要否の判定」` | 判断 | `section:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md#「確認要否の判定」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../review-standards/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/review-standards/SKILL.md` |

## 運用条件

拒否に遭遇したときの行動手順は`agent-toolkit:user-confirmation-and-report`の`references/permission-denial.md`が定める。

## CLIサブコマンド

- `claude auto-mode defaults`: デフォルトの全ルールをJSON形式で出力する。
- `claude auto-mode config`: 現在有効な設定（デフォルト＋カスタム）を表示する。
- `claude auto-mode critique`: カスタムルールをAIがレビューし、曖昧さ・冗長性・誤検知（偽陽性）のリスクを指摘する。出力は非決定的な挙動を示し、終了コード0のまま末尾が欠落することがあるため、ファイルへ保存して欠落の有無を確認し、欠落があれば再実行する。

## カスタムルール追加のワークフロー

1. `claude auto-mode config`で現在有効なルールを確認し、配布側の設定ファイルのsourceや`claude auto-mode defaults`が示す標準ルール、および`clears when ...`の例外条件を突き合わせて、誤って拒否されている領域を特定する。
2. 提案内容と有効なルールを、対象操作、適用条件、信頼境界の包含関係に基づいて比較し、カバーされていない操作のみを追加対象とする。同等の範囲を持つローカルルールについて重複を解消する場合は、実効的な許可・拒否の範囲と信頼境界が維持されるなら自身の判断で確定して報告する。セキュリティ上の有効性や認可範囲が変化する場合、またはユーザー固有の好みが関わる場合は、`agent-toolkit:user-confirmation-and-report`の「確認要否の判定」に従って確認する。権限拒否を迂回する目的では使用しない。
3. 配布側の設定ファイルが存在する場合はそのsourceを、存在しない場合は`~/.claude/settings.json`を編集し、`autoMode.allow`に追加する。誤検知（偽陽性）として実行を承認した後は、`permission-denial.md`「偽陽性と判断できる拒否への対応」の手順6で定めた分類（環境固有・汎用など）を本一般則より優先して適用する。
4. 設定の再読み込み後、`claude auto-mode config`を実行して該当項目が有効になっていること、および`"$defaults"`を含む区分で標準項目の件数が維持されていることを確認する。
5. `claude auto-mode critique`による指摘は、変更前後の対応関係が追跡できる場合に限り、新規または悪化した差分を評価する。すべての指摘を採用することは必須とせず、`agent-toolkit:review-standards`に基づき、課題と対策の比例性を考慮して採否を判断する。配布元が存在する場合は、配布元が規定する契約を既存の対象固有テストで検証する。

配布元の`autoMode.allow`からラベルごと廃止したルールは、配列のunionマージが行われるためユーザー環境の設定に残る。そのため、廃止したラベルの旧文面を除去する処理を配布処理へ組み込む。

認証を必要とするClaude Codeの動作検証では、`CLAUDE_CONFIG_DIR`の既存の値を維持し、標準の認証解決手順を保つ。`--settings <file-or-json>`は既存設定に対する追加読み込みとして扱う。
