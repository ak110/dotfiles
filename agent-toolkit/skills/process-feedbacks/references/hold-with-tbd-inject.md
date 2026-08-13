# 保留状態の永続化

ユーザー判断または外部条件を待つ項目を、次回セッションが単独で再開できる状態にする。

## ユーザー判断

技術調査や明文化済み方針で確定できないユーザー判断だけをTBDにする。保留理由、解除条件、
再開工程、対象feedbackを完成済み入力として、実行主体が`agent-toolkit:add-feedback`をSkill機能で起動する。
質問本文の構築、重複判定、保存は同スキルへ委ねる。

既存の未回答TBDへ観測を追記する場合、`atk mq edit <TBD filename> "<本文>"`へは質問本文だけを渡す。
`## 質問`と`## 回答`以降を含めず、質問本文内の`## 回答方法`は保持する。
`atk mq show <TBD filename>`の出力を再利用する場合は、表示用のtarget_repo見出し、filename見出し、frontmatterを除外し、
完全一致の`## 質問`行の直後から完全一致の`## 回答`行の直前までを抽出する。
`atk mq show`などの表示用出力を、feedback本文の追記や要約統合に用いる全置換本文の起点にしない。
全置換が必要な場合は、Git履歴から復元した原文又は既存保存内容の直接編集を起点とする。
新規TBDの質問本文の構築、重複判定、保存は引き続き`agent-toolkit:add-feedback`へ委ねる。

## 外部条件

- 別feedbackの完了を待つ場合はトップレベルの`depends_on`へfilenameを記録する
- filenameで表せない外部条件は、観測方法、現在値、解除条件、再開工程をfeedback本文へ記録し、
  `atk mq return-to-inbox <filename> --cooldown-days=3`で差し戻す。外部条件に応じて3日より長い日数も指定できる
- ユーザー判断を伴わない外部条件待ちではTBDを生成しない

過去の`queue_schedule.dependency`は読取互換だけ維持し、新規記録へ用いない。

## 保留と再開

未回答TBDを解除条件にする場合は、現行feedbackの有効依存を復元してからTBD filenameを追加する。
トップレベルの`depends_on`があればその全件を採用し、無ければ読取互換の`queue_schedule.dependency`を確認する。
旧`none`は空集合として扱い、旧`entries`と`external-repo-entry`は既存実装が依存として解釈するfilename集合へ移行する。
回答済み時点で成立する旧`external-user`、時刻条件の`external-upstream`、集合条件の`inbox-empty`、
不正又は未知の旧条件はfilename集合へ同じ意味で表現できないため、依存更新と差し戻しを行わず保留更新を停止する。

移行できる場合は、重複を除いた既存の有効依存とTBD filenameをそれぞれ`--depends-on`へ指定して
`atk mq set-dependencies`を実行する。保存結果で旧形式が除去され、既存依存とTBDがトップレベルに全て保持されたことを
照合してから通常の`atk mq return-to-inbox`で差し戻す。その後、
`atk mq list --status=active --target-repo=<repo-path>`の対象行が`blocked`であることを確認する。
TBDを伴わない外部条件待ちは`--cooldown-days`を使い、`depends_on`と重ねない。
いずれの方法も設定できない場合はreadyのまま差し戻さず、同一セッション内で終端させる。
回答済みTBDを能動的にpollせず、同一セッション内でsleep又は時限待機をしない。
process-loopがactive状態の変化を検出し、readiness成立後に新しいprocess-feedbacksセッションを起動する。

再開時はfeedbackと対応する回答済みTBDを読み、暫定判断と回答の差分を反映する。
回答内容が現在の処理と独立する新規作業なら、実行主体が`agent-toolkit:add-feedback`をSkill機能で起動し、
完成済み本文と対象リポジトリを渡す。
