# AWIのレーン処理：終了

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## 終了

工程の区切りそのものを確認の理由にしない。
確認要否の判定に基づいて、ユーザーの判断が真に必要な事項だけを確認待ちとし、その判断を待たずに進められる工程はそのまま継続する。
回答がすぐに得られない確認事項は、`agent-toolkit:user-confirmation-and-report`の手順に従ってUWIへ退避させる。
`processing`状態の元項目も同じ手順に従い、そのUWIへの依存を保存して`inbox`へ戻す。
作業完了の報告は`agent-toolkit:report-completion`に従って行い、本スキルの工程で作成したコミットを公開した上で報告する。
公開工程の承認スコープは、選定工程で判別した結果を使い、`selection-procedure-starting.md`「公開工程の承認スコープ」に従う。
