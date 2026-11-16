# rules-main.md: メインエージェント向け規範

メインの対話・接点運用を定める。

## 協調と自律

スキルが許可する成果物の種類は`../../skills/user-confirmation-and-report/references/user-utterance-correction.md`「成果物の制限」に従う。

割り込み・継続指示の処理と、委譲先の返却をユーザー確認へ回す判断は`../main-user-messages.md`「割り込み・継続と確認への振り分け」に従う。

取り消しが難しい操作前は指示と対象の整合を確かめる。

### 目的との一致の確認

成果物作成前、追加発話・工程境界・圧縮後の契約確認は`../main-user-messages.md`「目的との一致と方針の保持」に従う。

## 進捗報告

最上位メインの30分点検と報告行の書式・区分は`../main-improvements.md`「進捗報告」に従う。

## 作業中の改善点の報告

同じユーザーへ送達成功を確認済みで原文・対象・観測が同じ改善点は、途中報告と次の発話へ繰り返し転記しない。
初回・新しい観測・未送達の回復を保ち、送達済みかの確認と記録は`agent-toolkit:delegation`の`references/receiving-followup.md`「受領後の扱い」に従う。

改善点の処置・AWI化・報告は`../main-improvements.md`「作業中の改善点の報告」、報告行も同資料の書式・区分に従う。

## ユーザー向け発話ルール

実ユーザーの指摘には調査・修正・再評価前の応答冒頭に「指摘の理解」を置き、`../main-user-messages.md`に従う。
ユーザー向けの説明・回答・報告・確認も同資料に従う。

## 参照資料の取得

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 成果物作成前、追加発話・工程境界・圧縮後に契約を確認する前 | `../main-user-messages.md` |
| 最上位メインの30分点検・進捗報告、または改善点の処置・AWI化・報告を行う前 | `../main-improvements.md` |
| 会話で継続的な方針を受け取った時（操作対象外: ユーザー発話・回答の受領） | `../main-user-messages.md`、`../../skills/write-plan/references/plan-file-standards-design.md` |
| process-wi中の`気付いた改善点:`の処置・AWI化ステータス決定、追加AWIへの方針反映、ユーザーの「即時対応」指示を扱う前 | `../../skills/process-wi/references/immediate-fix.md` |
| 実ユーザーの割り込み・継続指示・指摘を処理する前、委譲先の返却をユーザー確認へ回す前、またはユーザー向けの説明・回答・報告・確認を書く前（操作対象外: 会話上の発話と確認判断） | `../main-user-messages.md` |

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 |
| --- | --- |
| ドキュメント品質：文章の共通規定の基準を適用する前 | `agent-basics:writing-standards/references/writing-common.md` |
