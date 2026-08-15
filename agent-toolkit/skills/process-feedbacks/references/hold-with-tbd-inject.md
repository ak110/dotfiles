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
既存保存内容の所在は、`atk config show`が出力する`private_notes`配下の状態フォルダー（`inbox/`・`processing/`）から解決する。
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

通常の回答済みTBDでは、回答がTBD本文へ保存済みであることを確認し、回答済みTBDを先に採用終端する。
終端後にactive一覧とreadinessを再取得し、依存が解除されたfeedbackを通常経路で開始する。
再開時はfeedback本文と依存先filenameを対応付け、
`atk mq show <TBD filename> --target-repo=<repo-path>`で終端済みTBDを取得する。
保存済みの質問・回答とfeedbackの暫定判断との差分を反映する。
回答の反映後にfeedbackの処理が失敗した場合も、TBD本文とterminal状態を回答の正本として維持する。
再試行では同じfilenameで終端済みTBDを取得し、TBDをactiveへ戻さない。
回答内容が現在の処理と独立する新規作業なら、実行主体が`agent-toolkit:add-feedback`をSkill機能で起動し、
完成済み本文と対象リポジトリを渡す。

失敗TBDへの回答から再処理する場合は、`atk mq show <元feedback filename> --target-repo=<repo-path>`で
却下済みの元feedbackを取得する。
`atk mq show`の出力から表示用見出し（`target_repo`見出しとfilename見出し）とYAML frontmatterを除外し、
feedback本文を検索対象とする。
feedback本文を末尾から検索し、最後の`## 処理結果`節が`採否: rejected`、ISO形式の`処理日時`、
対応する失敗TBD filenameと一致する`メモ`だけを持ち、節後がEOFである場合に限り、その1節だけを除外する。
元本文中の同名見出しと、いずれかの条件に一致しない末尾節は保持する。
得た元本文と回答内容を完成済み本文として`agent-toolkit:add-feedback`へ渡し、
`depends_on=<失敗TBD filename>`を持つ新規feedbackとして保存する。
新規feedbackの本文と依存を再取得して照合した後に失敗TBDを採用終端する。
新規feedbackの保存又は照合が失敗した場合は失敗TBDをactiveのまま保持する。
