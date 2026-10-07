# 終了工程の判断の記録

本書は`agent-toolkit:completion-report`の工程を進めるメインが、Stop hookの遮断を受けた時と、作業の中止・置換、待機または技術的不成立の判断を記録する時の手順を定める。報告本文の判定は同スキルの`SKILL.md`「工程」の後段が定める。

ユーザーが作業の中止、別作業への置換を指示した場合、確認の回答または起動した委譲先の結果を待つ場合、技術的に続行できない場合は、`atk run-script termination-evidence -- --decision-file <判断JSONの絶対パス>`で判断を記録する。
判断JSONは`session_id`、`action`（`cancel`・`replace`・`start`・`resume`・`wait`・`blocked`）、対象の`work_id`と`reason`を持つ。
中止・置換・開始・再開にはユーザーの原入力の`input_id`と全文の`quote`を、確認待ちには投入した未回答UWIの`uwi_file`と全文の`quote`を、委譲先の待機には`target_session_id`を、技術的不成立には失敗した呼び出しの`call_id`とその応答全体のJSONの`quote`を渡す。
`session_id`、`work_id`と直近のユーザー入力の`input_id`は遮断の通知から得る。
判断は指定した作業だけへ作用し、ユーザーの割り込みと直接関係しない他の作業の残工程は残る。
生成されたStopの継続入力やhookの注記はユーザーの原入力として受理されず、完了の申告を記録する操作は無い。
証拠を取得できない場合は遮断せず、判定できなかったことを記録する。
自律モードの報告用UWIの保存検収と`atk agents-exit-session`は報告段階の判定と別の工程として維持する。
