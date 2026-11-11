# レビュー担当の品質基準と運用

レビュー担当側の品質基準は、`agent-basics:review-standards/references/reviewer.md`で定義している。
呼び出し元の読込条件を引き継ぎ、本書の運用条件と条件に該当する正式な資料の全文を読む。
両方に同じ主題があれば、両方の全文を確認する。
範囲の選択と取得済み本文の再利用は`agent-basics:writing-standards/references/read-method.md`に従う。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| レビュー担当として判定する前 | `agent-basics:review-standards/references/reviewer.md` |
| 初回レビューを判定する前 | `agent-basics:review-standards/references/reviewer-initial.md` |
| 再レビューを判定する前 | `agent-basics:review-standards/references/reviewer-followup.md` |
| 要求単位・完成条件と観測の達成を判定する前 | `agent-basics:review-standards/references/reviewer-execution.md` |
| 実行レビューを判定する前 | `agent-basics:review-standards/references/reviewer-execution.md` |
| 画面のユーザビリティを判定する前 | `agent-basics:review-standards/references/reviewer-usability.md` |
| 要求の縮小を判定する時 | `../skills/user-confirmation-and-report/references/user-confirmation-and-report-authorization.md` |
| AWIの新旧形式を判断する前 | `../skills/wi-standards/references/awi-body.md` |

## 専用レビュータスク契約

受け取った`<役割名>.subagent.md`において、レビュー指摘管理表の絶対パスと`track`が専用レビュータスク契約の入力として明示的に指定されている場合を、「専用レビュータスク契約」と呼ぶ。
この契約においてのみレビュー指摘管理表を直接操作し、「完了報告」で定める形式を用いて返却する。
表と`track`を受け取った実装担当が行う一般的な妥当性評価や、この契約を受け取らないメインエージェントによる直接レビューは対象外とし、それぞれの評価を求めた工程が定める形式で結果を返す。

専用レビュータスク契約を受け取らないメインエージェントの直接レビューであっても、検証が到達不能な場合は`続行できない理由:`を用いる。
具体的には、実機検証、完了条件、または待機手順に基づいて合否を判定する指摘について、対象成果物に許可されたツール、禁止事項、入力前提、通常の状態遷移だけでは検証が物理的に到達不能であると確定した要求を、計画や規範の再設計が必要な問題として報告する場合に適用する。

## 読者別探索

調査対象の分割は、読者別探索の親契約に定められた読取量、共通文脈、想定読者像、品質基準に基づいて行う。
各ファイルの比較資料、判定結果、証拠、起動識別子を適切に対応付けた上で、探索結果を検収して指摘を統合する。

文章成果物の日本語については、`agent-basics:writing-standards/references/writing.md`「日本語の書き方」を執筆時と共通の基準として適用する。
想定読者への適合性や、委譲元・委譲先の役割への適合性については、`agent-toolkit:/share/reader-fit-review.parent.md`に従い、低リスクの直接確認または読者別探索担当で判定する。
その探索結果の裏付けを検収した上で、指摘を統合する。
なお、探索の起動を省略できる差分の条件は同資料が定める。

## 完了報告

専用レビュータスク契約では、`atk review-table add`を用いて指摘を記録する。
コマンド形式は`atk review-table add <表の絶対パス> --round <ラウンド番号> --track <区分> --level <指摘レベル> --location-file <指摘箇所PATH> --issue-file <指摘内容PATH>`とする。
引数の渡し方や保存本文の検証ルールは、`review-table-operations.md`「レビュー指摘管理表の共通操作」に従う。

返却メッセージは以下の形式で記述する。

- 正常終了時は`指摘件数: <非負整数>`と`要件仕様件数: <非負整数>`（そのラウンドで登録した`指摘レベル`が`要件`または`仕様`である件数）の2行を返し、指摘の本文は含めない。
  `気付いた改善点:`の行は、`agent-toolkit:/share/rules/rules-subagent.md`「返却形式の受け渡し」に従って送る
- 基準となる文書を読み取る際に切り詰めの通知を受けた場合のみ、続けて`切り詰め: <文書の絶対パス> <全文読解の成否>`の1行を記述する（`全文到達`、または`未到達`とその範囲）。
  この行が存在しないことは、切り詰めの通知を受けなかったことを意味する
- 処理を続行できない場合は、`agent-toolkit:/share/rules/rules-subagent.md`「返却形式の受け渡し」の区分と理由を返す。
  指摘内容、検証の網羅性、検証結果、および変更の要約は、レビュー指摘管理表側に保持される

## 適用契約

品質の定義については、`agent-basics:review-standards/references/reviewer.md`「適用契約」に従う。

## 基本方針

品質の定義については、`agent-basics:review-standards/references/reviewer.md`「基本方針」に従う。

## 初回レビュー

品質の定義については、`agent-basics:review-standards/references/reviewer-initial.md`「初回レビュー」に従う。

## 再レビューの場合

品質の定義については、`agent-basics:review-standards/references/reviewer-followup.md`「再レビューの場合」に従う。

## ユーザビリティレビューの判定

品質の定義については、`agent-basics:review-standards/references/reviewer-usability.md`「ユーザビリティレビューの判定」に従う。

## 指摘内容

品質の定義については、`agent-basics:review-standards/references/reviewer.md`「指摘内容」に従う。

## 条件別資料

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 計画と表の優先・実行レビューの判定に関する判断・作業の前 | `exec-review-criteria-execution.md` |
