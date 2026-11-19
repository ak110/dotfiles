# AWIのレーン処理：終了

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## 終了

工程の区切りそのものを確認の理由にしない。
確認要否の判定に基づいて、ユーザーの判断が真に必要な事項だけを確認待ちとし、その判断を待たずに進められる工程はそのまま継続する。
回答がすぐに得られない確認事項は、`agent-toolkit:atk-mode/playbooks/confirmation.md`「未確定判断の保留と暫定判断」に従ってUWIへ退避させる。
`processing`状態の元項目も同じ手順に従い、そのUWIへの依存を保存して`inbox`へ戻す。
作業完了の報告を所有するメインは`agent-toolkit:atk-mode/playbooks/report-completion.md`に従い、本スキルの工程で作成したコミットを公開した上で報告する。
明示的に委譲されたリポジトリ担当は役割契約に従って委譲元へ返し、担当除外のために同スキル本体・termination-evidenceを取得したり`omit-completion`を記録したりしない。
反映が必要な担当は`agent-toolkit:atk-mode/playbooks/report-completion.md`を使い、反映・稼働確認結果を返す。
公開工程の承認スコープは、選定工程で判別した結果を使い、`selection-procedure-starting.md`「公開工程の承認スコープ」に従う。
