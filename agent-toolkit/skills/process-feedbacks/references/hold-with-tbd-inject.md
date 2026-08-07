# 保留状態の永続化

ユーザー判断または外部条件を待つ項目を、次回セッションが単独で再開できる状態にする。

## ユーザー判断

技術調査や明文化済み方針で確定できないユーザー判断だけをTBDにする。保留理由、解除条件、
再開工程、対象feedbackを完成済み入力として、実行主体が`agent-toolkit:add-feedback`をSkill機能で起動する。
質問本文の構築、重複判定、保存は同スキルへ委ねる。

## 外部条件

- 別feedbackの完了を待つ場合はトップレベルの`depends_on`へfilenameを記録する
- filenameで表せない外部条件は、観測方法、現在値、解除条件をfeedback本文へ記録する
- ユーザー判断を伴わない外部条件待ちではTBDを生成しない

過去の`queue_schedule.dependency`は読取互換だけ維持し、新規記録へ用いない。

## 保留と再開

保留理由、確認済み事実、解除条件、再開工程をfeedback本文へ記録し、processing項目を
`atk mq return-to-inbox`でinboxへ戻す。回答済みTBDを能動的にpollせず、時限待機もしない。
process-loopがactive状態の変化を検出し、readiness成立後に新しいprocess-feedbacksセッションを起動する。

再開時はfeedbackと対応する回答済みTBDを読み、暫定判断と回答の差分を反映する。
回答内容が現在の処理と独立する新規作業なら、実行主体が`agent-toolkit:add-feedback`をSkill機能で起動し、
完成済み本文と対象リポジトリを渡す。
