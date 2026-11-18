# auto modeのカスタムルールを追加する手順

Claude Codeのauto modeの`autoMode.allow`へカスタムルールを追加・編集する手順を定める。
ルールの区分と書き方の基準は`agent-basics:writing-standards/references/claude-hooks.md`が定義元であり、文面を作る前に同書を全文読む。
拒否を受けた時の処置は`agent-toolkit:atk-mode/playbooks/permission-denial.md`が定め、偽陽性として許可した後のルールの追加は同書「偽陽性と判断できる拒否への対応」の手順6から本書へ進む。

## CLIサブコマンド

- `claude auto-mode defaults`: デフォルトの全ルールをJSONで出力する。
- `claude auto-mode config`: 現在有効な設定（デフォルトとカスタム）を表示する。
- `claude auto-mode critique`: カスタムルールをAIがレビューし、曖昧さ、冗長さ、偽陽性のリスクを指摘する。
  出力は非決定的で、終了コード0のまま末尾が欠けることがあるため、ファイルへ保存して欠落の有無を確かめ、欠けていれば再実行する。

## カスタムルール追加のワークフロー

1. `claude auto-mode config`で有効なルールを確かめ、配布側の設定ファイルのsource、`claude auto-mode defaults`が示す標準ルール、`clears when ...`の例外条件と比べて、誤って拒否されている領域を特定する。
2. 提案と有効なルールを、対象の操作、適用条件、信頼境界の包含関係で比べ、覆われていない操作だけを追加の対象にする。
   権限拒否を迂回する目的には使わない。
   同等の範囲を持つローカルのルールの重複を解消する時は、実効の許可・拒否の範囲と信頼境界が保たれるなら、自分で確定して報告する。
   セキュリティ上の有効性や認可の範囲が変わる時と、ユーザー固有の好みが関わる時は、`agent-toolkit:atk-mode/playbooks/confirmation.md`「確認要否の判定」に従って確認する。
3. 配布側の設定ファイルがあればそのsourceを、無ければ`~/.claude/settings.json`を編集し、`autoMode.allow`へ追加する。
   偽陽性として実行を承認した後は、`agent-toolkit:atk-mode/playbooks/permission-denial.md`「偽陽性と判断できる拒否への対応」の手順6の分類（環境に固有、汎用など）を、この手順より優先して当てる。
4. 設定を読み込み直した後、`claude auto-mode config`で、追加した項目が有効なことと、`"$defaults"`を含む区分で標準の項目の件数が保たれていることを確かめる。
5. `claude auto-mode critique`の指摘は変更前後の対応を追跡できる時に限り、新しく出た差分と悪化した差分を評価する。
   全ての指摘を採用する必要は無く、`agent-basics:review-standards`に基づき、課題と対策の釣り合いで採否を決める。
   配布元がある時は、配布元が定める契約を既存の対象固有のテストで検証する。

配布元の`autoMode.allow`からラベルごと廃止したルールは、配列のunionマージのためユーザー環境の設定に残る。
そのため、廃止したラベルの旧文面を除去する処理を配布処理へ組み込む。

認証が要るClaude Codeの動作検証では、`CLAUDE_CONFIG_DIR`の既存の値を保ち、標準の認証の解決手順を保つ。
`--settings <file-or-json>`は既存の設定への追加の読み込みとして扱う。
